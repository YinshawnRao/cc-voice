#!/usr/bin/env python3
"""
混音对齐：把 RVC 推理后的人声 + 原曲伴奏 合成成品

输入：
  outputs/phase1_demo/converted_vocals.wav  (RVC 推理输出)
  dataset/source-songs/no_vocals/<原名>.wav (Demucs 分离的伴奏)

输出：
  outputs/phase1_demo/final.wav

策略：
  - 强制采样率 44.1kHz, mono (训练标准)，最后转 stereo
  - 时间轴对齐（默认零位对齐，假设推理输出未经时长改变）
  - 简单 RMS 归一化避免人声过响 / 过弱
  - 不做 EQ / 压缩等 DAW 级处理 — Phase 3 用 DAW 精修

依赖：librosa, soundfile, numpy (RVC venv 已装)

用法：
    cd frameworks/rvc-webui-macos && . .venv/bin/activate
    cd /Users/yinshawnrao/explorer/cc-voice
    python scripts/07_mix.py \
        --vocals outputs/phase1_demo/converted_vocals.wav \
        --instrumental dataset/source-songs/no_vocals/光年之外.wav \
        --output outputs/phase1_demo/final.wav
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf


def rms_normalize(audio: np.ndarray, target_rms: float = 0.1) -> np.ndarray:
    cur = np.sqrt(np.mean(audio**2))
    if cur < 1e-6:
        return audio
    return audio * (target_rms / cur)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vocals", type=Path, required=True, help="converted vocals from RVC")
    parser.add_argument("--instrumental", type=Path, required=True, help="instrumental stem from demucs")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--vocals-gain-db", type=float, default=0.0, help="extra gain on vocals after RMS norm")
    parser.add_argument("--sr", type=int, default=44100)
    args = parser.parse_args()

    if not args.vocals.exists():
        print(f"ERROR: vocals not found: {args.vocals}", file=sys.stderr)
        return 1
    if not args.instrumental.exists():
        print(f"ERROR: instrumental not found: {args.instrumental}", file=sys.stderr)
        return 1

    print(f"Loading vocals      : {args.vocals}")
    v, _ = librosa.load(str(args.vocals), sr=args.sr, mono=True)
    print(f"Loading instrumental: {args.instrumental}")
    inst, _ = librosa.load(str(args.instrumental), sr=args.sr, mono=True)

    print(f"  vocals    : {len(v)/args.sr:.2f}s, rms={np.sqrt(np.mean(v**2)):.4f}")
    print(f"  inst      : {len(inst)/args.sr:.2f}s, rms={np.sqrt(np.mean(inst**2)):.4f}")

    # Align lengths
    n = max(len(v), len(inst))
    v = np.pad(v, (0, n - len(v)))
    inst = np.pad(inst, (0, n - len(inst)))

    # RMS normalize separately, then sum
    v = rms_normalize(v, target_rms=0.1)
    inst = rms_normalize(inst, target_rms=0.08)  # slightly quieter than vocals
    v *= 10 ** (args.vocals_gain_db / 20.0)

    mix = v + inst

    # Soft clip safety
    peak = np.max(np.abs(mix))
    if peak > 0.99:
        mix = mix * (0.99 / peak)
        print(f"  peak normalized {peak:.3f} → 0.99")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(args.output), mix, args.sr, subtype="PCM_16")
    print(f"\nWritten: {args.output} ({len(mix)/args.sr:.2f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
