# CampusPulse ForumTwin

校园社会治理模拟与推演 · Campus governance simulation

[中文](#中文) · [English](#english)

<a id="中文"></a>

## 中文

CampusPulse 在一个由 1,000 个持久合成 Agent 组成的校园论坛世界中，模拟事件传播、群体反应与治理回应。用户可以创建项目、配置事件和治理方案、设置模型与预算，并逐时间步观察公开讨论、好友私聊、小群传播和服务处理。

系统服务于校园资源分配与公共服务的方案预演：哪些群体关心什么、争议如何传播、治理回应被谁看到和采用、哪些问题仍需要处理。

![CampusPulse 架构：论坛数据、Agent 人口、公开与私下互动、治理及评估](docs/images/campus-pulse-architecture.png)

### 主要功能

- **固定的 Agent 世界**：人物资料、历史经历、合成关系、情景利益与记忆共同约束决策；每个 Agent 配有 10 个状态粒子，用于表示状态不确定性。
- **公开与私下传播**：全局热榜前十、最新帖、回复通知、匿名表达、好友私聊及动态小群；同一轮可以同时发言、互动和私聊。
- **三类治理主体**：管理方、服务方与社区桥接者依据各自可见的信息，协调证据说明、服务工单和跨群触达。
- **预算与恢复**：支持预算化激活、全量关键帧、全量逐时间步；运行前核对调用数量与 Token 上限，运行中保存检查点。
- **交互分析**：平行论坛、节点与连线详情、人物档案、风险信号、治理响应和运行记录回放。

### 与玉兰-万象的关系

CampusPulse 是在 [YuLan-OneSim（玉兰-万象）](https://github.com/RUC-GSAI/YuLan-OneSim) 代码工程上扩展的校园治理应用。项目保留 OneSim 通用框架与启动入口，新增 ForumTwin 专用执行器、校园论坛数据处理、人物与关系建模、公私传播、治理评估和交互工作台。当前校园案例通过 ForumTwin 执行器与 CampusPulse 专用 API 运行；OneSim 通用模拟入口作为另一条运行路径保留。

从产品定位看，CampusPulse 为玉兰-万象补充校园论坛与公共服务治理场景。代码分工和上游比较见[架构说明](docs/ARCHITECTURE_AND_CODE.md)与[上游对照](docs/UPSTREAM_COMPARISON.md)。

### 三个项目

| 项目 | 作用 | 查看内容 |
|---|---|---|
| 暑期住宿床位分配正当性争议 | 资源分配方案对比 | 自然演化与组合治理下的资格争议、排序解释、服务处理和信任变化 |
| 世纪馆“幽灵预约”争议 | 项目配置与运行过程展示 | 从“现场空场、系统不可预约”的学生讨论出发，查看预约记录、异常处理与群体传播 |
| 讲座冲突事件传播 | 跨议题情景对比 | 冲突回应、责任讨论、居民追问和后续保障 |

三个项目都提供 Natural（无新增治理）与 D（动态组合治理）的对照。世纪馆包含 Tick 0–10 的保存记录，支持从新建项目进入回放。

### 快速开始

环境要求：Python 3.11–3.13、Node.js 22.6 或更高版本，以及 npm。首次安装依赖需要网络。

在项目根目录打开 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-campus-pulse-online.lock
cd src/frontend
npm ci
cd ../..
python scripts/campus_pulse/run_full_system.py
```

打开 [CampusPulse](http://127.0.0.1:4173/campus-pulse)。启动器同时启动前端、项目 API 和运行进度服务；关闭启动器会停止它管理的服务。

Windows 可直接运行 `.\START_FULL_SYSTEM.cmd`。Linux/macOS 可执行 `python scripts/campus_pulse/run_full_system.py`。

浏览已有记录无需模型密钥。新模型运行在“模型与 API”中配置 Base URL、模型名称、API Key、超时、并发和输出上限。密钥保存在本机用户配置目录，与项目数据分开管理。

### 使用流程

1. **创建项目**：选择事件模板或“从空白开始”，填写项目目标、事件经过、已知信息、待解答问题与涉及群体。
2. **确认配置**：检查 Agent 人口、事件与治理方案；创建后从项目总览继续操作。
3. **选择执行方式**：世纪馆保存记录可直接回放；新模型运行需要配置模型连接并确认发送范围与预算。
4. **观察演化**：按时间步查看热榜、帖子、评论、人物和传播连线；点击内容可暂停检查，支持中英文切换。
5. **比较结果**：在相同时间步对照 Natural 与 D，查看讨论内容、服务反馈、风险和群体差异。

保存记录可在项目运行页按时间步播放。自定义模型运行的支持范围、配置和恢复方法见[用户指南](docs/USER_GUIDE.md)。

### 结果摘要

**住宿分配：服务处理与程序信任是两项治理任务。** 组合治理使资格、材料与复核问题进入更明确的处理流程，模拟中的服务积压得到解决。与此同时，居民仍追问排序依据、补材料时限与申诉入口。这个结果提示：处理具体求助后，还需要解释分配程序，并让相关群体获得一致的信息。

**讲座冲突：回应内容影响后续讨论的方向。** 治理方对冲突、责任和保障作出回应后，居民通过直接回复和引用继续追问处理进度。模拟中议题更集中，信任改善、关切下降，讨论仍然活跃。消息链能够展示回应如何被理解，以及哪些问题继续存在。

**世纪馆预约：把争议转化为可以核查的问题。** 面对空场却不可预约的现象，Agent 提出维护、取消释放和签到核销等解释。治理分支引入预约证据、异常处理工单与跨项目触达，讨论进一步聚焦记录和处理程序。用户可逐步检查从最初疑问到分支演化的完整过程。

这些结果来自合成 Agent 在指定情景中的模拟。实验设计、四维评估、统计结果与复现命令统一见[实验报告](EXPERIMENT_REPORT.md)。

### 项目结构与文档

| 路径 | 作用 |
|---|---|
| `src/onesim/` | YuLan-OneSim 基础框架 |
| `src/envs/campus_pulse_governance/` | 论坛、人物输入、关系、私聊、粒子、抽样与治理内核 |
| `src/backend/campus_pulse/` | 项目管理、数据存储、运行编排、恢复与结果服务 |
| `src/frontend/` | Vue 前端、页面测试与公开案例资产 |
| `data/campus_pulse_workspace/` | 项目目录、人口和运行组件；本机数据库由启动器创建 |
| `evidence/` | 三个项目及配套评估的可复现证据 |
| `scripts/` | 数据处理、实验执行、分析、验证和启动工具 |
| `_run/` | 本机运行进度与临时产物，默认不提交 |

| 文档 | 内容 |
|---|---|
| [用户指南](docs/USER_GUIDE.md) | 启动、页面操作、模型配置、运行与恢复 |
| [架构与代码](docs/ARCHITECTURE_AND_CODE.md) | 模块职责、调用关系和实现入口 |
| [数据处理](docs/DATA_PIPELINE.md) | 论坛语料如何转为人物与模拟数据 |
| [实验报告](EXPERIMENT_REPORT.md) | 实验设计、人群参数、情景对比、统计与结论 |
| [评估复现说明](docs/EVALUATION_REPRODUCTION.md) | 95.8%、99.3%、70.8% 等指标的代码、证据与复现方式 |
| [上游功能对照](docs/UPSTREAM_COMPARISON.md) | 相对 YuLan-OneSim 的扩展与代码清单 |

### 开发与测试

```powershell
# 在项目根目录运行后端核心测试
$env:PYTHONPATH='src;.'
python -m unittest src.backend.tests.test_campus_pulse_api
python -m unittest src.envs.campus_pulse_governance.tests.test_forum_turns

# 前端测试、翻译检查与构建
cd src/frontend
npm run test:forum-twin
npm run check:i18n
npm run build -- --configLoader runner
```

端到端测试见[用户指南](docs/USER_GUIDE.md)。提交修改前请运行相关测试，并在 Pull Request 中说明改动、验证方法及界面截图。问题反馈请附操作步骤、预期行为与实际结果；分享配置时请移除密钥及个人信息。

### 许可证与来源

本项目基于 [YuLan-OneSim](https://github.com/RUC-GSAI/YuLan-OneSim) 扩展，并依 [Apache License 2.0](LICENSE) 发布。仓库提供脱敏派生数据与合成案例；原始论坛语料由其持有方管理。

---

<a id="english"></a>

## English

CampusPulse simulates campus incidents, group reactions and governance responses in a persistent forum world of 1,000 synthetic Agents. Users create projects, configure incidents and interventions, set model budgets, and inspect public discussion, private chats, group conversations and service handling over time.

The product supports rehearsing campus resource-allocation and public-service decisions: what different groups care about, how disputes spread, who receives and adopts a response, and what remains unresolved.

### Features

- **Persistent Agent world**: profiles, reviewed history, synthetic relationships, scenario stakes and memory inform decisions. Ten particles per Agent represent state uncertainty.
- **Public and private interaction**: a shared top-ten hot list, latest posts, reply notifications, anonymous expression, friend chats and dynamic groups. Agents can speak, react and chat in the same turn.
- **Three governance roles**: authority, service operator and community bridge coordinate evidence, service tickets and outreach using their own limited observations.
- **Budgeting and recovery**: budgeted activation, full-population keyframes and every-tick modes; request and token limits are checked before execution, with checkpoints saved during runs.
- **Interactive analysis**: parallel forums, node and edge inspection, Agent dossiers, risk signals, governance responses and saved-run replay.

### Relationship to YuLan-OneSim

CampusPulse extends the [YuLan-OneSim](https://github.com/RUC-GSAI/YuLan-OneSim) codebase with a campus-governance application. It retains the general OneSim framework and entry points while adding the dedicated ForumTwin executor, forum-data preparation, Agent and relationship modelling, public/private interaction, governance evaluation and a project workbench. The campus cases run through ForumTwin and the dedicated CampusPulse API; the general OneSim simulator remains a separate execution path.

The application complements YuLan-OneSim with campus-forum and public-service governance scenarios. See the [architecture guide](docs/ARCHITECTURE_AND_CODE.md) and [upstream comparison](docs/UPSTREAM_COMPARISON.md) for the code-level division of responsibilities.

### Three projects

| Project | Role | What to inspect |
|---|---|---|
| Summer housing allocation dispute | Resource-allocation comparison | Eligibility, ranking explanations, service handling and trust under Natural and combined governance |
| Century Gym “ghost booking” dispute | Project setup and process walkthrough | Student questions about empty yet unavailable courts, booking records, exception handling and group diffusion |
| Lecture conflict incident | Cross-topic comparison | Responses to conflict, accountability, resident follow-up and safeguards |

All three projects compare Natural (no additional intervention) with D (adaptive combined governance). Century Gym includes a saved Tick 0–10 sequence accessible through project creation.

### Quick start

Requirements: Python 3.11–3.13, Node.js 22.6 or later, and npm. Initial dependency installation requires internet access.

From the project root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-campus-pulse-online.lock
cd src/frontend
npm ci
cd ../..
python scripts/campus_pulse/run_full_system.py
```

Open [CampusPulse](http://127.0.0.1:4173/campus-pulse). The launcher starts the frontend, project API and progress service together and stops its child services when closed.

On Windows, run `.\START_FULL_SYSTEM.cmd`. On Linux/macOS, run `python scripts/campus_pulse/run_full_system.py`.

Saved records require no model key. For a new model run, open **Model & API** and configure the base URL, model, key, timeout, concurrency and output limit. Credentials are kept in the local user configuration directory, separate from project data.

### Workflow

1. **Create a project**: choose a template or **Start from scratch**; enter the objective, incident, known information, open questions and affected groups.
2. **Review settings**: check the Agent population, incident and governance plans; continue from the project overview after creation.
3. **Choose execution**: play the saved Century Gym record, or configure the model and approve the scope and budget for a new run.
4. **Inspect evolution**: open posts, comments, profiles and network links at each time step. Inspection pauses playback; the interface supports Chinese and English.
5. **Compare outcomes**: examine Natural and D at the same time step, including discussion, service feedback, risks and group differences.

Saved records play by time step from the project run page. See the [User guide](docs/USER_GUIDE.md) for supported custom-run configurations and recovery.

### Results

**Housing allocation: service delivery and procedural trust need separate attention.** Combined governance moves eligibility, documentation and review questions into a clearer handling process and resolves the simulated service backlog. Residents continue to question ranking, deadlines and appeal access. Resolving requests therefore leaves a further task: explaining the allocation procedure and reaching affected groups consistently.

**Lecture conflict: response content shapes subsequent discussion.** Residents directly reply to and quote governance messages about the incident, responsibility and safeguards. In the simulation, discussion becomes more focused, trust improves and concern declines while activity continues. Linked messages show how responses are understood and which questions persist.

**Century Gym: turn a booking dispute into checkable questions.** Agents interpret empty yet unavailable courts through maintenance, cancellation release and check-in rules. The governance branch introduces booking evidence, exception tickets and cross-sport outreach, focusing discussion on records and procedures. Users can inspect the sequence from the first questions to branch divergence.

These findings describe synthetic-Agent simulations under specified scenarios. The [Experiment report](EXPERIMENT_REPORT.md) contains the design, four-dimensional evaluation, statistics and reproduction commands.

### Structure and documentation

| Path | Purpose |
|---|---|
| `src/onesim/` | YuLan-OneSim foundation |
| `src/envs/campus_pulse_governance/` | Forum, persona input, relationships, private channels, particles, sampling and governance |
| `src/backend/campus_pulse/` | Projects, persistence, orchestration, recovery and results |
| `src/frontend/` | Vue application, tests and public case assets |
| `data/campus_pulse_workspace/` | Project catalog, population and runtime assets; local databases are created at startup |
| `evidence/` | Reproducible case and evaluation evidence |
| `scripts/` | Data preparation, execution, analysis, verification and launch tools |
| `_run/` | Local progress and temporary output, excluded from commits by default |

- [User guide](docs/USER_GUIDE.md): setup, navigation, model configuration and recovery.
- [Architecture and code](docs/ARCHITECTURE_AND_CODE.md): responsibilities, dependencies and implementation entry points.
- [Data pipeline](docs/DATA_PIPELINE.md): forum corpus to profiles and simulation inputs.
- [Experiment report](EXPERIMENT_REPORT.md): design, population, parameters, comparisons and findings.
- [Evaluation reproduction](docs/EVALUATION_REPRODUCTION.md): metric definitions, evaluator entry points, public evidence checks and private-input requirements.
- [Upstream comparison](docs/UPSTREAM_COMPARISON.md): extensions to YuLan-OneSim and their supporting files.

### Development and testing

```powershell
# Backend checks from the project root
$env:PYTHONPATH='src;.'
python -m unittest src.backend.tests.test_campus_pulse_api
python -m unittest src.envs.campus_pulse_governance.tests.test_forum_turns

# Frontend tests, translation checks and build
cd src/frontend
npm run test:forum-twin
npm run check:i18n
npm run build -- --configLoader runner
```

See the [User guide](docs/USER_GUIDE.md) for end-to-end tests. Contributions should include relevant tests, a description of changes and UI screenshots where appropriate. Bug reports should include reproduction steps, expected behaviour and actual behaviour. Remove credentials and personal information before sharing configuration.

### License and source

CampusPulse extends [YuLan-OneSim](https://github.com/RUC-GSAI/YuLan-OneSim) and is distributed under the [Apache License 2.0](LICENSE). This repository includes de-identified derived assets and synthetic cases; the source forum corpus is managed by its data holders.
