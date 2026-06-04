#!/bin/sh
# Phase 3 微调：在 e200 基础上加 5 首近期中低音歌，warm-start 继续训练。
# 非破坏：全新实验 zhang-shaohan-phase3，绝不覆盖 phase2/e200。
#
# 数据：dataset/zhang-shaohan/slices （261 片 = 原186 + 新75，已"只加不删"）
# warm-start：phase2 的 G_2333333.pth / D_2333333.pth（=e200 权重）做 pretrained
# 早停策略：save_every=10，练出 e10/e20/e30... 逐个 A/B，够好就 kill，不必跑满
#
# 用法（后台）：
#   nohup scripts/22_phase3_finetune.sh > outputs/phase2_e200/phase3_train.log 2>&1 &
#   tail -f outputs/phase2_e200/phase3_train.log

set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RVC_ROOT="$PROJECT_ROOT/frameworks/rvc-webui-macos"
RVC_PY="$RVC_ROOT/.venv/bin/python"

EXP="zhang-shaohan-phase3"
EXP_DIR="$RVC_ROOT/logs/$EXP"
DATASET_DIR="$PROJECT_ROOT/dataset/zhang-shaohan/slices"
SR="40k"; VERSION="v2"; BATCH_SIZE=4; GPUS="0"
TOTAL_EPOCH=50; SAVE_EVERY=10
WARM_G="logs/zhang-shaohan-phase2/G_2333333.pth"   # e200 generator
WARM_D="logs/zhang-shaohan-phase2/D_2333333.pth"   # e200 discriminator

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] === $* ==="; }

# 安全护栏：warm-start checkpoint 必须存在，且绝不能误写 phase2 目录
[ -f "$RVC_ROOT/$WARM_G" ] && [ -f "$RVC_ROOT/$WARM_D" ] || { echo "FATAL: 缺 e200 warm-start checkpoint"; exit 1; }
case "$EXP" in *phase2*) echo "FATAL: EXP 名含 phase2，中止以防覆盖 e200"; exit 1;; esac

cd "$RVC_ROOT"
export PYTORCH_ENABLE_MPS_FALLBACK=1
export PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0
export KMP_DUPLICATE_LIB_OK=TRUE
export OMP_NUM_THREADS=1
mkdir -p "$EXP_DIR"

# config.json（v2+40k 实际用 v1 config，与 phase2 一致）
"$RVC_PY" <<PYEOF
import json, os
sr="$SR"; version="$VERSION"
sub="v1" if (version=="v1" or sr=="40k") else "v2"
src=os.path.join("$RVC_ROOT", f"configs/inuse/{sub}/{sr}.json")
if not os.path.exists(src): src=os.path.join("$RVC_ROOT", f"configs/{sub}/{sr}.json")
cfg=json.load(open(src))
json.dump(cfg, open(os.path.join("$EXP_DIR","config.json"),"w"), ensure_ascii=False, indent=4, sort_keys=True)
print("config.json written")
PYEOF

log "Stage 1/5 · preprocess (resample 40k + 3s sub-slice) · 261 片"
"$RVC_PY" infer/modules/train/preprocess.py "$DATASET_DIR" 40000 8 "$EXP_DIR" False 3.0

log "Stage 2/5 · F0 extract (rmvpe + MPS)"
"$RVC_PY" infer/modules/train/extract/extract_f0_rmvpe.py 1 0 0 "$EXP_DIR" False

log "Stage 3/5 · Hubert feature extract (MPS)"
"$RVC_PY" infer/modules/train/extract_feature_print.py mps 1 0 "$EXP_DIR" "$VERSION" False

log "Stage 4/5 · filelist.txt"
"$RVC_PY" <<PYEOF
import os, random
exp_dir="$EXP_DIR"; mute_base=os.path.join("$RVC_ROOT","logs/mute"); sr2="$SR"; spk_id=0
gt=os.path.join(exp_dir,"0_gt_wavs"); fea=os.path.join(exp_dir,"3_feature768")
f0=os.path.join(exp_dir,"2a_f0"); f0n=os.path.join(exp_dir,"2b-f0nsf")
names=(set(n.split(".")[0] for n in os.listdir(gt)) & set(n.split(".")[0] for n in os.listdir(fea))
       & set(n.split(".")[0] for n in os.listdir(f0)) & set(n.split(".")[0] for n in os.listdir(f0n)))
opt=[f"{gt}/{n}.wav|{fea}/{n}.npy|{f0}/{n}.wav.npy|{f0n}/{n}.wav.npy|{spk_id}" for n in sorted(names)]
for _ in range(2):
    opt.append(f"{mute_base}/0_gt_wavs/mute{sr2}.wav|{mute_base}/3_feature768/mute.npy|{mute_base}/2a_f0/mute.wav.npy|{mute_base}/2b-f0nsf/mute.wav.npy|{spk_id}")
random.seed(42); random.shuffle(opt)
open(os.path.join(exp_dir,"filelist.txt"),"w").write("\n".join(opt))
print(f"filelist: {len(opt)} entries")
PYEOF

log "Stage 5/5 · train (warm-start from e200) · $TOTAL_EPOCH epoch · save_every $SAVE_EVERY"
"$RVC_PY" infer/modules/train/train.py \
  -e "$EXP" -sr "$SR" -f0 1 -bs $BATCH_SIZE -g $GPUS \
  -te $TOTAL_EPOCH -se $SAVE_EVERY \
  -pg "$WARM_G" -pd "$WARM_D" \
  -l 1 -c 0 -sw 1 -v "$VERSION"

log "✓ Phase 3 训练完成。权重在 assets/weights/${EXP}*.pth"
ls -lh "$RVC_ROOT/assets/weights/${EXP}"*.pth 2>/dev/null
