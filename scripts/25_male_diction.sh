#!/bin/sh
# 男声咬字优化：protect=0.20(激进)把辅音抹糊了 → 提高 protect 保辅音、降 index 减发音平均。
# 用一点"音色像"换"咬字清"。每首两档（平衡 / 咬字优先），升调沿用建议值。
# 上一批 protect-0.20 的男声版归档不删。
#
# 用法： nohup scripts/25_male_diction.sh > "outputs/新歌试听_激进版/male_diction.log" 2>&1 &

set -e
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RVC_PY="$PROJECT_ROOT/frameworks/rvc-webui-macos/.venv/bin/python"
IDX=$(ls "$PROJECT_ROOT"/frameworks/rvc-webui-macos/logs/zhang-shaohan-phase2/added_*.index | head -1)
OUT="$PROJECT_ROOT/outputs/新歌试听_激进版"
ARC="$OUT/_废弃_咬字差_protect020"
mkdir -p "$ARC"
log() { echo "[$(date '+%H:%M:%S')] === $* ==="; }

# 归档上一批纯升调(protect0.20)的男声版
for f in "$OUT"/搁浅*升*半音.wav "$OUT"/心中的日月*升*半音.wav; do
  [ -f "$f" ] && mv "$f" "$ARC/" && echo "归档: $(basename "$f")"
done

# slug:f0up:protect:index:中文名
JOBS="
geqian:3:0.33:0.75:搁浅-周杰伦_升3_咬字平衡
geqian:3:0.50:0.50:搁浅-周杰伦_升3_咬字优先
xinzhongderiyue:5:0.33:0.75:心中的日月-王力宏_升5_咬字平衡
xinzhongderiyue:5:0.50:0.50:心中的日月-王力宏_升5_咬字优先
"
for j in $JOBS; do
  slug=$(echo "$j" | cut -d: -f1); f0=$(echo "$j" | cut -d: -f2)
  prot=$(echo "$j" | cut -d: -f3); ir=$(echo "$j" | cut -d: -f4); cn=$(echo "$j" | cut -d: -f5)
  vin="$PROJECT_ROOT/dataset/source-songs/vocals/$slug.wav"
  inst="$PROJECT_ROOT/dataset/source-songs/no_vocals/$slug.wav"
  log "$slug +$f0 protect=$prot index=$ir → $cn"
  "$RVC_PY" "$PROJECT_ROOT/scripts/06_infer.py" \
    --model zhang-shaohan-phase2.pth --input "$vin" --output "/tmp/${slug}_${prot}_voc.wav" \
    --f0method rmvpe --protect "$prot" --index "$IDX" --index_rate "$ir" \
    --f0up_key "$f0" --filter_radius 5
  "$RVC_PY" "$PROJECT_ROOT/scripts/07_mix.py" \
    --vocals "/tmp/${slug}_${prot}_voc.wav" --instrumental "$inst" --output "/tmp/${slug}_${prot}_fin.wav"
  mv "/tmp/${slug}_${prot}_fin.wav" "$OUT/${cn}.wav"; rm -f "/tmp/${slug}_${prot}_voc.wav"
done
log "✓ 完成"
ls -1 "$OUT"/搁浅*.wav "$OUT"/心中的日月*.wav 2>/dev/null | sed 's#.*/##'