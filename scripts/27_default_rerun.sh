#!/bin/sh
# 用当前默认模型 phase3 把所有处理过的源歌重跑一遍，全中文命名，独立成品文件夹。
#   女声：激进档 protect0.20 / index0.90 / rmvpe / 不变调
#   男声：咬字优先 protect0.50 / index0.50 / filter_radius5 + 实测升调(搁浅+3 / 心中的日月+5)
#   突然想起你 / 心墙：phase3 已跑过，直接复制
#
# 输出：outputs/默认模型成品_phase3/<中文名>.wav
# 用法： nohup scripts/27_default_rerun.sh > "outputs/默认模型成品_phase3/run.log" 2>&1 &

set -e
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RVC_PY="$PROJECT_ROOT/frameworks/rvc-webui-macos/.venv/bin/python"
IDX=$(ls "$PROJECT_ROOT"/frameworks/rvc-webui-macos/logs/zhang-shaohan-phase3/added_*.index | head -1)
OUT="$PROJECT_ROOT/outputs/默认模型成品_phase3"
mkdir -p "$OUT"
log() { echo "[$(date '+%H:%M:%S')] === $* ==="; }
log "phase3 index = $IDX"

# slug:f0up:protect:index:filter_radius:中文名
SONGS="
aiqingxunxi:0:0.20:0.90:3:爱情讯息-郭静
caihong:0:0.20:0.90:3:彩虹-梁静茹
daoshu:0:0.20:0.90:3:倒数-萧亚轩
guangnianzhiwai:0:0.20:0.90:3:光年之外-邓紫棋
kubuchulai:0:0.20:0.90:3:哭不出来-张惠妹
lixiangqingren:0:0.20:0.90:3:理想情人-杨丞琳
manleng:0:0.20:0.90:3:慢冷-梁静茹
tiankongmeiyoujixian:0:0.20:0.90:3:天空没有极限-邓紫棋
yigerenshenghuo:0:0.20:0.90:3:一个人生活-林凡
yuai:0:0.20:0.90:3:雨爱-杨丞琳
geqian:3:0.50:0.50:5:搁浅-周杰伦
xinzhongderiyue:5:0.50:0.50:5:心中的日月-王力宏
"
for s in $SONGS; do
  slug=$(echo "$s" | cut -d: -f1); f0=$(echo "$s" | cut -d: -f2)
  prot=$(echo "$s" | cut -d: -f3); ir=$(echo "$s" | cut -d: -f4)
  fr=$(echo "$s" | cut -d: -f5); cn=$(echo "$s" | cut -d: -f6)
  vin="$PROJECT_ROOT/dataset/source-songs/vocals/$slug.wav"
  inst="$PROJECT_ROOT/dataset/source-songs/no_vocals/$slug.wav"
  [ -f "$vin" ] || { echo "⚠️ 缺 $slug 干声，跳过"; continue; }
  log "$cn (f0+$f0 protect$prot index$ir fr$fr)"
  "$RVC_PY" "$PROJECT_ROOT/scripts/06_infer.py" \
    --model zhang-shaohan-phase3.pth --input "$vin" --output "/tmp/${slug}_d_voc.wav" \
    --f0method rmvpe --protect "$prot" --index "$IDX" --index_rate "$ir" \
    --f0up_key "$f0" --filter_radius "$fr"
  "$RVC_PY" "$PROJECT_ROOT/scripts/07_mix.py" \
    --vocals "/tmp/${slug}_d_voc.wav" --instrumental "$inst" --output "$OUT/${cn}.wav"
  rm -f "/tmp/${slug}_d_voc.wav"
done

# 已跑过的 phase3 两首直接复制
cp "$PROJECT_ROOT/outputs/phase3_对比/突然想起你_phase3.wav" "$OUT/突然想起你-萧亚轩.wav" 2>/dev/null || echo "⚠️ 缺 突然想起你 phase3"
cp "$PROJECT_ROOT/outputs/phase3_对比/心墙_phase3.wav"       "$OUT/心墙-郭静.wav"       2>/dev/null || echo "⚠️ 缺 心墙 phase3"

log "✓ 全部完成（应 14 首）"
ls -1 "$OUT"/*.wav | sed 's#.*/##'