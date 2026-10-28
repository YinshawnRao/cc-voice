# Hajimi Production Standard V1 / V1.1

**状态**：Audio V1.1 已完成三首机器回归；《百年孤寂》《突然想起你》音频获用户偏好确认，两支新版 MV 已完成 video-stream reuse 与机器 QA，等待用户观看  
**版本**：Audio `hajimi-note-event-v1`（冻结回归）/ `hajimi-note-event-v1.1`（去气泡候选）+ Video `hajimi-rain-window-v1`  
**基准作品**：《绿光》用于音频方向对标；《雨爱》用于音频与 9:16 MV 联合验收；《百年孤寂》《突然想起你》用于共享 CLI 泛化回归  
**适用范围**：严格限于 20–75 秒、一个完整乐段、内部验证/体验

这份文档是 Hajimi 音频和 MV 的唯一跨环节规范。算法细节仍见
[`NOTE_EVENT_PLAYBOOK.md`](NOTE_EVENT_PLAYBOOK.md)，单曲实测见
[`yuai_event_v1/README.md`](yuai_event_v1/README.md)。Performance V4 固定短句循环只保留为 audition / regression baseline，不再代表成品主线。

## 1. V1 能力边界

V1 已验证：

- 同轴 guide vocal / instrumental 上的逐 note、逐 syllable、逐 phrase 编排；
- 中文 attack + 日语 Hajimi sustain 的 hybrid 声源；
- 真实休止、tie / melisma、分段人声自动化、stereo doubles、room、ducking 和安全母带；
- renderer 时间轴直接驱动逐字字幕；
- 1080×1920 竖屏、单路同曲 MV 窗口、四段色彩状态、post-mux master；
- 单任务串行、线程限额、完整媒体 QA；
- 单份 per-song recipe 驱动共享 Audio / MV CLI，不复制《雨爱》renderer，不在共享源码中写死标题、duration、groups、sections、BPM 或 MV 路径；
- 《百年孤寂》54.70 秒与《突然想起你》37.24 秒两个真实项目从 recipe 到 post-mux、机器 QA 的泛化回归；二者分别覆盖 `0.000s` 和 `+0.390s` 的 MV 对音频 offset。
- V1.1 phase-locked microcycle 在三首歌上保持事件时间轴不变，三首合并气泡代理中位值相对 V1 降低 `71.4%`；V1 显式路径仍可逐字节复现旧 dry SHA。

V1 尚未验证：

- 超过 75 秒或整首歌的自动分段拼接；
- 无人工校正的全自动 event manifest；
- 多 pitch-center voicebank 或 SVC post-stage；
- 多首歌并行或无人值守批处理；
- 《百年孤寂》《突然想起你》的人工试听、人工观看与 package 批准；
- 《雨爱》V1.1 完整成品的单独人工试听、两支新版 MV 的人工观看与默认版本切换；
- 跨 macOS / Linux 的中文字体像素级一致性。

共享单曲 CLI 已不再是待实现计划，但 V1 仍不能描述成“整首歌、无人值守、全自动批量生产线”。20–75 秒之外必须升级版本或拆成经单独批准的乐段，不能静默放宽窗口。

## 2. 统一状态机

每首作品必须按下列状态推进，禁止跨级：

```text
SOURCE_READY
→ WINDOW_APPROVED
→ AUTO_DRAFTED
→ EVENT_APPROVED
→ AUDIO_RENDERED
→ AUDIO_AUTO_QA_PASS
→ AUDIO_HUMAN_APPROVED
→ MV_INPUT_VALIDATED
→ MV_BUILT
→ MV_PREVIEW_APPROVED
→ MV_RENDERED
→ MASTER_MUXED
→ FINAL_QA_PASS
→ PACKAGED
```

`AUTO_DRAFT_REQUIRES_HUMAN_LISTENING` 只能用于 preview，不能冒充 `EVENT_APPROVED` 或 `AUDIO_HUMAN_APPROVED`。内部预览允许以显式 `--allow-auto-draft-preview` 从 `AUTO_DRAFTED` 进入 render / QA，但机器状态只能到 `AUDIO_AUTO_QA_PASS`；自动 QA 的 `preview_only` 必须为 `true`、`human_approval` 必须为 `PENDING`，`manual_listening` 必须继续写 `NOT_PERFORMED_BY_AUTOMATION`。

同理，MV 机器 QA 通过后的状态是 `AUTO_QA_PASS_HUMAN_REVIEW_PENDING`。它表示 strict decode、媒体合同、字幕覆盖、响度、黑场/静音、音画相关和代表帧抽取均已完成自动检查，不表示用户已经试听或观看。内部 preview lane 可以先生成 raw、post-mux MP4 和 QA 报告供审阅，但不能据此推进生产状态。只有独立保存人工结论后，才能推进 `AUDIO_HUMAN_APPROVED`、`MV_PREVIEW_APPROVED`、`FINAL_QA_PASS` 或 `PACKAGED`；不得回写或篡改机器报告。

### 2.1 Canonical CLI

从仓库根目录执行。Audio CLI 固定使用项目 Python 3.10 venv；`RECIPE` 必须是版本化 per-song recipe，`RUN_ID` 必须显式提供且与 preset tag 一致：

```bash
PYTHON=frameworks/rvc-webui-macos/.venv/bin/python
RECIPE=hajimi-remix/recipes/<song_id>.json       # V1
RUN_ID=YYYYMMDD_<song_id>_hev1_rNN
# V1.1 使用 recipes/<song_id>-v1p1.json 与 YYYYMMDD_<song_id>_hev1p1_rNN

$PYTHON hajimi-remix/scripts/hajimi_audio.py validate "$RECIPE" --run-id "$RUN_ID"
$PYTHON hajimi-remix/scripts/hajimi_audio.py draft "$RECIPE" --run-id "$RUN_ID"
$PYTHON hajimi-remix/scripts/hajimi_audio.py render "$RECIPE" --run-id "$RUN_ID" --allow-auto-draft-preview
$PYTHON hajimi-remix/scripts/hajimi_audio.py qa "$RECIPE" --run-id "$RUN_ID"

$PYTHON hajimi-remix/mv/template-v1/scripts/hajimi_mv_runtime.py build "$RECIPE" --run-id "$RUN_ID"
$PYTHON hajimi-remix/mv/template-v1/scripts/hajimi_mv_runtime.py check "$RECIPE" --run-id "$RUN_ID"
$PYTHON hajimi-remix/mv/template-v1/scripts/hajimi_mv_runtime.py snapshot "$RECIPE" --run-id "$RUN_ID"
# 用户批准 Studio preview 后才允许 render
$PYTHON hajimi-remix/mv/template-v1/scripts/hajimi_mv_runtime.py render "$RECIPE" --run-id "$RUN_ID" --quality high
$PYTHON hajimi-remix/mv/template-v1/scripts/hajimi_mv_runtime.py mux "$RECIPE" --run-id "$RUN_ID"
$PYTHON hajimi-remix/mv/template-v1/scripts/hajimi_mv_runtime.py qa "$RECIPE" --run-id "$RUN_ID"
```

这里的 `render --allow-auto-draft-preview` 明确选择内部 preview lane；它不会授予任何人工批准。Audio `package` 会先拒绝非 `AUDIO_HUMAN_APPROVED` 状态，且 V1 preview CLI 有意不实现交付打包，因此不属于上述机器预览序列。MV 的 `build` 会先校验输入；当前 canonical MV 入口是 `hajimi_mv_runtime.py`，不能用复制单曲目录或直接调用内部 `build_frames.py` 替代。

## 3. 单曲输入契约

音频真值：

```text
guide.wav               # mono；只提供 timing / pitch / dynamics，永不进最终 mix
instrumental.wav        # stereo；与 guide 同版本、同一次分离、严格同轴
event-approved.json     # 人工批准后的 note / syllable / phrase 时间轴
song-manifest.json      # 歌曲、窗口、输入 hash、preset 与结构点
```

MV 真值：

```text
song.wav                # AUDIO_HUMAN_APPROVED 的最终 master
timed_lyrics.json       # 只由 approved syllable groups 生成
theme.json              # 主题、布局、标题、颜色与素材 provenance
visual.mp4              # 可选；静音、预裁、与 song.wav 同轴
```

所有路径都必须配 SHA256。`song.wav` 的实测采样率、声道、帧数和时长是下游唯一音频真值；代码中不得用另一份同名拷贝或单曲常量替代。

### 3.1 随机选曲与同版门禁

“随机”只决定合格候选的检查顺序，不能绕过素材门禁。每次选择必须冻结 seed、排序后的初始池、随机顺序、目标数量、拒绝原因和替补结果；本轮记录见 [`20260813-random-mv-selection.json`](recipes/20260813-random-mv-selection.json)。候选只有同时满足以下条件才能进入 recipe：

- 本地存在同一录音版本、严格同轴的 guide vocal 与 instrumental，且二者可解码；
- 本地 MV 可严格解码，并记录路径、来源、媒体规格与 SHA256；
- 用多个窗口的音频互相关证明 MV 与 stems 是同一版本，得到稳定的 `audio_to_video_offset_seconds`；
- 窗口能覆盖一个 20–75 秒完整乐段，并通过安全起止点检查。

任一条件失败就 fail closed，并按已冻结的随机顺序取下一候选。例如本轮《那么骄傲》因 MV 为缩短 live、stems 为 studio，相关峰低且 offset 不一致而被拒绝；随后按同一随机顺序由《突然想起你》替补。不得因为歌名相同、肉眼看似同步或为了凑足数量而跳过同版门禁。

## 4. Audio V1 / V1.1

### 4.1 素材与窗口

- 原始音频采样率不得低于 44.1 kHz；工作和交付 WAV 统一 44.1 kHz。
- guide 裁成 mono，instrumental 保持 stereo；裁剪后的 sample frame 数必须完全相同。
- 禁止 `min(len(guide), len(instrumental))` 静默截短。不同轴或不同长直接 FAIL。
- 先用 FFmpeg 裁到精确窗口，再让 Python 分析；Python 入口拒绝超过 75 秒输入。
- 窗口必须覆盖完整乐句或完整副歌，开头不能切在半个字、未落稳和弦或分离瞬态中。
- `midi_min/max`、pitch floor / ceiling、octave correction 都是逐曲配置；禁止所有男声或低音一律升八度。

### 4.2 Event renderer

冻结回归 preset 为 `hajimi-note-event-v1`：

- Praat autocorrelation，hop `10 ms`；minimum stable note `70 ms`。
- 默认 voicing threshold `0.45`、strength `0.78`、RMS `-38 dBFS`、cents window `55`。
- extractor 只桥接相同 note label 且 `<=40 ms` 的 tracker 短洞。
- 遇到可靠 attack、phrase reset 或 `>=120 ms` gap 时重新放置字头。
- 只有没有低能量休止证据、不是 phrase end 且 gap `<=280 ms` 时，才允许元音延续。
- consonant / attack 是 one-shot；只延长稳定 vowel sustain；句尾才使用 release。
- 同一 syllable 跨多个 note 时使用 tie / melisma，不重复辅音。
- attack、sustain、release 使用短 equal-power crossfade，边界不得产生 click。
- production 默认只输出 hybrid：中文 Yushu attack + 日语 Hajimi sustain；`zh` / `jp` 单声源仅用于 A/B 校准。
- PSOLA / pitch render 异常必须 fail closed，禁止静默回退到未调音 source。

#### 4.2.1 V1.1 去气泡 sustain

`hajimi-note-event-v1.1` 只由 `*-v1p1.json` 与 `hev1p1` run 显式选择，不能改变旧 V1 recipe 的含义：

- 对需要延长的 JP sustain 在 `120–700Hz` 内做 normalized autocorrelation；采用 NCC `>=0.80`、前后半周期差 `<=5%`、源长度 `>=10P` 的周期分析。
- 从源中心取 `8P` microcycle，以 `2P` equal-power overlap 循环；目标短于源时继续中心 crop。周期低置信、长度不足、非有限值或输出帧数不符均 fail closed，禁止回到旧固定 `18ms` OLA。
- candidate dry 用独立 pyworld Harvest + StoneMask 检查；若仅个别 microcycle group 出现 event-median octave error，允许显式 `F0_SAFETY_V1_GROUP_FALLBACK`，必须记录 group ids，比例不得超过 `5%`。超限或回退后仍错误则整首 FAIL。
- 回归 recipe 固定复用并校验 V1 event draft，避免 extractor 版本漂移混入 sustain 单变量实验。新歌首次 candidate 可自动发现 safety groups；进入可重复生产前必须把已验证 group ids 写回版本化 recipe。
- bubble production gate 只统计真正应用 microcycle 的长 sustain；crop 与 F0-safety groups 仍在 all-long-groups advisory 中完整报告，不能用未改动组反向否定算法，也不能隐藏它们。

### 4.3 Mix / master

当前 preset 名称固定为 `hajimi-pop-v1`：

- vocal HPF `90 Hz`、LPF `14.5 kHz`；
- compressor `-20 dBFS / 3:1`，attack `8 ms`，release `90 ms`；
- doubles 约 `±0.045 semitone`，左右 delay `4.7 / 8.3 ms`；
- `280 ms` 短 room；
- vocal 活跃时，instrumental 在 `1.8–5.2 kHz` 最多让位 `1.6 dB`；
- premaster sample peak `<=-3 dBFS`；
- style master `-9.5 LUFS ±0.5 LU`、`<=-1 dBTP`；
- section vocal balance、width、room 和 ducking 只能通过显式 preset / manifest override 修改，并写入 report。

单 pitch-center voicebank 是 V1 已知上限。实际 pitch shift 的 P90 超过 `4 semitones` 或最大值超过 `7 semitones` 时标 WARN；这不是靠 limiter 或激进 EQ 能修复的问题。

### 4.4 Audio 自动门禁

| 检查 | PASS | WARN / FAIL |
| --- | --- | --- |
| schema、窗口、event 顺序 | 100% | 任一不一致 FAIL |
| rendered note coverage | 100% | 少一个即 FAIL |
| F0 median / P90 | `<=10 / 25 cents` | 超限 FAIL |
| octave error | `0` | 任一即 FAIL |
| onset median / P90 | `<=35 / 70 ms` | 超限 FAIL |
| onset within 100 ms | `100%` | `>=98%` WARN；`<98%` FAIL |
| splice discontinuity | `0` 个越过局部 2ms diff P99 | 任一即 FAIL |
| output | 44.1k stereo PCM24、非有限样本 0、严格解码 PASS | 不满足即 FAIL |
| master | `-10～-9 LUFS`、`<=-1 dBTP` | 不满足即重做 master |
| V1.1 bubble median / P90 | 每首相对 V1 `>=35%`，且 `<=0.50 / 0.85` | 任一超限 FAIL |
| V1.1 independent F0 | median/P90 `<=20/50 cents`，event octave `0`，frame octave `<=1%` | 任一超限 FAIL |
| V1.1 spectral drift | centroid `<=10%`，4–8k presence `<=1.5dB` | 超限视为靠变暗掩盖，FAIL |

所有 WARN、octave correction、phrase 首字、`<100 ms` 短音、最高/最低音、最长 sustain、section 边界和最后 release 都必须人工试听。《雨爱》当前 `96/97` onset 命中保留为 WARN，人工重点听 clip `10.270s` 的第 25 个“南”。

### 4.5 Audio 性能预算

- crop-first 是硬规则：先用 FFmpeg 把 guide / instrumental 裁成 20–75 秒窗口，再进入任何 Python 分析；MV 也先裁成 928×642 静音代理，再交给浏览器 / HyperFrames。
- 音频 extract、draft、render、QA 与视频 build、check、snapshot、render、mux、QA 全部串行；跨歌曲同一时间只允许一个重任务。
- `OMP_NUM_THREADS`、`OPENBLAS_NUM_THREADS`、`VECLIB_MAXIMUM_THREADS`、`NUMEXPR_NUM_THREADS`、`MKL_NUM_THREADS` 固定 `2`。
- FFmpeg 固定 `threads=2`；event draft 禁止顺带启动 Demucs、Whisper、torchcrepe。
- 默认只渲染 hybrid 一个 production variant。
- 每阶段记录 wall time、user/system CPU、peak RSS、swap 和输入/输出 hash。

M4 Pro V1 基线门：extract `<=10s / 1GB`，render+mix `<=30s / 2GB`，QA `<=10s / 1GB`。V1.1 最终 renderer 进一步要求 wall `<=1.5×同曲 V1` 且 `<=10s`、RSS `<=1.25×V1` 且 `<1.6GB`；独立 F0 preflight 与必要的 repair pass 必须分阶段串行记录，峰值不得相加，swapout 必须为 0。

## 5. Video V1：Hajimi Rain Window

### 5.1 画布与布局

固定 `1080×1920 / 30fps / SDR / BT.709`，禁止从 16:9 成片中心裁切：

| 区域 | 几何 |
| --- | --- |
| 横向安全区 | `x=72–1008` |
| 顶部视觉区 | `y=0–870` |
| MV 外框 | `x=72 y=160 w=936 h=650`，4px ink border |
| MV 内画面 | `x=76 y=164 w=928 h=642` |
| 歌曲 / 歌词区 | `y=870–1780` |
| 底部进度区 | `y=1780–1920`，高 140px |

MV 底部带为 72px 半透明 ink，并带 3px coral 顶边。一个作品只允许一条连续 video 元素跨越所有 section；不能每段重新创建、seek 或播放视频。

### 5.2 视觉语言

固定色板：Coral `#E85D5D`、Coral Dark `#D44A4A`、Ink `#1A1A1A`、Cream `#F5F0E8`、Cream Dark `#E8E0D4`、Gray `#6B6B6B`。

- 硬边、平面、杂志排版；45° hatch 只用于背景。
- 禁止圆角卡片、投影、柔和渐变、粒子、Three.js、通用频谱柱和全屏闪白。
- 英文标签 / metadata：Inter 700、uppercase、tracking 2–4px。
- 中文标题：宋体展示风格，当前基线 142px。
- 当前歌词：黑体 80px，高亮 coral，`scale≈1.06 / y≈-4px`；相邻歌词 30px。
- 布局不随 section 跳动，只切换色彩状态、章节标签和歌词内容。

章节色彩：Open = coral/cream/ink；Build = cream/ink/coral；Climax = ink/coral/cream；Outro = cream/cream/coral。

中文字体当前只在经过 preflight 的 macOS 上冻结；项目带本地 Inter WOFF2，但宋体 / 黑体仍依赖系统字体。跨机器正式批量前必须补齐可随项目分发的中文字体，或明确拒绝未通过字体 preflight 的机器。

### 5.3 MV 素材代理

- 内容源优先选择同曲的官方艺人 / 唱片公司 MV；视觉源必须记录 URL、标题、频道、源窗口、下载格式和 SHA256。
- 先用音频互相关求源视频相对 song master 的 offset；不能按“歌名相同”猜时间轴。
- 代理在进入 HyperFrames 前完成裁剪、缩放、去音频和兼容编码。
- V1 canonical 代理固定为 928×642、H.264、yuv420p、30fps、无音轨，时长与 song 相差不超过一帧。
- 最大关键帧间隔 `<=1.1s`；建议 `-g 30 -keyint_min 30 -sc_threshold 0`，避免随机 seek 重解长 GOP。
- `<video muted playsinline>`；成片唯一声音始终是 `song.wav`。
- 没有 visual 时按 ADR-002 回退到同尺寸的自有生成雨窗；当前无 visual fixture 尚未落地，不能宣称已机械验证。

### 5.4 字幕时间轴

- 只显示实际听到的 Hajimi token，不用原歌词冒充唱词。
- `timed_lyrics.json` 必须从 audio renderer 的 approved syllable groups 生成，禁止 Whisper 回识别。
- UI `start = attack_start`；若相邻 render 音频有 crossfade，UI `end = min(raw_end, next_start)`，任意时刻最多一个 active token。
- section 边界必须与 phrase reset 共用同一套时间真值，无缝覆盖 `0 → duration`。
- 每个 phrase 内默认每 7 token 一行，不跨 phrase reset；残行允许少于 7 字。
- 新 section 第一帧直接显示歌词和 footer，禁止用重复的整块入场动画遮住 phrase 首字。

### 5.5 HyperFrames 分层

```text
track 1   section shell subcomposition
track 2   one continuous muted visual.mp4
track 3   MV information band
track 11  only song.wav audio
```

video / audio 必须是 host root 的直接子元素。host composition ID、subcomposition ID、inner ID 和 `window.__timelines` key 必须一致。子画面脚本按正式 `data-composition-id` 宿主取作用域，不能依赖渲染编译器可能丢弃的辅助属性。运行时禁止网络请求、ASR、FFT、F0、随机数以及手动 `play()` / `currentTime`。

### 5.6 Build、预览和渲染门禁

构建顺序固定：

```text
validate inputs
→ build timed lyrics
→ run `music-to-video/scripts/analyze-beatgrid.py` once, or reuse its hash-bound cache
→ precompute offline audio bands
→ build section frames
→ assemble host index
→ finalize media tracks
→ lint / validate / inspect
→ boundary snapshots
→ user preview approval
→ one-worker render
→ post-mux current master
→ final QA
```

快照至少覆盖：`0.1s`、每个 section midpoint、每个 section 边界前后各一帧、所有 phrase 首字、最后一个字和结束前 `0.1s`。

资源预算：

- preview server 在正式 render 前停止；
- HyperFrames 固定 `0.6.69`、`workers=1`；V1 不允许提高 worker 数；
- FFmpeg 固定 `threads=2`；V1 不允许提高线程数；
- build / render 接入非阻塞 `resource_budget.py`；即使预算允许，仍受上述一 worker / 两线程硬上限约束；
- 不让浏览器解码完整 MV，只解码预裁代理；
- 不并发渲染横 / 竖版或多首歌曲；
- `render-evidence.json` 必须记录 wall time、peak RSS 与 macOS 单调 swap page counters。新增 `swapout` 视为内存压力并 FAIL；仅有少量 `swapin`、没有 `swapout` 时保留 WARN 和原始页数，因为它可能是系统回收既有 swap，而非本任务新增换出；
- raw render 不是成片。必须在 render 后 post-mux 当前 run 的 master：H.264 video copy + AAC-LC 320k / 44.1k stereo + faststart；禁止把 HyperFrames raw 的音轨或旧 master 当权威音频。

若 V1.1 与冻结 V1 的 sample count、group/section/字幕时间投影及全部视觉依赖签名完全一致，可在用户批准新音频后使用 `reuse-video`：它只复用旧 H.264 packet stream，写入新的 revision 目录和 `video-reuse-evidence.json`，随后仍重新执行 mux 与 Final MV QA。任一签名不同、renderer 消费 audio-reactive 数据或证据 hash 断裂，必须回退完整 HyperFrames render；禁止改写旧 `render-evidence.json` 冒充新渲染。

## 6. Final MV QA

最终必须生成 `final-media-qa.json`，并机械验证：

- strict full decode 无 error；恰好一个 video、一个 audio stream；
- H.264 High、1080×1920、30fps、yuv420p、BT.709 SDR；
- AAC-LC、44.1kHz、stereo；音画首尾差均小于一帧；
- AAC 对 master 互相关 lag 小于一帧，响度差 `<=0.3 LU`；
- 无 `>=0.5s` 全屏黑场、无 `>=1s / -35dB` 意外静音；
- 所有 syllable coverage 100%，任意时刻最多一个高亮；
- section 边界无空窗、掉载、进度倒退或时间码归零；
- 代表帧人工抽查 MV 连续性、标题安全区、第一/最后字；
- 记录 master、visual proxy、raw、final、timed lyrics、theme、index 和 renderer 的 SHA256。

《雨爱》最终回归基线：50.15s、1503 video frames、约 `-9.6 LUFS / 0.7 LRA / -1.3 dBTP`；正式 render 单 worker 峰值 RSS 约 814MiB、0 swap。具体数值以当次 `final-media-qa.json` 为准。

V1.1 三首音频机器回归如下；数值来自 2026-08-20 当前 QA，状态均保留人工待验：

| 项目 | bubble V1 → V1.1 | 降幅 | F0 median / P90 | micro / crop / safety | master |
| --- | ---: | ---: | ---: | ---: | ---: |
| 《雨爱》 | `0.870 → 0.200` | `77.0%` | `4.24 / 11.52 cents` | `73 / 24 / 0` | `-9.73 LUFS / -1.69 dBTP` |
| 《百年孤寂》 | `0.868 → 0.220` | `74.6%` | `3.87 / 11.07 cents` | `71 / 26 / 0` | `-9.91 LUFS / -1.69 dBTP` |
| 《突然想起你》 | `0.855 → 0.329` | `61.5%` | `4.09 / 11.53 cents` | `69 / 24 / 2` | `-9.83 LUFS / -1.69 dBTP` |

三首合并 bubble median `0.867 → 0.248`，降低 `71.4%`；全部 event octave error 为 0，render peak RSS `1.14–1.45GB`、swapout 0。该结论是机器门禁 PASS，不是三首完整人工试听 APPROVED。

2026-08-21 用户试听两份等响 A/B 后确认“B的感觉更好一些”，因此《百年孤寂》《突然想起你》的 audio manifest 推进为 `AUDIO_HUMAN_APPROVED`，只授权进入内部 MV preview；不等于 MV 已获人工观看批准，也不自动批准《雨爱》或把 V1.1 设为默认。

两支 V1.1 MV 实测通过 `VIDEO_STREAM_REUSED_TIMELINE_IDENTICAL`：旧 H.264 packet stream 保持一致，新 master 在独立 revision 中重新 mux，并重跑 Final MV QA。

| 项目 | revision | Final AAC | AAC 对 master | AV 尾差 | continuity / subtitles |
| --- | --- | ---: | ---: | ---: | --- |
| 《百年孤寂》 | `...hev1p1-r02` | `-9.93 LUFS / -1.29 dBTP` | lag 0、corr `0.999964` | `20.998ms` | 黑场/静音 0；`97/97` |
| 《突然想起你》 | `...hev1p1-r01` | `-9.86 LUFS / -1.52 dBTP` | lag 0、corr `0.999947` | `20.340ms` | 黑场/静音 0；`95/95` |

reuse、mux、QA 三阶段 swapout 均为 0；两支状态均为 `AUTO_QA_PASS_HUMAN_REVIEW_PENDING`。

旧 V1 MV 泛化回归如下，继续冻结且不被 V1.1 覆盖：

| 项目 | 窗口 | Audio 机器状态 | MV 机器状态 | Final AAC / 对 master | AV 尾差 / render 资源 |
| --- | ---: | --- | --- | --- | --- |
| 《百年孤寂》 | 54.70s | `AUDIO_AUTO_QA_PASS`、`preview_only=true`、`human_approval=PENDING` | `AUTO_QA_PASS_HUMAN_REVIEW_PENDING` | `-10.01 LUFS / 4.2 LRA / -1.52 dBTP`；lag 0 sample，corr 0.999937 | `0.031ms`；62.36s / 815.25MiB / swapout 0（swapin 32 pages WARN） |
| 《突然想起你》 | 37.24s | `AUDIO_AUTO_QA_PASS`、`preview_only=true`、`human_approval=PENDING` | `AUTO_QA_PASS_HUMAN_REVIEW_PENDING` | `-9.61 LUFS / 1.6 LRA / -1.54 dBTP`；lag 0 sample，corr 0.999944 | `0.689ms`；43.80s / 814.50MiB / swapout 0（swapin 12 pages WARN） |

两首均已通过 strict full decode、单 video / 单 audio stream、字幕 coverage 100%、最大同时高亮 1、黑场 / 意外静音 0、代表帧与 contact sheet 生成。这里的“通过”严格指机器门禁，不补写人工试听或观看结论。

## 7. 命名和产物

```text
run_id = YYYYMMDD_{song_id}_hev1_rNN | YYYYMMDD_{song_id}_hev1p1_rNN

outputs/{song_id}/{run_id}/
  {song_id}_hajimi_master.wav
  {song_id}_hajimi_vocal.wav
  {song_id}_hajimi_ab.wav
  event-approved.json
  timed_lyrics.json
  render-report.json
  audio-qa-report.json
  listening-review.json
  artifact-manifest.json

mv/{song_id}-hajimi-mv-v1/
  data/timed_lyrics.json
  data/theme.json
  assets/bgm.wav
  assets/visual.mp4
  renders/{song_id}-hajimi-mv-v1_raw.mp4
  renders/{song_id}-hajimi-mv-v1.mp4
  qa/final-media-qa.json
```

音频、视频、源素材和生成 QA 默认不入 git；git 只保存 renderer、配置、标准、ADR 和小型 manifest。

## 8. 变更规则

- 时间轴 schema、preset 含义、voicebank contract、MV 输入 contract 或 QA 门槛改变时必须升版本。
- 影响超过 5 个文件或建立共享 audio→MV 接口时，先写 ADR 并等用户确认。
- 参数变化必须落到版本化 preset / manifest，不允许只改隐藏常量后继续叫 V1。
- 旧产物永不覆盖；修订使用新 run id。只有 raw/final 的同一轮失败重渲染可以覆盖同名未交付文件。
- 自动门禁 PASS 不等于人工体验 APPROVED；两类结论必须分别保存。

[`ADR-003`](../docs/decisions/ADR-003-hajimi-production-contract-v1.md) 已采纳并实施：共享 schema、preset、Audio CLI、MV runtime、QA 合同和测试已落地，并通过《百年孤寂》《突然想起你》两首真实项目泛化回归。第二首开始只替换 recipe 与输入素材，不复制 renderer 源码。

[`ADR-004`](../docs/decisions/ADR-004-hajimi-note-event-v1-1-debubble.md) 已采纳并实施：V1.1 双版本接口、三首回归、独立 bubble/F0/spectral/resource 门禁及可审计 video-stream reuse lane 已落地并通过两首真实 MV 验证；当前停在 MV 人工观看门，尚未切换新 recipe 默认值。

当前结论是“20–75 秒单乐段的共享窄入口已完成机器验证”，不是“整首歌、多首并行、无人值守且已经人工批准的一键生产”。超出 V1 能力边界时必须新建 ADR / 版本，不得用脚本能启动代替能力已验证。
