#!/usr/bin/env python3
"""
音频切片：dataset/zhang-shaohan/vocals/*.wav → dataset/zhang-shaohan/slices/*.wav

按静音检测切成 5-15s 片段，过滤掉静音区。RVC 训练需要的标准片段长度。

实现：librosa.effects.split 检测非静音区，再 merge/split 到目标长度。

依赖：librosa, soundfile (用 RVC venv 即可，已装好)

用法：
    cd frameworks/rvc-webui-macos && . .venv/bin/activate
    cd /Users/yinshawnrao/explorer/cc-voice
    python scripts/03_slice.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VOCALS_DIR = PROJECT_ROOT / "dataset" / "zhang-shaohan" / "vocals"
SLICES_DIR = PROJECT_ROOT / "dataset" / "zhang-shaohan" / "slices"

# Slicing config
TARGET_SR = 44100
MIN_LEN_SEC = 5.0
MAX_LEN_SEC = 15.0
SILENCE_TOP_DB = 30  # quieter than this counted as silence
MIN_SILENCE_GAP_SEC = 0.3  # gaps shorter than this won't split


def slice_audio(audio: np.ndarray, sr: int) -> list[np.ndarray]:
    """Split audio into 5-15s chunks at silence boundaries."""
    intervals = librosa.effects.split(audio, top_db=SILENCE_TOP_DB)

    # Merge intervals separated by < MIN_SILENCE_GAP_SEC
    merged: list[tuple[int, int]] = []
    min_gap = int(MIN_SILENCE_GAP_SEC * sr)
    for start, end in intervals:
        if merged and start - merged[-1][1] < min_gap:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))

    # Split intervals longer than MAX_LEN_SEC at midpoint(s)
    min_samples = int(MIN_LEN_SEC * sr)
    max_samples = int(MAX_LEN_SEC * sr)
    chunks: list[np.ndarray] = []
    for start, end in merged:
        length = end - start
        if length < min_samples:
            continue
        if length <= max_samples:
            chunks.append(audio[start:end])
            continue
        # subdivide long interval at silence-respecting cut points
        n_subs = int(np.ceil(length / max_samples))
        step = length // n_subs
        for i in range(n_subs):
            s = start + i * step
            e = start + (i + 1) * step if i < n_subs - 1 else end
            if e - s >= min_samples:
                chunks.append(audio[s:e])
    return chunks


def main() -> int:
    inputs = sorted(VOCALS_DIR.glob("*.wav"))
    if not inputs:
        print(f"No vocals under {VOCALS_DIR}. Run 02_separate.py first.")
        return 1

    SLICES_DIR.mkdir(parents=True, exist_ok=True)

    total_chunks = 0
    total_sec = 0.0
    for wav_path in inputs:
        print(f"\n[slice] {wav_path.name}")
        audio, sr = librosa.load(str(wav_path), sr=TARGET_SR, mono=True)
        chunks = slice_audio(audio, sr)
        for i, chunk in enumerate(chunks):
            out = SLICES_DIR / f"{wav_path.stem}_{i:03d}.wav"
            sf.write(str(out), chunk, sr, subtype="PCM_16")
            total_chunks += 1
            total_sec += len(chunk) / sr
        print(f"  → {len(chunks)} chunks ({sum(len(c) for c in chunks)/sr:.1f}s)")

    print(f"\nTotal: {total_chunks} chunks, {total_sec/60:.1f} min")
    print(f"Output: {SLICES_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
