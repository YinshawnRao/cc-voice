#!/usr/bin/env python3
"""Extract a conservative note/onset draft from a pre-cropped vocal stem.

The input must already be the exact delivery window.  Refusing long files is a
deliberate performance guard: full-song transcription, separation and pitch
analysis must happen as separate steps, never concurrently inside this tool.

The JSON is evidence for rendering and human correction, not a score claim.
It retains octave-fold reasons and only labels stable semitone runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import resource
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import librosa
import numpy as np
import parselmouth
import soundfile as sf
from scipy import signal


EPS = 1e-10
HOP_SECONDS = 0.01
MIN_EVENT_SECONDS = 0.07
MAX_BRIDGE_SECONDS = 0.04


@dataclass
class Run:
    start: int
    end: int
    midi: int


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def note_name(midi: int) -> str:
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[midi % 12]}{midi // 12 - 1}"


def db(value: float) -> float:
    return 20.0 * math.log10(max(abs(value), EPS))


def true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    padded = np.pad(mask.astype(np.int8), (1, 1))
    edges = np.diff(padded)
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def smooth_register(midi: np.ndarray, voiced: np.ndarray) -> np.ndarray:
    result = midi.copy()
    for start, end in true_runs(voiced):
        count = end - start
        if count < 3:
            continue
        kernel = min(7, count if count % 2 else count - 1)
        if kernel >= 3:
            result[start:end] = signal.medfilt(result[start:end], kernel_size=kernel)
    return result


def fold_register(
    raw_f0: np.ndarray, midi_min: int, midi_max: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    corrected = raw_f0.copy()
    correction = np.full(len(raw_f0), "none", dtype=object)
    voiced = raw_f0 > 0.0
    midi = np.full(len(raw_f0), np.nan, dtype=np.float64)
    midi[voiced] = 69.0 + 12.0 * np.log2(raw_f0[voiced] / 440.0)
    low = voiced & (midi < midi_min)
    high = voiced & (midi > midi_max)
    while np.any(low):
        corrected[low] *= 2.0
        midi[low] += 12.0
        correction[low] = "octave_up_to_allowed_register"
        low = voiced & (midi < midi_min)
    while np.any(high):
        corrected[high] /= 2.0
        midi[high] -= 12.0
        correction[high] = "octave_down_to_allowed_register"
        high = voiced & (midi > midi_max)
    return corrected, midi, correction


def bridge_same_label_gaps(labels: np.ndarray) -> np.ndarray:
    result = labels.copy()
    limit = max(1, int(round(MAX_BRIDGE_SECONDS / HOP_SECONDS)))
    for start, end in true_runs(result < 0):
        if (
            end - start <= limit
            and start > 0
            and end < len(result)
            and result[start - 1] >= 0
            and result[start - 1] == result[end]
        ):
            result[start:end] = result[start - 1]
    return result


def label_runs(labels: np.ndarray) -> list[Run]:
    minimum = max(1, int(round(MIN_EVENT_SECONDS / HOP_SECONDS)))
    output: list[Run] = []
    if not len(labels):
        return output
    start = 0
    current = int(labels[0])
    for index in range(1, len(labels) + 1):
        value = int(labels[index]) if index < len(labels) else -9999
        if value == current:
            continue
        if current >= 0 and index - start >= minimum:
            output.append(Run(start, index, current))
        start = index
        current = value
    return output


def split_rearticulations(
    runs: list[Run], onset_times: np.ndarray, frame_times: np.ndarray
) -> list[Run]:
    minimum = max(1, int(round(MIN_EVENT_SECONDS / HOP_SECONDS)))
    output: list[Run] = []
    for run in runs:
        start_time = frame_times[run.start]
        end_time = frame_times[min(run.end - 1, len(frame_times) - 1)] + HOP_SECONDS
        candidates = onset_times[
            (onset_times >= start_time + MIN_EVENT_SECONDS)
            & (onset_times <= end_time - MIN_EVENT_SECONDS)
        ]
        boundaries = [run.start]
        for onset in candidates:
            frame = int(np.argmin(np.abs(frame_times - onset)))
            if frame - boundaries[-1] >= minimum:
                boundaries.append(frame)
        if run.end - boundaries[-1] < minimum and len(boundaries) > 1:
            boundaries.pop()
        boundaries.append(run.end)
        output.extend(
            Run(left, right, run.midi)
            for left, right in zip(boundaries, boundaries[1:])
            if right - left >= minimum
        )
    return output


def rms_dbfs(audio: np.ndarray) -> float:
    return db(float(np.sqrt(np.mean(audio * audio)))) if audio.size else -200.0


def nearest_onset(
    start_seconds: float,
    onset_times: np.ndarray,
    onset_strengths: np.ndarray,
) -> dict[str, Any]:
    candidates = np.flatnonzero(np.abs(onset_times - start_seconds) <= 0.085)
    if not candidates.size:
        return {
            "detected": False,
            "relative_onset_seconds": None,
            "pitch_stability_lead_ms": None,
            "normalized_strength": None,
            "confidence": "low",
            "fallback": "stable_pitch_entry",
        }
    index = int(candidates[np.argmin(np.abs(onset_times[candidates] - start_seconds))])
    onset = float(onset_times[index])
    lead = (start_seconds - onset) * 1000.0
    return {
        "detected": True,
        "relative_onset_seconds": round(onset, 4),
        "pitch_stability_lead_ms": round(lead, 2),
        "normalized_strength": round(float(onset_strengths[index]), 3),
        "confidence": "high" if abs(lead) <= 35.0 else "medium",
    }


def extract(args: argparse.Namespace) -> dict[str, Any]:
    audio, sample_rate = sf.read(args.vocal, always_2d=True, dtype="float64")
    duration = len(audio) / sample_rate
    if duration > args.max_duration:
        raise ValueError(
            f"Refusing {duration:.1f}s input; crop to <= {args.max_duration:.1f}s first"
        )
    mono = np.mean(audio, axis=1)
    sound = parselmouth.Sound(mono, sampling_frequency=sample_rate)
    pitch = sound.to_pitch_ac(
        time_step=HOP_SECONDS,
        pitch_floor=args.pitch_floor,
        pitch_ceiling=args.pitch_ceiling,
        very_accurate=True,
        voicing_threshold=args.voicing_threshold,
        octave_cost=0.02,
        octave_jump_cost=0.35,
        voiced_unvoiced_cost=0.14,
    )
    frame_times = pitch.xs()
    raw_f0 = pitch.selected_array["frequency"].astype(np.float64)
    strength = pitch.selected_array["strength"].astype(np.float64)
    hop_samples = max(1, int(round(sample_rate * HOP_SECONDS)))
    rms_frames = librosa.feature.rms(
        y=mono,
        frame_length=hop_samples * 4,
        hop_length=hop_samples,
        center=True,
    )[0]
    count = min(len(frame_times), len(raw_f0), len(strength), len(rms_frames))
    frame_times = frame_times[:count]
    raw_f0 = raw_f0[:count]
    strength = strength[:count]
    rms_frames = rms_frames[:count]
    level_db = 20.0 * np.log10(np.maximum(rms_frames, EPS))

    corrected_f0, corrected_midi, correction = fold_register(
        raw_f0, args.midi_min, args.midi_max
    )
    voiced = corrected_f0 > 0.0
    smoothed_midi = smooth_register(corrected_midi, voiced)
    nearest_midi = np.rint(smoothed_midi)
    cents = (smoothed_midi - nearest_midi) * 100.0
    valid = (
        voiced
        & (strength >= args.strength_threshold)
        & (level_db >= args.rms_threshold_dbfs)
        & (nearest_midi >= args.midi_min)
        & (nearest_midi <= args.midi_max)
        & (np.abs(cents) <= args.cents_threshold)
    )
    labels = bridge_same_label_gaps(np.where(valid, nearest_midi, -1).astype(np.int16))

    onset_hop = hop_samples
    onset_envelope = librosa.onset.onset_strength(
        y=mono, sr=sample_rate, hop_length=onset_hop, aggregate=np.median
    )
    onset_frames = librosa.onset.onset_detect(
        onset_envelope=onset_envelope,
        sr=sample_rate,
        hop_length=onset_hop,
        backtrack=False,
        pre_max=3,
        post_max=3,
        pre_avg=8,
        post_avg=8,
        delta=args.onset_delta,
        wait=7,
    )
    onset_times = librosa.frames_to_time(
        onset_frames, sr=sample_rate, hop_length=onset_hop
    )
    normalizer = max(float(np.percentile(onset_envelope, 95)), EPS)
    onset_strengths = np.asarray(
        [
            float(
                np.max(
                    onset_envelope[
                        max(0, int(frame) - 2) : min(len(onset_envelope), int(frame) + 8)
                    ]
                )
                / normalizer
            )
            for frame in onset_frames
        ],
        dtype=np.float64,
    )

    runs = split_rearticulations(label_runs(labels), onset_times, frame_times)
    events: list[dict[str, Any]] = []
    previous_end: float | None = None
    phrase_id = 0
    structure_points = sorted(args.structure_points)
    unused_structure_points = structure_points.copy()
    for index, run in enumerate(runs):
        relative_start = float(frame_times[run.start])
        relative_end = float(frame_times[min(run.end - 1, count - 1)] + HOP_SECONDS)
        rest_before = None if previous_end is None else max(0.0, relative_start - previous_end)
        if previous_end is None or not rest_before:
            gap_level = None
        else:
            gap = mono[
                int(round(previous_end * sample_rate)) : int(round(relative_start * sample_rate))
            ]
            gap_level = rms_dbfs(gap)
        explicit_rest = bool(
            rest_before is not None
            and rest_before >= args.explicit_rest_seconds
            and gap_level is not None
            and gap_level <= args.explicit_rest_dbfs
        )
        attack = nearest_onset(relative_start, onset_times, onset_strengths)
        event_anchor = (
            float(attack["relative_onset_seconds"])
            if attack["relative_onset_seconds"] is not None
            else relative_start
        )
        structure_distance = (
            min(abs(event_anchor - point) for point in unused_structure_points)
            if unused_structure_points
            else None
        )
        structure_reset = bool(
            structure_distance is not None
            and structure_distance <= args.structure_tolerance_seconds
        )
        if structure_reset:
            closest = min(
                unused_structure_points, key=lambda point: abs(event_anchor - point)
            )
            unused_structure_points.remove(closest)
        phrase_reset = bool(
            previous_end is None
            or structure_reset
            or (explicit_rest and rest_before is not None and rest_before >= 0.30)
        )
        if phrase_reset:
            phrase_id += 1
        indices = np.arange(run.start, run.end)
        indices = indices[valid[indices]]
        raw_values = raw_f0[indices]
        corrected_values = corrected_f0[indices]
        event_cents = cents[indices]
        corrections = sorted(set(str(value) for value in correction[indices]))
        events.append(
            {
                "event_id": index + 1,
                "phrase_id": phrase_id,
                "source_start_seconds": round(args.source_offset + relative_start, 4),
                "source_end_seconds": round(args.source_offset + relative_end, 4),
                "relative_start_seconds": round(relative_start, 4),
                "relative_end_seconds": round(relative_end, 4),
                "duration_seconds": round(relative_end - relative_start, 4),
                "quantized_midi": int(run.midi),
                "note_name": note_name(int(run.midi)),
                "pitch": {
                    "raw_f0_median_hz": round(float(np.median(raw_values)), 3),
                    "corrected_f0_median_hz": round(float(np.median(corrected_values)), 3),
                    "median_cents_from_equal_temperament": round(
                        float(np.median(event_cents)), 2
                    ),
                    "p90_abs_cents": round(float(np.percentile(np.abs(event_cents), 90)), 2),
                    "correction_reason": corrections,
                },
                "attack": attack,
                "rest_before_seconds": round(rest_before, 4) if rest_before is not None else None,
                "rest_before_analysis": {
                    "gap_rms_dbfs": round(gap_level, 2) if gap_level is not None else None,
                    "explicit_rest_hint": explicit_rest,
                },
                "phrase_reset_hint": phrase_reset,
                "structure_boundary_distance_seconds": (
                    round(float(structure_distance), 4)
                    if structure_distance is not None
                    else None
                ),
                "token": {
                    "syllable": None,
                    "assignment_status": "AUTO_CYCLE_REQUIRES_HUMAN_LISTENING",
                },
            }
        )
        previous_end = relative_end

    for index, event in enumerate(events):
        following_rest = (
            max(
                0.0,
                float(events[index + 1]["relative_start_seconds"])
                - float(event["relative_end_seconds"]),
            )
            if index + 1 < len(events)
            else max(0.0, duration - float(event["relative_end_seconds"]))
        )
        event["rest_after_seconds"] = round(following_rest, 4)
        event["line_break_candidate_hint"] = bool(following_rest >= 0.25)
        event["phrase_end_hint"] = bool(
            index + 1 == len(events)
            or (index + 1 < len(events) and events[index + 1]["phrase_reset_hint"])
        )

    abs_cents = np.abs(cents[valid])
    return {
        "schema_version": 1,
        "artifact_kind": "song-note-onset-event-draft",
        "status": "AUTO_DRAFT_REQUIRES_HUMAN_LISTENING",
        "performance_guard": {
            "input_was_pre_cropped": True,
            "maximum_input_duration_seconds": args.max_duration,
            "analysis_backend": "Praat autocorrelation; no neural F0 model",
        },
        "source": {
            "vocal_stem": str(args.vocal),
            "sha256": sha256(args.vocal),
            "sample_rate": sample_rate,
            "channels": int(audio.shape[1]),
            "source_offset_seconds": args.source_offset,
        },
        "window": {
            "start_seconds": args.source_offset,
            "end_seconds": round(args.source_offset + duration, 4),
            "duration_seconds": round(duration, 4),
        },
        "extractor": {
            "hop_seconds": HOP_SECONDS,
            "pitch_floor_hz": args.pitch_floor,
            "pitch_ceiling_hz": args.pitch_ceiling,
            "midi_range": [args.midi_min, args.midi_max],
            "register_correction": "fold by octaves only outside the declared song window register",
            "strength_threshold": args.strength_threshold,
            "rms_threshold_dbfs": args.rms_threshold_dbfs,
            "cents_threshold": args.cents_threshold,
            "minimum_event_seconds": MIN_EVENT_SECONDS,
            "structure_points_relative_seconds": structure_points,
            "structure_reset_tolerance_seconds": args.structure_tolerance_seconds,
        },
        "summary": {
            "event_count": len(events),
            "phrase_count": phrase_id,
            "stable_note_duration_seconds": round(
                sum(float(event["duration_seconds"]) for event in events), 4
            ),
            "stable_note_coverage_ratio": round(
                sum(float(event["duration_seconds"]) for event in events) / duration, 4
            ),
            "events_with_detected_attack": sum(
                bool(event["attack"]["detected"]) for event in events
            ),
            "events_with_octave_fold": sum(
                any(reason != "none" for reason in event["pitch"]["correction_reason"])
                for event in events
            ),
            "frame_pitch_median_abs_cents": (
                round(float(np.median(abs_cents)), 2) if abs_cents.size else None
            ),
            "frame_pitch_p90_abs_cents": (
                round(float(np.percentile(abs_cents, 90)), 2) if abs_cents.size else None
            ),
        },
        "events": events,
    }


def main() -> int:
    started = time.perf_counter()
    parser = argparse.ArgumentParser()
    parser.add_argument("--vocal", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-offset", type=float, default=0.0)
    parser.add_argument("--max-duration", type=float, default=75.0)
    parser.add_argument("--pitch-floor", type=float, default=90.0)
    parser.add_argument("--pitch-ceiling", type=float, default=700.0)
    parser.add_argument("--midi-min", type=int, default=57)
    parser.add_argument("--midi-max", type=int, default=76)
    parser.add_argument("--voicing-threshold", type=float, default=0.45)
    parser.add_argument("--strength-threshold", type=float, default=0.78)
    parser.add_argument("--rms-threshold-dbfs", type=float, default=-38.0)
    parser.add_argument("--cents-threshold", type=float, default=55.0)
    parser.add_argument("--onset-delta", type=float, default=0.08)
    parser.add_argument("--explicit-rest-seconds", type=float, default=0.16)
    parser.add_argument("--explicit-rest-dbfs", type=float, default=-38.0)
    parser.add_argument("--structure-points", nargs="*", type=float, default=[])
    parser.add_argument("--structure-tolerance-seconds", type=float, default=0.40)
    args = parser.parse_args()
    result = extract(args)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    max_rss_bytes = int(
        usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024
    )
    result["performance_measurement"] = {
        "wall_seconds": round(time.perf_counter() - started, 3),
        "user_cpu_seconds": round(float(usage.ru_utime), 3),
        "system_cpu_seconds": round(float(usage.ru_stime), 3),
        "max_resident_set_mb": round(max_rss_bytes / 1024.0 / 1024.0, 2),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"output": str(args.output), "summary": result["summary"]},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
