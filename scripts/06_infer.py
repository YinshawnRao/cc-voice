#!/usr/bin/env python3
"""
RVC 推理 CLI（绕开 RVC fork 的 tools/infer_cli.py，避免 hubert.to() 在 mps 上 segfault）

行为：
  - 显式 NO_MPS 路径（在任何 RVC 模块导入之前 monkey-patch）
  - 显式 torch.load weights_only=False
  - 调用 infer.modules.vc.modules.VC.vc_single

用法：
  cd /Users/yinshawnrao/explorer/cc-voice
  . frameworks/rvc-webui-macos/.venv/bin/activate
  python scripts/06_infer.py \\
      --model zhang-shaohan-phase1.pth \\
      --input dataset/source-songs/vocals/guangnianzhiwai.wav \\
      --output outputs/phase1_demo/converted_vocals.wav
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RVC_ROOT = PROJECT_ROOT / "frameworks" / "rvc-webui-macos"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="zhang-shaohan-phase1.pth")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--index", type=Path, default=None,
                        help="path to added_*.index; auto-detected if omitted")
    parser.add_argument("--f0up_key", type=int, default=0)
    parser.add_argument("--f0method", default="rmvpe",
                        choices=["pm", "harvest", "dio", "rmvpe"])
    parser.add_argument("--index_rate", type=float, default=0.75)
    parser.add_argument("--protect", type=float, default=0.5)
    parser.add_argument("--filter_radius", type=int, default=3)
    parser.add_argument("--rms_mix_rate", type=float, default=1.0)
    parser.add_argument("--resample_sr", type=int, default=0)
    args = parser.parse_args()
    # Resolve to absolute paths BEFORE chdir
    args.input = args.input.resolve()
    args.output = args.output.resolve()
    if args.index:
        args.index = args.index.resolve()

    # === Critical: patch torch BEFORE any RVC import ===
    os.environ["NO_MPS"] = "1"
    os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
    os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
    # OpenMP single-thread: fairseq + faiss both ship libomp.dylib; running
    # multi-threaded triggers a SIGSEGV in faiss.index.search() after hubert load.
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    import torch
    torch.backends.mps.is_available = lambda: False
    torch.backends.mps.is_built = lambda: False
    torch.set_num_threads(1)
    import faiss
    faiss.omp_set_num_threads(1)
    # Patch torch.load default to allow fairseq dictionary unpickle
    _orig_load = torch.load
    torch.load = lambda *a, **k: _orig_load(*a, **{**k, "weights_only": k.get("weights_only", False)})

    # Switch into RVC framework dir so relative paths in Config work
    os.chdir(RVC_ROOT)
    sys.path.insert(0, str(RVC_ROOT))
    from dotenv import load_dotenv
    load_dotenv()
    # Explicit fallback if .env parsing failed (the spaces around `=` in .env tripped dotenv)
    os.environ.setdefault("weight_root", "assets/weights")
    os.environ.setdefault("weight_uvr5_root", "assets/uvr5_weights")
    os.environ.setdefault("index_root", "logs")
    os.environ.setdefault("outside_index_root", "assets/indices")
    os.environ.setdefault("rmvpe_root", "assets/rmvpe")

    # Auto-detect index if not given
    if args.index is None:
        idx_dir = RVC_ROOT / "logs" / Path(args.model).stem
        cand = sorted(idx_dir.glob("added_*.index"))
        args.index = cand[0] if cand else None
        if args.index is None:
            print(f"WARN: no added_*.index found in {idx_dir}; running without retrieval")
            args.index = ""

    print(f"Model       : {args.model}")
    print(f"Input       : {args.input}")
    print(f"Output      : {args.output}")
    print(f"Index       : {args.index or '(none)'}")
    print(f"f0_up_key   : {args.f0up_key}")
    print(f"f0_method   : {args.f0method}")
    print(f"index_rate  : {args.index_rate}")
    print(f"protect     : {args.protect}")
    print()

    # Clear argv before Config init — Config parses its own argv internally
    sys.argv = sys.argv[:1]
    from configs.config import Config
    config = Config()
    config.device = "cpu"
    config.is_half = False

    from infer.modules.vc.modules import VC
    vc = VC(config)
    vc.get_vc(args.model)

    _, wav_opt = vc.vc_single(
        0,                              # sid
        str(args.input),                # input_audio_path
        args.f0up_key,                  # f0_up_key
        None,                           # f0_file
        args.f0method,                  # f0_method
        str(args.index) if args.index else "",  # file_index
        None,                           # file_index2
        args.index_rate,                # index_rate
        args.filter_radius,             # filter_radius
        args.resample_sr,               # resample_sr
        args.rms_mix_rate,              # rms_mix_rate
        args.protect,                   # protect
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    from scipy.io import wavfile
    sr, audio = wav_opt
    wavfile.write(str(args.output), sr, audio)
    print(f"\n✓ Done: {args.output}")
    print(f"  sample_rate={sr}, length={len(audio)/sr:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
