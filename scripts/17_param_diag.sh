#!/bin/sh
# 推理参数诊断：在 e200 + 光年之外 上拆解 protect / retrieval-index 对"像不像"的贡献。
# 目的：在投入"补低音素材 + 重训"之前，先量化两个便宜杠杆能捞回多少相似度。
#
# 四象限（protect × index）：
#   A  p066 no-idx  = 现有 baseline（outputs/phase2_e200/guangnianzhiwai_e200_p066_rmvpe_final.wav）
#   B  p033 no-idx  ← 本脚本，在建 index 之前跑（auto-detect 找不到 index）
#   C  p066 idx075  ← 隔离 index 效果
#   D  p033 idx075  ← index + 更激进 protect（预期"最像"）
#
# 输出：outputs/phase2_e200/param_diag/guangnianzhiwai_e200_{变体}_{vocals,final}.wav
#
# 用法（后台）：
#   nohup scripts/17_param_diag.sh > outputs/phase2_e200/param_diag.log 2>&1 &

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RVC_PY="$PROJECT_ROOT/frameworks/rvc-webui-macos/.venv/bin/python"
EXP_LOG="$PROJECT_ROOT/frameworks/rvc-webui-macos/logs/zhang-shaohan-phase2"
OUT="$PROJECT_ROOT/outputs/phase2_e200/param_diag"
mkdir -p "$OUT"

SONG=guangnianzhiwai
MODEL=zhang-shaohan-phase2.pth
IN="$PROJECT_ROOT/dataset/source-songs/vocals/$SONG.wav"
INST="$PROJECT_ROOT/dataset/source-songs/no_vocals/$SONG.wav"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] === $* ==="; }

infer() {  # <variant-name> <protect> [extra args...]
  name=$1; protect=$2; shift 2
  voc="$OUT/${SONG}_e200_${name}_vocals.wav"
  fin="$OUT/${SONG}_e200_${name}_final.wav"
  log "推理 $name (protect=$protect $*)"
  "$RVC_PY" "$PROJECT_ROOT/scripts/06_infer.py" \
    --model "$MODEL" --input "$IN" --output "$voc" \
    --f0method rmvpe --protect "$protect" --f0up_key 0 "$@"
  "$RVC_PY" "$PROJECT_ROOT/scripts/07_mix.py" \
    --vocals "$voc" --instrumental "$INST" --output "$fin"
}

# --- B: protect 0.33, 无 index（必须在建 index 之前跑）---
infer p033_noidx 0.33

# --- 建 faiss retrieval index ---
log "build faiss index (zhang-shaohan-phase2)"
"$RVC_PY" "$PROJECT_ROOT/scripts/05_train_index.py" --exp zhang-shaohan-phase2
IDX=$(ls "$EXP_LOG"/added_*.index 2>/dev/null | head -1)
log "index = $IDX"

# --- C / D: 带 index ---
infer p066_idx075 0.66 --index "$IDX" --index_rate 0.75
infer p033_idx075 0.33 --index "$IDX" --index_rate 0.75

# 把 baseline A 复制进来方便并排对听
cp "$PROJECT_ROOT/outputs/phase2_e200/${SONG}_e200_p066_rmvpe_final.wav" \
   "$OUT/${SONG}_e200_p066_noidx_final.wav" 2>/dev/null || true

log "✓ 诊断完成"
ls -lh "$OUT"/*_final.wav
