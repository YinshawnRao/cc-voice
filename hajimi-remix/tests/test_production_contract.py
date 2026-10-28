#!/usr/bin/env python3
"""Fast contract checks for Hajimi production V1 and V1.1."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


HAJIMI_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = HAJIMI_ROOT / "schemas"
PRESET_DIR = HAJIMI_ROOT / "presets"
SHARED_SOURCES = (
    HAJIMI_ROOT / "scripts" / "hajimi_audio.py",
    HAJIMI_ROOT / "scripts" / "qa_package.py",
    HAJIMI_ROOT / "mv" / "template-v1" / "scripts" / "hajimi_mv.py",
    HAJIMI_ROOT / "mv" / "template-v1" / "scripts" / "hajimi_mv_runtime.py",
)

EXPECTED_SCHEMAS = {
    "song-manifest.schema.json": "Hajimi Materialized Song Manifest V1",
    "song-recipe.schema.json": "Hajimi Editable Song Recipe V1",
    "video-reuse-evidence.schema.json": "Hajimi Video Stream Reuse Evidence V1",
    "event-approved.schema.json": "Hajimi Approved Note, Syllable and Phrase Events V1",
    "timed-lyrics.schema.json": "Hajimi Timed Lyrics V1",
    "theme.schema.json": "Hajimi Rain Window Theme V1",
    "visual.schema.json": "Hajimi Visual Window Evidence V1",
}

EXPECTED_PRESETS = {
    "hajimi-note-event-v1.json": "hajimi-note-event-v1",
    "hajimi-note-event-v1.1.json": "hajimi-note-event-v1.1",
    "hajimi-pop-v1.json": "hajimi-pop-v1",
    "hajimi-rain-window-v1.json": "hajimi-rain-window-v1",
}

# These tokens identify the Rain Love golden fixture or its one-off constants.
# They must never leak into the shared renderer/QA entry points.
FORBIDDEN_SHARED_SOURCE_PATTERNS = {
    "rain_love_cn": r"雨爱",
    "rain_love_en": r"RAIN LOVE",
    "rain_love_romanized": r"YU AI",
    "rain_love_slug": r"\byuai\b",
    "rain_love_visual": r"yuai-official-mv-window",
    "rain_love_duration": r"\b50\.15\b",
    "rain_love_phrase_boundaries": r"\b(?:18\.28|32\.58|42\.295)\b",
    "rain_love_counts": r"\b(?:97|158)\b",
    "rain_love_bpm": r"\b152\s*BPM\b",
}


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return value


class ProductionContractTest(unittest.TestCase):
    def test_schema_files_parse_and_use_draft_2020_12(self) -> None:
        for filename, title in EXPECTED_SCHEMAS.items():
            with self.subTest(schema=filename):
                schema = load_json(SCHEMA_DIR / filename)
                self.assertEqual(
                    schema.get("$schema"),
                    "https://json-schema.org/draft/2020-12/schema",
                )
                self.assertEqual(schema.get("title"), title)
                self.assertEqual(schema.get("type"), "object")
                self.assertFalse(schema.get("additionalProperties", True))
                self.assertTrue(schema.get("required"))

    def test_schemas_declare_fail_closed_contracts(self) -> None:
        song = load_json(SCHEMA_DIR / "song-manifest.schema.json")
        recipe = load_json(SCHEMA_DIR / "song-recipe.schema.json")
        reuse = load_json(SCHEMA_DIR / "video-reuse-evidence.schema.json")
        event = load_json(SCHEMA_DIR / "event-approved.schema.json")
        timed = load_json(SCHEMA_DIR / "timed-lyrics.schema.json")
        theme = load_json(SCHEMA_DIR / "theme.schema.json")
        visual = load_json(SCHEMA_DIR / "visual.schema.json")

        self.assertEqual(
            event["properties"]["status"], {"const": "EVENT_APPROVED"}
        )
        self.assertEqual(
            song["properties"]["audio"]["properties"]["bridge_gap_seconds"]["maximum"],
            0.28,
        )
        self.assertEqual(
            recipe["properties"]["presets"]["properties"]["audio"]["enum"],
            ["hajimi-note-event-v1", "hajimi-note-event-v1.1"],
        )
        self.assertEqual(
            reuse["properties"]["status"],
            {"const": "VIDEO_STREAM_REUSED_TIMELINE_IDENTICAL"},
        )
        self.assertEqual(
            timed["properties"]["audit"]["properties"]["highlight_overlap_count"],
            {"const": 0},
        )
        self.assertEqual(
            theme["properties"]["typography"]["properties"]["font_preflight_status"],
            {"const": "PASS"},
        )
        source_branch = visual["allOf"][0]["then"]
        self.assertEqual(source_branch["required"], ["source", "alignment", "proxy"])
        self.assertEqual(
            visual["$defs"]["proxy"]["properties"]["audio_stream_count"],
            {"const": 0},
        )

    def test_preset_identity_and_version_are_frozen(self) -> None:
        for filename, preset_id in EXPECTED_PRESETS.items():
            with self.subTest(preset=filename):
                preset = load_json(PRESET_DIR / filename)
                self.assertEqual(preset.get("schema_version"), 1)
                self.assertEqual(preset.get("preset_id"), preset_id)
                self.assertEqual(preset.get("preset_version"), 1)
                self.assertEqual(preset.get("status"), "FROZEN_INTERNAL_BASELINE")

    def test_event_and_mix_preset_match_v1_standard(self) -> None:
        event = load_json(PRESET_DIR / "hajimi-note-event-v1.json")
        mix = load_json(PRESET_DIR / "hajimi-pop-v1.json")

        self.assertEqual(event["scope"]["maximum_window_seconds"], 75)
        self.assertEqual(event["scope"]["production_variant"], "hybrid")
        self.assertEqual(event["extractor"]["hop_seconds"], 0.01)
        self.assertEqual(event["extractor"]["minimum_stable_note_seconds"], 0.07)
        self.assertEqual(event["event_grouping"]["maximum_vowel_bridge_seconds"], 0.28)
        self.assertEqual(event["source_policy"]["pitch_failure_policy"], "FAIL_CLOSED")
        self.assertEqual(event["source_policy"]["input_length_mismatch_policy"], "FAIL_CLOSED")
        self.assertEqual(event["resources"]["numerical_library_threads"], 2)
        self.assertEqual(event["resources"]["concurrent_heavy_tasks"], 1)

        self.assertEqual(mix["vocal_filter"], {"highpass_hz": 90, "lowpass_hz": 14500})
        self.assertEqual(mix["compressor"]["ratio"], 3)
        self.assertEqual(mix["instrument_duck"]["maximum_reduction_db"], 1.6)
        self.assertEqual(mix["master"]["integrated_lufs_target"], -9.5)
        self.assertEqual(mix["master"]["true_peak_dbtp_max"], -1)

    def test_v1p1_microcycle_contract_is_frozen_and_fail_closed(self) -> None:
        event = load_json(PRESET_DIR / "hajimi-note-event-v1.1.json")
        extension = event["sustain_extension"]
        self.assertEqual(event["extends"], "hajimi-note-event-v1")
        self.assertEqual(event["scope"]["production_variant"], "hybrid_v1p1")
        self.assertEqual(extension["repitch_scope"], "whole_group_frozen_v1")
        self.assertEqual(extension["production_selected_ncc_min"], 0.8)
        self.assertEqual(extension["production_split_half_delta_percent_max"], 5.0)
        self.assertEqual(extension["minimum_source_periods"], 10)
        self.assertEqual(extension["microcycle_periods"], 8)
        self.assertEqual(extension["overlap_periods"], 2)
        self.assertEqual(
            extension["f0_safety_policy"],
            "EXPLICIT_V1_GROUP_FALLBACK_AND_RECORD",
        )
        self.assertEqual(extension["f0_safety_maximum_group_ratio"], 0.05)
        self.assertEqual(extension["low_confidence_policy"], "FAIL_CLOSED")

    def test_v1p1_recipes_validate_and_preserve_v1_recipes(self) -> None:
        try:
            import jsonschema
        except ImportError as error:
            self.fail(f"jsonschema missing from the production test environment: {error}")
        schema = load_json(SCHEMA_DIR / "song-recipe.schema.json")
        for slug in ("yuai", "bainianguji", "turanxiangqini"):
            candidate = load_json(HAJIMI_ROOT / "recipes" / f"{slug}-v1p1.json")
            jsonschema.validate(candidate, schema)
            self.assertEqual(candidate["presets"]["audio"], "hajimi-note-event-v1.1")
            self.assertEqual(candidate["regression"]["baseline_event_preset"], "hajimi-note-event-v1")
        for slug in ("bainianguji", "turanxiangqini"):
            baseline = load_json(HAJIMI_ROOT / "recipes" / f"{slug}.json")
            jsonschema.validate(baseline, schema)
            self.assertEqual(baseline["presets"]["audio"], "hajimi-note-event-v1")

    def test_shared_cli_maps_preset_to_run_tag_explicitly(self) -> None:
        source = (HAJIMI_ROOT / "scripts" / "hajimi_audio.py").read_text(encoding="utf-8")
        self.assertIn('"run_tag": "hev1"', source)
        self.assertIn('"run_tag": "hev1p1"', source)
        self.assertNotIn('DEFAULT_DATE = "20260813"', source)

    def test_mv_preset_matches_portrait_resource_contract(self) -> None:
        preset = load_json(PRESET_DIR / "hajimi-rain-window-v1.json")
        self.assertEqual(preset["canvas"], {
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "pixel_format": "yuv420p",
            "color": "BT.709 SDR",
        })
        self.assertEqual(preset["layout"]["mv_inner"], {
            "x": 76,
            "y": 164,
            "width": 928,
            "height": 642,
        })
        self.assertEqual(preset["visual_proxy"]["continuous_video_elements"], 1)
        self.assertEqual(preset["render"]["workers"], 1)
        self.assertEqual(preset["render"]["ffmpeg_threads"], 2)
        self.assertEqual(preset["qa"]["maximum_simultaneous_highlights"], 1)

    def test_shared_sources_do_not_embed_golden_song_constants(self) -> None:
        existing_sources = [path for path in SHARED_SOURCES if path.exists()]
        if not existing_sources:
            self.skipTest("shared production entry points have not been created yet")

        failures: list[str] = []
        for source in existing_sources:
            content = source.read_text(encoding="utf-8")
            for label, pattern in FORBIDDEN_SHARED_SOURCE_PATTERNS.items():
                if re.search(pattern, content, flags=re.IGNORECASE):
                    failures.append(f"{source.relative_to(HAJIMI_ROOT)}: {label}")
        self.assertEqual(failures, [], "golden fixture constants leaked into shared code")


if __name__ == "__main__":
    unittest.main()
