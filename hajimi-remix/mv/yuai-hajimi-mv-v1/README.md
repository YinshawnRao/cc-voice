# 雨爱 · Hajimi MV V1

当前状态：`FINAL_QA_PASS`。权威成片是 [`renders/yuai-hajimi-mv-v1.mp4`](renders/yuai-hajimi-mv-v1.mp4)，raw render 只是中间产物。

这是 ADR-002 的首个实例：保留 Daily One 类播放器的信息层级，自建 1080×1920「雨窗播放器」竖屏视觉。上方窗口使用静音且已精确裁切的《雨爱》官方 MV，成品唯一音轨仍是 Hajimi master；实际听到的 97 个“哈基米南北绿豆”音节由成品 render report 直接生成逐字高亮，不会用 ASR 猜时间，也不会展示并未演唱的原歌词。

## 输入与真值

- 音频：`assets/bgm.wav`，50.15 秒，44.1kHz stereo PCM24
- 字幕：`data/timed_lyrics.json`，97 syllables / 16 lines / 4 phrases
- 节拍：`audiomap.json`，152 BPM 分析草图
- 音频响应：`data/audio-bands.json`，30fps / 16 bands，离线预计算
- 视频窗口：`assets/yuai-official-mv-window.mp4`，928×642、29.97fps、1503 帧、50.150 秒、无音轨
- 主题：`data/theme.json`
- 分镜：`STORYBOARD.md`

`data/timed_lyrics.json` 中相邻音频交叉淡化已压成互斥 UI 区间，因此任意时刻最多高亮一个字。`g025`（10.270 秒“南”）保留人工听审标记，视觉没有擅自改时。

## MV 来源与同步

- 来源：[杨丞琳官方频道《雨爱》MV](https://www.youtube.com/watch?v=oec9R5ypf-o)，页面标注 © 2010 Sony Music Entertainment Taiwan Ltd.
- 本地源：`hajimi-remix/work/source/rain-love-original/oec9R5ypf-o-hls.mp4`，852×480、29.97fps、AAC/H.264、282.633288 秒
- 源窗口：66.450–116.600 秒；代理只保留该 50.150 秒并预裁成雨窗内部尺寸
- 同步核验：官方 MV 与 `raw/source-songs/yuai.wav` 各取 60–90 秒、转 mono 8kHz 做 waveform cross-correlation，峰值 lag 为 0 秒，normalized peak 为 1.000
- 代理 SHA256：`29d235d04d6a5341ee631112a294a3856727e885b4d0d26aaf1101a52f381693`
- 代理关键帧：约每 1.001 秒一帧（`-g 30 -keyint_min 30 -sc_threshold 0`），避免正式渲染随机 seek 时重复长 GOP 解码

视频在 host root 只挂一次，跨四个章节连续播放；四个 subcomposition 不各自解码视频。`data-media-start=0` 是因为代理已经预裁，不能再填 66.45。视频强制 `muted`，避免原唱叠入成品。

## 生成

```bash
python3 scripts/build_timed_lyrics.py
python3 scripts/build_frames.py
node /Users/yinshawnrao/.codex/skills/music-to-video/scripts/assemble-index.mjs \
  --storyboard STORYBOARD.md --hyperframes . --audiomap audiomap.json \
  --bgm assets/bgm.wav --out index.html
python3 scripts/finalize_index.py
```

`build_frames.py` 只在构建期切出每个章节所需的数据；`finalize_index.py` 注入一个连续的静音视频元素与底部信息带。浏览器运行时不做 WebAudio、F0、ASR 或网络请求。

## 检查与预览

```bash
HYPERFRAMES_SKIP_SKILLS=1 npx --yes hyperframes@0.6.69 lint --json
HYPERFRAMES_SKIP_SKILLS=1 npx --yes hyperframes@0.6.69 validate --json
HYPERFRAMES_SKIP_SKILLS=1 npx --yes hyperframes@0.6.69 inspect --json
HYPERFRAMES_SKIP_SKILLS=1 npx --yes hyperframes@0.6.69 snapshot \
  --at 0.2,5.5,10.27,18.6,27,33.2,39.9,43,49.2 --describe false
HYPERFRAMES_SKIP_SKILLS=1 npx --yes hyperframes@0.6.69 preview
```

当前自动检查：lint 0 error / 0 warning；inspect 0 issue；边界快照确认 18.28 / 32.58 / 42.295 秒三个 phrase reset 的第一字、全局时间码和进度条都连续。曾出现过 subcomposition 依赖辅助属性导致正式 render 时间线静止，以及每段重复 entrance 遮住第一字的问题；现已改为按正式 composition id 取宿主，并让歌词面板在 section 第一帧直接可见。这两项已写入 V1 标准，不依赖人工绕过。

## 性能边界

- 1080×1920、30fps、SDR，原生 9:16 reflow，不从横版裁切
- 全片渲染先用 1 worker，最多 2 workers
- FFmpeg 固定 2 threads
- 原版 MV 先裁为 50.15 秒 / 7.1MB、最长关键帧间隔约 1.001 秒的单路代理，不让渲染器解码完整 282.6 秒源文件
- 不使用 Three.js、大粒子系统、实时 FFT、Whisper、多路并发视频或多画幅并行
- 最终 MP4 渲染后再回灌当前 PCM24 master，mux 后文件才是权威成品

正式 render 实测：HyperFrames 0.6.69、high / CRF18 / SDR、1 worker，用时 `56.23s`，maximum RSS `853,393,408 bytes`（约 814MiB），swap `0`。最终 MP4 为 50.150 秒、11,525,309 bytes，SHA256 `d0cc2f584c15115f0747e13861518651caba4d95a81867b903f59745e6a0e46e`。

终片独立 QA：strict decode PASS；H.264 High 1080×1920 / 30fps / yuv420p / BT.709；AAC-LC 44.1kHz stereo；`-9.6 LUFS / 0.7 LRA / -1.3 dBTP`；黑场和 1 秒静音均为 0；AAC 对 master lag `0ms`、相关性 `0.999884142`；音画首尾差均小于一帧。机器报告见 `qa/final-media-qa.json`。
