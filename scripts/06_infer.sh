#!/bin/sh
# RVC 推理 CLI 包装（替代 WebUI "模型推理" Tab）
#
# 用法：
#   scripts/06_infer.sh
#
# 默认参数符合 Phase 1 walkthrough §4：
#   model:       zhang-shaohan-phase1.pth
#   input:       dataset/source-songs/vocals/guangnianzhiwai.wav
#   output:      outputs/phase1_demo/converted_vocals.wav
#   f0up_key:    0    (同性别)
#   f0method:    rmvpe
#   index_rate:  0.75
#   protect:     0.5
#   device:      mps
#
# 改参数时直接编辑下面变量，或者通过环境变量覆盖。

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RVC_ROOT="$PROJECT_ROOT/frameworks/rvc-webui-macos"

MODEL_NAME="${MODEL_NAME:-zhang-shaohan-phase1.pth}"
INPUT_PATH="${INPUT_PATH:-$PROJECT_ROOT/dataset/source-songs/vocals/guangnianzhiwai.wav}"
OUTPUT_PATH="${OUTPUT_PATH:-$PROJECT_ROOT/outputs/phase1_demo/converted_vocals.wav}"
F0UP_KEY="${F0UP_KEY:-0}"
F0METHOD="${F0METHOD:-rmvpe}"
INDEX_RATE="${INDEX_RATE:-0.75}"
PROTECT="${PROTECT:-0.5}"
DEVICE="${DEVICE:-cpu}"  # MPS hubert load segfaults on M-series; CPU is stable
FILTER_RADIUS="${FILTER_RADIUS:-3}"
RMS_MIX_RATE="${RMS_MIX_RATE:-1}"

# Auto-detect index path
INDEX_PATH="${INDEX_PATH:-$(find "$RVC_ROOT/logs/zhang-shaohan-phase1" -name "added_*.index" 2>/dev/null | head -1)}"
if [ -z "$INDEX_PATH" ] || [ ! -f "$INDEX_PATH" ]; then
  echo "WARN: no .index file found. Run scripts/05_train_index.py first."
  echo "      Continuing without index (lower similarity, faster inference)."
  INDEX_PATH=""
fi

if [ ! -f "$INPUT_PATH" ]; then
  echo "ERROR: input not found: $INPUT_PATH" >&2
  exit 1
fi
if [ ! -f "$RVC_ROOT/assets/weights/$MODEL_NAME" ]; then
  echo "ERROR: model not found: $RVC_ROOT/assets/weights/$MODEL_NAME" >&2
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT_PATH")"

echo "=== RVC inference ==="
echo "  model:        $MODEL_NAME"
echo "  input:        $INPUT_PATH"
echo "  output:       $OUTPUT_PATH"
echo "  index:        ${INDEX_PATH:-<none>}"
echo "  f0up_key:     $F0UP_KEY"
echo "  f0method:     $F0METHOD"
echo "  index_rate:   $INDEX_RATE"
echo "  protect:      $PROTECT"
echo "  device:       $DEVICE"
echo ""

cd "$RVC_ROOT"
. .venv/bin/activate

# RVC's infer_cli.py uses dotenv and assumes RVC_ROOT as CWD
export PYTORCH_ENABLE_MPS_FALLBACK=1
export PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0
export KMP_DUPLICATE_LIB_OK=TRUE
# Disable MPS detection — hubert.to(mps) segfaults on M-series Mac
[ "$DEVICE" = "cpu" ] && export NO_MPS=1

python tools/infer_cli.py \
  --model_name "$MODEL_NAME" \
  --input_path "$INPUT_PATH" \
  --opt_path "$OUTPUT_PATH" \
  --index_path "$INDEX_PATH" \
  --f0up_key "$F0UP_KEY" \
  --f0method "$F0METHOD" \
  --index_rate "$INDEX_RATE" \
  --protect "$PROTECT" \
  --device "$DEVICE" \
  --filter_radius "$FILTER_RADIUS" \
  --rms_mix_rate "$RMS_MIX_RATE"

echo ""
echo "✓ Done: $OUTPUT_PATH"
ls -lh "$OUTPUT_PATH"
