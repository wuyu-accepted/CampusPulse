# ForumTwin v2 验收矩阵

状态含义：`PASS` 表示代码和离线测试已通过；`COMPLETE` 表示真实 LLM 运行及其公开证据已完成；`READY` 表示工具链已就绪但真实 LLM 证据尚未生成；`DEFERRED` 表示出于成本控制不在当前交付运行；`BLOCKED` 表示必须等待前置门禁。

| ID | 验收项 | 实现 | 测试/证据 | 状态 |
|---|---|---|---|---|
| POP-01 | 1,000 Agent 利益结构带来源标签 | `forum_relationships.py` | `test_forum_relationships.py`、stake release | PASS |
| POP-02 | Profile、episode focus、场景利益分离 | release compiler / turn-v3 renderer | renderer 与 release 测试 | PASS |
| REL-01 | 每 Agent 2/4/2 关系且无孤立 | `build_relationship_release_v1` | 1,000 Agent、8,000 边 release 审计 | PASS |
| REL-02 | close friend 双向，cross-group 跨利益连接 | `forum_relationships.py` | 关系结构单测 | PASS |
| REL-03 | 公共热榜全局一致，关系不改排名 | `forum_twin.py` / `forum_v2_execution.py` | Feed 与 run-plan 合同测试 | PASS |
| PRIV-01 | direct/group 会话、邀请、接受、拒绝、退出 | `forum_twin.py` | 私聊领域测试 | PASS |
| PRIV-02 | 私聊严格成员与时间可见 | `forum_twin.py` / `forum_turns.py` | 非成员、退出后、未来消息拒绝测试 | PASS |
| PRIV-03 | 私聊回流生成新公共 Claim 与私有 lineage | turn-v3 materializer | spillover/claim 测试 | PASS |
| TURN-01 | 每 Tick 1 公开、6 互动、2 私聊 | `forum_turns.py` | v3 validator 测试 | PASS |
| TURN-02 | 公私动作可同时发生 | `materialize_forum_agent_turn_v3` | 组合动作单测 | PASS |
| TURN-03 | 公开/私聊 Prompt 边界和好友语气 | `render_forum_agent_turn_v3` | renderer 合同与隐私扫描 | PASS |
| TURN-04 | Provider 仅看自然语言人物/处境和短别名，不看内部哈希、ID 或原始状态值 | `render_forum_agent_turn_v5` / `hero-pilot-bridge-v6.json` | v5 renderer、alias 回译、隐私扫描与 1,000 Profile 编译检查 | PASS |
| TURN-05 | 每个 Agent 至少 3 条可读的已审阅过往行为经历，不使用原文或源 ID | `compile_reviewed_behavior_portfolio_v1` / v2 bridge builder | 1,000/1,000 Agent 通过；行为经历发布哈希绑定 | PASS |
| ANON-01 | 匿名是同一小喇叭帖子/评论的作者显示选择，不是第二频道 | `render/validate/materialize_forum_agent_turn_v4` | v4 实名/匿名、禁用匿名、public-view 测试 | PASS |
| ANON-02 | 敏感/严重/怕熟人认出时允许匿名，但不改证据与安全边界 | turn-v4 Prompt | Prompt payload 与严格字段测试 | PASS |
| ANON-03 | 治理主体不可看匿名背后 Agent 身份 | `ForumMessageV2.public_view` / visibility risk | 匿名 public projection 与风险隐私测试 | PASS |
| SCHED-01 | 私聊不超过非 Anchor 槽位 40% | `schedule_budgeted_forum_v2_activations` | 调度器单测 | PASS |
| SCHED-02 | 公开高风险/回复/回执优先且私聊顺延 | 同上 | 优先级、重复候选、可复现测试 | PASS |
| ECO-01 | 131,604 线程计数守恒并生成三阶段继续讨论概率 | `forum_ecology_calibration.py` | release `862e832f…b798`、校准单测 | PASS |
| ECO-02 | 先验不改全局排名、不强迫发言、所有候选权重为正 | `forum_hero.py` 打开选择 | 热榜/最新帖不变与确定性选择测试 | PASS |
| ECO-03 | 私有 episode 模式按后月留出，只有 log loss 改善才启用宏观角色部分池化 | calibration builder | 合成留出测试通过；真实私有 episode 文件当前未在本机执行 | READY |
| ECO-04 | 成熟线程与全部线程分开报告并绑定校准前基线 | `forum_realism.py` / analyzer | `evidence/four-dimension-v5-formal-v1/forum-ecology-benchmark-v2.json` | PASS |
| SCHED-03 | 8 个长期锚点以设计权重抽取，非锚点继续进入正概率 PPS | `select_cost_aware_anchors` / scheduler | 12 槽精确预算与 Tick 3–4 根帖/直接回复集成测试 | PASS |
| ECO-05 | 空论坛首轮不允许所有激活主体同时抢占根帖机会 | `_select_event_initiators` / `_turn_inputs` | 4 个可选首发机会、其余 read/ignore 回归 | PASS |
| ECO-06 | 小审计并发进入 preflight 哈希并受 1–24 边界约束 | `build_hero_preflight` / authorized runner | `max_concurrency=4` 预检与校验测试 | PASS |
| MODE-01 | budgeted / keyframe / every-tick 三档计划 | `activation_design_for_tick` | v9 合同测试 | PASS |
| MODE-02 | census π=1 且关闭 HT/GREG | `ActivationDesign` | census 设计单测 | PASS |
| MODE-03 | 1,000 槽先全验证再统一提交 | `FullPopulationTickTransaction` + v3/v4 executor | 顺序不变、失败不提交、v4 匿名物化测试 | PASS |
| RISK-01 | 七类风险独立输出，无总分 | `forum_risk.py` | 七信号与 schema 测试 | PASS |
| RISK-02 | 阈值来自冻结聚合分位数 release | `build_risk_threshold_release_v1` | 至少 20 观测、退化分布拒绝测试 | PASS |
| GOV-01 | 三治理角色不读取私聊原文 | `forum_risk.py` / actor contracts | actor projection 隐私测试 | PASS |
| GOV-02 | QRE、资源、延迟、scheme_bonus=0 | 复用 `forum_actor_execution.py` | 现有治理回归 | PASS |
| DB-01 | schema v13 additive、append-only | `store.py` / `forum_twin_v2_persistence.py` | schema v13 触发器测试 | PASS |
| DB-02 | 旧 lease/generation 写入拒绝 | persistence guards | schema v13 与 runtime 测试 | PASS |
| DB-03 | schema v14 追加 Profile/visibility/author/risk/result 表，旧行不回填 | `forum_visibility_persistence.py` | schema v14、append-only、无第二匿名消息表测试 | PASS |
| BIND-01 | v9 模拟主计划与 v10 可见性侧车哈希绑定 | `forum_visibility.py` / service / store | builder/validator、runtime binding 集成测试 | PASS |
| PROFILE-01 | 项目级 Profile 修改为后端不可变快照，下一运行绑定其 SHA | service/store/dossier API | schema v14 与 API 保存/恢复测试 | PASS |
| CP-01 | Checkpoint v5 保存关系/私聊/风险/frontier | `forum_twin_v2_checkpoint.py` | 往返、哈希和隐私测试 | PASS |
| RES-01 | result-v7 结构、SHA、隐私字段 fail closed | backend/frontend validators | result-v7 合同测试 | PASS |
| UI-01 | 三档模式与精确槽位/Token 预检 | Workbench plan panel | M06 前端合同测试 | PASS |
| UI-02 | 风险、census、消融与私聊聚合结果 | `ForumTwinV2DecisionPanel.vue` | 前端服务与组件测试 | PASS |
| UI-03 | 新增界面中英文完整 | i18n inventory/catalog | `npm run check:i18n` | PASS |
| UI-04 | 固定 1,000-Agent 世界、聚合关系网、逐 Tick 轮换激活、三档模式与运行合同可视化 | `AgentWorldPanel.vue` / `WorkbenchRunMonitor.vue` / aggregate world API | M06 与后端 API 合同测试 | PASS |
| UI-05 | 世纪馆 112 个微角色投影节点逐 Tick 轮换，LLM 发言与公私触达分层，传播边与内容可下钻 | `CenturyGymLivePage.vue` / `social-world-v1.json` / social-world API | M05、浏览器 Tick 4/Tick 7/Tick 10 节点差异、后端哈希与隐私合同测试 | PASS |
| UI-06 | 每个可见 Agent 节点可检查 Profile、Persona、脱敏 Prompt、记忆和历史；真实发言者叠加案例轨迹，背景节点不伪造发言 | `AgentDossierDrawer.vue` / `CaseAgentEvolution.vue` / `AgentWorldPanel.vue` | M05/M06 合同测试、浏览器世纪馆与住宿案例验收 | PASS |
| UI-07 | 新项目可配置关系注意、好友私聊、动态小群、服务工单和同论坛匿名，并进入通用实时世界 | plan/live/workbench components | M06 SFC、view-model、route 合同测试 | PASS |
| UI-08 | 用户从前端配置模型、选择情景和运行深度后一键生成 preflight、启动、轮询、恢复并查看逐 Tick 结果；不手工管理 runner 版本或输出目录 | `forum_live_vignette.py` / `ForumLiveVignettePanel.vue` / API client | 后端未跳过 API/恢复测试 35 项、前端合同 153 项 | PASS |
| REC-01 | unknown 只显示 body-free attempt ID，人工永久弃用后最多补发一次；第二次 unknown 停止 | resolve-unknown API / attempt-bound evidence / runner | unknown 裁决与重复补发拒绝测试 | PASS |
| PRIV-04 | 可只补世纪馆真实私聊/建群，冻结公开时间线且不作因果主张 | private-shadow preflight/runner、turn-v3 shadow renderer | 336 槽 fixture、时序、恢复、公开哈希不变测试 | READY |
| CEN-01 | 200 人分层面板与 603 槽 preflight | census build scripts | census tools tests | PASS |
| CEN-02 | live runner 可恢复且不保存 Prompt/body/key | census runner | dry-run、checkpoint 与 ledger tests | READY |
| CEN-03 | B=24/B=32 MAE、覆盖率、recall 门禁 | census analyzer | 需 603 槽真实结果 | READY |
| ABL-01 | census 未通过时禁止消融 | ablation preflight builder | blocked-preflight test | PASS |
| ABL-02 | 80 Agent × 4 条件 × Tick 3–8 | ablation runner | fixture/resume test | READY |
| ABL-03 | 240 私聊及强制边界样本人工审阅 | ablation review packet | packet/decision validation test | READY |
| ABL-04 | 聚合结果不含文本、ID、关系边 | ablation analyzer | privacy publish test | PASS |
| LIVE-01 | 200-Agent census 实际完成 | 单次授权 live run | 当前交付不运行 | DEFERRED |
| LIVE-02 | 80-Agent 消融实际完成 | census 通过后 live run | 当前交付不运行 | DEFERRED |
| LIVE-03 | 1,000-Agent 单关键帧实际完成 | 独立 preflight | 只保留能力，不进行付费调用 | DEFERRED |
| LIVE-04 | 住宿 Resident turn v4 公域—私域合同真实运行 | 358 槽 live LLM + exact cache | 358/358、504 请求、5,948,114 Token、0 unknown；私聊/建群为 0，不宣称私聊效果 | COMPLETE |
| LIVE-05 | 论坛生态 12 槽质量审计 | OpenLux preflight `998047e1…a734` | 合同完成但论坛生态失败：12/12 根帖；聚合审计 `8c9e6238…c11b` | FAILED |
| LIVE-06 | 修复后的 Natural、Tick 3–4 两时点复审 | 16 槽 preflight `2672ca77…26da` | 16/16、17 请求、83,456 Token、0 unknown；3 根帖、8 严格父消息回复；结构通过但人物内容仍偏同质 | COMPLETE |
| LIVE-07 | v2 情景利益与关系输入下的 Natural/D 小闭环 | 99 槽运行 `forum_persona_a_990927d4a5daebe3a606eae5` | 历史中间诊断；已由 LIVE-08 正式运行覆盖，公开重复证据已移除 | SUPERSEDED_BY_LIVE_08 |
| FID-01 | 论坛生态、微观人格、父 Agent 异质性、网络/治理响应使用同一次分支运行 | `build_four_dimension_fidelity_report` / 根目录 `EXPERIMENT_REPORT.md` | 四维证据和缺失证据 `pending` 单测 | PASS |
| FID-02 | 同一次 366 槽运行生成 24 条相近人物四选一盲评 | `forum_persona_audit.py` / authorized runner | 盲评候选、私有答案、下一时间步状态合并和失配拒绝测试 | PASS |
| FID-03 | 366 槽四维运行的零网络结构闭环 | `run_forum_four_dimension_structural_fixture.py` | 366/366；异质性与治理机制通过，论坛有回复线程占比误差仍需真实 LLM 验证，人格保持 pending | PASS |
| FID-04 | 同一 Agent 的稳定人物/经历跨事件不变，事件只覆盖当前处境和可见帖子 | V5 `resident_context` / `scenario_context` 分层 | 住宿、世纪馆、讲座三场景 Prompt 回归测试；旁观者不继承申请材料或现场证据 | PASS |
| FID-05 | 以小规模配对和表达机会补测检查人物输入 | 根目录 `EXPERIMENT_REPORT.md` | 最新补测 24/24 完成；表达案例四选一为 3/6、处境承接 6/6、事实臆造和模板化为 0；细粒度人物辨识仍需迭代 | PARTIAL_PERSONA_GATE_FAILED |
| FID-06 | 居民模型能看到人物特有的讨论介入方式，以及可并行、非配额的公开互动和私聊/小群机会 | V5 natural renderer / `audit_forum_persona_opportunity_renderer.py` | 24/24 零调用输入具备公开并行动作、私聊动机和至少 4 名封闭联系人；10 种介入方式；Tick 4 的 12/12 输入出现合法小群机会 | PASS_STRUCTURAL |
| LIVE-08 | V5 四维同源 Natural/D 真实 LLM 运行 | 私有 `_run/four-dimension-v1/formal-v5-20260830-continuation/`；公开 `evidence/four-dimension-v5-formal-v1/` | 366/366 槽完成；Natural/D 均到 Tick 10；1,126/1,200 请求；保守 Token 6,389,909/6,500,000；0 unresolved unknown；结果 SHA `f793353b…1999` | COMPLETE |
| FID-07 | V5 正式运行四维证据 | `fidelity-evaluation-v3.json` / `forum-ecology-benchmark-v2.json` / `joint-constraint-v1/` | 论坛生态、连续状态与公开表达异质性、治理机制通过；48 槽配对面板及留一 Agent 检验支持 Profile 与上一 Tick 状态的约束效应跨 Agent 复现；全体人口的精确行为预测仍为混合证据 | EFFECT_GENERALIZATION_PASS_EXACT_PREDICTION_MIXED |
| LIVE-09 | 修复后人物机会集的 24 槽真实 LLM 小审计 | `build_forum_persona_opportunity_live_preflight.py` / `run_forum_persona_opportunity_authorized.py` | preflight `d532c4bf…9a4dc` 仅保留历史溯源；相同 V5 renderer 已由 LIVE-08 的 366 槽正式运行覆盖，旧小审计禁止再次执行 | SUPERSEDED_BY_LIVE_08 |
