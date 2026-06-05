#!/bin/sh
# 重做两首男声跨性别：+12 升八度过高导致撕裂，改用按实测 F0 算出的合理升调。
# 每首两档（围最优值），protect/index 仍激进档，filter_radius=5 平滑减毛刺。
# 旧 +12 版归档不删。
#
# 用法： nohup scripts/24_redo_male_songs.sh > "outputs/新歌试听_激进版/redo_male.log" 2>&1 &

set -e
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RVC_PY="$PROJECT_ROOT/frameworks/rvc-webui-macos/.venv/bin/python"
IDX=$(ls "$PROJECT_ROOT"/frameworks/rvc-webui-macos/logs/zhang-shaohan-phase2/added_*.index | head -1)
OUT="$PROJECT_ROOT/outputs/新歌试听_激进版"
ARC="$OUT/_废弃_升8度过高"
mkdir -p "$ARC"

log() { echo "[$(date '+%H:%M:%S')] === $* ==="; }

# 归档旧的 +12 版（含"男声升8度"字样的）
for f in "$OUT"/*男声升8度*.wav; do [ -f "$f" ] && mv "$f" "$ARC/" && echo "归档: $(basename "$f")"; done

# slug:f0up:中文名
JOBS="
geqian:3:搁浅-周杰伦_升3半音
geqian:5:搁浅-周杰伦_升5半音
xinzhongderiyue:5:心中的日月-王力宏_升5半音
xinzhongderiyue:7:心中的日月-王力宏_升7半音
"
for j in $JOBS; do
  slug=$(echo "$j" | cut -d: -f1); f0=$(echo "$j" | cut -d: -f2); cn=$(echo "$j" | cut -d: -f3)
  vin="$PROJECT_ROOT/dataset/source-songs/vocals/$slug.wav"
  inst="$PROJECT_ROOT/dataset/source-songs/no_vocals/$slug.wav"
  log "推理 $slug +$f0 半音 (protect0.20 idx0.90 fr5) → $cn"
  "$RVC_PY" "$PROJECT_ROOT/scripts/06_infer.py" \
    --model zhang-shaohan-phase2.pth --input "$vin" --output "/tmp/${slug}_${f0}_voc.wav" \
    --f0method rmvpe --protect 0.20 --index "$IDX" --index_rate 0.90 \
    --f0up_key "$f0" --filter_radius 5
  "$RVC_PY" "$PROJECT_ROOT/scripts/07_mix.py" \
    --vocals "/tmp/${slug}_${f0}_voc.wav" --instrumental "$inst" --output "/tmp/${slug}_${f0}_fin.wav"
  mv "/tmp/${slug}_${f0}_fin.wav" "$OUT/${cn}.wav"; rm -f "/tmp/${slug}_${f0}_voc.wav"
done
log "✓ 重做完成"
ls -lh "$OUT"/搁浅*.wav "$OUT"/心中的日月*.wav 2>/dev/null