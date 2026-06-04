#!/bin/sh
# 续训 zhang-shaohan-phase2: e150 → e200 (ONLY training, no data re-processing)
#
# 背景：6/2 训练在 ~epoch 191 时机器崩溃。RVC 每 50 epoch 才存盘，最近一次
# 可续训 checkpoint 停在 e150 (G_2333333.pth/D_2333333.pth, iteration=150)，
# 151→191 的权重全丢。本脚本从 e150 自动 resume 跑到 200。
#
# 与 phase2_run.sh 的区别：
#   - 只跑 Stage 10 (train)，不重跑 yt-dlp/Demucs/切片/预处理/特征提取
#   - SAVE_EVERY 50 → 10：刚崩过一次，缩小存盘间隔，崩溃最多丢 10 epoch
#   - 推理 + 混音不在这里做（训练完单独跑，便宜可重试）
#
# resume 机制：train.py 同名实验会自动 load latest G_*/D_*.pth (=e150) 续训，
#              -pg/-pd 底模仅在无 checkpoint 时使用，这里不会触发。
#
# 用法（后台、断会话也不死）：
#   nohup scripts/14_resume_train_e200.sh > outputs/phase2_e200/resume_train.log 2>&1 &
#   tail -f outputs/phase2_e200/resume_train.log

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RVC_ROOT="$PROJECT_ROOT/frameworks/rvc-webui-macos"
RVC_PY="$RVC_ROOT/.venv/bin/python"

EXP="zhang-shaohan-phase2"
EXP_DIR="$RVC_ROOT/logs/$EXP"
SR="40k"
TOTAL_EPOCH=200
SAVE_EVERY=10          # was 50; smaller = crash-resilient
BATCH_SIZE=4
VERSION="v2"
GPUS="0"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] === $* ==="; }

# --- sanity: resume checkpoint must exist (否则会从底模从头训，必须中止) ---
if [ ! -f "$EXP_DIR/G_2333333.pth" ] || [ ! -f "$EXP_DIR/D_2333333.pth" ]; then
  echo "FATAL: 找不到可续训 checkpoint ($EXP_DIR/G_2333333.pth)。中止，避免从头重训。" >&2
  exit 1
fi
RESUME_EPOCH=$("$RVC_PY" - "$EXP_DIR/G_2333333.pth" <<'PYEOF'
import sys, torch
print(torch.load(sys.argv[1], map_location="cpu").get("iteration"))
PYEOF
)
log "resume from epoch $RESUME_EPOCH → target $TOTAL_EPOCH (save_every=$SAVE_EVERY)"

cd "$RVC_ROOT"
export PYTORCH_ENABLE_MPS_FALLBACK=1
export PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0
export KMP_DUPLICATE_LIB_OK=TRUE
export OMP_NUM_THREADS=1

log "train RVC v2 · resume $RESUME_EPOCH→$TOTAL_EPOCH · ETA ~$(( (TOTAL_EPOCH - RESUME_EPOCH) * 11 / 50 ))h"
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

log "✓ 训练完成。最终模型应为 assets/weights/$EXP.pth (+ e200 snapshot)。下一步：建 index + 推理两首歌。"
ls -lh "$RVC_ROOT/assets/weights/${EXP}"*.pth 2>/dev/null
