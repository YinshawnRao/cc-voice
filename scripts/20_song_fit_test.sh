#!/bin/sh
# 歌曲适配性测试：固定 e200 模型 + p033_idx075（用户验证的最佳档），只换源歌，
# 验证"音色不够像"是否与歌曲本身有关（光年之外是邓紫棋极具辨识度的招牌嗓，最难洗）。
#
# 测试曲（中音区、嗓音较柔，对照组）：
#   突然想起你 - 萧亚轩  (turanxiangqini)
#   心墙       - 郭静    (xinqiang)
#
# 输出：outputs/phase2_e200/song_fit_test/{song}_e200_p033_idx075_{vocals,final}.wav
#
# 用法（后台）：
#   nohup scripts/20_song_fit_test.sh > outputs/phase2_e200/song_fit_test.log 2>&1 &

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEMUCS_PY="$PROJECT_ROOT/venv/demucs/bin/python"
RVC_PY="$PROJECT_ROOT/frameworks/rvc-webui-macos/.venv/bin/python"
IDX=$(ls "$PROJECT_ROOT"/frameworks/rvc-webui-macos/logs/zhang-shaohan-phase2/added_*.index | head -1)
OUT="$PROJECT_ROOT/outputs/phase2_e200/song_fit_test"
mkdir -p "$OUT"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] === $* ==="; }

# 1. 分离（02_separate.py 处理 raw/source-songs，已存在的 skip）
log "Demucs 分离（新增 2 首）"
"$DEMUCS_PY" "$PROJECT_ROOT/scripts/02_separate.py" 2>&1 | grep -E "demucs|→|skip|failed" || true

# 2. 人声 stem 体检：voiced 比例太低 = 可能是无主唱卡拉OK轨
log "人声 stem 体检（voiced 比例 / 中位音高）"
"$RVC_PY" - "$PROJECT_ROOT/dataset/source-songs/vocals" turanxiangqini xinqiang <<'PY'
import sys, os, numpy as np, librosa, pyworld
names=["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
note=lambda h:f"{names[int(round(69+12*np.log2(h/440)))%12]}{int(round(69+12*np.log2(h/440)))//12-1}"
voc=sys.argv[1]
for slug in sys.argv[2:]:
    f=os.path.join(voc,slug+".wav")
    if not os.path.exists(f): print(f"  {slug}: 缺失"); continue
    y,sr=librosa.load(f,sr=16000,mono=True)
    f0,_=pyworld.harvest(y.astype(np.float64),sr,f0_floor=65,f0_ceil=1100,frame_period=10)
    vr=np.mean(f0>0); v=f0[f0>0]
    flag="⚠️ voiced 过低,疑似无主唱!" if vr<0.15 else "OK"
    med=np.median(v) if len(v) else 0
    print(f"  {slug:18} voiced={vr*100:4.1f}%  中位={med:5.0f}Hz {note(med) if med else '-':>4}  {flag}")
PY

# 3. 推理 @ p033_idx075 + 混音
for song in turanxiangqini xinqiang; do
  voc="$OUT/${song}_e200_p033_idx075_vocals.wav"
  fin="$OUT/${song}_e200_p033_idx075_final.wav"
  log "推理 $song (protect=0.33 index_rate=0.75)"
  "$RVC_PY" "$PROJECT_ROOT/scripts/06_infer.py" \
    --model zhang-shaohan-phase2.pth \
    --input "$PROJECT_ROOT/dataset/source-songs/vocals/${song}.wav" \
    --output "$voc" --f0method rmvpe --protect 0.33 --f0up_key 0 \
    --index "$IDX" --index_rate 0.75
  log "混音 $song"
  "$RVC_PY" "$PROJECT_ROOT/scripts/07_mix.py" \
    --vocals "$voc" --instrumental "$PROJECT_ROOT/dataset/source-songs/no_vocals/${song}.wav" \
    --output "$fin"
done

log "✓ 适配性测试完成"
ls -lh "$OUT"/*_final.wav
