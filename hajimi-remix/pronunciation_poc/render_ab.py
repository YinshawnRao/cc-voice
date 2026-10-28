#!/usr/bin/env python3
"""Render an equal-loudness V1 versus V1.1 articulation listening pair.

This is deliberately outside the shared production CLI.  It reuses one frozen
event timeline, renders the canonical hybrid and the explicit experimental
variant serially in one process, and never touches MV assets.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any, Iterator


for _name in (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS", "MKL_NUM_THREADS",
):
    os.environ[_name] = "2"

SCRIPT = Path(__file__).resolve()
HAJIMI_ROOT = SCRIPT.parents[1]
REPO_ROOT = HAJIMI_ROOT.parent
RENDERER = HAJIMI_ROOT / "greenlight_poc" / "render_demo.py"
DEFAULT_PRESET = HAJIMI_ROOT / "presets" / "hajimi-note-event-v1.1-poc1.json"
DEFAULT_FIXTURE = HAJIMI_ROOT / "work" / "yuai_event_v1"
DEFAULT_BASELINE_DRY = DEFAULT_FIXTURE / "render-v1" / "yuai_hybrid_dry.wav"
DEFAULT_OUTPUT = (
    HAJIMI_ROOT / "outputs" / "pronunciation-poc" / "yuai"
    / "20260814_yuai_hev1p1_poc01"
)
JP_VOICE = HAJIMI_ROOT / "assets" / "voicebank" / "hachimi_o_nameruto_phrase.wav"
ZH_VOICE = HAJIMI_ROOT / "assets" / "voicebank" / "hajimi_nanbei_lvdou_zh_yushu.wav"
VARIANT_A = "hybrid"
VARIANT_B = "hybrid_v1p1_poc1"
TARGET_LUFS = -9.5
TARGET_TP = -1.0
TARGET_LRA = 11.0
MASTER_LIMIT_TP = -1.7
MASTER_MAX_ATTEMPTS = 4
THREAD_ENV = {
    "OMP_NUM_THREADS": "2",
    "OPENBLAS_NUM_THREADS": "2",
    "VECLIB_MAXIMUM_THREADS": "2",
    "NUMEXPR_NUM_THREADS": "2",
    "MKL_NUM_THREADS": "2",
}


class PocError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str | Path]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(THREAD_ENV)
    completed = subprocess.run(
        [str(item) for item in command], env=env, capture_output=True,
        text=True, check=False,
    )
    if completed.returncode != 0:
        raise PocError(
            f"command failed ({completed.returncode}): {' '.join(map(str, command))}\n"
            f"{completed.stderr[-4000:]}"
        )
    return completed


@contextlib.contextmanager
def serial_lock() -> Iterator[None]:
    lock_path = HAJIMI_ROOT / "work" / ".pronunciation-poc.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PocError("another Hajimi audio task holds the POC lock") from exc
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def python_for_audio() -> Path:
    candidate = REPO_ROOT / "frameworks" / "rvc-webui-macos" / ".venv" / "bin" / "python"
    return candidate if candidate.is_file() else Path(sys.executable)


def wav_contract(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as source:
        return {
            "sample_rate": source.getframerate(),
            "channels": source.getnchannels(),
            "sample_width_bytes": source.getsampwidth(),
            "frame_count": source.getnframes(),
            "duration_seconds": source.getnframes() / source.getframerate(),
        }


def loudnorm_measure(path: Path) -> dict[str, float]:
    completed = run([
        "ffmpeg", "-hide_banner", "-v", "info", "-nostats", "-threads", "2",
        "-filter_threads", "2", "-i", path,
        "-af", f"loudnorm=I={TARGET_LUFS}:LRA={TARGET_LRA}:TP={TARGET_TP}:print_format=json",
        "-f", "null", "-",
    ])
    candidates = re.findall(r'\{\s*"input_i".*?\}', completed.stderr, flags=re.DOTALL)
    if not candidates:
        raise PocError(f"ffmpeg loudnorm emitted no JSON for {path}")
    raw = json.loads(candidates[-1])
    return {key: float(raw[key]) for key in (
        "input_i", "input_tp", "input_lra", "input_thresh", "target_offset"
    )}


def normalize(path: Path, destination: Path, frame_count: int) -> dict[str, float]:
    """Create an equal-loudness comparison without violating true peak.

    Linear loudnorm cannot reach the target on these crest-heavy premaster
    files while also staying below -1 dBTP.  Use the same measured,
    oversampled limiter shape as the production wrapper and keep additional
    headroom so a later AAC preview remains safe.
    """

    measured = loudnorm_measure(path)
    gain_db = max(-12.0, min(24.0, TARGET_LUFS - measured["input_i"] + 2.5))
    limit_linear = 10.0 ** (MASTER_LIMIT_TP / 20.0)
    final: dict[str, float] | None = None
    for _attempt in range(MASTER_MAX_ATTEMPTS):
        level_in = 10.0 ** (gain_db / 20.0)
        graph = (
            "aresample=192000,"
            f"alimiter=level_in={level_in:.9f}:limit={limit_linear:.9f}:"
            "attack=5:release=80:level=false:latency=true,"
            f"aresample=44100,apad=whole_len={frame_count},"
            f"atrim=end_sample={frame_count},asetpts=PTS-STARTPTS"
        )
        run([
            "ffmpeg", "-hide_banner", "-v", "error", "-nostats", "-threads", "2",
            "-filter_threads", "2", "-i", path, "-af", graph,
            "-ar", "44100", "-ac", "2", "-c:a", "pcm_s24le",
            "-map_metadata", "-1", "-y", destination,
        ])
        final = loudnorm_measure(destination)
        if (
            TARGET_LUFS - 0.5 <= final["input_i"] <= TARGET_LUFS + 0.5
            and final["input_tp"] <= TARGET_TP + 1e-6
        ):
            return final
        gain_db = max(-12.0, min(24.0, gain_db + TARGET_LUFS - final["input_i"]))
    raise PocError(f"adaptive mastering did not pass: {final}")


def timeline_signature(groups: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    keys = (
        "group_id", "token", "attack_start_seconds", "stable_start_seconds",
        "stable_end_seconds", "render_end_seconds", "note_event_ids",
    )
    return [tuple(json.dumps(group[key], sort_keys=True) for key in keys) for group in groups]


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-draft", type=Path, default=DEFAULT_FIXTURE / "event-draft.json")
    parser.add_argument("--guide", type=Path, default=DEFAULT_FIXTURE / "yuai_chorus_guide.wav")
    parser.add_argument(
        "--instrumental", type=Path,
        default=DEFAULT_FIXTURE / "yuai_chorus_instrumental.wav",
    )
    parser.add_argument("--jp-voice", type=Path, default=JP_VOICE)
    parser.add_argument("--zh-voice", type=Path, default=ZH_VOICE)
    parser.add_argument("--preset", type=Path, default=DEFAULT_PRESET)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--slug", default="yuai_pronunciation_ab")
    # Keep the accepted Rain Love section automation identical to the V1
    # golden render; articulation is the only A/B variable.
    parser.add_argument("--climax-seconds", type=float, default=32.85)
    parser.add_argument("--bridge-gap-seconds", type=float, default=0.280)
    parser.add_argument("--force", action="store_true")
    return parser


def main() -> int:
    args = make_parser().parse_args()
    required = (
        args.event_draft, args.guide, args.instrumental, args.jp_voice,
        args.zh_voice, args.preset, RENDERER,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise PocError(f"missing inputs: {missing}")
    preset = json.loads(args.preset.read_text(encoding="utf-8"))
    if preset.get("preset_id") != "hajimi-note-event-v1.1-poc1":
        raise PocError("unexpected POC preset")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    final_paths = {
        "a": output_dir / "A_v1.wav",
        "b": output_dir / "B_v1p1_poc1.wav",
        "ab": output_dir / "AB_equal_loudness.wav",
        "focus": output_dir / "AB_focus_two_cycles.wav",
        "report": output_dir / "ab-report.json",
    }
    if not args.force and any(path.exists() for path in final_paths.values()):
        raise PocError("POC outputs already exist; choose a new directory or pass --force")

    started = time.perf_counter()
    with serial_lock():
        run([
            python_for_audio(), RENDERER,
            "--event-draft", args.event_draft,
            "--guide", args.guide,
            "--instrumental", args.instrumental,
            "--jp-voice", args.jp_voice,
            "--zh-voice", args.zh_voice,
            "--output-dir", output_dir,
            "--sample-rate", "44100",
            "--climax-seconds", str(args.climax_seconds),
            "--bridge-gap-seconds", str(args.bridge_gap_seconds),
            "--slug", args.slug,
            "--title", "Rain Love pronunciation V1 versus V1.1 POC",
            "--variants", VARIANT_A, VARIANT_B,
            "--articulation-preset", args.preset,
        ])
        render_report_path = output_dir / "render-report.json"
        report = json.loads(render_report_path.read_text(encoding="utf-8"))
        variant_a = report["variants"][VARIANT_A]
        variant_b = report["variants"][VARIANT_B]
        if timeline_signature(variant_a["groups"]) != timeline_signature(variant_b["groups"]):
            raise PocError("A/B group timing changed")
        if any(
            float(group.get("applied_group_sustain_ratio", 0.0)) > 0.30 + 1e-9
            for group in variant_b["groups"]
        ):
            raise PocError("candidate exceeded the 30% sustain cap")

        premaster_a = Path(variant_a["files"]["premaster"])
        premaster_b = Path(variant_b["files"]["premaster"])
        if not premaster_a.is_absolute():
            premaster_a = (REPO_ROOT / premaster_a).resolve()
        if not premaster_b.is_absolute():
            premaster_b = (REPO_ROOT / premaster_b).resolve()
        frame_count = wav_contract(premaster_a)["frame_count"]
        if wav_contract(premaster_b)["frame_count"] != frame_count:
            raise PocError("candidate changed frame count")

        rendered_a_dry = Path(variant_a["files"]["dry"])
        if not rendered_a_dry.is_absolute():
            rendered_a_dry = (REPO_ROOT / rendered_a_dry).resolve()
        if not DEFAULT_BASELINE_DRY.is_file():
            raise PocError(f"missing accepted V1 dry reference: {DEFAULT_BASELINE_DRY}")
        baseline_dry_identical = sha256(rendered_a_dry) == sha256(DEFAULT_BASELINE_DRY)
        if not baseline_dry_identical:
            raise PocError("default V1 dry render is not PCM-file identical to the accepted baseline")
        loudness_a = normalize(premaster_a, final_paths["a"], frame_count)
        loudness_b = normalize(premaster_b, final_paths["b"], frame_count)
        if abs(loudness_a["input_i"] - loudness_b["input_i"]) > 0.10:
            raise PocError("A/B loudness delta exceeds 0.10 LU")
        if max(loudness_a["input_tp"], loudness_b["input_tp"]) > TARGET_TP + 1e-6:
            raise PocError("A/B true peak exceeds -1 dBTP")

        shutil.copy2(Path(variant_a["files"]["vocal"]), output_dir / "A_vocal_only.wav")
        shutil.copy2(Path(variant_b["files"]["vocal"]), output_dir / "B_vocal_only.wav")
        run([
            "ffmpeg", "-hide_banner", "-v", "error", "-threads", "2",
            "-filter_threads", "2", "-i", final_paths["a"], "-i", final_paths["b"],
            "-filter_complex",
            "[0:a]asetpts=PTS-STARTPTS[a];anullsrc=r=44100:cl=stereo:d=1[s];"
            "[1:a]asetpts=PTS-STARTPTS[b];[a][s][b]concat=n=3:v=0:a=1[out]",
            "-map", "[out]", "-c:a", "pcm_s24le", "-y", final_paths["ab"],
        ])
        # Two complete token cycles including the known g25 "南" review point.
        run([
            "ffmpeg", "-hide_banner", "-v", "error", "-threads", "2",
            "-filter_threads", "2", "-i", final_paths["a"], "-i", final_paths["b"],
            "-filter_complex",
            "[0:a]atrim=start=8.95:end=15.98,asetpts=PTS-STARTPTS[a];"
            "anullsrc=r=44100:cl=stereo:d=0.7[s];"
            "[1:a]atrim=start=8.95:end=15.98,asetpts=PTS-STARTPTS[b];"
            "[a][s][b]concat=n=3:v=0:a=1[out]",
            "-map", "[out]", "-c:a", "pcm_s24le", "-y", final_paths["focus"],
        ])
        for path in final_paths.values():
            if path == final_paths["report"]:
                continue
            run([
                "ffmpeg", "-v", "error", "-xerror", "-threads", "2",
                "-i", path, "-f", "null", "-",
            ])

        usage = resource.getrusage(resource.RUSAGE_SELF)
        evidence = {
            "schema_version": 1,
            "status": "POC_MACHINE_CHECKS_PASS_HUMAN_LISTENING_PENDING",
            "preset": {
                "path": str(args.preset), "sha256": sha256(args.preset),
                "id": preset["preset_id"], "version": preset["preset_version"],
            },
            "inputs": {
                path.name: {"path": str(path), "sha256": sha256(path)}
                for path in required[:-1]
            },
            "invariants": {
                "timeline_identical": True,
                "frame_count_identical": True,
                "accepted_v1_dry_file_identical": baseline_dry_identical,
                "accepted_v1_dry_sha256": sha256(DEFAULT_BASELINE_DRY),
                "maximum_nucleus_ratio": max(
                    float(group.get("applied_group_sustain_ratio", 0.0))
                    for group in variant_b["groups"]
                ),
                "fallback_group_count": variant_b["articulation_poc"]["fallback_group_count"],
            },
            "loudness": {"a": loudness_a, "b": loudness_b},
            "outputs": {
                name: {"path": str(path), "sha256": sha256(path)}
                for name, path in final_paths.items() if name != "report"
            },
            "listening_order": {
                "AB_equal_loudness.wav": "A full mix, 1.0s silence, then B full mix",
                "AB_focus_two_cycles.wav": (
                    "A source 8.95-15.98s, 0.7s silence, then B source 8.95-15.98s; "
                    "includes two token cycles and g25 南"
                ),
            },
            "performance": {
                "wall_seconds": round(time.perf_counter() - started, 3),
                "orchestrator_max_rss_mb": round(
                    resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0 / 1024.0, 2
                ),
                "renderer": report.get("performance_measurement"),
                "numerical_threads": 2,
                "ffmpeg_threads": 2,
                "serial_lock": True,
            },
            "manual_listening": "NOT_PERFORMED_BY_AUTOMATION",
        }
        final_paths["report"].write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps({"status": evidence["status"], "output_dir": str(output_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PocError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
