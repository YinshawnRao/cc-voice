#!/bin/sh
# 候选训练素材体检：分离干声 + 量 F0 音域分布，判断每首是否填补"低音区缺口"。
# 不直接进训练集——先看数据再决定（呼应：别凭歌名猜音域）。
#
# 输入：raw/zhang-shaohan-candidates/*.wav
# 输出：dataset/zhang-shaohan-candidates/vocals/*.wav  +  控制台音域报告
#
# 参照基线（当前训练集 32.4min）：中位 F4 / <C4 仅 15.5% / <G3 仅 3.5%
# 目标：挑 <C4 占比高的，把低区拉上来。
#
# 用法（后台）：
#   nohup scripts/18_screen_candidates.sh > outputs/phase2_e200/candidate_screen.log 2>&1 &

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEMUCS_PY="$PROJECT_ROOT/venv/demucs/bin/python"
RVC_PY="$PROJECT_ROOT/frameworks/rvc-webui-macos/.venv/bin/python"
RAW="$PROJECT_ROOT/raw/zhang-shaohan-candidates"
VOC="$PROJECT_ROOT/dataset/zhang-shaohan-candidates/vocals"
TMP="$PROJECT_ROOT/dataset/.demucs_tmp_cand"
mkdir -p "$VOC"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] === $* ==="; }

# --- 分离（demucs two-stems, MPS）---
for f in "$RAW"/*.wav; do
  slug=$(basename "$f" .wav)
  if [ -f "$VOC/$slug.wav" ]; then echo "[skip sep] $slug"; continue; fi
  log "分离 $slug"
  "$DEMUCS_PY" -m demucs --two-stems vocals -n htdemucs -d mps -o "$TMP" "$f" 2>&1 | tail -1
  mv "$TMP/htdemucs/$slug/vocals.wav" "$VOC/$slug.wav"
done
rm -rf "$TMP"

# --- F0 音域体检 ---
log "F0 音域分析"
"$RVC_PY" - "$VOC" <<'PY'
import sys, glob, os, numpy as np, librosa, pyworld

names=["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]
def note(h):
    n=int(round(69+12*np.log2(h/440.0))); return f"{names[n%12]}{n//12-1}"

voc_dir=sys.argv[1]
files=sorted(glob.glob(os.path.join(voc_dir,"*.wav")))
print(f"\n{'候选':22} {'有声min':>7} {'中位':>10} {'<C4%':>6} {'<G3%':>6}  低区填充判定")
print("-"*78)
rows=[]
for f in files:
    y,sr=librosa.load(f,sr=16000,mono=True)
    f0,t=pyworld.harvest(y.astype(np.float64),sr,f0_floor=65,f0_ceil=1100,frame_period=10)
    v=f0[f0>0]
    if len(v)<50:  # 几乎无声
        print(f"{os.path.basename(f)[:22]:22}   (voiced 太少，跳过)"); continue
    dur=len(v)/100.0
    med=np.median(v)
    lowC4=100*np.mean(v<262); lowG3=100*np.mean(v<196)
    if lowC4>=35: verdict="✅ 强填充(低区多)"
    elif lowC4>=22: verdict="🟡 中等(略高于基线15%)"
    else: verdict="❌ 偏高，意义不大"
    rows.append((os.path.basename(f).replace('.wav',''),dur,med,lowC4,lowG3,verdict))
    print(f"{os.path.basename(f).replace('.wav','')[:22]:22} {dur/60:6.1f}m {med:6.0f}Hz {note(med):>4} {lowC4:5.1f}% {lowG3:5.1f}%  {verdict}")
print("-"*78)
print("基线(现训练集): 中位 352Hz F4 / <C4 15.5% / <G3 3.5%")
PY

log "✓ 体检完成"
