# 《绿光》哈基米版音频对标分析

> 参考作品：[🐱“基米在寻觅，绿豆在哪里”🐱](https://www.bilibili.com/video/BV1L62NBWEAk/)  
> BV：`BV1L62NBWEAk`  
> 分析日期：2026-08-10  
> 范围：只分析音频制作范式，不复刻或公开发布作品。

## 1. 结论

我们的总体链路没有走反：分离 guide vocal、提取旋律与动态、重新生成歌声、再与伴奏混合，确实属于正确的问题空间。

但是，当前 Performance V4 已经优化到了错误的抽象层。参考作品更接近传统 **人力 VOCALOID / 鬼畜调音**：先构建短音节或音素音源，再按歌曲的 note、beat 和乐句逐事件编排、调音、叠层和混音。V4 则把一条 1.681 秒中文 TTS 语句拉成固定 3.1 秒七槽，再无限连续循环并套 guide F0。两者不是同一种制作粒度。

因此：

- 换一首更好唱的歌只能改善音域、guide F0 稳定度、伴奏宽度和 Demucs 残留。
- 它无法消除固定 3.1 秒复读、随机字头入口、低带宽 TTS 音色、碎 gate、单声道干声和主唱埋低。
- 下一步不应继续围绕 slot、threshold、offset 或 limiter 小修；主线应改为 **song-specific note/phoneme event renderer**。
- 当前 F0 跟随已经够用，音高不是第一瓶颈。第一瓶颈是音节落点与乐句编排，第二是 voicebank，第三才是 production/mix。

## 2. 来源与本地证据

### 2.1 下载快照

使用工作区 `yt-dlp 2026.06.09`、项目 cookies 和 `--no-playlist`，只下载所给 BV 的最佳原始音轨，没有拉取合集中的其他作品。

```text
title       🐱“基米在寻觅，绿豆在哪里”🐱
uploader    咆哮丶土拨鼠
upload_date 2025-11-07
duration    95.061 s
views       3,374,629        # 2026-08-10 metadata snapshot
likes       437,623          # 2026-08-10 metadata snapshot
comments    6,258            # 2026-08-10 metadata snapshot
```

本地文件：

```text
hajimi-remix/work/reference/BV1L62NBWEAk/BV1L62NBWEAk.m4a
hajimi-remix/work/reference/BV1L62NBWEAk/BV1L62NBWEAk.info.json
hajimi-remix/work/reference/BV1L62NBWEAk/demucs/vocals.wav
hajimi-remix/work/reference/BV1L62NBWEAk/demucs/no_vocals.wav
```

原始音轨：AAC-LC、48 kHz、stereo、约 205 kbps。

```text
audio sha256      a5491c5f09272ed70dd0219d6af6dabe9a20dc60fa77fcd7d7fc455e7f857d6b
vocal stem sha256 424ddb9b6dd965d83958b87a8eeefaf74b3014640b428c780f5bfb2f132e02aa
music stem sha256 3a4f654ab190bcfb94467a9b85cd563a80b62a2f4d054bdeccc1b4683c141926
```

所有本地音频和 `work/` 都由 `.gitignore` 排除，只用于个人技术调研。

### 2.2 外部制作语境

目标视频的简介没有公开具体软件或插件，因此不能从页面直接确认 exact DAW、voicebank 或模型。

但 UP 主自己的[鬼畜 RAP 制作教程](https://www.bilibili.com/video/BV1334y1L7vy/)把流程明确拆为：素材音频提取、纯人声提取、音源制作、填词与 beat、调音、混音和 PV。B 站的相关哈基米排行榜也把该类作品归在[人力 VOCALOID、鬼畜调教、音 MAD](https://www.bilibili.com/video/BV1VxvoBvEjR/)语境，并给咆哮丶土拨鼠标注“调音”。

这些公开信息与本次音频取证相互印证：目标高概率是 sample-based 人力调音，不是简单的整句 TTS 循环。

仍需保留边界：这不能证明《绿光》具体用了 Melodyne、UTAU、OpenUtau、某个 DAW sampler、RVC 或其他特定软件。

## 3. 分析方法与证据边界

本次使用：

- `ffprobe`：编码、采样率、时长、比特率。
- `ffmpeg ebur128=peak=true`：LUFS、LRA、true peak。
- Demucs 4.0.1 `htdemucs --two-stems vocals`：宏观人声/伴奏分离。
- `librosa`：tempo、beat、onset、自相关、周期折叠、频谱与立体声统计。
- Praat + torchcrepe：F0、半音量化、稳定 note run。
- Whisper-base：只作结构辅助，不作为歌词可懂度门禁；歌唱旋律会改变普通话声调，原始识别文本不可靠。

Demucs stem 不是原始 multitrack，可能包含串音、混响归属错误和 clamp；因此 stem 峰值、极细 EQ 和具体效果器参数不作为事实。宏观节奏、周期、音高事件、相对层级和空间变化可以作为较强证据。

本轮没有完成人耳盲听，主观音色结论仍需用户试听确认。

## 4. 参考作品可以确认什么

### 4.1 它是强网格化、逐事件的歌声

参考节奏约 `106 BPM`；librosa 粗估为 `107.67 BPM`，逐 onset 网格拟合得到十六分音符约 `0.1415 s`，对应约 `106 BPM`。

取证结果：

```text
vocal attacks                     ~250
stable pitch runs                 247
attack IOI p25 / median / p75     0.151 / 0.267 / 0.430 s
attacks within 16th grid ±15 ms   63.6%
attacks within 16th grid ±25 ms   81.2%
stable note duration median       0.160 s
stable note duration p90          0.454 s
longest stable note               1.380 s
```

相邻攻击中约 42% 相隔一个十六分音符，约 28% 相隔两个十六分音符。它更像按谱面放置的 note/phoneme events，不像一个整句自由滚动的循环。

### 4.2 音高被明显 hard-tune / sample-map

高置信 F0 帧到最近十二平均律音的距离：

```text
median absolute error   4.8 cents
p90 absolute error      15.0 cents
p95 absolute error      21.1 cents
run-internal F0 IQR     3.56 cents median
run-internal F0 std     4.07 cents median
```

稳定音内部非常平，支持逐音符 hard-tune 或 sample pitch mapping。它并非直接保留自然演唱中全部连续滑音与微抖。

### 4.3 乐句按小节推进，而不是 3.1 秒循环

参考 onset/RMS 自相关强峰约为：

```text
0.279 s   八分音符附近
0.569 s   四分音符附近
1.126 s   两拍附近
2.264 s   一小节附近
4.528 s   两小节附近
```

`3.10 s` 相关仅约 `0.186`，不是主周期。多特征结构变点约在 `1.49 / 19.57 / 37.71 / 54.6–54.9 / 75.05–76.07 / 91.2–91.8 s`，接近按八小节推进的段落设计。

### 4.4 它有分段层级和空间自动化

Demucs 宏观估算显示，人声相对伴奏从早段约 `-2.34 dB`，逐步推进到高潮约 `+6.37 dB`。立体声人声也从较窄的 `side/mid -15.35 dB` 扩展到高潮约 `-5.96 dB`。

高通后互相关除 0 ms 主峰外出现约 `±2.54 ms` 对称次峰。它支持短 stereo delay、doubler、叠轨或宽混响的推断，但无法区分具体手段。

### 4.5 它做了非常激进的母带

```text
integrated loudness   -6.4 LUFS
loudness range         2.6 LU
true peak             +0.5 dBFS
```

这是很强的压缩/限制，并存在 intersample overs。响度会显著放大“更有冲击力”的主观印象，但不应照抄 `+0.5 dBTP` 的过载。

## 5. 与 Performance V4 的量化差距

统一用 htdemucs 分离并用同一套特征口径，对比了：

- 《突然想起你》Performance V4
- 《残酷月光》Performance V4
- 《恶作剧》Performance V4
- B 站《绿光》参考

| 维度 | 《绿光》参考 | 我们的 V4 | 判断 |
| --- | ---: | ---: | --- |
| 3.1s onset 自相关 | `0.172` | `0.460–0.634` | V4 有强固定循环指纹 |
| 3.1s 周期折叠一致性 | `-0.010` | `0.276–0.398` | 三首 V4 最佳周期都精确为 3.10s |
| vocal attacks / min | `125.6` | `121–139` | 密度接近，问题是落点而非数量 |
| active runs / min | `~5.0` | `25.3–34.7` | V4 gate 更碎、起停更陡 |
| vocal spectral centroid | `2728 Hz` | `1211–1249 Hz` | V4 明显更暗、更窄 |
| vocal rolloff 85% | `5276 Hz` | `2186–2670 Hz` | V4 缺高频辅音/气息 |
| vocal 4–8k share | `-10.17 dB` | `-24.75～-27.17 dB` | presence 缺口约 14.6–17 dB |
| vocal harmonicity median | `13.19 dB` | `19.64–21.19 dB` | V4 更周期化、蜂鸣/塑料感更强 |
| vocal active dynamic P90-P10 | `9.87 dB` | `13.68–15.90 dB` | V4 人声音量更飘 |
| vocal / instrumental active median | `-1.81 dB` | `-3.61～-5.96 dB` | V4 主唱约再埋低 1.8–4.2dB |
| vocal side/mid | `-7.69 dB` | `-30.7～-54.9 dB` | V4 基本是单声道干声 |
| vocal L/R correlation | `0.710` | `>=0.998` | 参考有明显 stereo processing |
| integrated loudness | `-6.4 LUFS` | `-16.1～-18.4 LUFS` | 参考响约 9.7–12 LU |
| true peak | `+0.5 dBFS` | `-2.9 dBFS` | 参考更激进但不安全 |

最关键的归因证据是：三首原始 guide vocal 在 3.1 秒处的 onset 相关只有约 `-0.034～0.031`，但经过 V4 后都变成强 3.1 秒周期。这个机械纹理是 renderer 注入的，不是歌曲本身。

V4 的原始 YuShu TTS voicebank 自己就只有约 `1403 Hz` spectral centroid、`-27.6 dB` 的 4–8k 占比，和成品非常接近。EQ 无法真正创造已经缺失的齿音、辅音攻击、气息和音色细节。

## 6. 参考作品大概率如何制作

### 高置信推断

1. 从原曲获得伴奏和旋律/节拍参考。
2. 建立哈基米短音节或音素音源，不把整句当作一个不可拆分的 cycle。
3. 先填适合原曲音符数和节奏的改编词，再把字头放到 note onset。
4. 每个音节或元音 sustain 独立设置 pitch、duration、attack、tie/melisma 和 rest。
5. 音高按半音网格 hard-tune，长音按该 note 的时值拉伸。
6. 按 verse/chorus/climax 做人声音量、宽度、doubles 与伴奏让位自动化。
7. 最后做 EQ、压缩、短空间效果和强母带。

### 中置信推断

- 使用多个 attack/sustain 或不同力度/音高的 voice units，而非单个 Siri TTS 样本。
- 使用短 stereo delay、doubler、叠轨或短 ambience；高潮层数更多。
- 可能使用人力 VOCALOID/UTAU/OpenUtau/Melodyne/DAW sampler 中的一种或多种。

### 不能确认

- 具体 DAW、调音器、压缩器、EQ、混响、插件参数。
- 是否使用 RVC/SVC 或具体角色模型。
- voicebank 的精确来源、切分方法和样本数量。

## 7. 为什么只换歌曲不够

歌曲确实会影响：

- guide 的音域、滑音和 F0 跳变；
- 原始音节密度；
- 伴奏 stereo 宽度；
- Demucs 分离残留与孔洞。

因此《残酷月光》《恶作剧》比《突然想起你》更适合作为 V4 流程验证。

但换歌无法影响：

- 3.1 秒固定七槽；
- 连续 cycle 在静音中继续推进；
- 复唱入口可能落在任意字；
- 单一低带宽中文 TTS；
- 24 ms gate 引入的碎边；
- mono dry vocal；
- 缺少 section automation 和成品级 mix bus。

所以“素材歌曲难唱”是局部问题，不是当前主要问题。

## 8. 下一步改进计划

### Stage 0：冻结参考基线（已完成）

- 固定来源 hash、BPM、结构、F0、onset、宽度、相对电平和母带指标。
- 保留原 AAC、info JSON 和 Demucs stems。
- 把现有 V4 降级为 regression baseline，不再视为最终架构。

### Stage 1：Alignment-first POC

先做《绿光》20–30 秒同曲 A/B，只改变编排层，继续使用当前 YuShu voicebank 和 dry mix，以隔离变量。

新增 event manifest，至少包含：

```text
start / end / syllable / note / tie / melisma / rest
phrase_reset / attack / hold / release / gain
```

渲染规则：

- 辅音只占约 30–80 ms，锚定到 note onset，不随整段 F0 抖动。
- 只拉伸元音 nucleus；跨音延长元音，不重复字头。
- rest 必须静音，只有乐句边界才 reset。
- 自动 note/onset 只生成 draft，关键 20–30 秒先人工校正。

验收：

```text
event onset error median <= 35 ms
event onset error p90    <= 70 ms
duration error           <= 50 ms or 5%
steady F0 median         <= 10 cents
steady F0 p90            <= 35 cents
rest / tie / reset       100% execute manifest
loudness-matched A/B     timing preference >= 4/5 clips
```

如果 Stage 1 在等响 A/B 中没有明显优于 V4，不进入音色阶段，继续修 arranger。

### Stage 2：多单元、高带宽 voicebank

- 从整句切换到 consonant + vowel nucleus (+ coda) 单元。
- 先按参考确认真实 phoneme inventory，不把系统锁死为固定七个整字。
- 每个主要单元准备 3–5 个 pitch centers、至少 soft/neutral/strong 两到三档力度。
- 选择最接近目标 F0 和力度的样本后，只做小范围 PSOLA。
- 常态 pitch shift 控制在 `±3～4 semitones`，P90 不超过 4 semitones，max 不超过 7。

当前 Siri source median 约 204 Hz；女声 demo 的 guide median/p95 约 354/475 Hz，相当于对单一样本大量上移约 `+9.6/+14.6 semitones`，塑料感属于结构问题。

如果多样本 voicebank 仍无法达到角色音色，再 A/B 训练过的 RVC/SVC post-stage；不要先上 zero-shot VC，也不要未经确认下载大于 1 GB 的模型。

### Stage 3：表达与 formant

- 把 pitch 分成 macro note 和 micro residual。
- note center/transition 来自 arrangement；只在元音 sustain 上叠加 scoop、portamento、vibrato residual。
- 辅音不跟随 vibrato 抖动。
- 支持每 event 的 vibrato delay/rate/depth、portamento、gain envelope。
- 默认 formant preserve；只有参考量测支持时才做小范围 register-dependent shift。

做三版 A/B：raw guide F0、纯 note quantized、note + expressive residual。

### Stage 4：分段人声制作与安全 loud master

- vocal HPF / corrective EQ / de-ess / serial compression / light saturation。
- 短 room 或 plate、可选 tempo delay、微 doubler。
- 伴奏在 2–5 kHz 做约 1–2 dB 动态让位。
- verse 保持窄和低，chorus 加宽，climax 增加 doubles/layer 并抬高主唱。

输出两版：

```text
fair QA master       -14 LUFS / <= -1 dBTP
style loud master    first target -9 to -8 LUFS / <= -1 dBTP
```

参考的 `-6.4 LUFS / +0.5 dBTP` 只作为风格证据，不作为安全验收目标。

### Stage 5：可复用性验证

- 用未参与调参的一首男声、一首女声，各做 20–30 秒。
- auto draft + 人工修订的目标耗时：每 30 秒不超过 15 分钟。
- 仍需满足 onset、F0、rest、响度和无 click 门禁。

若只能在《绿光》上工作，应诚实标为 song-specific 工程，不能称为可复用工作流。

## 9. 实施边界

完整 V5 预计涉及 ADR、event schema、arranger、voicebank manifest/selector、expression、mix preset、QA、tests 和 README，约 8–12 个 tracked files，并且修改 renderer 接口。

按项目 `AGENTS.md`，实施前必须先写：

```text
docs/decisions/ADR-002-hajimi-v5-event-renderer.md
```

ADR 草稿需用户确认。确认后只实施 Stage 1 的 20–30 秒 alignment A/B，并停下来等待试听；不默认连续推进 Stage 2–5，不自动 commit，也不下载大模型。

