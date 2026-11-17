# CampusPulse 模拟内核

本目录实现论坛世界、Agent 决策、信息传播与治理。产品启动和使用见[项目 README](../../../README.md)；实验设计与结果见[实验报告](../../../EXPERIMENT_REPORT.md)。

## 模块入口

| 模块 | 职责 |
|---|---|
| `forum_twin.py` | 讨论串、消息、主张、曝光与记忆 |
| `forum_hero.py` | 时间步编排、共享基线、分支推进、热榜与最新帖 |
| `forum_turns.py` | 人物、状态与时间可见上下文的输入输出合同 |
| `forum_relationships.py` | 合成关系与情景利益结构 |
| `forum_v2_execution.py` | 预算化和全量激活、私聊调度与统一提交 |
| `forum_visibility.py` | 项目可见性、关系通知、私聊及匿名表达配置 |
| `forum_risk.py` | 风险信号和治理角色可见观测 |
| `forum_realism.py` | 论坛生态统计对照 |
| `forum_persona_audit.py` | 人物、上下文与状态一致性评估 |
| `tests/` | 内核合同、时序、恢复与统计回归测试 |

公开消息与私聊正文来自模型输出或保存的运行记录。普通后台计算负责注意力、Feed 投递、状态粒子与资源队列。三个治理角色分别获得公开讨论、服务请求与触达缺口等观测。

## 开发与验证

在项目根目录运行：

```powershell
$env:PYTHONPATH='src;.'
python -m unittest src.envs.campus_pulse_governance.tests.test_forum_turns
python -m unittest src.envs.campus_pulse_governance.tests.test_forum_realism
```

模块详细说明见[架构与代码](../../../docs/ARCHITECTURE_AND_CODE.md)，原始语料处理与人口编译见[数据处理](../../../docs/DATA_PIPELINE.md)。
