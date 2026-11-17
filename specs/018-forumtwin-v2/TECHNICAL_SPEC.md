# CampusPulse ForumTwin v2 技术规格

本文冻结 ForumTwin v2 的代码合同。产品议题是“校园稀缺公共资源分配与服务公平”：同一事件在公开论坛、好友私聊和动态小群中传播，治理主体只能依据有限观测采取行动，系统比较 Natural、A、B、C、D 五个平行分支。

## 1. 版本身份

| 对象 | 冻结版本 |
|---|---|
| 执行模式 | `llm_forum_twin_v2` |
| 模拟主计划 | `campus-pulse-run-plan-v9` |
| 社会可见性侧车 | `campus-pulse-run-plan-v10` |
| 居民输出 | `campus-pulse-forum-agent-turn-v4`（兼容 v3 公私交互合同） |
| 合成关系 | `campus-pulse-synthetic-relationship-release-v1` |
| 情景利益 | `campus-pulse-scenario-stake-release-v1` |
| 私聊会话 | `campus-pulse-private-conversation-v1` |
| 私聊消息 | `campus-pulse-private-message-v1` |
| 风险结果 | `campus-pulse-governance-risk-result-v1` |
| Checkpoint | `campus-pulse-forum-checkpoint-v5` + 可见性 `v6` |
| 聚合结果 | `campus-pulse-live-aggregate-result-v7` + 可见性 `v8` |
| SQLite | additive schema v14 |

新项目仍以 v9 作为可执行的模拟主计划，v10 以哈希绑定的侧车形式冻结 Profile 覆盖版本、四通道、关系注意和公开匿名规则。旧 run-plan v8/v9、schema v12/v13、历史运行与公开结果继续按各自原合同读取，不被迁移或回填。

## 2. 人口、利益与关系

- 人口固定为 1,000 个数据驱动的合成 LLM Agent；10,000 个粒子表示这些 Agent 的状态不确定性。
- 稳定 Profile、历史 episode focus 和当前场景利益位置分开保存。
- 情景利益字段包括资源需求、材料完整度、时间紧迫性、服务渠道依赖、信息获取、申诉成本、替代资源、直接影响、使用经历以及桥接/照护责任。
- 每个利益字段必须标记为 `forum_derived`、`scenario_assumption` 或 `runtime_state`。
- 每名 Agent 初始拥有 2 条双向 close-friend 关系、4 条 familiar-peer 关系和 2 条 cross-group 关系；不允许孤立节点。
- Agent 级关系与利益位置属于私有模拟状态。公开结果只含角色群体间聚合矩阵。

当前编译 release 的规模为 1,000 名 Agent、8,000 条有向关系记录和 3,000 条场景利益位置。关系发布和利益发布都绑定人口 SHA-256。

## 3. 公共论坛与关系注意

公开排序只有两份全局一致的内容：热榜前十和最新帖。热度由近期活跃、回复者、点赞、转发与时效组成，举报不增加热度。

关系不会修改公开排名。关系注意只影响以下行为：

1. Agent 是否打开榜单中的帖子；
2. 直接回复、引用、已参与线程和好友转述的通知；
3. 私聊对象与动态群邀请候选；
4. 后续共同参与和纠错承接。

关闭关系注意的消融仍保留同一公共热榜，用于隔离关系注意对传播结构的贡献。

### 3.1 小喇叭公开匿名

匿名不是独立“树洞”频道。Agent 仍在同一个小喇叭版面中发帖、评论、回复和引用，遵守同一热榜、Claim、时序和审核规则；只是公开作者栏可在 `named` 与 `anonymous` 之间选择。

Prompt 会明确告知 Agent：当内容敏感、严重、暴露脆弱处境，或担心熟人凭细节认出时，可合理选择匿名；当持续公开身份、声誉或责任很重要时可选择实名。这个选择必须来自 Profile、当前事件和关系成本，不能让全体 Agent 统一匿名。匿名不改变证据标注，不允许补造事实。治理主体可看到公开文字和聚合指标，看不到匿名背后的 Agent 身份。

## 4. 私聊与动态小群

`ForumLedger` 同时维护公共 Thread/Message/Claim 和私有 Conversation/Message/Claim。私聊严格按成员与时间可见，治理主体不能读取私聊原文。

动态小群规则：

- 邀请对象必须来自关系闭集或共同参与过的公开线程；
- 创建时邀请 2–7 人，至少 3 人接受后激活；
- 邀请在 3 个 Tick 后过期，群成员上限 12；
- 每名 Agent 同时最多加入 3 个活跃小群，每场景最多创建 1 个群；
- 支持邀请、接受、拒绝、退出和创建者解散；
- 已退出成员不能读取退出后的消息。

私聊内容可口语化、情绪化、不完整，也可包含受 Profile 约束的猜测、附和和质疑。猜测必须结构化标记为 `speculation` 或 `hearsay`，不能补造现实身份或未见事实。

私聊回流公开论坛时创建新的公共 Claim，并标记 `private_hearsay_origin=true`。私有来源 lineage 只保存在私有账本；公共结果只输出回流数量、方向和聚合群体来源。

私聊具有显式冷启动合同。公开论坛尚无时间可见内容时不制造私聊动机；一旦已有讨论而私聊账本仍为空，调度器可在非 Anchor 预算内选择少量首聊候选。候选排序只使用稳定 Profile 倾向、当前合成利益、attention/concern、关系信任与固定种子，不指定观点或文字。Prompt 必须向入选者说明联系人是好友、熟人还是跨群联系人、对方的互动方式、是否直接受当前情景影响，以及双方共同可见的公共榜单。LLM 仍可返回空私聊；系统不得用配额、模板文字或 Dynamics 强迫产生对话。

## 5. Resident turn v4

一次居民 turn 最多返回：

- 1 个公开主动作；
- 6 个相互独立的点赞、转发或举报动作；
- 2 个私聊动作。

如果公开主动作产生帖子或评论，v4 还必须返回 `public_identity_mode` 。`ignore/read` 不得伪造一个“匿名作者”。匿名消息物化后保留私有审计映射，公开 API 只返回稳定的合成匿名代号。

公开动作、互动和私聊互不排斥。一个 Agent 可以公开沉默但私下讨论，也可以公开回复后同时点赞、转发或私聊。

Prompt 输入包含 Profile、场景利益、posterior、最多两条真实 LLM 记忆、全局热榜/最新帖、时间可见论坛上下文、关系类型和最多四条时间可见私聊。公开上下文不超过 1,200 个中文字符，私聊上下文不超过 600 字符，总动态上下文不超过 1,800 字符。

Prompt 将频道选择拆成两个独立判断：先决定公开主动作，再依据“告诉好友、求证、吐槽/吃瓜、求助/协调”等 Profile 相关动机决定私聊。`private_actions=[]` 始终合法，但不能因为已经公开发言就自动置空。普通不确定性优先从一对一对话开始；只有出现具体多人协调需求时才建议动态建群，邀请仍需后续 Agent 接受。

Dynamics 只能推进阅读、沉默、注意力、Feed 投递、状态衰减、服务排队和粒子，不能生成公开或私聊文字。

## 6. 激活与三档运行模式

### 6.1 预算化多方案 `budgeted_pps`

- 每 Tick 激活 16–64 名居民；
- 由 Anchor、公开互动队列、私聊互动队列和 PPS 组成；
- 私聊来源最多占非 Anchor 槽位的 40%；
- 公开直接回复、纠错、风险上报和服务回执优先；
- 超额私聊互动顺延到下一 Tick，不由 Dynamics 代写；
- 未抽样居民按缺失观测处理，使用 GREG/HT、部分池化和 Kish ESS 恢复总体。

### 6.2 全量关键帧 `full_population_keyframes`

Run plan 明确冻结 census Tick。census Tick 调用全部 1,000 名 Agent，纳入概率为 1，不执行 HT/GREG；其他 Tick 使用预算化激活。

### 6.3 全量逐时间步 `full_population_every_tick`

每 Tick 调用全部 1,000 名 Agent。系统先冻结全局榜单、私聊收件箱、状态和记忆，再并发取得全部输出；只有所有输出通过验证后，才按 Agent ID 升序统一物化。请求返回顺序不得改变结果哈希。

单场景、一个种子的冻结规模：Tick 0–10 Natural/D 为 19,006 个主槽位；24 Tick Natural/D 为 45,012 个；24 Tick 五分支为 108,048 个。

## 7. 200-Agent census 与关系消融

200-Agent census 使用住宿床位分配场景和种子 20260722，覆盖共享 Tick 4、Natural Tick 9、D Tick 9，共 600 个居民槽位和 3 个治理槽位。完整 200 人输出作为有限总体，用于比较 B=16、24、32、48 的预算化抽样误差。

发布门禁：

- B=24 的 action/stance/topic/private-action 份额 MAE 不高于 0.06；
- B=32 的对应 MAE 不高于 0.05；
- 95% 区间覆盖率在 0.90–0.99；
- public-action scheduler recall 不低于 0.90；
- 非空利益层具有正纳入概率。

只有 census 通过后，才能生成 80-Agent、Tick 3–8 的四组消融 preflight：完整系统、关闭私聊、关闭关系注意、关闭跨群桥接。消融共 1,944 个主槽位，所有条件共享 Agent、事件、初态和随机种子。

## 8. 七类风险信号

系统并列报告以下信号，不生成单一总风险分：

1. unsupported claim velocity；
2. private-to-public spillover；
3. contested claim share；
4. cross-group exposure gap；
5. correction penetration and delay；
6. help backlog and SLA breach；
7. governance observation gap。

正式严重度阈值必须来自共享 baseline 或冻结历史聚合分布的哈希发布；每类至少 20 个聚合观测，并记录 q75/q95。开发默认阈值明确标记为不可发布，不能冒充正式 baseline。

## 9. 多主体治理

三个治理角色保持独立有限观测：Authority 看延迟公开风险与隐私保护的回流指标，Service 看主动提交的工单与 SLA，Bridge 看群体级触达缺口与纠错穿透。三者都不能读取私聊正文、成员身份、Agent 关系边或隐藏总体真值。

决策继续使用动作可行性过滤、100 单位资源账本、执行延迟、QRE 温度 0.20、三轮阻尼响应和 4 Tick rollout。Natural 不调用治理角色，所有方案 `scheme_bonus=0`。结果必须同时发布偏好差异、选择概率、资源冲突和未选动作。

## 10. v14 存储与恢复

schema v13 只新增 append-only 表：关系与利益 release、私聊会话、成员事件、私聊消息批、私聊 Claim、关系更新、风险信号、census 轮次/checkpoint 和经审阅私聊摘录。

schema v14 在 v13 上只追加项目级 Profile 覆盖 release、v10 可见性运行绑定、实名/匿名计数批、五类跨通道风险批和 result-v8 投影。Profile 修改不直接覆盖人口母版：每次保存都产生不可变完整快照，下一个运行通过 v10 哈希绑定该版本。

所有记录具有 tenant/project/run 外键、内容 SHA-256、幂等键、update/delete 拒绝触发器和当前 lease/generation 校验。Checkpoint v5 保存关系状态、群成员、邀请、私聊收件箱、积压、风险信号和 census slot frontier。

恢复必须满足：已提交私聊、邀请和关系更新不重复积分；unknown 状态失败关闭；旧 lease 不能写 v13；同 Tick 返回顺序不影响最终哈希。

## 11. result-v7/v8 与公开边界

result-v7 只公开：公共论坛聚合、私聊聚合、七类风险、治理决策、census 精度、关系消融指标和经人工审阅的匿名摘录。它不得包含私聊原文、Agent ID、conversation ID、记录级关系、私有 Prompt、Provider body、密钥或原始论坛字段。

result-v8 是可见性侧车的聚合投影：只允许各通道计数、实名/匿名比例、匿名 Claim 增速、私聊回流、显性—隐性意见差、纠错穿透和服务转化缺口。匿名作者映射仅以本机加密私有工件存储，不进入普通报告、前端或 ZIP。

`emulator_only_development` 运行可以验证工作量、恢复和合同，但 `publication_eligible=false` 且公开语言数必须为 0。正式结果只能来自 exact trace replay 或单次授权的 live LLM，并完成全部槽位与审阅门禁。

## 12. 一键运行控制面

工作台不要求用户选择 `run_*_vN.py`、手工创建账本或拼接输出目录。前端只提交情景、运行深度和哈希确认；后端依次完成：

1. 从本机 Provider 配置读取 endpoint、模型和密钥状态，密钥不返回浏览器；
2. 生成与人口、Profile、关系、利益、runner 和公共进度写入器哈希绑定的不可变 preflight；
3. 创建 session、authorization、ledger、checkpoint 和 public-progress 目录；
4. 启动相应版本执行器并按 1.6 秒轮询公开状态；
5. 每个完整 Tick 先提交 checkpoint，再原子发布带哈希快照；
6. 浏览器或后端重启后通过同一 session 恢复，已结算槽位不重发；
7. unknown outcome 只暴露 body-free attempt ID。操作员可永久弃用迟到结果并补发一次，替代请求再次 unknown 时永久停止。

当前公开产品以 366 槽 Resident turn v5 Natural/D 正式运行为住宿主证据，可复核结果位于 `evidence/four-dimension-v5-formal-v1/`。条件私聊与动态小群使用单独的安全聚合 sealed asset，不改写主实验公开时间线；版本化 runner 仍作为内部审计边界保留，但不构成用户操作步骤。
