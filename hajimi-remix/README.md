# Hajimi Remix · 固定中文歌词调音实验

这个子目录与仓库原来的“张韶涵 RVC 翻唱”主线相互独立。原主线只替换音色，输入人声唱什么，RVC 仍然唱什么；这里保留源歌的旋律、节奏和伴奏，把主唱内容替换成循环的普通话“哈基米南北绿豆”。

## 当前主线：Note/Event Audio V1

用户已接受《绿光》Note/Event POC 的体验方向，《雨爱》进一步完成 50.15 秒 hybrid 音频和 1080×1920 MV 联合验证。当前成品主线不再使用固定 3.1 秒整句循环，而是按歌曲的 note、syllable、phrase 和真实休止进行编排：中文 Yushu attack 保留字头，日语 Hajimi sustain 提供高带宽角色纹理，再做分段 mix 与安全母带。

跨音频和视频的唯一规范见 [`HAJIMI_PRODUCTION_STANDARD.md`](HAJIMI_PRODUCTION_STANDARD.md)，音频细节见 [`NOTE_EVENT_PLAYBOOK.md`](NOTE_EVENT_PLAYBOOK.md)，《雨爱》实测见 [`yuai_event_v1/README.md`](yuai_event_v1/README.md)。V1 当前严格支持 20–75 秒完整乐段；整首歌自动拼接和一键多曲批处理尚未宣称支持。

## MV 配套方向

配套视频已确定以 B 站 Daily One 的“主题图 + 歌曲信息 + 音频响应 + 滚动歌词”结构为视觉基础，但使用项目自有的轻量 HTML / HyperFrames + FFmpeg 模板实现，不直接依赖或复制第三方 AE 工程。目标输入固定为 `song.wav + timed_lyrics.json + theme.json + format`，以后单曲主要替换音频、歌词时间轴和主题配置即可。

当前所有 Hajimi 音频与 MV 均处于内部验证/体验阶段。只有用户明确提出发布、投稿、分发或商用时，才启动单独的发布 gate；完整决策见 [`ADR-002`](../docs/decisions/ADR-002-hajimi-universal-mv-template.md)。

## 旧版 audition baseline：Performance V4

```text
普通话七音节 ──> 借用 Clean V2 的时值 / 停连 / 重音
                                      │
已有源歌干声 ──> torchcrepe 提取 F0 / 停顿 / 响度
                                      ↓
                         Praat PSOLA 重合成中文唱声
                                      ↓
              新干声 + 原 stereo instrumental stem
```

Chinese V3 只把 1.681 秒的普通话 TTS 整句循环，并给它套上原唱 F0。七个字仍然约每 0.2 秒匀速念出，音高虽然正确，听感却是“带音高的朗读”。

V4 把用户认可的日语 Clean V2 当作 performance template，只借它的音节长短和强弱，不混入日语声音。普通话七音节先重排成一个 3.1 秒表演周期，再沿原曲 F0 连续演唱。默认七槽如下：

| 字 | 哈 | 基 | 米 | 南 | 北 | 绿 | 豆 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 时值（ms） | 236 | 145 | 189 | 704 | 255 | 1247 | 323 |
| 相对重音（dB） | +7.7 | +6.9 | -5.5 | -2.6 | +5.5 | 0.0 | -7.3 |

因此“南”和“绿”成为真正的长音，七个字不再平均吐出；V4 同时恢复 Clean V2 的 continuous tile、`cycle_resets=0` 和 24ms gate。

## 中文声源

默认原始声源位于：

```text
assets/voicebank/hajimi_nanbei_lvdou_zh_yushu.wav
```

它由当前 Mac 的 `AVSpeechSynthesizer` 普通话女声生成：

- 文本：`哈基米南北绿豆`
- language：`zh-CN`
- voice id：`com.apple.ttsbundle.siri_yushu_zh-CN_compact`
- rate：`0.45`
- pitch multiplier：`1.0`
- 音频：44.1 kHz、mono、24-bit PCM、1.681 秒
- SHA256：`cd48f74a9ce92705f10814122717d5570b25bfaa7674b94fcf234150f0f99c7d`

Whisper-base 和 Whisper-small 对未调音 WAV 都完整识别为 `哈基米南北綠豆`；“綠”只是繁体字形。变成歌声后，语音 ASR 会因旋律取代普通话声调而写成“吉/敏/旅/都”等同音字，因此最终仍以人工试听为准。

V4 解决的是“严格中文音素 + 不再匀速朗读”。音色仍是本机中文 TTS，不是东海帝王原角色；若要同时复刻具体角色音色，需要在中文唱声之后增加 zero-shot SVC。

本地素材与衍生成品都受本目录 `.gitignore` 排除，只用于个人技术调研；不要提交到 git、公开分发或商用。

## 运行环境

复用现有 RVC venv 中的音频依赖，但不调用 RVC 模型：

```bash
RVC_PY="frameworks/rvc-webui-macos/.venv/bin/python"
"$RVC_PY" -c "import librosa, parselmouth, soundfile, torchcrepe, pyworld; print('OK')"
```

## 复现 Performance V4

示范曲使用本项目以前处理过的《突然想起你》：

```bash
RVC_PY="frameworks/rvc-webui-macos/.venv/bin/python"
"$RVC_PY" hajimi-remix/render.py \
  --guide-vocal dataset/source-songs/vocals/turanxiangqini.wav \
  --instrumental dataset/source-songs/no_vocals/turanxiangqini.wav \
  --voice-cycle hajimi-remix/assets/voicebank/hajimi_nanbei_lvdou_zh_yushu.wav \
  --lyrics "哈基米南北绿豆" \
  --voice-cycle-language zh-CN \
  --alignment-mode performance-template \
  --start 11.3 \
  --duration 45 \
  --target-peak-dbfs -2.9 \
  --title "突然想起你 · 中文哈基米 Performance V4" \
  --slug turanxiangqini_zh_performance \
  --output-dir hajimi-remix/outputs/turanxiangqini_chinese_v4_performance
```

产物包括：

- `turanxiangqini_zh_performance_hajimi_demo.wav`：可直接试听的伴奏混合成品。
- `turanxiangqini_zh_performance_hajimi_vocal.wav`：单独的新干声。
- `report.json`：歌词、语言、七槽边界/重音、输入哈希、F0、响度和伴奏直通误差。

V4 特意把最终响度匹配到用户认可的日语 Clean V2，避免“更响所以更好听”的 A/B 偏差。当前成品为 45 秒、44.1 kHz、stereo、24-bit PCM，约 `-16.1 LUFS`、true peak `-2.9 dBFS`。F0 中位绝对误差约 `3.2 cents`、P90 约 `21 cents`，没有超过 1dB 的 vocal limiter 压缩；伴奏在 master gain 前的直通误差为 `1.11e-16`。

## 用 Performance V4 快速 audition 另一首歌

替换 `--guide-vocal`、`--instrumental`、`--title` 和 `--slug` 可以快速试听固定 phrase baseline。两条音轨必须来自同一首歌且时间轴一致。它不能作为当前标准成品：确认歌曲方向后，必须进入 `hajimi-note-event-v1`，制作该歌曲专属的 note / syllable / phrase alignment。

## 已知边界

1. V4 的节奏、长音和重音已经显著接近日语 Clean V2，但中文 TTS 的长元音仍比真人演唱更干净、更周期化，可能残留少量“塑料感”。
2. `performance-template` 是固定七槽循环，不是逐音符人工 VOCALOID 工程；它优先复刻用户认可版的整体表演感。
3. 只有官方 instrumental/stems 才能严格保持背景音乐；当前伴奏来自 Demucs 分离，仍可能有轻微人声残留或孔洞。
4. `--alignment-mode guide-onsets` 是实验性逐音节模式，虽然更贴歌曲起音，但当前会损害“绿豆”的可懂度，不作为默认成品。
