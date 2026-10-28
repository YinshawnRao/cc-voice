# 《绿光》对标实验 · POC V1

## 结论

这个 POC 不再沿用 Performance V4 的固定 3.1 秒七槽循环。它把参考作品当作 timing / note / dynamics guide，逐事件生成 58 个音节攻击和 77 个稳定音符；参考人声本身没有混入结果。

首版选择参考视频的 `65.000–91.800s`，通过伴奏波形相关映射到孙燕姿官方原版的 `159.959–186.759s`。这 26.8 秒覆盖高潮前铺垫和完整高潮，适合同时判断字头、长音、段落叠轨和母带。

官方原版输入来自孙燕姿官方频道的 [Green Light](https://www.youtube.com/watch?v=0oPLlmUkCC8)。参考目标来自用户指定的 [咆哮丶土拨鼠 B 站作品](https://www.bilibili.com/video/BV1L62NBWEAk)。素材和输出仅用于本地个人技术调研，不公开发布或商用。

## 试听文件

- `../outputs/greenlight_poc_v1/AB_reference_then_ours.wav`：等响 A/B，前 26.8 秒是参考，1 秒静音后是本项目版本。
- `../outputs/greenlight_poc_v1/B_ours_greenlight_event_v1.wav`：本项目成品。
- `../outputs/greenlight_poc_v1/C_ours_vocal_only.wav`：处理后的人声层，用于排查吐字、接缝和空间效果。
- `../outputs/greenlight_poc_v1/A_reference_loudness_matched.wav`：仅供本地等响比较的参考片段。

所有 WAV 都是 44.1 kHz、stereo、24-bit PCM。音频受 `.gitignore` 排除。

## 本轮方案

1. 从参考 vocal stem 提取 onset、稳定 F0 run、休止和结构点；保留原始时间，不把检测结果强行吸附到网格。
2. 把相邻 tie / melisma 合成一个音节事件：辅音只出现一次，元音跨多个音符改变 pitch。
3. 用中文 Yushu 单元提供“哈基米南北绿豆”的 attack，用日语 Hajimi 原声提供高带宽 sustain。纯日语和纯中文版本只作内部对照。
4. 对没有静音证据、短于 280 ms 的 pitch-tracker 空洞延续元音；真实乐句休止仍保持静音。
5. 高潮增加 stereo doubles、短 room 和人声前置；伴奏只在 1.8–5.2 kHz 做最多 1.6 dB 动态让位。
6. 母带目标不复制参考的 intersample overs，采用约 `-9 LUFS`、不高于 `-1 dBTP` 的安全版本。

核心脚本是 `render_demo.py`。它是 song-specific POC，不修改通用 `render.py` 的接口。

## 自动 QA

| 项目 | POC V1 | 判定 |
| --- | ---: | --- |
| 稳定音符覆盖 | 77 / 77 | PASS |
| F0 绝对误差 median / P90 | 2.47 / 5.47 cents | PASS |
| octave error | 0 | PASS |
| 音节 onset 命中 | 58 / 58 | PASS |
| onset 误差 median / P90 | 22.40 / 41.44 ms | PASS |
| 合成 vocal 4–8 kHz 占比 | -8.83 dB | 接近参考 -10.17 dB |
| 最终 mix side / mid | -7.21 dB | 接近参考片段 -6.67 dB |
| 最终响度 / LRA / true peak | -9.3 LUFS / 0.9 LU / -1.1 dBTP | PASS |
| A/B 响度差 | 0.0 LU | PASS |
| 116 个拼接边界高于局部 2 ms 一阶差分 P99 | 0 | PASS |
| ffmpeg 严格解码 | 4 / 4 文件 | PASS |
| 人工试听 | 未由自动化执行 | USER REVIEW REQUIRED |

自动指标只证明音高、时序、频宽、空间和母带没有明显工程错误，不能替代“歌词是否听得懂、角色感是否自然”的人工判断。

## 下一步计划

1. **先审 POC V1**：只收集三类反馈——节奏/字头、吐字/角色感、混音前后关系。不要同时泛化到其他歌。
2. **人工修正 arrangement**：听审 20 个自动 octave-fold 事件、58 个音节 token 和乐句 reset；把 auto draft 升为人工批准的 manifest。
3. **补齐角色 voicebank**：至少为关键音素录制 3–5 个 pitch center 和 2 个力度，常态 pitch shift 控制在 ±4 semitones；当前单样本只适合 POC。
4. **表达与混音精修**：按 event 增加 portamento、vibrato delay/depth、高潮 harmony/doubles automation，再做 reference-matched EQ 和压缩。
5. **扩为完整工作流**：POC 通过后先起 ADR-002，定义 arrangement / voicebank / mix preset 接口，再实现通用 renderer 和全曲；随后用未参与调参的一男一女片段验证泛化。

完整 V5 会超过 5 个 tracked files 并引入新接口，因此必须遵守项目约定：先写 ADR 草稿并等待用户确认，不能从本 POC 直接扩张。
