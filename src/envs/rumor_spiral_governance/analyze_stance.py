# -*- coding: utf-8 -*-
"""
沉默螺旋分析器：读取一次模拟的 decisions_*.json，用 LLM 把每个用户每轮的
私人想法(private_note)与公开表达(action+public_text)分类，计算沉默差距等指标并出图。

private_stance ∈ {support(相信/倾向支持谣言), skeptical(怀疑/反对), neutral(中立/不确定)}
public_stance  ∈ {support(公开支持), query(公开质疑), silent(未公开表达)}

用法：
    python analyze_stance.py [decisions.json]   # 缺省取 rumor_spiral_governance 最新一次
"""
import asyncio, json, sys, os, re, glob
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")))  # src on path
import onesim
from onesim.models import get_model
from onesim.models.core.message import Message

SCENE_RUNS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")
MODEL_CFG = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "config", "model_config.json"))


def _extract(output: str, key: str) -> str:
    m = re.search(r'"%s"\s*:\s*"([^"]*)"' % key, output or "")
    return m.group(1) if m else ""


def latest_decisions() -> str:
    files = sorted(glob.glob(os.path.join(SCENE_RUNS, "*", "datasets", "decisions_*.json")))
    if not files:
        raise SystemExit("未找到 decisions 文件，请先运行模拟。")
    return files[-1]


async def classify(model, records):
    """records: [{'idx','private_note','action_type','public_text'}] -> [{'private','public'}]"""
    # 分块，避免单次过长
    results = {}
    CHUNK = 15
    for s in range(0, len(records), CHUNK):
        chunk = records[s:s + CHUNK]
        payload = [{"idx": r["idx"], "private_note": r["private_note"],
                    "public_action": r["action_type"], "public_text": r["public_text"]} for r in chunk]
        instruction = (
            "下面是若干社交平台用户在'某地今晚将临时管控/需囤货'这条未经证实传言下的记录。"
            "对每条，请分别判断：\n"
            "private_stance = 该用户【私下真实态度】(仅据 private_note)：support=相信或倾向认为传言可能属实/值得照做；"
            "skeptical=怀疑、认为多半不实或需等官方；neutral=中立或不确定。\n"
            "public_stance = 该用户【公开表达】(据 public_action 与 public_text)："
            "support=公开转发/评论表达相信或号召照做；query=公开质疑、求证、提醒勿信；"
            "silent=没有任何公开表态(no_action，或仅 like 而无文字)。\n"
            "只依据文本判断，不要脑补。严格输出 JSON 数组，每项 {\"idx\":<原idx>,\"private\":\"...\",\"public\":\"...\"}，"
            "用 ```json 代码块包裹。\n\n数据：\n" + json.dumps(payload, ensure_ascii=False)
        )
        prompt = model.format(Message("user", instruction, role="user"))
        resp = await model.acall(prompt)
        text = resp.text if hasattr(resp, "text") else str(resp)
        mm = re.search(r'```json\s*(\[.*?\])\s*```', text, re.S) or re.search(r'(\[.*\])', text, re.S)
        arr = json.loads(mm.group(1)) if mm else []
        for item in arr:
            results[int(item["idx"])] = (item.get("private", "neutral"), item.get("public", "silent"))
    return results


async def main():
    path = sys.argv[1] if len(sys.argv) > 1 else latest_decisions()
    print(f"分析文件: {path}")
    data = json.load(open(path, encoding="utf-8"))

    # 每个 (agent, step) 取最后一条决策
    latest = {}
    for x in data:
        latest[(x["agent_id"], x.get("step", 0))] = x
    records = []
    meta = []
    for (aid, step), x in latest.items():
        out = x.get("output", "")
        records.append({
            "idx": len(records),
            "private_note": _extract(out, "private_note"),
            "action_type": _extract(out, "action_type") or "no_action",
            "public_text": _extract(out, "public_text"),
        })
        meta.append((aid, step))

    await onesim.init(model_config_path=MODEL_CFG, components=["model"])
    model = get_model(config_name="gpt-4o-mini")
    cls = await classify(model, records)

    # 按轮聚合
    per_round = defaultdict(lambda: {"n": 0, "priv_sup": 0, "priv_skep": 0,
                                     "pub_sup": 0, "pub_query": 0, "silent": 0})
    for i, (aid, step) in enumerate(meta):
        priv, pub = cls.get(i, ("neutral", "silent"))
        r = per_round[step]
        r["n"] += 1
        r["priv_sup"] += priv == "support"
        r["priv_skep"] += priv == "skeptical"
        r["pub_sup"] += pub == "support"
        r["pub_query"] += pub == "query"
        r["silent"] += pub == "silent"

    steps = sorted(per_round)
    def rate(step, key):
        r = per_round[step]; return r[key] / r["n"] if r["n"] else 0
    priv_skep = [rate(s, "priv_skep") for s in steps]
    pub_query = [rate(s, "pub_query") for s in steps]
    priv_sup = [rate(s, "priv_sup") for s in steps]
    pub_sup = [rate(s, "pub_sup") for s in steps]
    silent = [rate(s, "silent") for s in steps]
    silence_gap = [a - b for a, b in zip(priv_skep, pub_query)]

    print("\n轮次 | 私下怀疑 | 公开质疑 | 沉默差距 | 私下支持 | 公开支持 | 沉默率")
    for i, s in enumerate(steps):
        print(f" {s}   |  {priv_skep[i]:.2f}   |  {pub_query[i]:.2f}   |  {silence_gap[i]:+.2f}   |  {priv_sup[i]:.2f}   |  {pub_sup[i]:.2f}   |  {silent[i]:.2f}")

    # 出图（英文标签避免中文字体缺失）
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.2))
    ax1.plot(steps, priv_skep, "o-", color="#2b6cb0", label="Private skepticism")
    ax1.plot(steps, pub_query, "s-", color="#dd6b20", label="Public skepticism (voiced)")
    ax1.fill_between(steps, pub_query, priv_skep, color="#f6ad55", alpha=0.3, label="Silence gap")
    ax1.plot(steps, silent, "^--", color="#718096", label="Public silence rate")
    ax1.set_title("Spiral of Silence: private doubt vs. publicly voiced doubt")
    ax1.set_xlabel("Round"); ax1.set_ylabel("Fraction of users"); ax1.set_ylim(0, 1); ax1.legend(fontsize=9); ax1.grid(alpha=.3)

    ax2.plot(steps, priv_sup, "o-", color="#38a169", label="Private support")
    ax2.plot(steps, pub_sup, "s-", color="#e53e3e", label="Public support (visible majority)")
    ax2.set_title("False majority: private support vs. visible public support")
    ax2.set_xlabel("Round"); ax2.set_ylabel("Fraction of users"); ax2.set_ylim(0, 1); ax2.legend(fontsize=9); ax2.grid(alpha=.3)

    out_png = os.path.join(os.path.dirname(path), "silence_gap_analysis.png")
    plt.tight_layout(); plt.savefig(out_png, dpi=150, bbox_inches="tight")
    print(f"\n图已保存: {out_png}")


if __name__ == "__main__":
    asyncio.run(main())
