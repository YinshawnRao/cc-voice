#!/bin/sh
# 批量下载 playlist.txt 里的 URL → .wav 文件
# 用法：scripts/01_download.sh
#
# 处理两个 playlist：
#   raw/zhang-shaohan/playlist.txt  → raw/zhang-shaohan/*.wav  (训练素材)
#   raw/source-songs/playlist.txt   → raw/source-songs/*.wav   (待翻唱源歌)
#
# 关键设计：
# - cookies.txt 合并 B 站 + YouTube cookies，yt-dlp 按 domain 自动匹配
# - 提取 wav 44.1kHz mono (RVC 训练标准格式)
# - --download-archive 防重复下载，断点续传
# - --ignore-errors 单个失败不中断批量

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COOKIES="$PROJECT_ROOT/cookies.txt"

if [ ! -f "$COOKIES" ]; then
  echo "ERROR: cookies file missing: $COOKIES"
  exit 1
fi

download_batch() {
  local label="$1"
  local playlist="$2"
  local output_dir="$3"
  local archive="$output_dir/.downloaded.txt"

  if [ ! -f "$playlist" ]; then
    echo "[$label] playlist missing: $playlist — skip"
    return 0
  fi

  mkdir -p "$output_dir"

  echo ""
  echo "=== [$label] $playlist → $output_dir ==="

  yt-dlp \
    --cookies "$COOKIES" \
    -f "bestaudio[ext=m4a]/bestaudio" \
    --extract-audio \
    --audio-format wav \
    --audio-quality 0 \
    --postprocessor-args "ffmpeg:-ar 44100 -ac 1" \
    -o "$output_dir/%(title)s.%(ext)s" \
    --download-archive "$archive" \
    --batch-file "$playlist" \
    --no-overwrites \
    --ignore-errors \
    --progress
}

download_batch "training" \
  "$PROJECT_ROOT/raw/zhang-shaohan/playlist.txt" \
  "$PROJECT_ROOT/raw/zhang-shaohan"

download_batch "source-songs" \
  "$PROJECT_ROOT/raw/source-songs/playlist.txt" \
  "$PROJECT_ROOT/raw/source-songs"

echo ""
echo "=== Summary ==="
echo "Training samples:"
ls -lh "$PROJECT_ROOT/raw/zhang-shaohan/"*.wav 2>/dev/null | awk '{print "  " $9, "(" $5 ")"}' || echo "  (none)"
echo "Source songs:"
ls -lh "$PROJECT_ROOT/raw/source-songs/"*.wav 2>/dev/null | awk '{print "  " $9, "(" $5 ")"}' || echo "  (none)"
