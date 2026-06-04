#!/usr/bin/env python3
"""
训练 faiss 检索索引（替代 WebUI "训练特征索引" 按钮）

输入：frameworks/rvc-webui-macos/logs/<exp_name>/3_feature768/*.npy (v2)
输出：frameworks/rvc-webui-macos/logs/<exp_name>/added_IVF*.index
       (推理时 --index_path 用这个)

用法：
    cd /Users/yinshawnrao/explorer/cc-voice
    . frameworks/rvc-webui-macos/.venv/bin/activate
    python scripts/05_train_index.py --exp zhang-shaohan-phase1
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
    parser.add_argument("--exp", required=True, help="experiment name (e.g. zhang-shaohan-phase1)")
    parser.add_argument("--version", default="v2", choices=["v1", "v2"])
    args = parser.parse_args()

    os.chdir(RVC_ROOT)
    import numpy as np
    import faiss

    exp_dir = Path(f"logs/{args.exp}")
    feature_subdir = "3_feature256" if args.version == "v1" else "3_feature768"
    feature_dir = exp_dir / feature_subdir

    if not feature_dir.exists():
        print(f"ERROR: feature dir missing: {feature_dir}", file=sys.stderr)
        print("  run 特征提取 first (or rerun WebUI step)", file=sys.stderr)
        return 1

    files = sorted(feature_dir.glob("*.npy"))
    if not files:
        print(f"ERROR: no .npy under {feature_dir}", file=sys.stderr)
        return 1

    print(f"Loading {len(files)} feature files from {feature_dir}...")
    npys = [np.load(str(p)) for p in files]
    big_npy = np.concatenate(npys, 0)
    print(f"  shape: {big_npy.shape}")

    # Shuffle
    idx = np.arange(big_npy.shape[0])
    np.random.shuffle(idx)
    big_npy = big_npy[idx]

    # MiniBatchKMeans reduction only if > 200k samples
    if big_npy.shape[0] > 2e5:
        from sklearn.cluster import MiniBatchKMeans
        print(f"  > 200k samples, reducing to 10k via MiniBatchKMeans...")
        big_npy = MiniBatchKMeans(
            n_clusters=10000, verbose=True, batch_size=256 * 8,
            compute_labels=False, init="random",
        ).fit(big_npy).cluster_centers_

    # Save total feature dump
    np.save(str(exp_dir / "total_fea.npy"), big_npy)

    # Build IVF index
    dim = 256 if args.version == "v1" else 768
    n_ivf = min(int(16 * np.sqrt(big_npy.shape[0])), big_npy.shape[0] // 39)
    print(f"  IVF nlist = {n_ivf}, dim = {dim}")

    index = faiss.index_factory(dim, f"IVF{n_ivf},Flat")
    index_ivf = faiss.extract_index_ivf(index)
    index_ivf.nprobe = 1

    print("  training (kmeans on IVF centroids)...")
    index.train(big_npy)

    trained_name = f"trained_IVF{n_ivf}_Flat_nprobe_{index_ivf.nprobe}_{args.exp}_{args.version}.index"
    faiss.write_index(index, str(exp_dir / trained_name))

    print("  adding vectors...")
    for i in range(0, big_npy.shape[0], 8192):
        index.add(big_npy[i : i + 8192])

    added_name = f"added_IVF{n_ivf}_Flat_nprobe_{index_ivf.nprobe}_{args.exp}_{args.version}.index"
    added_path = exp_dir / added_name
    faiss.write_index(index, str(added_path))

    print(f"\n✓ Done: {RVC_ROOT / added_path}")
    print(f"\nUse this in inference: --index_path \"{(RVC_ROOT / added_path).resolve()}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
