# ADR-004 · Hajimi Note/Event V1.1 去气泡 sustain

**日期**：2026-08-16  
**状态**：已采纳  
**决策者**：用户 + Codex

**确认记录**：2026-08-20，用户明确回复“开始执行”，确认按本 ADR 实施 V1.1、三首串行回归与人工试听门。

## 背景

`hajimi-note-event-v1` 已把固定 3.1 秒 phrase loop 替换为逐 note / syllable event，但它在单个音节内部仍先用固定 `18 ms` overlap 反复拼接短 sustain，再对整组执行 Praat PSOLA。短 JP sustain 因而形成约 `8.5–24 Hz` 的慢速周期包络；当歌曲目标音高比声源低很多时，PSOLA 会进一步放大这种咕噜、creaky 或“气泡音”。

现有三首的目标音高相对 JP sustain 的中位移调约为：

| 曲目 | 中位移调 | 绝对移调超过 7 semitones 的 groups |
| --- | ---: | ---: |
| 《雨爱》 | -4.8 st | 20 / 97 |
| 《百年孤寂》 | -7.6 st | 53 / 97 |
| 《突然想起你》 | -6.8 st | 41 / 95 |

《百年孤寂》单变量 POC 保持 event、pitch、attack、PSOLA、mix 和 master 不变，仅把 sustain 改为 phase-locked microcycle：从稳定样本中心取 8 个基频周期，以 2 个基频周期 equal-power overlap 循环。机器结果如下：

| 指标 | V1 | microcycle B |
| --- | ---: | ---: |
| 6–30 Hz / 1–45 Hz envelope modulation median | 0.851 | 0.316 |
| 同指标 P90 | 0.982 | 0.725 |
| envelope CV median | 0.243 | 0.114 |
| 4–8 kHz presence | -9.59 dB | -9.19 dB |
| integrated loudness | -9.99 LUFS | -9.99 LUFS |

2026-08-16 用户试听后确认：**“B确实有减少一些，我觉得这个更符合标准”**。这确认了算法方向，可以进入正式版本设计；它不等于本 ADR 已确认，也不等于三首完整成品都已完成人工验收。

## 决策

### 1. 新增正式 audio preset

新增 `hajimi-note-event-v1.1`，继承 V1 的事件、音高、音源与混音合同，只替换“目标 sustain 长于源 sustain”时的延长算法：

1. 在 `120–700 Hz` 范围内计算 normalized autocorrelation；整数 lag 范围固定为 `ceil(sr/700)` 到 `min(floor(sr/120), floor(N/3))`。
2. 在全局最佳分数 `98%` 范围内选择最短 lag，避免误取倍频周期；同时记录 `best_score` 和实际采用的 `selected_score`。
3. production PASS 要求 `selected_score >= 0.80`、源 sustain 前后半段的周期差 `<=5%`，且源至少容纳 10 个估计周期。`0.65–0.80` 或周期差 `5–10%` 只允许生成标为 preview 的诊断结果；低于该范围直接失败。
4. 从稳定源中心选取 8 个周期作为 microcycle，每次使用 2 个周期 equal-power overlap，净推进 6 个周期；不得继续使用固定 `18 ms` overlap，也不得缩成 4–7 个周期。
5. 当目标 sustain 不长于源 sustain 时，继续使用 V1 的中心 crop，不做循环。
6. lag 范围无效、NaN、静音、overlap 少于 4 samples、源少于 `10P` 或输出长度不能精确满足目标时必须 fail closed；周期检测失败不得回退到旧 OLA。
7. 独立 whole-dry F0 检查若发现 microcycle group 出现 event-median octave error，允许显式 `F0_SAFETY_V1_GROUP_FALLBACK`：必须记录 group ids、candidate/final F0 和原因，比例 `<=5%`；超限、发生在非 microcycle group 或回退后仍失败时整首 fail closed。这是音准安全阀，不是低置信 period fallback。

每个 group 的 report 必须记录：`method`、`estimated_f0_hz`、`period_samples`、`best_score`、`selected_score`、`confidence_class`、`split_half_period_delta_percent`、`source_period_count`、`cycle_count`、`overlap_samples`、`loop_count`、`target_samples` 和 `fallback_reason`。汇总层记录 `standard_microcycle_groups`、`cropped_groups` 和 `failures`。

### 2. V1 保持冻结并可复现

- `hajimi-note-event-v1` 继续保留为旧产物的 regression preset，不原地改参数或含义。
- 现有 recipe 及其 hash 保持 V1；新增独立 `*-v1p1.json` 用于回归。三首机器回归和完整人工试听通过后，才把“新建 recipe”的默认值切到 V1.1。
- V1.1 run id 使用 `{date}_{song_id}_hev1p1_rNN`，不得覆盖任何 `_hev1_` 产物。
- articulation `v1.1-poc1` 已被试听证明“差异不明显”，只保留实验记录，不进入生产默认。

### 3. 共享接口显式升版

editable recipe 与 immutable materialized manifest 分开验证：新增 `song-recipe.schema.json` 覆盖实际 recipe 形状；`song-manifest.schema.json` 只验证物化后的 run 真值。Audio CLI、MV runtime、package 和 QA 必须同时认识 V1 / V1.1，不允许靠 renderer 隐藏常量切换：

- recipe / manifest 的 `event_preset` 是唯一选择源；
- preset 与 run tag 强制交叉校验：V1=`hev1`，V1.1=`hev1p1`；production render/package 要求显式 `--run-id`，不再依赖硬编码日期或“最新目录” glob；
- Audio CLI 根据 preset 加载对应 JSON，并把 sustain profile 显式传给 renderer；
- render report、artifact manifest 和 QA 写入 preset id、preset SHA、renderer SHA 与 run id；
- `package` 继续要求机器 QA 与人工试听分别通过，不能沿用 V1 的人工状态。

MV 时间轴和字幕合同不变。为规模化减少重复渲染，新增显式 `VIDEO_STREAM_REUSED_TIMELINE_IDENTICAL` 证据通道，而不是改写旧 `render-evidence.json`：

- 旧 audio run、MV project、raw、final 与 QA 全部冻结；新结果进入独立 revision 目录。
- 复用前要求新旧 master 的 sample rate、channels、PCM format 和 sample count 完全一致；group projection、sections、字幕、duration/fps 与视觉依赖签名逐字段一致。
- `video-reuse-evidence.json` 记录旧 raw/render/final SHA、新旧 timeline signature、visual signature、renderer SHA 与新 master SHA；新 final 的 H.264 packet hash 必须与旧视频流一致。
- reuse lane 重新执行 mux 与完整 final-media QA。任一签名/hash 不一致，或 renderer 真正消费 audio-reactive 数据时，自动回退完整 `build → check → snapshot → render → mux → qa`，不允许人工强行复用。

## 验收门槛

### 自动门禁

- event、group、token、note ids、attack / stable / render 时间戳与同 recipe V1 完全一致；最终 frame count 相同。
- 现有 decode、format/timeline、loudness 与 event coverage 门禁继续通过；不能把当前不存在的 onset/rest/splice 检查写成既有能力。
- V1.1 新增独立 pyworld Harvest + StoneMask F0 QA：5 ms、70–900 Hz、event 稳定区两端 trim 10 ms；event coverage 100%，median absolute error `<=20 cents`、P90 `<=50 cents`、event-median octave error=0、frame octave-error rate `<=1%`，且 P90 不得比同曲 V1 恶化超过 10 cents。
- bubble proxy 固定在同时间轴 dry mono 上计算：每组 `stable_start+25 ms` 到 `render_end-20 ms`，裁后至少 140 ms；Hilbert envelope；`resample_poly(..., 200, sr)`；mean envelope `>=1e-5`；Welch `nperseg=min(128,len)`；统计 `PSD[6,30] / PSD[1,45]`，并报告 eligible/skipped groups 及原因。Production gate 只统计真正应用 microcycle 的 groups；crop 与 F0-safety groups 继续作为 all-long-groups advisory 报告。
- 对《雨爱》《百年孤寂》《突然想起你》三首回归：每首 median 相对 V1 降低 `>=35%` 且绝对值 `<=0.50`，P90 `<=0.85`；三首合并 median 降低 `>=40%`。Envelope CV 每首 median `<=0.20`、P90 `<=0.30`；CV `<0.06` 只记“可能过直” WARN，交由人工判断。
- 频谱不能靠变暗掩盖气泡：在等时间轴 dry 上测 spectral centroid 变化 `<=10%`、4–8 kHz presence 变化 `<=1.5 dB`。
- microcycle seam 统计固定为 `abs(x[t]-x[t-1]) / max(P99(abs(diff(local ±20 ms，排除 seam ±1 sample))), 1e-6)`；每个 seam ratio `<=1.5`。任何可听 click 仍由人工门禁直接判失败，机器统计与试听结果分别记录。
- master 维持 `-10～-9 LUFS`、`<=-1 dBTP`；同曲等响 A/B 差 `<=0.1 LU`。
- 单曲串行；numerical / FFmpeg threads 均为 2；最终 renderer wall `<=1.5×V1` 且绝对 `<=10s`，peak RSS `<=1.25×V1` 且绝对 `<1.6GB`；独立 F0 preflight / 首次 safety discovery / repair 分阶段串行记录，swapout 必须为 0，swapin 非零只记 WARN。回归 recipe 固化已验证的 safety group ids，后续同 recipe 只跑一次最终 renderer。

### 人工门禁

- 已确认：《百年孤寂》短窗 B 的气泡感减少，方向符合标准。
- 待确认：三首完整 vocal-only 与 mix；V1.1 必须减少气泡，且不能引入明显的蜂鸣、过度发直、金属音、节奏或咬字退化。
- 《百年孤寂》《突然想起你》的新 MV 只能标为 internal preview；用户试听 / 观看确认后才能成为 authoritative final。

## 实施范围（确认后执行）

预计修改 15 个 tracked files，已触发 `AGENTS.md` 的 ADR 门槛：

```text
docs/decisions/ADR-004-hajimi-note-event-v1-1-debubble.md
hajimi-remix/presets/hajimi-note-event-v1.1.json
hajimi-remix/greenlight_poc/render_demo.py
hajimi-remix/scripts/hajimi_audio.py
hajimi-remix/schemas/song-manifest.schema.json
hajimi-remix/schemas/song-recipe.schema.json
hajimi-remix/schemas/video-reuse-evidence.schema.json
hajimi-remix/tests/test_production_contract.py
hajimi-remix/HAJIMI_PRODUCTION_STANDARD.md
hajimi-remix/NOTE_EVENT_PLAYBOOK.md
hajimi-remix/mv/template-v1/scripts/hajimi_mv_runtime.py
hajimi-remix/scripts/qa_package.py
hajimi-remix/recipes/yuai-v1p1.json
hajimi-remix/recipes/bainianguji-v1p1.json
hajimi-remix/recipes/turanxiangqini-v1p1.json
```

音频、A/B、render report、QA 和 post-mux MP4 仍属于 ignored/generated artifacts，不计入 tracked 源码，但必须生成新目录和完整 hash 证据。

## 迁移计划

1. 实现并冻结 V1.1 preset、renderer profile、recipe/manifest schemas、CLI/package/tests；证明显式 V1 仍能复现旧 dry hash。
2. 串行回归《雨爱》《百年孤寂》《突然想起你》，一次只运行一首，先 dry / vocal-only，再 master。
3. 通过自动门禁后，为《百年孤寂》《突然想起你》生成等响 V1/V1.1 A/B 并停在人工试听状态。
4. 用户确认两首音频后，优先走有独立 evidence 的 video-stream reuse lane 写入新 revision 目录；签名不一致则完整重渲染。严格解码、AV tail、黑场、静音、响度、AAC-master correlation、H.264 packet identity 和抽帧 QA 全部重算。
5. 用户确认新 MV 后，才把 V1.1 设为后续新 recipe 的默认 audio preset。

## 未选择的方案

- **一次性全局 resample sustain**：诊断时 modulation 降幅明显，但会改变 formant / 频谱，容易变糊，不作为正式算法。
- **WORLD 全组重合成**：部分短组无法稳定分析，需要回退，且会引入明显 vocoder 质感。
- **整段 octave-up**：能减少向下移调，但改变编曲音区，不是透明修复。
- **只做 phase-aligned 整段循环**：仍会周期性重复整个源包络，慢速调制改善不足。
- **继续调 EQ、doubles、room 或 limiter**：干声已经存在气泡，后级不是首要根因。
- **直接更换 voicebank**：仍是后续必要方向，尤其用于解决大幅降调和哈/基/绿的源声上限；本 ADR 先解决已验证的循环伪影，不把多 pitch-center voicebank 混入同一变量。

## 影响与边界

- V1.1 解决的是 sustain 慢速循环伪影，不承诺解决单一高音声源被下移 `7–15 semitones` 的全部 creaky / formant 问题。
- 新算法可能更稳定但更“直”；因此 modulation 下降不能替代人工试听。
- 不改变张韶涵 RVC 三阶段主线，不引入 SVC 模型，不下载大模型。
- 不扩大 V1 的 20–75 秒单乐段、单任务串行和内部验证边界。

## 实施记录

2026-08-20 已完成接口与三首机器回归：V1 dry 基线仍逐字节复现；《雨爱》《百年孤寂》《突然想起你》的 microcycle-applied bubble median 分别降低 `77.0% / 74.6% / 61.5%`，三首合并降低 `71.4%`。最终 F0 P90 为 `11.1–11.5 cents`、event octave error 均为 0；《突然想起你》使用 2/95 个显式 F0 safety groups。

2026-08-21 用户试听两首等响 A/B 后确认“B的感觉更好一些”，《百年孤寂》《突然想起你》推进为 `AUDIO_HUMAN_APPROVED`。随后两支 MV 在新 revision 目录通过 `VIDEO_STREAM_REUSED_TIMELINE_IDENTICAL`、post-mux 与 Final MV QA：AAC/master lag 均 0，相关系数分别 `0.999964 / 0.999947`，黑场、意外静音、swapout 均为 0。当前停在 MV 人工观看门；《雨爱》单独人工试听和 V1.1 默认切换仍 PENDING。
