#!/bin/sh
# 推理参数 sweep on Phase 1 model
# 跑 5 个变体，CPU 推理 (~3min each)。training 用 MPS，不冲突。
#
# 目标：找出能改善 Phase 1 的 2:15-2:30 撕裂段的参数组合
#
# 输出：outputs/phase1_sweep/<variant>.wav

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$PROJECT_ROOT/outputs/phase1_sweep"
mkdir -p "$OUT_DIR"

INPUT="$PROJECT_ROOT/dataset/source-songs/vocals/guangnianzhiwai.wav"
INST="$PROJECT_ROOT/dataset/source-songs/no_vocals/guangnianzhiwai.wav"
MODEL=zhang-shaohan-phase1.pth
INDEX="$PROJECT_ROOT/frameworks/rvc-webui-macos/logs/zhang-shaohan-phase1/added_IVF1592_Flat_nprobe_1_zhang-shaohan-phase1_v2.index"

RVC_PY="$PROJECT_ROOT/frameworks/rvc-webui-macos/.venv/bin/python"

run_variant() {
  local name=$1
  local f0method=$2
  local protect=$3
  local filter_radius=$4
  local idx_rate=$5
  local idx_arg=""
  if [ "$idx_rate" != "0" ]; then
    idx_arg="--index $INDEX --index_rate $idx_rate"
  else
    idx_arg="--index /tmp/__noindex__.index --index_rate 0"
  fi

  echo ""
  echo "========================================="
  echo "Variant: $name"
  echo "  f0=$f0method protect=$protect filter_radius=$filter_radius index_rate=$idx_rate"
  echo "========================================="

  "$RVC_PY" "$PROJECT_ROOT/scripts/06_infer.py" \
    --model "$MODEL" \
    --input "$INPUT" \
    --output "$OUT_DIR/${name}_vocals.wav" \
    --f0method "$f0method" \
    --protect "$protect" \
    --filter_radius "$filter_radius" \
    $idx_arg 2>&1 | tail -5

  if [ -f "$OUT_DIR/${name}_vocals.wav" ]; then
    "$RVC_PY" "$PROJECT_ROOT/scripts/07_mix.py" \
      --vocals "$OUT_DIR/${name}_vocals.wav" \
      --instrumental "$INST" \
      --output "$OUT_DIR/${name}_final.wav" 2>&1 | tail -2
  else
    echo "  ✗ output missing, skipped mix"
  fi
}

# v0: Phase 1 当前设置 (baseline reference, 应该 = outputs/phase1_demo/final.wav)
run_variant "v0_baseline"      harvest 0.5  3  0
# v1: 启用 index (faiss segfault 已修)
run_variant "v1_idx075"        harvest 0.5  3  0.75
# v2: lower protect = 更"张韶涵化"
run_variant "v2_protect033"    harvest 0.33 3  0.75
# v3: + filter_radius=5 平滑 f0
run_variant "v3_radius5"       harvest 0.33 5  0.75
# v4: 试 dio f0 (pyworld 另一算法)
run_variant "v4_dio"           dio     0.33 5  0.75

echo ""
echo "=== Sweep complete ==="
ls -lh "$OUT_DIR/"
