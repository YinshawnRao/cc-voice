#!/usr/bin/env python3
"""Aggregate Hajimi audio/MV evidence without upgrading human-review state."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import datetime as dt
from pathlib import Path
from typing import Any


HAJIMI_ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def collect(song_id: str, run_id: str, mv_project: str | None = None) -> dict[str, Any]:
    audio_dir = HAJIMI_ROOT / "outputs" / song_id / run_id
    mv_dir = HAJIMI_ROOT / "mv" / (mv_project or f"{song_id}-hajimi-mv-v1")
    required = {
        "song_manifest": audio_dir / "song-manifest.json",
        "event_draft": audio_dir / "event-draft.json",
        "render_report": audio_dir / "render-report.json",
        "audio_qa": audio_dir / "audio-qa-report.json",
        "listening_review": audio_dir / "listening-review.json",
        "timed_lyrics": mv_dir / "data/timed_lyrics.json",
        "theme": mv_dir / "data/theme.json",
        "visual": mv_dir / "data/visual.json",
        "mv_qa": mv_dir / "qa/final-media-qa.json",
    }
    missing = [str(path) for path in required.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing package evidence: " + ", ".join(missing))
    audio_qa = load(required["audio_qa"])
    song_manifest = load(required["song_manifest"])
    listening_review = load(required["listening_review"])
    mv_qa = load(required["mv_qa"])
    if audio_qa.get("overall_status") != "PASS" or audio_qa.get("machine_status") != "AUDIO_AUTO_QA_PASS":
        raise RuntimeError("Audio machine QA is not a verified PASS")
    if mv_qa.get("status") != "AUTO_QA_PASS_HUMAN_REVIEW_PENDING" or mv_qa.get("failures"):
        raise RuntimeError("MV machine QA is not a verified PASS")
    checks = mv_qa.get("automated_checks", {})
    if checks.get("strict_decode") != "PASS" or checks.get("stream_contract") != "PASS":
        raise RuntimeError("MV decode or stream contract did not pass")
    audio_human_approved = (
        song_manifest.get("status") == "AUDIO_HUMAN_APPROVED"
        and listening_review.get("status") == "AUDIO_HUMAN_APPROVED"
        and listening_review.get("human_review", {}).get("approved_for_internal_mv_preview") is True
    )
    if not audio_human_approved:
        raise RuntimeError("Audio human approval evidence is incomplete")

    repo_root = HAJIMI_ROOT.parent
    master_relative = audio_qa["checks"]["format_and_timeline"]["master"]["path"]
    master = repo_root / master_relative
    final = mv_dir / "renders" / f"{song_id}-hajimi-mv-v1.mp4"
    for label, path in (("master", master), ("final_mp4", final)):
        if not path.is_file():
            raise FileNotFoundError(f"Missing {label}: {path}")
    master_hash = sha256(master)
    final_hash = sha256(final)
    audio_master_hash = audio_qa["checks"]["format_and_timeline"]["master"]["sha256"]
    if master_hash != audio_master_hash or master_hash != mv_qa.get("hashes", {}).get("master"):
        raise RuntimeError("Authoritative master hash chain is inconsistent")
    if final_hash != mv_qa.get("hashes", {}).get("final"):
        raise RuntimeError("Final MP4 hash does not match final-media QA")

    required["master"] = master
    required["final_mp4"] = final
    artifacts = {
        name: {
            "path": str(path.relative_to(HAJIMI_ROOT)) if path.is_relative_to(HAJIMI_ROOT) else str(path.relative_to(repo_root)),
            "sha256": sha256(path),
            "size_bytes": path.stat().st_size,
        }
        for name, path in required.items()
    }
    return {
        "schema_version": 1,
        "song_id": song_id,
        "run_id": run_id,
        "status": "MV_AUTO_QA_PASS_HUMAN_REVIEW_PENDING",
        "audio_qa_status": audio_qa.get("machine_status"),
        "mv_qa_status": mv_qa.get("status"),
        "artifacts": artifacts,
        "human_gates": {
            "event_approved": False,
            "audio_human_approved": True,
            "mv_preview_approved": False,
            "packaged": False,
        },
        "policy": "Automation may collect evidence but cannot manufacture human approval.",
    }


def cross_song_regression(run_specs: list[str]) -> dict[str, Any]:
    if len(run_specs) != 3:
        raise RuntimeError("V1.1 regression requires exactly three song_id:run_id entries")
    songs: list[dict[str, Any]] = []
    baseline_values: list[float] = []
    candidate_values: list[float] = []
    seen: set[str] = set()
    for spec in run_specs:
        if ":" not in spec:
            raise RuntimeError(f"Invalid --audio-run {spec!r}; expected song_id:run_id")
        song_id, run_id = spec.split(":", 1)
        if song_id in seen:
            raise RuntimeError(f"Duplicate regression song: {song_id}")
        seen.add(song_id)
        path = HAJIMI_ROOT / "outputs" / song_id / run_id / "audio-qa-report.json"
        if not path.is_file():
            raise FileNotFoundError(f"Missing audio QA: {path}")
        report = load(path)
        if report.get("event_preset") != "hajimi-note-event-v1.1":
            raise RuntimeError(f"Regression run is not V1.1: {path}")
        check = report.get("checks", {}).get("v1p1_regression", {})
        if report.get("overall_status") != "PASS" or check.get("status") != "PASS":
            raise RuntimeError(f"Per-song V1.1 QA is not PASS: {path}")
        baseline = [float(value) for value in check["baseline"]["bubble"]["values"]]
        candidate = [float(value) for value in check["candidate"]["bubble"]["values"]]
        if not baseline or not candidate:
            raise RuntimeError(f"Regression bubble samples are empty: {path}")
        baseline_values.extend(baseline)
        candidate_values.extend(candidate)
        songs.append(
            {
                "song_id": song_id,
                "run_id": run_id,
                "audio_qa": {
                    "path": str(path.relative_to(HAJIMI_ROOT)),
                    "sha256": sha256(path),
                },
                "baseline_median": check["baseline"]["bubble"]["median"],
                "candidate_median": check["candidate"]["bubble"]["median"],
                "relative_median_reduction": check["candidate"]["bubble_relative_median_reduction"],
                "status": "PASS",
            }
        )
    baseline_median = float(statistics.median(baseline_values))
    candidate_median = float(statistics.median(candidate_values))
    relative_reduction = 1.0 - candidate_median / max(baseline_median, 1e-12)
    passed = relative_reduction >= 0.40
    report = {
        "schema_version": 1,
        "artifact_kind": "hajimi-v1p1-three-song-regression",
        "status": "PASS" if passed else "FAIL",
        "songs": songs,
        "combined": {
            "baseline_eligible_group_count": len(baseline_values),
            "candidate_eligible_group_count": len(candidate_values),
            "baseline_median": round(baseline_median, 6),
            "candidate_median": round(candidate_median, 6),
            "relative_median_reduction": round(relative_reduction, 6),
            "minimum_required": 0.40,
        },
        "human_listening": "PENDING",
        "created_at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    output = HAJIMI_ROOT / "outputs" / "regression" / "20260820_hajimi_v1p1_three_song_regression.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not passed:
        raise RuntimeError(f"Three-song combined bubble regression failed; see {output}")
    return {"output": str(output), "status": "PASS", "report": report}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("audit", "package", "regression"))
    parser.add_argument("song_id", nargs="?")
    parser.add_argument("--run-id")
    parser.add_argument("--mv-project")
    parser.add_argument("--audio-run", action="append", default=[])
    args = parser.parse_args()
    if args.command == "regression":
        result = cross_song_regression(args.audio_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if not args.song_id or not args.run_id:
        parser.error("audit/package require song_id and explicit --run-id")
    manifest = collect(args.song_id, args.run_id, args.mv_project)
    if args.command == "package":
        raise SystemExit(
            "Refusing PACKAGED: EVENT_APPROVED, AUDIO_HUMAN_APPROVED and "
            "MV_PREVIEW_APPROVED are pending"
        )
    output = HAJIMI_ROOT / "outputs" / args.song_id / args.run_id / "artifact-manifest.json"
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(output), "status": manifest["status"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
