#!/bin/sh
# e100 ↔ e200 干净 A/B：两个 epoch 各推光年之外 + 友爱，参数完全一致。
#
# 唯一变量 = epoch（100 vs 200）。其余固定：
#   protect=0.66（对齐当年 comparison_e100 的 p066 命名）
#   f0method=rmvpe（06_infer.py 默认）
#   无 index（phase2 从未建 index → 自动跳过 retrieval，两边天然一致）
#   f0up_key=0（女声→女声不变调）
#
# 输出：outputs/phase2_e200/{song}_{e100,e200}_p066_rmvpe_{vocals,final}.wav
#
# 用法（后台）：
#   nohup scripts/15_infer_compare_e100_e200.sh > outputs/phase2_e200/infer.log 2>&1 &
#   tail -f outputs/phase2_e200/infer.log

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RVC_PY="$PROJECT_ROOT/frameworks/rvc-webui-macos/.venv/bin/python"
OUT_DIR="$PROJECT_ROOT/outputs/phase2_e200"
mkdir -p "$OUT_DIR"

PROTECT=0.66
F0METHOD=rmvpe

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] === $* ==="; }

# tag -> model weight 文件名（位于 assets/weights/）
infer_one() {
  tag=$1; model=$2; song=$3
  voc="$OUT_DIR/${song}_${tag}_p066_rmvpe_vocals.wav"
  fin="$OUT_DIR/${song}_${tag}_p066_rmvpe_final.wav"

  log "[$tag] $song · 推理 (protect=$PROTECT f0=$F0METHOD no-index)"
  "$RVC_PY" "$PROJECT_ROOT/scripts/06_infer.py" \
    --model "$model" \
    --input "$PROJECT_ROOT/dataset/source-songs/vocals/${song}.wav" \
    --output "$voc" \
    --f0method "$F0METHOD" \
    --protect "$PROTECT" \
    --f0up_key 0

  log "[$tag] $song · 混音"
  "$RVC_PY" "$PROJECT_ROOT/scripts/07_mix.py" \
    --vocals "$voc" \
    --instrumental "$PROJECT_ROOT/dataset/source-songs/no_vocals/${song}.wav" \
    --output "$fin"
}

for song in guangnianzhiwai yuai; do
  infer_one e200 "zhang-shaohan-phase2.pth"            "$song"
  infer_one e100 "zhang-shaohan-phase2_e100_s21000.pth" "$song"
done

log "✓ 全部完成"
ls -lh "$OUT_DIR"/*_p066_rmvpe_final.wav
