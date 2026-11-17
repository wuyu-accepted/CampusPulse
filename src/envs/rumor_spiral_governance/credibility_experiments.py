# -*- coding: utf-8 -*-
"""
可信性实验脚本：三组实验验证 Agent 的仿真可信度。

实验 A（任务泄露对照）：盲 Feed 组 vs 直接提问组
实验 B（画像区分）：同 Feed 给不同画像，比较行为分布
实验 C（前后稳定性）：同 agent 跨轮行为是否一致于人格

用法：python credibility_experiments.py
"""
import asyncio, json, sys, os, re, glob
from collections import defaultdict, Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
import onesim
from onesim.models import get_model
from onesim.models.core.message import Message
from analyze_stance import _extract, classify

RUNS = os.path.join(HERE, "runs")
MODEL_CFG = os.path.abspath(os.path.join(HERE, "..", "..", "..", "config", "model_config.json"))


def latest_decisions():
    fs = sorted(glob.glob(os.path.join(RUNS, "*", "datasets", "decisions_*.json")))
    return fs[-1] if fs else None


# ======= 实验 A：任务泄露对照 =======
BLIND_INSTRUCTION = """你正在刷微博。上面是你刚刷到的信息流。请像你平时使用微博时那样，自然地决定接下来做什么。
可选：no_action/like/repost/comment/reply/report。"""
DIRECT_INSTRUCTION = """请评估以下信息的可信度，并回答：
1. 你是否相信这条消息？（yes/no/unsure）
2. 对于这类未经证实的消息，你会怎么做？（no_action/转发提醒求证/转发并相信/举报）
请只用 JSON 回答。"""

LEASE_OBSERVATION = "信息流：\n[P0] @seed_account：听说今晚本市部分地区将临时管控，大家提前囤点生活物资（点赞8 转发12 评论3）\n[P1] @local_news_x：关于'临时管控'的说法，暂未看到任何官方通知"


async def experiment_a_lease(model):
    print("\n=== 实验 A：任务泄露对照 ===")
    results = {}
    for name, inst in [("blind", BLIND_INSTRUCTION), ("direct", DIRECT_INSTRUCTION)]:
        prompt = model.format(
            Message("user",
                    f"### Agent Profile:\n{{'name':'理性搬砖人','persona':'你倾向于相信正规媒体...'}}\n\n"
                    f"### Observation:\n{LEASE_OBSERVATION}\n\n"
                    f"### Instruction:\n{inst}\n\n请输出 JSON。",
                    role="user"),
        )
        resp = await model.acall(prompt)
        text = resp.text if hasattr(resp, "text") else str(resp)
        results[name] = text[:400]
        label = "盲 Feed 组" if name == "blind" else "直接提问组"
        print(f"\n{label} 输出:\n{text[:300]}")
    return results


# ======= 实验 B：画像区分 =======
async def experiment_b_persona(model, decisions_path=None):
    print("\n=== 实验 B：画像区分 ===")
    if not decisions_path:
        decisions_path = latest_decisions()
    if not decisions_path:
        print("无决策数据，跳过")
        return {}
    data = json.load(open(decisions_path, encoding="utf-8"))
    # 取一轮的决策，按 agent 聚合行为
    step1 = [x for x in data if x.get("step") == 1]
    act_by_persona = defaultdict(list)
    for x in step1:
        out = x.get("output", "")
        act = _extract(out, "action_type") or "no_action"
        # 从 prompt 提取 persona 前 20 字作简要标签
        prompt = x.get("prompt", "")
        pm = re.search(r'"persona"\s*:\s*"([^"]{0,40})', prompt)
        tag = pm.group(1) if pm else "unknown"
        act_by_persona[tag].append(act)
    print("同 Feed 下不同画像的行为分布：")
    for tag, acts in sorted(act_by_persona.items()):
        c = Counter(acts)
        print(f"  {tag[:25]:25s} | {dict(c)}")
    return {"act_by_persona": dict(act_by_persona)}


# ======= 实验 C：前后稳定性 =======
def experiment_c_stability(decisions_path=None):
    print("\n=== 实验 C：前后稳定性 ===")
    if not decisions_path:
        decisions_path = latest_decisions()
    if not decisions_path:
        print("无决策数据，跳过")
        return {}
    data = json.load(open(decisions_path, encoding="utf-8"))
    by_agent = defaultdict(lambda: defaultdict(list))
    for x in data:
        out = x.get("output", "")
        act = _extract(out, "action_type") or "no_action"
        by_agent[x["agent_id"]][x.get("step", 0)].append(act)

    # 检查同 agent 跨轮是否保持角色一致（活跃者始终活跃 / 沉默者始终沉默）
    stable, inconsistent = 0, 0
    for aid, steps in by_agent.items():
        acts_all = [a for acts in steps.values() for a in acts]
        dominant = Counter(acts_all).most_common(1)[0][0] if acts_all else None
        # 如果 agent 在 3+ 轮中至少一轮偏离了 dominant，标为不稳定
        deviations = 0
        for s, acts in steps.items():
            for a in acts:
                if a != dominant:
                    deviations += 1
        if deviations <= max(1, len(acts_all) // 3):  # 允许少量波动
            stable += 1
        else:
            inconsistent += 1

    print(f"行为稳定(跨轮一致于主导模式): {stable} 人")
    print(f"行为漂移(跨轮不一致):            {inconsistent} 人")

    # 聚合：沉默者的跨轮 silence persistence
    silent_agents = set()
    for aid, steps in by_agent.items():
        all_silent = all(all(a == "no_action" for a in acts) for acts in steps.values())
        if all_silent:
            silent_agents.add(aid)
    print(f"始终沉默的 agent: {len(silent_agents)}")
    return {"stable": stable, "inconsistent": inconsistent, "consistently_silent": len(silent_agents)}


async def main():
    await onesim.init(model_config_path=MODEL_CFG, components=["model"])
    model = get_model(config_name="gpt-4o-mini")
    dp = latest_decisions()
    print(f"决策文件: {dp}")

    r_a = await experiment_a_lease(model)
    r_b = await experiment_b_persona(model, dp)
    r_c = experiment_c_stability(dp)

    out = os.path.join(HERE, "credibility_results.json")
    json.dump({"experiment_a": {"blind": r_a.get("blind",""), "direct": r_a.get("direct","")},
               "experiment_b": r_b,
               "experiment_c": r_c},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out}")


if __name__ == "__main__":
    asyncio.run(main())
