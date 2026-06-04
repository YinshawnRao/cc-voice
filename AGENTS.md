# AI Singing Voice Cloning · 调研项目

> 一个端到端的歌声音色克隆（SVC, Singing Voice Conversion）调研工程。目标是把 B 站爆款"AI 张韶涵翻唱 XXX"那一套流程，从原理、工具链、效果三个层面跑明白，并产出可复用的工作流和选型结论。

---

## 1. Project Context

### 1.1 调研目标（三阶段递进）

本项目分三阶段推进，每个阶段都有明确的交付物，**不要跳阶段**：

**Phase 1 · 技术摸底（先打通端到端）**
- 目标：用社区现成的预训练模型，跑通"素材→分离→推理→混音"全链路，产出一首可听的 demo。
- 不追求音色像、不追求音质好，**只追求流程通**。
- 关键产物：`outputs/phase1_demo/` 下的成品音频 + `docs/phase1-walkthrough.md` 流程笔记。

**Phase 2 · 方案选型（横向对比）**
- 目标：在统一素材、统一目标音色（张韶涵）、统一评测曲目下，横向对比主流 SVC 框架。
- 维度：训练成本、推理速度、音质（MOS 主观打分 + 频谱客观对比）、Apple Silicon 兼容性、社区活跃度、上手难度。
- 关键产物：`docs/phase2-comparison-report.md` 选型报告，包含决策矩阵。

**Phase 3 · 效果对标（复刻爆款水平）**
- 目标：用 Phase 2 选定的方案 + 自训练或精挑细选的张韶涵模型，做出一首能跟 B 站百万播放视频对标的成品。
- 关键产物：`outputs/phase3_final/` 成品 + `docs/phase3-postmortem.md` 复盘。

每阶段完成后，先停下来等用户确认，再启动下一阶段。**不要默认连跑**。

### 1.2 用户背景

- 用户是 Tencent Cloud COS 控制台的前端工程师，熟悉 Codex 多实例并行工作流（git worktree）。
- ML 工程经验有限，更关注**工程化流程和选型方法论**，而非论文细节。
- 解释技术原理时可以深入，但避免堆砌数学公式；多用类比和"为什么这么设计"。

### 1.3 硬件环境

- MacBook Pro M4 Pro（具体显存以 `system_profiler SPHardwareDataType` 实测为准）
- macOS Sequoia 或 Tahoe（运行 `sw_vers` 确认）
- 优先使用 MPS（Metal Performance Shaders）加速；不可用时降级 CPU
- **训练策略**：MPS 能跑就本地训，跑不动或速度不可接受时立即切换到"用社区预训练模型推理"模式，不要在 MPS 兼容性问题上死磕超过 1 小时

---

## 2. 技术背景知识（Codex 必读）

### 2.1 SVC vs TTS vs VC

不要混淆三个概念，选错框架白干：

| 类型 | 输入 | 输出 | 代表项目 | 适合本项目? |
|---|---|---|---|---|
| **TTS**（语音合成） | 文本 | 语音 | GPT-SoVITS, CosyVoice2, Fish Speech | ❌ 不适合唱歌 |
| **VC**（说话音色转换） | 说话音频 | 说话音频（换音色） | OpenVoice | ❌ 唱歌音准崩 |
| **SVC**（歌声音色转换） | 唱歌音频 | 唱歌音频（换音色） | RVC, So-VITS-SVC, DDSP-SVC | ✅ **本项目目标** |

"AI 孙燕姿/张韶涵"现象本质是 **SVC**，不是从零合成歌声。

### 2.2 完整流程链路

```
[YouTube/B站 素材] 
    ↓ yt-dlp（用户已有 cookie 文件）
[原始音频 .m4a/.webm]
    ↓ ffmpeg 转 wav 44.1kHz 16bit mono
[原始 wav]
    ↓ UVR5 / Demucs（人声分离）
[干声 vocal.wav] + [伴奏 instrumental.wav]
    ↓ 切片（10-30s 片段）+ 降噪 + 人工筛选
[训练集 dataset/]
    ↓ SVC 框架训练（或跳过，用社区模型）
[音色模型 .pth]
    ↓ 对另一首歌的干声推理
[转换后的人声 converted.wav]
    ↓ 与原曲伴奏混音对齐（pydub / DAW）
[成品 final.wav]
```

### 2.3 主流 SVC 框架对比（先验认知，需在 Phase 2 实测验证）

| 框架 | 训练速度 | 音质 | Mac/MPS 友好度 | 社区现成模型 | 备注 |
|---|---|---|---|---|---|
| **RVC** (Retrieval-based VC) | 快 | 高 | 中（社区有 MacOS fork：`qingbo1011/RVC-WebUI-MacOS`、`NevilPatel01/RVC-WebUI-MacOS`） | **极多** | 当前最主流，B 站爆款基本都用它 |
| **So-VITS-SVC** | 慢 | 极高 | 差（MPS 支持不完整） | 多 | 2023 年 AI 孙燕姿原生方案，原仓库已删库，用社区 fork `voicepaw/so-vits-svc-fork` |
| **DDSP-SVC** | 极快 | 中（配合 shallow diffusion 接近 RVC） | 好（CPU 也能跑） | 少 | 轻量级，适合 Mac 实测 |
| **Diff-SVC / Diffusion-SVC** | 极慢 | 极高 | 差 | 少 | 学术质量上限高，工程不友好，本项目暂不优先 |

**默认主线推荐**：Phase 1 用 `RVC-WebUI-MacOS` fork 跑通；Phase 2 同时测 RVC + DDSP-SVC + so-vits-svc-fork；Phase 3 用 Phase 2 选定方案。

### 2.4 人声分离工具对比

| 工具 | 算法 | 速度 | 分离质量 | Mac 支持 |
|---|---|---|---|---|
| **UVR5 (Ultimate Vocal Remover)** | 多模型集成（MDX-Net / VR Arch / Demucs） | 中 | 顶级 | 原生支持，有 GUI |
| **Demucs** (facebookresearch) | Hybrid Transformer | 快 | 高 | 命令行友好，pip 安装 |
| **Spleeter** (Deezer) | U-Net | 极快 | 中（已被超越） | 老牌但落后 |

**推荐**：批量处理用 `demucs` 命令行（脚本化友好）；精修关键素材用 UVR5 GUI（可挑模型）。

---

## 3. 项目结构

```
.
├── AGENTS.md                    # 本文件
├── README.md                    # 面向人类的项目说明
├── .gitignore                   # 必须排除：audio/、models/、venv/、*.pth、*.wav、*.m4a
│
├── raw/                         # 原始素材（yt-dlp 下载）
│   └── zhang-shaohan/          # 张韶涵相关
│       ├── playlist.txt        # 待下载链接列表（含来源说明）
│       └── *.m4a               # 实际音频文件（不入 git）
│
├── dataset/                     # 处理后的训练集
│   └── zhang-shaohan/
│       ├── vocals/             # 分离后的干声
│       ├── slices/             # 切片后的训练片段
│       └── manifest.json       # 数据集元信息（来源、时长、质量评分）
│
├── models/                      # 模型权重
│   ├── pretrained/             # 框架预训练底模（hubert、rmvpe 等）
│   ├── community/              # 社区分享的目标音色模型
│   └── trained/                # 本项目训练产出
│       └── {framework}/{run_id}/
│
├── frameworks/                  # 各 SVC 框架的本地 clone（git submodule 或独立 clone）
│   ├── rvc-webui-macos/
│   ├── so-vits-svc-fork/
│   └── ddsp-svc/
│
├── scripts/                     # 自动化脚本
│   ├── 01_download.sh          # yt-dlp 批量下载
│   ├── 02_separate.py          # demucs 批量分离
│   ├── 03_slice.py             # 音频切片 + 静音检测
│   ├── 04_quality_check.py     # SNR / 时长 / 采样率检查
│   ├── 05_train_{framework}.sh # 各框架训练入口
│   ├── 06_infer_{framework}.sh # 各框架推理入口
│   └── 07_mix.py               # 与伴奏对齐混音
│
├── outputs/                     # 最终产物
│   ├── phase1_demo/
│   ├── phase2_comparison/      # 同一曲目用不同框架转出，文件名带框架标记
│   └── phase3_final/
│
├── docs/                        # 调研笔记
│   ├── phase1-walkthrough.md
│   ├── phase2-comparison-report.md
│   ├── phase3-postmortem.md
│   ├── framework-notes/        # 每个框架的踩坑记录
│   └── decisions/              # ADR (Architecture Decision Records)
│
└── notebooks/                   # 探索性 Jupyter（可选）
    └── spectrum-analysis.ipynb
```

**禁止入 git 的内容**（务必在 `.gitignore` 配置）：
- 所有 `.wav .m4a .mp3 .flac .webm` 音频
- 所有 `.pth .ckpt .safetensors` 权重文件
- `frameworks/` 下的子项目（用 submodule 或单独 clone）
- `venv/ __pycache__/ .DS_Store`

---

## 4. 工作约定

### 4.1 环境管理

- **每个框架一个独立 venv**：SVC 框架之间 PyTorch、numpy、librosa 版本冲突严重，不要试图统一。
  ```bash
  cd frameworks/rvc-webui-macos && python3.10 -m venv .venv && source .venv/bin/activate
  ```
- Python 版本统一用 **3.10**（多数框架的甜蜜点；3.11+ 部分依赖装不上）。
- 用 `uv` 或 `pip-tools` 锁版本，每个 venv 都要有 `requirements.lock.txt`。

### 4.2 yt-dlp 素材采集

用户已有可用的 cookies 文件，路径通常在 `~/.config/yt-dlp/cookies.txt` 或项目本地。**不要把 cookies 文件入 git**，在 `.gitignore` 显式排除。

标准下载命令模板（写进 `scripts/01_download.sh`）：
```bash
yt-dlp \
  --cookies "$COOKIES_FILE" \
  -f "bestaudio[ext=m4a]/bestaudio" \
  --extract-audio --audio-format wav --audio-quality 0 \
  -o "raw/zhang-shaohan/%(title)s.%(ext)s" \
  --download-archive raw/zhang-shaohan/.downloaded.txt \
  --batch-file raw/zhang-shaohan/playlist.txt
```

- `--download-archive` 保证断点续传、不重复下载
- 失败重试由 yt-dlp 自带，不需要外层 wrap
- B 站链接用 `BV` 号，YouTube 用完整 URL
- **素材伦理边界**：仅用于个人技术调研，不发布、不商用；调研结束后产物自行决定是否保留

### 4.3 数据集质量标准

训练张韶涵音色模型，建议素材目标：
- **总时长 30-60 分钟**干声（多 ≠ 好，质量优先）
- **采样率 ≥ 44.1kHz**（低于则丢弃，不要重采样填充）
- **片段长度 5-15 秒**（过短信息量不足，过长不利于训练 batch）
- **无伴奏残留、无和声、无混响**（关键，宁缺毋滥）
- 覆盖**多个音域**（低音、中音、高音都要有；张韶涵高音质量是其音色特征核心）

筛选时跑 `scripts/04_quality_check.py` 输出报告，**人工抽听 10% 样本**确认。

### 4.4 实验记录规范

每次训练运行必须在 `models/trained/{framework}/{run_id}/` 下产生：
- `config.json` —— 完整训练配置（含数据集快照 hash）
- `train.log` —— 训练日志
- `eval/` —— 同一首测试曲在不同 epoch 的推理结果
- `notes.md` —— 这次 run 的假设、变量、观察

`run_id` 命名：`{date}_{framework}_{key-hyperparam}`，例如 `20260528_rvc_v2_e300`。

### 4.5 评测协议

Phase 2 横向对比必须用**同一组测试曲**，建议挑：
- 一首张韶涵自己的歌（验证自相似性，应该最像）
- 一首女声中音区流行歌（如《起风了》原版周深的女声 cover）
- 一首男声歌（验证跨性别转换；这是 SVC 的硬考题）
- 一首高音爆发歌（验证高音区不破音）

主观评测用 **MOS 5 分制**（自然度、相似度分开打），客观指标看：
- F0 RMSE（音准误差，用 `pyworld` 或 `crepe` 提取对比）
- 频谱图肉眼对比（`librosa.display.specshow`）

### 4.6 与 Codex 协作的约定

- **回答前先看 `docs/decisions/` 下的 ADR**，已经决策过的事不要重复讨论
- **改动超过 5 个文件或涉及框架间接口时**，先在 `docs/decisions/` 写 ADR 草稿等用户确认再动手
- **不要自动 commit**；阶段性产物由用户手动 review 后入库
- **不要默认下载大模型权重**（>1GB 的）；先告知用户大小、来源、用途，得到确认再下
- **HuggingFace / GitHub Release 下载**时打印 SHA256，落盘后校验
- 如果某个框架在 MPS 上反复报错且查找超过 30 分钟无果，**立刻切方案**而不是继续死磕；把卡点记录到 `docs/framework-notes/{framework}-pitfalls.md`
- 涉及 Python 依赖冲突时，倾向于**新建 venv** 而不是降级现有环境

### 4.7 用户偏好

- 中文沟通；技术术语保留英文
- 不要过度使用 bullet point，正常段落更易读
- 长报告（Phase 总结、ADR）用 Markdown；短答案用纯文本
- 用户讨厌"营销式"框架介绍，只关心**真实能力边界和失败模式**

---

## 5. Phase 1 启动检查清单

Codex 第一次接管时，**先执行**：

1. 跑 `sw_vers && system_profiler SPHardwareDataType | grep -E "Chip|Memory"`，确认硬件实际型号和统一内存大小
2. 检查 `which python3.10 ffmpeg yt-dlp git` 是否齐全；缺哪个用 `brew install` 装哪个
3. 确认 `~/.config/yt-dlp/cookies.txt` 或项目本地 cookies 是否存在、可用
4. 执行 `python3.10 -c "import torch; print(torch.backends.mps.is_available(), torch.backends.mps.is_built())"` 验证 MPS
5. 创建项目结构骨架（按第 3 节）
6. 初始化 `raw/zhang-shaohan/playlist.txt`，先给用户列 5-10 个候选张韶涵 live/录音棚音频源，**让用户确认**再下载

把检查结果汇总到一个 message 里给用户，**等用户确认后**再启动 Phase 1。

---

## 6. 法律与伦理边界

本项目仅用于：
- 个人技术调研、学习 SVC 工程化流程
- 内部技术分享、不商用

**禁止**：
- 公开发布以张韶涵名义的 AI 翻唱作品（B 站、抖音、YouTube 均不可）
- 将训练得到的音色模型公开分享、出售
- 用于任何商业产品或为商业内容生成配音

引用相关法律风险：2023 年孙燕姿本人公开回应过 AI 翻唱事件；环球音乐集团对 AI 翻唱发过版权声明；国内 2024 年起多起 AI 配音侵权案判决支持原告。**Codex 在任何阶段不应主动建议用户发布作品**；若用户询问发布渠道，需提醒上述风险。
