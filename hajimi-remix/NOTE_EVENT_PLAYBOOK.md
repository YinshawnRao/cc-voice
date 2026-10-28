# Hajimi Note/Event 制作手册

这份手册沉淀自《绿光》对标实验。2026-08-12，用户试听 POC V1 后确认“效果还不错”，因此它成为后续歌曲的体验基线；这不等于已经达到参考 UP 主的最终音色上限。

本手册是 `hajimi-note-event-v1` 与 `hajimi-note-event-v1.1` 的音频权威说明；跨音频 / MV 的输入契约、状态机、资源预算和最终打包门禁见 [`HAJIMI_PRODUCTION_STANDARD.md`](HAJIMI_PRODUCTION_STANDARD.md)。两版都只声明支持 20–75 秒完整乐段。

## 生产状态

```text
SOURCE_READY
→ WINDOW_APPROVED
→ AUTO_DRAFTED
→ EVENT_APPROVED
→ RENDERED
→ AUTO_QA_PASS
→ HUMAN_APPROVED
→ PACKAGED
```

auto draft 不得直接成为正式 package。人工校正另存 `event-approved.json`，保留 draft hash、改动项、审核时间和结论；`timed_lyrics.json` 只从 approved syllable groups 生成。当前《雨爱》仍保留 `AUTO_DRAFT_REQUIRES_HUMAN_LISTENING` 机器状态和用户已试听认可的会话事实，两者不能相互冒充。

## 已验证的结论

固定 3.1 秒整句循环适合快速 audition，不适合成品。它会把与歌曲无关的周期、字头和停顿强行写进每首歌。成品主线应是：

```text
同轴 guide vocal + instrumental
        ↓
note / onset / phrase event map
        ↓
辅音 attack + 元音 sustain + 句尾 release
        ↓
逐 event PSOLA + tie / melisma + 真休止
        ↓
section gain / doubles / room / ducking
        ↓
安全母带 + 自动 QA + 人工试听
```

### 1. 时间组织优先于继续卷 F0

- 字头固定在 syllable onset；辅音只出现一次。
- 同一音节跨多个音符时只改变元音 pitch，不重复辅音。
- pitch tracker 的短失稳区不是天然休止。只有没有低能量休止证据且 gap `<=280 ms` 时才延续元音。
- 明确的乐句休止保持静音；乐句入口可以重置“哈基米南北绿豆”的 token cycle。
- 自动 note map 是草图，必须保留 raw F0、octave correction reason 和低置信事件，不能冒充人工标注。

### 2. 声源按职责拆分

- 中文 Yushu 样本负责可识别的“哈基米南北绿豆” attack。
- 日语 Hajimi 原声负责高带宽、角色感更强的 sustain。
- 不要试图用 EQ 补回源样本根本不存在的辅音、气息和 4–8 kHz 信息。
- attack 尽量保持 one-shot；只延长稳定元音。V1 的固定约 `18ms` OLA 只保留作冻结回归；V1.1 使用基频同步的 `8P` microcycle + `2P` overlap，周期低置信直接失败。
- V1.1 candidate 必须经过独立 pyworld F0 检查。极少数发生次谐波的 group 可以显式回到冻结 V1 group，并记录 ID、原因和比例；比例超过 `5%` 或回退后仍有 octave error 时整首失败。
- 单一 pitch center 仍是当前上限。后续正式 voicebank 应至少覆盖 3–5 个 pitch center 和 2 个力度，使常态移调控制在约 `±4 semitones`。

### 3. Mix 是编排的一部分

- synthetic vocal 先做 HPF、轻压缩，再进入空间处理。
- 保留中置主声，低电平 doubles 使用不同的微移调与 4–9 ms 左右延迟。
- verse 较窄；chorus / climax 加宽并提高人声前景。
- 伴奏只在 vocal 活跃时对 1.8–5.2 kHz 动态让位约 1–2 dB，避免全频 pumping。
- 参考成品很响不代表要复制 clipping。默认成品目标约 `-10～-9 LUFS`、`<=-1 dBTP`。
- 有参考作品时必须做等响 A/B，避免“更响所以更好”的偏差。

## 新歌制作顺序

1. 确认 vocal / instrumental 来自同一版本、同一采样率、严格等时长。
2. 选完整乐句或完整副歌；不要从半个字或未落稳的和弦进入。
3. 扫描 vocal activity、F0 范围、onset 密度、长休止和 section boundary。
4. 生成 auto event draft；人工优先复核 octave fold、超短音符、连音、句尾和 reset。
5. 内部至少比较 hybrid 与单一声源；用统一 premaster 和响度判断。
6. 最后才做 doubles、room、ducking 和 master。
7. 同时交付成品、vocal-only、QA 报告；有直接目标时再交等响 A/B。

## 自动验收门槛

| 项目 | 门槛 |
| --- | ---: |
| event 顺序、窗口边界、manifest schema | 100% PASS |
| 稳定音符覆盖 | 100% |
| F0 absolute error median / P90 | `<=10 / 25 cents` |
| octave error | `0` |
| syllable onset error median / P90 | `<=35 / 70 ms` |
| event 拼接边界 | 不高于局部 2 ms 一阶差分 P99 |
| 最终 true peak | `<=-1 dBTP` |
| 严格解码 | 所有交付文件 PASS |
| V1.1 bubble median / P90 | 每首相对 V1 降低 `>=35%`，且 `<=0.50 / 0.85` |
| V1.1 independent F0 median / P90 | `<=20 / 50 cents`；event octave `0`；frame octave `<=1%` |

F0、onset、频宽、响度和解码通过只说明工程链路没有明显错误。歌词可懂度、角色感、机械感和人声是否“坐进伴奏”仍以人工试听为最终体验门。

onset 100ms 内命中率 `100%` 才自动 PASS；`>=98%` 标 WARN 并要求人工 disposition，低于 `98%` FAIL。PSOLA 或 pitch render 失败必须 fail closed，禁止用未经调音的 source 静默占位。

## 资源硬门

- 先裁后算，Python 入口拒绝超过 75 秒输入。
- extract、render、QA、video render 严格串行。
- OMP / OpenBLAS / Accelerate / NumExpr / MKL 线程全部固定为 2；FFmpeg 固定 2 threads。
- event draft 不启动 Demucs、Whisper 或 torchcrepe；分离属于独立上游。
- production 默认只渲染 hybrid 一个 variant，并记录 wall time、peak RSS、swap 与 hash。

M4 Pro 回归门：extract `<=10s / 1GB`；V1.1 最终 renderer `<=10s / <1.6GB` 且不超过同曲 V1 的 `1.5× wall / 1.25× RSS`；QA 与必要 repair 均分阶段串行，swapout 必须为 0。RSS 超限、产生 swapout 或发现并行重音频进程时直接中止。

## 已知失败模式

- **固定 phrase 循环**：产生与歌曲无关的周期指纹和错拍字头。
- **按 activity gate 重启整句**：每个短停顿都回到“哈”，导致碎切。
- **所有低 F0 一律升八度**：会把女声低音区真实旋律误判成 octave error。
- **只用中文 TTS sustain**：高频和角色纹理不足，塑料感不能靠 limiter 修好。
- **填平所有 pitch gap**：会消灭真实休止；必须结合 gap energy。
- **先追响度再比音色**：会掩盖 timing、timbre 和 mix 的真实差距。
- **自动 QA 代替试听**：音高正确不等于吐字自然或具有角色感。
- **固定非基频 OLA 气泡音**：短 sustain 的慢速周期包络会被大幅降调放大；先改为 phase-locked microcycle，不要先堆 EQ、room 或 limiter。
- **microcycle 次谐波**：独立 F0 若发现 event median octave error，只允许显式、限量、可审计的 frozen-V1 group safety；禁止在报告里隐藏回退。

## V1.1 三首回归结论

2026-08-20 的机器回归中，《雨爱》《百年孤寂》《突然想起你》的 microcycle-applied bubble median 分别降低 `77.0% / 74.6% / 61.5%`，合并降低 `71.4%`。最终 F0 P90 为 `11.1–11.5 cents`，event octave error 均为 0；《突然想起你》显式记录了 2/95 个 F0 safety groups。该结果只证明算法与工程门禁，三首完整听感仍等待用户确认。

《绿光》的具体实现和量化结果见 [`greenlight_poc/README.md`](greenlight_poc/README.md)。
