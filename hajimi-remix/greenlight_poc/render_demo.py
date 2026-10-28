#!/usr/bin/env python3
"""Render the first song-specific Green Light Hajimi comparison demo.

This is intentionally a narrow experiment instead of a new generic renderer.
It consumes the auto-draft note/onset map produced from the local reference
stem, groups tied notes into syllable events, and anchors each consonant attack
to an event.  Only the vowel sustain is extended.  The target reference vocal
is never mixed into the result; it is used only for timing, pitch and dynamics.

Three source variants are rendered for an equal-processing comparison:

* ``jp``: the high-bandwidth Japanese Hajimi phrase for attack and sustain;
* ``hybrid``: Mandarin Yushu attacks plus Japanese Hajimi sustains;
* ``zh``: the Mandarin Yushu phrase for both attack and sustain.

The resulting files are premaster WAVs.  Final loudness and true-peak mastering
is deliberately kept as a separate, measurable ffmpeg step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import resource
import sys
import time
from pathlib import Path
from typing import Any

import librosa
import numpy as np
import parselmouth
import soundfile as sf
from parselmouth.praat import call as praat_call
from scipy import signal
from scipy.ndimage import gaussian_filter1d


EPS = 1e-10
TOKENS = ("哈", "基", "米", "南", "北", "绿", "豆")
BASE_VARIANTS = ("jp", "hybrid", "zh")
ARTICULATION_POC_VARIANT = "hybrid_v1p1_poc1"
MICROCYCLE_VARIANT = "hybrid_v1p1"
VARIANTS = BASE_VARIANTS + (ARTICULATION_POC_VARIANT, MICROCYCLE_VARIANT)

# These acoustic boundaries come from the local voicebank forensic pass.  They
# are deliberately acoustic macro-units, not a claim of Japanese phoneme labels.
JP_UNITS = (
    {"id": "jp_u01", "slice": (0.132, 0.282), "sustain": (0.195, 0.250)},
    {"id": "jp_u02", "slice": (0.282, 0.369), "sustain": (0.325, 0.355)},
    {"id": "jp_u03", "slice": (0.369, 0.638), "sustain": (0.420, 0.545)},
    {"id": "jp_u04", "slice": (0.638, 1.334), "sustain": (0.780, 1.220)},
    {"id": "jp_u05", "slice": (1.334, 1.651), "sustain": (1.475, 1.610)},
    {"id": "jp_u06", "slice": (1.651, 2.841), "sustain": (1.800, 2.380)},
    {"id": "jp_u07", "slice": (2.841, 3.080), "sustain": (2.985, 3.025)},
)

ZH_UNITS = (
    {"id": "zh_ha", "slice": (0.050, 0.262), "sustain": (0.130, 0.235)},
    {"id": "zh_ji", "slice": (0.262, 0.520), "sustain": (0.345, 0.500)},
    {"id": "zh_mi", "slice": (0.520, 0.700), "sustain": (0.585, 0.675)},
    {"id": "zh_nan", "slice": (0.700, 0.990), "sustain": (0.810, 0.955)},
    {"id": "zh_bei", "slice": (0.990, 1.190), "sustain": (1.045, 1.160)},
    {"id": "zh_lv", "slice": (1.190, 1.404), "sustain": (1.255, 1.380)},
    {"id": "zh_dou", "slice": (1.404, 1.660), "sustain": (1.465, 1.580)},
)

# The Japanese phrase has no one-to-one Mandarin inventory.  These choices keep
# a recognisable attack cycle while borrowing its most stable vowel regions.
JP_SUSTAIN_FOR_TOKEN = (0, 2, 2, 4, 4, 5, 3)


def validate_articulation_preset(value: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the opt-in V1.1 articulation POC profile."""

    required_top = {
        "schema_version", "preset_id", "preset_version", "status", "extends",
        "scope", "articulation", "invariants",
    }
    if set(value) != required_top:
        raise ValueError("articulation preset has unexpected or missing top-level fields")
    if value.get("schema_version") != 1:
        raise ValueError("articulation preset schema_version must be 1")
    if value.get("preset_id") != "hajimi-note-event-v1.1-poc1":
        raise ValueError("unexpected articulation preset_id")
    if value.get("preset_version") != "1.1.0-poc.1":
        raise ValueError("unexpected articulation preset_version")
    if value.get("status") != "EXPERIMENTAL_AB_ONLY":
        raise ValueError("articulation preset must remain EXPERIMENTAL_AB_ONLY")
    if value.get("extends") != "hajimi-note-event-v1":
        raise ValueError("articulation preset must extend the frozen V1 baseline")

    articulation = value.get("articulation")
    if not isinstance(articulation, dict):
        raise ValueError("articulation must be an object")
    required_articulation = {
        "mode", "token_nucleus_ms", "crossfade_ms",
        "maximum_group_sustain_ratio", "minimum_applied_nucleus_ms",
        "short_group_policy",
    }
    if set(articulation) != required_articulation:
        raise ValueError("articulation has unexpected or missing fields")
    if articulation.get("mode") != "zh_vowel_nucleus_then_jp_sustain":
        raise ValueError("unsupported articulation mode")
    token_ms = articulation.get("token_nucleus_ms")
    if not isinstance(token_ms, dict) or tuple(token_ms) != TOKENS:
        raise ValueError("token_nucleus_ms must contain the seven tokens in canonical order")
    normalized_ms: dict[str, float] = {}
    for token in TOKENS:
        duration = float(token_ms[token])
        if not 1.0 <= duration <= 120.0:
            raise ValueError(f"invalid nucleus duration for {token}: {duration}")
        normalized_ms[token] = duration
    crossfade_ms = float(articulation.get("crossfade_ms"))
    cap = float(articulation.get("maximum_group_sustain_ratio"))
    minimum_ms = float(articulation.get("minimum_applied_nucleus_ms"))
    if abs(crossfade_ms - 8.0) > 1e-9:
        raise ValueError("V1.1 POC crossfade must be exactly 8 ms")
    if not 0.0 < cap <= 0.30:
        raise ValueError("maximum_group_sustain_ratio must be in (0, 0.30]")
    if not crossfade_ms < minimum_ms <= min(normalized_ms.values()):
        raise ValueError("minimum_applied_nucleus_ms must exceed crossfade and fit every token")
    if articulation.get("short_group_policy") != "FALLBACK_TO_V1_AND_RECORD":
        raise ValueError("short groups must fall back to V1 and be recorded")
    return {
        "preset_id": value["preset_id"],
        "preset_version": value["preset_version"],
        "token_nucleus_ms": normalized_ms,
        "crossfade_ms": crossfade_ms,
        "maximum_group_sustain_ratio": cap,
        "minimum_applied_nucleus_ms": minimum_ms,
    }


def load_articulation_preset(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read articulation preset {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("articulation preset root must be an object")
    return validate_articulation_preset(value)


def validate_sustain_preset(value: dict[str, Any]) -> dict[str, Any]:
    """Validate the accepted V1.1 phase-locked microcycle contract."""

    required_top = {
        "schema_version", "preset_id", "preset_version", "status", "extends",
        "scope", "sustain_extension", "qa", "resources",
    }
    if set(value) != required_top:
        raise ValueError("sustain preset has unexpected or missing top-level fields")
    if value.get("schema_version") != 1:
        raise ValueError("sustain preset schema_version must be 1")
    if value.get("preset_id") != "hajimi-note-event-v1.1":
        raise ValueError("unexpected sustain preset_id")
    if value.get("preset_version") != 1:
        raise ValueError("unexpected sustain preset_version")
    if value.get("status") != "FROZEN_INTERNAL_BASELINE":
        raise ValueError("V1.1 sustain preset must be frozen before production use")
    if value.get("extends") != "hajimi-note-event-v1":
        raise ValueError("V1.1 sustain preset must extend the frozen V1 baseline")

    extension = value.get("sustain_extension")
    if not isinstance(extension, dict):
        raise ValueError("sustain_extension must be an object")
    required_extension = {
        "method", "repitch_scope", "pitch_floor_hz", "pitch_ceiling_hz",
        "shortest_lag_within_best_ratio", "production_selected_ncc_min",
        "preview_selected_ncc_min", "production_split_half_delta_percent_max",
        "preview_split_half_delta_percent_max", "minimum_source_periods",
        "microcycle_periods", "overlap_periods", "minimum_overlap_samples",
        "f0_safety_policy", "f0_safety_event_median_octave_threshold_cents",
        "f0_safety_maximum_group_ratio", "short_target_policy", "low_confidence_policy",
    }
    if set(extension) != required_extension:
        raise ValueError("sustain_extension has unexpected or missing fields")
    expected = {
        "method": "phase_locked_microcycle",
        "repitch_scope": "whole_group_frozen_v1",
        "pitch_floor_hz": 120,
        "pitch_ceiling_hz": 700,
        "shortest_lag_within_best_ratio": 0.98,
        "production_selected_ncc_min": 0.80,
        "preview_selected_ncc_min": 0.65,
        "production_split_half_delta_percent_max": 5.0,
        "preview_split_half_delta_percent_max": 10.0,
        "minimum_source_periods": 10,
        "microcycle_periods": 8,
        "overlap_periods": 2,
        "minimum_overlap_samples": 4,
        "f0_safety_policy": "EXPLICIT_V1_GROUP_FALLBACK_AND_RECORD",
        "f0_safety_event_median_octave_threshold_cents": 600,
        "f0_safety_maximum_group_ratio": 0.05,
        "short_target_policy": "CENTER_CROP_V1",
        "low_confidence_policy": "FAIL_CLOSED",
    }
    for key, expected_value in expected.items():
        actual = extension.get(key)
        if isinstance(expected_value, float):
            if abs(float(actual) - expected_value) > 1e-9:
                raise ValueError(f"unexpected sustain_extension.{key}: {actual}")
        elif actual != expected_value:
            raise ValueError(f"unexpected sustain_extension.{key}: {actual}")
    return {
        "preset_id": value["preset_id"],
        "preset_version": value["preset_version"],
        **expected,
    }


def load_sustain_preset(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read sustain preset {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("sustain preset root must be an object")
    return validate_sustain_preset(value)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def db(value: float) -> float:
    return 20.0 * math.log10(max(abs(value), EPS))


def rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0


def load_audio(path: Path, *, mono: bool, sample_rate: int) -> np.ndarray:
    audio, source_rate = sf.read(path, always_2d=True, dtype="float64")
    if mono:
        audio = np.mean(audio, axis=1)
    if source_rate == sample_rate:
        return audio
    if audio.ndim == 1:
        return librosa.resample(audio, orig_sr=source_rate, target_sr=sample_rate)
    return np.stack(
        [
            librosa.resample(audio[:, channel], orig_sr=source_rate, target_sr=sample_rate)
            for channel in range(audio.shape[1])
        ],
        axis=1,
    )


def extract(audio: np.ndarray, bounds: tuple[float, float], sample_rate: int) -> np.ndarray:
    start = max(0, int(round(bounds[0] * sample_rate)))
    end = min(len(audio), int(round(bounds[1] * sample_rate)))
    return np.asarray(audio[start:end], dtype=np.float64)


def midi_hz(midi: int) -> float:
    return 440.0 * 2.0 ** ((midi - 69.0) / 12.0)


def equal_power_join(left: np.ndarray, right: np.ndarray, overlap: int) -> np.ndarray:
    overlap = max(0, min(overlap, len(left), len(right)))
    if overlap == 0:
        return np.concatenate((left, right))
    phase = np.linspace(0.0, math.pi / 2.0, overlap, endpoint=True)
    joined = left[-overlap:] * np.cos(phase) + right[:overlap] * np.sin(phase)
    return np.concatenate((left[:-overlap], joined, right[overlap:]))


def extend_sustain(source: np.ndarray, target_samples: int, sample_rate: int) -> np.ndarray:
    """Extend a stable vowel with overlap-add; avoid stretching consonants."""

    target_samples = max(1, int(target_samples))
    source = np.asarray(source, dtype=np.float64)
    if len(source) < 8:
        return signal.resample(source, target_samples) if len(source) else np.zeros(target_samples)
    if target_samples <= len(source):
        # A centered crop keeps the settled vowel and avoids a new spectral attack.
        start = max(0, (len(source) - target_samples) // 2)
        result = source[start : start + target_samples].copy()
        fade = min(len(result) // 3, int(round(0.004 * sample_rate)))
        if fade:
            result[:fade] *= np.linspace(0.0, 1.0, fade)
        return result
    overlap = min(
        max(4, int(round(0.018 * sample_rate))),
        max(4, len(source) // 4),
    )
    result = source.copy()
    while len(result) < target_samples:
        result = equal_power_join(result, source, overlap)
    return result[:target_samples]


def _period_analysis(
    source: np.ndarray, sample_rate: int, profile: dict[str, Any]
) -> dict[str, Any]:
    """Estimate one stable fundamental period using the ADR-004 integer contract."""

    centered = np.asarray(source, dtype=np.float64) - float(np.mean(source))
    if len(centered) < 3 or rms(centered) < 1e-5:
        raise ValueError("MICROCYCLE_SOURCE_SILENT_OR_TOO_SHORT")
    lag_min = int(math.ceil(sample_rate / float(profile["pitch_ceiling_hz"])))
    lag_max = min(
        int(math.floor(sample_rate / float(profile["pitch_floor_hz"]))),
        int(math.floor(len(centered) / 3)),
    )
    if lag_max <= lag_min:
        raise ValueError("MICROCYCLE_LAG_RANGE_INVALID")
    scores: list[float] = []
    for lag in range(lag_min, lag_max + 1):
        left = centered[:-lag]
        right = centered[lag:]
        denominator = float(np.sqrt(np.dot(left, left) * np.dot(right, right)))
        score = float(np.dot(left, right) / max(denominator, EPS))
        if not math.isfinite(score):
            raise ValueError("MICROCYCLE_NCC_NOT_FINITE")
        scores.append(score)
    best_score = max(scores)
    threshold = best_score * float(profile["shortest_lag_within_best_ratio"])
    selected_index = next(
        (index for index, score in enumerate(scores) if score >= threshold),
        int(np.argmax(scores)),
    )
    selected_period = lag_min + selected_index
    return {
        "period_samples": selected_period,
        "best_score": best_score,
        "selected_score": scores[selected_index],
        "lag_min": lag_min,
        "lag_max": lag_max,
    }


def analyze_microcycle_source(
    source: np.ndarray, sample_rate: int, profile: dict[str, Any]
) -> dict[str, Any]:
    """Validate that a sustain can enter the production microcycle path."""

    source = np.asarray(source, dtype=np.float64)
    full = _period_analysis(source, sample_rate, profile)
    midpoint = len(source) // 2
    first = _period_analysis(source[:midpoint], sample_rate, profile)
    second = _period_analysis(source[midpoint:], sample_rate, profile)
    split_delta = (
        abs(first["period_samples"] - second["period_samples"])
        / max((first["period_samples"] + second["period_samples"]) / 2.0, 1.0)
        * 100.0
    )
    source_period_count = len(source) / full["period_samples"]
    selected_score = float(full["selected_score"])
    production = (
        selected_score >= float(profile["production_selected_ncc_min"])
        and split_delta <= float(profile["production_split_half_delta_percent_max"])
        and source_period_count >= int(profile["minimum_source_periods"])
    )
    preview = (
        selected_score >= float(profile["preview_selected_ncc_min"])
        and split_delta <= float(profile["preview_split_half_delta_percent_max"])
        and source_period_count >= int(profile["minimum_source_periods"])
    )
    confidence_class = "PRODUCTION" if production else "PREVIEW" if preview else "FAIL"
    analysis = {
        **full,
        "estimated_f0_hz": sample_rate / full["period_samples"],
        "split_half_period_delta_percent": split_delta,
        "source_period_count": source_period_count,
        "confidence_class": confidence_class,
    }
    if not production:
        reason = (
            "MICROCYCLE_CONFIDENCE_PREVIEW_ONLY"
            if preview else "MICROCYCLE_CONFIDENCE_FAILED"
        )
        raise ValueError(
            f"{reason}: selected_ncc={selected_score:.6f}, "
            f"split_delta={split_delta:.4f}%, periods={source_period_count:.3f}"
        )
    return analysis


def phase_locked_microcycle_extend(
    source: np.ndarray,
    target_samples: int,
    sample_rate: int,
    profile: dict[str, Any],
    analysis: dict[str, Any] | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Extend one sustain with an 8P cycle and 2P phase-locked overlap."""

    target_samples = max(1, int(target_samples))
    source = np.asarray(source, dtype=np.float64)
    if target_samples <= len(source):
        result = extend_sustain(source, target_samples, sample_rate)
        return result, {
            "method": "center_crop_v1",
            "estimated_f0_hz": None,
            "period_samples": None,
            "best_score": None,
            "selected_score": None,
            "confidence_class": "NOT_REQUIRED",
            "split_half_period_delta_percent": None,
            "source_period_count": None,
            "cycle_count": 0,
            "overlap_samples": 0,
            "loop_count": 0,
            "target_samples": target_samples,
            "fallback_reason": None,
            "seam_offsets_samples": [],
        }
    analysis = analysis or analyze_microcycle_source(source, sample_rate, profile)
    period = int(analysis["period_samples"])
    cycle_count = int(profile["microcycle_periods"])
    overlap = int(profile["overlap_periods"]) * period
    loop_length = cycle_count * period
    if len(source) < int(profile["minimum_source_periods"]) * period:
        raise ValueError("MICROCYCLE_SOURCE_HAS_FEWER_THAN_TEN_PERIODS")
    if overlap < int(profile["minimum_overlap_samples"]):
        raise ValueError("MICROCYCLE_OVERLAP_TOO_SHORT")
    if loop_length > len(source) or overlap >= loop_length:
        raise ValueError("MICROCYCLE_GEOMETRY_INVALID")
    start = (len(source) - loop_length) // 2
    loop = source[start : start + loop_length].copy()
    result = loop.copy()
    seam_offsets: list[int] = []
    loop_count = 0
    while len(result) < target_samples:
        seam_offsets.append(len(result) - overlap)
        result = equal_power_join(result, loop, overlap)
        loop_count += 1
    result = result[:target_samples]
    if len(result) != target_samples:
        raise RuntimeError("MICROCYCLE_OUTPUT_LENGTH_MISMATCH")
    return result, {
        "method": "phase_locked_microcycle_8p_2p",
        "estimated_f0_hz": round(float(analysis["estimated_f0_hz"]), 6),
        "period_samples": period,
        "best_score": round(float(analysis["best_score"]), 6),
        "selected_score": round(float(analysis["selected_score"]), 6),
        "confidence_class": str(analysis["confidence_class"]),
        "split_half_period_delta_percent": round(
            float(analysis["split_half_period_delta_percent"]), 6
        ),
        "source_period_count": round(float(analysis["source_period_count"]), 6),
        "cycle_count": cycle_count,
        "overlap_samples": overlap,
        "loop_count": loop_count,
        "target_samples": target_samples,
        "fallback_reason": None,
        "seam_offsets_samples": seam_offsets,
    }


def group_note_events(
    events: list[dict[str, Any]], bridge_gap_seconds: float
) -> list[dict[str, Any]]:
    """Group ties/melisma while keeping confident attacks and real gaps separate."""

    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for event in events:
        rest_before = event.get("rest_before_seconds")
        attack = bool(event["attack"]["detected"])
        new_group = (
            current is None
            or bool(event.get("phrase_reset_hint"))
            or attack
            or (rest_before is not None and float(rest_before) >= 0.12)
        )
        if new_group:
            current = {"events": [event]}
            groups.append(current)
        else:
            current["events"].append(event)

    token_counter = 0
    for index, group in enumerate(groups):
        notes = group["events"]
        first = notes[0]
        last = notes[-1]
        stable_start = float(first["relative_start_seconds"])
        detected_onset = first["attack"].get("relative_onset_seconds")
        if detected_onset is None:
            attack_start = stable_start - 0.050
        else:
            attack_start = min(float(detected_onset), stable_start - 0.025)
        group["attack_start"] = max(0.0, attack_start)
        group["stable_start"] = stable_start
        group["stable_end"] = float(last["relative_end_seconds"])
        if index > 0 and bool(first.get("phrase_reset_hint")):
            token_counter = 0
        group["token_index"] = token_counter % len(TOKENS)
        group["token"] = TOKENS[group["token_index"]]
        token_counter += 1
        group["phrase_reset"] = bool(first.get("phrase_reset_hint"))
        group["release"] = bool(last.get("phrase_end_hint"))

    # Stable-pitch tracking drops consonants and short transitions.  If the
    # draft does not identify a real low-energy rest, carry the current vowel
    # to the following attack for gaps up to 280 ms.  This avoids reintroducing
    # the old hard-gate/chopped-vocal failure while keeping line breaks intact.
    for index, group in enumerate(groups):
        group["render_end"] = float(group["stable_end"])
        if index + 1 == len(groups):
            group["release"] = True
            continue
        following = groups[index + 1]
        following_first = following["events"][0]
        explicit_rest = bool(
            following_first.get("rest_before_analysis", {}).get("explicit_rest_hint")
        )
        gap = float(following["attack_start"]) - float(group["stable_end"])
        bridge = (
            not bool(group["release"])
            and not explicit_rest
            and 0.0 < gap <= bridge_gap_seconds
        )
        if bridge:
            group["render_end"] = float(following["attack_start"]) + 0.006
            group["bridged_unstable_gap_seconds"] = round(gap, 4)
        elif gap > 0.0:
            group["release"] = True
    return groups


def source_parts(
    variant: str,
    token_index: int,
    jp_voice: np.ndarray,
    zh_voice: np.ndarray,
    sample_rate: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, str]]:
    jp_attack_unit = JP_UNITS[token_index]
    jp_sustain_unit = JP_UNITS[JP_SUSTAIN_FOR_TOKEN[token_index]]
    zh_unit = ZH_UNITS[token_index]
    if variant == "jp":
        attack_voice, attack_unit = jp_voice, jp_attack_unit
        sustain_voice, sustain_unit = jp_voice, jp_sustain_unit
    elif variant == "hybrid":
        attack_voice, attack_unit = zh_voice, zh_unit
        sustain_voice, sustain_unit = jp_voice, jp_sustain_unit
    elif variant == "zh":
        attack_voice = sustain_voice = zh_voice
        attack_unit = sustain_unit = zh_unit
    else:
        raise ValueError(f"Unknown source variant: {variant}")

    attack_bounds = (attack_unit["slice"][0], attack_unit["sustain"][0])
    sustain_bounds = sustain_unit["sustain"]
    release_bounds = (sustain_unit["sustain"][1], sustain_unit["slice"][1])
    return (
        extract(attack_voice, attack_bounds, sample_rate),
        extract(sustain_voice, sustain_bounds, sample_rate),
        extract(sustain_voice, release_bounds, sample_rate),
        {
            "attack_unit": str(attack_unit["id"]),
            "sustain_unit": str(sustain_unit["id"]),
        },
    )


def articulation_sustain_target(
    jp_sustain: np.ndarray,
    zh_voice: np.ndarray,
    token_index: int,
    sustain_samples: int,
    sample_rate: int,
    profile: dict[str, Any],
) -> tuple[np.ndarray, dict[str, Any]]:
    """Keep a short Mandarin vowel nucleus before the original JP sustain.

    The returned array always has exactly ``sustain_samples`` samples.  A short
    group falls back to the unmodified V1 sustain and records why; it never
    moves an event boundary or steals time from the following attack.
    """

    baseline = extend_sustain(jp_sustain, sustain_samples, sample_rate)
    token = TOKENS[token_index]
    requested_samples = int(round(profile["token_nucleus_ms"][token] * sample_rate / 1000.0))
    cap_samples = int(math.floor(sustain_samples * profile["maximum_group_sustain_ratio"]))
    nucleus_samples = min(requested_samples, cap_samples)
    crossfade_samples = int(round(profile["crossfade_ms"] * sample_rate / 1000.0))
    minimum_samples = int(round(profile["minimum_applied_nucleus_ms"] * sample_rate / 1000.0))
    metadata: dict[str, Any] = {
        "articulation_preset_id": profile["preset_id"],
        "zh_nucleus_unit": str(ZH_UNITS[token_index]["id"]),
        "requested_nucleus_ms": profile["token_nucleus_ms"][token],
        "maximum_group_sustain_ratio": profile["maximum_group_sustain_ratio"],
        "crossfade_ms": profile["crossfade_ms"],
    }
    if nucleus_samples < max(minimum_samples, crossfade_samples + 1):
        metadata.update({
            "fallback_to_v1": True,
            "fallback_reason": "GROUP_SUSTAIN_TOO_SHORT_FOR_MINIMUM_NUCLEUS",
            "applied_nucleus_ms": 0.0,
            "applied_group_sustain_ratio": 0.0,
        })
        return baseline, metadata

    jp_target_samples = sustain_samples - nucleus_samples + crossfade_samples
    if jp_target_samples <= crossfade_samples:
        metadata.update({
            "fallback_to_v1": True,
            "fallback_reason": "GROUP_SUSTAIN_TOO_SHORT_FOR_JP_REMAINDER",
            "applied_nucleus_ms": 0.0,
            "applied_group_sustain_ratio": 0.0,
        })
        return baseline, metadata

    zh_unit = ZH_UNITS[token_index]
    zh_nucleus = extract(zh_voice, zh_unit["sustain"], sample_rate)
    if len(zh_nucleus) < 8:
        raise ValueError(f"empty or invalid ZH vowel nucleus for token {token}")
    zh_target = extend_sustain(zh_nucleus, nucleus_samples, sample_rate)
    jp_target = extend_sustain(jp_sustain, jp_target_samples, sample_rate)
    combined = equal_power_join(zh_target, jp_target, crossfade_samples)
    if len(combined) != sustain_samples:
        raise RuntimeError(
            f"articulation sustain changed timeline: {len(combined)} != {sustain_samples}"
        )
    metadata.update({
        "fallback_to_v1": False,
        "fallback_reason": None,
        "applied_nucleus_ms": round(nucleus_samples * 1000.0 / sample_rate, 4),
        "applied_group_sustain_ratio": round(nucleus_samples / sustain_samples, 6),
    })
    return combined, metadata


def target_pitch_curve(
    note_events: list[dict[str, Any]],
    local_start: float,
    local_duration: float,
    step: float = 0.005,
) -> tuple[np.ndarray, np.ndarray]:
    times = np.arange(step / 2.0, local_duration, step)
    first_hz = midi_hz(int(note_events[0]["quantized_midi"]))
    target = np.full(len(times), first_hz, dtype=np.float64)
    previous_hz = first_hz
    previous_end = 0.0
    for event in note_events:
        start = float(event["relative_start_seconds"]) - local_start
        end = float(event["relative_end_seconds"]) - local_start
        hz = midi_hz(int(event["quantized_midi"]))
        gap = (times >= previous_end) & (times < start)
        if np.any(gap) and start > previous_end:
            target[gap] = np.linspace(previous_hz, hz, int(np.sum(gap)), endpoint=False)
        active = (times >= start) & (times < end)
        target[active] = hz
        previous_hz = hz
        previous_end = end
    target[times >= previous_end] = previous_hz

    # A 12 ms transition removes zipper clicks while retaining the target's
    # intentionally hard note centres.
    sigma = max(0.5, 0.012 / step / 3.0)
    target = gaussian_filter1d(target, sigma=sigma, mode="nearest")
    return times, target


def repitch_group(
    source: np.ndarray,
    note_events: list[dict[str, Any]],
    local_start: float,
    sample_rate: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    duration = len(source) / sample_rate
    tier_times, target = target_pitch_curve(note_events, local_start, duration)
    try:
        sound = parselmouth.Sound(np.ascontiguousarray(source), sampling_frequency=sample_rate)
        manipulation = praat_call(sound, "To Manipulation", 0.005, 70.0, 900.0)
        tier = praat_call(manipulation, "Extract pitch tier")
        praat_call(tier, "Remove points between", 0.0, duration)
        for point_time, frequency in zip(tier_times, target):
            praat_call(tier, "Add point", float(point_time), float(frequency))
        praat_call([tier, manipulation], "Replace pitch tier")
        rendered = praat_call(manipulation, "Get resynthesis (overlap-add)").values[0]
    except Exception as exc:
        raise RuntimeError(
            "Praat PSOLA resynthesis failed; refusing unpitched fallback"
        ) from exc
    rendered = np.pad(rendered, (0, max(0, len(source) - len(rendered))))[: len(source)]
    return np.asarray(rendered, dtype=np.float64), tier_times, target


def independent_group_f0_check(
    audio: np.ndarray,
    note_events: list[dict[str, Any]],
    local_start: float,
    sample_rate: int,
    octave_threshold_cents: float,
) -> dict[str, Any]:
    """Use pyworld only as a V1.1 safety check, independent of Praat resynthesis."""

    try:
        import pyworld
    except ImportError as exc:
        raise RuntimeError("pyworld is required by the V1.1 F0 safety gate") from exc
    waveform = np.ascontiguousarray(audio, dtype=np.float64)
    raw_f0, times = pyworld.harvest(
        waveform, sample_rate, f0_floor=70.0, f0_ceil=900.0, frame_period=5.0
    )
    f0 = pyworld.stonemask(waveform, raw_f0, times, sample_rate)
    failures: list[dict[str, Any]] = []
    covered = 0
    for event in note_events:
        start = float(event["relative_start_seconds"]) - local_start + 0.010
        end = float(event["relative_end_seconds"]) - local_start - 0.010
        if end <= start:
            start = float(event["relative_start_seconds"]) - local_start
            end = float(event["relative_end_seconds"]) - local_start
        observed = f0[(times >= start) & (times < end) & (f0 > 0)]
        if not len(observed):
            continue
        covered += 1
        target = midi_hz(int(event["quantized_midi"]))
        median_cents = float(np.median(1200.0 * np.log2(observed / target)))
        if abs(median_cents) >= octave_threshold_cents:
            failures.append(
                {
                    "event_id": int(event["event_id"]),
                    "median_cents": round(median_cents, 4),
                }
            )
    return {
        "status": "PASS" if not failures and covered == len(note_events) else "FAIL",
        "covered_event_count": covered,
        "event_count": len(note_events),
        "failures": failures,
    }


def assemble_and_repitch_group(
    attack_target: np.ndarray,
    sustain_target: np.ndarray,
    release: np.ndarray,
    group: dict[str, Any],
    attack_start: float,
    attack_samples: int,
    sustain_samples: int,
    guide_rms: float,
    median_guide_rms: float,
    sample_rate: int,
) -> tuple[np.ndarray, int]:
    """Build one group with the frozen V1 join, pitch, gain, and fade chain."""

    local = equal_power_join(
        attack_target,
        sustain_target,
        min(int(round(0.004 * sample_rate)), attack_samples // 3, sustain_samples // 3),
    )
    if bool(group["release"]) and len(release):
        release_samples = min(
            len(release),
            max(
                1,
                int(
                    round(
                        min(
                            0.050,
                            float(group["events"][-1].get("rest_after_seconds") or 0.050),
                        )
                        * sample_rate
                    )
                ),
            ),
        )
        local = equal_power_join(
            local,
            release[:release_samples],
            min(int(round(0.006 * sample_rate)), release_samples // 3),
        )
    local, tier_times, _target = repitch_group(
        local, group["events"], attack_start, sample_rate
    )
    voiced_start = min(len(local) - 1, attack_samples)
    local_rms = rms(local[voiced_start:])
    relative = float(np.clip(guide_rms / max(median_guide_rms, EPS), 0.55, 1.60))
    local *= 0.105 * relative / max(local_rms, EPS)
    fade = min(int(round(0.005 * sample_rate)), len(local) // 3)
    if fade:
        local[:fade] *= np.linspace(0.0, 1.0, fade)
        local[-fade:] *= np.linspace(1.0, 0.0, fade)
    return local, len(tier_times)


def smooth_compressor(
    audio: np.ndarray,
    sample_rate: int,
    *,
    threshold_dbfs: float = -20.0,
    ratio: float = 3.0,
) -> tuple[np.ndarray, float]:
    window = max(3, int(round(0.020 * sample_rate)))
    kernel = signal.windows.hann(window * 2 + 1)
    kernel /= np.sum(kernel)
    envelope = np.sqrt(signal.fftconvolve(audio * audio, kernel, mode="same") + EPS)
    envelope_db = 20.0 * np.log10(np.maximum(envelope, EPS))
    reduction_db = np.minimum(
        0.0, (threshold_dbfs - envelope_db) * (1.0 - 1.0 / ratio)
    )
    target_gain = np.power(10.0, reduction_db / 20.0)
    gain = np.empty_like(target_gain)
    gain[0] = target_gain[0]
    attack = math.exp(-1.0 / (0.008 * sample_rate))
    release = math.exp(-1.0 / (0.090 * sample_rate))
    for index in range(1, len(gain)):
        coefficient = attack if target_gain[index] < gain[index - 1] else release
        gain[index] = coefficient * gain[index - 1] + (1.0 - coefficient) * target_gain[index]
    return audio * gain, float(np.min(20.0 * np.log10(np.maximum(gain, EPS))))


def process_dry_bus(audio: np.ndarray, sample_rate: int) -> tuple[np.ndarray, float]:
    highpass = signal.butter(3, 90.0, btype="highpass", fs=sample_rate, output="sos")
    lowpass = signal.butter(3, 14500.0, btype="lowpass", fs=sample_rate, output="sos")
    processed = signal.sosfiltfilt(highpass, audio)
    processed = signal.sosfiltfilt(lowpass, processed)
    return smooth_compressor(processed, sample_rate)


def render_variant(
    variant: str,
    groups: list[dict[str, Any]],
    guide: np.ndarray,
    jp_voice: np.ndarray,
    zh_voice: np.ndarray,
    sample_rate: int,
    articulation_profile: dict[str, Any] | None = None,
    sustain_profile: dict[str, Any] | None = None,
    f0_safety_group_ids: set[int] | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    is_articulation_poc = variant == ARTICULATION_POC_VARIANT
    is_microcycle = variant == MICROCYCLE_VARIANT
    if is_articulation_poc != (articulation_profile is not None):
        raise ValueError("V1.1 POC variant and articulation profile must be selected together")
    if is_microcycle != (sustain_profile is not None):
        raise ValueError("V1.1 variant and sustain profile must be selected together")
    if articulation_profile is not None and sustain_profile is not None:
        raise ValueError("articulation and microcycle profiles cannot be combined")
    f0_safety_group_ids = set(f0_safety_group_ids or set())
    if f0_safety_group_ids and sustain_profile is None:
        raise ValueError("F0 safety group ids require the V1.1 sustain profile")
    output = np.zeros(len(guide), dtype=np.float64)
    guide_group_rms = []
    for group in groups:
        start = int(round(float(group["stable_start"]) * sample_rate))
        end = int(round(float(group["stable_end"]) * sample_rate))
        guide_group_rms.append(rms(guide[max(0, start):min(len(guide), end)]))
    median_guide_rms = float(np.median([value for value in guide_group_rms if value > 0]))

    rendered_groups = []
    pitch_target_points = 0
    sustain_analysis_cache: dict[str, dict[str, Any]] = {}
    for group_index, (group, guide_rms) in enumerate(zip(groups, guide_group_rms)):
        token_index = int(group["token_index"])
        attack, sustain, release, source_meta = source_parts(
            "hybrid" if (is_articulation_poc or is_microcycle) else variant,
            token_index, jp_voice, zh_voice, sample_rate
        )
        attack_start = float(group["attack_start"])
        stable_start = float(group["stable_start"])
        stable_end = float(group["stable_end"])
        render_end = float(group["render_end"])
        attack_samples = max(1, int(round((stable_start - attack_start) * sample_rate)))
        attack_target = signal.resample(attack, attack_samples)
        sustain_samples = max(1, int(round((render_end - stable_start) * sample_rate)))
        if sustain_profile is not None:
            sustain_samples = max(1, int(round((render_end - stable_start) * sample_rate)))
            analysis: dict[str, Any] | None = None
            if sustain_samples > len(sustain):
                cache_key = str(source_meta["sustain_unit"])
                analysis = sustain_analysis_cache.get(cache_key)
                if analysis is None:
                    analysis = analyze_microcycle_source(sustain, sample_rate, sustain_profile)
                    sustain_analysis_cache[cache_key] = analysis
            sustain_target, sustain_meta = phase_locked_microcycle_extend(
                sustain, sustain_samples, sample_rate, sustain_profile, analysis
            )
            if group_index + 1 in f0_safety_group_ids:
                if sustain_meta.get("method") != "phase_locked_microcycle_8p_2p":
                    raise RuntimeError(
                        f"F0 safety group {group_index + 1} did not enter microcycle path"
                    )
                candidate_meta = dict(sustain_meta)
                sustain_target = extend_sustain(sustain, sustain_samples, sample_rate)
                sustain_meta = {
                    **candidate_meta,
                    "method": "f0_safety_v1_group_fallback",
                    "original_microcycle_method": candidate_meta["method"],
                    "candidate_loop_count": candidate_meta["loop_count"],
                    "loop_count": 0,
                    "seam_offsets_samples": [],
                    "fallback_reason": "EXTERNAL_INDEPENDENT_F0_EVENT_MEDIAN_OCTAVE_ERROR",
                }
            articulation_meta = {}
        elif articulation_profile is None:
            # Frozen V1 default path: keep the original operation byte-for-byte.
            sustain_target = extend_sustain(sustain, sustain_samples, sample_rate)
            articulation_meta: dict[str, Any] = {}
            sustain_meta: dict[str, Any] = {}
        else:
            sustain_target, articulation_meta = articulation_sustain_target(
                sustain, zh_voice, token_index, sustain_samples, sample_rate,
                articulation_profile,
            )
            sustain_meta = {}
        pitch_scope = "whole_group_v1p1" if sustain_profile is not None else "whole_group_v1"
        if sustain_meta.get("method") == "f0_safety_v1_group_fallback":
            pitch_scope = "whole_group_frozen_v1_f0_safety"
        local, tier_count = assemble_and_repitch_group(
            attack_target,
            sustain_target,
            release,
            group,
            attack_start,
            attack_samples,
            sustain_samples,
            guide_rms,
            median_guide_rms,
            sample_rate,
        )
        pitch_target_points += tier_count

        destination = int(round(attack_start * sample_rate))
        count = min(len(local), len(output) - destination)
        if count > 0:
            output[destination : destination + count] += local[:count]
        group_report = {
                "group_id": group_index + 1,
                "token": group["token"],
                "attack_start_seconds": round(attack_start, 4),
                "stable_start_seconds": round(stable_start, 4),
                "stable_end_seconds": round(stable_end, 4),
                "render_end_seconds": round(render_end, 4),
                "bridged_unstable_gap_seconds": group.get("bridged_unstable_gap_seconds"),
                "note_event_ids": [int(event["event_id"]) for event in group["events"]],
                **source_meta,
                **articulation_meta,
                **sustain_meta,
                "pitch_scope": pitch_scope,
            }
        rendered_groups.append(group_report)

    output, compressor_reduction = process_dry_bus(output, sample_rate)
    report: dict[str, Any] = {
        "variant": variant,
        "syllable_group_count": len(groups),
        "note_event_count": sum(len(group["events"]) for group in groups),
        "pitch_target_points": pitch_target_points,
        "compressor_min_gain_db": round(compressor_reduction, 3),
        "groups": rendered_groups,
    }
    if articulation_profile is not None:
        report["articulation_poc"] = {
            "preset_id": articulation_profile["preset_id"],
            "preset_version": articulation_profile["preset_version"],
            "applied_group_count": sum(
                not bool(group["fallback_to_v1"]) for group in rendered_groups
            ),
            "fallback_group_count": sum(
                bool(group["fallback_to_v1"]) for group in rendered_groups
            ),
        }
    if sustain_profile is not None:
        methods = [str(group.get("method")) for group in rendered_groups]
        report["sustain_extension"] = {
            "preset_id": sustain_profile["preset_id"],
            "preset_version": sustain_profile["preset_version"],
            "standard_microcycle_groups": methods.count("phase_locked_microcycle_8p_2p"),
            "cropped_groups": methods.count("center_crop_v1"),
            "f0_safety_v1_group_fallbacks": methods.count("f0_safety_v1_group_fallback"),
            "requested_f0_safety_group_ids": sorted(f0_safety_group_ids),
            "failures": 0,
        }
        fallback_ratio = report["sustain_extension"]["f0_safety_v1_group_fallbacks"] / max(len(groups), 1)
        report["sustain_extension"]["f0_safety_fallback_ratio"] = round(fallback_ratio, 6)
        actual_fallback_ids = [
            int(group["group_id"])
            for group in rendered_groups
            if group.get("method") == "f0_safety_v1_group_fallback"
        ]
        if actual_fallback_ids != sorted(f0_safety_group_ids):
            raise RuntimeError(
                f"F0 safety group contract mismatch: {actual_fallback_ids} != "
                f"{sorted(f0_safety_group_ids)}"
            )
        if fallback_ratio > float(sustain_profile["f0_safety_maximum_group_ratio"]):
            fallback_ids = [
                int(group["group_id"])
                for group in rendered_groups
                if group.get("method") == "f0_safety_v1_group_fallback"
            ]
            raise RuntimeError(
                "V1.1 F0 safety fallback ratio exceeds preset maximum: "
                f"{fallback_ratio:.6f} > {sustain_profile['f0_safety_maximum_group_ratio']}; "
                f"groups={fallback_ids}"
            )
    return output, report


def delay(audio: np.ndarray, samples: int) -> np.ndarray:
    if samples <= 0:
        return audio.copy()
    return np.pad(audio, (samples, 0))[: len(audio)]


def section_envelope(
    length: int,
    boundary: int,
    before: float,
    after: float,
    transition_samples: int,
) -> np.ndarray:
    """Create a cheap deterministic section crossfade without a huge Gaussian."""

    envelope = np.full(length, before, dtype=np.float64)
    boundary = int(np.clip(boundary, 0, length))
    envelope[boundary:] = after
    half = max(1, transition_samples // 2)
    start = max(0, boundary - half)
    end = min(length, boundary + half)
    if end > start:
        envelope[start:end] = np.linspace(before, after, end - start)
    return envelope


def stereo_vocal_production(
    vocal: np.ndarray, sample_rate: int, climax_seconds: float
) -> np.ndarray:
    """Centre-safe vocal with low-level detuned doubles and short ambience."""

    climax = int(round(climax_seconds * sample_rate))
    transition = max(1, int(round(0.45 * sample_rate)))
    width = section_envelope(
        len(vocal),
        climax,
        10.0 ** (-7.5 / 20.0),
        10.0 ** (-4.0 / 20.0),
        transition,
    )

    left_double = delay(
        librosa.effects.pitch_shift(
            vocal, sr=sample_rate, n_steps=-0.045, res_type="kaiser_fast"
        ),
        int(round(0.0047 * sample_rate)),
    )
    right_double = delay(
        librosa.effects.pitch_shift(
            vocal, sr=sample_rate, n_steps=0.045, res_type="kaiser_fast"
        ),
        int(round(0.0083 * sample_rate)),
    )
    stereo = np.column_stack((vocal, vocal))
    stereo[:, 0] += left_double * width
    stereo[:, 1] += right_double * width

    # A deterministic 280 ms room: early reflections do most of the audible
    # work; the low noise tail only prevents the vocal from sounding pasted on.
    rng = np.random.default_rng(20260810)
    ir_length = int(round(0.280 * sample_rate))
    times = np.arange(ir_length, dtype=np.float64) / sample_rate
    decay = np.exp(-times / 0.075)
    noise = rng.normal(0.0, 1.0, ir_length) * decay
    noise[0] = 0.0
    for seconds, gain in ((0.019, 0.75), (0.037, 0.48), (0.061, 0.31), (0.097, 0.18)):
        index = min(ir_length - 1, int(round(seconds * sample_rate)))
        noise[index] += gain
    room_left = signal.oaconvolve(vocal, noise, mode="full")[: len(vocal)]
    room_right = signal.oaconvolve(vocal, noise[::-1], mode="full")[: len(vocal)]
    room_peak = max(float(np.max(np.abs(room_left))), float(np.max(np.abs(room_right))), EPS)
    room = np.column_stack((room_left, room_right)) / room_peak * max(
        float(np.max(np.abs(vocal))), EPS
    )
    room_gain = section_envelope(
        len(vocal),
        climax,
        10.0 ** (-15.0 / 20.0),
        10.0 ** (-10.0 / 20.0),
        transition,
    )
    stereo += room * room_gain[:, None]
    return stereo


def dynamic_instrument_duck(
    instrumental: np.ndarray, vocal: np.ndarray, sample_rate: int
) -> np.ndarray:
    """Make 2–5 kHz room for the synthetic attack without pumping the bass."""

    result = instrumental.copy()
    mid = np.mean(result, axis=1)
    band = signal.butter(3, (1800.0, 5200.0), btype="bandpass", fs=sample_rate, output="sos")
    presence = signal.sosfiltfilt(band, mid)
    rms_window = max(3, int(round(0.045 * sample_rate)))
    envelope = np.sqrt(
        signal.oaconvolve(
            vocal * vocal,
            np.full(rms_window, 1.0 / rms_window, dtype=np.float64),
            mode="same",
        )
        + EPS
    )
    envelope /= max(float(np.percentile(envelope, 95)), EPS)
    envelope = np.clip(envelope, 0.0, 1.0)
    amount = (1.0 - 10.0 ** (-1.6 / 20.0)) * envelope
    result[:, 0] -= presence * amount
    result[:, 1] -= presence * amount
    return result


def section_balance(
    vocal_stereo: np.ndarray,
    instrumental: np.ndarray,
    dry_vocal: np.ndarray,
    sample_rate: int,
    climax_seconds: float,
) -> tuple[np.ndarray, dict[str, float]]:
    midpoint = int(round(climax_seconds * sample_rate))
    envelope = np.abs(signal.hilbert(dry_vocal))
    active = envelope >= max(float(np.percentile(envelope, 45)), 10.0 ** (-42.0 / 20.0))
    gain = np.ones(len(dry_vocal), dtype=np.float64)
    stats: dict[str, float] = {}
    for label, start, end, desired_db in (
        ("pre_climax", 0, midpoint, -1.2),
        ("climax", midpoint, len(dry_vocal), 3.2),
    ):
        mask = active[start:end]
        vocal_mid = np.mean(vocal_stereo[start:end], axis=1)
        inst_mid = np.mean(instrumental[start:end], axis=1)
        vocal_rms = rms(vocal_mid[mask]) if np.any(mask) else rms(vocal_mid)
        inst_rms = rms(inst_mid[mask]) if np.any(mask) else rms(inst_mid)
        desired = inst_rms * 10.0 ** (desired_db / 20.0)
        scale = float(np.clip(desired / max(vocal_rms, EPS), 0.08, 12.0))
        gain[start:end] = scale
        stats[f"{label}_vocal_to_instrument_target_db"] = desired_db
        stats[f"{label}_vocal_gain_db"] = round(db(scale), 3)
    gain = section_envelope(
        len(dry_vocal),
        midpoint,
        float(gain[0]),
        float(gain[-1]),
        max(1, int(round(0.300 * sample_rate))),
    )
    return vocal_stereo * gain[:, None], stats


def stereo_width_db(audio: np.ndarray) -> float:
    mid = (audio[:, 0] + audio[:, 1]) * 0.5
    side = (audio[:, 0] - audio[:, 1]) * 0.5
    return db(rms(side) / max(rms(mid), EPS))


def spectral_metrics(audio: np.ndarray, sample_rate: int) -> dict[str, float]:
    mono = np.mean(audio, axis=1) if audio.ndim == 2 else audio
    stft = np.abs(librosa.stft(mono, n_fft=2048, hop_length=512)) ** 2
    frequencies = librosa.fft_frequencies(sr=sample_rate, n_fft=2048)
    total = float(np.sum(stft))
    presence = float(np.sum(stft[(frequencies >= 4000) & (frequencies < 8000)]))
    centroid = librosa.feature.spectral_centroid(S=np.sqrt(stft), sr=sample_rate)[0]
    return {
        "spectral_centroid_median_hz": round(float(np.median(centroid)), 2),
        "presence_4_8k_db_of_total": round(10.0 * math.log10(max(presence, EPS) / max(total, EPS)), 2),
    }


def main() -> int:
    started = time.perf_counter()
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-draft", type=Path, required=True)
    parser.add_argument("--guide", type=Path, required=True)
    parser.add_argument("--instrumental", type=Path, required=True)
    parser.add_argument("--jp-voice", type=Path, required=True)
    parser.add_argument("--zh-voice", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sample-rate", type=int, default=44100)
    parser.add_argument("--climax-seconds", type=float, default=10.085)
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=list(BASE_VARIANTS))
    parser.add_argument(
        "--articulation-preset",
        type=Path,
        help="Explicit experimental profile required by hybrid_v1p1_poc1",
    )
    parser.add_argument(
        "--sustain-preset",
        type=Path,
        help="Explicit frozen phase-locked profile required by hybrid_v1p1",
    )
    parser.add_argument(
        "--f0-safety-group-ids",
        nargs="*",
        type=int,
        default=[],
        help="Explicit group ids selected by the wrapper's independent whole-dry F0 check",
    )
    parser.add_argument("--slug", default="greenlight")
    parser.add_argument("--title", default="Green Light Hajimi note-event POC")
    parser.add_argument("--bridge-gap-seconds", type=float, default=0.280)
    args = parser.parse_args()

    articulation_profile = (
        load_articulation_preset(args.articulation_preset)
        if args.articulation_preset is not None
        else None
    )
    sustain_profile = (
        load_sustain_preset(args.sustain_preset)
        if args.sustain_preset is not None
        else None
    )
    candidate_selected = ARTICULATION_POC_VARIANT in args.variants
    if candidate_selected != (articulation_profile is not None):
        parser.error(
            "hybrid_v1p1_poc1 and --articulation-preset must be selected together"
        )
    microcycle_selected = MICROCYCLE_VARIANT in args.variants
    if microcycle_selected != (sustain_profile is not None):
        parser.error("hybrid_v1p1 and --sustain-preset must be selected together")
    if candidate_selected and microcycle_selected:
        parser.error("articulation POC and production microcycle cannot render together")
    if args.f0_safety_group_ids and not microcycle_selected:
        parser.error("--f0-safety-group-ids requires hybrid_v1p1")
    if len(args.f0_safety_group_ids) != len(set(args.f0_safety_group_ids)):
        parser.error("--f0-safety-group-ids must be unique")
    if any(group_id <= 0 for group_id in args.f0_safety_group_ids):
        parser.error("--f0-safety-group-ids must be positive")

    draft = json.loads(args.event_draft.read_text(encoding="utf-8"))
    groups = group_note_events(draft["events"], args.bridge_gap_seconds)
    guide = load_audio(args.guide, mono=True, sample_rate=args.sample_rate)
    instrumental = load_audio(args.instrumental, mono=False, sample_rate=args.sample_rate)
    jp_voice = load_audio(args.jp_voice, mono=True, sample_rate=args.sample_rate)
    zh_voice = load_audio(args.zh_voice, mono=True, sample_rate=args.sample_rate)
    if len(guide) != len(instrumental):
        raise ValueError(
            "Guide/instrumental frame mismatch: "
            f"{len(guide)} != {len(instrumental)}; silent truncation is forbidden"
        )
    length = len(guide)
    instrumental = instrumental[:, :2]
    args.output_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "mode": "song-specific-note-event-poc-v1",
        "title": args.title,
        "slug": args.slug,
        "guide_audio_policy": "timing, note and dynamics guide only; never mixed into result",
        "sample_rate": args.sample_rate,
        "duration_seconds": round(length / args.sample_rate, 6),
        "climax_seconds": args.climax_seconds,
        "bridge_gap_seconds": args.bridge_gap_seconds,
        "token_cycle": "".join(TOKENS),
        "inputs": {
            "event_draft": {"path": str(args.event_draft), "sha256": sha256(args.event_draft)},
            "guide": {"path": str(args.guide), "sha256": sha256(args.guide)},
            "instrumental": {"path": str(args.instrumental), "sha256": sha256(args.instrumental)},
            "jp_voice": {"path": str(args.jp_voice), "sha256": sha256(args.jp_voice)},
            "zh_voice": {"path": str(args.zh_voice), "sha256": sha256(args.zh_voice)},
        },
        "draft_status": draft.get("status"),
        "note_event_count": len(draft["events"]),
        "syllable_group_count": len(groups),
        "variants": {},
        "manual_listening": "NOT_PERFORMED_BY_AUTOMATION",
    }
    if articulation_profile is not None:
        report["articulation_poc"] = {
            **articulation_profile,
            "preset_path": str(args.articulation_preset),
            "preset_sha256": sha256(args.articulation_preset),
            "status": "EXPERIMENTAL_AB_ONLY",
        }
    if sustain_profile is not None:
        report["sustain_extension"] = {
            **sustain_profile,
            "preset_path": str(args.sustain_preset),
            "preset_sha256": sha256(args.sustain_preset),
            "status": "FROZEN_INTERNAL_BASELINE",
        }

    for variant in args.variants:
        dry, variant_meta = render_variant(
            variant, groups, guide, jp_voice, zh_voice, args.sample_rate,
            articulation_profile=(
                articulation_profile if variant == ARTICULATION_POC_VARIANT else None
            ),
            sustain_profile=(
                sustain_profile if variant == MICROCYCLE_VARIANT else None
            ),
            f0_safety_group_ids=(
                set(args.f0_safety_group_ids) if variant == MICROCYCLE_VARIANT else None
            ),
        )
        instrumental_ducked = dynamic_instrument_duck(instrumental, dry, args.sample_rate)
        produced = stereo_vocal_production(dry, args.sample_rate, args.climax_seconds)
        produced, balance_meta = section_balance(
            produced, instrumental_ducked, dry, args.sample_rate, args.climax_seconds
        )
        mix = instrumental_ducked + produced
        peak = max(float(np.max(np.abs(mix))), EPS)
        premaster_scale = min(1.0, 10.0 ** (-3.0 / 20.0) / peak)
        mix *= premaster_scale
        produced *= premaster_scale
        dry *= premaster_scale

        prefix = args.output_dir / f"{args.slug}_{variant}"
        sf.write(prefix.with_name(prefix.name + "_dry.wav"), dry, args.sample_rate, subtype="PCM_24")
        sf.write(prefix.with_name(prefix.name + "_vocal.wav"), produced, args.sample_rate, subtype="PCM_24")
        sf.write(prefix.with_name(prefix.name + "_premaster.wav"), mix, args.sample_rate, subtype="PCM_24")
        report["variants"][variant] = {
            **variant_meta,
            **balance_meta,
            "premaster_gain_db": round(db(premaster_scale), 3),
            "premaster_sample_peak_dbfs": round(db(float(np.max(np.abs(mix)))), 3),
            "vocal_side_to_mid_db": round(stereo_width_db(produced), 3),
            "dry_spectral": spectral_metrics(dry, args.sample_rate),
            "files": {
                "dry": str(prefix.with_name(prefix.name + "_dry.wav")),
                "vocal": str(prefix.with_name(prefix.name + "_vocal.wav")),
                "premaster": str(prefix.with_name(prefix.name + "_premaster.wav")),
            },
        }

    usage = resource.getrusage(resource.RUSAGE_SELF)
    max_rss_bytes = int(
        usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024
    )
    report["performance_measurement"] = {
        "wall_seconds": round(time.perf_counter() - started, 3),
        "user_cpu_seconds": round(float(usage.ru_utime), 3),
        "system_cpu_seconds": round(float(usage.ru_stime), 3),
        "max_resident_set_mb": round(max_rss_bytes / 1024.0 / 1024.0, 2),
    }
    report_path = args.output_dir / "render-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report_path), "variants": args.variants}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
