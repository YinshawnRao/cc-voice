#!/usr/bin/env python3
"""
人声分离：
  raw/zhang-shaohan/*.wav  → dataset/zhang-shaohan/vocals/*.wav         (训练用)
  raw/source-songs/*.wav   → dataset/source-songs/{vocals,no_vocals}/   (源歌：需保留伴奏)

用 Demucs htdemucs (Hybrid Transformer Demucs)。
MPS 加速，CPU 回退。

依赖：demucs (venv/demucs/ 已装)

用法：
    . venv/demucs/bin/activate
    python scripts/02_separate.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TMP_DIR = PROJECT_ROOT / "dataset" / ".demucs_tmp"


def run_demucs(wav_path: Path, tmp_dir: Path) -> Path | None:
    """Run demucs on a single file, return path to stem output dir."""
    for device in ("mps", "cpu"):
        # Use sys.executable so the subprocess uses the SAME python as the parent
        # (orchestrator invocations don't have "python" on PATH otherwise).
        cmd = [
            sys.executable, "-m", "demucs",
            "--two-stems", "vocals",
            "-n", "htdemucs",
            "-d", device,
            "-o", str(tmp_dir),
            str(wav_path),
        ]
        print(f"  [demucs/{device}] {wav_path.name}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            stem_dir = tmp_dir / "htdemucs" / wav_path.stem
            if stem_dir.exists():
                return stem_dir
        else:
            print(f"  {device} failed: {result.stderr[-200:].strip()}")
    return None


def separate_batch(raw_dir: Path, out_root: Path, keep_no_vocals: bool) -> None:
    """Process all wavs under raw_dir. Save vocals (and optionally no_vocals) under out_root/."""
    inputs = sorted(raw_dir.glob("*.wav"))
    if not inputs:
        print(f"  (no input wavs under {raw_dir})")
        return

    vocals_dir = out_root / "vocals"
    vocals_dir.mkdir(parents=True, exist_ok=True)
    no_vocals_dir = out_root / "no_vocals"
    if keep_no_vocals:
        no_vocals_dir.mkdir(parents=True, exist_ok=True)

    for wav in inputs:
        out_v = vocals_dir / wav.name
        if out_v.exists():
            print(f"  [skip]  {wav.name}")
            continue

        stem_dir = run_demucs(wav, TMP_DIR)
        if stem_dir is None:
            print(f"  ✗ separation failed: {wav.name}", file=sys.stderr)
            continue

        v = stem_dir / "vocals.wav"
        if v.exists():
            shutil.move(str(v), str(out_v))
            print(f"  → {out_v.relative_to(PROJECT_ROOT)}")

        if keep_no_vocals:
            nv = stem_dir / "no_vocals.wav"
            if nv.exists():
                out_nv = no_vocals_dir / wav.name
                shutil.move(str(nv), str(out_nv))
                print(f"  → {out_nv.relative_to(PROJECT_ROOT)}")

        shutil.rmtree(stem_dir, ignore_errors=True)


def main() -> int:
    try:
        import demucs  # noqa: F401
    except ImportError:
        print("ERROR: demucs not installed. Activate venv/demucs first:", file=sys.stderr)
        print("  . venv/demucs/bin/activate", file=sys.stderr)
        return 1

    TMP_DIR.mkdir(parents=True, exist_ok=True)

    print("=== Training samples (vocals only) ===")
    separate_batch(
        raw_dir=PROJECT_ROOT / "raw" / "zhang-shaohan",
        out_root=PROJECT_ROOT / "dataset" / "zhang-shaohan",
        keep_no_vocals=False,
    )

    print("\n=== Source songs (vocals + instrumental) ===")
    separate_batch(
        raw_dir=PROJECT_ROOT / "raw" / "source-songs",
        out_root=PROJECT_ROOT / "dataset" / "source-songs",
        keep_no_vocals=True,
    )

    shutil.rmtree(TMP_DIR, ignore_errors=True)
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
