# 相比官方 YuLan-OneSim，我们新增和修改了什么

## 当前运行关系

CampusPulse 是玉兰-万象代码工程上的校园治理扩展，使用自研 ForumTwin 执行器完成当前校园案例。两条运行路径保存在同一工程中：

| 路径 | 入口与调用关系 | 职责 |
|---|---|---|
| OneSim 通用模拟 | `setup.py` 注册 `yulan-onesim-cli` → `src/main.py` → `src/onesim/` | 通用 Agent、环境、事件调度、模型管理与分布式能力 |
| CampusPulse 应用 | `scripts/campus_pulse/run_full_system.py` → `src/backend/campus_pulse_app.py` → `src/backend/campus_pulse/` | 项目配置、运行管理、存储与前端 API |
| ForumTwin 新模型运行 | `src/backend/campus_pulse/forum_live_vignette.py` → `scripts/run_forum_hero_authorized.py` → `src/envs/campus_pulse_governance/forum_hero.py` | 居民决策、论坛与私聊、激活抽样、治理协调、检查点 |
| 保存记录回放 | 前端运行台 → 公开进度服务与已保存时间步 | 按记录展示帖子、互动和治理状态；播放期间新增模型调用为零 |

当前 ForumTwin 内核直接组合自身的居民、论坛、抽样与治理模块。其代码目录与 CampusPulse 后端没有导入 `onesim`；`GeneralAgent`、`BasicSimEnv` 和 OneSim 事件总线由保留的通用入口使用。因此，项目介绍采用“在玉兰-万象工程上扩展，补充校园治理场景；校园实验使用 ForumTwin 专用执行器”的表述。

以下逐文件统计是 2026-08-12 的历史源码快照对照；当前调用关系依据本次发布代码核对。

## 1. 比较基线与口径

比较基准为官方仓库 [`RUC-GSAI/YuLan-OneSim`](https://github.com/RUC-GSAI/YuLan-OneSim/tree/main) 的 `main`：

- 比较日期：2026-08-12
- 方法：固定官方 `main` 与本项目在比较日的源码快照，按路径和文件内容比较；排除 `.git`、`node_modules`、`dist`、`_run`、运行数据库、二进制数据、缓存、历史 evidence 和文档归档。

本文采用**固定快照文件比较**，统计依据是路径与文件内容。“modified”表示路径同名但内容不同；“added”表示官方固定快照没有该路径。逐提交继承关系需要独立的 Git 历史证据。

逐文件完整表见 [UPSTREAM_DIFF_MANIFEST.csv](UPSTREAM_DIFF_MANIFEST.csv)，共 616 行：598 个新增文件、18 个同路径修改文件。表中每行只保留状态、功能类别和路径，可由 Excel 直接打开。

复现命令：

```powershell
python scripts/compare_upstream.py `
  --upstream D:\path\to\official-YuLan-OneSim-main `
  --project . `
  --output docs/UPSTREAM_DIFF_MANIFEST.csv
```

若还需盘点本项目缺少哪些官方最新文件，追加 `--include-absent`；这类“上游漂移”不计入本项目新增/修改功能表。

## 2. 功能级完整对照表

| 编号 | 相对官方新增/修改的功能 | 用户能得到什么 | 主要支持代码 | 文件比较状态 |
|---|---|---|---|---|
| CP-01 | CampusPulse 专用安全 API | 独立启动校园治理产品，不暴露官方通用代码生成、文件系统和模型管理接口 | `src/backend/campus_pulse_app.py`、`src/backend/routers/campus_pulse.py` | 新增 |
| CP-02 | 在线治理工作台 | 创建项目，绑定证据，配置场景/政策，计划、入队、取消、恢复和下载报告 | `src/backend/campus_pulse/service.py`、`store.py`、`runtime*.py`、前端 `workbench/` | 新增 |
| CP-03 | 版本化、append-only 持久化 | v3–v14 合同兼容；幂等键、lease、generation、不可变记录和旧运行读取 | `store.py`、`*_persistence.py`、`runtime_contract.py` | 新增 |
| CP-04 | 小喇叭全量数据谱系 | 原始数据本地清洗、快照哈希、thread 重建、缺口和采集制度标记 | `scripts/campus_pulse/prepare_forum_twin_data.py`、`forum_data.py` | 新增 |
| CP-05 | 本地语义索引 | 全量 BGE 嵌入、float16 分片、断点、完整性和私有索引 | `run_forum_bge_embedding_worker.py`、`forum_embedding.py` | 新增 |
| CP-06 | 主动语义审阅 | 分层抽取稀有/不确定线程，使用脱敏 LLM 语义卡而非发送全量原文 | `build_forum_active_review_payloads.py`、`run_forum_active_review.py`、`apply_forum_active_review.py` | 新增 |
| CP-07 | 全量 episode 标签传播 | 从 3,000 审阅线程扩展到 131,604 个冲突自由 episode，并保留置信度和拒绝 | `label_full_corpus_episodes.py`、`build_forum_twin_episodes.py` | 新增 |
| CP-08 | 数据驱动异质人口 | 120 微角色、22,477 Profile bundles、1,000 合成 LLM Agent，稳定人格与 episode 分离 | `compile_forum_twin_releases.py`、`build_forum_twin_formal_release.py`、`llm_population.py` | 新增 |
| CP-09 | 一等论坛领域模型 | Thread、Message、Claim、引用、纠错、求助、风险上报、TTL、lineage 和记忆 | `forum_twin.py` | 新增 |
| CP-10 | 小喇叭式公共 Feed | 所有人共享热榜前十和最新帖，加直接回复/参与线程通知；不伪造个性化推荐 | `forum_hero.py`、`forum_twin.py`、`forum_execution.py` | 新增 |
| CP-11 | LLM-first 公开内容 | 所有可见帖子/评论/治理消息必须来自 live LLM 或精确 trace/cache；Dynamics 不能代写 | `forum_turns.py`、`forum_expression.py`、`forum_execution.py`、`forum_trace.py` | 新增 |
| CP-12 | 复合论坛动作 | 一次 turn 可同时产生一项发言与点赞、转发、举报等多项互动 | `forum_turns.py`、`forum_execution.py`、前端论坛组件 | 新增 |
| CP-13 | 预算化大规模 LLM 人口 | 1,000 个可调用 Agent 中每 tick 只激活几十个；Anchor、互动队列和 PPS 共存 | `forum_sampling.py`、`forum_hero.py`、`llm_population.py` | 新增 |
| CP-14 | 10,000 状态粒子 | 每个 Agent 的 10 个不确定性假设、父内 ESS、重采样和分支共享随机数 | `governance_arena_v2.py`、`yulan_scale_engine.py` | 新增 |
| CP-15 | PPS 与总体恢复 | 正纳入概率、轮换、GREG/HT、Beta–Dirichlet、Kish ESS 和写回门禁 | `forum_sampling.py`、`semantic_population.py`、`yulan_scale.py` | 新增 |
| CP-16 | 三个治理 LLM 主体 | 治理部门、服务执行者和社区桥接者具有不同私有观测和动作目录 | `forum_governance.py`、`governance_v2_contract.py` | 新增 |
| CP-17 | 受约束动态治理 | QRE、滚动评估、执行延迟、资源守恒、服务工单、证据卡与跨群触达；无预设赢家 bonus | `forum_actor_execution.py`、`governance_arena_v2.py` | 新增 |
| CP-18 | 平行世界实验 | baseline 只运行一次；Natural/A/D 等分支从同一 checkpoint 复制并对比 | `forum_hero.py`、`forum_pilot.py`、`experiment.py` | 新增 |
| CP-19 | 严格机制链审计 | 只有直接回复/引用才算承接；区分“已受理”和“已解决” | `forum_quality.py`、`analyze_forum_parallel_result.py`、`analyze_forum_resource_policy_result.py` | 新增 |
| CP-20 | Provider preflight 和授权 | 调用前冻结 endpoint、模型、字段、槽位、请求/Token 上限；未知结果失败关闭 | `live_authorized_guard.py`、`live_transport.py`、`build_forum_hero_preflight.py` | 新增 |
| CP-21 | 精确 trace/cache replay | prompt/model/renderer 指纹相同才重放；不会把 fixture 冒充 LLM | `forum_trace.py`、`live_asset_catalog.py`、`yulan_scale_live.py` | 新增 |
| CP-22 | 槽位级账本与恢复 | 已结算 Provider turn、消息、治理决策不重复调用或重复积分 | `forum_twin_checkpoint.py`、`forum_twin_round_coordinator.py`、`live_sqlite_repository.py` | 新增 |
| CP-23 | 结果、报告与可视化资产 | result-v6/v7、公共 thread/claim API、粒子帧、JSON/HTML 报告与 hash fail-close | `forum_twin_contract.py`、`forum_twin_v2_contract.py`、`live_result_contract.py`、`live_report_export.py`、API 路由 | 新增 |
| CP-24 | 比赛级 Vue 产品 | 首页、论坛回放、结果中心、项目工作台、系统证据、移动端与中英界面 | `src/frontend/src/campus-pulse/`、`src/frontend/src/views/` | 新增为主，官方同路径入口有修改 |
| CP-25 | 安全本机模型配置 | 前端输入 base URL、模型、API Key 和参数；密钥在用户目录单独保存且永不回显 | `provider_config.py`、`SystemProviderTab.vue`、`campusPulseApi.js` | 新增 |
| CP-26 | 本地案例浏览 | 从完整系统读取已完成案例，资产完整性由前端加载器校验 | `run_full_system.py`、前端资产验证 | 新增 |
| CP-27 | 三个校园治理项目 | 住宿资源正当性争议、世纪馆幽灵预约和讲座事件的 LLM 平行论坛 | `evidence/final-projects/`、`evidence/four-dimension-v5-formal-v1/`、前端 sealed assets | 新增证据（不计代码 manifest） |
| CP-28 | 世纪馆过程演示 | 从工作台创建“空场但不可预约”的项目，按 Tick 0–10 观察完整 Natural/D 演化 | `CenturyGymLivePage.vue`、`build_century_gym_demo_replay.py`、`run_full_system.py`、相关 evidence | 新增 |
| CP-29 | 隐私和发布门禁 | 原文、源 ID、Prompt body、密钥、私有 Feed、记录级图边不进入公开 API/ZIP | `live_asset_catalog.py`、`forum_quality.py`、API 错误脱敏和测试 | 新增 |
| CP-30 | 旧合同兼容与上游集成 | 保留官方 OneSim 核心，同时修改启动、依赖、路由和前端入口接入 CampusPulse | `setup.py`、`Makefile`、`src/main.py`、`src/frontend/*` 同路径文件 | 修改 |
| CP-31 | 另一治理环境 | 谣言螺旋与治理场景扩展，作为 CampusPulse 之外的实验环境 | `src/envs/rumor_spiral_governance/` | 新增 |
| CP-32 | 合成关系与治理利益位置 | 每名 Agent 具有 2/4/2 合成关系和带来源标签的场景利益；公开只输出群体矩阵 | `forum_relationships.py`、`build_forum_twin_v2_relationship_releases.py` | 新增 |
| CP-33 | 好友私聊与动态小群 | direct/group 会话、邀请/接受/拒绝/退出、私有 Claim 与跨渠道回流 | `forum_twin.py`、`forum_turns.py` | 新增 |
| CP-34 | Resident turn v3/v4 | 一次 LLM turn 同时支持 1 个公开动作、6 个独立互动和 2 个私聊动作；v4 进一步支持同一小喇叭帖子/评论的实名或匿名作者显示 | `forum_turns.py`、`forum_v2_execution.py` | 新增 |
| CP-35 | 关系注意与私聊预算 | 关系影响打开/通知而不修改公共排名；私聊激活不超过非 Anchor 槽位 40% | `forum_relationships.py`、`forum_v2_execution.py` | 新增 |
| CP-36 | 七类治理风险雷达 | 分别追踪 Claim、回流、争议、触达、纠错、服务与观测差，并绑定冻结阈值 release | `forum_risk.py`、result-v7 前后端 | 新增 |
| CP-37 | 三档 LLM 人口模式 | budgeted、全量关键帧、全量逐 Tick；1,000 槽输出采用顺序无关两阶段提交 | `forum_v2_execution.py`、`forum_twin_v2_contract.py`、工作台 | 新增 |
| CP-38 | 200 人 census 与关系消融 | 603 槽有限总体校准、1,944 槽四组消融、人工私聊审阅和聚合发布门禁 | `scripts/*forum_twin_v2_census*`、`scripts/*forum_twin_v2_ablation*` | 新增 |
| CP-39 | 项目级可见性与 Profile 版本 | v9 主计划加 v10 侧车冻结 Profile 快照、关系注意、好友私聊、动态群、服务工单和匿名规则；schema v14 保证不可变绑定 | `forum_visibility.py`、`forum_visibility_persistence.py`、工作台 | 新增 |
| CP-40 | 全量可检查 Agent 世界 | 前端物化全部 1,000 个长期 Agent，逐 Tick 显示激活、关系和 10 粒子状态；任意节点可读取并版本化修改项目 Profile | `AgentWorldPanel.vue`、`AgentDossierDrawer.vue`、Agent world/dossier API | 新增 |

## 3. 代码文件数量汇总

以下数量来自逐文件 manifest，不含 evidence、文档、依赖和生成物：

| 类别 | 新增 | 同路径修改 | 主要内容 |
|---|---:|---:|---|
| CampusPulse 模拟内核 | 194 | 0 | ForumTwin、LLM 人口、粒子、抽样、治理、场景与测试 |
| CampusPulse API 与运行时 | 66 | 0 | FastAPI、Service、SQLite、checkpoint、live runtime、本机 Provider 配置 |
| 后端测试 | 67 | 0 | API、恢复、隐私、合同、旧版本兼容 |
| CampusPulse 产品前端 | 180 | 10 | 五个产品入口、组件、数据适配、模型配置、测试和官方前端入口改造 |
| 数据准备与 release 工具 | 40 | 0 | 快照、嵌入、主动审阅、人口和打包 |
| 实验与运维工具 | 19 | 0 | Hero、正式编排、账本检查、分析、授权和上游比较 |
| 治理场景扩展 | 24 | 0 | rumor_spiral_governance |
| 构建与启动 | 7 | 5 | Windows/Unix 启动、Docker、Makefile、依赖和 Git 忽略 |
| 配置与上游集成 | 1 | 3 | 配置及官方入口接合 |
| **合计** | **598** | **18** | **616 个逐文件差异** |

## 4. 18 个同路径修改文件

同路径修改文件及其职责如下：

| 路径 | 修改目的 |
|---|---|
| `.dockerignore`、`.gitignore` | 排除 CampusPulse 私有数据、运行状态和凭据 |
| `Makefile`、`setup.py` | 增加 CampusPulse 运行和依赖支持 |
| `README.md` | 项目入口改为 CampusPulse 总览 |
| `config/config.json` | 接入本项目运行配置（密钥配置仍被忽略） |
| `src/main.py` | 接合 CampusPulse/官方入口 |
| `src/backend/routers/__init__.py` | 注册新增后端路由 |
| `src/frontend/README.md`、`index.html`、`package.json`、`vite.config.js` | 产品构建、依赖和 API 代理 |
| `src/frontend/src/App.vue`、`main.js`、`router/index.js`、`style.css` | 产品 Shell、CampusPulse 路由和设计系统 |
| `src/frontend/src/components/Sidebar.vue`、`ThemeToggle.vue` | 兼容官方界面与 CampusPulse 产品导航 |

## 5. 不应误读的差异

- 官方 `main` 在比较日期包含一些本项目快照没有的后续场景和前端公开资产。它们属于**上游版本漂移**，不是 CampusPulse 主动删除的功能，也不应计入“我们新增了什么”。
- `evidence/` 中的真实 LLM 结果是新增项目证据，但本清单刻意排除它们，避免把数据文件数量包装成代码贡献。
- `_run/`、`dist/`、`node_modules/`、SQLite 和 embedding 均不计入代码差异。
- 文件数量不能代替功能质量；正式主张仍需对应测试、运行哈希和人工审阅。

## 6. 如何更新本表

当官方 `main` 或本项目发生重要变化时：

1. 下载官方新 commit 的干净快照；
2. 修改 `scripts/compare_upstream.py` 中 `UPSTREAM_COMMIT`；
3. 重新生成 `UPSTREAM_DIFF_MANIFEST.csv`；
4. 更新本文的比较日期、数量和功能映射；
5. 人工检查新增/修改文件是否被归入正确功能，不能只粘贴自动统计。
