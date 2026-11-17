# ForumTwin 当前技术规格

## 固定规模

- 1,000 个 episode 驱动的持久合成 LLM Agent；
- 每 Agent 10 个 SMC 状态粒子，共 10,000；
- 24 时点：baseline 3、burst 5、spread 10、decay 6；
- Natural、A、B、C、D 五分支；当前产品验证使用 3 个配对种子；
- 16 anchors + 32 PPS；
- 单轮最多 64 个居民 LLM + 3 个治理 LLM；
- 系统保留 16,544 槽位压力计划的编排能力，但比赛产品验证不要求消费该上限。

## LLM 与 Dynamics 边界

- 所有公开帖子、回复、引用、纠错、求助、风险上报和治理消息必须来自 authorized live LLM 或 reviewed trace replay。
- Dynamics 只处理阅读、沉默、注意力、Feed、状态衰减、服务队列和粒子，不得生成公开文字。
- LLM Agent 具有 profile、真实 LLM 记忆、结构化状态、可见线程上下文和严格 JSON 输出合同。
- 未激活 Agent 不是“普通模型 Agent”；它仍是可调用的 LLM Agent，只是在预算外进行无语言状态推进。

## 论坛对象

- `ForumThread`：讨论串、board、root、参与人数、reply 数和活动时点。
- `ForumMessage`：发帖/回复/引用/纠错/求助等动作、公开文本、stance、evidence 和 provenance。
- `ForumClaim`：unverified/contested/corrected/verified/expired 状态与纠错关系。
- `AgentMemory`：近期真实 LLM episode、长期摘要和参与线程。
- `FeedExposure`：私有投递关系；公共结果只输出群体聚合。

## 治理主体

- Authority 只看延迟公开讨论、风险上报和 PPS posterior。
- Service operator 只看求助、SLA、容量和积压。
- Community bridge 只看局部讨论、跨群传播和参与缺口。
- QRE 温度 0.20，3 轮阻尼 response，4 tick rollout。
- Natural 不调用治理主体；D 可动态选择完整动作目录。
- 资源满足 `opening = consumed + committed + remaining`，没有 scheme bonus。

## 运行合同

- SQLite additive schema v12；
- run plan `campus-pulse-run-plan-v8`；
- execution mode `llm_forum_twin`；
- aggregate `campus-pulse-live-aggregate-result-v6`；
- domain `campus-pulse-forum-twin-result-v1`；
- visualization `campus-pulse-forum-twin-visualization-v4`。

对外结果必须明确标出验证范围、完成种子、槽位、激活计划和 provenance，且隐私扫描、哈希链、资源守恒和公开内容门禁通过。`three_seed_product_validation` 与 `four_seed_full` 是不同范围，前者不得伪装成后者；二者都不要求消费 16,544 槽位的压力上限。

## 数据与人口发布状态

- 全量索引：582,408 条语义行、12 个 BGE float16 分片；
- 主动审阅：3,000 个线程，2,459 accepted / 541 changed；
- 语义传播：131,604 个冲突自由线程；
- 异质性发布：120 个微角色、22,477 个 profile bundle；
- population-v2：1,000 个 Agent，population SHA-256 `7499da74…`；
- 全系统压力 preflight：16,544 槽位、`ready=true`，preflight SHA-256 `72161f85…`；它证明编排输入完整，不是比赛必须消费的预算。
- 当前住宿五方案产品验证 preflight SHA-256：`6da4776bfffb316ee95147f6b548fb87fc29c6bf3aa72099cfac589d712915f3`。

大体积全量 embedding 与 `episodes.full.json` 属于服务器私有数据资产；本项目目录仅携带脱敏关键 release。`ready=true` 只表示输入和预算完整，不等于 live trace 已完成或 publication eligible。

## 隐私和非主张

- 不公开原始正文、source ID、真实身份、抽样身份、私有 Feed、父粒子映射、记录级边、Prompt/Provider body 或凭据。
- Agent 不是现实学生，合成图不是现实社交网络。
- LLM 发言不是人类意见，结果不是政策因果效果或全校民意。

## 当前已验证主结果（2026-08-15）

比赛主结果是暑期住宿床位分配的 `three_seed_product_validation`：三个配对种子、Natural/A/B/C/D 五分支、Tick 3–15，共 2,028 个居民与治理 LLM 语义槽位。结果由 171 个已提交 checkpoint 只读重建，重建过程 Provider 调用为 0，原 live 账本和模型绑定不变。

- 人口：完整绑定 population-v2，包含 1,000 个合成 LLM Agent、120 个微角色和 22,477 个 Profile bundle；
- 论坛：全局热榜前十、最新帖、直接回复和线程参与共同决定可见上下文；公开文本全部来自授权 LLM 或已结算精确结果；
- 方案：Natural、标准公开 A、服务承接 B、跨群触达 C、自适应多主体 D；没有 `scheme_bonus` 或预设赢家；
- 同预算 A/B/C：平均 Claim 为 32.3/34.0/29.3，治理直接承接链为 4.3/1.0/3.7；
- 同预算 Natural/D：平均居民发言均为 191.0，D 平均形成 2.7 条治理承接链，但 Claim 从 31.0 增至 33.7；
- 状态不确定性：相对 Natural 的 trust、concern、satisfaction 三种子 95% 区间全部跨过 0，不能宣称某方案稳定改善情绪状态；
- 可见机制：证据卡收到对材料格式和截止时间的直接追问；服务回执明确“进入队列不代表已解决”，随后居民继续要求查询入口；
- 自动门禁：槽位/Token 守恒、公开 provenance、lineage、资源、五方案和无硬编码赢家检查通过，最终结果内 unknown 为 0；
- 待完成项：三人盲审仍为 `pending_human_review`，不能用自动检查替代内容审阅。

该版本的主线已被 V5 正式实验取代。当前可复核产物位于 `evidence/four-dimension-v5-formal-v1/`，三个最终项目的入口位于 `evidence/final-projects/index.json`。
