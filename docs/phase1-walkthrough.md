# Phase 1 · 自训 张韶涵 RVC v2 模型流程

> 本文档同步记录 Phase 1 实操步骤、参数选择、踩坑点。运行中持续更新。

---

## 0. 背景

按 `docs/decisions/ADR-001` 决定，Phase 1 改为自训路线。
目标：在 M4 Pro MPS 上跑通"采集 → 分离 → 切片 → 训练 → 推理 → 混音"全链路，产出一首可听 demo。

---

## 1. 环境

| 组件 | 版本 / 路径 | 备注 |
|---|---|---|
| OS | macOS 15.6 (24G84) | Sequoia |
| Chip | Apple M4 Pro / 48GB UMA | |
| Python | 3.10.20 (brew) | CLAUDE.md §4.1 强制 |
| RVC | qingbo1011/RVC-WebUI-MacOS @ HEAD | macOS 优化 fork |
| RVC venv | `frameworks/rvc-webui-macos/.venv` | torch 2.12.0 + MPS |
| Demucs venv | `venv/demucs/` | demucs 4.0.1 + torchcodec 0.13 |

**关键环境变量**（RVC 的 `run.sh` 自动设置）：
```sh
PYTORCH_ENABLE_MPS_FALLBACK=1         # 不支持的 op 回退 CPU
PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0  # 解除 MPS 内存限
KMP_DUPLICATE_LIB_OK=TRUE             # faiss + PyTorch OpenMP 冲突
OMP_NUM_THREADS=1
```

---

## 2. 数据准备

### 2.1 素材采集

`scripts/01_download.sh` + `raw/*/playlist.txt`。
yt-dlp 用合并的 B 站+YouTube cookies (`cookies.txt`)。
统一转 wav 44.1kHz mono。

实际下载（Phase 1）：

| 曲目 | BV | 大小 | 类型 |
|---|---|---|---|
| 隐形的翅膀 (录音棚 4K) | BV1Mz421d7uo | 19M | studio |
| 欧若拉 (4K MV) | BV1u9cTzWEjw | 22M | studio |
| 阿刁 (我是歌手 4K) | BV16NqVYqEjy | 32M | live |
| 淋雨一直走 (官方 HD MV) | BV1ws411H7tT | 18M | studio |
| 梦里花 (我是歌手 MV) | BV1LW411B7gh | 19M | studio |
| 看得最远的地方 (4K 修复 MV) | BV1UL41137sS | 21M | studio |
| 邓紫棋 光年之外 (源歌) | BV13q4y1T7xc | 20M | studio |

### 2.2 人声分离

`scripts/02_separate.py` → Demucs htdemucs (Hybrid Transformer Demucs)。

**踩坑 1（已修）**：Demucs 4.0.1 + torchaudio 2.11.0 在 save 时报 `ImportError: TorchCodec is required`。
解：`pip install torchcodec` (0.13.0)。

**踩坑 2（已修）**：RVC requirements.txt 锁 `gradio==3.34.0`，但 pip 装出的 `gradio_client==2.5.0` 是新版 API，跟 gradio 3.34 不兼容（`ImportError: cannot import name 'media_data' from 'gradio_client'`）。
解：`pip install "gradio_client==0.2.10"`（gradio 3.34 同期版本）。

**踩坑 3（已避免）**：中文文件名 + RVC ffmpeg 调用。
解：`scripts/00_rename.py` 全部转 ASCII slug，`dataset/filename_map.json` 记录映射。

**踩坑 4（已修）**：`infer-web.py` 的 F0 提取分支逻辑：
```python
if gpus_rmvpe != "-":  # 多 GPU 路径 → extract_f0_rmvpe.py
    ...
else:                  # 单设备路径 → extract_f0_rmvpe_dml.py (Windows DirectML 专用!)
```
Mac 用户被 UI 文案误导，看到"卡号"以为 `-` 是"没有卡=不指定"，结果走 DML 路径必炸。
解：
1. WebUI 里 **"rmvpe卡号配置" 填 `0`**（不是 `-`）。
2. patch `extract_f0_rmvpe.py:53` 让 RMVPE 根据 `torch.backends.mps.is_available()` 选 mps/cuda/cpu。

**踩坑 5（已修）**：fairseq + PyTorch 2.6+ 的 `weights_only=True` 默认值。
fork 在推理路径（`rmvpe.py`, `vc/modules.py`）修了，但漏修训练路径。
解：`extract_feature_print.py` 顶部 monkeypatch `torch.load` 默认 `weights_only=False`；`train.py` 显式加参数。文件来源是 `dlmodels.sh` 从 RVC 官方 huggingface 仓库下，本地受信任，acceptable trade-off。

MPS 加速：每首 ~30s-1min；CPU 回退 ~2-4min。
源歌额外保留 `no_vocals/`（混音用），训练素材只保留 vocals。

### 2.3 切片 + 质检

`scripts/03_slice.py`：librosa.effects.split, top_db=30, 5-15s/段。
`scripts/04_quality_check.py`：RMS / peak / 时长 / 采样率检查，生成 manifest.json。

CLAUDE.md §4.3 要求人工抽听 10%。脚本会列出建议抽听片段。

---

## 3. RVC 训练

### 3.1 启动 Web UI

```bash
cd frameworks/rvc-webui-macos
./run.sh
# 浏览器打开 http://127.0.0.1:7865
```

### 3.2 训练 Tab 操作顺序（Phase 1 参数）

1. **输入实验名**：`zhang-shaohan-phase1`
2. **目标采样率**：`40k` (v2 推荐 40k，速度与质量平衡；48k 仅适合干净 studio 数据)
3. **模型版本**：`v2` (CLAUDE.md §2.3)
4. **是否有音高**：`true` (歌唱必选)
5. **数据集目录**：填 `/Users/yinshawnrao/explorer/cc-voice/dataset/zhang-shaohan/slices`
6. **音高提取算法**：`rmvpe_gpu` (优于 harvest/crepe，已下底模)
7. **预处理**：点 "处理数据" → 等到提示完成
8. **特征提取**：点 "特征提取" → MPS 加速，~5-10 min
9. **训练参数**：
   - `保存频率 save_every_epoch` = 50
   - `总训练轮数 total_epoch` = 100  ← Phase 1 极轻
   - `每张显卡的批次大小 batch_size_per_gpu` = 4 (MPS 安全值，M4 Pro 48GB 可调大但保守起步)
   - `是否仅保存最新` = `yes` (省磁盘)
   - `是否缓存所有训练集至显存` = `yes` (40 min 数据集足够小)
10. **训练**：点 "训练模型" → 估算 1-2h
11. **训练索引**：训练完后点 "训练特征索引" → 生成 `.index` 文件（推理时检索用，提升音色相似度）

### 3.3 监控训练

- 训练日志：`logs/zhang-shaohan-phase1/train.log`
- TensorBoard：`tensorboard --logdir logs/`
- 关注指标：`loss_g_total` 应下降；`loss_g_f0` 应在 50 epoch 后稳定 < 5

### 3.4 中断 / 续训

`run.sh` 已设置 `-c 1`（最新模型续训）。如 MPS 内存爆炸：减 batch 到 2 或 1，重启 webui，从最新 checkpoint 续。

---

## 4. 推理

训练产物：
- 权重：`assets/weights/zhang-shaohan-phase1.pth`
- 索引：`logs/zhang-shaohan-phase1/added_*.index`

Web UI "模型推理 Tab"：

1. **模型**：`zhang-shaohan-phase1.pth`
2. **音高变化**：`0` (原源歌为女声，张韶涵也是女声，无需移调)
3. **输入音频路径**：`dataset/source-songs/vocals/光年之外...wav` (注意：用分离后的人声，不是原 mp3/wav)
4. **音高提取算法**：`rmvpe`
5. **特征检索库索引**：选 added_*.index
6. **特征检索占比**：`0.75` (越高越像目标音色，但可能损失原唱表达力)
7. **声音保护**：`0.5` (默认)
8. **输出**：`outputs/phase1_demo/converted_vocals.wav`

---

## 5. 混音

```bash
cd /Users/yinshawnrao/explorer/cc-voice
. frameworks/rvc-webui-macos/.venv/bin/activate
python scripts/07_mix.py \
  --vocals outputs/phase1_demo/converted_vocals.wav \
  --instrumental dataset/source-songs/no_vocals/G.E.M.*.wav \
  --output outputs/phase1_demo/final.wav
```

---

## 6. Phase 1 验收标准

不是"像不像张韶涵"，而是：

- ✅ 整条 pipeline 跑完无错
- ✅ 成品音频 ≥ 60s 可听（不爆音、不卡顿）
- ✅ 能听出"换了人在唱"（即使不像张韶涵）
- ✅ 时间轴跟原曲对齐

如以上任一不达标 → 进入 framework-notes/ 记录失败模式 → 调整 Phase 2 计划。

---

## 7. 实测记录（边跑边填）

### 7.1 数据集统计

> （04_quality_check.py 输出后填）

### 7.2 训练实测

| Metric | Value |
|---|---|
| 数据集时长 | TBD |
| Preprocess 耗时 | TBD |
| Feature extract 耗时 | TBD |
| Per-epoch 耗时 (avg) | TBD |
| 100-epoch 总耗时 | TBD |
| 最终 loss_g_total | TBD |
| 显存峰值 | TBD |

### 7.3 推理实测

> （推理完后填）
