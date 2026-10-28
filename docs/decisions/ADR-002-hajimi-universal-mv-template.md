# ADR-002 · Hajimi 通用 MV 模板采用自有轻量渲染器

**日期**：2026-08-13  
**状态**：已采纳  
**决策者**：用户 + Codex

## 背景

Hajimi 已能产出带精确 note / syllable 时间的音频。下一步需要为每首歌生成配套视频，并尽量把单曲制作收敛为“注入音频和歌词即可”。模板本身必须能在没有原版 MV 时独立工作；首个《雨爱》验证中，用户进一步决定把同曲原版 MV 作为可选窗口素材，以获得更完整的观看体验。

双平台调研中，B 站 [Daily One AE 模板](https://www.bilibili.com/video/BV1s94y1L7sF/) 的“主题图、歌曲信息、音频响应波形、滚动歌词”结构最接近目标，也已有 AI 翻唱复用案例。但直接依赖其 AE 工程会带来本机软件依赖、授权边界、批量自动化和渲染性能问题。

## 决策

1. 以 Daily One 的信息层级和视觉语法作为首版设计基础，不复制其角色图、字体、工程代码或其他第三方素材。
2. 在项目内实现自有的轻量模板，默认使用 HTML / HyperFrames 生成画面，FFmpeg 后期复用最终 master 音频；不把 After Effects、Premiere 或 Resolve 作为生产依赖。
3. 固定单曲输入契约：

   ```text
   song.wav
   timed_lyrics.json   # start / end / token / line_id
   theme.json          # title / palette / artwork / credits
   visual.mp4          # 可选；静音、已裁到成片时间轴的窗口视频
   format              # 16:9 或 9:16
   ```

4. 优先读取 Hajimi renderer 已有的 syllable/event 时间，不再用 Whisper 重识别合成唱词。《雨爱》现有 97 个 syllable groups 可直接作为首个 POC 的逐字高亮时间轴。
5. 横版与竖版共享数据，但分别 reflow 布局；禁止把 16:9 成片简单中心裁切为 9:16。短视频首发形态默认采用 1080×1920（9:16），横版仅在后续明确需要时单独生成。
6. 默认性能预算为 1080p30、预计算音频能量/频谱、横竖版串行渲染、HyperFrames workers 不超过 2、FFmpeg 2–4 threads。首版不使用 4K60、Three.js、大量粒子、长时间大面积 blur 或多路高码率背景视频。
7. 当前阶段明确为内部验证/体验。后续内部制作无需反复确认发布许可；只有用户明确提出发布、投稿、分发或商用时，才进入独立发布 gate，重新对齐音乐/歌词/图片/字体/模板授权、AI 标识、署名和平台要求。
8. `visual.mp4` 只作为可选视觉层：必须静音，成品音频仍只来自 `song.wav`；优先在进入 HyperFrames 前裁成精确时长、统一窗口分辨率与兼容编码。每支成片只挂一个连续视频元素，不按章节拆成多路视频。没有该输入时回退到自有生成视觉。

## 影响

- 后续每首歌的正常增量应主要是音频、时间歌词、主题配置和可选的预裁窗口视频，而不是重新剪一支 MV。
- 外部模板只承担视觉研究与 A/B 参考，不成为不可替换的运行依赖。
- 初次搭建成本高于直接套 AE，但会得到可审计、可批量、双画幅一致且资源可控的工作流。
- 该决策不改变张韶涵 RVC 主线，也不改变 Hajimi note/event 音频 renderer。

## 首个验证目标

以《雨爱》50.15 秒 Hajimi 成品制作 1080×1920（9:16）POC，验证逐字歌词、音频响应、信息层级和资源预算。画面采用原生竖屏 reflow：上部窗口承载已对齐且静音的原版 MV 片段，中部歌曲信息，下部逐字歌词与进度；没有视频素材时可回退到生成雨窗，不从横版裁切。

该目标已于 2026-08-13 完成内部体验验证。当前规范名为 `hajimi-rain-window-v1`，权威像素、字幕、代理、渲染、post-mux 和 QA 约束见 [`hajimi-remix/HAJIMI_PRODUCTION_STANDARD.md`](../../hajimi-remix/HAJIMI_PRODUCTION_STANDARD.md)。从《雨爱》专用脚本抽取共享 schema / preset / CLI 的后续接口决策见 ADR-003 草案；在其确认前，不把当前工程描述为已完成的一键批处理器。
