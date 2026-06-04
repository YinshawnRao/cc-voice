#!/bin/sh
# 新增两首源歌的 e100 ↔ e200 推理（参数与 15_infer_compare_e100_e200.sh 完全一致）
#   天空没有极限 - 邓紫棋  (slug tiankongmeiyoujixian)
#   理想情人     - 杨丞琳  (slug lixiangqingren)
#
# 固定参数：protect=0.66 / f0method=rmvpe / 无 index / f0up_key=0
# 输出：outputs/phase2_e200/{song}_{e100,e200}_p066_rmvpe_{vocals,final}.wav
#
# 用法（后台）：
#   nohup scripts/16_infer_new_songs.sh > outputs/phase2_e200/infer_new.log 2>&1 &

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RVC_PY="$PROJECT_ROOT/frameworks/rvc-webui-macos/.venv/bin/python"
OUT_DIR="$PROJECT_ROOT/outputs/phase2_e200"
mkdir -p "$OUT_DIR"

PROTECT=0.66
F0METHOD=rmvpe

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] === $* ==="; }

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

for song in tiankongmeiyoujixian lixiangqingren; do
  infer_one e200 "zhang-shaohan-phase2.pth"             "$song"
  infer_one e100 "zhang-shaohan-phase2_e100_s21000.pth" "$song"
done

log "✓ 新增两首完成"
ls -lh "$OUT_DIR"/tiankongmeiyoujixian_*_final.wav "$OUT_DIR"/lixiangqingren_*_final.wav
