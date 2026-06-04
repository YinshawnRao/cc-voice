# 新机器引导（cc-voice bootstrap）

> git 里只有代码/文档/配置。音频、模型权重、venv、RVC 框架都不在 git 里，
> 换电脑后按本文复原。环境：macOS + Apple Silicon（MPS）。

## 0. 前置工具（Homebrew）

```bash
brew install python@3.10 ffmpeg yt-dlp aria2 git
```

Python 统一用 **3.10**（多数 SVC 框架的甜蜜点，3.11+ 部分依赖装不上）。

## 1. 克隆本仓库

```bash
git clone <你的私有远程> cc-voice
cd cc-voice
```

## 2. 复原 RVC 框架（含本项目的关键补丁）

框架本体不在 git 里，要重新 clone 并打补丁（来源见 `patches/rvc-source.txt`）：

```bash
git clone https://github.com/qingbo1011/RVC-WebUI-MacOS.git frameworks/rvc-webui-macos
cd frameworks/rvc-webui-macos
git checkout 0440e6d97a4564ba4219db5f5ba9505abd80437d
git apply ../../patches/rvc-macos.patch     # 6 处补丁：MPS / weights_only / faiss segfault
cd ../..
```

补丁没打 = 训练/推理必炸（详见 `docs/progress.md` §6 踩坑速查表）。

## 3. 建 venv + 装依赖

**RVC venv**（框架内）：

```bash
cd frameworks/rvc-webui-macos
python3.10 -m venv .venv && source .venv/bin/activate
pip install "pip<24.1"                 # 否则 omegaconf 旧版装不上（踩坑1）
pip install -r requirements.txt
pip install gradio_client==0.2.10      # 踩坑4
deactivate && cd ../..
```

**Demucs venv**（人声分离，独立环境避免依赖冲突）：

```bash
python3.10 -m venv venv/demucs && source venv/demucs/bin/activate
pip install demucs torchcodec          # torchcodec 必装，否则 demucs 报错（踩坑2）
deactivate
```

> 各框架 venv 之间 PyTorch/numpy/librosa 版本冲突严重，**不要试图统一**。

## 4. 下载底模（hubert / rmvpe / pretrained，~1.8G）

```bash
cd frameworks/rvc-webui-macos
bash tools/dlmodels.sh                  # 需要 aria2（已在第 0 步装）
cd ../..
```

## 5. cookies（yt-dlp 采集素材用）

把你自己的 cookies 放到项目根 `cookies.txt`（YouTube + B站合并，按 domain 自动匹配）。
**cookies 不在 git 里**（含登录凭证）。

## 6. 同步「训练好的模型」和「数据集」（git 之外）

这两样是大二进制，git 不收录，用云盘 / 移动硬盘 / rsync 同步：

| 内容 | 路径 | 说明 |
|---|---|---|
| 推理权重（小，~55M/个） | `frameworks/rvc-webui-macos/assets/weights/*.pth` | 成品模型，如 zhang-shaohan-phase2.pth |
| 训练 checkpoint（大，400-800M） | `frameworks/rvc-webui-macos/logs/<exp>/{G,D}_*.pth` | 续训/微调用；不需要就不传 |
| faiss 检索索引 | `frameworks/rvc-webui-macos/logs/<exp>/added_*.index` | 推理 retrieval 用 |
| 训练数据集 | `dataset/zhang-shaohan/{vocals,slices}` | 或用 `scripts/01-04` 从 playlist.txt 重新生成 |
| 源歌素材 | `dataset/source-songs/{vocals,no_vocals}` | 同上 |

数据集也可以**不传、靠脚本重建**（`raw/**/playlist.txt` 在 git 里）：
`scripts/01_download.sh → 02_separate.py → 03_slice.py → 04_quality_check.py`。

## 7. 验证

```bash
# MPS 可用？
frameworks/rvc-webui-macos/.venv/bin/python -c \
  "import torch; print('MPS:', torch.backends.mps.is_available())"

# 跑一首推理（确认全链路通）—— 激进档是当前选定基准
frameworks/rvc-webui-macos/.venv/bin/python scripts/06_infer.py \
  --model zhang-shaohan-phase2.pth \
  --input dataset/source-songs/vocals/xinqiang.wav \
  --output /tmp/test.wav \
  --f0method rmvpe --protect 0.20 --index_rate 0.90
```

跑通即环境复原成功。
