# ADR-003 · Hajimi Audio / MV V1 批量生产契约

**日期**：2026-08-13  
**状态**：已采纳  
**决策者**：用户 + Codex

**确认记录**：2026-08-13，用户在验收《雨爱》成片与 V1 规范后，明确要求按该规范继续制作两首作品并为后续规模化打基础。

## 背景

《绿光》已证明固定 3.1 秒 phrase loop 不适合作为成品主线；《雨爱》进一步验证了 Note/Event hybrid audio 与 1080×1920 Rain Window MV 的联合体验。当前结果可听、可看且资源可控，但 audio renderer、MV builder 和单曲常量仍散落在多个 POC / 项目脚本中，尚不能安全地复制目录后批量运行。

本 ADR 决定是否把已接受的制作方法升级为版本化共享接口。详细像素、参数和门禁见 [`hajimi-remix/HAJIMI_PRODUCTION_STANDARD.md`](../../hajimi-remix/HAJIMI_PRODUCTION_STANDARD.md)。

## 建议决策

1. 把 `hajimi-note-event-v1` 冻结为 20–75 秒单乐段的正式 audio preset；Performance V4 降为 audition / regression baseline。
2. 把 `hajimi-rain-window-v1` 冻结为 1080×1920、30fps、SDR 的正式 MV preset。
3. audio 与 MV 通过版本化文件交接，不通过隐式目录或同名文件猜测：

   ```text
   song-manifest.json
   event-approved.json
   song.wav
   timed_lyrics.json
   theme.json
   visual.json + visual.mp4   # optional
   ```

4. 建立共享 schema、preset、fixture、builder、QA 和单一 CLI；每首歌只提交 recipe / manifest，不复制 renderer 源码。
5. 状态机、自动 QA、人工试听和 artifact hash 都成为 package 的一部分；任何 FAIL 禁止继续打包。
6. 完整歌曲、multi-song parallel、SVC post-stage、多 pitch-center voicebank 和跨平台字体冻结不塞进 V1，分别在验证后升级版本。

## 实施范围（确认后执行）

预计会新增或修改 8–12 个 tracked files，因此按 `AGENTS.md` 必须先确认本 ADR：

```text
hajimi-remix/schemas/song-manifest.schema.json
hajimi-remix/schemas/event-approved.schema.json
hajimi-remix/schemas/timed-lyrics.schema.json
hajimi-remix/schemas/theme.schema.json
hajimi-remix/schemas/visual.schema.json
hajimi-remix/presets/hajimi-note-event-v1.json
hajimi-remix/presets/hajimi-pop-v1.json
hajimi-remix/presets/hajimi-rain-window-v1.json
hajimi-remix/scripts/hajimi_audio.py
hajimi-remix/mv/template-v1/scripts/hajimi_mv.py
hajimi-remix/scripts/qa_package.py
hajimi-remix/tests/test_production_contract.py
```

CLI 只暴露窄入口；每个命令只处理一首歌，批次由外层顺序调用：

```bash
python3 hajimi-remix/scripts/hajimi_audio.py validate|draft|render|qa|package recipes/<song>.json
python3 hajimi-remix/mv/template-v1/scripts/hajimi_mv.py build|check|snapshot|render|mux|qa recipes/<song>.json
```

## 验收

- 《雨爱》作为 golden fixture，生成物 timing、97 syllables、158 notes、master hash 链和 MV section 行为均可复算。
- 新增两首未参与实现的真实歌曲，分别完成一个 20–75 秒片段；每首从 recipe 到预览不复制 renderer 文件。曲目性别不是随机批次的强制条件，跨性别泛化另作定向回归。
- 输入不同轴、超过 75 秒、未批准 event、字幕 hash 不匹配、无 visual fallback、字体未通过 preflight、QA FAIL 都有明确 fail-closed 测试。
- 单任务串行、audio threads=2、HyperFrames workers=1、FFmpeg threads=2；每阶段报告 wall/RSS/swap。

## 影响

- 优点：消除《雨爱》硬编码、时间轴双真值和目录分叉；第二首开始只替换输入与 recipe。
- 成本：需要一次接口抽取和 golden tests；不能把这部分混进当前单曲成片的紧急修补。
- 不影响：张韶涵 RVC 三阶段主线；当前内部验证/体验约定；已有音频和视频二进制产物。

## 未选择的方案

- 继续复制《雨爱》目录：最快，但会迅速形成无法审计的模板分叉。
- 直接把 `theme.json` 当唯一配置：当前脚本并未真正消费全部 theme 字段，无法构成可靠接口。
- 先支持整首歌或多首并行：会重新引入之前的 CPU / 内存事故风险，且没有 V1 回归证据。
