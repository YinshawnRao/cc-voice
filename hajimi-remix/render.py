#!/usr/bin/env python3
"""Render a local "哈基米南北绿豆" singing demo from an existing song.

This is deliberately separate from the repository's RVC pipeline.  RVC changes
timbre but keeps the guide vocal's words; this renderer instead loops a fixed
phrase while borrowing pitch, pauses and loudness from an already-separated
guide vocal.  Praat/PSOLA is the clean default; WORLD remains an A/B fallback.

Runtime dependencies live in frameworks/rvc-webui-macos/.venv:
numpy, scipy, librosa, soundfile, torch, torchcrepe, parselmouth and pyworld.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import librosa
import numpy as np
import parselmouth
import pyworld
import soundfile as sf
import torch
import torchcrepe
from parselmouth.praat import call as praat_call
from scipy import signal
from scipy.ndimage import gaussian_filter1d, maximum_filter1d


MODULE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = MODULE_ROOT.parent
EPS = 1e-10


def resolve_project_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def db(value: float) -> float:
    return 20.0 * math.log10(max(abs(value), EPS))


def rms(audio: np.ndarray, mask: np.ndarray | None = None) -> float:
    values = audio[mask] if mask is not None else audio
    return float(np.sqrt(np.mean(np.square(values)))) if values.size else 0.0


def load_audio(path: Path, *, mono: bool = False) -> tuple[np.ndarray, int]:
    data, sample_rate = sf.read(path, dtype="float64", always_2d=True)
    if mono:
        return np.mean(data, axis=1), sample_rate
    return data, sample_rate


def resample_channels(audio: np.ndarray, old_sr: int, new_sr: int) -> np.ndarray:
    if old_sr == new_sr:
        return audio
    if audio.ndim == 1:
        return librosa.resample(audio, orig_sr=old_sr, target_sr=new_sr)
    channels = [
        librosa.resample(audio[:, channel], orig_sr=old_sr, target_sr=new_sr)
        for channel in range(audio.shape[1])
    ]
    return np.stack(channels, axis=1)


def true_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    padded = np.pad(mask.astype(np.int8), (1, 1))
    edges = np.diff(padded)
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    return list(zip(starts.tolist(), ends.tolist()))


def clean_activity_mask(
    mask: np.ndarray,
    *,
    hop_seconds: float,
    fill_gap_seconds: float = 0.18,
    min_active_seconds: float = 0.10,
) -> np.ndarray:
    cleaned = mask.astype(bool).copy()
    gap_limit = max(1, int(round(fill_gap_seconds / hop_seconds)))
    active_limit = max(1, int(round(min_active_seconds / hop_seconds)))

    for start, end in true_runs(~cleaned):
        if start > 0 and end < len(cleaned) and end - start <= gap_limit:
            cleaned[start:end] = True
    for start, end in true_runs(cleaned):
        if end - start < active_limit:
            cleaned[start:end] = False
    return cleaned


def extract_guide_features(
    guide: np.ndarray,
    sample_rate: int,
    *,
    f0_floor: float,
    f0_ceil: float,
    rms_threshold_db: float,
    periodicity_threshold: float,
    hop_seconds: float = 0.01,
) -> dict[str, np.ndarray | float]:
    analysis_sr = 16000
    hop_length = int(round(analysis_sr * hop_seconds))
    resampled = librosa.resample(guide, orig_sr=sample_rate, target_sr=analysis_sr)
    tensor = torch.from_numpy(np.ascontiguousarray(resampled, dtype=np.float32))[None]

    with torch.inference_mode():
        pitch, periodicity = torchcrepe.predict(
            tensor,
            analysis_sr,
            hop_length,
            fmin=f0_floor,
            fmax=f0_ceil,
            model="tiny",
            decoder=torchcrepe.decode.weighted_argmax,
            return_periodicity=True,
            batch_size=2048,
            device="cpu",
            pad=True,
        )
    pitch_np = pitch.squeeze(0).cpu().numpy().astype(np.float64)
    periodicity_np = periodicity.squeeze(0).cpu().numpy().astype(np.float64)
    periodicity_np = signal.medfilt(periodicity_np, kernel_size=3)

    rms_frames = librosa.feature.rms(
        y=guide,
        frame_length=max(256, int(round(sample_rate * 0.04))),
        hop_length=max(1, int(round(sample_rate * hop_seconds))),
        center=True,
    )[0]
    feature_count = min(len(pitch_np), len(periodicity_np), len(rms_frames))
    pitch_np = pitch_np[:feature_count]
    periodicity_np = periodicity_np[:feature_count]
    rms_frames = rms_frames[:feature_count]
    times = np.arange(feature_count, dtype=np.float64) * hop_seconds
    rms_db = 20.0 * np.log10(np.maximum(rms_frames, EPS))

    valid = (
        np.isfinite(pitch_np)
        & (pitch_np >= f0_floor)
        & (pitch_np <= f0_ceil)
        & (periodicity_np >= periodicity_threshold)
        & (rms_db >= rms_threshold_db)
    )
    active = clean_activity_mask(
        (rms_db >= rms_threshold_db) & (periodicity_np >= periodicity_threshold),
        hop_seconds=hop_seconds,
    )

    filled_pitch = np.zeros(feature_count, dtype=np.float64)
    global_fallback = float(np.median(pitch_np[valid])) if np.any(valid) else 220.0
    for start, end in true_runs(active):
        run_valid = valid[start:end]
        positions = np.flatnonzero(run_valid)
        if len(positions) >= 2:
            filled = np.interp(
                np.arange(end - start), positions, pitch_np[start:end][positions]
            )
        elif len(positions) == 1:
            filled = np.full(end - start, pitch_np[start:end][positions[0]])
        else:
            filled = np.full(end - start, global_fallback)
        if len(filled) >= 5:
            log_pitch = np.log2(np.maximum(filled, f0_floor))
            log_pitch = signal.medfilt(log_pitch, kernel_size=5)
            filled = np.power(2.0, log_pitch)
        filled_pitch[start:end] = np.clip(filled, f0_floor, f0_ceil)

    return {
        "times": times,
        "pitch": filled_pitch,
        "periodicity": periodicity_np,
        "rms": rms_frames.astype(np.float64),
        "active": active,
        "hop_seconds": hop_seconds,
    }


def analyse_cycle(
    cycle: np.ndarray,
    sample_rate: int,
    *,
    f0_floor: float,
    f0_ceil: float,
    frame_period_ms: float,
    energy_cap_percentile: float,
) -> dict[str, np.ndarray | float]:
    cycle = np.ascontiguousarray(cycle, dtype=np.float64)
    cycle -= float(np.mean(cycle))
    f0, times = pyworld.harvest(
        cycle,
        sample_rate,
        f0_floor=f0_floor,
        f0_ceil=f0_ceil,
        frame_period=frame_period_ms,
    )
    f0 = pyworld.stonemask(cycle, f0, times, sample_rate)
    spectral = pyworld.cheaptrick(
        cycle, f0, times, sample_rate, f0_floor=f0_floor
    )
    aperiodicity = pyworld.d4c(
        cycle,
        f0,
        times,
        sample_rate,
        fft_size=(spectral.shape[1] - 1) * 2,
    )
    voiced_ratio = float(np.mean(f0 > 0))
    if voiced_ratio < 0.25:
        raise RuntimeError(
            f"Voice cycle has too little detected pitch ({voiced_ratio:.1%}); "
            "check the source clip or F0 range."
        )

    # CheapTrick preserves the source phrase's frame-to-frame energy.  A few
    # unusually hot frames become repeated impulses when the phrase is cycled,
    # even though guide loudness is applied later.  Only attenuate the upper
    # tail; never boost quiet consonants or silence.
    frame_energy_db = 10.0 * np.log10(
        np.maximum(np.mean(spectral, axis=1), EPS)
    )
    reference_frames = (f0 > 0) & np.isfinite(frame_energy_db)
    energy_cap_db = float(
        np.percentile(frame_energy_db[reference_frames], energy_cap_percentile)
    )
    attenuation_db = np.maximum(frame_energy_db - energy_cap_db, 0.0)
    spectral *= np.power(10.0, -attenuation_db[:, None] / 10.0)
    return {
        "f0": f0,
        "sp": spectral,
        "ap": aperiodicity,
        "energy_cap_percentile": energy_cap_percentile,
        "energy_cap_db": energy_cap_db,
        "max_frame_attenuation_db": float(np.max(attenuation_db)),
    }


def render_world_cycle(
    source: dict[str, np.ndarray],
    guide_features: dict[str, np.ndarray | float],
    *,
    output_samples: int,
    sample_rate: int,
    frame_period_ms: float,
    reset_silence_seconds: float,
) -> tuple[np.ndarray, dict[str, np.ndarray | int]]:
    frame_seconds = frame_period_ms / 1000.0
    frame_count = int(math.ceil((output_samples / sample_rate) / frame_seconds)) + 1
    world_times = np.arange(frame_count, dtype=np.float64) * frame_seconds
    feature_times = np.asarray(guide_features["times"])
    pitch = np.interp(
        world_times, feature_times, np.asarray(guide_features["pitch"]), left=0, right=0
    )
    active = np.interp(
        world_times,
        feature_times,
        np.asarray(guide_features["active"], dtype=np.float64),
        left=0,
        right=0,
    ) >= 0.5
    expected_voiced = np.zeros(frame_count, dtype=bool)

    output = np.zeros(output_samples, dtype=np.float64)
    source_frames = len(source["f0"])
    cursor = 0
    previous_end = 0
    rendered_runs = 0
    reset_count = 0
    source_cycles = 0
    fade_samples = max(1, int(round(sample_rate * 0.010)))

    for start, end in true_runs(active):
        gap_seconds = (start - previous_end) * frame_seconds
        if gap_seconds >= reset_silence_seconds:
            cursor = 0
            reset_count += 1
        run_frames = end - start
        indices = (cursor + np.arange(run_frames)) % source_frames
        wrapped = (cursor + run_frames) // source_frames
        source_cycles += int(wrapped)

        source_f0 = source["f0"][indices]
        expected_voiced[start:end] = source_f0 > 0
        target_f0 = pitch[start:end]
        positive = target_f0[target_f0 > 0]
        fallback = float(np.median(positive)) if positive.size else 220.0
        target_f0 = np.where(target_f0 > 0, target_f0, fallback)
        rendered_f0 = np.where(source_f0 > 0, target_f0, 0.0)
        chunk = pyworld.synthesize(
            np.ascontiguousarray(rendered_f0, dtype=np.float64),
            np.ascontiguousarray(source["sp"][indices], dtype=np.float64),
            np.ascontiguousarray(source["ap"][indices], dtype=np.float64),
            sample_rate,
            frame_period=frame_period_ms,
        )

        sample_start = int(round(start * frame_seconds * sample_rate))
        sample_end = min(output_samples, int(round(end * frame_seconds * sample_rate)))
        wanted = max(0, sample_end - sample_start)
        if wanted == 0:
            continue
        if len(chunk) < wanted:
            chunk = np.pad(chunk, (0, wanted - len(chunk)))
        chunk = chunk[:wanted]
        local_fade = min(fade_samples, wanted // 2)
        if local_fade:
            ramp = np.sin(np.linspace(0.0, np.pi / 2.0, local_fade)) ** 2
            chunk[:local_fade] *= ramp
            chunk[-local_fade:] *= ramp[::-1]
        output[sample_start:sample_end] += chunk

        cursor = int((cursor + run_frames) % source_frames)
        previous_end = end
        rendered_runs += 1

    return output, {
        "world_times": world_times,
        "target_f0": pitch,
        "active_frames": active,
        "expected_voiced_frames": expected_voiced,
        "rendered_runs": rendered_runs,
        "reset_count": reset_count,
        "completed_source_cycles": source_cycles,
    }


def apply_guide_loudness(
    synthetic: np.ndarray,
    guide: np.ndarray,
    guide_features: dict[str, np.ndarray | float],
    *,
    sample_rate: int,
    vocal_gain_db: float,
    lowpass_hz: float,
) -> tuple[np.ndarray, dict[str, float], np.ndarray]:
    sample_times = np.arange(len(synthetic), dtype=np.float64) / sample_rate
    feature_times = np.asarray(guide_features["times"])
    envelope = np.interp(
        sample_times, feature_times, np.asarray(guide_features["rms"]), left=0, right=0
    )
    active_samples = np.interp(
        sample_times,
        feature_times,
        np.asarray(guide_features["active"], dtype=np.float64),
        left=0,
        right=0,
    ) >= 0.5
    active_envelope = envelope[active_samples & (envelope > 0)]
    median_envelope = float(np.median(active_envelope)) if active_envelope.size else 1.0
    relative = np.clip(envelope / max(median_envelope, EPS), 0.25, 3.0)
    shaped = synthetic * relative

    if len(shaped) > sample_rate // 2:
        highpass = signal.butter(2, 70.0, btype="highpass", fs=sample_rate, output="sos")
        shaped = signal.sosfiltfilt(highpass, shaped)
        if 0 < lowpass_hz < sample_rate / 2:
            lowpass = signal.butter(
                4, lowpass_hz, btype="lowpass", fs=sample_rate, output="sos"
            )
            shaped = signal.sosfiltfilt(lowpass, shaped)

    guide_rms = rms(guide, active_samples)
    synthetic_rms = rms(shaped, active_samples)
    desired = guide_rms * (10.0 ** (vocal_gain_db / 20.0))
    scale = desired / max(synthetic_rms, EPS)
    scale = float(np.clip(scale, 0.02, 30.0))
    shaped *= scale
    return shaped, {
        "guide_active_rms_dbfs": db(guide_rms),
        "synthetic_active_rms_before_dbfs": db(synthetic_rms),
        "synthetic_gain_db": db(scale),
        "synthetic_active_rms_after_gain_dbfs": db(rms(shaped, active_samples)),
    }, active_samples


def apply_lookahead_peak_limiter(
    audio: np.ndarray,
    *,
    sample_rate: int,
    ceiling_dbfs: float,
    lookahead_ms: float,
    smoothing_ms: float,
) -> tuple[np.ndarray, dict[str, float]]:
    """Limit peaks with a smooth gain envelope instead of waveshaping.

    The previous MVP used tanh directly on samples.  That guaranteed headroom
    but changed the waveform shape and added audible high-frequency harmonics.
    This limiter anticipates local peaks, lowers a short region around them and
    otherwise leaves the waveform untouched.
    """

    ceiling = 10.0 ** (ceiling_dbfs / 20.0)
    window = max(3, int(round(sample_rate * lookahead_ms / 1000.0)) * 2 + 1)
    local_peak = maximum_filter1d(np.abs(audio), size=window, mode="nearest")
    target_gain = np.minimum(1.0, ceiling / np.maximum(local_peak, EPS))
    sigma = max(1.0, sample_rate * smoothing_ms / 1000.0)
    smooth_gain = gaussian_filter1d(target_gain, sigma=sigma, mode="nearest")
    gain = np.minimum(target_gain, smooth_gain)
    limited = audio * gain

    peak = float(np.max(np.abs(limited)))
    safety_gain = min(1.0, ceiling / max(peak, EPS))
    limited *= safety_gain
    return limited, {
        "vocal_peak_before_limiter_dbfs": db(float(np.max(np.abs(audio)))),
        "vocal_peak_ceiling_dbfs": ceiling_dbfs,
        "vocal_peak_after_limiter_dbfs": db(float(np.max(np.abs(limited)))),
        "limiter_min_gain_db": db(float(np.min(gain)) * safety_gain),
        "limiter_affected_sample_ratio": float(np.mean(gain < 0.999)),
        "limiter_lookahead_ms": lookahead_ms,
        "limiter_smoothing_ms": smoothing_ms,
    }


def compress_source_envelope(
    audio: np.ndarray,
    sample_rate: int,
    *,
    threshold_dbfs: float = -18.0,
    ratio: float = 3.0,
    window_ms: float = 20.0,
) -> tuple[np.ndarray, float]:
    """Tame hot phrase frames with an RMS gain envelope, not waveshaping."""

    half_window = max(1, int(round(sample_rate * window_ms / 1000.0)))
    window = signal.windows.hann(half_window * 2 + 1)
    window /= np.sum(window)
    envelope = np.sqrt(signal.fftconvolve(audio * audio, window, mode="same") + EPS)
    envelope_db = 20.0 * np.log10(np.maximum(envelope, EPS))
    reduction_db = np.minimum(
        0.0,
        (threshold_dbfs - envelope_db) * (1.0 - 1.0 / ratio),
    )
    compressed = audio * np.power(10.0, reduction_db / 20.0)
    return compressed, float(np.min(reduction_db))


def stretch_syllable_unit(
    unit: np.ndarray,
    target_samples: int,
    *,
    sample_rate: int,
    f0_floor: float,
    f0_ceil: float,
) -> np.ndarray:
    """Time-warp one syllable while keeping its consonant attack compact."""

    unit = np.ascontiguousarray(unit, dtype=np.float64)
    target_samples = max(1, int(target_samples))
    if len(unit) < 16 or target_samples < 16:
        return signal.resample(unit, target_samples).astype(np.float64)

    source_seconds = len(unit) / sample_rate
    target_seconds = target_samples / sample_rate
    attack_seconds = min(0.055, source_seconds * 0.30)
    release_seconds = min(0.035, source_seconds * 0.18)
    sustain_seconds = max(
        source_seconds - attack_seconds - release_seconds,
        1.0 / sample_rate,
    )
    desired_sustain = target_seconds - attack_seconds - release_seconds

    manipulation = praat_call(
        parselmouth.Sound(unit, sampling_frequency=sample_rate),
        "To Manipulation",
        0.005,
        f0_floor,
        f0_ceil,
    )
    duration_tier = praat_call(manipulation, "Extract duration tier")
    if desired_sustain > 0.025:
        sustain_factor = float(np.clip(desired_sustain / sustain_seconds, 0.15, 20.0))
        transition = min(0.003, sustain_seconds * 0.15)
        release_start = source_seconds - release_seconds
        points = (
            (0.0, 1.0),
            (max(0.0, attack_seconds - transition), 1.0),
            (min(source_seconds, attack_seconds + transition), sustain_factor),
            (max(0.0, release_start - transition), sustain_factor),
            (min(source_seconds, release_start + transition), 1.0),
            (source_seconds, 1.0),
        )
    else:
        constant_factor = float(np.clip(target_seconds / source_seconds, 0.15, 20.0))
        points = ((0.0, constant_factor), (source_seconds, constant_factor))

    last_time = -1.0
    for point_time, factor in points:
        if point_time <= last_time + 1e-6:
            continue
        praat_call(duration_tier, "Add point", float(point_time), float(factor))
        last_time = point_time
    praat_call([duration_tier, manipulation], "Replace duration tier")
    stretched = praat_call(manipulation, "Get resynthesis (overlap-add)").values[0]
    if len(stretched) != target_samples:
        stretched = signal.resample(stretched, target_samples)
    return np.asarray(stretched, dtype=np.float64)


def build_performance_cycle(
    cycle: np.ndarray,
    *,
    sample_rate: int,
    source_boundaries_seconds: list[float],
    target_boundaries_seconds: list[float],
    unit_gains_db: list[float],
    cycle_offset_seconds: float,
    f0_floor: float,
    f0_ceil: float,
) -> tuple[np.ndarray, dict[str, float | int]]:
    """Warp Mandarin units onto the timing and accents of the accepted V2."""

    unit_count = len(source_boundaries_seconds) - 1
    if unit_count <= 0 or len(target_boundaries_seconds) != unit_count + 1:
        raise ValueError("Source and performance boundaries must describe equal units")
    if len(unit_gains_db) != unit_count:
        raise ValueError("--performance-unit-gains-db must match the unit count")
    for label, boundaries in (
        ("voice", source_boundaries_seconds),
        ("performance", target_boundaries_seconds),
    ):
        if boundaries[0] < 0 or any(
            right <= left for left, right in zip(boundaries, boundaries[1:])
        ):
            raise ValueError(f"{label} boundaries must be non-negative and increasing")
    source_seconds = len(cycle) / sample_rate
    if source_boundaries_seconds[-1] > source_seconds + 1e-3:
        raise ValueError("Voice-unit boundaries extend past the voice cycle")
    target_seconds = target_boundaries_seconds[-1]
    if not 0 <= cycle_offset_seconds < target_seconds:
        raise ValueError("--performance-cycle-offset must lie within the cycle")

    manipulation = praat_call(
        parselmouth.Sound(np.ascontiguousarray(cycle), sampling_frequency=sample_rate),
        "To Manipulation",
        0.005,
        f0_floor,
        f0_ceil,
    )
    duration_tier = praat_call(manipulation, "Extract duration tier")
    source_boundaries = np.asarray(source_boundaries_seconds, dtype=np.float64)
    target_boundaries = np.asarray(target_boundaries_seconds, dtype=np.float64)
    duration_factors = np.diff(target_boundaries) / np.diff(source_boundaries)
    transition = 0.002
    last_time = -1.0
    for index, factor in enumerate(duration_factors):
        start = source_boundaries[index]
        end = source_boundaries[index + 1]
        points = (
            start if index == 0 else start + transition,
            end if index == unit_count - 1 else end - transition,
        )
        for point_time in points:
            if point_time <= last_time + 1e-6:
                continue
            praat_call(duration_tier, "Add point", float(point_time), float(factor))
            last_time = point_time
    praat_call([duration_tier, manipulation], "Replace duration tier")
    shaped = praat_call(manipulation, "Get resynthesis (overlap-add)").values[0]
    target_samples = int(round(target_seconds * sample_rate))
    if len(shaped) != target_samples:
        shaped = signal.resample(shaped, target_samples)

    gain = np.ones(target_samples, dtype=np.float64)
    for index, gain_db in enumerate(unit_gains_db):
        start = int(round(target_boundaries[index] * sample_rate))
        end = int(round(target_boundaries[index + 1] * sample_rate))
        gain[start:end] = 10.0 ** (gain_db / 20.0)
    gain = gaussian_filter1d(gain, max(1, int(round(sample_rate * 0.012))))
    shaped *= gain
    shaped *= rms(cycle) / max(rms(shaped), EPS)

    offset_samples = int(round(cycle_offset_seconds * sample_rate))
    shaped = np.roll(shaped, offset_samples)
    junction_fade = min(
        max(1, int(round(sample_rate * 0.006))),
        offset_samples,
        len(shaped) - offset_samples,
    )
    if junction_fade:
        shaped[offset_samples - junction_fade : offset_samples] *= np.linspace(
            1.0, 0.0, junction_fade
        )
        shaped[offset_samples : offset_samples + junction_fade] *= np.linspace(
            0.0, 1.0, junction_fade
        )

    durations = np.diff(target_boundaries)
    gains = np.asarray(unit_gains_db, dtype=np.float64)
    return np.asarray(shaped, dtype=np.float64), {
        "voice_unit_count": unit_count,
        "performance_cycle_seconds": target_seconds,
        "performance_cycle_offset_seconds": cycle_offset_seconds,
        "performance_duration_cv": float(np.std(durations) / np.mean(durations)),
        "performance_long_unit_ratio": float(np.mean(durations > 0.5)),
        "performance_unit_gain_std_db": float(np.std(gains)),
        "completed_source_cycles": 0,
        "rendered_syllable_events": 0,
    }


def build_onset_aligned_source(
    cycle: np.ndarray,
    guide: np.ndarray,
    active_runs: list[tuple[int, int]],
    *,
    sample_rate: int,
    unit_boundaries_seconds: list[float],
    onset_min_spacing_seconds: float,
    onset_delta: float,
    f0_floor: float,
    f0_ceil: float,
) -> tuple[np.ndarray, dict[str, float | int]]:
    """Assign one Mandarin syllable to each usable guide-vocal onset."""

    if len(unit_boundaries_seconds) < 2:
        raise ValueError("At least two --voice-unit-boundaries values are required")
    if any(
        right <= left
        for left, right in zip(unit_boundaries_seconds, unit_boundaries_seconds[1:])
    ):
        raise ValueError("--voice-unit-boundaries must be strictly increasing")
    if unit_boundaries_seconds[0] < 0:
        raise ValueError("--voice-unit-boundaries cannot start below zero")
    if unit_boundaries_seconds[-1] > len(cycle) / sample_rate + 1e-3:
        raise ValueError("--voice-unit-boundaries extend past the voice cycle")

    boundary_samples = [
        int(round(seconds * sample_rate)) for seconds in unit_boundaries_seconds
    ]
    units = [
        np.ascontiguousarray(cycle[start:end], dtype=np.float64)
        for start, end in zip(boundary_samples, boundary_samples[1:])
    ]
    if any(len(unit) < int(round(sample_rate * 0.04)) for unit in units):
        raise ValueError("Every voice unit must be at least 40 ms")

    hop_length = max(1, int(round(sample_rate * 0.01)))
    onset_envelope = librosa.onset.onset_strength(
        y=guide,
        sr=sample_rate,
        hop_length=hop_length,
        aggregate=np.median,
    )
    onset_frames = librosa.onset.onset_detect(
        onset_envelope=onset_envelope,
        sr=sample_rate,
        hop_length=hop_length,
        backtrack=True,
        pre_max=3,
        post_max=3,
        pre_avg=8,
        post_avg=8,
        delta=onset_delta,
        wait=6,
    )
    onset_samples = librosa.frames_to_samples(
        onset_frames,
        hop_length=hop_length,
    )
    minimum_spacing = int(round(onset_min_spacing_seconds * sample_rate))
    minimum_event = int(round(0.08 * sample_rate))
    source = np.zeros(len(guide), dtype=np.float64)
    event_durations: list[float] = []
    completed_cycles = 0
    cache: dict[tuple[int, int], np.ndarray] = {}
    event_count = 0

    for run_start, run_end in active_runs:
        event_starts = [run_start]
        for onset_sample in onset_samples:
            onset_sample = int(onset_sample)
            if not run_start < onset_sample < run_end:
                continue
            if onset_sample - event_starts[-1] >= minimum_spacing:
                event_starts.append(onset_sample)
        if run_end - event_starts[-1] < minimum_event and len(event_starts) > 1:
            event_starts.pop()
        event_boundaries = event_starts + [run_end]
        run_event_count = len(event_boundaries) - 1
        completed_cycles += run_event_count // len(units)

        for local_index, (event_start, event_end) in enumerate(
            zip(event_boundaries, event_boundaries[1:])
        ):
            target_samples = event_end - event_start
            if target_samples <= 0:
                continue
            unit_index = local_index % len(units)
            cache_key = (unit_index, target_samples)
            rendered = cache.get(cache_key)
            if rendered is None:
                rendered = stretch_syllable_unit(
                    units[unit_index],
                    target_samples,
                    sample_rate=sample_rate,
                    f0_floor=f0_floor,
                    f0_ceil=f0_ceil,
                )
                cache[cache_key] = rendered
            rendered = rendered.copy()
            fade_samples = min(
                max(1, int(round(sample_rate * 0.003))),
                len(rendered) // 2,
            )
            if fade_samples:
                fade = np.linspace(0.0, 1.0, fade_samples)
                rendered[:fade_samples] *= fade
                rendered[-fade_samples:] *= fade[::-1]
            source[event_start:event_end] = rendered
            event_durations.append(target_samples / sample_rate)
            event_count += 1

    durations = np.asarray(event_durations, dtype=np.float64)
    return source, {
        "rendered_syllable_events": event_count,
        "voice_unit_count": len(units),
        "completed_source_cycles": completed_cycles,
        "event_duration_p10_seconds": float(np.percentile(durations, 10)),
        "event_duration_median_seconds": float(np.median(durations)),
        "event_duration_p90_seconds": float(np.percentile(durations, 90)),
        "event_duration_max_seconds": float(np.max(durations)),
        "onset_min_spacing_seconds": onset_min_spacing_seconds,
        "onset_delta": onset_delta,
    }


def apply_release_peak_limiter(
    audio: np.ndarray,
    *,
    sample_rate: int,
    ceiling_dbfs: float,
    lookahead_ms: float,
    release_ms: float,
) -> tuple[np.ndarray, dict[str, float]]:
    """Offline look-ahead limiter using only a multiplicative gain envelope."""

    radius = max(1, int(round(sample_rate * lookahead_ms / 1000.0)))
    peak = maximum_filter1d(np.abs(audio), size=radius * 2 + 1, mode="nearest")
    ceiling = 10.0 ** (ceiling_dbfs / 20.0)
    requested = np.minimum(1.0, ceiling / np.maximum(peak, EPS))
    gain = np.empty_like(requested)
    gain[0] = requested[0]
    release = math.exp(-1.0 / (release_ms * sample_rate / 1000.0))
    for index in range(1, len(requested)):
        recovered = release * gain[index - 1] + (1.0 - release)
        gain[index] = min(requested[index], recovered)
    limited = audio * gain
    return limited, {
        "vocal_peak_before_limiter_dbfs": db(float(np.max(np.abs(audio)))),
        "vocal_peak_ceiling_dbfs": ceiling_dbfs,
        "vocal_peak_after_limiter_dbfs": db(float(np.max(np.abs(limited)))),
        "limiter_min_gain_db": db(float(np.min(gain))),
        "limiter_affected_sample_ratio": float(np.mean(gain < 0.999)),
        "limiter_gt_1db_reduction_ratio": float(
            np.mean(gain < 10.0 ** (-1.0 / 20.0))
        ),
        "limiter_lookahead_ms": lookahead_ms,
        "limiter_release_ms": release_ms,
    }


def render_psola_cycle(
    cycle: np.ndarray,
    guide: np.ndarray,
    guide_features: dict[str, np.ndarray | float],
    *,
    output_samples: int,
    sample_rate: int,
    f0_floor: float,
    f0_ceil: float,
    frame_period_ms: float,
    pitch_octaves: int,
    vocal_gain_db: float,
    vocal_peak_dbfs: float,
    limiter_lookahead_ms: float,
    limiter_release_ms: float,
    alignment_mode: str,
    unit_boundaries_seconds: list[float],
    performance_boundaries_seconds: list[float],
    performance_unit_gains_db: list[float],
    performance_cycle_offset_seconds: float,
    onset_min_spacing_seconds: float,
    onset_delta: float,
) -> tuple[
    np.ndarray,
    dict[str, np.ndarray | int],
    dict[str, float],
    np.ndarray,
    dict[str, float | int | str],
]:
    """Build the selected phrase performance and repitch it with PSOLA."""

    feature_times = np.asarray(guide_features["times"])
    sample_times = np.arange(output_samples, dtype=np.float64) / sample_rate
    activity = np.interp(
        sample_times,
        feature_times,
        np.asarray(guide_features["active"], dtype=np.float64),
        left=0.0,
        right=0.0,
    )
    active_samples = activity >= 0.5
    active_runs = true_runs(active_samples)
    if not active_runs:
        raise RuntimeError("No active singing runs are available for PSOLA")

    if alignment_mode == "performance-template":
        performance_cycle, alignment_meta = build_performance_cycle(
            cycle,
            sample_rate=sample_rate,
            source_boundaries_seconds=unit_boundaries_seconds,
            target_boundaries_seconds=performance_boundaries_seconds,
            unit_gains_db=performance_unit_gains_db,
            cycle_offset_seconds=performance_cycle_offset_seconds,
            f0_floor=f0_floor,
            f0_ceil=f0_ceil,
        )
        compressed_cycle, compressor_reduction = compress_source_envelope(
            performance_cycle, sample_rate
        )
        source = np.resize(compressed_cycle, output_samples).astype(np.float64)
        alignment_meta["completed_source_cycles"] = int(
            output_samples // len(compressed_cycle)
        )
        gate_preroll_ms = 0.0
        gate_release_ms = 0.0
        gate_fade_ms = 24.0
        gate_radius = max(1, int(round(sample_rate * gate_fade_ms / 1000.0)))
        gate_activity = activity
        phrase_restart_each_active_run = 0
        reset_count = 0
    else:
        compressed_cycle, compressor_reduction = compress_source_envelope(
            cycle, sample_rate
        )
        # Open the gate before each restarted source run so the initial /x/ in
        # "哈" is not lost.  The accepted performance-template path above uses
        # the original Clean V2 continuous-tile gate instead.
        gate_preroll_ms = 40.0
        gate_release_ms = 60.0
        gate_fade_ms = 12.0
        gate_preroll = int(round(sample_rate * gate_preroll_ms / 1000.0))
        gate_release = int(round(sample_rate * gate_release_ms / 1000.0))
        gate_radius = max(1, int(round(sample_rate * gate_fade_ms / 1000.0)))
        gate_activity = np.zeros(output_samples, dtype=np.float64)
        for run_start, run_end in active_runs:
            gate_start = max(0, run_start - gate_preroll)
            gate_end = min(output_samples, run_end + gate_release)
            gate_activity[gate_start:gate_end] = 1.0
        phrase_restart_each_active_run = 1
        reset_count = max(0, len(active_runs) - 1)

    if alignment_mode == "guide-onsets":
        source, alignment_meta = build_onset_aligned_source(
            compressed_cycle,
            guide,
            active_runs,
            sample_rate=sample_rate,
            unit_boundaries_seconds=unit_boundaries_seconds,
            onset_min_spacing_seconds=onset_min_spacing_seconds,
            onset_delta=onset_delta,
            f0_floor=f0_floor,
            f0_ceil=f0_ceil,
        )
    elif alignment_mode == "phrase-cycle":
        source = np.zeros(output_samples, dtype=np.float64)
        rendered_source_samples = 0
        restart_fade = max(1, int(round(sample_rate * 0.003)))
        for run_start, run_end in active_runs:
            gate_end = min(output_samples, run_end + gate_release)
            source_end = min(output_samples, gate_end + gate_radius)
            segment_samples = source_end - run_start
            if segment_samples <= 0:
                continue
            segment = np.resize(compressed_cycle, segment_samples).astype(np.float64)
            fade_samples = min(restart_fade, segment_samples)
            segment[:fade_samples] *= np.linspace(0.0, 1.0, fade_samples)
            source[run_start:source_end] = segment
            rendered_source_samples += segment_samples
        alignment_meta = {
            "rendered_syllable_events": 0,
            "voice_unit_count": max(0, len(unit_boundaries_seconds) - 1),
            "completed_source_cycles": int(rendered_source_samples // len(cycle)),
        }
    elif alignment_mode != "performance-template":
        raise ValueError(f"Unsupported alignment mode: {alignment_mode}")

    gate_window = signal.windows.hann(gate_radius * 2 + 1)
    gate_window /= np.sum(gate_window)
    gate = np.clip(
        signal.fftconvolve(gate_activity, gate_window, mode="same"), 0.0, 1.0
    )
    envelope = np.interp(
        sample_times,
        feature_times,
        np.asarray(guide_features["rms"]),
        left=0.0,
        right=0.0,
    )
    active_envelope = envelope[active_samples]
    median_envelope = float(np.median(active_envelope)) if active_envelope.size else 1.0
    relative_envelope = np.clip(envelope / max(median_envelope, EPS), 0.35, 2.0)

    duration = output_samples / sample_rate
    manipulation = praat_call(
        parselmouth.Sound(source, sampling_frequency=sample_rate),
        "To Manipulation",
        frame_period_ms / 1000.0,
        f0_floor,
        f0_ceil,
    )
    pitch_tier = praat_call(manipulation, "Extract pitch tier")
    praat_call(pitch_tier, "Remove points between", 0.0, duration)
    frame_seconds = frame_period_ms / 1000.0
    tier_times = np.arange(frame_seconds / 2.0, duration, frame_seconds)
    target_pitch = np.interp(
        tier_times,
        feature_times,
        np.asarray(guide_features["pitch"]),
        left=0.0,
        right=0.0,
    )
    voiced = np.flatnonzero(target_pitch > 0.0)
    if not voiced.size:
        raise RuntimeError("No voiced guide pitch is available for PSOLA")
    target_pitch = np.interp(np.arange(len(target_pitch)), voiced, target_pitch[voiced])
    target_pitch = np.clip(
        target_pitch * (2.0**pitch_octaves), f0_floor, f0_ceil - 10.0
    )
    for point_time, frequency in zip(tier_times, target_pitch):
        praat_call(pitch_tier, "Add point", float(point_time), float(frequency))
    praat_call([pitch_tier, manipulation], "Replace pitch tier")
    vocal = praat_call(manipulation, "Get resynthesis (overlap-add)").values[0]
    vocal = np.pad(vocal, (0, max(0, output_samples - len(vocal))))[:output_samples]
    vocal *= gate * relative_envelope
    if len(vocal) > sample_rate // 2:
        highpass = signal.butter(
            2, 70.0, btype="highpass", fs=sample_rate, output="sos"
        )
        vocal = signal.sosfiltfilt(highpass, vocal)

    guide_rms = rms(guide, active_samples)
    vocal_rms = rms(vocal, active_samples)
    desired_rms = guide_rms * 10.0 ** (vocal_gain_db / 20.0)
    scale = float(np.clip(desired_rms / max(vocal_rms, EPS), 0.02, 30.0))
    vocal *= scale
    vocal, limiter_meta = apply_release_peak_limiter(
        vocal,
        sample_rate=sample_rate,
        ceiling_dbfs=vocal_peak_dbfs,
        lookahead_ms=limiter_lookahead_ms,
        release_ms=limiter_release_ms,
    )

    qa_times = np.arange(
        int(math.ceil(duration / frame_seconds)) + 1, dtype=np.float64
    ) * frame_seconds
    qa_target = np.interp(
        qa_times,
        tier_times,
        target_pitch,
        left=target_pitch[0],
        right=target_pitch[-1],
    )
    qa_active = np.interp(
        qa_times,
        feature_times,
        np.asarray(guide_features["active"], dtype=np.float64),
        left=0.0,
        right=0.0,
    ) >= 0.5
    render_meta: dict[str, np.ndarray | int] = {
        "target_f0": qa_target,
        "expected_voiced_frames": qa_active,
        "rendered_runs": len(active_runs),
        "reset_count": reset_count,
        "completed_source_cycles": int(alignment_meta["completed_source_cycles"]),
    }
    loudness_meta = {
        "guide_active_rms_dbfs": db(guide_rms),
        "synthetic_active_rms_before_dbfs": db(vocal_rms),
        "synthetic_gain_db": db(scale),
        "synthetic_active_rms_after_gain_dbfs": db(desired_rms),
        **limiter_meta,
        "synthetic_active_rms_post_limiter_dbfs": db(rms(vocal, active_samples)),
    }
    engine_meta: dict[str, float | int | str] = {
        "alignment_mode": alignment_mode,
        "pitch_octaves": pitch_octaves,
        "source_compressor_max_reduction_db": compressor_reduction,
        "source_compressor_threshold_dbfs": -18.0,
        "source_compressor_ratio": 3.0,
        "phrase_restart_each_active_run": phrase_restart_each_active_run,
        "activity_gate_preroll_ms": gate_preroll_ms,
        "activity_gate_release_ms": gate_release_ms,
        "activity_gate_fade_ms": gate_fade_ms,
        **alignment_meta,
    }
    return vocal, render_meta, loudness_meta, active_samples, engine_meta


def pitch_qa(
    synthetic: np.ndarray,
    target_f0: np.ndarray,
    active_frames: np.ndarray,
    *,
    sample_rate: int,
    f0_floor: float,
    f0_ceil: float,
    frame_period_ms: float,
) -> dict[str, float | int | None]:
    contiguous = np.ascontiguousarray(synthetic, dtype=np.float64)
    detected, times = pyworld.harvest(
        contiguous,
        sample_rate,
        f0_floor=f0_floor,
        f0_ceil=f0_ceil,
        frame_period=frame_period_ms,
    )
    detected = pyworld.stonemask(contiguous, detected, times, sample_rate)
    count = min(len(detected), len(target_f0), len(active_frames))
    valid = (
        active_frames[:count]
        & (detected[:count] > 0)
        & (target_f0[:count] > 0)
    )
    if not np.any(valid):
        return {"compared_frames": 0, "median_abs_cents": None, "p90_abs_cents": None}
    cents = np.abs(1200.0 * np.log2(detected[:count][valid] / target_f0[:count][valid]))
    return {
        "compared_frames": int(len(cents)),
        "median_abs_cents": float(np.median(cents)),
        "p90_abs_cents": float(np.percentile(cents, 90)),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Loop a fixed Mandarin Hajimi phrase along an existing guide vocal's melody."
    )
    parser.add_argument("--guide-vocal", required=True)
    parser.add_argument("--instrumental", required=True)
    parser.add_argument(
        "--engine",
        choices=("psola", "world"),
        default="psola",
        help="psola is the clean default; world remains available for A/B",
    )
    parser.add_argument(
        "--voice-cycle",
        default="hajimi-remix/assets/voicebank/hajimi_nanbei_lvdou_zh_yushu.wav",
    )
    parser.add_argument("--lyrics", default="哈基米南北绿豆")
    parser.add_argument("--voice-cycle-language", default="zh-CN")
    parser.add_argument(
        "--alignment-mode",
        choices=("performance-template", "guide-onsets", "phrase-cycle"),
        default="performance-template",
        help="performance-template borrows the timing/accent pattern of Clean V2",
    )
    parser.add_argument(
        "--voice-unit-boundaries",
        default="0,0.22,0.42,0.64,0.86,1.12,1.34,1.680816",
        help="comma-separated Mandarin unit boundaries in source seconds",
    )
    parser.add_argument(
        "--performance-boundaries",
        default="0,0.236463,0.381134,0.569706,1.274105,1.529524,2.776690,3.1",
        help="Clean V2 timing slots in cycle-relative seconds",
    )
    parser.add_argument(
        "--performance-unit-gains-db",
        default="7.717,6.884,-5.519,-2.647,5.485,0,-7.297",
        help="Clean V2 relative accent for each timing slot",
    )
    parser.add_argument("--performance-cycle-offset", type=float, default=0.132698)
    parser.add_argument("--onset-min-spacing", type=float, default=0.12)
    parser.add_argument("--onset-delta", type=float, default=0.08)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--slug", default=None)
    parser.add_argument("--title", default=None)
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--duration", type=float, default=45.0)
    parser.add_argument("--f0-floor", type=float, default=60.0)
    parser.add_argument("--f0-ceil", type=float, default=900.0)
    parser.add_argument("--frame-period-ms", type=float, default=5.0)
    parser.add_argument("--rms-threshold-db", type=float, default=-42.0)
    parser.add_argument("--periodicity-threshold", type=float, default=0.18)
    parser.add_argument("--reset-silence", type=float, default=1.5)
    parser.add_argument("--pitch-octaves", type=int, choices=(0, 1), default=0)
    parser.add_argument("--vocal-gain-db", type=float, default=-3.0)
    parser.add_argument("--vocal-peak-dbfs", type=float, default=-1.5)
    parser.add_argument("--vocal-lowpass-hz", type=float, default=12000.0)
    parser.add_argument("--limiter-lookahead-ms", type=float, default=8.0)
    parser.add_argument("--limiter-smoothing-ms", type=float, default=2.0)
    parser.add_argument("--limiter-release-ms", type=float, default=100.0)
    parser.add_argument(
        "--spectral-energy-cap-percentile", type=float, default=85.0
    )
    parser.add_argument("--target-peak-dbfs", type=float, default=-1.5)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    guide_path = resolve_project_path(args.guide_vocal)
    instrumental_path = resolve_project_path(args.instrumental)
    cycle_path = resolve_project_path(args.voice_cycle)
    output_dir = resolve_project_path(args.output_dir)
    for path in (guide_path, instrumental_path, cycle_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    if args.start < 0 or args.duration <= 0:
        raise ValueError("--start must be >= 0 and --duration must be > 0")
    if not 50.0 <= args.spectral_energy_cap_percentile <= 100.0:
        raise ValueError("--spectral-energy-cap-percentile must be between 50 and 100")
    if (
        args.limiter_lookahead_ms <= 0
        or args.limiter_smoothing_ms <= 0
        or args.limiter_release_ms <= 0
    ):
        raise ValueError("Limiter timing values must be > 0")
    if args.onset_min_spacing <= 0 or args.onset_delta <= 0:
        raise ValueError("Onset spacing and delta must be > 0")
    try:
        unit_boundaries_seconds = [
            float(value.strip())
            for value in args.voice_unit_boundaries.split(",")
            if value.strip()
        ]
        performance_boundaries_seconds = [
            float(value.strip())
            for value in args.performance_boundaries.split(",")
            if value.strip()
        ]
        performance_unit_gains_db = [
            float(value.strip())
            for value in args.performance_unit_gains_db.split(",")
            if value.strip()
        ]
    except ValueError as error:
        raise ValueError("Boundary and gain lists must contain only numbers") from error

    instrumental, sample_rate = load_audio(instrumental_path, mono=False)
    guide, guide_sr = load_audio(guide_path, mono=True)
    if guide_sr != sample_rate:
        guide = resample_channels(guide, guide_sr, sample_rate)
    cycle, cycle_sr = load_audio(cycle_path, mono=True)
    if cycle_sr != sample_rate:
        cycle = resample_channels(cycle, cycle_sr, sample_rate)

    start_sample = int(round(args.start * sample_rate))
    requested_samples = int(round(args.duration * sample_rate))
    available = min(len(instrumental), len(guide)) - start_sample
    if available <= 0:
        raise ValueError("Requested start is past the available audio")
    output_samples = min(requested_samples, available)
    instrumental = instrumental[start_sample : start_sample + output_samples]
    guide = guide[start_sample : start_sample + output_samples]

    slug = args.slug or guide_path.stem
    title = args.title or slug
    output_dir.mkdir(parents=True, exist_ok=True)
    vocal_output = output_dir / f"{slug}_hajimi_vocal.wav"
    final_output = output_dir / f"{slug}_hajimi_demo.wav"
    report_output = output_dir / "report.json"

    print(f"Guide       : {guide_path}")
    print(f"Instrument  : {instrumental_path}")
    print(f"Voice cycle : {cycle_path}")
    print(f"Window      : {args.start:.3f}s + {output_samples / sample_rate:.3f}s")
    print("Extracting guide pitch on CPU...")
    guide_features = extract_guide_features(
        guide,
        sample_rate,
        f0_floor=args.f0_floor,
        f0_ceil=args.f0_ceil,
        rms_threshold_db=args.rms_threshold_db,
        periodicity_threshold=args.periodicity_threshold,
    )
    if not np.any(guide_features["active"]):
        raise RuntimeError("No active singing detected; lower the thresholds or pick another window")

    print(f"Rendering fixed words with {args.engine.upper()}...")
    if args.engine == "psola":
        synthetic, render_meta, loudness_meta, active_samples, engine_meta = (
            render_psola_cycle(
                cycle,
                guide,
                guide_features,
                output_samples=output_samples,
                sample_rate=sample_rate,
                f0_floor=args.f0_floor,
                f0_ceil=args.f0_ceil,
                frame_period_ms=args.frame_period_ms,
                pitch_octaves=args.pitch_octaves,
                vocal_gain_db=args.vocal_gain_db,
                vocal_peak_dbfs=args.vocal_peak_dbfs,
                limiter_lookahead_ms=args.limiter_lookahead_ms,
                limiter_release_ms=args.limiter_release_ms,
                alignment_mode=args.alignment_mode,
                unit_boundaries_seconds=unit_boundaries_seconds,
                performance_boundaries_seconds=performance_boundaries_seconds,
                performance_unit_gains_db=performance_unit_gains_db,
                performance_cycle_offset_seconds=args.performance_cycle_offset,
                onset_min_spacing_seconds=args.onset_min_spacing,
                onset_delta=args.onset_delta,
            )
        )
    else:
        source_features = analyse_cycle(
            cycle,
            sample_rate,
            f0_floor=args.f0_floor,
            f0_ceil=args.f0_ceil,
            frame_period_ms=args.frame_period_ms,
            energy_cap_percentile=args.spectral_energy_cap_percentile,
        )
        synthetic, render_meta = render_world_cycle(
            source_features,
            guide_features,
            output_samples=output_samples,
            sample_rate=sample_rate,
            frame_period_ms=args.frame_period_ms,
            reset_silence_seconds=args.reset_silence,
        )
        synthetic, loudness_meta, active_samples = apply_guide_loudness(
            synthetic,
            guide,
            guide_features,
            sample_rate=sample_rate,
            vocal_gain_db=args.vocal_gain_db,
            lowpass_hz=args.vocal_lowpass_hz,
        )
        synthetic, limiter_meta = apply_lookahead_peak_limiter(
            synthetic,
            sample_rate=sample_rate,
            ceiling_dbfs=args.vocal_peak_dbfs,
            lookahead_ms=args.limiter_lookahead_ms,
            smoothing_ms=args.limiter_smoothing_ms,
        )
        loudness_meta.update(
            {
                **limiter_meta,
                "synthetic_active_rms_post_limiter_dbfs": db(
                    rms(synthetic, active_samples)
                ),
            }
        )
        engine_meta = {
            "vocal_lowpass_hz": args.vocal_lowpass_hz,
            "source_spectral_energy_cap_percentile": source_features[
                "energy_cap_percentile"
            ],
            "source_spectral_energy_cap_db": source_features["energy_cap_db"],
            "source_max_frame_attenuation_db": source_features[
                "max_frame_attenuation_db"
            ],
        }

    centered = np.repeat(synthetic[:, None], instrumental.shape[1], axis=1)
    pre_master = instrumental + centered
    invariant_error = float(np.max(np.abs((pre_master - centered) - instrumental)))
    peak_before = float(np.max(np.abs(pre_master)))
    peak_target = 10.0 ** (args.target_peak_dbfs / 20.0)
    master_gain = min(1.0, peak_target / max(peak_before, EPS))
    final_mix = pre_master * master_gain

    if not np.all(np.isfinite(final_mix)) or not np.all(np.isfinite(synthetic)):
        raise RuntimeError("Non-finite audio generated")
    sf.write(vocal_output, synthetic, sample_rate, subtype="PCM_24")
    sf.write(final_output, final_mix, sample_rate, subtype="PCM_24")

    active_pitch = np.asarray(guide_features["pitch"])
    active_pitch = active_pitch[active_pitch > 0]
    qa_pitch = pitch_qa(
        synthetic,
        np.asarray(render_meta["target_f0"]),
        np.asarray(render_meta["expected_voiced_frames"]),
        sample_rate=sample_rate,
        f0_floor=args.f0_floor,
        f0_ceil=args.f0_ceil,
        frame_period_ms=args.frame_period_ms,
    )
    report = {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(),
        "title": title,
        "lyrics": args.lyrics,
        "voice_cycle_language": args.voice_cycle_language,
        "mode": (
            f"fixed-phrase-{args.engine}-{args.alignment_mode}-mvp"
            if args.engine == "psola"
            else "fixed-phrase-world-cycle-mvp"
        ),
        "window": {
            "start_seconds": args.start,
            "duration_seconds": output_samples / sample_rate,
        },
        "audio": {
            "sample_rate": sample_rate,
            "channels": int(instrumental.shape[1]),
            "guide_active_ratio": float(np.mean(active_samples)),
            "guide_pitch_hz": {
                "p05": float(np.percentile(active_pitch, 5)),
                "median": float(np.median(active_pitch)),
                "p95": float(np.percentile(active_pitch, 95)),
            },
            "peak_before_master_dbfs": db(peak_before),
            "master_gain_db": db(master_gain),
            "final_peak_dbfs": db(float(np.max(np.abs(final_mix)))),
            "instrumental_invariant_max_error_before_master": invariant_error,
        },
        "render": {
            "engine": args.engine,
            "frame_period_ms": args.frame_period_ms,
            **engine_meta,
            "voice_unit_boundaries_seconds": unit_boundaries_seconds,
            "performance_boundaries_seconds": performance_boundaries_seconds,
            "performance_unit_gains_db": performance_unit_gains_db,
            "rendered_runs": int(render_meta["rendered_runs"]),
            "cycle_resets": int(render_meta["reset_count"]),
            "completed_source_cycles": int(render_meta["completed_source_cycles"]),
            **loudness_meta,
            "pitch_qa": qa_pitch,
        },
        "inputs": {
            "guide_vocal": str(guide_path),
            "guide_sha256": sha256(guide_path),
            "instrumental": str(instrumental_path),
            "instrumental_sha256": sha256(instrumental_path),
            "voice_cycle": str(cycle_path),
            "voice_cycle_sha256": sha256(cycle_path),
        },
        "outputs": {
            "synthetic_vocal": str(vocal_output),
            "final_mix": str(final_output),
        },
        "known_limitations": [
            "The accepted V2 timing/accent template is a fixed continuous cycle, not manual note-level alignment for this song.",
            "Long vowels originate from spoken TTS and can remain more periodic than a human singing performance.",
            "The instrumental is an AI-separated stem, so it cannot be bit-identical to an official instrumental master.",
        ],
    }
    report_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    print(f"Synthetic   : {vocal_output}")
    print(f"Final       : {final_output}")
    print(f"Report      : {report_output}")
    print(
        "QA          : "
        f"peak={report['audio']['final_peak_dbfs']:.2f} dBFS, "
        f"pitch median={qa_pitch['median_abs_cents']} cents, "
        f"active={report['audio']['guide_active_ratio']:.1%}"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:  # concise CLI failure with a non-zero status
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
