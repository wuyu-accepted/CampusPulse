# -*- coding: utf-8 -*-
"""
治理策略对照分析：读 governance_manifest.json，对每次运行做 stance 分类，
计算各模式的 传播量/沉默差距/公开质疑率/反弹，聚合并出对照图。

用法：python governance_analysis.py
"""
import asyncio, json, os, sys, glob
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))  # src
import onesim
from onesim.models import get_model
from analyze_stance import classify, _extract  # 复用分类器

MODEL_CFG = os.path.abspath(os.path.join(HERE, "..", "..", "..", "config", "model_config.json"))
MODE_ORDER = ["none", "delete", "command_debunk", "evidence_debunk", "boost_visibility"]
MODE_LABEL = {"none": "No action", "delete": "Delete/limit",
              "command_debunk": "Command debunk", "evidence_debunk": "Evidence debunk",
              "boost_visibility": "Boost doubts"}
INTERVENTION_ROUND = 3


async def analyze_run(model, run_dir):
    files = glob.glob(os.path.join(run_dir, "datasets", "decisions_*.json"))
    if not files:
        return None
    data = json.load(open(sorted(files)[-1], encoding="utf-8"))
    latest = {}
    for x in data:
        latest[(x["agent_id"], x.get("step", 0))] = x
    records, meta = [], []
    for (aid, step), x in latest.items():
        out = x.get("output", "")
        records.append({"idx": len(records), "private_note": _extract(out, "private_note"),
                        "action_type": _extract(out, "action_type") or "no_action",
                        "public_text": _extract(out, "public_text")})
        meta.append((aid, step, records[-1]["action_type"]))
    cls = await classify(model, records)

    per_round = defaultdict(lambda: {"n": 0, "priv_skep": 0, "pub_query": 0, "pub_sup": 0})
    support_acts = reposts = reposts_after = 0
    for i, (aid, step, act) in enumerate(meta):
        priv, pub = cls.get(i, ("neutral", "silent"))
        r = per_round[step]; r["n"] += 1
        r["priv_skep"] += priv == "skeptical"
        r["pub_query"] += pub == "query"
        r["pub_sup"] += pub == "support"
        if act == "repost":
            reposts += 1
            if step >= INTERVENTION_ROUND:
                reposts_after += 1
        if pub == "support":
            support_acts += 1
    steps = sorted(per_round)
    def rate(s, k): r = per_round[s]; return r[k] / r["n"] if r["n"] else 0
    sg = [rate(s, "priv_skep") - rate(s, "pub_query") for s in steps]
    pq = [rate(s, "pub_query") for s in steps]
    return {
        "silence_gap": sum(sg) / len(sg) if sg else 0,
        "public_query": sum(pq) / len(pq) if pq else 0,
        "rumor_support_acts": support_acts,
        "reposts": reposts,
        "backfire_after": reposts_after,
        "final_public_support": rate(steps[-1], "pub_sup") if steps else 0,
    }


async def main():
    manifest = json.load(open(os.path.join(HERE, "governance_manifest.json"), encoding="utf-8"))
    await onesim.init(model_config_path=MODEL_CFG, components=["model"])
    model = get_model(config_name="gpt-4o-mini")

    by_mode = defaultdict(list)
    for entry in manifest:
        rd = entry.get("run_dir")
        if not rd or not os.path.isdir(rd):
            continue
        m = await analyze_run(model, rd)
        if m:
            by_mode[entry["mode"]].append(m)
            print(f"{entry['mode']} rep{entry['rep']}: {m}", flush=True)

    keys = ["rumor_support_acts", "silence_gap", "public_query", "backfire_after", "final_public_support"]
    agg = {}
    for mode in MODE_ORDER:
        runs = by_mode.get(mode, [])
        agg[mode] = {k: (sum(r[k] for r in runs) / len(runs) if runs else 0) for k in keys}

    print("\n模式 | 谣言支持行为 | 沉默差距 | 公开质疑率 | 干预后转发(反弹) | 末轮公开支持")
    for mode in MODE_ORDER:
        a = agg[mode]
        print(f"{MODE_LABEL[mode]:16} | {a['rumor_support_acts']:.1f} | {a['silence_gap']:+.2f} | {a['public_query']:.2f} | {a['backfire_after']:.1f} | {a['final_public_support']:.2f}")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    labels = [MODE_LABEL[m] for m in MODE_ORDER]
    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    panels = [
        ("rumor_support_acts", "Rumor spread (public support acts)  ↓ better", "#e53e3e"),
        ("silence_gap", "Silence gap (private doubt not voiced)  ↓ better", "#dd6b20"),
        ("public_query", "Publicly voiced skepticism  ↑ better", "#2b6cb0"),
    ]
    for ax, (k, title, color) in zip(axes, panels):
        vals = [agg[m][k] for m in MODE_ORDER]
        bars = ax.bar(labels, vals, color=color, alpha=0.85)
        ax.set_title(title, fontsize=11)
        ax.tick_params(axis="x", rotation=25, labelsize=9)
        ax.grid(axis="y", alpha=0.3)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, f"{v:.2f}", ha="center", va="bottom", fontsize=9)
    plt.suptitle("Governance strategy comparison (rumor spread / silence spiral)", fontsize=13)
    out = os.path.join(HERE, "runs", "governance_comparison.png")
    plt.tight_layout(); plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"\n对照图已保存: {out}")


if __name__ == "__main__":
    asyncio.run(main())
