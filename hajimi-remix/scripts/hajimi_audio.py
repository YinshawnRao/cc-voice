#!/usr/bin/env python3
"""Single-song Hajimi Note/Event V1/V1.1 production entrypoint.

This wrapper intentionally keeps the expensive stages serial.  It validates
the source contract, crops both stems before analysis, delegates event
extraction and rendering to the accepted V1 implementation, creates a
two-pass loudness-normalized preview master, and writes machine QA evidence.

Auto drafts are preview-only.  No command in this file can mark a song as
human-approved or package it for delivery.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import re
import resource
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator


for _thread_variable in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "MKL_NUM_THREADS",
):
    os.environ[_thread_variable] = "2"


REPO_ROOT = Path(__file__).resolve().parents[2]
HAJIMI_ROOT = REPO_ROOT / "hajimi-remix"
TARGET_SAMPLE_RATE = 44_100
TARGET_I = -9.5
TARGET_TP = -1.0
TARGET_LRA = 11.0
# AAC decode can add roughly 0.2 dB of intersample overshoot.  Keep the WAV
# master below the public V1 ceiling so the post-mux AAC remains <= -1 dBTP.
MASTER_LIMIT_TP = -1.7
MASTER_MAX_ATTEMPTS = 4
MAX_WINDOW_SECONDS = 75.0
MIN_WINDOW_SECONDS = 20.0
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
RUN_ID_RE = re.compile(
    r"^[0-9]{8}_[a-z0-9][a-z0-9_-]*_(hev1p1|hev1)_r[0-9]{2}$"
)
EVENT_PRESET_CONTRACTS = {
    "hajimi-note-event-v1": {
        "run_tag": "hev1",
        "variant": "hybrid",
        "filename": "hajimi-note-event-v1.json",
        "title": "Hajimi Note/Event V1",
    },
    "hajimi-note-event-v1.1": {
        "run_tag": "hev1p1",
        "variant": "hybrid_v1p1",
        "filename": "hajimi-note-event-v1.1.json",
        "title": "Hajimi Note/Event V1.1",
    },
}
DEFAULT_VOICEBANK = {
    "jp_sustain": {
        "path": "hajimi-remix/assets/voicebank/hachimi_o_nameruto_phrase.wav",
        "sha256": "9cf80e21fb487edaa773eec0ec76402d9cd26c9e59b620d3609f9fb7022292f2",
    },
    "zh_attack": {
        "path": "hajimi-remix/assets/voicebank/hajimi_nanbei_lvdou_zh_yushu.wav",
        "sha256": "cd48f74a9ce92705f10814122717d5570b25bfaa7674b94fcf234150f0f99c7d",
    },
}


class ContractError(RuntimeError):
    """An input or output violated the fail-closed production contract."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def root_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()


def root_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError as error:
        raise ContractError(f"Generated audio path must remain under repository root: {path}") from error


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"Cannot read JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise ContractError(f"Expected a JSON object: {path}")
    return value


def validate_json_schema(instance: dict[str, Any], schema_path: Path, label: str) -> None:
    try:
        import jsonschema
    except ImportError as error:
        raise ContractError(
            "jsonschema is required for production validation; run this CLI with "
            "frameworks/rvc-webui-macos/.venv/bin/python"
        ) from error
    try:
        jsonschema.validate(instance, read_json(schema_path))
    except jsonschema.ValidationError as error:
        location = ".".join(str(part) for part in error.absolute_path) or "<root>"
        raise ContractError(f"{label} schema violation at {location}: {error.message}") from error


def event_preset_contract(event_preset: str) -> dict[str, Any]:
    raw = EVENT_PRESET_CONTRACTS.get(event_preset)
    if raw is None:
        raise ContractError(f"Unsupported Audio event preset: {event_preset}")
    path = HAJIMI_ROOT / "presets" / str(raw["filename"])
    if not path.is_file():
        raise ContractError(f"Missing event preset: {path}")
    value = read_json(path)
    if value.get("preset_id") != event_preset or value.get("status") != "FROZEN_INTERNAL_BASELINE":
        raise ContractError(f"Event preset identity/status is not frozen: {path}")
    return {**raw, "path": path, "sha256": sha256(path), "value": value}


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def resolve_recipe(value: str) -> Path:
    direct = root_path(value)
    if direct.is_file():
        return direct
    candidate = HAJIMI_ROOT / "recipes" / f"{value}.json"
    if candidate.is_file():
        return candidate.resolve()
    raise ContractError(f"Recipe not found: {value}")


def source_entry(recipe: dict[str, Any], canonical: str, legacy: str) -> dict[str, Any]:
    source = recipe.get("source")
    if not isinstance(source, dict):
        raise ContractError("recipe.source must be an object")
    entry = source.get(canonical, source.get(legacy))
    if not isinstance(entry, dict):
        raise ContractError(f"Missing recipe source entry: {canonical}/{legacy}")
    return entry


def audio_config(recipe: dict[str, Any]) -> dict[str, Any]:
    if isinstance(recipe.get("audio"), dict):
        return dict(recipe["audio"])
    event = recipe.get("event_extraction")
    arrangement = recipe.get("arrangement")
    presets = recipe.get("presets")
    if not all(isinstance(value, dict) for value in (event, arrangement, presets)):
        raise ContractError("Recipe needs canonical audio or event_extraction/arrangement/presets")
    return {
        "event_preset": presets.get("audio"),
        "mix_preset": presets.get("mix"),
        "midi_min": event.get("midi_min"),
        "midi_max": event.get("midi_max"),
        "pitch_floor_hz": event.get("pitch_floor_hz"),
        "pitch_ceiling_hz": event.get("pitch_ceiling_hz"),
        "structure_points_seconds": event.get("structure_points_relative_seconds", []),
        "bridge_gap_seconds": arrangement.get("bridge_gap_seconds"),
        "climax_seconds": arrangement.get("climax_seconds"),
    }


def voicebank_entry(recipe: dict[str, Any], key: str) -> dict[str, Any]:
    voicebank = recipe.get("voicebank")
    if isinstance(voicebank, dict) and isinstance(voicebank.get(key), dict):
        return voicebank[key]
    # The canonical song-manifest schema keeps the frozen V1 voicebank outside
    # per-song configuration.  Legacy recipes may still pin it explicitly.
    return DEFAULT_VOICEBANK[key]


def display_config(recipe: dict[str, Any]) -> dict[str, str]:
    display = recipe.get("display")
    if not isinstance(display, dict):
        raise ContractError("recipe.display must be an object")
    title = display.get("title", display.get("title_zh"))
    title_en = display.get("title_en", display.get("title_english"))
    artist = display.get("artist_label")
    if not all(isinstance(value, str) and value.strip() for value in (title, title_en, artist)):
        raise ContractError("display title/title_zh, title_en and artist_label are required")
    return {
        "title": str(title).strip(),
        "title_en": str(title_en).strip(),
        "artist_label": str(artist).strip(),
        "credit": str(display.get("credit", display.get("hajimi_label", "HAJIMI REMIX"))),
    }


def system_swap_used_mb() -> float | None:
    """Return a best-effort system swap snapshot without adding a dependency.

    Swap is system-wide rather than attributable to one child process, so the
    report labels it as a snapshot/delta and never treats it as an audio QA
    pass condition.
    """

    if sys.platform == "darwin":
        try:
            completed = subprocess.run(
                ["sysctl", "-n", "vm.swapusage"],
                cwd=REPO_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                env=os.environ.copy(),
            )
        except OSError:
            return None
        if completed.returncode == 0:
            match = re.search(r"\bused\s*=\s*([0-9.]+)([KMG])", completed.stdout)
            if match:
                value = float(match.group(1))
                scale = {"K": 1 / 1024, "M": 1, "G": 1024}[match.group(2)]
                return round(value * scale, 2)
        return None
    meminfo = Path("/proc/meminfo")
    if meminfo.is_file():
        try:
            values = {
                key.rstrip(":"): float(number)
                for key, number, *_rest in (
                    line.split() for line in meminfo.read_text(encoding="utf-8").splitlines()
                )
                if key in {"SwapTotal:", "SwapFree:"}
            }
        except (OSError, ValueError):
            return None
        if {"SwapTotal", "SwapFree"} <= values.keys():
            return round((values["SwapTotal"] - values["SwapFree"]) / 1024.0, 2)
    return None


def system_swap_counters() -> dict[str, int] | None:
    """Return cumulative OS swap-in/out page counters when available."""

    command = ["vm_stat"] if sys.platform == "darwin" else ["cat", "/proc/vmstat"]
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            env=os.environ.copy(),
        )
    except OSError:
        return None
    if completed.returncode:
        return None
    if sys.platform == "darwin":
        values: dict[str, int] = {}
        for key, label in (("swapins_pages", "Swapins"), ("swapouts_pages", "Swapouts")):
            match = re.search(rf"^{label}:\s*([0-9]+)\.", completed.stdout, flags=re.MULTILINE)
            if match:
                values[key] = int(match.group(1))
        return values if len(values) == 2 else None
    values = {}
    for key, label in (("swapins_pages", "pswpin"), ("swapouts_pages", "pswpout")):
        match = re.search(rf"^{label}\s+([0-9]+)$", completed.stdout, flags=re.MULTILINE)
        if match:
            values[key] = int(match.group(1))
    return values if len(values) == 2 else None


def run_command(command: list[str], *, capture: bool = True) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    swap_before = system_swap_used_mb()
    swap_counters_before = system_swap_counters()
    started = time.perf_counter()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            check=False,
            env=os.environ.copy(),
        )
    except OSError as error:
        raise ContractError(f"Cannot start command {command[0]}: {error}") from error
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    max_rss_bytes = int(after.ru_maxrss if sys.platform == "darwin" else after.ru_maxrss * 1024)
    swap_after = system_swap_used_mb()
    swap_counters_after = system_swap_counters()
    swap_counter_delta = (
        {
            key: max(0, swap_counters_after[key] - swap_counters_before[key])
            for key in swap_counters_before
        }
        if swap_counters_before is not None and swap_counters_after is not None
        else None
    )
    measurement = {
        "wall_seconds": round(time.perf_counter() - started, 3),
        "user_cpu_seconds": round(max(0.0, after.ru_utime - before.ru_utime), 3),
        "system_cpu_seconds": round(max(0.0, after.ru_stime - before.ru_stime), 3),
        "max_resident_set_mb": round(max_rss_bytes / 1024.0 / 1024.0, 2),
        "system_swap_used_mb_before": swap_before,
        "system_swap_used_mb_after": swap_after,
        "system_swap_delta_mb": (
            round(swap_after - swap_before, 2)
            if swap_before is not None and swap_after is not None
            else None
        ),
        "system_swap_counters_before": swap_counters_before,
        "system_swap_counters_after": swap_counters_after,
        "system_swap_counter_delta": swap_counter_delta,
    }
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "").strip()[-4000:]
        raise ContractError(
            f"Command failed ({completed.returncode}): {' '.join(command[:5])}\n{detail}"
        )
    return completed, measurement


def combine_performance(measurements: list[dict[str, Any]]) -> dict[str, Any]:
    if not measurements:
        raise ContractError("Cannot combine an empty performance measurement list")
    counter_deltas = [
        value.get("system_swap_counter_delta")
        for value in measurements
        if isinstance(value.get("system_swap_counter_delta"), dict)
    ]
    return {
        "stage_count": len(measurements),
        "wall_seconds": round(sum(float(value["wall_seconds"]) for value in measurements), 3),
        "user_cpu_seconds": round(sum(float(value["user_cpu_seconds"]) for value in measurements), 3),
        "system_cpu_seconds": round(sum(float(value["system_cpu_seconds"]) for value in measurements), 3),
        "max_resident_set_mb": max(float(value["max_resident_set_mb"]) for value in measurements),
        "system_swap_counter_delta": (
            {
                key: sum(int(value.get(key, 0)) for value in counter_deltas)
                for key in ("swapins_pages", "swapouts_pages")
            }
            if counter_deltas else None
        ),
        "stages": measurements,
    }


def ffprobe(path: Path) -> dict[str, Any]:
    completed, _ = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,codec_name,sample_rate,channels,duration,duration_ts,time_base,bits_per_sample,bits_per_raw_sample,sample_fmt,width,height,r_frame_rate,pix_fmt,color_space,color_transfer,color_primaries",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ]
    )
    return json.loads(completed.stdout)


def audio_stream(probe: dict[str, Any]) -> dict[str, Any]:
    streams = [stream for stream in probe.get("streams", []) if stream.get("codec_type") == "audio"]
    if len(streams) != 1:
        raise ContractError(f"Expected exactly one audio stream, found {len(streams)}")
    return streams[0]


def source_frame_count(stream: dict[str, Any], probe: dict[str, Any]) -> int:
    duration_ts = stream.get("duration_ts")
    time_base = str(stream.get("time_base", ""))
    sample_rate = int(stream.get("sample_rate", 0))
    if duration_ts is not None and time_base == f"1/{sample_rate}":
        return int(duration_ts)
    return int(round(float(probe["format"]["duration"]) * sample_rate))


def wav_info(path: Path) -> dict[str, Any]:
    probe = ffprobe(path)
    stream = audio_stream(probe)
    codec = str(stream.get("codec_name", ""))
    if not codec.startswith("pcm_"):
        raise ContractError(f"Expected PCM WAV, found {codec or 'unknown'}: {path}")
    sample_rate = int(stream.get("sample_rate", 0))
    channels = int(stream.get("channels", 0))
    frames = source_frame_count(stream, probe)
    bits = int(stream.get("bits_per_raw_sample") or stream.get("bits_per_sample") or 0)
    if bits <= 0:
        codec_widths = {"pcm_s16le": 2, "pcm_s24le": 3, "pcm_s32le": 4, "pcm_f32le": 4}
        sample_width = codec_widths.get(codec, 0)
    else:
        sample_width = (bits + 7) // 8
    if sample_rate <= 0 or channels <= 0 or frames <= 0 or sample_width <= 0:
        raise ContractError(f"Incomplete PCM metadata for WAV: {path}")
    return {
        "path": root_relative(path),
        "sha256": sha256(path),
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_width_bytes": sample_width,
        "frame_count": frames,
        "duration_seconds": round(frames / sample_rate, 6),
    }


def validate_hash(entry: dict[str, Any], label: str) -> Path:
    raw_path = entry.get("path")
    expected = entry.get("sha256")
    if not isinstance(raw_path, str) or not raw_path:
        raise ContractError(f"{label}.path is required")
    if not isinstance(expected, str) or not HASH_RE.fullmatch(expected):
        raise ContractError(f"{label}.sha256 must be 64 lowercase hex characters")
    path = root_path(raw_path)
    if not path.is_file():
        raise ContractError(f"Missing {label}: {path}")
    actual = sha256(path)
    if actual != expected:
        raise ContractError(f"SHA256 mismatch for {label}: expected {expected}, got {actual}")
    return path


def validate_recipe(recipe_path: Path) -> dict[str, Any]:
    recipe = read_json(recipe_path)
    validate_json_schema(
        recipe, HAJIMI_ROOT / "schemas" / "song-recipe.schema.json", "song recipe"
    )
    song_id = recipe.get("song_id")
    if not isinstance(song_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", song_id):
        raise ContractError("song_id must be a lowercase slug")
    display_config(recipe)
    window = recipe.get("window")
    if not isinstance(window, dict):
        raise ContractError("recipe.window must be an object")
    start = float(window.get("start_seconds", -1))
    end = float(window.get("end_seconds", -1))
    duration = end - start
    if start < 0 or end <= start or not MIN_WINDOW_SECONDS <= duration <= MAX_WINDOW_SECONDS:
        raise ContractError(f"Window must be {MIN_WINDOW_SECONDS:g}-{MAX_WINDOW_SECONDS:g}s: {start}-{end}")
    declared_duration = window.get("duration_seconds")
    if declared_duration is not None and abs(float(declared_duration) - duration) > 1 / TARGET_SAMPLE_RATE:
        raise ContractError("window.duration_seconds does not equal end_seconds - start_seconds")

    audio = audio_config(recipe)
    event_contract = event_preset_contract(str(audio.get("event_preset")))
    if audio.get("mix_preset") != "hajimi-pop-v1":
        raise ContractError("Mix preset must be hajimi-pop-v1")
    mix_preset_path = HAJIMI_ROOT / "presets" / "hajimi-pop-v1.json"
    mix_preset = read_json(mix_preset_path)
    if mix_preset.get("preset_id") != "hajimi-pop-v1":
        raise ContractError("Mix preset identity mismatch")
    regression_paths: dict[str, Path] = {}
    if audio.get("event_preset") == "hajimi-note-event-v1.1":
        regression = recipe.get("regression")
        if not isinstance(regression, dict):
            raise ContractError("V1.1 recipe requires regression baseline evidence")
        regression_paths = {
            "baseline_event": validate_hash(
                {
                    "path": regression.get("baseline_event_path"),
                    "sha256": regression.get("baseline_event_sha256"),
                },
                "V1 baseline event draft",
            ),
            "baseline_dry": validate_hash(
                {
                    "path": regression.get("baseline_dry_path"),
                    "sha256": regression.get("baseline_dry_sha256"),
                },
                "V1 baseline dry",
            ),
            "baseline_render_report": validate_hash(
                {
                    "path": regression.get("baseline_render_report_path"),
                    "sha256": regression.get("baseline_render_report_sha256"),
                },
                "V1 baseline render report",
            ),
        }
    midi_min, midi_max = int(audio["midi_min"]), int(audio["midi_max"])
    floor, ceiling = float(audio["pitch_floor_hz"]), float(audio["pitch_ceiling_hz"])
    if midi_min >= midi_max or floor >= ceiling:
        raise ContractError("Invalid MIDI or pitch range")
    bridge = float(audio["bridge_gap_seconds"])
    climax = float(audio["climax_seconds"])
    structure = [float(value) for value in audio.get("structure_points_seconds", [])]
    if not 0 <= bridge <= 0.28 or not 0 <= climax <= duration:
        raise ContractError("bridge_gap_seconds or climax_seconds lies outside V1 limits")
    if structure != sorted(set(structure)) or any(point <= 0 or point >= duration for point in structure):
        raise ContractError("structure points must be unique, sorted and inside the cropped window")

    guide_entry = source_entry(recipe, "guide", "guide_vocal")
    instrumental_entry = source_entry(recipe, "instrumental", "instrumental")
    guide_path = validate_hash(guide_entry, "guide vocal")
    instrumental_path = validate_hash(instrumental_entry, "instrumental")
    jp_path = validate_hash(voicebank_entry(recipe, "jp_sustain"), "Japanese sustain voicebank")
    zh_path = validate_hash(voicebank_entry(recipe, "zh_attack"), "Chinese attack voicebank")
    mv = recipe.get("mv", {})
    if isinstance(mv, dict) and isinstance(mv.get("source_video"), dict):
        if mv.get("alignment_evidence", {}).get("status") != "PASS":
            raise ContractError("MV alignment evidence must PASS")

    guide_probe = ffprobe(guide_path)
    instrumental_probe = ffprobe(instrumental_path)
    guide_stream = audio_stream(guide_probe)
    instrumental_stream = audio_stream(instrumental_probe)
    guide_rate = int(guide_stream["sample_rate"])
    instrumental_rate = int(instrumental_stream["sample_rate"])
    guide_frames = source_frame_count(guide_stream, guide_probe)
    instrumental_frames = source_frame_count(instrumental_stream, instrumental_probe)
    if guide_rate < TARGET_SAMPLE_RATE or instrumental_rate < TARGET_SAMPLE_RATE:
        raise ContractError("Original stems must be at least 44.1 kHz")
    if guide_rate != instrumental_rate or guide_frames != instrumental_frames:
        raise ContractError(
            "Original guide/instrumental are not co-timed: "
            f"rate {guide_rate}/{instrumental_rate}, frames {guide_frames}/{instrumental_frames}"
        )
    if end > guide_frames / guide_rate + 1 / guide_rate:
        raise ContractError("Requested window exceeds source duration")

    return {
        "recipe": recipe,
        "recipe_path": recipe_path,
        "recipe_sha256": sha256(recipe_path),
        "song_id": song_id,
        "window": {"start": start, "end": end, "duration": duration},
        "audio": audio,
        "event_contract": event_contract,
        "mix_preset": {
            "path": mix_preset_path,
            "sha256": sha256(mix_preset_path),
            "value": mix_preset,
        },
        "paths": {
            "guide": guide_path,
            "instrumental": instrumental_path,
            "jp_voice": jp_path,
            "zh_voice": zh_path,
            **regression_paths,
        },
        "source": {
            "sample_rate": guide_rate,
            "frame_count": guide_frames,
            "duration_seconds": round(guide_frames / guide_rate, 6),
        },
    }


def python_for_audio() -> Path:
    candidate = REPO_ROOT / "frameworks" / "rvc-webui-macos" / ".venv" / "bin" / "python"
    return candidate if candidate.is_file() else Path(sys.executable)


def run_id_for(song_id: str, event_contract: dict[str, Any], override: str | None) -> str:
    run_tag = str(event_contract["run_tag"])
    today = dt.date.today().strftime("%Y%m%d")
    run_id = override or f"{today}_{song_id}_{run_tag}_r01"
    if not RUN_ID_RE.fullmatch(run_id):
        raise ContractError(f"Invalid run_id: {run_id}")
    if f"_{song_id}_{run_tag}_" not in run_id:
        raise ContractError(
            f"run_id must match song_id={song_id} and preset tag={run_tag}"
        )
    return run_id


def output_paths(song_id: str, run_id: str) -> dict[str, Path]:
    directory = HAJIMI_ROOT / "outputs" / song_id / run_id
    return {
        "directory": directory,
        "guide": directory / "guide.wav",
        "instrumental": directory / "instrumental.wav",
        "event": directory / "event-draft.json",
        "manifest": directory / "song-manifest.json",
        "render_report": directory / "render-report.json",
        "qa_report": directory / "audio-qa-report.json",
        "master": directory / f"{song_id}_hajimi_preview_master.wav",
        "vocal": directory / f"{song_id}_hajimi_preview_vocal.wav",
    }


def variant_path(paths: dict[str, Path], song_id: str, variant: str, kind: str) -> Path:
    if kind not in {"dry", "vocal", "premaster"}:
        raise ContractError(f"Unknown variant artifact kind: {kind}")
    return paths["directory"] / f"{song_id}_{variant}_{kind}.wav"


@contextlib.contextmanager
def audio_lock() -> Iterator[None]:
    lock_path = HAJIMI_ROOT / "work" / ".hajimi-audio.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ContractError("Another Hajimi audio task holds the serial production lock") from error
        stream.seek(0)
        stream.truncate()
        stream.write(f"pid={os.getpid()}\n")
        stream.flush()
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def crop_stem(source: Path, destination: Path, start: float, end: float, channels: int) -> dict[str, Any]:
    layout = "mono" if channels == 1 else "stereo"
    _, performance = run_command(
        [
            "ffmpeg", "-v", "error", "-xerror", "-threads", "2",
            "-filter_threads", "2", "-filter_complex_threads", "2", "-i", str(source),
            "-af", f"atrim=start={start:.9f}:end={end:.9f},asetpts=PTS-STARTPTS,aformat=channel_layouts={layout}",
            "-ar", str(TARGET_SAMPLE_RATE), "-ac", str(channels), "-c:a", "pcm_s24le",
            "-map_metadata", "-1", "-y", str(destination),
        ]
    )
    return performance


def dynamic_sections(phrase_ids: list[int]) -> list[dict[str, Any]]:
    unique = sorted(set(phrase_ids))
    if not unique:
        raise ContractError("Draft contains no phrase ids")
    roles = ["open", "build", "climax", "outro"][: min(4, len(unique))]
    sections: list[dict[str, Any]] = []
    for index, role in enumerate(roles):
        left = round(index * len(unique) / len(roles))
        right = round((index + 1) * len(unique) / len(roles))
        ids = unique[left:right]
        if not ids:
            continue
        sections.append(
            {
                "phrase_ids": ids,
                "role": role,
                "label": role.upper(),
                "descriptor": f"Dynamic V1 section mapped from phrase ids {ids[0]}-{ids[-1]}",
            }
        )
    return sections


def build_manifest(validated: dict[str, Any], run_id: str, paths: dict[str, Path], status: str) -> dict[str, Any]:
    recipe = validated["recipe"]
    display = display_config(recipe)
    guide = wav_info(paths["guide"])
    instrumental = wav_info(paths["instrumental"])
    if guide["sample_rate"] != instrumental["sample_rate"] or guide["frame_count"] != instrumental["frame_count"]:
        raise ContractError("Cropped guide/instrumental frame mismatch")
    event = read_json(paths["event"])
    phrase_ids = [int(item["phrase_id"]) for item in event.get("events", [])]
    original_guide = source_entry(recipe, "guide", "guide_vocal")
    original_instrumental = source_entry(recipe, "instrumental", "instrumental")
    audio = validated["audio"]
    return {
        "schema_version": 1,
        "artifact_kind": "hajimi-song-manifest",
        "song_id": validated["song_id"],
        "run_id": run_id,
        "status": status,
        "display": {
            **display,
            "status": "INTERNAL AUTO-DRAFT PREVIEW / HUMAN LISTENING PENDING",
        },
        "source": {
            "guide": {"path": str(original_guide["path"]), "sha256": str(original_guide["sha256"])},
            "instrumental": {
                "path": str(original_instrumental["path"]),
                "sha256": str(original_instrumental["sha256"]),
            },
        },
        "window": {
            "start_seconds": validated["window"]["start"],
            "end_seconds": validated["window"]["end"],
            "approval_note": str(
                recipe.get("window", {}).get(
                    "approval_note",
                    recipe.get("window", {}).get("selection_evidence", "Window selected from prior local analysis"),
                )
            )[:500],
        },
        "audio": {
            "event_preset": str(audio["event_preset"]),
            "event_preset_sha256": str(validated["event_contract"]["sha256"]),
            "mix_preset": "hajimi-pop-v1",
            "mix_preset_sha256": str(validated["mix_preset"]["sha256"]),
            "midi_min": int(audio["midi_min"]),
            "midi_max": int(audio["midi_max"]),
            "pitch_floor_hz": float(audio["pitch_floor_hz"]),
            "pitch_ceiling_hz": float(audio["pitch_ceiling_hz"]),
            "bridge_gap_seconds": float(audio["bridge_gap_seconds"]),
            "climax_seconds": float(audio["climax_seconds"]),
            "structure_points_seconds": [float(value) for value in audio.get("structure_points_seconds", [])],
        },
        "sections": dynamic_sections(phrase_ids),
        "mv": {"preset": "hajimi-rain-window-v1", "visual_manifest": None, "show_bpm": False},
        "inputs": {
            "guide": {key: guide[key] for key in ("path", "sha256", "sample_rate", "channels", "frame_count", "duration_seconds")},
            "instrumental": {
                key: instrumental[key]
                for key in ("path", "sha256", "sample_rate", "channels", "frame_count", "duration_seconds")
            },
        },
        "created_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
    }


def draft(validated: dict[str, Any], run_id: str, paths: dict[str, Path], force: bool) -> dict[str, Any]:
    directory = paths["directory"]
    protected = [paths["guide"], paths["instrumental"], paths["event"], paths["manifest"]]
    existing = [str(path) for path in protected if path.exists()]
    if existing and not force:
        raise ContractError(f"Draft outputs already exist; use a new run_id or --force: {existing}")
    rendered_files = [
        paths["render_report"], paths["qa_report"], paths["master"], paths["vocal"],
        directory / f"{validated['song_id']}_{validated['event_contract']['variant']}_dry.wav",
        directory / f"{validated['song_id']}_{validated['event_contract']['variant']}_vocal.wav",
        directory / f"{validated['song_id']}_{validated['event_contract']['variant']}_premaster.wav",
    ]
    rendered_existing = [str(path) for path in rendered_files if path.exists()]
    if rendered_existing:
        raise ContractError(
            "Draft cannot replace a run that already has render/QA artifacts; "
            f"use a new run_id: {rendered_existing}"
        )
    directory.mkdir(parents=True, exist_ok=True)
    crop_performance = {
        "guide": crop_stem(
            validated["paths"]["guide"], paths["guide"],
            validated["window"]["start"], validated["window"]["end"], 1,
        ),
        "instrumental": crop_stem(
            validated["paths"]["instrumental"], paths["instrumental"],
            validated["window"]["start"], validated["window"]["end"], 2,
        ),
    }
    guide = wav_info(paths["guide"])
    instrumental = wav_info(paths["instrumental"])
    if guide["sample_rate"] != TARGET_SAMPLE_RATE or instrumental["sample_rate"] != TARGET_SAMPLE_RATE:
        raise ContractError("Cropped stems are not 44.1 kHz")
    if guide["channels"] != 1 or instrumental["channels"] != 2:
        raise ContractError("Cropped guide must be mono and instrumental stereo")
    if guide["frame_count"] != instrumental["frame_count"]:
        raise ContractError("Cropped stem frame mismatch; silent truncation is forbidden")
    if guide["duration_seconds"] > MAX_WINDOW_SECONDS:
        raise ContractError("Crop-first guard failed: cropped guide exceeds 75 seconds")

    audio = validated["audio"]
    if audio["event_preset"] == "hajimi-note-event-v1.1":
        event = read_json(validated["paths"]["baseline_event"])
        source = event.get("source")
        if not isinstance(source, dict):
            raise ContractError("Frozen V1 event draft has no source evidence")
        source.update(
            {
                "vocal_stem": root_relative(paths["guide"]),
                "sha256": guide["sha256"],
                "sample_rate": guide["sample_rate"],
                "channels": guide["channels"],
                "source_offset_seconds": validated["window"]["start"],
            }
        )
        event["window"] = {
            "start_seconds": validated["window"]["start"],
            "end_seconds": validated["window"]["end"],
            "duration_seconds": round(validated["window"]["duration"], 6),
        }
        extraction_performance = {
            "mode": "FROZEN_V1_EVENT_REUSE_FOR_SINGLE_VARIABLE_REGRESSION",
            "baseline_event_sha256": validated["recipe"]["regression"]["baseline_event_sha256"],
            "wall_seconds": 0.0,
            "max_resident_set_mb": 0.0,
        }
        write_json(paths["event"], event)
    else:
        extractor_command = [
            str(python_for_audio()), str(HAJIMI_ROOT / "note_event_poc" / "extract_events.py"),
            "--vocal", str(paths["guide"]), "--output", str(paths["event"]),
            "--source-offset", str(validated["window"]["start"]),
            "--max-duration", str(MAX_WINDOW_SECONDS),
            "--pitch-floor", str(audio["pitch_floor_hz"]),
            "--pitch-ceiling", str(audio["pitch_ceiling_hz"]),
            "--midi-min", str(audio["midi_min"]), "--midi-max", str(audio["midi_max"]),
        ]
        points = audio.get("structure_points_seconds", [])
        if points:
            extractor_command += ["--structure-points", *[str(value) for value in points]]
        _, extraction_performance = run_command(extractor_command)
        event = read_json(paths["event"])
    if event.get("status") != "AUTO_DRAFT_REQUIRES_HUMAN_LISTENING":
        raise ContractError("Extractor returned an unexpected status")
    if not event.get("events"):
        raise ContractError("Extractor returned no stable note events")
    event["pipeline_evidence"] = {
        "recipe_path": root_relative(validated["recipe_path"]),
        "recipe_sha256": validated["recipe_sha256"],
        "crop_performance": crop_performance,
        "extraction_wrapper_performance": extraction_performance,
        "event_source": (
            "FROZEN_V1_REGRESSION_COPY"
            if audio["event_preset"] == "hajimi-note-event-v1.1"
            else "CURRENT_EXTRACTOR"
        ),
        "manual_listening": "NOT_PERFORMED_BY_AUTOMATION",
    }
    write_json(paths["event"], event)
    manifest = build_manifest(validated, run_id, paths, "AUTO_DRAFTED")
    validate_json_schema(
        manifest, HAJIMI_ROOT / "schemas" / "song-manifest.schema.json", "song manifest"
    )
    write_json(paths["manifest"], manifest)
    return {
        "status": "AUTO_DRAFTED",
        "run_id": run_id,
        "output_directory": root_relative(directory),
        "event_count": event["summary"]["event_count"],
        "phrase_count": event["summary"]["phrase_count"],
        "manual_listening": "REQUIRED_BEFORE_APPROVAL_OR_PACKAGE",
    }


def parse_loudnorm(text: str) -> dict[str, Any]:
    candidates = re.findall(r"\{\s*\"input_i\".*?\}", text, flags=re.DOTALL)
    if not candidates:
        raise ContractError("FFmpeg loudnorm did not emit JSON measurements")
    try:
        return json.loads(candidates[-1])
    except json.JSONDecodeError as error:
        raise ContractError(f"Cannot parse loudnorm measurements: {error}") from error


def loudnorm_measure(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    completed, performance = run_command(
        [
            "ffmpeg", "-hide_banner", "-v", "info", "-nostats", "-threads", "2",
            "-filter_threads", "2", "-i", str(path),
            "-af", f"loudnorm=I={TARGET_I}:LRA={TARGET_LRA}:TP={TARGET_TP}:print_format=json",
            "-f", "null", "-",
        ]
    )
    return parse_loudnorm(completed.stderr), performance


def loudnorm_master(premaster: Path, destination: Path, frame_count: int) -> dict[str, Any]:
    """Hit the frozen loudness gate with an oversampled, measured limiter.

    FFmpeg loudnorm correctly refuses to reach -9.5 LUFS when the premaster
    crest factor would violate the true-peak ceiling.  The V1 style master is
    intentionally dense, so first measure the premaster, then iteratively feed
    an oversampled look-ahead limiter.  Every pass is measured with EBU R128;
    the loop stops only when both contract gates pass.
    """
    source_measurement, source_performance = loudnorm_measure(premaster)
    source_i = float(source_measurement["input_i"])
    if not (-100.0 < source_i < 10.0):
        raise ContractError(f"Invalid premaster loudness: {source_i}")
    gain_db = max(-12.0, min(24.0, TARGET_I - source_i + 2.5))
    attempts: list[dict[str, Any]] = []
    limit_linear = 10.0 ** (MASTER_LIMIT_TP / 20.0)
    for attempt in range(1, MASTER_MAX_ATTEMPTS + 1):
        level_in = 10.0 ** (gain_db / 20.0)
        filter_graph = (
            "aresample=192000,"
            f"alimiter=level_in={level_in:.9f}:limit={limit_linear:.9f}:"
            "attack=5:release=80:level=false:latency=true,"
            f"aresample={TARGET_SAMPLE_RATE},apad=whole_len={frame_count},"
            f"atrim=end_sample={frame_count},asetpts=PTS-STARTPTS"
        )
        _, render_performance = run_command(
            [
                "ffmpeg", "-hide_banner", "-v", "error", "-nostats", "-threads", "2",
                "-filter_threads", "2", "-i", str(premaster), "-af", filter_graph,
                "-ar", str(TARGET_SAMPLE_RATE), "-ac", "2", "-c:a", "pcm_s24le",
                "-map_metadata", "-1", "-y", str(destination),
            ]
        )
        measurement, measure_performance = loudnorm_measure(destination)
        integrated = float(measurement["input_i"])
        true_peak = float(measurement["input_tp"])
        attempts.append(
            {
                "attempt": attempt,
                "gain_db": round(gain_db, 4),
                "measurement": measurement,
                "performance": {"render": render_performance, "measure": measure_performance},
            }
        )
        if TARGET_I - 0.5 <= integrated <= TARGET_I + 0.5 and true_peak <= TARGET_TP + 1e-6:
            return {
                "mode": "adaptive-oversampled-true-peak-limiter",
                "source_measurement": source_measurement,
                "source_measurement_performance": source_performance,
                "limiter_true_peak_dbtp": MASTER_LIMIT_TP,
                "attempts": attempts,
                "final": measurement,
            }
        gain_db = max(-12.0, min(24.0, gain_db + TARGET_I - integrated))
    raise ContractError(
        "Adaptive mastering could not satisfy loudness/true-peak gates; "
        f"last measurement={attempts[-1]['measurement']}"
    )


def render(validated: dict[str, Any], run_id: str, paths: dict[str, Path], allow_preview: bool) -> dict[str, Any]:
    if not allow_preview:
        raise ContractError(
            "event-draft.json is not human-approved; render requires --allow-auto-draft-preview"
        )
    required = (paths["guide"], paths["instrumental"], paths["event"], paths["manifest"])
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ContractError(f"Run draft first; missing: {missing}")
    event = read_json(paths["event"])
    if event.get("status") != "AUTO_DRAFT_REQUIRES_HUMAN_LISTENING":
        raise ContractError("Preview gate only accepts the explicit auto-draft status")
    pipeline_evidence = event.get("pipeline_evidence")
    if not isinstance(pipeline_evidence, dict):
        raise ContractError("event draft has no pipeline evidence")
    pipeline_evidence["recipe_path"] = root_relative(validated["recipe_path"])
    pipeline_evidence["recipe_sha256"] = validated["recipe_sha256"]
    write_json(paths["event"], event)
    guide = wav_info(paths["guide"])
    instrumental = wav_info(paths["instrumental"])
    if guide["frame_count"] != instrumental["frame_count"]:
        raise ContractError("Guide/instrumental frame mismatch before render")
    renderer = HAJIMI_ROOT / "greenlight_poc" / "render_demo.py"
    if not renderer.is_file():
        raise ContractError(f"Missing V1 renderer: {renderer}")
    audio = validated["audio"]
    event_contract = validated["event_contract"]
    variant = str(event_contract["variant"])
    title = f"{display_config(validated['recipe'])['title']} · {event_contract['title']}"
    renderer_command = [
        str(python_for_audio()), str(renderer),
        "--event-draft", str(paths["event"]), "--guide", str(paths["guide"]),
        "--instrumental", str(paths["instrumental"]),
        "--jp-voice", str(validated["paths"]["jp_voice"]),
        "--zh-voice", str(validated["paths"]["zh_voice"]),
        "--output-dir", str(paths["directory"]), "--sample-rate", str(TARGET_SAMPLE_RATE),
        "--climax-seconds", str(audio["climax_seconds"]), "--variants", variant,
        "--slug", validated["song_id"], "--title", title,
        "--bridge-gap-seconds", str(audio["bridge_gap_seconds"]),
    ]
    if audio["event_preset"] == "hajimi-note-event-v1.1":
        renderer_command += ["--sustain-preset", str(event_contract["path"])]
        configured_f0_safety_group_ids = [
            int(value)
            for value in validated["recipe"]["regression"].get("f0_safety_group_ids", [])
        ]
        if configured_f0_safety_group_ids:
            renderer_command += [
                "--f0-safety-group-ids",
                *[str(group_id) for group_id in configured_f0_safety_group_ids],
            ]
    else:
        configured_f0_safety_group_ids = []
    renderer_measurements: list[dict[str, Any]] = []
    _, candidate_render_performance = run_command(renderer_command)
    renderer_measurements.append(candidate_render_performance)
    raw_report = read_json(paths["render_report"])
    hybrid = raw_report.get("variants", {}).get(variant)
    if not isinstance(hybrid, dict):
        raise ContractError(f"Renderer did not produce the {variant} variant")
    premaster = root_path(hybrid.get("files", {}).get("premaster", ""))
    rendered_vocal = root_path(hybrid.get("files", {}).get("vocal", ""))
    if not premaster.is_file() or not rendered_vocal.is_file():
        raise ContractError("Renderer report points to missing hybrid files")
    f0_safety_evidence: dict[str, Any] | None = None
    if audio["event_preset"] == "hajimi-note-event-v1.1":
        candidate_dry = variant_path(
            paths, validated["song_id"], variant, "dry"
        )
        preflight_started = time.perf_counter()
        candidate_f0 = independent_f0_metrics(
            candidate_dry, event["events"], TARGET_SAMPLE_RATE
        )
        failed_event_ids = {
            int(item["event_id"])
            for item in candidate_f0["event_median_octave_errors"]
        }
        discovered_group_ids = sorted(
            int(group["group_id"])
            for group in hybrid["groups"]
            if failed_event_ids.intersection(
                int(event_id) for event_id in group["note_event_ids"]
            )
        )
        maximum_ratio = float(
            event_contract["value"]["sustain_extension"]["f0_safety_maximum_group_ratio"]
        )
        if configured_f0_safety_group_ids and discovered_group_ids:
            raise ContractError(
                "Configured F0 safety groups no longer cover the independent failures: "
                f"configured={configured_f0_safety_group_ids} failures={discovered_group_ids}"
            )
        safety_group_ids = (
            configured_f0_safety_group_ids
            if configured_f0_safety_group_ids
            else discovered_group_ids
        )
        if len(safety_group_ids) / max(len(hybrid["groups"]), 1) > maximum_ratio:
            raise ContractError(
                "Independent F0 safety requires too many V1 groups: "
                f"{safety_group_ids}"
            )
        for group in hybrid["groups"]:
            expected_method = (
                "f0_safety_v1_group_fallback"
                if configured_f0_safety_group_ids
                else "phase_locked_microcycle_8p_2p"
            )
            if int(group["group_id"]) in safety_group_ids and group.get("method") != expected_method:
                raise ContractError(
                    "Independent F0 safety group method mismatch: "
                    f"group {group['group_id']} expected={expected_method} "
                    f"actual={group.get('method')}"
                )
        if discovered_group_ids:
            _, repair_render_performance = run_command(
                [
                    *renderer_command,
                    "--f0-safety-group-ids",
                    *[str(group_id) for group_id in discovered_group_ids],
                ]
            )
            renderer_measurements.append(repair_render_performance)
            raw_report = read_json(paths["render_report"])
            hybrid = raw_report.get("variants", {}).get(variant)
            if not isinstance(hybrid, dict):
                raise ContractError("Repaired renderer report lost the V1.1 variant")
            premaster = root_path(hybrid.get("files", {}).get("premaster", ""))
            rendered_vocal = root_path(hybrid.get("files", {}).get("vocal", ""))
        final_f0 = (
            independent_f0_metrics(
                variant_path(paths, validated["song_id"], variant, "dry"),
                event["events"],
                TARGET_SAMPLE_RATE,
            )
            if discovered_group_ids
            else candidate_f0
        )
        if (
            final_f0["event_median_octave_error_count"] != 0
            or final_f0["frame_octave_error_rate"] > 0.01
        ):
            raise ContractError(
                "Independent F0 safety remains failed after explicit repair: "
                f"{final_f0}"
            )
        f0_safety_evidence = {
            "candidate": candidate_f0,
            "configured_group_ids": configured_f0_safety_group_ids,
            "discovered_group_ids": discovered_group_ids,
            "selected_group_ids": safety_group_ids,
            "final": final_f0,
            "wall_seconds": round(time.perf_counter() - preflight_started, 3),
            "policy": "EXPLICIT_V1_GROUP_FALLBACK_AND_RECORD",
        }
    wrapper_performance = combine_performance(renderer_measurements)
    shutil.copy2(rendered_vocal, paths["vocal"])
    master_evidence = loudnorm_master(premaster, paths["master"], guide["frame_count"])
    if wav_info(paths["master"])["frame_count"] != guide["frame_count"]:
        raise ContractError("Mastering changed the exact timeline frame count")
    raw_report["production_contract"] = {
        "preset": f"{audio['event_preset']}+hajimi-pop-v1",
        "event_preset": str(audio["event_preset"]),
        "event_preset_sha256": str(event_contract["sha256"]),
        "mix_preset": "hajimi-pop-v1",
        "mix_preset_sha256": str(validated["mix_preset"]["sha256"]),
        "run_id": run_id,
        "recipe_sha256": validated["recipe_sha256"],
        "status": "AUDIO_RENDERED",
        "preview_only": True,
        "auto_draft_override": "EXPLICITLY_ALLOWED_FOR_PREVIEW",
        "manual_listening": "NOT_PERFORMED_BY_AUTOMATION",
        "renderer_sha256": sha256(renderer),
        "wrapper_sha256": sha256(Path(__file__)),
        "wrapper_performance": wrapper_performance,
        "f0_safety_preflight": f0_safety_evidence,
        "loudnorm": master_evidence,
        "deliverables": {
            "preview_master": {"path": root_relative(paths["master"]), "sha256": sha256(paths["master"])},
            "preview_vocal": {"path": root_relative(paths["vocal"]), "sha256": sha256(paths["vocal"])},
        },
    }
    write_json(paths["render_report"], raw_report)
    manifest = read_json(paths["manifest"])
    manifest["status"] = "AUDIO_RENDERED"
    manifest["production_evidence"] = {
        "recipe_sha256": validated["recipe_sha256"],
        "event_preset": str(audio["event_preset"]),
        "event_preset_sha256": str(event_contract["sha256"]),
        "renderer_sha256": sha256(renderer),
        "wrapper_sha256": sha256(Path(__file__)),
    }
    validate_json_schema(
        manifest, HAJIMI_ROOT / "schemas" / "song-manifest.schema.json", "song manifest"
    )
    write_json(paths["manifest"], manifest)
    return {
        "status": "AUDIO_RENDERED",
        "preview_only": True,
        "master": root_relative(paths["master"]),
        "vocal": root_relative(paths["vocal"]),
        "manual_listening": "REQUIRED_BEFORE_APPROVAL_OR_PACKAGE",
    }


def strict_decode(path: Path) -> dict[str, Any]:
    _, performance = run_command(
        ["ffmpeg", "-v", "error", "-xerror", "-threads", "2", "-i", str(path), "-f", "null", "-"]
    )
    return {"status": "PASS", "performance": performance}


GROUP_TIMELINE_KEYS = (
    "group_id", "token", "attack_start_seconds", "stable_start_seconds",
    "stable_end_seconds", "render_end_seconds", "note_event_ids",
)


def group_projection(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: group.get(key) for key in GROUP_TIMELINE_KEYS}
        for group in groups
    ]


def load_mono_float(path: Path) -> tuple[Any, int]:
    try:
        import numpy as np
        import soundfile as sf
    except ImportError as error:
        raise ContractError(
            "numpy and soundfile are required for V1.1 QA; use the RVC Python 3.10 venv"
        ) from error
    audio, sample_rate = sf.read(path, always_2d=True, dtype="float64")
    return np.mean(audio, axis=1), int(sample_rate)


def bubble_metrics(
    dry_path: Path, groups: list[dict[str, Any]], sample_rate: int
) -> dict[str, Any]:
    try:
        import numpy as np
        from scipy import signal as scipy_signal
    except ImportError as error:
        raise ContractError("numpy/scipy are required for bubble QA") from error
    audio, actual_rate = load_mono_float(dry_path)
    if actual_rate != sample_rate:
        raise ContractError(f"Bubble QA sample-rate mismatch: {actual_rate} != {sample_rate}")
    values: list[float] = []
    cvs: list[float] = []
    skipped: dict[str, int] = {}
    for group in groups:
        start = int(round((float(group["stable_start_seconds"]) + 0.025) * sample_rate))
        end = int(round((float(group["render_end_seconds"]) - 0.020) * sample_rate))
        if end - start < int(round(0.140 * sample_rate)):
            skipped["ROI_SHORTER_THAN_140MS"] = skipped.get("ROI_SHORTER_THAN_140MS", 0) + 1
            continue
        segment = np.asarray(audio[max(0, start):min(len(audio), end)], dtype=np.float64)
        if len(segment) < int(round(0.140 * sample_rate)):
            skipped["ROI_OUTSIDE_AUDIO"] = skipped.get("ROI_OUTSIDE_AUDIO", 0) + 1
            continue
        envelope = np.abs(scipy_signal.hilbert(segment))
        mean_envelope = float(np.mean(envelope))
        if mean_envelope < 1e-5:
            skipped["MEAN_ENVELOPE_BELOW_1E-5"] = skipped.get("MEAN_ENVELOPE_BELOW_1E-5", 0) + 1
            continue
        envelope_200 = scipy_signal.resample_poly(envelope, 200, sample_rate)
        frequencies, psd = scipy_signal.welch(
            envelope_200,
            fs=200,
            nperseg=min(128, len(envelope_200)),
            detrend="constant",
            scaling="density",
        )
        numerator = float(np.sum(psd[(frequencies >= 6) & (frequencies <= 30)]))
        denominator = float(np.sum(psd[(frequencies >= 1) & (frequencies <= 45)]))
        if denominator <= 1e-20:
            skipped["PSD_DENOMINATOR_ZERO"] = skipped.get("PSD_DENOMINATOR_ZERO", 0) + 1
            continue
        values.append(numerator / denominator)
        cvs.append(float(np.std(envelope_200) / max(np.mean(envelope_200), 1e-12)))
    if not values:
        raise ContractError("Bubble QA found no eligible sustain groups")
    return {
        "eligible_group_count": len(values),
        "skipped_group_count": sum(skipped.values()),
        "skipped_reasons": skipped,
        "values": [round(value, 9) for value in values],
        "median": round(float(np.median(values)), 6),
        "p90": round(float(np.percentile(values, 90)), 6),
        "envelope_cv_values": [round(value, 9) for value in cvs],
        "envelope_cv_median": round(float(np.median(cvs)), 6),
        "envelope_cv_p90": round(float(np.percentile(cvs, 90)), 6),
    }


def independent_f0_metrics(
    dry_path: Path, events: list[dict[str, Any]], sample_rate: int
) -> dict[str, Any]:
    try:
        import numpy as np
        import pyworld
    except ImportError as error:
        raise ContractError("numpy/pyworld are required for independent F0 QA") from error
    audio, actual_rate = load_mono_float(dry_path)
    if actual_rate != sample_rate:
        raise ContractError(f"F0 QA sample-rate mismatch: {actual_rate} != {sample_rate}")
    waveform = np.ascontiguousarray(audio, dtype=np.float64)
    raw_f0, times = pyworld.harvest(
        waveform, sample_rate, f0_floor=70.0, f0_ceil=900.0, frame_period=5.0
    )
    f0 = pyworld.stonemask(waveform, raw_f0, times, sample_rate)
    errors: list[float] = []
    event_median_octave_errors = 0
    event_failures: list[dict[str, Any]] = []
    covered_events = 0
    for event in events:
        start = float(event["relative_start_seconds"]) + 0.010
        end = float(event["relative_end_seconds"]) - 0.010
        if end <= start:
            start = float(event["relative_start_seconds"])
            end = float(event["relative_end_seconds"])
        mask = (times >= start) & (times < end) & (f0 > 0)
        observed = f0[mask]
        if not len(observed):
            continue
        covered_events += 1
        target = 440.0 * 2.0 ** ((int(event["quantized_midi"]) - 69.0) / 12.0)
        event_errors = 1200.0 * np.log2(observed / target)
        errors.extend(float(value) for value in event_errors)
        event_median_cents = float(np.median(event_errors))
        if abs(event_median_cents) >= 600.0:
            event_median_octave_errors += 1
            event_failures.append(
                {
                    "event_id": int(event["event_id"]),
                    "median_cents": round(event_median_cents, 6),
                }
            )
    if not errors:
        raise ContractError("Independent F0 QA found no voiced frames")
    absolute = np.abs(np.asarray(errors, dtype=np.float64))
    return {
        "backend": "pyworld_harvest_stonemask",
        "event_count": len(events),
        "covered_event_count": covered_events,
        "event_coverage_ratio": round(covered_events / max(len(events), 1), 6),
        "voiced_frame_count": len(errors),
        "median_abs_cents": round(float(np.median(absolute)), 6),
        "p90_abs_cents": round(float(np.percentile(absolute, 90)), 6),
        "event_median_octave_error_count": event_median_octave_errors,
        "event_median_octave_errors": event_failures,
        "frame_octave_error_rate": round(float(np.mean(absolute >= 600.0)), 8),
    }


def dry_spectral_metrics(path: Path, sample_rate: int) -> dict[str, Any]:
    try:
        import numpy as np
        from scipy import signal as scipy_signal
    except ImportError as error:
        raise ContractError("numpy/scipy are required for spectral QA") from error
    audio, actual_rate = load_mono_float(path)
    if actual_rate != sample_rate:
        raise ContractError(f"Spectral QA sample-rate mismatch: {actual_rate} != {sample_rate}")
    frequencies, _times, spectrum = scipy_signal.stft(
        audio, fs=sample_rate, nperseg=2048, noverlap=1536, boundary=None
    )
    magnitude = np.abs(spectrum)
    frame_energy = np.sum(magnitude, axis=0)
    valid = frame_energy > 1e-9
    centroids = np.sum(frequencies[:, None] * magnitude[:, valid], axis=0) / frame_energy[valid]
    power = magnitude * magnitude
    total = float(np.sum(power[(frequencies >= 150) & (frequencies <= 10000)]))
    presence = float(np.sum(power[(frequencies >= 4000) & (frequencies < 8000)]))
    return {
        "spectral_centroid_median_hz": round(float(np.median(centroids)), 6),
        "presence_4_8k_db_of_total": round(
            10.0 * math.log10(max(presence, 1e-20) / max(total, 1e-20)), 6
        ),
    }


def seam_metrics(
    dry_path: Path, groups: list[dict[str, Any]], sample_rate: int
) -> dict[str, Any]:
    try:
        import numpy as np
    except ImportError as error:
        raise ContractError("numpy is required for seam QA") from error
    audio, actual_rate = load_mono_float(dry_path)
    if actual_rate != sample_rate:
        raise ContractError(f"Seam QA sample-rate mismatch: {actual_rate} != {sample_rate}")
    ratios: list[float] = []
    window = int(round(0.020 * sample_rate))
    for group in groups:
        offsets = group.get("seam_offsets_samples", [])
        attack_start = float(group["attack_start_seconds"])
        stable_start = float(group["stable_start_seconds"])
        render_end = float(group["render_end_seconds"])
        attack_samples = max(1, int(round((stable_start - attack_start) * sample_rate)))
        sustain_samples = max(1, int(round((render_end - stable_start) * sample_rate)))
        join_overlap = min(int(round(0.004 * sample_rate)), attack_samples // 3, sustain_samples // 3)
        destination = int(round(attack_start * sample_rate))
        sustain_origin = destination + attack_samples - join_overlap
        for offset in offsets:
            position = sustain_origin + int(offset)
            if position <= 1 or position >= len(audio) - 1:
                raise ContractError("Recorded microcycle seam lies outside rendered dry audio")
            left = max(0, position - window)
            right = min(len(audio), position + window)
            local_diff = np.abs(np.diff(audio[left:right]))
            seam_local = position - left - 1
            keep = np.ones(len(local_diff), dtype=bool)
            keep[max(0, seam_local - 1):min(len(keep), seam_local + 2)] = False
            reference = float(np.percentile(local_diff[keep], 99)) if np.any(keep) else 0.0
            seam_step = abs(float(audio[position] - audio[position - 1]))
            ratios.append(seam_step / max(reference, 1e-6))
    return {
        "seam_count": len(ratios),
        "maximum_local_derivative_ratio": round(max(ratios, default=0.0), 6),
        "p99_local_derivative_ratio": round(float(np.percentile(ratios, 99)), 6) if ratios else 0.0,
    }


def baseline_render_performance(report: dict[str, Any]) -> dict[str, float]:
    value = report.get("performance_measurement")
    if not isinstance(value, dict):
        raise ContractError("Baseline render report has no performance_measurement")
    return {
        "wall_seconds": float(value["wall_seconds"]),
        "max_resident_set_mb": float(value["max_resident_set_mb"]),
    }


def qa(validated: dict[str, Any], run_id: str, paths: dict[str, Path]) -> dict[str, Any]:
    swap_before = system_swap_used_mb()
    swap_counters_before = system_swap_counters()
    started = time.perf_counter()
    variant = str(validated["event_contract"]["variant"])
    dry_path = variant_path(paths, validated["song_id"], variant, "dry")
    required = (
        paths["guide"], paths["instrumental"], paths["event"], paths["render_report"],
        paths["manifest"], paths["master"], paths["vocal"], dry_path,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ContractError(f"Run render first; missing: {missing}")

    decode: dict[str, Any] = {}
    for label, path in (("master", paths["master"]), ("vocal", paths["vocal"])):
        try:
            decode[label] = strict_decode(path)
        except ContractError as error:
            decode[label] = {"status": "FAIL", "error": str(error)}
    decode_pass = all(item.get("status") == "PASS" for item in decode.values())

    master: dict[str, Any] = {}
    vocal: dict[str, Any] = {}
    instrumental: dict[str, Any] = {}
    format_error: str | None = None
    try:
        master = wav_info(paths["master"])
        vocal = wav_info(paths["vocal"])
        instrumental = wav_info(paths["instrumental"])
        format_pass = all(
            [
                master["sample_rate"] == TARGET_SAMPLE_RATE,
                master["channels"] == 2,
                master["sample_width_bytes"] == 3,
                vocal["sample_rate"] == TARGET_SAMPLE_RATE,
                vocal["channels"] == 2,
                vocal["sample_width_bytes"] == 3,
                master["frame_count"] == instrumental["frame_count"],
                vocal["frame_count"] == instrumental["frame_count"],
            ]
        )
    except (ContractError, KeyError, TypeError, ValueError) as error:
        format_pass = False
        format_error = str(error)

    integrated: float | None = None
    true_peak: float | None = None
    loudness: dict[str, Any] = {}
    loudness_performance: dict[str, Any] = {}
    loudness_error: str | None = None
    loudness_range: float | None = None
    try:
        loudness, loudness_performance = loudnorm_measure(paths["master"])
        integrated = float(loudness["input_i"])
        true_peak = float(loudness["input_tp"])
        loudness_range = float(loudness["input_lra"])
        if not all(math.isfinite(value) for value in (integrated, true_peak, loudness_range)):
            raise ContractError("Loudness measurement is not finite")
        loudness_pass = -10.0 <= integrated <= -9.0 and true_peak <= -1.0 + 1e-6
    except (ContractError, KeyError, TypeError, ValueError) as error:
        loudness_pass = False
        loudness_error = str(error)
        integrated = None
        true_peak = None
        loudness_range = None

    draft_event_ids: list[int] = []
    rendered_ids: list[int] = []
    groups: list[dict[str, Any]] = []
    coverage_error: str | None = None
    try:
        event = read_json(paths["event"])
        render_report = read_json(paths["render_report"])
        hybrid = render_report.get("variants", {}).get(variant, {})
        if not isinstance(hybrid, dict):
            raise ContractError(f"render-report.json has no {variant} variant")
        draft_event_ids = [int(item["event_id"]) for item in event.get("events", [])]
        groups_value = hybrid.get("groups", [])
        if not isinstance(groups_value, list) or not all(
            isinstance(group, dict) for group in groups_value
        ):
            raise ContractError("Hybrid groups must be an array of objects")
        groups = groups_value
        rendered_ids = [
            int(event_id)
            for group in groups
            for event_id in group.get("note_event_ids", [])
        ]
        coverage_pass = (
            bool(draft_event_ids)
            and draft_event_ids == list(range(1, len(draft_event_ids) + 1))
            and sorted(rendered_ids) == draft_event_ids
            and len(rendered_ids) == len(set(rendered_ids))
            and int(hybrid.get("note_event_count", -1)) == len(draft_event_ids)
            and int(hybrid.get("syllable_group_count", -1)) == len(groups)
        )
    except (ContractError, KeyError, TypeError, ValueError) as error:
        coverage_pass = False
        coverage_error = str(error)

    checks = {
        "strict_decode": {"status": "PASS" if decode_pass else "FAIL", "files": decode},
        "format_and_timeline": {
            "status": "PASS" if format_pass else "FAIL",
            "master": master,
            "vocal": vocal,
            "expected_frame_count": instrumental.get("frame_count"),
            "error": format_error,
        },
        "loudness": {
            "status": "PASS" if loudness_pass else "FAIL",
            "integrated_lufs": integrated,
            "loudness_range_lu": loudness_range,
            "true_peak_dbtp": true_peak,
            "target": {"integrated_lufs": TARGET_I, "true_peak_dbtp_max": TARGET_TP},
            "performance": loudness_performance,
            "error": loudness_error,
        },
        "event_group_coverage": {
            "status": "PASS" if coverage_pass else "FAIL",
            "draft_event_count": len(draft_event_ids),
            "rendered_event_references": len(rendered_ids),
            "unique_rendered_events": len(set(rendered_ids)),
            "syllable_group_count": len(groups),
            "error": coverage_error,
        },
    }
    if validated["audio"]["event_preset"] == "hajimi-note-event-v1.1":
        regression_detail: dict[str, Any] = {}
        regression_error: str | None = None
        try:
            preset = validated["event_contract"]["value"]
            qa_contract = preset["qa"]
            baseline_dry = validated["paths"]["baseline_dry"]
            baseline_report_path = validated["paths"]["baseline_render_report"]
            baseline_report = read_json(baseline_report_path)
            baseline_variant = baseline_report.get("variants", {}).get("hybrid")
            if not isinstance(baseline_variant, dict):
                raise ContractError("V1 baseline render report has no hybrid variant")
            baseline_groups = baseline_variant.get("groups")
            if not isinstance(baseline_groups, list):
                raise ContractError("V1 baseline groups are missing")
            timeline_pass = group_projection(groups) == group_projection(baseline_groups)
            if not timeline_pass:
                raise ContractError("V1.1 group timeline differs from frozen V1 baseline")
            baseline_info = wav_info(baseline_dry)
            current_info = wav_info(dry_path)
            if (
                current_info["sample_rate"] != baseline_info["sample_rate"]
                or current_info["frame_count"] != baseline_info["frame_count"]
            ):
                raise ContractError("V1.1 dry sample contract differs from V1 baseline")

            current_bubble_all = bubble_metrics(dry_path, groups, TARGET_SAMPLE_RATE)
            baseline_bubble_all = bubble_metrics(baseline_dry, baseline_groups, TARGET_SAMPLE_RATE)
            applied_group_ids = {
                int(group["group_id"])
                for group in groups
                if group.get("method") == "phase_locked_microcycle_8p_2p"
            }
            if not applied_group_ids:
                raise ContractError("V1.1 regression has no applied microcycle groups")
            current_applied_groups = [
                group for group in groups if int(group["group_id"]) in applied_group_ids
            ]
            baseline_applied_groups = [
                group for group in baseline_groups if int(group["group_id"]) in applied_group_ids
            ]
            current_bubble = bubble_metrics(dry_path, current_applied_groups, TARGET_SAMPLE_RATE)
            baseline_bubble = bubble_metrics(
                baseline_dry, baseline_applied_groups, TARGET_SAMPLE_RATE
            )
            relative_reduction = 1.0 - (
                current_bubble["median"] / max(baseline_bubble["median"], 1e-12)
            )
            bubble_contract = qa_contract["bubble_proxy"]
            bubble_pass = all(
                [
                    relative_reduction >= float(bubble_contract["per_song_relative_median_reduction_min"]),
                    current_bubble["median"] <= float(bubble_contract["per_song_median_max"]),
                    current_bubble["p90"] <= float(bubble_contract["per_song_p90_max"]),
                    current_bubble["envelope_cv_median"] <= float(qa_contract["envelope_cv"]["median_max"]),
                    current_bubble["envelope_cv_p90"] <= float(qa_contract["envelope_cv"]["p90_max"]),
                ]
            )

            event_value = read_json(paths["event"])
            events = event_value.get("events", [])
            if not isinstance(events, list) or not events:
                raise ContractError("Event draft has no events for F0 QA")
            current_f0 = independent_f0_metrics(dry_path, events, TARGET_SAMPLE_RATE)
            baseline_f0 = independent_f0_metrics(baseline_dry, events, TARGET_SAMPLE_RATE)
            f0_contract = qa_contract["independent_f0"]
            f0_pass = all(
                [
                    current_f0["event_coverage_ratio"] == 1.0,
                    current_f0["median_abs_cents"] <= float(f0_contract["median_abs_cents_max"]),
                    current_f0["p90_abs_cents"] <= float(f0_contract["p90_abs_cents_max"]),
                    current_f0["event_median_octave_error_count"] <= int(f0_contract["event_median_octave_error_count_max"]),
                    current_f0["frame_octave_error_rate"] <= float(f0_contract["frame_octave_error_rate_max"]),
                    current_f0["p90_abs_cents"] <= baseline_f0["p90_abs_cents"] + float(f0_contract["p90_regression_vs_v1_cents_max"]),
                ]
            )

            current_spectral = dry_spectral_metrics(dry_path, TARGET_SAMPLE_RATE)
            baseline_spectral = dry_spectral_metrics(baseline_dry, TARGET_SAMPLE_RATE)
            centroid_delta = (
                current_spectral["spectral_centroid_median_hz"]
                / max(baseline_spectral["spectral_centroid_median_hz"], 1e-12)
                - 1.0
            )
            presence_delta = (
                current_spectral["presence_4_8k_db_of_total"]
                - baseline_spectral["presence_4_8k_db_of_total"]
            )
            spectral_pass = all(
                [
                    abs(centroid_delta) <= float(qa_contract["spectral"]["centroid_relative_delta_max"]),
                    abs(presence_delta) <= float(qa_contract["spectral"]["presence_4_8k_delta_db_max"]),
                ]
            )

            seams = seam_metrics(dry_path, groups, TARGET_SAMPLE_RATE)
            seam_pass = seams["maximum_local_derivative_ratio"] <= float(
                qa_contract["seam_local_derivative_ratio_max"]
            )
            current_performance = baseline_render_performance(render_report)
            baseline_performance = baseline_render_performance(baseline_report)
            resource_contract = preset["resources"]
            wrapper_performance = render_report.get("production_contract", {}).get(
                "wrapper_performance", {}
            )
            swapout_pages = (
                wrapper_performance.get("system_swap_counter_delta", {}) or {}
            ).get("swapouts_pages")
            performance_pass = all(
                [
                    current_performance["wall_seconds"] <= float(resource_contract["render_mix_wall_seconds_absolute_max"]),
                    current_performance["wall_seconds"] <= baseline_performance["wall_seconds"] * float(resource_contract["render_mix_wall_ratio_vs_v1_max"]),
                    current_performance["max_resident_set_mb"] < float(resource_contract["render_mix_rss_mb_absolute_max"]),
                    current_performance["max_resident_set_mb"] <= baseline_performance["max_resident_set_mb"] * float(resource_contract["render_mix_rss_ratio_vs_v1_max"]),
                    swapout_pages == int(resource_contract["swapout_pages_max"]),
                ]
            )
            regression_pass = all(
                [timeline_pass, bubble_pass, f0_pass, spectral_pass, seam_pass, performance_pass]
            )
            regression_detail = {
                "status": "PASS" if regression_pass else "FAIL",
                "timeline_identity": "PASS" if timeline_pass else "FAIL",
                "baseline": {
                    "dry": {**baseline_info, "expected_sha256": validated["recipe"]["regression"]["baseline_dry_sha256"]},
                    "render_report": {"path": root_relative(baseline_report_path), "sha256": sha256(baseline_report_path)},
                    "bubble": baseline_bubble,
                    "bubble_all_long_groups_advisory": baseline_bubble_all,
                    "f0": baseline_f0,
                    "spectral": baseline_spectral,
                    "performance": baseline_performance,
                },
                "candidate": {
                    "dry": current_info,
                    "bubble": current_bubble,
                    "bubble_all_long_groups_advisory": current_bubble_all,
                    "bubble_gate_scope": "MICROCYCLE_APPLIED_GROUPS_ONLY",
                    "microcycle_applied_group_ids": sorted(applied_group_ids),
                    "bubble_relative_median_reduction": round(relative_reduction, 6),
                    "f0": current_f0,
                    "spectral": current_spectral,
                    "spectral_centroid_relative_delta": round(centroid_delta, 6),
                    "presence_4_8k_delta_db": round(presence_delta, 6),
                    "seams": seams,
                    "performance": current_performance,
                    "wrapper_swapout_pages": swapout_pages,
                },
                "gates": {
                    "bubble": "PASS" if bubble_pass else "FAIL",
                    "independent_f0": "PASS" if f0_pass else "FAIL",
                    "spectral": "PASS" if spectral_pass else "FAIL",
                    "seams": "PASS" if seam_pass else "FAIL",
                    "performance": "PASS" if performance_pass else "FAIL",
                },
                "cross_song_combined_gate": "PENDING_UNTIL_THREE_SONG_REGRESSION",
                "too_straight_warning": (
                    current_bubble["envelope_cv_median"]
                    < float(qa_contract["envelope_cv"]["too_straight_warn_below"])
                ),
            }
        except (ContractError, KeyError, TypeError, ValueError, ZeroDivisionError) as error:
            regression_detail = {"status": "FAIL", "error": str(error)}
            regression_error = str(error)
        checks["v1p1_regression"] = regression_detail
    overall = "PASS" if all(check["status"] == "PASS" for check in checks.values()) else "FAIL"
    usage = resource.getrusage(resource.RUSAGE_SELF)
    children_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    max_rss_bytes = int(usage.ru_maxrss if sys.platform == "darwin" else usage.ru_maxrss * 1024)
    children_max_rss_bytes = int(
        children_usage.ru_maxrss if sys.platform == "darwin" else children_usage.ru_maxrss * 1024
    )
    swap_after = system_swap_used_mb()
    swap_counters_after = system_swap_counters()
    report = {
        "schema_version": 1,
        "artifact_kind": "hajimi-audio-machine-qa",
        "song_id": validated["song_id"],
        "run_id": run_id,
        "event_preset": validated["audio"]["event_preset"],
        "event_preset_sha256": validated["event_contract"]["sha256"],
        "recipe_sha256": validated["recipe_sha256"],
        "overall_status": overall,
        "machine_status": "AUDIO_AUTO_QA_PASS" if overall == "PASS" else "AUDIO_AUTO_QA_FAIL",
        "preview_only": True,
        "manual_listening": "NOT_PERFORMED_BY_AUTOMATION",
        "human_approval": "PENDING",
        "checks": checks,
        "artifacts": {
            "event_draft": {"path": root_relative(paths["event"]), "sha256": sha256(paths["event"])},
            "render_report": {"path": root_relative(paths["render_report"]), "sha256": sha256(paths["render_report"])},
            "preview_master": {"path": root_relative(paths["master"]), "sha256": sha256(paths["master"])},
            "preview_vocal": {"path": root_relative(paths["vocal"]), "sha256": sha256(paths["vocal"])},
            "dry": {"path": root_relative(dry_path), "sha256": sha256(dry_path)},
        },
        "performance": {
            "wall_seconds": round(time.perf_counter() - started, 3),
            "max_resident_set_mb": round(max_rss_bytes / 1024.0 / 1024.0, 2),
            "children_high_water_rss_mb": round(children_max_rss_bytes / 1024.0 / 1024.0, 2),
            "thread_limit": 2,
            "serial_lock": True,
            "system_swap_used_mb_before": swap_before,
            "system_swap_used_mb_after": swap_after,
            "system_swap_delta_mb": (
                round(swap_after - swap_before, 2)
                if swap_before is not None and swap_after is not None
                else None
            ),
            "system_swap_counters_before": swap_counters_before,
            "system_swap_counters_after": swap_counters_after,
            "system_swap_counter_delta": (
                {
                    key: max(0, swap_counters_after[key] - swap_counters_before[key])
                    for key in swap_counters_before
                }
                if swap_counters_before is not None and swap_counters_after is not None
                else None
            ),
        },
    }
    write_json(paths["qa_report"], report)
    manifest = read_json(paths["manifest"])
    manifest["status"] = "AUDIO_AUTO_QA_PASS" if overall == "PASS" else "AUDIO_RENDERED"
    validate_json_schema(
        manifest, HAJIMI_ROOT / "schemas" / "song-manifest.schema.json", "song manifest"
    )
    write_json(paths["manifest"], manifest)
    if overall != "PASS":
        raise ContractError(f"Audio QA failed; see {paths['qa_report']}")
    return {
        "status": "AUDIO_AUTO_QA_PASS",
        "preview_only": True,
        "qa_report": root_relative(paths["qa_report"]),
        "manual_listening": "REQUIRED_BEFORE_APPROVAL_OR_PACKAGE",
    }


def package(paths: dict[str, Path]) -> dict[str, Any]:
    if not paths["manifest"].is_file():
        raise ContractError("No materialized manifest; run draft/render/qa first")
    status = read_json(paths["manifest"]).get("status")
    if status != "AUDIO_HUMAN_APPROVED":
        raise ContractError(
            f"Package refused: manifest status is {status!r}; AUDIO_HUMAN_APPROVED is required"
        )
    raise ContractError("Packaging is intentionally not implemented in the V1 preview CLI")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate, draft, render and QA one Hajimi Note/Event V1/V1.1 song at a time."
    )
    parser.add_argument("action", choices=("validate", "draft", "render", "qa", "package"))
    parser.add_argument("song", help="Song slug or recipe JSON path")
    parser.add_argument(
        "--run-id",
        help="Explicit version-matched run id; defaults to today's date for validate/draft/qa only.",
    )
    parser.add_argument(
        "--allow-auto-draft-preview",
        action="store_true",
        help="Explicitly permit render from an auto draft; never changes human approval status.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace incomplete outputs within the selected run id (draft only).",
    )
    return parser


def main() -> int:
    parser = make_parser()
    args = parser.parse_args()
    try:
        recipe_path = resolve_recipe(args.song)
        validated = validate_recipe(recipe_path)
        if args.action in {"render", "package"} and not args.run_id:
            raise ContractError(f"{args.action} requires an explicit --run-id")
        run_id = run_id_for(
            validated["song_id"], validated["event_contract"], args.run_id
        )
        paths = output_paths(validated["song_id"], run_id)
        if args.force and args.action != "draft":
            raise ContractError("--force is only valid with the draft action")
        if args.allow_auto_draft_preview and args.action != "render":
            raise ContractError("--allow-auto-draft-preview is only valid with the render action")
        if args.action == "validate":
            result = {
                "status": "WINDOW_APPROVED",
                "recipe": root_relative(recipe_path),
                "recipe_sha256": validated["recipe_sha256"],
                "song_id": validated["song_id"],
                "run_id": run_id,
                "window": validated["window"],
                "source": validated["source"],
                "performance_policy": {"crop_first": True, "threads": 2, "serial": True},
            }
        else:
            with audio_lock():
                if args.action == "draft":
                    result = draft(validated, run_id, paths, args.force)
                elif args.action == "render":
                    result = render(validated, run_id, paths, args.allow_auto_draft_preview)
                elif args.action == "qa":
                    result = qa(validated, run_id, paths)
                else:
                    result = package(paths)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ContractError as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
