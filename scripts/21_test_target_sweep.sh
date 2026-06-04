#!/bin/sh
# 测试目标参数 sweep：突然想起你(萧亚轩) / 心墙(郭静) —— 已确认是适合张韶涵音色的歌。
# 固定 e200 模型 + rmvpe + 不变调，扫 protect × index_rate 网格，挑最佳档。
#   protect    ∈ {0.33, 0.20}   越低音色替换越彻底（越像，过低易电音/呼吸音怪）
#   index_rate ∈ {0.50, 0.75, 0.90}  retrieval 强度，越高越往训练音色拉（过高易过平滑）
# 每首 6 版 = 12 个 final。
#
# 输出：outputs/phase2_e200/test_targets/{song}_e200_p{NN}_idx{NNN}_{vocals,final}.wav
#
# 用法（后台）：
#   nohup scripts/21_test_target_sweep.sh > outputs/phase2_e200/test_targets/sweep.log 2>&1 &

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RVC_PY="$PROJECT_ROOT/frameworks/rvc-webui-macos/.venv/bin/python"
IDX=$(ls "$PROJECT_ROOT"/frameworks/rvc-webui-macos/logs/zhang-shaohan-phase2/added_*.index | head -1)
OUT="$PROJECT_ROOT/outputs/phase2_e200/test_targets"
mkdir -p "$OUT"

SONGS="turanxiangqini xinqiang"
# "protect:ptag"
PROTECTS="0.33:33 0.20:20"
# "index_rate:itag"
INDEX_RATES="0.50:050 0.75:075 0.90:090"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] === $* ==="; }

for song in $SONGS; do
  IN="$PROJECT_ROOT/dataset/source-songs/vocals/${song}.wav"
  INST="$PROJECT_ROOT/dataset/source-songs/no_vocals/${song}.wav"
  for pp in $PROTECTS; do
    p=${pp%:*}; ptag=${pp#*:}
    for ii in $INDEX_RATES; do
      ir=${ii%:*}; itag=${ii#*:}
      name="p${ptag}_idx${itag}"
      voc="$OUT/${song}_e200_${name}_vocals.wav"
      fin="$OUT/${song}_e200_${name}_final.wav"
      log "$song · $name (protect=$p index_rate=$ir)"
      "$RVC_PY" "$PROJECT_ROOT/scripts/06_infer.py" \
        --model zhang-shaohan-phase2.pth --input "$IN" --output "$voc" \
        --f0method rmvpe --protect "$p" --f0up_key 0 \
        --index "$IDX" --index_rate "$ir"
      "$RVC_PY" "$PROJECT_ROOT/scripts/07_mix.py" \
        --vocals "$voc" --instrumental "$INST" --output "$fin"
    done
  done
done

log "✓ sweep 完成"
ls -1 "$OUT"/*_final.wav | sed 's#.*/##'
