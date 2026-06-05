#!/bin/sh
# 一批新歌试听：统一 e200 + 激进档（protect 0.20 / index 0.90 / rmvpe），每首一版。
# 男声歌 f0 升八度(+12)做跨性别转换；女声 f0=0。
# 中间处理用拼音 slug（避中文路径踩坑3），最终成品中文命名。
#
# 输出：outputs/新歌试听_激进版/<中文名>.wav
# 用法（后台）：
#   nohup scripts/23_new_songs_aggressive.sh > "outputs/新歌试听_激进版/run.log" 2>&1 &

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RVC_PY="$PROJECT_ROOT/frameworks/rvc-webui-macos/.venv/bin/python"
DEMUCS_PY="$PROJECT_ROOT/venv/demucs/bin/python"
COOKIES="$PROJECT_ROOT/cookies.txt"
RAW="$PROJECT_ROOT/raw/source-songs"
IDX=$(ls "$PROJECT_ROOT"/frameworks/rvc-webui-macos/logs/zhang-shaohan-phase2/added_*.index | head -1)
OUT="$PROJECT_ROOT/outputs/新歌试听_激进版"
mkdir -p "$OUT" "$RAW"

log() { echo "[$(date '+%H:%M:%S')] === $* ==="; }

# slug:youtube_id:f0up:中文名（中文名用 _ 不用空格）
SONGS="
caihong:HnnXkxlyuM8:0:彩虹-梁静茹
kubuchulai:TN4zgS5gEzw:0:哭不出来-张惠妹
yigerenshenghuo:hUJl1HwYIN8:0:一个人生活-林凡
daoshu:GIJKAr3rN30:0:倒数-萧亚轩
aiqingxunxi:t7Q-MkGfdHg:0:爱情讯息-郭静
geqian:YJfHuATJYsQ:12:搁浅-周杰伦_男声升8度
manleng:2LuW7acW9B8:0:慢冷-梁静茹
xinzhongderiyue:2B2IX7yTFvg:12:心中的日月-王力宏_男声升8度
"

# 1. 下载
for line in $SONGS; do
  slug=$(echo "$line" | cut -d: -f1); vid=$(echo "$line" | cut -d: -f2)
  if [ -f "$RAW/$slug.wav" ]; then echo "[skip dl] $slug"; continue; fi
  log "下载 $slug ($vid)"
  yt-dlp --cookies "$COOKIES" -f "bestaudio[ext=m4a]/bestaudio" \
    --extract-audio --audio-format wav --audio-quality 0 \
    -o "$RAW/$slug.%(ext)s" "https://www.youtube.com/watch?v=$vid" 2>&1 | grep -E "ExtractAudio|ERROR" | tail -1
done

# 2. 分离（MPS）
log "Demucs 分离（新歌，已存在的 skip）"
"$DEMUCS_PY" "$PROJECT_ROOT/scripts/02_separate.py" 2>&1 | grep -E "demucs|→|skip|failed" || true

# 3. 推理 + 混音
for line in $SONGS; do
  slug=$(echo "$line" | cut -d: -f1)
  f0=$(echo "$line" | cut -d: -f3); cn=$(echo "$line" | cut -d: -f4)
  vin="$PROJECT_ROOT/dataset/source-songs/vocals/$slug.wav"
  inst="$PROJECT_ROOT/dataset/source-songs/no_vocals/$slug.wav"
  if [ ! -f "$vin" ]; then echo "⚠️ $slug 无分离干声，跳过"; continue; fi
  log "推理 $slug (f0up=$f0) → $cn"
  "$RVC_PY" "$PROJECT_ROOT/scripts/06_infer.py" \
    --model zhang-shaohan-phase2.pth --input "$vin" --output "/tmp/${slug}_voc.wav" \
    --f0method rmvpe --protect 0.20 --index "$IDX" --index_rate 0.90 --f0up_key "$f0"
  "$RVC_PY" "$PROJECT_ROOT/scripts/07_mix.py" \
    --vocals "/tmp/${slug}_voc.wav" --instrumental "$inst" --output "/tmp/${slug}_fin.wav"
  mv "/tmp/${slug}_fin.wav" "$OUT/${cn}.wav"   # 拼音处理完，最终中文命名
  rm -f "/tmp/${slug}_voc.wav"
done

log "✓ 全部完成"
ls -lh "$OUT"/*.wav 2>/dev/null