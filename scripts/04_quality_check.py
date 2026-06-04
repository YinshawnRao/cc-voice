#!/usr/bin/env python3
"""
切片质量检查：扫描 dataset/zhang-shaohan/slices/ 输出每片的统计 + 生成 manifest.json

检查项：
  - 采样率 (应该统一 44.1kHz)
  - 时长 (5-15s 范围)
  - RMS 能量 (太低 → 静音残留；太高 → 失真)
  - 估算 SNR (简单基于 RMS std / mean)
  - 峰值 (clipping detection)

输出 manifest.json: 每个片段一条记录，含路径、时长、质量评分

CLAUDE.md §4.3 要求人工抽听 10%，本脚本输出建议抽听片段列表。

依赖：librosa, soundfile, numpy (RVC venv 已装)
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import librosa
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SLICES_DIR = PROJECT_ROOT / "dataset" / "zhang-shaohan" / "slices"
MANIFEST_PATH = PROJECT_ROOT / "dataset" / "zhang-shaohan" / "manifest.json"

EXPECTED_SR = 44100
MIN_LEN_SEC = 5.0
MAX_LEN_SEC = 15.0
MIN_RMS = 0.005   # below = likely silence
MAX_PEAK = 0.99   # above = likely clipping


def analyze_slice(path: Path) -> dict:
    audio, sr = librosa.load(str(path), sr=None, mono=True)
    duration = len(audio) / sr
    rms = float(np.sqrt(np.mean(audio**2)))
    peak = float(np.max(np.abs(audio)))
    rms_std = float(np.std(librosa.feature.rms(y=audio)[0]))

    flags = []
    if sr != EXPECTED_SR:
        flags.append(f"sr={sr}")
    if duration < MIN_LEN_SEC:
        flags.append(f"too_short={duration:.1f}s")
    if duration > MAX_LEN_SEC + 0.5:
        flags.append(f"too_long={duration:.1f}s")
    if rms < MIN_RMS:
        flags.append(f"silent_rms={rms:.4f}")
    if peak > MAX_PEAK:
        flags.append(f"clipping_peak={peak:.3f}")

    return {
        "path": str(path.relative_to(PROJECT_ROOT)),
        "name": path.name,
        "sample_rate": sr,
        "duration_sec": round(duration, 2),
        "rms": round(rms, 4),
        "peak": round(peak, 3),
        "rms_std": round(rms_std, 4),
        "flags": flags,
        "ok": len(flags) == 0,
    }


def main() -> int:
    inputs = sorted(SLICES_DIR.glob("*.wav"))
    if not inputs:
        print(f"No slices under {SLICES_DIR}. Run 03_slice.py first.")
        return 1

    results = [analyze_slice(p) for p in inputs]
    n_ok = sum(1 for r in results if r["ok"])
    n_flagged = len(results) - n_ok
    total_sec = sum(r["duration_sec"] for r in results)

    manifest = {
        "source": "张韶涵 真嗓素材",
        "sample_rate_target": EXPECTED_SR,
        "slice_config": {
            "min_len_sec": MIN_LEN_SEC,
            "max_len_sec": MAX_LEN_SEC,
        },
        "stats": {
            "total_slices": len(results),
            "total_sec": round(total_sec, 1),
            "total_min": round(total_sec / 60, 2),
            "n_ok": n_ok,
            "n_flagged": n_flagged,
        },
        "slices": results,
    }

    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    print(f"=== Slice quality summary ===")
    print(f"Total: {len(results)} slices, {total_sec/60:.2f} min")
    print(f"OK:        {n_ok}")
    print(f"Flagged:   {n_flagged}")
    print()
    if n_flagged:
        print("Flagged slices (review or remove):")
        for r in results:
            if not r["ok"]:
                print(f"  {r['name']}: {r['flags']}")
        print()

    # CLAUDE.md §4.3: 人工抽听 10%
    sample_n = max(3, len(results) // 10)
    random.seed(42)
    sample = random.sample(results, min(sample_n, len(results)))
    print(f"Suggested manual review samples ({len(sample)} of {len(results)}):")
    for r in sample:
        print(f"  {r['name']}  ({r['duration_sec']}s, rms={r['rms']})")

    print(f"\nManifest written: {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
