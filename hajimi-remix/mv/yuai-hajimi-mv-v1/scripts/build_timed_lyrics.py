#!/usr/bin/env python3
"""Build the canonical Rain Love Hajimi lyric timeline from render evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
HAJIMI_DIR = PROJECT_DIR.parent.parent
DEFAULT_REPORT = HAJIMI_DIR / "outputs/yuai_hajimi_event_v1/render-report.json"
DEFAULT_EVENTS = HAJIMI_DIR / "outputs/yuai_hajimi_event_v1/event-draft.json"
DEFAULT_AUDIO = PROJECT_DIR / "assets/bgm.wav"
DEFAULT_OUTPUT = PROJECT_DIR / "data/timed_lyrics.json"

LINE_GROUP_RANGES = (
    (1, 7),
    (8, 14),
    (15, 21),
    (22, 28),
    (29, 35),
    (36, 39),
    (40, 46),
    (47, 53),
    (54, 60),
    (61, 67),
    (68, 70),
    (71, 77),
    (78, 84),
    (85, 85),
    (86, 92),
    (93, 97),
)

PHRASE_GROUP_RANGES = ((1, 39), (40, 70), (71, 85), (86, 97))

SECTION_IDENTITIES = (
    ("chorus-a", "CHORUS A"),
    ("build", "BUILD"),
    ("climax", "CLIMAX"),
    ("outro", "OUTRO"),
)


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_timeline(report_path: Path, events_path: Path, audio_path: Path) -> dict:
    report = load_json(report_path)
    event_draft = load_json(events_path)
    groups = report["variants"]["hybrid"]["groups"]
    events = event_draft["events"]
    duration = float(report["duration_seconds"])

    if [group["group_id"] for group in groups] != list(range(1, 98)):
        raise ValueError("Expected exactly 97 sequential syllable groups")

    events_by_id = {event["event_id"]: event for event in events}
    if sorted(events_by_id) != list(range(1, 159)):
        raise ValueError("Expected exactly 158 sequential note events")

    syllables = []
    covered_note_ids: list[int] = []
    for index, group in enumerate(groups):
        group_id = int(group["group_id"])
        note_ids = [int(event_id) for event_id in group["note_event_ids"]]
        covered_note_ids.extend(note_ids)
        phrase_ids = {int(events_by_id[event_id]["phrase_id"]) for event_id in note_ids}
        if len(phrase_ids) != 1:
            raise ValueError(f"Group {group_id} spans multiple phrases: {phrase_ids}")

        start = float(group["attack_start_seconds"])
        raw_end = float(group["render_end_seconds"])
        next_start = (
            float(groups[index + 1]["attack_start_seconds"])
            if index + 1 < len(groups)
            else duration
        )
        highlight_end = min(raw_end, next_start, duration)
        if not 0.0 <= start < highlight_end <= duration:
            raise ValueError(
                f"Invalid highlight interval for group {group_id}: {start}..{highlight_end}"
            )

        qa_flags = ["ONSET_REQUIRES_HUMAN_AUDITION"] if group_id == 25 else []
        syllables.append(
            {
                "id": f"g{group_id:03d}",
                "group_id": group_id,
                "text": group["token"],
                "phrase_id": next(iter(phrase_ids)),
                "line_id": None,
                "start_seconds": round(start, 6),
                "end_seconds": round(highlight_end, 6),
                "raw_audio_end_seconds": round(raw_end, 6),
                "stable_start_seconds": round(float(group["stable_start_seconds"]), 6),
                "stable_end_seconds": round(float(group["stable_end_seconds"]), 6),
                "note_event_ids": note_ids,
                "qa_flags": qa_flags,
            }
        )

    if sorted(covered_note_ids) != list(range(1, 159)) or len(covered_note_ids) != 158:
        raise ValueError("Syllable groups must cover every note event exactly once")

    lines = []
    line_by_group_id: dict[int, str] = {}
    for line_number, (first_group_id, last_group_id) in enumerate(
        LINE_GROUP_RANGES, start=1
    ):
        line_id = f"line-{line_number:02d}"
        selected = syllables[first_group_id - 1 : last_group_id]
        phrase_ids = {item["phrase_id"] for item in selected}
        if len(phrase_ids) != 1:
            raise ValueError(f"Line {line_id} crosses a phrase reset")
        next_line_start = (
            syllables[LINE_GROUP_RANGES[line_number][0] - 1]["start_seconds"]
            if line_number < len(LINE_GROUP_RANGES)
            else duration
        )
        phrase_id = next(iter(phrase_ids))
        phrase_end = (18.28, 32.58, 42.295, duration)[phrase_id - 1]
        display_end = min(float(next_line_start), phrase_end)
        lines.append(
            {
                "id": line_id,
                "phrase_id": phrase_id,
                "text": "".join(item["text"] for item in selected),
                "group_ids": list(range(first_group_id, last_group_id + 1)),
                "start_seconds": selected[0]["start_seconds"],
                "end_seconds": round(display_end, 6),
            }
        )
        for group_id in range(first_group_id, last_group_id + 1):
            line_by_group_id[group_id] = line_id

    for syllable in syllables:
        syllable["line_id"] = line_by_group_id[syllable["group_id"]]

    phrase_ends = (18.28, 32.58, 42.295, duration)
    phrases = []
    for phrase_id, (first_group_id, last_group_id) in enumerate(
        PHRASE_GROUP_RANGES, start=1
    ):
        phrases.append(
            {
                "id": phrase_id,
                "group_ids": list(range(first_group_id, last_group_id + 1)),
                "start_seconds": syllables[first_group_id - 1]["start_seconds"],
                "end_seconds": phrase_ends[phrase_id - 1],
                "text": "".join(
                    item["text"] for item in syllables[first_group_id - 1 : last_group_id]
                ),
            }
        )

    for previous, current in zip(syllables, syllables[1:]):
        if previous["end_seconds"] > current["start_seconds"]:
            raise ValueError(
                f"Highlight overlap: {previous['id']} and {current['id']}"
            )

    return {
        "schema_version": 1,
        "timebase": "seconds",
        "audio": {
            "path": "assets/bgm.wav",
            "sha256": sha256(audio_path),
            "duration_seconds": duration,
            "sample_rate": int(report["sample_rate"]),
            "channels": 2,
        },
        "source_window": {
            "start_seconds": float(event_draft["window"]["start_seconds"]),
            "end_seconds": float(event_draft["window"]["end_seconds"]),
        },
        "display": {
            "title": "雨爱",
            "title_en": "RAIN LOVE",
            "version": "HAJIMI NOTE / EVENT V1",
            "credit": "哈基米南北绿豆",
            "status": "INTERNAL EXPERIENCE DEMO",
        },
        # Visual section boundaries intentionally reuse the canonical phrase
        # boundaries. This prevents captions, palettes, and phrase resets from
        # drifting onto separate time axes.
        "sections": [
            {
                "id": section_id,
                "label": label,
                "start_seconds": phrase["start_seconds"],
                "end_seconds": phrase["end_seconds"],
            }
            for (section_id, label), phrase in zip(SECTION_IDENTITIES, phrases)
        ],
        "phrases": phrases,
        "lines": lines,
        "syllables": syllables,
        "audit": {
            "render_report_path": str(report_path.relative_to(HAJIMI_DIR)),
            "render_report_sha256": sha256(report_path),
            "event_draft_path": str(events_path.relative_to(HAJIMI_DIR)),
            "event_draft_sha256": sha256(events_path),
            "syllable_group_count": len(syllables),
            "note_event_count": len(events),
            "highlight_overlap_count": 0,
            "caption_coverage": "97/97",
            "manual_audition_flags": ["g025"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--audio", type=Path, default=DEFAULT_AUDIO)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    timeline = build_timeline(
        args.report.resolve(), args.events.resolve(), args.audio.resolve()
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "syllables": len(timeline["syllables"]),
                "lines": len(timeline["lines"]),
                "phrases": len(timeline["phrases"]),
                "duration_seconds": timeline["audio"]["duration_seconds"],
                "audio_sha256": timeline["audio"]["sha256"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
