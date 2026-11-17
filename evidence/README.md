# Evidence 结果目录

这个目录只保留比赛最终版本仍在使用的公开、脱敏证据。运行账本、Provider 请求、Prompt、失败 preflight 和中间试验不放在这里。

| 目录 | 用途 |
|---|---|
| `final-projects/` | 住宿、世纪馆和讲座三个最终项目的统一索引，以及最新人物诊断摘要 |
| `four-dimension-v5-formal-v1/` | 住宿床位分配主实验：Natural/D 正式结果、四维评估、14帧匿名完整粒子面板、三张报告图和结果报告 |
| `century-gym-demo-v2/` | 世纪馆 Tick 0–10 演示回放、关系世界和经审阅的合成私聊展示层 |
| `century-gym-live-v1/` | 世纪馆实际 LLM 运行的最终结果、逐 Tick 公开快照和耗时摘要 |
| `lecture-incident-v1/` | 从封存运行恢复的讲座案例：Natural/D、Tick 3–10 公开快照、聚合分析和清单 |
| `joint-constraint-v1/` | 12 个固定 Agent 的 Profile × 上一 Tick 粒子状态四条件配对实验、盲审汇总、留一 Agent 泛化检验和结果说明 |

讲座迁移案例仍可由前端读取 `src/frontend/public/campus-pulse-data/lecture-open-choice-r4.json`；`lecture-incident-v1/` 提供独立、可检查的比赛证据入口。

`century-gym-live-v1/public-agent-trace-FT-0084.json` 串联了一名 Agent 的人物资料、每轮可由公开账本确认的根帖/父消息/引用、本人操作、直接回应和后续行为。原运行未发布 Provider Prompt 正文，因此该文件将上下文范围标为 `provable_context_reconstruction`。

## English

This directory contains only the public, redacted evidence still used by the final competition build. Provider requests, prompts, failure preflights, private ledgers, and superseded experiments are excluded.

- `final-projects/`: canonical index for the three final projects and the latest persona diagnostic summary.
- `four-dimension-v5-formal-v1/`: formal housing Natural/D result, four-dimension evaluation, privacy-safe particle sufficient statistics, and report figures.
- `century-gym-demo-v2/`: Tick 0–10 replay and reviewed synthetic social-world overlay.
- `century-gym-live-v1/`: completed Century Gym LLM result, public Tick snapshots, and timing summary.
- `lecture-incident-v1/`: restored lecture-case evidence with Natural/D public snapshots for Ticks 3–10, aggregate analysis, and a public manifest.
- `joint-constraint-v1/`: four-view paired evidence and leave-one-Agent-out tests showing how stable Profile and the previous-Tick particle state constrain resident LLM behavior under an unchanged forum context.

`metric-provenance-v2.json` maps every numeric result line in the root experiment report to existing statistics code and public evidence. The anonymous particle panel is documented and hash-bound by `four-dimension-v5-formal-v1/anonymous-particle-panel-v1.manifest.json`.

The frontend can still load `src/frontend/public/campus-pulse-data/lecture-open-choice-r4.json`; `lecture-incident-v1/` is the standalone auditable evidence entry point.

`century-gym-live-v1/public-agent-trace-FT-0084.json` links one Agent's profile, ledger-provable root/parent/quote context, action, direct responses and subsequent behaviour. Its scope is `provable_context_reconstruction` because the historical run did not publish Provider prompt bodies.
