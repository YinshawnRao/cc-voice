#!/bin/sh
# phase3 ↔ e200 A/B：建 phase3 自己的索引，用激进档推测试曲，跟 e200 激进版并排。
# 输出：outputs/phase3_对比/{歌}_e200.wav  vs  {歌}_phase3.wav
#
# 用法： nohup scripts/26_phase3_ab.sh > "outputs/phase3_对比/run.log" 2>&1 &

set -e
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RVC_PY="$PROJECT_ROOT/frameworks/rvc-webui-macos/.venv/bin/python"
P3LOG="$PROJECT_ROOT/frameworks/rvc-webui-macos/logs/zhang-shaohan-phase3"
OUT="$PROJECT_ROOT/outputs/phase3_对比"
E200DIR="$PROJECT_ROOT/outputs/phase2_e200/test_targets"
mkdir -p "$OUT"
log() { echo "[$(date '+%H:%M:%S')] === $* ==="; }

# 0. 建 phase3 索引（若无）
IDX=$(ls "$P3LOG"/added_*.index 2>/dev/null | head -1)
if [ -z "$IDX" ]; then
  log "建 phase3 faiss 索引"
  "$RVC_PY" "$PROJECT_ROOT/scripts/05_train_index.py" --exp zhang-shaohan-phase3
  IDX=$(ls "$P3LOG"/added_*.index | head -1)
fi
log "index = $IDX"

# 1. phase3 激进档推理（protect0.20 / index0.90 / rmvpe / 不变调）
for slug_cn in "turanxiangqini:突然想起你" "xinqiang:心墙"; do
  slug=${slug_cn%:*}; cn=${slug_cn#*:}
  log "phase3 推理 $cn"
  "$RVC_PY" "$PROJECT_ROOT/scripts/06_infer.py" \
    --model zhang-shaohan-phase3.pth \
    --input "$PROJECT_ROOT/dataset/source-songs/vocals/$slug.wav" \
    --output "/tmp/${slug}_p3_voc.wav" \
    --f0method rmvpe --protect 0.20 --index "$IDX" --index_rate 0.90 --f0up_key 0
  "$RVC_PY" "$PROJECT_ROOT/scripts/07_mix.py" \
    --vocals "/tmp/${slug}_p3_voc.wav" \
    --instrumental "$PROJECT_ROOT/dataset/source-songs/no_vocals/$slug.wav" \
    --output "$OUT/${cn}_phase3.wav"
  rm -f "/tmp/${slug}_p3_voc.wav"
done

# 2. 拷贝已有的 e200 激进版做并排
cp "$E200DIR/突然想起你-激进版.wav" "$OUT/突然想起你_e200.wav" 2>/dev/null || echo "⚠️ 缺 e200 突然想起你 激进版"
cp "$E200DIR/心墙-激进版.wav"       "$OUT/心墙_e200.wav"       2>/dev/null || echo "⚠️ 缺 e200 心墙 激进版"

log "✓ A/B 就绪"
ls -1 "$OUT"/*.wav | sed 's#.*/##'