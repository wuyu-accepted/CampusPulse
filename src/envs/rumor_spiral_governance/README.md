# 破默：基于玉兰万象的谣言沉默螺旋治理推演系统

代号 `rumor_spiral_governance`

## 系统目标

弥合"私人态度"与"公开言论"之间的差距：**少数高活跃用户制造虚假多数感，使多数持怀疑态度的公众保持沉默。** 系统比较删除、命令式辟谣、证据式核查与提升质疑可见度等治理策略，评估其对谣言扩散、沉默差距及反弹的共同影响。

基于 RUC-GSAI 的 YuLan-OneSim 社会模拟器改造。

---

## 核心机制创新

### 1. 微博式局部信息流（非全局广播）
每个用户每轮收到**个性化的信息流**（非全局 Feed）：广播帖（谣言/媒体/辟谣，人人可见）+ 自己关注对象的帖子/评论/回复 **完整线程内容**（两级嵌套：帖→评论→回复）。消息末尾附"**关注圈氛围**"——你关注的人里有K位在讨论此事。

### 2. 私人/公开双通道
LLM 每轮输出 `{private_note, public_action}`：
- `private_note` → 写入自身 profile，**不外泄**（用于后续 stance 分析识别"私下怎么想"）
- `public_action`（repost/comment/like/report/no_action）→ 作为 `UserActionEvent` 公开回传环境

### 3. 全局变局部：帖子池按轮演化
ENV（环境）维护全局 `post_pool`，每轮给每个用户组装个性化 Feed 并回收公开行为更新池。

### 4. 气候敏感型人格
怀疑者在没人质疑时沉默，看到他人质疑或权威辟谣就发声；相信者对有依据的辟谣收敛、对空口通知不买账。

### 5. 两级线程渲染
帖子下的评论用 `↳评论 @X（你关注的人/网友）：文本` 缩进显示；评论下的回复用 `↳↳回复 @X：文本`。

### 6. 事实核查有真值
`env_data.json` 的 `case_bundle` 隐藏 claim+ground_truth+evidence；只有 FactCheck 用 `get_env_data()` 读取，普通用户不可见。

---

## 目录结构

```
src/envs/rumor_spiral_governance/
├── scene_info.json           # 场景元信息 & ODD 协议
├── env_data.json             # 初始帖子池 + case_bundle + 模拟配置
├── events.json               # 工作流事件图定义
├── actions.json              # 各 Agent 动作定义
├── governance_manifest.json  # 治理对照 sweep 记录
├── code/
│   ├── SimEnv.py             # 自定义环境（帖子池/社交图谱/Feed/行为回收）
│   ├── OrdinaryUser.py       # 普通用户（browse_and_act）
│   ├── OpinionLeader.py      # 意见领袖（=画像+网络不同的 OrdinaryUser）
│   ├── MediaOrganization.py  # 媒体机构（select_and_publish）
│   ├── FactCheckOrganization.py  # 事实核查（读真值发辟谣）
│   ├── events.py             # 自定义事件类
│   └── __init__.py
├── profile/
│   ├── schema/               # 画像 Schema（sampling 控制LLM生成）
│   └── data/                 # 实际画像 & Relationship.csv
├── runs/                     # 运行产物（decisions_*.json + metrics_plots + 图）
├── analyze_stance.py         # stance 分类器 & 沉默差距图
├── driver_governance.py      # 治理对照驱动（sweep 5 模式）
├── governance_analysis.py    # 治理对照分析 & 出图
└── credibility_experiments.py  # 可信性实验
```

---

## 运行方法

### 前置条件
- `.venv`（Python 3.13，依赖已装）
- ollama 运行 bge-m3 embedding（未装时需关掉 long_term_storage）
- 配置见 `config/rumor_config.json` + `config/model_config.json`（gpt-4o-mini）

### 单次运行
```sh
cd src
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ../.venv/Scripts/python.exe main.py \
  --config ../config/rumor_config.json \
  --model_config ../config/model_config.json \
  --mode single
```

### 治理对照 sweep
```sh
cd src
python envs/rumor_spiral_governance/driver_governance.py 5   # 5模式×5次
python envs/rumor_spiral_governance/governance_analysis.py     # 分析&出图
```

### 沉默差距分析
```sh
python envs/rumor_spiral_governance/analyze_stance.py [decisions.json]
```

---

## 关键实验结果

### RQ1：画像是能区分行为吗？ → **是**
控制化人格下 same feed → 行为分布：repost 2 / like 2 / comment 2 / no_action 12。重度活跃人格（如快讯君）持续传播，潜水者（如社恐小吴）始终沉默。

**可信性实验 B（画像区分）**：同 Feed 给 25 个不同画像 → 行为从 repost 到 no_action 分明，没有全收敛到一个模式。

### RQ2：少数活跃者能制造虚假多数感吗？ → **能**
基线（无干预，10人6轮）：私下怀疑率 70%，但**公开质疑率始终为0%**，沉默差距 +0.7 ~ +1.0。公开领域只看得到少数相信者的支持：

| 轮次 | 私下怀疑 | 公开质疑 | 沉默差距 | 私下支持 | 公开支持 |
|------|---------|---------|---------|---------|---------|
| 1 | 70% | 0% | +0.70 | 30% | 0% |
| 3 | 70% | 0% | +0.70 | 30% | 30% |
| 6 | 70% | 0% | +0.70 | 30% | 30% |

**可信性实验 C（前后稳定性）**：26 个 agent 跨 3 轮全部行为稳定于主导模式（0 漂移）。

### RQ3：什么治理最优？ → **证据式辟谣最有效**
25 次运行均值（reps=5，每模式 5 次重复）：

| 策略 | 沉默差距 | 干预后反弹 | 效应方向 |
|------|---------|-----------|---------|
| 不干预 | +0.78 | 7.0 | 基准 |
| 删除/限流 | +0.77 | 10.6 | ⚠️ 几乎没降低沉默 |
| 命令式辟谣 | +0.72 | 11.0 | ⚠️ 轻微反弹 |
| **证据式辟谣** | **+0.67** | 14.2 | ✅ 最有效降沉默，但有反弹 |
| **提升质疑曝光** | **+0.67** | 12.2 | ✅ 降沉默效果同证据式 |

**结论**：证据式辟谣 + 提升质疑曝光是"破默"最有效的两种策略；命令式辟谣的沉默差距与不干预无显著差异。

### 可信性实验 A（任务泄露）：
待 API 恢复后补充。预期：盲 Feed 组行为分布更分散（自然反应），直接提问组过度集中（all-理性公民）。

---

## 模型配置

`config/model_config.json`：chat 用 gpt-4o-mini（`yunwu.ai/v1` 代理），embedding 用本地 ollama bge-m3（`127.0.0.1:11434`）。

## 场景可配置参数

`env_data.json` 的 `sim` 段：
- `feed_size`：每用户每轮信息流条数（默认 4）
- `max_comments_shown`：每条帖子下最多展示的评论数（默认 4）
- `intervention_mode`：`none` / `delete` / `command_debunk` / `evidence_debunk` / `boost_visibility`
- `intervention_round`：干预从第几轮开始

Agent 数量见 `config/rumor_config.json` 的 `agent.profile.<Type>.count`。
