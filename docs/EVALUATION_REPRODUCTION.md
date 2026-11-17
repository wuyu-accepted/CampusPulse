# CampusPulse 评估代码与结果复现说明

## 这份说明解决什么问题

本项目的前端会展示若干四维评估结果。评估逻辑并不写在前端，独立实现位于 `scripts/` 和 `src/envs/campus_pulse_governance/`，公开汇总位于 `evidence/`。

需要区分两种复现：

1. **公开证据复核**：使用仓库中已经脱敏的汇总 JSON、粒子面板和数据卡，验证数值、字段、来源关系和统计口径；不需要 API 调用。
2. **逐条记录重算**：重新读取每一个居民 turn、上一时间步状态和四条件对照行，再执行评估脚本。原始逐条记录含合成 Agent 的上下文与行为文本，保存在本地运行目录，不进入开源仓库。因此，完整重算需要另外取得私有、已脱敏的运行证据包；不能用前端常量代替这一步。

## 95.8%、99.3% 和 70.8% 分别来自哪里

| 展示数值 | 含义 | 计算代码 | 公开汇总 | 逐条重算需要的输入 |
| --- | --- | --- | --- | --- |
| 95.8% | 匿名盲审中，下一步表达正确承接直接可见上下文的比例（24 条审阅样本） | `src/envs/campus_pulse_governance/forum_persona_audit.py`、`scripts/score_forum_persona_audit.py`、`scripts/analyze_forum_four_dimension_fidelity.py` | `evidence/four-dimension-v5-formal-v1/fidelity-evaluation-v3.json` 的 `dimensions.micro_persona_consistency.independent_blind_review.direct_context_uptake_rate` | 私有盲审输入和逐条审阅记录 |
| 99.3% | 结构化 LLM 输出字段进入粒子状态更新时，方向一致的比例（机制一致性检查，不是独立预测准确率） | `src/envs/campus_pulse_governance/forum_fidelity_v2.py`、`scripts/evaluate_forum_fidelity_v2.py` | 同一 JSON 的 `dimensions.micro_persona_consistency.immediate_llm_particle_assimilation.direction_agreement_rate` | 私有的 turn 结构化结果、上一状态和粒子更新记录 |
| 70.8% | 四条件对照中，留出一个 Agent 后，Profile 因子和上一状态因子仍能识别条件的准确率 | `scripts/analyze_joint_constraint_cross_agent.py`、`scripts/analyze_forum_joint_constraint.py`、`src/envs/campus_pulse_governance/forum_turns.py` | `evidence/joint-constraint-v1/joint-constraint-cross-agent-v1.json` 的 `metrics.profile_factor.held_out_accuracy` 和 `metrics.previous_state_factor.held_out_accuracy` | 四个条件的私有 12-Agent 对照文件 |

因此，合作者在前端看到的数值确实有后端实现和结果文件支撑；GitHub 版本缺少的是逐条私有输入，而不是评估公式本身。公开包不会放入 API Key、Provider body、原始论坛正文、源 ID、私聊原文或本地 SQLite。

## 评估代码地图

### 人物与上下文一致性

- `src/envs/campus_pulse_governance/forum_persona_audit.py`：定义盲审行、上下文承接、模板化表达和无依据事实的统计口径。
- `scripts/score_forum_persona_audit.py`：对已脱敏的审阅记录进行评分。
- `scripts/analyze_persona_four_dimension_99.py`：汇总人物阶段实验并生成公开聚合结果。
- `scripts/analyze_forum_four_dimension_fidelity.py`：合并人物、粒子和跨 Agent 约束结果。

### LLM 输出与粒子状态衔接

- `src/envs/campus_pulse_governance/forum_fidelity_v2.py`：比较 LLM 结构化字段的方向和粒子状态更新方向，并计算状态距离、终点 MAE 等指标。
- `scripts/evaluate_forum_fidelity_v2.py`：离线重放已结算结果，不连接网络；同时检查论坛时间线和公开结果是否一致。

### 人群异质性与跨陌生 Agent 泛化

- `scripts/analyze_joint_constraint_cross_agent.py`：四条件向量化、留一 Agent 训练、条件识别和置换检验。
- `scripts/analyze_forum_joint_constraint.py`：生成四条件盲审面板和公开聚合摘要。
- `scripts/score_forum_joint_constraint_blind_review.py`：对四条件盲审样本评分。
- `src/envs/campus_pulse_governance/forum_turns.py`：固定四种输入条件和输出合同。
- `src/envs/campus_pulse_governance/forum_realism.py`、`scripts/analyze_forum_ecology_fidelity.py`：论坛线程规模、回复功能、主题分布和生态指标。

### 证据来源核对

- `scripts/build_metric_provenance.py`：把实验报告中的数字逐行绑定到代码和证据文件，并检查缺失引用。
- `evidence/metric-provenance-v2.json`：数字—代码—证据的公开索引。
- `scripts/check_delivery_readiness.py`：检查交付目录中是否混入密钥、SQLite 或缺少必要文件。

## 公开证据复核（不调用模型）

在项目根目录执行：

```powershell
$env:PYTHONPATH = "src;."
python scripts/check_delivery_readiness.py
python scripts/build_metric_provenance.py `
  --report EXPERIMENT_REPORT.md `
  --root . `
  --output evidence/metric-provenance-v2.json
```

第一条命令检查交付范围和隐私边界；第二条命令核对报告中的数字是否都有对应代码和公开证据。两条命令都不访问 Provider。

## 逐条记录重算

### 论坛四维评估

需要一个本地私有运行目录（不应提交 GitHub），其中至少包含：

- 已结算的 resident ledger；
- continuation ledger 和 unknown 处理记录；
- 脱敏人口 release、论坛生态 release 和原始结果快照；
- 私有 persona turn evidence；
- 私有粒子状态快照。

命令参数对应 `scripts/evaluate_forum_fidelity_v2.py --help`：

```powershell
$env:PYTHONPATH = "src;."
python scripts/evaluate_forum_fidelity_v2.py `
  --initial-ledger <private-run>/authorized-live-ledger.sqlite3 `
  --continuation-ledger <private-continuation>/authorized-live-ledger.sqlite3 `
  --unknown-resolution <private-continuation>/prior-unknown-resolution-v1.json `
  --population <public-or-private-population.json> `
  --formal-population <public-or-private-formal-population.json> `
  --ecology <public-or-private-ecology.json> `
  --original-result <private-run>/formal-result.json `
  --persona-evidence <private-run>/persona-evidence.json `
  --public-dir <local-output>/public `
  --private-dir <local-output>/private
```

这个脚本是离线重放器；缓存缺失会直接报错，不会自动发起网络请求。

### 四条件跨 Agent 评估

私有目录中应有以下四个文件，每个文件包含同一组 12 个 Agent 的已结算结构化决策：

```text
context_only.private.json
context_plus_profile.private.json
context_ablation.private.json
full_natural_profile.private.json
```

先生成盲审面板，再做跨 Agent 留出评估：

```powershell
$env:PYTHONPATH = "src;."
python scripts/analyze_forum_joint_constraint.py `
  --run-dir <private-joint-run> `
  --output-dir <local-output>/joint-review

python scripts/analyze_joint_constraint_cross_agent.py `
  --condition-dir <private-joint-run>/conditions `
  --output <local-output>/joint-constraint-cross-agent.json
```

该脚本使用固定的特征向量、留一 Agent 训练和置换检验；它不会读取原始帖子，也不会生成公开 Agent ID 或文本。

### 人物阶段评估

`scripts/analyze_persona_four_dimension_99.py` 需要阶段 A/B 的私有结果、人口 release 和盲审预筛记录。参数和默认目录见脚本末尾的 `ArgumentParser`；没有这些私有输入时，不应把公开汇总数字误称为重新计算结果。

## 交付包里应该给合作者什么

本次交付包包含：

- 全部公开源代码和评估脚本；
- `evidence/` 下的三项目证据、四维度汇总、粒子面板和指标来源索引；
- `docs/` 下的架构、数据处理、上游对照、用户指南和本说明；
- `EXPERIMENT_REPORT.md` 及其 PDF 版；
- `src/frontend/` 的前端源码和公开展示数据；
- `requirements-campus-pulse-online.txt` 与锁定版本文件。

另外，团队包会把本机 `_run/forum-twin-live-sessions/*/public-progress/` 下的公开进度快照复制到 `experiment-data/forum-twin-live-sessions/`。这些文件只包含可展示的时间步、帖子/互动聚合和校验信息，方便合作者直接检查世纪馆案例的逐 Tick 回放；它们不是私有 ledger。

交付包不包含：

- `API.txt`、任何 Provider Key 或 `.env` 密钥；
- `_run/`、SQLite、checkpoint、私有 ledger；
- `data/private/`、原始帖子/评论、源 ID 和联系方式；
- `node_modules/`、构建缓存和本地 IDE 文件；
- 内部规划草稿 `PAPER_EXPERIMENT_PLAN.md`。

如果需要合作者独立重算 95.8%、99.3% 或 70.8%，需要单独通过安全渠道交付“私有评估输入包”，只包含脱敏结构化行和粒子状态，不包含原文、密钥或 Provider body。源码包本身已经足够检查公式、运行公开证据复核和复现前端结果。

团队内部全量包另行包含本机 `data/private/`、`_run/`、Provider 请求/响应材料和本地 SQLite，用于交接和排查历史运行；它只通过线下或受控共享发送，不应上传 GitHub 或作为公开发布物。

## English summary

The repository contains the evaluation implementations. The missing part in the public GitHub branch was the record-level private input used by the final runs, not the formulas. The shipped package includes the public source, evaluators, aggregate evidence, provenance index and reproduction instructions. Exact record-level recomputation of 95.8%, 99.3% and 70.8% requires a separate sanitized private evidence bundle; it must never include API keys, raw forum text, source IDs, provider bodies or SQLite ledgers.
