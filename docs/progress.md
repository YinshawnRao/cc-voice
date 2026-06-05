# cc-voice 项目进度档案

> 持续维护的项目流水账。背景、关键决策、踩坑、当前状态、后续计划。新人/未来的自己想搞清楚"我们是怎么走到今天的"，从这里开始。

最后更新：2026-06-03 10:40

---

## 1. 项目背景

复刻 B 站爆款"AI 张韶涵翻唱 XXX"那一套工程链路。三阶段（CLAUDE.md §1.1）：

| Phase | 目标 | 验收 |
|---|---|---|
| 1 · 技术摸底 | 跑通"素材→分离→训练→推理→混音"全链路 | 一首可听 demo，不追求像 |
| 2 · 方案选型 | RVC / So-VITS-SVC / DDSP-SVC 横向对比 | docs/phase2-comparison-report.md 决策矩阵 |
| 3 · 效果对标 | 复刻 B 站爆款水平 | outputs/phase3_final/ + postmortem |

硬件：MacBook Pro M4 Pro / 48GB UMA / macOS 15.6。

法律边界：仅个人调研，**禁止公开发布、商用**（CLAUDE.md §6）。

---

## 2. 关键决策

### ADR-001 · Phase 1 改自训路线
日期：2026-05-28
原计划用社区现成张韶涵 RVC 模型推理 demo。实际操作中：
- HuggingFace 搜不到（推测版权清理）
- voice-models.com / weights.gg / AI Hub 都靠 JS / Discord，WebFetch 拿不到
- 用户不愿手动浏览这些聚合页

→ **Phase 1 直接自训**（CLAUDE.md §1.1 原本 Phase 2/3 才训），把 Phase 1+2+3 主线压成单条"自训 + 不断扩数据 / 调超参"的路径。Phase 2 横向框架对比降级为"effective fallback"。完整记录见 `docs/decisions/ADR-001-self-train-instead-of-community-model.md`。

---

## 3. 时间线 · 实际经过

### 2026-05-28

**11:00 启动**：环境探测
- macOS 15.6 / M4 Pro 48GB 确认
- ffmpeg / yt-dlp / git / brew 已装；**python3.10 缺失** → `brew install python@3.10` 装 3.10.20
- cookies：YouTube 在 `~/explorer/cc-media/sandbox/www.youtube.com_cookies.txt`，复制过来

**11:10-11:13 仓库骨架**
- `raw/ dataset/ models/ frameworks/ scripts/ outputs/ docs/ notebooks/` 全部 mkdir
- `.gitignore` 屏蔽 `*.wav *.pth cookies* venv/` 等
- B 站 cookies 后来用户补到 `cookies.bilibili.txt`，合并 YouTube + B 站到 `cookies.txt`

**11:13-11:16 RVC 框架**
- `qingbo1011/RVC-WebUI-MacOS` clone 到 `frameworks/rvc-webui-macos/`
- venv 用 python3.10 建好
- **踩坑 1**：pip 24+ 拒绝 `omegaconf 2.0.5/2.0.6`（非法 metadata）→ 降级 `pip<24.1`，重装通过。MPS 验证 `MPS available: True`

**11:13-11:19 底模 + 素材并行下载**
- `tools/dlmodels.sh` 缺 `aria2c` → `brew install aria2`，重跑成功。1.78GB 底模到位：hubert_base.pt + rmvpe.pt + pretrained v1/v2 + uvr5_weights
- yt-dlp 6 首张韶涵真嗓 (~131MB) + 1 首邓紫棋《光年之外》(~20MB)：
  - 隐形的翅膀 (录音棚 4K)
  - 欧若拉 (4K MV)
  - 阿刁 (我是歌手 2018 4K)
  - 淋雨一直走 (官方 HD MV)
  - 梦里花 (我是歌手 MV)
  - 看得最远的地方 (4K 修复 MV)
  - 邓紫棋 光年之外 (官方 MV，源歌)

**11:17-11:30 分离 + 切片 + 质检**
- Demucs htdemucs MPS 跑分离
- **踩坑 2**：`Demucs 4.0.1 + torchaudio 2.11.0` 报 `TorchCodec is required`。`pip install torchcodec` 修
- 7 个文件全 MPS 分离成功（源歌额外保留 instrumental）
- `scripts/03_slice.py`：librosa silence detection 切 5-15s/段 → 97 chunks, 19.21 min 干净干声
- `scripts/04_quality_check.py`：97/97 OK，manifest.json 落盘

**11:33-11:35 ASCII rename**
- **踩坑 3 预防**：RVC README 警告中文路径让 ffmpeg 挂掉
- `scripts/00_rename.py` 把所有 wav 文件名重映射成 `adiao_NNN.wav` / `ouluola_NNN.wav` / `guangnianzhiwai.wav` 等 ASCII slug，记录到 `dataset/filename_map.json`

**14:20 WebUI 第一次启动**
- **踩坑 4**：`gradio==3.34.0` 锁定但 `gradio_client==2.5.0` 是新版 API → 降级 `gradio_client==0.2.10`，UI 起来了

**14:20-14:36 特征提取**
- **踩坑 5**：fairseq + PyTorch 2.6+ 默认 `weights_only=True` 在 hubert 加载时炸。fork 修了推理路径但漏修训练路径 → `extract_feature_print.py` 顶部 monkey-patch `torch.load`
- **踩坑 6**：RVC fork 的 `infer-web.py` F0 分支逻辑：`gpus_rmvpe == "-"` 跑到 Windows DirectML 路径（必炸）。需要用户填 `0` 而不是 `-`，同时 patch `extract_f0_rmvpe.py:53` 让 device 根据 `torch.backends.mps.is_available()` 选

**14:39-02:49 训练 (12h10min)**
- 100 epochs RVC v2 on MPS
- **踩坑 7**：`matplotlib 3.10` 删了 `FigureCanvasAgg.tostring_rgb()`，TensorBoard spectrogram 写挂 → `infer/lib/train/utils.py` 改用 `buffer_rgba()`
- 速度：~7.5 min/epoch，远比预期 (~1 min/epoch) 慢。M4 Pro MPS 在 RVC v2 上不少 ops fallback CPU
- Loss 进度健康：
  - epoch 1   : loss_mel=39.0  loss_kl=9.00
  - epoch 50  : loss_mel=24.8  loss_kl=1.22
  - epoch 100 : loss_mel=22.1  loss_kl=0.75
- 产出：`assets/weights/zhang-shaohan-phase1.pth` (53MB) + e50/e100 快照

### 2026-05-29

**10:39 用户拒绝继续用 WebUI（"体验太差刷新就重置"）**
- 改 CLI 路线：写 `scripts/05_train_index.py` + `scripts/06_infer.sh` + `scripts/06_infer.py`

**10:40-10:50 推理踩坑五连**
- **踩坑 8**：infer_cli.py 在 `Config()` 初始化时 MPS 检测让 hubert.to('mps') segfault → 加 `NO_MPS=1` env，monkey-patch `torch.backends.mps.is_available = lambda: False`
- **踩坑 9**：还是 segfault — Config 内部 argparse 吃 sys.argv → `sys.argv = sys.argv[:1]` 清空后再 init Config
- **踩坑 10**：env vars 缺失 (`weight_root` 等) → .env 文件 `KEY = value` 带空格 dotenv 不解析。手动 `os.environ.setdefault(...)`
- **踩坑 11**：相对路径在 chdir 后失效 → 解析成绝对路径再 chdir
- **踩坑 12**：传 faiss `.index` 路径让推理 segfault（仅当 file_index 非空时）→ Phase 1 直接不用 retrieval，留 Phase 2 修

**10:53 Phase 1 demo 落盘**
- `outputs/phase1_demo/converted_vocals.wav` (18MB, 3:55, RVC 推理输出)
- `outputs/phase1_demo/final.wav` (20MB, +伴奏混音)

### 2026-05-30 ~ 06-02 · Phase 2 进行中（本档有缺口，下列为 06-03 从磁盘产物回填）

> 这段 Phase 2 工作当时没即时记进本档。以下关键事实是 2026-06-03 从磁盘产物 / 脚本 / tfevents 反推确认的，**细节（具体超参演进、MOS 打分）待补**。

- 数据扩量到 ~12 首（`logs/zhang-shaohan-phase2/filelist.txt` 852 条切片），重训 Phase 2 RVC v2，配置见 `scripts/phase2_run.sh`：`total_epoch=200 / save_every=50 / batch=4 / v2 / 40k`
- 快照落盘节奏：e50（6/1 16:13）→ e100（6/2 03:18）→ e150（6/2 14:25），约 **11h / 50 epoch**
- 横向对比产物：`outputs/comparison_e50_vs_p1/`、`outputs/comparison_e100/`（光年之外 + 雨爱两首；文件名 `p050`/`p066` = 推理 protect 值）
- 雨爱（杨丞琳《海派甜心》主题曲）belting 段（44–47s）撕裂诊断：`scripts/13_analyze_segment.py` + `outputs/comparison_e50_vs_p1/diagnose_yuai_44_47.png`（slug `yuai` = 雨 yǔ + 爱 ài，非"友爱"）

### 2026-06-02 · 训练中机器崩溃

- Phase 2 训练跑到 **~epoch 191**（tfevents 末步 40200 / 210 steps·epoch⁻¹；末次写入 23:35）时机器崩溃关机
- **后果**：`save_every=50`，最近一次落盘 checkpoint 停在 **e150**；151→191 的 ~40 epoch 权重全丢，只剩 tensorboard loss 曲线（曲线无发散，`g/total` ~40–48 抖动属正常噪声）

### 2026-06-03 · 诊断 + 从 e150 续训

- 确认进程已停（`ps` 无残留）；读 `G_2333333.pth`/`D_2333333.pth` 内部 `iteration=150` 锁定续训点
- **用户决策**：续训到真 e200 再重推光年之外 + 雨爱（而非用 e150 直接推）
- 写 `scripts/14_resume_train_e200.sh`：只 resume train（**不重跑数据 / 特征提取**，避免 `phase2_run.sh` 的 stage 1–9 重洗数据集）；`save_every 50→10` 抗崩；启动前校验 checkpoint 存在，否则中止以防从底模从头训
- 10:35 `nohup` detached 启动，日志确认 `Loaded checkpoint ... (epoch 150)` 正确续上、MPS 训练中，ETA ~11h
- **待 e200**：推理两首（protect=0.66 / rmvpe / 无 index 对齐 e100）→ `outputs/phase2_e200/`，做 e100↔e200 A/B（`scripts/15_infer_compare_e100_e200.sh`，e100/e200 同参数重推消除变量）

### 2026-06-04 · e200 调参 + 数据诊断 + 启动 Phase 3 微调

- e100↔e200 A/B + 新增天空没有极限/理想情人 → e200 修好了 e100 的硬段，但音色"不够像、割裂感"仍在
- **参数诊断**（`scripts/17`）：2×2 protect×index + 建 phase2 faiss 索引 → 定**激进档 = protect0.20 + index0.90**（最像）
- **数据诊断**：量训练集 F0（`docs/trainset_f0_distribution.png`）→ 中位 F4、<C4 仅 15.5%、<G3 仅 68s，**低音区结构性缺口 = 割裂感根因**；另发现**音色年代混淆**（早期 4K 修复曲 + 近期混训）
- **选曲是关键杠杆**：光年之外（邓紫棋强势招牌嗓）怎么调都洗不掉 → 判定不适合；突然想起你/心墙（中音柔嗓）明显更像。SVC 只换音色不换唱法，源歌手辨识度越强残留越多
- 用户定**克隆目标 = 当前音色（2018+）**；F0 体检 11 首候选（`scripts/18`），挑出近期中低音料：无度 / 全世界失眠 / 因我而起 / 引路的风筝 / 还
- 启动 **Phase 3 微调**（`scripts/22`）：从 e200 warm-start、**只加不删** 5 首近期中低音歌（切片 186→261）、`save_every=10` / `-te 50`

### 2026-06-05 · Phase 3 成功 → 新基准 + git 初始化

- Phase 3 微调 50 epoch 完成（07:27，~18min/epoch）：`zhang-shaohan-phase3.pth` + 专属索引 `added_IVF4034..._phase3_v2.index`
- **A/B（`scripts/26`）：phase3 对突然想起你/心墙明显提升 → 定为新默认基准**（e200/e100 保留备查）。**印证低音/年代数据缺口是真问题、且"只加不删"微调能补**
- 批量新歌试听（`scripts/23`）：彩虹/哭不出来/一个人生活/倒数/爱情讯息/慢冷 6 女声 + 搁浅/心中的日月 2 男声
- **男声跨性别经验**：+12 升八度过高 → 撕裂（冲破 ~C#5/547Hz 封顶）；按实测 F0 改 搁浅 +3 / 心中的日月 +5（`scripts/24`）。**咬字** 靠 protect——激进 0.20 抹糊辅音，改 protect0.5 + index0.5"咬字优先"（`scripts/25`）；含糊唱腔源（周杰伦）有硬下限
- **git 初始化**（commit `e72fc69`）：只管代码/文档/配置；`patches/rvc-macos.patch` 存 RVC 6 处补丁；`SETUP.md` 新机器引导；重资产走 git 外同步

---

## 4. 当前状态

✅ **Phase 1 完成**。

✅ **自训模型迭代到 phase3，定为当前默认基准**（2026-06-05）：`zhang-shaohan-phase3.pth` = 从 e200 warm-start + 只加 5 首近期中低音歌微调，A/B 明显更像。推理用**激进档**（protect 0.20 + index 0.90 + rmvpe）+ phase3 专属索引；男声跨性别另按实测升调 + 咬字优先档（protect 0.5/index 0.5）。e200 / e100 / phase1 全部保留备查。

⏸️ 横向框架对比（So-VITS / DDSP）仍未启动（按 ADR-001，主线压成自训迭代）。

工程产出（项目根）：
- `CLAUDE.md` — 项目说明 / 工作约定 / Phase 路线图
- `README.md` — 简短入口
- `docs/`
  - `progress.md` — 本文件
  - `phase1-walkthrough.md` — Phase 1 操作手册 + 踩坑清单
  - `decisions/ADR-001-*.md` — 自训路线决策
- `scripts/00-07_*.{sh,py}` — 完整流水线
- `dataset/zhang-shaohan/{vocals,slices}` — 训练数据集
- `dataset/source-songs/{vocals,no_vocals}` — 源歌干声 + 伴奏
- `frameworks/rvc-webui-macos/` — RVC fork（多处 patched，见 §3 踩坑）
- `frameworks/rvc-webui-macos/assets/weights/zhang-shaohan-phase1*.pth` — 训练产物

---

## 5. 后续计划

### Phase 1 验收（用户侧）
听 `outputs/phase1_demo/final.wav`，判定：
- ✅ 不爆音、不卡顿、时间轴对齐 → **Phase 1 通过**
- ❌ 哪一项不达标 → 进入 framework-notes 记录，调 Phase 2

### Phase 2 · 数据扩量 + 横向比较（如启动）
**必做**
- 扩训练集：~40 min 干声 → playlist.txt Section A 追加 (喵喵、香水百合、潘多拉…等)
- 重训 RVC v2：300-500 epochs (估算 ~24-50h)
- 修 faiss `.index` 推理 segfault，启用 retrieval (`index_rate=0.75`) 测音色相似度提升
- MOS 主观打分 4 首测试曲（自相似、女声中音、男声跨性别、高音爆发）

**可选**（如 RVC 效果不理想）
- 装 `voicepaw/so-vits-svc-fork` 同样跑训练 + 推理
- 装 `DDSP-SVC` 测轻量级方案
- 写 `docs/phase2-comparison-report.md` 决策矩阵

### Phase 3 · 成品（如启动）
- Phase 2 选定方案 + UVR5 精修分离（替代 Demucs）
- DAW 级混音（替代 `07_mix.py`），加 EQ / 压缩 / 混响
- 跟 B 站百万播放视频对标
- `docs/phase3-postmortem.md` 复盘

### 工程改进 todo
- [ ] 修 `06_infer.py` 的 faiss index 段错误（推理时启用 retrieval）
- [ ] 把 patched RVC fork 的修改提炼成 `.patch` 文件，方便重新 clone 时 reapply
- [ ] `scripts/07_mix.py` 加时间轴对齐（当前是简单 padding，可以用 cross-correlation 找最佳 offset）
- [ ] 训练时间太长 — 评估 batch_size=8 / 12 是否能稳定 + 加速
- [x] 长训练 `save_every` 设小（10 而非 50）— 6/2 崩溃丢 ~40 epoch 的教训，`14_resume_train_e200.sh` 已改
- [ ] WebUI 用户体验差，考虑 fork 一个本地 stateless 配置版本（Phase 2 见仁见智）

---

## 6. 踩坑速查表

| # | 现象 | 文件 | 解 |
|---|---|---|---|
| 1 | `omegaconf` 非法 metadata pip 拒装 | requirements.txt | 降 `pip<24.1` |
| 2 | `TorchCodec is required` | demucs 输出 | `pip install torchcodec` |
| 3 | 中文路径 ffmpeg 挂 | scripts/00_rename.py 预防 | ASCII slug |
| 4 | `gradio_client.media_data` ImportError | gradio 版本 | `gradio_client==0.2.10` |
| 5 | fairseq + `weights_only=True` 炸 | extract_feature_print.py / train.py / vc/utils.py | monkey-patch `torch.load` |
| 6 | DirectML 路径 | gpus_rmvpe="-" + extract_f0_rmvpe.py:53 | 填 `0`，patch device 检测 |
| 7 | `tostring_rgb()` AttributeError | infer/lib/train/utils.py | 用 `buffer_rgba()` |
| 8 | hubert.to(mps) segfault | tools/infer_cli.py | `NO_MPS=1` + monkey-patch `is_available` |
| 9 | argparse 冲突 | scripts/06_infer.py | `sys.argv = sys.argv[:1]` |
| 10 | env vars 没 load | .env spaces around `=` | `os.environ.setdefault` 兜底 |
| 11 | 相对路径在 chdir 后失效 | scripts/06_infer.py | 先 `.resolve()` |
| 12 | faiss index → 推理 segfault | vc_single 传 file_index | Phase 1 暂时空 index，留 Phase 2 |
| 13 | 机器崩溃丢 ~40 epoch | train `save_every=50` 太大 | 长训练设 `save_every=10`，崩溃最多丢 10 epoch；RVC 同名实验自动从 `G_2333333.pth` 续训 |
