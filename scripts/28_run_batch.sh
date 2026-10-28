#!/bin/sh
# 通用批次跑歌：每批一个【时间戳独立目录】，用当前默认模型 phase3 出品。
# 不碰任何已有目录（如 outputs/默认模型成品_phase3/ 已冻结）。
#
# 用法:
#   scripts/28_run_batch.sh <配置文件> [标签]
#   → 输出到 outputs/batch_<YYYYMMDD-HHMM>[_标签]/（英文+数字命名），并把配置存一份进去留档
#
# 配置文件每行（'|' 分隔；'#' 注释；字段填 '-' 用默认）:
#   slug | youtube_id | f0 | protect | index | fr | 中文名
#   默认(填 '-'): f0=0 / protect=0.20 / index=0.90 / fr=3  (女声激进档)
#   youtube_id 填 '-' 表示该 slug 已下载过、不用再下
#   男声参考: f0=升调(搁浅+3/心中的日月+5) protect=0.50 index=0.50 fr=5

set -e
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RVC_PY="$PROJECT_ROOT/frameworks/rvc-webui-macos/.venv/bin/python"
DEMUCS_PY="$PROJECT_ROOT/venv/demucs/bin/python"
COOKIES="$PROJECT_ROOT/cookies.txt"
RAW="$PROJECT_ROOT/raw/source-songs"
IDX=$(ls "$PROJECT_ROOT"/frameworks/rvc-webui-macos/logs/zhang-shaohan-phase3/added_*.index | head -1)

CONF="$1"; LABEL="$2"
[ -f "$CONF" ] || { echo "用法: $0 <配置文件> [标签]"; exit 1; }
TS=$(date +%Y%m%d-%H%M)
# 批次目录用英文+数字命名（歌曲文件名仍用中文）
OUT="$PROJECT_ROOT/outputs/batch_${TS}${LABEL:+_$LABEL}"
mkdir -p "$OUT"
cp "$CONF" "$OUT/_batch_config.txt"
log() { echo "[$(date '+%H:%M:%S')] === $* ==="; }
log "批次目录: $OUT"
log "默认模型 phase3 / index=$(basename "$IDX")"

dflt() { [ "$1" = "-" ] || [ -z "$1" ] && echo "$2" || echo "$1"; }

# 1. 下载新歌
while IFS='|' read -r slug vid f0 prot ir fr cn; do
  slug=$(echo "$slug" | xargs); case "$slug" in ''|\#*) continue;; esac
  vid=$(echo "$vid" | xargs)
  { [ "$vid" = "-" ] || [ -z "$vid" ]; } && continue
  [ -f "$RAW/$slug.wav" ] && { echo "[skip dl] $slug"; continue; }
  log "下载 $slug"
  yt-dlp --cookies "$COOKIES" -f "bestaudio[ext=m4a]/bestaudio" \
    --extract-audio --audio-format wav --audio-quality 0 \
    -o "$RAW/$slug.%(ext)s" "https://www.youtube.com/watch?v=$vid" 2>&1 | grep -E "ExtractAudio|ERROR" | tail -1
done < "$CONF"

# 2. 分离新歌（02_separate 处理 raw/source-songs，已分离的 skip）
log "Demucs 分离"
"$DEMUCS_PY" "$PROJECT_ROOT/scripts/02_separate.py" 2>&1 | grep -E "→|skip|failed" || true

# 3. phase3 推理 + 混音
while IFS='|' read -r slug vid f0 prot ir fr cn; do
  slug=$(echo "$slug" | xargs); case "$slug" in ''|\#*) continue;; esac
  cn=$(echo "$cn" | xargs)
  f0=$(dflt "$(echo "$f0"|xargs)" 0); prot=$(dflt "$(echo "$prot"|xargs)" 0.20)
  ir=$(dflt "$(echo "$ir"|xargs)" 0.90); fr=$(dflt "$(echo "$fr"|xargs)" 3)
  vin="$PROJECT_ROOT/dataset/source-songs/vocals/$slug.wav"
  [ -f "$vin" ] || { echo "⚠️ 缺 $slug 干声，跳过"; continue; }
  log "$cn (f0+$f0 protect$prot index$ir fr$fr)"
  "$RVC_PY" "$PROJECT_ROOT/scripts/06_infer.py" \
    --model zhang-shaohan-phase3.pth --input "$vin" --output "/tmp/${slug}_b_voc.wav" \
    --f0method rmvpe --protect "$prot" --index "$IDX" --index_rate "$ir" \
    --f0up_key "$f0" --filter_radius "$fr"
  "$RVC_PY" "$PROJECT_ROOT/scripts/07_mix.py" \
    --vocals "/tmp/${slug}_b_voc.wav" \
    --instrumental "$PROJECT_ROOT/dataset/source-songs/no_vocals/$slug.wav" \
    --output "$OUT/${cn}.wav"
  rm -f "/tmp/${slug}_b_voc.wav"
done < "$CONF"

log "✓ 批次完成: $OUT"
ls -1 "$OUT"/*.wav 2>/dev/null | sed 's#.*/##'