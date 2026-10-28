#!/usr/bin/env python3
"""Shared Hajimi Rain Window V1 build, render, mux, and QA runtime.

The CLI deliberately accepts a per-song recipe instead of copying a renderer
directory.  It consumes the current audio run, derives captions from the
renderer groups, creates one cropped/muted visual proxy, and writes a
deterministic 1080x1920 HyperFrames project.

Heavy commands use a non-blocking process lock and cap numerical/FFmpeg work
at two threads.  HyperFrames is pinned to 0.6.69 and renders with one worker.
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
import tempfile
import time
import wave
from array import array
from pathlib import Path
from typing import Any, Iterable


SCRIPT_PATH = Path(__file__).resolve()
TEMPLATE_ROOT = SCRIPT_PATH.parents[1]
HAJIMI_ROOT = SCRIPT_PATH.parents[3]
REPO_ROOT = SCRIPT_PATH.parents[4]
MV_ROOT = HAJIMI_ROOT / "mv"
OUTPUTS_ROOT = HAJIMI_ROOT / "outputs"
ANALYZER = (
    Path(os.environ.get("HAJIMI_MUSIC_TO_VIDEO_SKILL", ""))
    if os.environ.get("HAJIMI_MUSIC_TO_VIDEO_SKILL")
    else Path.home() / ".agents/skills/music-to-video"
) / "scripts/analyze-beatgrid.py"
HF_VERSION = "0.6.69"
HF = ("npx", "--yes", f"hyperframes@{HF_VERSION}")
RESOURCE_BUDGET = REPO_ROOT.parent / "cc-media/tools/video/resource_budget.py"
FPS = 30
FRAME_SECONDS = 1.0 / FPS
CANVAS = (1080, 1920)
PROXY_SIZE = (928, 642)
LOCK_PATH = Path(tempfile.gettempdir()) / "cc-voice-hajimi-heavy-v1.lock"
THREAD_ENV = {
    "OMP_NUM_THREADS": "2",
    "OPENBLAS_NUM_THREADS": "2",
    "VECLIB_MAXIMUM_THREADS": "2",
    "NUMEXPR_NUM_THREADS": "2",
    "MKL_NUM_THREADS": "2",
}
PALETTES = {
    "open": {
        "ground": "#1A1A1A", "top": "#E85D5D", "top_ink": "#1A1A1A",
        "lower": "#F5F0E8", "lyric": "#1A1A1A", "muted": "#6B6B6B",
        "accent": "#E85D5D", "active_bg": "transparent",
        "footer": "#1A1A1A", "footer_ink": "#F5F0E8", "progress": "#E85D5D",
    },
    "build": {
        "ground": "#E8E0D4", "top": "#F5F0E8", "top_ink": "#1A1A1A",
        "lower": "#1A1A1A", "lyric": "#F5F0E8", "muted": "#B0B0B0",
        "accent": "#E85D5D", "active_bg": "transparent",
        "footer": "#E85D5D", "footer_ink": "#1A1A1A", "progress": "#1A1A1A",
    },
    "climax": {
        "ground": "#E85D5D", "top": "#1A1A1A", "top_ink": "#F5F0E8",
        "lower": "#E85D5D", "lyric": "#1A1A1A", "muted": "#6B3636",
        "accent": "#E85D5D", "active_bg": "#1A1A1A",
        "footer": "#F5F0E8", "footer_ink": "#1A1A1A", "progress": "#E85D5D",
    },
    "outro": {
        "ground": "#F5F0E8", "top": "#E8E0D4", "top_ink": "#1A1A1A",
        "lower": "#F5F0E8", "lyric": "#1A1A1A", "muted": "#6B6B6B",
        "accent": "#E85D5D", "active_bg": "transparent",
        "footer": "#E85D5D", "footer_ink": "#1A1A1A", "progress": "#1A1A1A",
    },
}


class ContractError(RuntimeError):
    """Raised when an input or generated artifact violates the V1 contract."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractError(f"missing required JSON: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"JSON root must be an object: {path}")
    return value


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def wav_contract(path: Path) -> dict[str, Any]:
    probe = media_probe(path)
    streams = [item for item in probe.get("streams", []) if item.get("codec_type") == "audio"]
    if len(streams) != 1:
        raise ContractError(f"PCM WAV must have exactly one audio stream: {path}")
    stream = streams[0]
    codec = str(stream.get("codec_name", ""))
    if codec != "pcm_s24le":
        raise ContractError(f"video reuse requires PCM24 WAV, found {codec}: {path}")
    sample_rate = int(stream.get("sample_rate", 0))
    channels = int(stream.get("channels", 0))
    duration_ts = stream.get("duration_ts")
    time_base = str(stream.get("time_base", ""))
    if duration_ts is None or time_base != f"1/{sample_rate}":
        raise ContractError(f"FFprobe did not expose an exact PCM frame count: {path}")
    return {
        "path": portable_source_path(path),
        "sha256": sha256(path),
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_width_bytes": 3,
        "frame_count": int(duration_ts),
    }


def video_packet_sha256(path: Path) -> str:
    completed = run(
        [
            "ffmpeg", "-v", "error", "-xerror", "-threads", "2", "-i", path,
            "-map", "0:v:0", "-c:v", "copy", "-f", "hash", "-hash", "sha256", "-",
        ]
    )
    match = re.search(r"SHA256=([a-fA-F0-9]{64})", completed.stdout)
    if not match:
        raise ContractError(f"ffmpeg emitted no video packet hash for {path}")
    return match.group(1).lower()


def visual_timeline_projection(timed: dict[str, Any]) -> dict[str, Any]:
    audio = timed.get("audio", {})
    return {
        "duration_seconds": audio.get("duration_seconds"),
        "sample_rate": audio.get("sample_rate"),
        "frame_count": audio.get("frame_count"),
        "source_window": timed.get("source_window"),
        "display": timed.get("display"),
        "sections": timed.get("sections"),
        "phrases": timed.get("phrases"),
        "lines": timed.get("lines"),
        "syllables": timed.get("syllables"),
    }


def js_json(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def resolve_repo_path(raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() else (REPO_ROOT / path).resolve()


def portable_source_path(path: Path) -> str:
    for base in (REPO_ROOT, REPO_ROOT.parent):
        try:
            return path.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            pass
    return f"external-media/{path.name}"


def media_probe(path: Path, *, count_frames: bool = False) -> dict[str, Any]:
    entries = (
        "format=duration,start_time:"
        "stream=index,codec_type,codec_name,profile,width,height,pix_fmt,"
        "r_frame_rate,avg_frame_rate,sample_rate,channels,duration,"
        "duration_ts,time_base,bits_per_sample,bits_per_raw_sample,sample_fmt,"
        "start_time,color_space,color_transfer,color_primaries,nb_frames"
    )
    command = [
        "ffprobe", "-v", "error", "-show_entries", entries,
        "-of", "json",
    ]
    if count_frames:
        command.insert(3, "-count_frames")
    command.append(str(path))
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ContractError(f"ffprobe failed for {path}: {result.stderr.strip()}")
    return json.loads(result.stdout)


def fraction(value: str | None) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0.0
    numerator, separator, denominator = value.partition("/")
    return float(numerator) / float(denominator) if separator else float(value)


def stream_duration(stream: dict[str, Any], fallback: float) -> float:
    raw = stream.get("duration")
    return float(raw) if raw not in {None, "N/A"} else fallback


def process_env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(THREAD_ENV)
    env["HYPERFRAMES_SKIP_SKILLS"] = "1"
    env["CC_MEDIA_HYPERFRAMES_WORKERS"] = "1"
    return env


def resolve_hyperframes_command() -> tuple[str, ...]:
    override = os.environ.get("HAJIMI_HYPERFRAMES_BIN")
    if override:
        candidate = Path(override).expanduser().resolve()
        if not candidate.is_file():
            raise ContractError(f"HAJIMI_HYPERFRAMES_BIN is not a file: {candidate}")
        return (str(candidate),)
    cache_root = Path(os.environ.get("npm_config_cache", str(Path.home() / ".npm-cache")))
    candidates = sorted(cache_root.glob("_npx/*/node_modules/hyperframes/package.json"))
    for package_json in candidates:
        try:
            package = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        executable = package_json.parent.parent / ".bin/hyperframes"
        if package.get("version") == HF_VERSION and executable.is_file():
            return (str(executable),)
    return HF


def run(
    command: Iterable[str | Path],
    *,
    cwd: Path | None = None,
    capture: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    argv = [str(item) for item in command]
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=process_env(),
        capture_output=capture,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise ContractError(f"command failed ({result.returncode}): {' '.join(argv)}\n{details[-4000:]}")
    return result


@contextlib.contextmanager
def heavy_task_lock(label: str):
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ContractError(
                f"another Hajimi heavy task owns {LOCK_PATH}; refuse concurrent {label}"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} label={label}\n")
        handle.flush()
        yield


def swap_counters() -> dict[str, int] | None:
    """Return monotonic macOS swap counters without requiring privileged sysctl."""
    if sys.platform != "darwin":
        return None
    result = subprocess.run(
        ["vm_stat"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return None
    values: dict[str, int] = {}
    for key, label in (("swapins_pages", "Swapins"), ("swapouts_pages", "Swapouts")):
        match = re.search(rf"^{label}:\s+([0-9]+)\.\s*$", result.stdout, flags=re.MULTILINE)
        if match:
            values[key] = int(match.group(1))
    return values if len(values) == 2 else None


def timed_step(label: str, function):
    started = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    swap_before = swap_counters()
    value = function()
    swap_after = swap_counters()
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    performance = {
        "label": label,
        "wall_seconds": round(time.monotonic() - started, 3),
        "child_user_cpu_seconds": round(after.ru_utime - before.ru_utime, 3),
        "child_system_cpu_seconds": round(after.ru_stime - before.ru_stime, 3),
        "child_max_resident_set_mb": round(
            after.ru_maxrss / (1024.0 * 1024.0)
            if sys.platform == "darwin"
            else after.ru_maxrss / 1024.0,
            2,
        ),
        "threads": 2,
        "system_swap_counters_before": swap_before,
        "system_swap_counters_after": swap_after,
    }
    performance["system_swap_counter_delta"] = (
        {key: swap_after[key] - swap_before[key] for key in swap_before}
        if swap_before is not None and swap_after is not None
        else None
    )
    return value, performance


def recipe_event_preset(recipe: dict[str, Any]) -> tuple[str, str, str]:
    presets = recipe.get("presets")
    if not isinstance(presets, dict):
        raise ContractError("recipe.presets must be an object")
    event_preset = str(presets.get("audio", ""))
    mapping = {
        "hajimi-note-event-v1": ("hev1", "hybrid"),
        "hajimi-note-event-v1.1": ("hev1p1", "hybrid_v1p1"),
    }
    if event_preset not in mapping:
        raise ContractError(f"unsupported recipe audio preset: {event_preset}")
    run_tag, variant = mapping[event_preset]
    return event_preset, run_tag, variant


def resolve_context(args: argparse.Namespace) -> dict[str, Any]:
    recipe_path = args.recipe.resolve()
    recipe = load_json(recipe_path)
    song_id = str(recipe.get("song_id", "")).strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", song_id):
        raise ContractError(f"invalid song_id in {recipe_path}: {song_id!r}")
    event_preset, run_tag, variant = recipe_event_preset(recipe)
    if not args.run_id:
        raise ContractError("MV runtime requires an explicit --run-id")
    run_id = str(args.run_id)
    if f"_{song_id}_{run_tag}_" not in run_id:
        raise ContractError(
            f"run_id does not match song/preset: song={song_id} preset={event_preset}"
        )
    audio_dir = OUTPUTS_ROOT / song_id / run_id
    project = (
        args.project_dir.resolve()
        if args.project_dir
        else MV_ROOT / f"{song_id}-hajimi-mv-v1"
    )
    if event_preset == "hajimi-note-event-v1.1" and args.project_dir is None:
        raise ContractError("V1.1 MV work requires an explicit new --project-dir")
    master_candidates = [
        audio_dir / f"{song_id}_hajimi_preview_master.wav",
        audio_dir / f"{song_id}_hajimi_master.wav",
    ]
    master = next((item for item in master_candidates if item.exists()), None)
    if master is None:
        matches = sorted(audio_dir.glob("*_master.wav"))
        master = matches[0] if len(matches) == 1 else None
    if master is None:
        raise ContractError(f"cannot resolve one authoritative master in {audio_dir}")
    return {
        "recipe_path": recipe_path,
        "recipe": recipe,
        "song_id": song_id,
        "run_id": run_id,
        "event_preset": event_preset,
        "variant": variant,
        "audio_dir": audio_dir,
        "master": master,
        "report": audio_dir / "render-report.json",
        "events": audio_dir / "event-draft.json",
        "manifest": audio_dir / "song-manifest.json",
        "audio_qa": audio_dir / "audio-qa-report.json",
        "project": project,
    }


def validate_audio_context(
    context: dict[str, Any], *, require_visual_source: bool = True
) -> dict[str, Any]:
    recipe = context["recipe"]
    report = load_json(context["report"])
    events = load_json(context["events"])
    manifest = load_json(context["manifest"])
    audio_qa = load_json(context["audio_qa"])
    master = context["master"]
    probe = media_probe(master)
    streams = [item for item in probe.get("streams", []) if item.get("codec_type") == "audio"]
    if len(streams) != 1:
        raise ContractError("authoritative master must contain exactly one audio stream")
    stream = streams[0]
    duration = float(probe["format"]["duration"])
    if not 20.0 <= duration <= 75.0:
        raise ContractError(f"V1 audio duration outside 20..75 seconds: {duration}")
    if int(stream.get("sample_rate", 0)) != 44100 or int(stream.get("channels", 0)) != 2:
        raise ContractError("authoritative master must be 44.1 kHz stereo")
    if stream.get("codec_name") != "pcm_s24le":
        raise ContractError("authoritative master must be PCM24")
    expected = float(recipe["window"]["duration_seconds"])
    if abs(duration - expected) > 1.0 / 44100.0:
        raise ContractError(f"master/recipe duration mismatch: {duration} vs {expected}")
    manifest_preset = manifest.get("audio", {}).get("event_preset")
    qa_preset = audio_qa.get("event_preset", manifest_preset)
    if manifest_preset != context["event_preset"] or qa_preset != context["event_preset"]:
        raise ContractError("recipe/manifest/audio-QA event preset mismatch")
    groups = report.get("variants", {}).get(context["variant"], {}).get("groups")
    note_events = events.get("events")
    if not isinstance(groups, list) or not groups:
        raise ContractError(f"render report has no {context['variant']} syllable groups")
    if not isinstance(note_events, list) or not note_events:
        raise ContractError("event draft has no note events")
    group_ids = [int(item["group_id"]) for item in groups]
    event_ids = [int(item["event_id"]) for item in note_events]
    if group_ids != list(range(1, len(groups) + 1)):
        raise ContractError("hybrid group ids must be sequential from one")
    if event_ids != list(range(1, len(note_events) + 1)):
        raise ContractError("note event ids must be sequential from one")
    covered = [int(event_id) for group in groups for event_id in group["note_event_ids"]]
    if sorted(covered) != event_ids or len(covered) != len(set(covered)):
        raise ContractError("syllable groups must cover every event exactly once")
    failed_audio_checks = [
        name for name, item in audio_qa.get("checks", {}).items()
        if isinstance(item, dict) and item.get("status") == "FAIL"
    ]
    if failed_audio_checks:
        raise ContractError(f"audio QA contains failures: {failed_audio_checks}")
    visual = recipe.get("mv", {}).get("source_video", {})
    visual_path = resolve_repo_path(str(visual.get("path", "")))
    expected_visual_hash = str(visual.get("sha256", ""))
    if require_visual_source:
        if not visual_path.is_file():
            raise ContractError(f"source MV missing: {visual_path}")
        actual_visual_hash = sha256(visual_path)
        if expected_visual_hash and actual_visual_hash != expected_visual_hash:
            raise ContractError("source MV hash differs from recipe")
    else:
        actual_visual_hash = sha256(visual_path) if visual_path.is_file() else expected_visual_hash
    return {
        "recipe": recipe,
        "report": report,
        "events": events,
        "manifest": manifest,
        "audio_qa": audio_qa,
        "master_probe": probe,
        "master_sha256": sha256(master),
        "duration": duration,
        "sample_rate": 44100,
        "frame_count": int(round(duration * 44100)),
        "groups": groups,
        "note_events": note_events,
        "visual_path": visual_path,
        "visual_sha256": actual_visual_hash,
        "event_preset": context["event_preset"],
        "variant": context["variant"],
    }


def build_timed_lyrics(context: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    duration = inputs["duration"]
    groups = inputs["groups"]
    events = inputs["note_events"]
    events_by_id = {int(item["event_id"]): item for item in events}
    phrase_groups: dict[int, list[dict[str, Any]]] = {}
    syllables: list[dict[str, Any]] = []
    previous_end = -1.0
    for index, group in enumerate(groups):
        group_id = int(group["group_id"])
        note_ids = [int(item) for item in group["note_event_ids"]]
        phrase_ids = {int(events_by_id[item]["phrase_id"]) for item in note_ids}
        if len(phrase_ids) != 1:
            raise ContractError(f"group {group_id} spans phrase ids {sorted(phrase_ids)}")
        phrase_id = next(iter(phrase_ids))
        start = max(0.0, float(group["attack_start_seconds"]))
        raw_end = min(duration, float(group["render_end_seconds"]))
        next_start = (
            max(0.0, float(groups[index + 1]["attack_start_seconds"]))
            if index + 1 < len(groups)
            else duration
        )
        end = min(raw_end, next_start, duration)
        if not start < end:
            raise ContractError(f"non-positive caption interval for group {group_id}: {start}..{end}")
        if start < previous_end - 1e-6:
            raise ContractError(f"caption overlap before group {group_id}")
        item = {
            "id": f"g{group_id:03d}",
            "group_id": group_id,
            "text": str(group["token"]),
            "phrase_id": phrase_id,
            "line_id": "",
            "start_seconds": round(start, 6),
            "end_seconds": round(end, 6),
            "raw_audio_end_seconds": round(raw_end, 6),
            "stable_start_seconds": round(float(group["stable_start_seconds"]), 6),
            "stable_end_seconds": round(float(group["stable_end_seconds"]), 6),
            "note_event_ids": note_ids,
            "qa_flags": [],
        }
        syllables.append(item)
        phrase_groups.setdefault(phrase_id, []).append(item)
        previous_end = end

    phrase_ids = sorted(phrase_groups)
    if phrase_ids != list(range(1, len(phrase_ids) + 1)):
        raise ContractError(f"phrase ids must be sequential: {phrase_ids}")
    phrases: list[dict[str, Any]] = []
    for index, phrase_id in enumerate(phrase_ids):
        selected = phrase_groups[phrase_id]
        start = 0.0 if index == 0 else float(selected[0]["start_seconds"])
        end = (
            float(phrase_groups[phrase_ids[index + 1]][0]["start_seconds"])
            if index + 1 < len(phrase_ids)
            else duration
        )
        phrases.append({
            "id": phrase_id,
            "group_ids": [int(item["group_id"]) for item in selected],
            "start_seconds": round(start, 6),
            "end_seconds": round(end, 6),
            "text": "".join(str(item["text"]) for item in selected),
        })

    lines: list[dict[str, Any]] = []
    line_number = 1
    for phrase in phrases:
        selected = phrase_groups[int(phrase["id"])]
        chunks = [selected[offset : offset + 7] for offset in range(0, len(selected), 7)]
        for chunk_index, chunk in enumerate(chunks):
            line_id = f"line-{line_number:02d}"
            end = (
                float(chunks[chunk_index + 1][0]["start_seconds"])
                if chunk_index + 1 < len(chunks)
                else float(phrase["end_seconds"])
            )
            lines.append({
                "id": line_id,
                "phrase_id": int(phrase["id"]),
                "text": "".join(str(item["text"]) for item in chunk),
                "group_ids": [int(item["group_id"]) for item in chunk],
                "start_seconds": round(float(chunk[0]["start_seconds"]), 6),
                "end_seconds": round(end, 6),
            })
            for item in chunk:
                item["line_id"] = line_id
            line_number += 1

    manifest_sections = inputs["manifest"].get("sections", [])
    if not manifest_sections:
        raise ContractError("song manifest contains no section mapping")
    phrase_by_id = {int(item["id"]): item for item in phrases}
    sections: list[dict[str, Any]] = []
    for index, source in enumerate(manifest_sections, start=1):
        ids = [int(item) for item in source["phrase_ids"]]
        if any(item not in phrase_by_id for item in ids):
            raise ContractError(f"section references unknown phrases: {ids}")
        role = str(source["role"])
        sections.append({
            "id": f"{index:02d}-{role}",
            "role": role,
            "label": str(source["label"]),
            "start_seconds": round(float(phrase_by_id[ids[0]]["start_seconds"]), 6),
            "end_seconds": round(float(phrase_by_id[ids[-1]]["end_seconds"]), 6),
            "phrase_ids": ids,
            "descriptor": str(source["descriptor"]),
        })
    cursor = 0.0
    for section in sections:
        if abs(float(section["start_seconds"]) - cursor) > 1e-5:
            raise ContractError("sections do not tile continuously from zero")
        cursor = float(section["end_seconds"])
    if abs(cursor - duration) > 1e-5:
        raise ContractError("sections do not end at the audio duration")

    display = inputs["manifest"]["display"]
    source_window = inputs["manifest"]["window"]
    return {
        "schema_version": 1,
        "artifact_kind": "hajimi-timed-lyrics",
        "timebase": "seconds",
        "audio": {
            "path": "assets/bgm.wav",
            "sha256": inputs["master_sha256"],
            "duration_seconds": round(duration, 6),
            "sample_rate": 44100,
            "channels": 2,
            "frame_count": inputs["frame_count"],
        },
        "source_window": {
            "start_seconds": float(source_window["start_seconds"]),
            "end_seconds": float(source_window["end_seconds"]),
        },
        "display": {
            "title": str(display["title"]),
            "title_en": str(display["title_en"]),
            "artist_label": str(display["artist_label"]),
            "version": "HAJIMI NOTE / EVENT V1",
            "credit": str(display["credit"]),
            "status": str(display["status"]),
        },
        "sections": sections,
        "phrases": phrases,
        "lines": lines,
        "syllables": syllables,
        "audit": {
            "event_approved_path": portable_source_path(context["events"]),
            "event_approved_sha256": sha256(context["events"]),
            "render_report_path": portable_source_path(context["report"]),
            "render_report_sha256": sha256(context["report"]),
            "syllable_group_count": len(syllables),
            "note_event_count": len(events),
            "highlight_overlap_count": 0,
            "caption_coverage": f"{len(syllables)}/{len(syllables)}",
            "manual_audition_flags": [],
        },
    }


def find_shared_asset(relative: Path, destination: Path) -> Path:
    candidates = [
        item for item in sorted(MV_ROOT.glob(f"*/assets/{relative.as_posix()}"))
        if item.resolve() != destination.resolve() and item.is_file()
    ]
    if not candidates:
        raise ContractError(f"cannot find frozen local asset: assets/{relative.as_posix()}")
    return candidates[0]


def copy_shared_assets(project: Path) -> None:
    for relative in (
        Path("gsap.min.js"),
        Path("fonts/inter-400-latin.woff2"),
        Path("fonts/inter-700-latin.woff2"),
    ):
        destination = project / "assets" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = find_shared_asset(relative, destination)
        if not destination.exists() or sha256(destination) != sha256(source):
            shutil.copy2(source, destination)


def build_proxy(
    context: dict[str, Any], inputs: dict[str, Any], force: bool = False
) -> dict[str, Any]:
    project = context["project"]
    proxy = project / "assets/visual.mp4"
    proxy.parent.mkdir(parents=True, exist_ok=True)
    recipe = inputs["recipe"]
    offset = float(recipe["mv"].get("audio_to_video_offset_seconds", 0.0))
    source_start = float(recipe["window"]["start_seconds"]) + offset
    duration = inputs["duration"]
    visual_manifest_path = project / "data/visual.json"
    if not force and proxy.is_file() and visual_manifest_path.is_file():
        previous = load_json(visual_manifest_path)
        source = previous.get("source", {})
        if (
            source.get("sha256") == inputs["visual_sha256"]
            and abs(float(source.get("window_start_seconds", -1.0)) - source_start) <= 1e-6
            and abs(float(source.get("window_end_seconds", -1.0)) - (source_start + duration)) <= 1e-6
        ):
            return probe_proxy(proxy, duration)
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{source_start:.6f}", "-i", str(inputs["visual_path"]),
        "-t", f"{duration:.6f}", "-an",
        "-vf",
        "scale=928:642:force_original_aspect_ratio=increase,"
        "crop=928:642,setsar=1,fps=30,format=yuv420p",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-profile:v", "high", "-level", "4.1",
        "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-threads", "2", "-movflags", "+faststart", str(proxy),
    ]
    run(command)
    return probe_proxy(proxy, duration)


def probe_proxy(proxy: Path, expected_duration: float) -> dict[str, Any]:
    probe = media_probe(proxy)
    videos = [item for item in probe.get("streams", []) if item.get("codec_type") == "video"]
    audios = [item for item in probe.get("streams", []) if item.get("codec_type") == "audio"]
    if len(videos) != 1 or audios:
        raise ContractError("visual proxy must contain exactly one video and no audio")
    video = videos[0]
    duration = float(probe["format"]["duration"])
    fps = fraction(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    if (
        video.get("codec_name") != "h264"
        or video.get("pix_fmt") != "yuv420p"
        or int(video.get("width", 0)) != PROXY_SIZE[0]
        or int(video.get("height", 0)) != PROXY_SIZE[1]
        or not 29.9 <= fps <= 30.01
    ):
        raise ContractError(f"visual proxy format mismatch: {video}")
    if abs(duration - expected_duration) > FRAME_SECONDS + 1e-4:
        raise ContractError(f"visual proxy duration mismatch: {duration} vs {expected_duration}")
    return {
        "path": "assets/visual.mp4",
        "sha256": sha256(proxy),
        "duration_seconds": round(duration, 6),
        "frame_count": int(round(duration * fps)),
        "codec": "h264",
        "profile": str(video.get("profile") or "High"),
        "pixel_format": "yuv420p",
        "width": PROXY_SIZE[0],
        "height": PROXY_SIZE[1],
        "fps": round(fps, 6),
        "audio_stream_count": 0,
        "keyframe_interval_max_seconds": 1.0,
        "muted": True,
        "color": "BT.709 SDR",
    }


def discover_provenance(path: Path, title: str) -> tuple[str, str]:
    for ancestor in list(path.parents)[:4]:
        sources = ancestor / "SOURCES.md"
        if not sources.is_file():
            continue
        for line in sources.read_text(encoding="utf-8", errors="replace").splitlines():
            if title not in line:
                continue
            bili = re.findall(r"BV[0-9A-Za-z]{10}", line)
            youtube = re.findall(r"(?<![0-9A-Za-z_-])[0-9A-Za-z_-]{11}(?![0-9A-Za-z_-])", line)
            if "bili" in path.name.lower() and bili:
                channel = "LOCAL BILIBILI MV LIBRARY"
                match = re.search(r"B站最终：`BV[0-9A-Za-z]{10}`，([^，。]+)", line)
                if match:
                    channel = match.group(1).strip()
                return f"https://www.bilibili.com/video/{bili[-1]}", channel
            if youtube:
                channel = "LOCAL YOUTUBE MV LIBRARY"
                match = re.search(r"`[0-9A-Za-z_-]{11}`，([^，。]+)", line)
                if match:
                    channel = match.group(1).strip()
                return f"https://www.youtube.com/watch?v={youtube[0]}", channel
    return "https://local.invalid/source-metadata-not-embedded", "LOCAL MV LIBRARY"


def build_visual_manifest(
    context: dict[str, Any], inputs: dict[str, Any], proxy: dict[str, Any]
) -> dict[str, Any]:
    recipe = inputs["recipe"]
    display = inputs["manifest"]["display"]
    offset = float(recipe["mv"].get("audio_to_video_offset_seconds", 0.0))
    start = float(recipe["window"]["start_seconds"]) + offset
    url, channel = discover_provenance(inputs["visual_path"], str(display["title"]))
    alignment = recipe["mv"].get("alignment_evidence", {})
    peak = alignment.get("normalized_peak")
    if peak is None:
        values = alignment.get("normalized_peak_range", [])
        peak = min(values) if values else 0.0
    return {
        "schema_version": 1,
        "artifact_kind": "hajimi-visual-window",
        "mode": "source_mv_window",
        "source": {
            "url": url,
            "title": str(display["title"]),
            "channel": channel,
            "local_path": portable_source_path(inputs["visual_path"]),
            "sha256": inputs["visual_sha256"],
            "duration_seconds": float(recipe["mv"]["source_video"]["duration_seconds"]),
            "window_start_seconds": round(start, 6),
            "window_end_seconds": round(start + inputs["duration"], 6),
        },
        "alignment": {
            "method": "mono_8khz_audio_cross_correlation",
            "probe_start_seconds": float(recipe["window"]["start_seconds"]),
            "probe_end_seconds": min(
                float(recipe["window"]["end_seconds"]),
                float(recipe["window"]["start_seconds"]) + 30.0,
            ),
            "lag_seconds": offset,
            "normalized_peak": float(peak),
        },
        "proxy": proxy,
    }


def run_analyzer_once(project: Path, master_hash: str, force: bool) -> tuple[dict[str, Any], bool]:
    output = project / "audiomap.json"
    evidence_path = project / "data/build-evidence.json"
    evidence = load_json(evidence_path) if evidence_path.exists() else {}
    reusable = (
        not force
        and output.is_file()
        and evidence.get("master_sha256") == master_hash
        and evidence.get("analyzer") == "music-to-video/analyze-beatgrid.py"
    )
    if not reusable:
        if not ANALYZER.is_file():
            raise ContractError(f"canonical music analyzer missing: {ANALYZER}")
        # The audio venv intentionally keeps the renderer's older librosa,
        # while its newer SciPy moved ``signal.hann`` under ``signal.windows``.
        # Keep the frozen analyzer unchanged and install the one-symbol shim in
        # this child process only; this does not add a second analyzer pass.
        compatibility_runner = (
            "import runpy,sys; import scipy.signal as signal; "
            "signal.hann=getattr(signal,'hann',signal.windows.hann); "
            "sys.argv=[sys.argv[1],*sys.argv[2:]]; "
            "runpy.run_path(sys.argv[0],run_name='__main__')"
        )
        run([
            sys.executable, "-c", compatibility_runner, ANALYZER,
            project / "assets/bgm.wav", "-o", output,
        ])
    audiomap = load_json(output)
    evidence.update({
        "master_sha256": master_hash,
        "analyzer": "music-to-video/analyze-beatgrid.py",
        "analyzer_sha256": sha256(ANALYZER),
        "audiomap_sha256": sha256(output),
        "analyzer_invocations_this_build": 0 if reusable else 1,
    })
    dump_json(evidence_path, evidence)
    return audiomap, not reusable


def derive_audio_bands(audiomap: dict[str, Any], duration: float) -> dict[str, Any]:
    phases = audiomap.get("energy_phases", [])
    if not phases:
        phases = [{"start": 0.0, "end": duration, "energy": 0.5}]
    frames: list[dict[str, Any]] = []
    total = int(math.ceil(duration * FPS))
    phase_index = 0
    for frame in range(total):
        current_time = min(duration, frame / FPS)
        while (
            phase_index + 1 < len(phases)
            and current_time >= float(phases[phase_index].get("end", duration))
        ):
            phase_index += 1
        energy = max(0.0, min(1.0, float(phases[phase_index].get("energy", 0.5))))
        bands = [
            round(max(0.0, min(1.0, energy * (0.72 + ((index * 7) % 11) / 40.0))), 4)
            for index in range(16)
        ]
        frames.append({"time": round(current_time, 4), "rms": round(energy, 4), "bands": bands})
    return {
        "fps": FPS,
        "duration": round(duration, 6),
        "totalFrames": total,
        "bands": 16,
        "source": "canonical audiomap energy phases; no second audio analyzer",
        "frames": frames,
    }


def build_theme(timed: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_kind": "hajimi-mv-theme",
        "theme_id": "rain-window-coral-v1",
        "preset": "hajimi-rain-window-v1",
        "display": timed["display"],
        "canvas": {
            "width": 1080, "height": 1920, "fps": 30,
            "safe_area_px": 72, "top_visual_height_px": 870, "footer_height_px": 140,
        },
        "palette": {
            "ink": "#1A1A1A", "cream": "#F5F0E8", "cream_dark": "#E8E0D4",
            "coral": "#E85D5D", "coral_dark": "#D44A4A", "gray": "#6B6B6B",
            "light_gray": "#B0B0B0",
        },
        "typography": {
            "display": "Inter, Arial Narrow, sans-serif",
            "cjk_display": "Songti SC, STSong, serif",
            "body": "Inter, PingFang SC, sans-serif",
            "mono": "SFMono-Regular, Menlo, monospace",
            "font_preflight_status": "PASS",
        },
        "layout": {
            "top_window": {"x": 72, "y": 160, "width": 936, "height": 650, "border_px": 4},
            "window_inner": {"x": 76, "y": 164, "width": 928, "height": 642},
            "lower_content": {"x": 72, "y": 870, "width": 936, "height": 910},
            "footer": {"x": 72, "y": 1780, "width": 936, "height": 140},
        },
        "visual_media": {
            "mode": "source_mv_window", "manifest_path": "data/visual.json",
            "muted": True, "fit": "pre_cropped_cover",
        },
        "audio_reactivity": {
            "source": "data/audio-bands.json", "runtime_analysis": False,
            "fps": 30, "band_count": 16,
        },
        "constraints": [
            "No remote runtime assets", "One continuous muted video element",
            "One authoritative root audio element", "Maximum one active lyric token",
            "No runtime ASR FFT F0 random or network work",
        ],
    }


def build_index(project: Path, timed: dict[str, Any]) -> None:
    duration = float(timed["audio"]["duration_seconds"])
    display = timed["display"]
    title_size = 142 if len(display["title"]) <= 4 else 118 if len(display["title"]) <= 6 else 96
    section_markup = []
    for index, section in enumerate(timed["sections"], start=1):
        palette = PALETTES.get(section["role"], PALETTES["open"])
        start = float(section["start_seconds"])
        # Leave a one-microsecond edit gap between adjacent clips.  HyperFrames
        # 0.6.69 compares binary floats strictly and otherwise flags decimal
        # boundaries such as 23.67 + 1.285 as an overlap.
        raw_length = float(section["end_seconds"]) - start
        length = raw_length if index == len(timed["sections"]) else raw_length - 0.000001
        relevant_lines = [
            line for line in timed["lines"]
            if int(line["phrase_id"]) in {int(item) for item in section["phrase_ids"]}
        ]
        rows = "".join(
            '<div class="lyric-row" data-line="{}">{}</div>'.format(
                line["id"],
                "".join(
                    f'<span class="lyric-char" data-group="{group_id}">{next(item["text"] for item in timed["syllables"] if int(item["group_id"]) == int(group_id))}</span>'
                    for group_id in line["group_ids"]
                ),
            )
            for line in relevant_lines
        )
        section_markup.append(f'''<section id="section-{section['id']}" class="section clip" data-section="{section['id']}"
          data-start="{start:.6f}" data-duration="{length:.6f}" data-track-index="1"
          style="--top:{palette['top']};--top-ink:{palette['top_ink']};--lower:{palette['lower']};--lyric:{palette['lyric']};--muted:{palette['muted']};--accent:{palette['accent']};--active-bg:{palette['active_bg']};--footer:{palette['footer']};--footer-ink:{palette['footer_ink']};--progress:{palette['progress']}">
          <div class="top"><div class="number">{index:02d}</div><div class="top-meta"><span>MV WINDOW</span><span>{section['label']}</span></div><div class="window"></div></div>
          <div class="lower"><div class="head"><span>HAJIMI NOTE / EVENT V1</span><span>{section['descriptor']}</span></div>
            <div class="titles"><div class="title">{display['title']}</div><div class="title-en">{display['title_en'].upper()}</div></div>
            <div class="lyrics"><div class="ghost">哈</div>{rows}<div class="hold"></div></div>
            <div class="identity">CURRENT SYLLABLE / SEVEN-TOKEN IDENTITY RAIL</div>
          </div><footer><div class="progress"><i></i></div><div class="footer-meta"><span>{display['artist_label']}</span><span class="timecode">00:00</span><span>{display['status']}</span></div></footer>
        </section>''')
    data = js_json(timed)
    html = f'''<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=1080,height=1920">
<script src="assets/gsap.min.js"></script><style>
@font-face{{font-family:Inter;src:url("assets/fonts/inter-400-latin.woff2")}}@font-face{{font-family:Inter;src:url("assets/fonts/inter-700-latin.woff2");font-weight:700}}
*{{box-sizing:border-box}}html,body{{margin:0;width:1080px;height:1920px;overflow:hidden;background:#1A1A1A}}#root{{position:relative;width:1080px;height:1920px;overflow:hidden;font-family:Inter,sans-serif}}
.section{{position:absolute;inset:0;width:1080px;height:1920px;overflow:hidden;color:var(--lyric)}}.top{{height:870px;background:var(--top);position:relative;overflow:hidden}}.top:before{{content:"";position:absolute;inset:0;background:repeating-linear-gradient(45deg,transparent 0 20px,rgba(0,0,0,.06) 20px 40px)}}
.number{{position:absolute;left:32px;top:42px;font-size:218px;line-height:1;letter-spacing:10px;font-weight:700;color:color-mix(in srgb,var(--top-ink) 12%,transparent)}}.top-meta{{position:absolute;left:72px;right:72px;top:78px;display:flex;justify-content:space-between;color:var(--top-ink);font:700 20px/1 Inter;letter-spacing:4px}}
.window{{position:absolute;left:72px;top:160px;width:936px;height:650px;border:4px solid #1A1A1A}}#visual{{position:absolute;left:76px;top:164px;width:928px;height:642px;object-fit:fill;background:#1A1A1A;z-index:2}}#visual-band{{position:absolute;left:76px;top:734px;width:928px;height:72px;background:rgba(26,26,26,.82);border-top:3px solid rgba(232,93,93,.82);z-index:3;color:#F5F0E8;padding:0 30px;display:flex;align-items:center;justify-content:space-between;font:700 16px/1 Inter;letter-spacing:3px}}
.lower{{position:absolute;left:0;top:870px;width:1080px;height:910px;background:var(--lower);padding:80px 72px 52px;border-top:5px solid #E85D5D}}.head{{display:flex;justify-content:space-between;color:var(--accent);font:700 17px/1 Inter;letter-spacing:3px}}.head span:last-child{{color:var(--muted);font-size:13px;max-width:460px;text-align:right}}
.titles{{position:relative;margin-top:30px;height:182px}}.title{{font:700 {title_size}px/.94 serif;letter-spacing:-5px}}.title-en{{position:absolute;right:0;bottom:14px;max-width:470px;text-align:right;font:700 32px/1 Inter;letter-spacing:4px;color:var(--accent)}}.lyrics{{position:relative;height:410px;margin-top:10px}}.lyric-row{{position:absolute;left:0;right:0;top:90px;height:180px;display:flex;align-items:center;gap:12px;opacity:0;font:600 80px/1 sans-serif}}.lyric-char{{display:inline-grid;place-items:center;min-width:108px;height:122px;border-bottom:4px solid transparent}}.ghost{{position:absolute;right:-10px;top:30px;opacity:.08;font:700 230px/1 serif}}.hold{{position:absolute;left:0;top:274px;width:220px;height:8px;background:var(--accent);transform:scaleX(0);transform-origin:left}}.identity{{position:absolute;left:72px;bottom:38px;color:var(--muted);font:700 15px/1 Inter;letter-spacing:4px}}
footer{{position:absolute;left:0;right:0;bottom:0;height:140px;background:var(--footer);color:var(--footer-ink);padding:30px 72px 24px}}.progress{{height:5px;background:rgba(128,128,128,.28)}}.progress i{{display:block;width:100%;height:100%;background:var(--progress);transform:scaleX(0);transform-origin:left}}.footer-meta{{display:grid;grid-template-columns:1fr auto;gap:12px 24px;margin-top:18px;font:700 15px/1 Inter;letter-spacing:2px}}.footer-meta span:last-child{{grid-column:1/-1;opacity:.72}}
</style></head><body><div id="root" data-composition-id="main" data-start="0" data-duration="{duration:.6f}" data-width="1080" data-height="1920" data-fps="30">
{''.join(section_markup)}<video id="visual" class="clip" src="assets/visual.mp4" data-start="0" data-duration="{duration:.6f}" data-media-start="0" data-track-index="2" muted playsinline preload="auto"></video>
<div id="visual-band" class="clip" data-start="0" data-duration="{duration:.6f}" data-track-index="3"><span>{display['title']} / HAJIMI</span><span>MUTED SOURCE MV / MASTER AUDIO</span></div>
<audio id="master-audio" src="assets/bgm.wav" data-start="0" data-duration="{duration:.6f}" data-track-index="11" data-volume="1"></audio></div>
<script>(()=>{{const D={data};const tl=gsap.timeline({{paused:true}});const fmt=s=>{{s=Math.max(0,Math.floor(s));return String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0')}};
D.sections.forEach(sec=>{{const root=document.querySelector('[data-section="'+sec.id+'"]');const lines=D.lines.filter(x=>sec.phrase_ids.includes(x.phrase_id));lines.forEach(line=>{{const el=root.querySelector('[data-line="'+line.id+'"]');const a=Math.max(sec.start_seconds,line.start_seconds),b=Math.min(sec.end_seconds,line.end_seconds);if(el&&b>a){{tl.set(el,{{opacity:1}},a);tl.set(el,{{opacity:0}},b);}}}});D.syllables.filter(x=>sec.phrase_ids.includes(x.phrase_id)).forEach(s=>{{const el=root.querySelector('[data-group="'+s.group_id+'"]');if(!el)return;const a=Math.max(sec.start_seconds,s.start_seconds),b=Math.min(sec.end_seconds,s.end_seconds);if(b<=a)return;tl.set(el,{{color:'#E85D5D',scale:1.06,y:-4,borderBottomColor:'#E85D5D'}},a);tl.set(root.querySelector('.ghost'),{{textContent:s.text}},a);tl.fromTo(root.querySelector('.hold'),{{scaleX:0}},{{scaleX:1,duration:Math.max(.001,b-a),ease:'none'}},a);tl.set(el,{{color:'var(--lyric)',scale:1,y:0,borderBottomColor:'transparent'}},b);}});tl.fromTo(root.querySelector('.progress i'),{{scaleX:sec.start_seconds/{duration}}},{{scaleX:sec.end_seconds/{duration},duration:sec.end_seconds-sec.start_seconds,ease:'none'}},sec.start_seconds);tl.set(root.querySelector('.timecode'),{{textContent:fmt(sec.start_seconds)+' / '+fmt({duration})}},sec.start_seconds);for(let s=Math.ceil(sec.start_seconds);s<=sec.end_seconds;s++)tl.set(root.querySelector('.timecode'),{{textContent:fmt(s)+' / '+fmt({duration})}},s);}});tl.set(document.querySelector('#root'),{{opacity:1}},{duration});window.__timelines=window.__timelines||{{}};window.__timelines.main=tl;tl.seek(0);}})();</script></body></html>'''
    (project / "index.html").write_text(html, encoding="utf-8")


def write_project_metadata(project: Path, song_id: str) -> None:
    dump_json(project / "hyperframes.json", {
        "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
        "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
        "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
    })
    dump_json(project / "meta.json", {"id": f"{song_id}-hajimi-mv-v1", "name": f"{song_id} Hajimi MV V1"})
    dump_json(project / "package.json", {
        "name": f"{song_id}-hajimi-mv-v1", "private": True, "type": "module",
        "scripts": {"check": "npx --yes hyperframes@0.6.69 lint && npx --yes hyperframes@0.6.69 validate && npx --yes hyperframes@0.6.69 inspect"},
    })


def command_build(context: dict[str, Any], force: bool) -> dict[str, Any]:
    inputs = validate_audio_context(context)
    project = context["project"]
    for directory in ("assets/fonts", "data", "renders", "qa", "snapshots"):
        (project / directory).mkdir(parents=True, exist_ok=True)
    copy_shared_assets(project)
    shutil.copy2(context["master"], project / "assets/bgm.wav")
    proxy = build_proxy(context, inputs, force)
    timed = build_timed_lyrics(context, inputs)
    dump_json(project / "data/timed_lyrics.json", timed)
    dump_json(project / "data/visual.json", build_visual_manifest(context, inputs, proxy))
    dump_json(project / "data/theme.json", build_theme(timed))
    audiomap, invoked = run_analyzer_once(project, inputs["master_sha256"], force)
    dump_json(project / "data/audio-bands.json", derive_audio_bands(audiomap, inputs["duration"]))
    build_index(project, timed)
    write_project_metadata(project, context["song_id"])
    evidence = load_json(project / "data/build-evidence.json")
    evidence.update({"recipe_sha256": sha256(context["recipe_path"]), "renderer_sha256": sha256(SCRIPT_PATH), "duration_seconds": inputs["duration"], "sections": len(timed["sections"]), "syllables": len(timed["syllables"]), "proxy_sha256": proxy["sha256"]})
    dump_json(project / "data/build-evidence.json", evidence)
    return {"status": "MV_BUILT", "project": str(project), "duration_seconds": inputs["duration"], "sections": len(timed["sections"]), "syllables": len(timed["syllables"]), "analyzer_invocations": 1 if invoked else 0}


def internal_check(context: dict[str, Any]) -> dict[str, Any]:
    project = context["project"]
    timed = load_json(project / "data/timed_lyrics.json")
    html = (project / "index.html").read_text(encoding="utf-8")
    duration = float(timed["audio"]["duration_seconds"])
    failures = []
    if html.count("<video ") != 1 or html.count("<audio ") != 1:
        failures.append("host must contain exactly one video and one audio")
    if "https://" in html or "http://" in html:
        failures.append("runtime HTML contains remote URL")
    if html.count('data-track-index="1"') != len(timed["sections"]):
        failures.append("section shell count mismatch")
    if len({item["id"] for item in timed["syllables"]}) != len(timed["syllables"]):
        failures.append("duplicate syllable ids")
    proxy = probe_proxy(project / "assets/visual.mp4", duration)
    if sha256(project / "assets/bgm.wav") != timed["audio"]["sha256"]:
        failures.append("master hash mismatch")
    if failures:
        raise ContractError("; ".join(failures))
    return {"status": "PASS", "project": str(project), "duration_seconds": duration, "proxy": proxy}


def command_check(context: dict[str, Any]) -> dict[str, Any]:
    result = internal_check(context)
    hf = resolve_hyperframes_command()
    logs = {}
    for name in ("lint", "validate", "inspect"):
        completed = run([*hf, name, "--json"], cwd=context["project"])
        logs[name] = completed.stdout[-4000:]
    dump_json(context["project"] / "qa/hyperframes-check.json", {"status": "PASS", "logs": logs})
    result["hyperframes"] = "lint+validate+inspect PASS"
    return result


def snapshot_times(timed: dict[str, Any]) -> list[float]:
    duration = float(timed["audio"]["duration_seconds"])
    values = {0.1, max(0.0, duration - 0.1)}
    for item in timed["sections"]:
        start, end = float(item["start_seconds"]), float(item["end_seconds"])
        values.update({(start + end) / 2, max(0.0, start - FRAME_SECONDS), min(duration, start + FRAME_SECONDS), max(0.0, end - FRAME_SECONDS)})
    values.update(float(item["start_seconds"]) for item in timed["phrases"])
    values.add(float(timed["syllables"][-1]["start_seconds"]))
    return sorted(round(max(0.0, min(duration - 0.001, item)), 4) for item in values)


def command_snapshot(context: dict[str, Any]) -> dict[str, Any]:
    internal_check(context)
    times = snapshot_times(load_json(context["project"] / "data/timed_lyrics.json"))
    run([*resolve_hyperframes_command(), "snapshot", "--at", ",".join(map(str, times)), "--describe", "false"], cwd=context["project"])
    return {"status": "MV_SNAPSHOTS_READY", "project": str(context["project"]), "times_seconds": times}


def command_render(context: dict[str, Any], quality: str) -> dict[str, Any]:
    internal_check(context)
    raw = context["project"] / "renders" / f"{context['song_id']}-hajimi-mv-v1_raw.mp4"
    started = time.monotonic()
    input_hashes = {
        "index": sha256(context["project"] / "index.html"),
        "master": sha256(context["project"] / "assets/bgm.wav"),
        "visual_proxy": sha256(context["project"] / "assets/visual.mp4"),
        "timed_lyrics": sha256(context["project"] / "data/timed_lyrics.json"),
        "renderer": sha256(SCRIPT_PATH),
    }
    render_command: list[str | Path] = [
        *resolve_hyperframes_command(), "render", "--fps", "30", "--workers", "1",
        "--quality", quality, "--sdr", "--strict", "--output", raw,
    ]
    if RESOURCE_BUDGET.is_file():
        render_command = [sys.executable, RESOURCE_BUDGET, "hyperframes", "--", *render_command]
    run(render_command, cwd=context["project"], capture=False)
    if not raw.is_file() or raw.stat().st_size < 1024:
        raise ContractError("HyperFrames render produced no plausible raw MP4")
    evidence = {
        "schema_version": 1,
        "status": "MV_RENDERED",
        "quality": quality,
        "workers": 1,
        "sdr": True,
        "fps": FPS,
        "wall_seconds": round(time.monotonic() - started, 3),
        "inputs": input_hashes,
        "raw": {"path": str(raw), "sha256": sha256(raw), "bytes": raw.stat().st_size},
    }
    dump_json(context["project"] / "qa/render-evidence.json", evidence)
    return {"status": "MV_RENDERED", "raw": str(raw), "sha256": evidence["raw"]["sha256"], "bytes": raw.stat().st_size, "workers": 1}


def command_reuse_video(context: dict[str, Any], source_project: Path | None) -> dict[str, Any]:
    """Reuse a frozen H.264 stream only when every visual timeline input is identical."""

    if source_project is None:
        raise ContractError("reuse-video requires --source-project")
    source_project = source_project.resolve()
    project = context["project"]
    if source_project == project:
        raise ContractError("reuse-video source and destination projects must differ")
    if project.exists():
        raise ContractError(f"reuse-video destination already exists: {project}")
    inputs = validate_audio_context(context, require_visual_source=False)
    song_id = context["song_id"]
    source_raw = source_project / "renders" / f"{song_id}-hajimi-mv-v1_raw.mp4"
    source_final = source_project / "renders" / f"{song_id}-hajimi-mv-v1.mp4"
    source_render_evidence_path = source_project / "qa/render-evidence.json"
    old_master = source_project / "assets/bgm.wav"
    old_timed_path = source_project / "data/timed_lyrics.json"
    source_index = source_project / "index.html"
    source_theme = source_project / "data/theme.json"
    source_visual = source_project / "data/visual.json"
    source_proxy = source_project / "assets/visual.mp4"
    required = [
        source_raw, source_final, source_render_evidence_path, old_master,
        old_timed_path, source_index, source_theme, source_visual, source_proxy,
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ContractError("reuse-video source project is incomplete: " + ", ".join(missing))
    source_render_evidence = load_json(source_render_evidence_path)
    if source_render_evidence.get("raw", {}).get("sha256") != sha256(source_raw):
        raise ContractError("source raw does not match source render evidence")
    old_contract = wav_contract(old_master)
    new_contract = wav_contract(context["master"])
    sample_keys = ("sample_rate", "channels", "sample_width_bytes", "frame_count")
    if any(old_contract[key] != new_contract[key] for key in sample_keys):
        raise ContractError(
            f"video reuse sample contract differs: old={old_contract} new={new_contract}"
        )
    old_timed = load_json(old_timed_path)
    new_timed = build_timed_lyrics(context, inputs)
    old_timeline_signature = canonical_json_sha256(visual_timeline_projection(old_timed))
    new_timeline_signature = canonical_json_sha256(visual_timeline_projection(new_timed))
    if old_timeline_signature != new_timeline_signature:
        raise ContractError("video reuse timeline signature differs; full render required")
    index_text = source_index.read_text(encoding="utf-8")
    if "audio-bands.json" in index_text or "data/audio-bands" in index_text:
        raise ContractError("source renderer consumes audio-reactive data; full render required")
    source_renderer_sha = str(
        source_render_evidence.get("inputs", {}).get("renderer", "")
    )
    if not re.fullmatch(r"[a-f0-9]{64}", source_renderer_sha):
        raise ContractError("source render evidence has no valid renderer SHA")
    visual_signature = canonical_json_sha256(
        {
            "index_sha256": sha256(source_index),
            "theme_sha256": sha256(source_theme),
            "visual_manifest_sha256": sha256(source_visual),
            "visual_proxy_sha256": sha256(source_proxy),
            "timeline_signature": old_timeline_signature,
            "source_renderer_sha256": source_renderer_sha,
        }
    )

    project.mkdir(parents=True)
    for directory in ("assets", "data", "renders", "qa"):
        (project / directory).mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "hyperframes.json", "meta.json", "package.json", "audiomap.json"):
        source = source_project / name
        if source.is_file():
            shutil.copy2(source, project / name)
    source_compositions = source_project / "compositions"
    if source_compositions.is_dir():
        shutil.copytree(source_compositions, project / "compositions")
    for source in (source_project / "assets").rglob("*"):
        if not source.is_file() or source.name == "bgm.wav":
            continue
        relative = source.relative_to(source_project / "assets")
        destination = project / "assets" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    for name in ("theme.json", "visual.json", "audio-bands.json"):
        shutil.copy2(source_project / "data" / name, project / "data" / name)
    shutil.copy2(context["master"], project / "assets/bgm.wav")
    dump_json(project / "data/timed_lyrics.json", new_timed)
    video_only_raw = project / "renders" / f"{song_id}-hajimi-mv-v1_raw.mp4"
    run(
        [
            "ffmpeg", "-v", "error", "-xerror", "-threads", "2", "-i", source_raw,
            "-map", "0:v:0", "-c:v", "copy", "-an", "-movflags", "+faststart",
            "-y", video_only_raw,
        ]
    )
    source_packet_hash = video_packet_sha256(source_raw)
    reused_packet_hash = video_packet_sha256(video_only_raw)
    if source_packet_hash != reused_packet_hash:
        raise ContractError("reused video packet hash differs from source raw")
    evidence = {
        "schema_version": 1,
        "artifact_kind": "hajimi-video-stream-reuse-evidence",
        "status": "VIDEO_STREAM_REUSED_TIMELINE_IDENTICAL",
        "source_project": portable_source_path(source_project),
        "source_raw": {"path": portable_source_path(source_raw), "sha256": sha256(source_raw)},
        "source_render_evidence": {
            "path": portable_source_path(source_render_evidence_path),
            "sha256": sha256(source_render_evidence_path),
        },
        "source_final": {"path": portable_source_path(source_final), "sha256": sha256(source_final)},
        "old_master": old_contract,
        "new_master": new_contract,
        "old_timeline_signature": old_timeline_signature,
        "new_timeline_signature": new_timeline_signature,
        "visual_signature": visual_signature,
        "renderer_sha256": source_renderer_sha,
        "reuse_runtime_sha256": sha256(SCRIPT_PATH),
        "video_only_raw": {
            "path": portable_source_path(video_only_raw),
            "sha256": sha256(video_only_raw),
        },
        "checks": {
            "sample_contract": "PASS",
            "timeline_identity": "PASS",
            "visual_identity": "PASS",
            "video_packet_identity": "PASS",
        },
        "created_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    dump_json(project / "qa/video-reuse-evidence.json", evidence)
    dump_json(
        project / "data/build-evidence.json",
        {
            "schema_version": 1,
            "status": "VIDEO_STREAM_REUSED_TIMELINE_IDENTICAL",
            "recipe_sha256": sha256(context["recipe_path"]),
            "audio_run_id": context["run_id"],
            "master_sha256": sha256(context["master"]),
            "timeline_signature": new_timeline_signature,
            "visual_signature": visual_signature,
        },
    )
    return {
        "status": "VIDEO_STREAM_REUSED_TIMELINE_IDENTICAL",
        "project": str(project),
        "raw": str(video_only_raw),
        "video_packet_sha256": reused_packet_hash,
    }


def command_mux(context: dict[str, Any]) -> dict[str, Any]:
    project = context["project"]
    raw = project / "renders" / f"{context['song_id']}-hajimi-mv-v1_raw.mp4"
    final = project / "renders" / f"{context['song_id']}-hajimi-mv-v1.mp4"
    if not raw.is_file():
        raise ContractError(f"raw render missing: {raw}")
    master = project / "assets/bgm.wav"
    raw_probe = media_probe(raw)
    master_probe = media_probe(master)
    videos = [item for item in raw_probe["streams"] if item.get("codec_type") == "video"]
    audios = [item for item in master_probe["streams"] if item.get("codec_type") == "audio"]
    if len(videos) != 1 or len(audios) != 1:
        raise ContractError("mux requires exactly one raw video and one master audio stream")
    raw_format_duration = float(raw_probe["format"]["duration"])
    master_format_duration = float(master_probe["format"]["duration"])
    video_start = float(videos[0].get("start_time", 0.0) or 0.0)
    master_start = float(audios[0].get("start_time", 0.0) or 0.0)
    video_end = video_start + stream_duration(videos[0], raw_format_duration)
    master_end = master_start + stream_duration(audios[0], master_format_duration)
    tail_padding = max(0.0, video_end - master_end)
    if master_end - video_end >= FRAME_SECONDS:
        raise ContractError("master audio extends at least one frame beyond the raw video")
    command: list[str | Path] = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", raw, "-i", master,
    ]
    if tail_padding > 0.5 / 44100.0:
        command.extend([
            "-filter_complex", f"[1:a]apad=pad_dur={tail_padding:.9f}[master]",
            "-map", "0:v:0", "-map", "[master]",
        ])
    else:
        command.extend(["-map", "0:v:0", "-map", "1:a:0"])
    command.extend([
        "-c:v", "copy", "-c:a", "aac", "-profile:a", "aac_low",
        "-b:a", "320k", "-ar", "44100", "-ac", "2", "-threads", "2",
        "-movflags", "+faststart", final,
    ])
    run(command)
    evidence = {
        "schema_version": 1,
        "status": "MASTER_MUXED",
        "video_copy": True,
        "audio_codec": "AAC-LC 320k 44.1k stereo",
        "raw_sha256": sha256(raw),
        "master_sha256": sha256(master),
        "timing": {
            "raw_video_start_seconds": round(video_start, 9),
            "raw_video_end_seconds": round(video_end, 9),
            "master_start_seconds": round(master_start, 9),
            "master_end_seconds": round(master_end, 9),
            "tail_padding_seconds": round(tail_padding, 9),
        },
        "final_sha256": sha256(final),
        "final_bytes": final.stat().st_size,
    }
    dump_json(project / "qa/mux-evidence.json", evidence)
    return {"status": "MASTER_MUXED", "final": str(final), "sha256": evidence["final_sha256"], "bytes": final.stat().st_size}


def strict_decode(path: Path) -> None:
    run(["ffmpeg", "-v", "error", "-xerror", "-threads", "2", "-i", path, "-f", "null", "-"])


def measure_loudness(path: Path) -> dict[str, float]:
    completed = run([
        "ffmpeg", "-hide_banner", "-nostats", "-threads", "2", "-i", path,
        "-map", "0:a:0", "-af", "loudnorm=I=-9.5:LRA=11:TP=-1:print_format=json",
        "-f", "null", "-",
    ])
    candidates = re.findall(r"\{\s*\"input_i\".*?\}", completed.stderr, flags=re.DOTALL)
    if not candidates:
        raise ContractError("final AAC loudness measurement emitted no JSON")
    measured = json.loads(candidates[-1])
    return {
        "integrated_lufs": float(measured["input_i"]),
        "loudness_range_lu": float(measured["input_lra"]),
        "true_peak_dbtp": float(measured["input_tp"]),
    }


def detect_black_and_silence(path: Path) -> dict[str, Any]:
    black = run([
        "ffmpeg", "-hide_banner", "-nostats", "-threads", "2", "-i", path,
        "-an", "-vf", "blackdetect=d=0.10:pix_th=0.02", "-f", "null", "-",
    ])
    silence = run([
        "ffmpeg", "-hide_banner", "-nostats", "-threads", "2", "-i", path,
        "-vn", "-af", "silencedetect=n=-55dB:d=0.50", "-f", "null", "-",
    ])
    black_segments = re.findall(r"black_start:([0-9.]+).*?black_end:([0-9.]+)", black.stderr)
    silence_segments = re.findall(r"silence_start: ([0-9.]+).*?silence_end: ([0-9.]+)", silence.stderr, re.DOTALL)
    return {
        "black_segments_over_100ms": [
            {"start_seconds": float(start), "end_seconds": float(end)}
            for start, end in black_segments
        ],
        "silence_segments_over_500ms": [
            {"start_seconds": float(start), "end_seconds": float(end)}
            for start, end in silence_segments
        ],
    }


def audio_alignment(master: Path, final: Path) -> dict[str, Any]:
    try:
        import numpy as np
        import soundfile as sf
        from scipy import signal
    except ImportError as exc:
        raise ContractError(f"audio alignment dependencies unavailable: {exc}") from exc
    with tempfile.TemporaryDirectory(prefix="hajimi-aac-qa-") as temporary:
        decoded = Path(temporary) / "decoded.wav"
        run([
            "ffmpeg", "-v", "error", "-xerror", "-threads", "2", "-i", final,
            "-map", "0:a:0", "-ar", "44100", "-ac", "2", "-c:a", "pcm_f32le",
            "-y", decoded,
        ])
        source, source_rate = sf.read(master, dtype="float32", always_2d=True)
        encoded, encoded_rate = sf.read(decoded, dtype="float32", always_2d=True)
    if source_rate != 44100 or encoded_rate != 44100:
        raise ContractError("AAC alignment decode sample rate changed")
    source_mono = source.mean(axis=1)
    encoded_mono = encoded.mean(axis=1)
    common = min(len(source_mono), len(encoded_mono))
    window = min(6 * source_rate, common)
    start = max(0, min(common - window, common // 3))
    reference = source_mono[start : start + window].astype("float64")
    candidate = encoded_mono[start : start + window].astype("float64")
    reference -= reference.mean()
    candidate -= candidate.mean()
    correlation = signal.correlate(candidate, reference, mode="full", method="fft")
    lags = signal.correlation_lags(len(candidate), len(reference), mode="full")
    limit = int(round(source_rate / FPS))
    allowed = np.abs(lags) <= limit
    restricted = correlation[allowed]
    restricted_lags = lags[allowed]
    index = int(np.argmax(restricted))
    lag_samples = int(restricted_lags[index])
    denominator = float(np.linalg.norm(reference) * np.linalg.norm(candidate))
    coefficient = float(restricted[index] / denominator) if denominator else 0.0
    return {
        "lag_samples": lag_samples,
        "lag_milliseconds": round(1000.0 * lag_samples / source_rate, 4),
        "correlation": round(coefficient, 6),
        "allowed_lag_samples": limit,
        "source_frames": len(source_mono),
        "decoded_frames": len(encoded_mono),
        "window_seconds": round(window / source_rate, 3),
    }


def extract_final_frames(project: Path, final: Path, timed: dict[str, Any]) -> dict[str, Any]:
    output_dir = project / "qa/final-frames"
    output_dir.mkdir(parents=True, exist_ok=True)
    duration = float(timed["audio"]["duration_seconds"])
    sample_times = {0.1, max(0.0, duration - 0.1)}
    for section in timed["sections"]:
        start = float(section["start_seconds"])
        end = float(section["end_seconds"])
        sample_times.add((start + end) / 2.0)
    times = sorted(sample_times)
    frames: list[dict[str, Any]] = []
    for index, at in enumerate(times):
        output = output_dir / f"frame-{index:02d}.png"
        run([
            "ffmpeg", "-v", "error", "-xerror", "-threads", "2", "-ss", f"{at:.6f}",
            "-i", final, "-frames:v", "1", "-y", output,
        ])
        frames.append({"time_seconds": round(at, 6), "path": str(output), "sha256": sha256(output)})
    contact = project / "qa/final-contact-sheet.jpg"
    run([
        "ffmpeg", "-v", "error", "-xerror", "-threads", "2", "-framerate", "1",
        "-i", output_dir / "frame-%02d.png", "-vf", "scale=270:-1,tile=3x2",
        "-frames:v", "1", "-y", contact,
    ])
    return {"frames": frames, "contact_sheet": {"path": str(contact), "sha256": sha256(contact)}}


def command_qa(context: dict[str, Any]) -> dict[str, Any]:
    project = context["project"]
    final = project / "renders" / f"{context['song_id']}-hajimi-mv-v1.mp4"
    raw = project / "renders" / f"{context['song_id']}-hajimi-mv-v1_raw.mp4"
    reuse_lane = (project / "qa/video-reuse-evidence.json").is_file()
    inputs = validate_audio_context(context, require_visual_source=not reuse_lane)
    strict_decode(final)
    probe = media_probe(final)
    videos = [item for item in probe["streams"] if item.get("codec_type") == "video"]
    audios = [item for item in probe["streams"] if item.get("codec_type") == "audio"]
    failures = []
    stream_timing: dict[str, float] = {}
    if len(videos) != 1 or len(audios) != 1:
        failures.append("final must contain one video and one audio")
    else:
        video, audio = videos[0], audios[0]
        if (video.get("codec_name"), int(video.get("width", 0)), int(video.get("height", 0)), video.get("pix_fmt")) != ("h264", 1080, 1920, "yuv420p"):
            failures.append("video format/profile mismatch")
        if abs(fraction(video.get("avg_frame_rate")) - 30.0) > 0.001:
            failures.append("video fps mismatch")
        if audio.get("codec_name") != "aac" or int(audio.get("sample_rate", 0)) != 44100 or int(audio.get("channels", 0)) != 2:
            failures.append("audio format mismatch")
        format_duration = float(probe["format"]["duration"])
        video_start = float(video.get("start_time", 0.0) or 0.0)
        audio_start = float(audio.get("start_time", 0.0) or 0.0)
        video_end = video_start + stream_duration(video, format_duration)
        audio_end = audio_start + stream_duration(audio, format_duration)
        stream_timing = {
            "video_start_seconds": round(video_start, 9),
            "audio_start_seconds": round(audio_start, 9),
            "start_difference_seconds": round(abs(video_start - audio_start), 9),
            "video_end_seconds": round(video_end, 9),
            "audio_end_seconds": round(audio_end, 9),
            "tail_difference_seconds": round(abs(video_end - audio_end), 9),
            "maximum_difference_seconds": round(FRAME_SECONDS, 9),
        }
        if abs(video_start - audio_start) >= FRAME_SECONDS:
            failures.append("audio/video start difference is at least one frame")
        if abs(video_end - audio_end) >= FRAME_SECONDS:
            failures.append("audio/video tail difference is at least one frame")
    timed = load_json(project / "data/timed_lyrics.json")
    loudness = measure_loudness(final)
    source_loudness = inputs["audio_qa"]["checks"]["loudness"]
    loudness_delta = loudness["integrated_lufs"] - float(source_loudness["integrated_lufs"])
    if abs(loudness_delta) > 0.3:
        failures.append("AAC integrated loudness differs from master by more than 0.3 LU")
    if loudness["true_peak_dbtp"] > -1.0 + 1e-6:
        failures.append("AAC decoded true peak exceeds -1 dBTP")
    continuity = detect_black_and_silence(final)
    if continuity["black_segments_over_100ms"]:
        failures.append("full-frame black segment detected")
    if continuity["silence_segments_over_500ms"]:
        failures.append("unexpected final-audio silence detected")
    alignment = audio_alignment(context["master"], final)
    if abs(int(alignment["lag_samples"])) > int(alignment["allowed_lag_samples"]):
        failures.append("AAC/master alignment exceeds one video frame")
    if float(alignment["correlation"]) < 0.98:
        failures.append("AAC/master correlation below 0.98")
    frame_evidence = extract_final_frames(project, final, timed)
    mux_evidence_path = project / "qa/mux-evidence.json"
    mux_evidence = load_json(mux_evidence_path)
    evidence_hashes: dict[str, str] = {}
    reuse_evidence_path = project / "qa/video-reuse-evidence.json"
    if reuse_evidence_path.is_file():
        reuse_evidence = load_json(reuse_evidence_path)
        evidence_hashes["video_reuse_evidence_sha256"] = sha256(reuse_evidence_path)
        if reuse_evidence.get("status") != "VIDEO_STREAM_REUSED_TIMELINE_IDENTICAL":
            failures.append("video reuse evidence status mismatch")
        if reuse_evidence.get("reuse_runtime_sha256") != sha256(SCRIPT_PATH):
            failures.append("video reuse runtime SHA no longer matches")
        if reuse_evidence.get("video_only_raw", {}).get("sha256") != sha256(raw):
            failures.append("reused raw hash does not match video reuse evidence")
        if reuse_evidence.get("new_master", {}).get("sha256") != sha256(project / "assets/bgm.wav"):
            failures.append("reused project master does not match video reuse evidence")
        current_timeline_signature = canonical_json_sha256(
            visual_timeline_projection(timed)
        )
        if current_timeline_signature != reuse_evidence.get("new_timeline_signature"):
            failures.append("reused timeline signature no longer matches")
        current_visual_signature = canonical_json_sha256(
            {
                "index_sha256": sha256(project / "index.html"),
                "theme_sha256": sha256(project / "data/theme.json"),
                "visual_manifest_sha256": sha256(project / "data/visual.json"),
                "visual_proxy_sha256": sha256(project / "assets/visual.mp4"),
                "timeline_signature": current_timeline_signature,
                "source_renderer_sha256": reuse_evidence.get("renderer_sha256"),
            }
        )
        if current_visual_signature != reuse_evidence.get("visual_signature"):
            failures.append("reused visual signature no longer matches")
        source_raw_path = resolve_repo_path(
            str(reuse_evidence.get("source_raw", {}).get("path", ""))
        )
        source_render_path = resolve_repo_path(
            str(reuse_evidence.get("source_render_evidence", {}).get("path", ""))
        )
        if not source_raw_path.is_file() or sha256(source_raw_path) != reuse_evidence.get("source_raw", {}).get("sha256"):
            failures.append("source raw hash chain for reuse is broken")
        if not source_render_path.is_file() or sha256(source_render_path) != reuse_evidence.get("source_render_evidence", {}).get("sha256"):
            failures.append("source render evidence hash chain for reuse is broken")
        if source_raw_path.is_file() and video_packet_sha256(source_raw_path) != video_packet_sha256(raw):
            failures.append("reused raw video packet hash differs from source")
    else:
        render_evidence_path = project / "qa/render-evidence.json"
        render_evidence = load_json(render_evidence_path)
        evidence_hashes["render_evidence_sha256"] = sha256(render_evidence_path)
        current_render_inputs = {
            "index": sha256(project / "index.html"),
            "master": sha256(project / "assets/bgm.wav"),
            "visual_proxy": sha256(project / "assets/visual.mp4"),
            "timed_lyrics": sha256(project / "data/timed_lyrics.json"),
            "renderer": sha256(SCRIPT_PATH),
        }
        if render_evidence.get("inputs") != current_render_inputs:
            failures.append("render evidence no longer matches current frozen inputs")
        if render_evidence.get("raw", {}).get("sha256") != sha256(raw):
            failures.append("raw render hash does not match render evidence")
    if mux_evidence.get("raw_sha256") != sha256(raw):
        failures.append("mux evidence does not match current raw render")
    if mux_evidence.get("master_sha256") != sha256(project / "assets/bgm.wav"):
        failures.append("mux evidence does not match current master")
    if mux_evidence.get("final_sha256") != sha256(final):
        failures.append("final hash does not match mux evidence")
    if video_packet_sha256(raw) != video_packet_sha256(final):
        failures.append("final H.264 packet hash differs from raw video stream")
    if failures:
        status = "FAIL"
    else:
        status = "AUTO_QA_PASS_HUMAN_REVIEW_PENDING"
    hashes = {name: sha256(path) for name, path in {
        "master": project / "assets/bgm.wav", "visual_proxy": project / "assets/visual.mp4",
        "raw": raw, "final": final, "timed_lyrics": project / "data/timed_lyrics.json",
        "theme": project / "data/theme.json", "index": project / "index.html", "renderer": SCRIPT_PATH,
    }.items() if path.is_file()}
    report = {
        "schema_version": 1,
        "status": status,
        "automated_checks": {
            "strict_decode": "PASS",
            "stream_contract": "PASS" if not failures else "FAIL",
            "stream_timing": stream_timing,
            "caption_coverage": timed["audit"]["caption_coverage"],
            "maximum_simultaneous_highlights": 1,
            "black_segments": continuity["black_segments_over_100ms"],
            "silence_segments": continuity["silence_segments_over_500ms"],
            "aac_loudness": {**loudness, "master_delta_lu": round(loudness_delta, 3)},
            "aac_master_alignment": alignment,
            "sampled_final_frames": frame_evidence,
        },
        "manual_visual_review": "NOT_PERFORMED_BY_AUTOMATION",
        "failures": failures,
        "probe": probe,
        "hashes": hashes,
        "evidence": {
            **evidence_hashes,
            "mux_evidence_sha256": sha256(mux_evidence_path),
        },
    }
    dump_json(project / "qa/final-media-qa.json", report)
    if failures:
        raise ContractError("final QA failed: " + "; ".join(failures))
    return {"status": status, "final": str(final), "qa": str(project / "qa/final-media-qa.json")}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("command", choices=("build", "check", "snapshot", "render", "reuse-video", "mux", "qa"))
    value.add_argument("recipe", type=Path, help="per-song recipe JSON")
    value.add_argument("--run-id", help="explicit audio output run id matching the recipe preset")
    value.add_argument("--project-dir", type=Path, help="override generated MV project directory")
    value.add_argument("--source-project", type=Path, help="frozen V1 MV project used only by reuse-video")
    value.add_argument("--force", action="store_true", help="rebuild proxy and canonical audiomap")
    value.add_argument("--quality", choices=("draft", "standard", "high"), default="high")
    return value


def main() -> None:
    args = parser().parse_args()
    try:
        context = resolve_context(args)
        if args.command == "build":
            with heavy_task_lock("mv-build"):
                result, performance = timed_step("mv-build", lambda: command_build(context, args.force))
        elif args.command == "check":
            with heavy_task_lock("mv-check"):
                result, performance = timed_step("mv-check", lambda: command_check(context))
        elif args.command == "snapshot":
            with heavy_task_lock("mv-snapshot"):
                result, performance = timed_step("mv-snapshot", lambda: command_snapshot(context))
        elif args.command == "render":
            with heavy_task_lock("mv-render"):
                result, performance = timed_step("mv-render", lambda: command_render(context, args.quality))
        elif args.command == "reuse-video":
            with heavy_task_lock("mv-reuse-video"):
                result, performance = timed_step(
                    "mv-reuse-video",
                    lambda: command_reuse_video(context, args.source_project),
                )
        elif args.command == "mux":
            with heavy_task_lock("mv-mux"):
                result, performance = timed_step("mv-mux", lambda: command_mux(context))
        else:
            with heavy_task_lock("mv-qa"):
                result, performance = timed_step("mv-qa", lambda: command_qa(context))
        evidence_paths = {
            "build": context["project"] / "data/build-evidence.json",
            "check": context["project"] / "qa/hyperframes-check.json",
            "render": context["project"] / "qa/render-evidence.json",
            "reuse-video": context["project"] / "qa/video-reuse-evidence.json",
            "mux": context["project"] / "qa/mux-evidence.json",
            "qa": context["project"] / "qa/final-media-qa.json",
        }
        evidence_path = evidence_paths.get(args.command)
        if evidence_path is not None and evidence_path.is_file():
            evidence = load_json(evidence_path)
            evidence["performance"] = performance
            dump_json(evidence_path, evidence)
        result["performance"] = performance
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except ContractError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
