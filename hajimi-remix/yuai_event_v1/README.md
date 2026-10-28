# 《雨爱》Hajimi Note/Event V1

## 试听

- `../outputs/yuai_hajimi_event_v1/yuai_hajimi_event_v1.wav`：推荐成品。
- `../outputs/yuai_hajimi_event_v1/AB_original_then_hajimi.wav`：等响 A/B；前 50.15 秒为原曲，静音 1 秒后为 Hajimi 版。
- `../outputs/yuai_hajimi_event_v1/yuai_hajimi_vocal.wav`：处理后人声层。
- `../outputs/yuai_hajimi_event_v1/yuai_original_loudness_matched.wav`：本地等响比较用原曲片段。

所有音频均为 44.1 kHz、stereo、24-bit PCM，仅用于本地个人技术调研，不公开发布或商用。

## 选段与输入

本次直接使用仓库已有的同轴 stems：

- `dataset/source-songs/vocals/yuai.wav`
- `dataset/source-songs/no_vocals/yuai.wav`

两轨均为 44.1 kHz stereo PCM16、时长 `282.633288s`。成品选择第一遍完整副歌 `66.450–116.600s`，共 `50.150s`；先裁剪，再进入任何 Python 音频分析。

## 制作方法

流程复用用户认可的《绿光》体验基线，不使用固定 3.1 秒循环：

1. 用轻量 Praat autocorrelation 从裁剪后 vocal 提取稳定 F0、onset、休止和乐句事件。
2. 把 158 个稳定音符编排为 97 个音节事件；辅音 attack 只出现一次，元音可以跨 tie / melisma。
3. 中文 Yushu 单元提供“哈基米南北绿豆”字头，日语 Hajimi 原声提供高带宽 sustain。
4. 仅桥接没有低能量休止证据且不超过 280 ms 的 pitch-tracker 空洞；真实乐句休止保留。
5. 副歌后半段增加 stereo doubles、短 room、人声自动化与伴奏 presence ducking。
6. 最终使用安全响度母带，不复制 clipping。

可复用原则见 [`../NOTE_EVENT_PLAYBOOK.md`](../NOTE_EVENT_PLAYBOOK.md)。事件提取入口是 `../note_event_poc/extract_events.py`；当前 renderer 仍位于 `../greenlight_poc/render_demo.py`，但已支持 `--slug`、`--title`、`--bridge-gap-seconds` 和单 variant 渲染。

## 自动 QA

| 项目 | 结果 | 判定 |
| --- | ---: | --- |
| note event coverage | 158 / 158 | PASS |
| rendered F0 error median / P90 | 1.59 / 3.81 cents | PASS |
| octave error | 0 | PASS |
| syllable onset within 100 ms | 96 / 97 | WARN |
| matched onset error median / P90 | 17.24 / 43.48 ms | PASS |
| splice boundary over local 2 ms difference P99 | 0 / 193 | PASS |
| final mix side / mid | -7.13 dB | PASS；原曲 -8.43 dB |
| final loudness / LRA / true peak | -9.6 LUFS / 0.6 LU / -1.4 dBTP | PASS |
| equal-loudness original | -9.7 LUFS / 0.8 LU / -1.0 dBTP | PASS |
| strict ffmpeg decode | 全部交付 WAV | PASS |
| 人工试听 | 自动化未执行 | USER REVIEW REQUIRED |

唯一未被 energy-onset detector 命中的事件是第 25 个“南”，位于 clip `10.270s`。该处并非静音：attack 约 `-22.9 dBFS RMS`，而是前一元音仍在延续，能量包络没有形成独立峰。因此标记为 WARN，而不是冒充 100% 自动通过；需要最终人工判断字头是否清楚。

## 性能复盘

此前卡死发生在多个代理并行扫描整首 `282.6s` 音频，同时主进程运行全曲 Whisper / Praat。重启后无法证明单一进程就是直接根因，但这种并行方式本身不合理。本轮改成以下硬约束：

- 音频分析前必须先由 ffmpeg 裁剪，extractor 默认拒绝超过 75 秒的输入。
- 不再为 event draft 启动 torchcrepe / Whisper / Demucs；只用 Praat autocorrelation。
- 不并行运行音频代理或多种声源 renderer；成品只渲染已验证的 hybrid。
- `OMP_NUM_THREADS`、`OPENBLAS_NUM_THREADS`、`VECLIB_MAXIMUM_THREADS`、`NUMEXPR_NUM_THREADS` 和 `MKL_NUM_THREADS` 均限制为 2。
- 将长 Gaussian smoothing 改成 section linear crossfade；长卷积改用 overlap-add；micro pitch-shift 使用 fast resampling。

实测资源：

| 阶段 | 墙钟时间 | 峰值 RSS |
| --- | ---: | ---: |
| event extraction | 0.212s | 506 MB |
| hybrid render + mix | 3.066s | 1.334 GB |
| pitch/onset/splice QA | 0.313s | 621 MB |

交付后只读复核：无遗留 Python / ffmpeg / Demucs / Whisper 进程，swap in/out 均为 0，系统内存可用率 87%。`nice -n 10` 在受限环境中没有权限生效，因此真正起作用的是裁剪、串行、线程上限和算法替换。

自动 QA 不能证明歌词可懂度和角色感。本轮最终体验门仍是人工试听，重点听：第 10 秒附近的“南”、低音 `B3/A#3` 三个事件，以及后半副歌人声是否过宽或过前。
