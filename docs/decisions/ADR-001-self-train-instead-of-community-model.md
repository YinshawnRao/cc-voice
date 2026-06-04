# ADR-001 · Phase 1 改为自训路线，不再依赖社区张韶涵模型

**日期**: 2026-05-28
**状态**: 已采纳
**决策者**: 用户 + Claude

---

## 背景

CLAUDE.md §1.1 原 Phase 1 设计：
> 用社区现成的预训练模型，跑通"素材→分离→推理→混音"全链路。

Phase 2/3 才涉及自训。

实际操作中发现：

1. **HuggingFace** 公开搜索 0 个张韶涵 RVC 模型（推测因华语流行音乐版权清理）
2. **voice-models.com / weights.gg**: 站内搜索靠 JS 动态加载，WebFetch / WebSearch 看不到结果；且 weights.gg Discord 2026-04 起半关闭
3. **AI Hub Discord**: 登录后浏览器可访问，自动化爬取不可行
4. **B 站 / CSDN / Notion 聚合页**: 部分页面（如 `pitch-family-06a.notion.site/AI-...`）有 300+ 模型清单，但 WebFetch 在 Notion 上被反爬阻断
5. **Quark 网盘合集** `pan.quark.cn/s/7517d82ead1a` (260 模型)、网盘类链接需浏览器手动取

结论：自动化检索拿不到张韶涵 RVC 模型；用户也不愿手动浏览这些聚合页（因为最终也是要自训的，多走一步无意义）。

## 决策

**Phase 1 ~ Phase 3 合并为单条自训主线**：

- Phase 1（轻量）：采集 15-20 min 干声 → 切片 → RVC v2 快训（100-200 epochs，~1-3h MPS）→ 推理 demo → 验证流程通
- Phase 2（精训）：扩充到 40-60 min → 500+ epochs → 完整评测协议
- Phase 3（成品）：用 Phase 2 模型 + UVR5 精修 → 最终成品

横向框架对比（CLAUDE.md §1.1 Phase 2 原内容）**降级为可选**：除非 Phase 1/2 RVC 效果不理想，否则不一定要测 So-VITS-SVC / DDSP-SVC。

## 影响

**新增工作**：
- 训练素材采集变为 critical path（playlist.txt Section A 必填）
- 训练硬件可行性：M4 Pro 48GB MPS，RVC v2 训练 200 epochs 估算 1-3h、500 epochs 估算 4-8h

**省去工作**：
- 不再花时间搜索 / 验证社区张韶涵模型
- Phase 2 横向框架对比可砍

**风险**：
- 数据采集质量直接决定模型上限。15-20 min 极简数据集可能产出"勉强能识别"水平，达不到 B 站爆款标准
- 训练失败的可能性（MPS 兼容性、超参数不当）需要 fallback：CLAUDE.md §1.3 已规定 MPS 死磕 < 1h 切方案

## 验证后退路

如果 Phase 1 自训完全失败，仍可降级回原方案：
- 用户手动从 voice-models.com / AI Hub Discord 拿一个张韶涵或其他模型，跳过训练直接推理
