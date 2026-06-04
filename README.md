# cc-voice · AI Singing Voice Cloning 调研

端到端的歌声音色克隆（SVC）调研工程，目标音色：张韶涵。

详细方法论、目录约定、协作规范见 [`CLAUDE.md`](./CLAUDE.md)。

## 当前阶段

**Phase 1 · 技术摸底**：用社区现成的预训练 RVC 模型跑通"素材→分离→推理→混音"全链路，产出第一个可听 demo。不追求音色像，只追求流程通。

## 仓库布局速览

| 目录 | 用途 |
|---|---|
| `raw/` | yt-dlp 下载的原始音频（不入 git） |
| `dataset/` | 分离 + 切片后的训练集 |
| `models/` | pretrained / community / trained 权重 |
| `frameworks/` | 各 SVC 框架本地 clone |
| `scripts/` | 01~07 流水线脚本 |
| `outputs/` | phase1/2/3 阶段产物 |
| `docs/` | 阶段报告、踩坑笔记、ADR |

## 法律边界

仅供个人技术调研，**禁止公开发布、商用**。详见 `CLAUDE.md` §6。
