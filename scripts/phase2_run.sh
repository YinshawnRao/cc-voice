#!/bin/sh
# Phase 2 端到端 orchestrator · CLI-only · 全自动
# 阶段：
#   1  yt-dlp 下载新增的 6 个 BV
#   2  Demucs 分离新音频
#   3  librosa 切片
#   4  ASCII rename (idempotent)
#   5  质量检查 + manifest
#   6  RVC preprocess (resample + sub-slice 到 40k/16k)
#   7  RVC F0 提取 (rmvpe + MPS)
#   8  RVC Hubert 特征提取 (MPS)
#   9  生成 filelist.txt
#   10 训练 (200 epoch, ~37h)
#   11 训练 faiss 索引
#   12 推理 + 混音 → outputs/phase2_demo/final.wav
#
# 用法：
#   scripts/phase2_run.sh
# 推荐后台启动并 tail 日志：
#   nohup scripts/phase2_run.sh > /tmp/phase2.log 2>&1 &

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RVC_ROOT="$PROJECT_ROOT/frameworks/rvc-webui-macos"
DEMUCS_PY="$PROJECT_ROOT/venv/demucs/bin/python"
RVC_PY="$RVC_ROOT/.venv/bin/python"

EXP="zhang-shaohan-phase2"
EXP_DIR="$RVC_ROOT/logs/$EXP"
DATASET_DIR="$PROJECT_ROOT/dataset/zhang-shaohan/slices"
SR="40k"
TOTAL_EPOCH=200
SAVE_EVERY=50
BATCH_SIZE=4
VERSION="v2"
GPUS="0"

OUT_DIR="$PROJECT_ROOT/outputs/phase2_demo"
mkdir -p "$OUT_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] === $* ==="; }

# === Stage 1: yt-dlp ===
log "Stage 1/12 · yt-dlp download"
"$PROJECT_ROOT/scripts/01_download.sh"

# === Stage 2: Demucs ===
log "Stage 2/12 · Demucs vocal separation (MPS)"
"$DEMUCS_PY" "$PROJECT_ROOT/scripts/02_separate.py"

# === Stage 3: slicing ===
log "Stage 3/12 · librosa silence-based slicing"
"$RVC_PY" "$PROJECT_ROOT/scripts/03_slice.py"

# === Stage 4: ASCII rename ===
log "Stage 4/12 · ASCII slug rename (idempotent)"
python3 "$PROJECT_ROOT/scripts/00_rename.py"

# === Stage 5: quality check ===
log "Stage 5/12 · quality check + manifest"
"$RVC_PY" "$PROJECT_ROOT/scripts/04_quality_check.py"

# === Stage 6: RVC preprocess ===
log "Stage 6/12 · RVC preprocess (resample 40k + sub-slice)"
mkdir -p "$EXP_DIR"
cd "$RVC_ROOT"
export PYTORCH_ENABLE_MPS_FALLBACK=1
export PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0
export KMP_DUPLICATE_LIB_OK=TRUE
export OMP_NUM_THREADS=1

# Write config.json (required by train.py hparams loader)
# NOTE: v2 + 40k 实际 maps to v1 config (RVC infer-web.py line 567-570 逻辑)
"$RVC_PY" <<PYEOF
import json, os
sr = "$SR"
version = "$VERSION"
# v2 with 40k uses v1 config; v2 with 48k/32k uses v2 config
sub = "v1" if (version == "v1" or sr == "40k") else "v2"
src = os.path.join("$RVC_ROOT", f"configs/inuse/{sub}/{sr}.json")
if not os.path.exists(src):
    # fallback to non-inuse template
    src = os.path.join("$RVC_ROOT", f"configs/{sub}/{sr}.json")
dst = os.path.join("$EXP_DIR", "config.json")
with open(src) as f:
    cfg = json.load(f)
with open(dst, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=4, sort_keys=True)
    f.write("\n")
print(f"config.json written: {dst} (from {src})")
PYEOF

# args: <input_dir> <sr> <n_processes> <exp_dir> <noparallel> <per>
"$RVC_PY" infer/modules/train/preprocess.py \
  "$DATASET_DIR" 40000 8 "$EXP_DIR" False 3.0

# === Stage 7: F0 extract (rmvpe, MPS-aware) ===
log "Stage 7/12 · F0 extract (rmvpe + MPS)"
# args: <n_part> <i_part> <i_gpu> <exp_dir> <is_half>
"$RVC_PY" infer/modules/train/extract/extract_f0_rmvpe.py \
  1 0 0 "$EXP_DIR" False

# === Stage 8: Hubert feature extract ===
log "Stage 8/12 · Hubert feature extract (MPS)"
# args: <device> <n_part> <i_part> <exp_dir> <version> <is_half>
"$RVC_PY" infer/modules/train/extract_feature_print.py \
  mps 1 0 "$EXP_DIR" "$VERSION" False

# === Stage 9: filelist.txt ===
log "Stage 9/12 · generate filelist.txt"
"$RVC_PY" <<PYEOF
import os, random
exp_dir = "$EXP_DIR"
mute_base = os.path.join("$RVC_ROOT", "logs/mute")
sr2 = "$SR"
spk_id = 0

gt = os.path.join(exp_dir, "0_gt_wavs")
fea = os.path.join(exp_dir, "3_feature768")
f0 = os.path.join(exp_dir, "2a_f0")
f0n = os.path.join(exp_dir, "2b-f0nsf")
names = (set(n.split(".")[0] for n in os.listdir(gt))
         & set(n.split(".")[0] for n in os.listdir(fea))
         & set(n.split(".")[0] for n in os.listdir(f0))
         & set(n.split(".")[0] for n in os.listdir(f0n)))
opt = []
for n in sorted(names):
    opt.append(f"{gt}/{n}.wav|{fea}/{n}.npy|{f0}/{n}.wav.npy|{f0n}/{n}.wav.npy|{spk_id}")
for _ in range(2):
    opt.append(f"{mute_base}/0_gt_wavs/mute{sr2}.wav|{mute_base}/3_feature768/mute.npy|{mute_base}/2a_f0/mute.wav.npy|{mute_base}/2b-f0nsf/mute.wav.npy|{spk_id}")
random.seed(42)
random.shuffle(opt)
fl = os.path.join(exp_dir, "filelist.txt")
with open(fl, "w") as f:
    f.write("\n".join(opt))
print(f"filelist: {len(opt)} entries → {fl}")
PYEOF

# === Stage 10: train ===
log "Stage 10/12 · train RVC v2 · $TOTAL_EPOCH epochs · ETA ~$((TOTAL_EPOCH * 11 / 60))h"
"$RVC_PY" infer/modules/train/train.py \
  -e "$EXP" \
  -sr "$SR" \
  -f0 1 \
  -bs $BATCH_SIZE \
  -g $GPUS \
  -te $TOTAL_EPOCH \
  -se $SAVE_EVERY \
  -pg assets/pretrained_v2/f0G40k.pth \
  -pd assets/pretrained_v2/f0D40k.pth \
  -l 1 \
  -c 0 \
  -sw 1 \
  -v "$VERSION"

# === Stage 11: faiss index ===
log "Stage 11/12 · build faiss index"
cd "$PROJECT_ROOT"
"$RVC_PY" "$PROJECT_ROOT/scripts/05_train_index.py" --exp "$EXP"

# === Stage 12: inference + mix ===
log "Stage 12/12 · inference + mix → outputs/phase2_demo/"
# faiss segfault FIXED (OMP_NUM_THREADS=1 + faiss.omp_set_num_threads(1) in 06_infer.py).
# Use the trained index for retrieval boost (index_rate=0.75 by default).
"$RVC_PY" "$PROJECT_ROOT/scripts/06_infer.py" \
  --model "$EXP.pth" \
  --input "$PROJECT_ROOT/dataset/source-songs/vocals/guangnianzhiwai.wav" \
  --output "$OUT_DIR/converted_vocals.wav" \
  --f0method harvest

"$RVC_PY" "$PROJECT_ROOT/scripts/07_mix.py" \
  --vocals "$OUT_DIR/converted_vocals.wav" \
  --instrumental "$PROJECT_ROOT/dataset/source-songs/no_vocals/guangnianzhiwai.wav" \
  --output "$OUT_DIR/final.wav"

log "✓ Phase 2 COMPLETE"
ls -lh "$OUT_DIR/"
