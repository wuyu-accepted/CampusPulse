# ForumTwin 引擎数据

这里保存模拟器运行所需的稳定组件，不代表多个前端项目，也不需要用户逐个选择版本。最终三个项目登记在上一级 `project-catalog.json`。

| 文件 | 作用 |
|---|---|
| `forum-ecology-release-v1.json` | 全量论坛语料的聚合生态特征 |
| `forum-ecology-calibration-v2.json` | 成熟线程继续讨论概率和规模先验 |
| `micro-role-release.json` | 120 个数据驱动微角色 |
| `semantic-llm-forum-agent-population-v2.json` | 1,000 个持久合成 Agent 人口 |
| `hero-pilot-bridge-v6.json` | 将正式人口、历史经历与当前 ForumTwin 内核连接起来的运行适配器 |
| `synthetic-relationship-release-v1.public.json` | 合成关系的群体级公开矩阵，不含 Agent 级关系边 |
| `scenario-stake-release-v1.public.json` | 三个场景的利益位置聚合 |
| `forum-v2-input-release-manifest.json` | 人口、关系和利益结构的组合清单 |
| `model-release.json` | 运行时组件绑定 |
| `slot-plan.json` | 旧合同兼容所需的槽位计划 |
| `hero-pilot-bridge-v5.json` | 旧运行恢复所需的兼容适配器 |

内部内容摘要用于恢复、缓存和资产完整性校验，不在产品界面或用户说明中展示。原始帖子、评论、源 ID、全量向量、API Key 和 Provider body 均不在本目录中。
