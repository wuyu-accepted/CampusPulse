# -*- coding: utf-8 -*-
"""
社交网络演化 GIF：读取 decisions 数据，逐轮渲染网络图并合成 GIF。
节点=agent，颜色=私人立场，边框=公开行为，大小=活跃度(non-silent)。
"""
import json, os, sys, re, glob
from collections import defaultdict
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")

# 立场 → 颜色映射
PRIV_COLORS = {"support": "#e53e3e", "skeptical": "#2b6cb0", "neutral": "#718096", "?": "#ccc"}
PUB_STYLES = {"support": "solid", "query": "dashed", "silent": "dotted"}
PUB_EDGE_COLORS = {"support": "#e53e3e", "query": "#2b6cb0", "silent": "#aaa"}


def _extract(output, key):
    m = re.search(r'"%s"\s*:\s*"([^"]*)"' % key, output or "")
    return m.group(1) if m else ""


def latest_decisions_and_rels():
    fs = sorted(glob.glob(os.path.join(RUNS, "*", "datasets", "decisions_*.json")))
    if not fs:
        raise SystemExit("无 decisions 文件")
    dp = fs[-1]
    data = json.load(open(dp, encoding="utf-8"))
    # 关系网
    rel_path = os.path.join(HERE, "profile", "data", "Relationship.csv")
    edges = []
    if os.path.exists(rel_path):
        import csv
        with open(rel_path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                edges.append((r["source_id"].strip(), r["target_id"].strip()))
    # agent name map
    profile_path = os.path.join(HERE, "profile", "data", "OrdinaryUser.json")
    name_map = {}
    if os.path.exists(profile_path):
        for u in json.load(open(profile_path, encoding="utf-8")):
            name_map[u["id"]] = u.get("name", u["id"])
    return dp, data, edges, name_map


def classify_stances(data):
    """对每条决策做简易 stance 分类(用 action 和文本关键词，不调 LLM)"""
    stances = {}
    latest = {}
    for x in data:
        latest[(x.get("agent_id"), x.get("step", 0))] = x
    for (aid, step), x in latest.items():
        out = x.get("output", "")
        act = _extract(out, "action_type") or "no_action"
        priv_note = _extract(out, "private_note") or ""
        pub_text = _extract(out, "public_text") or ""
        # private stance
        if any(kw in priv_note for kw in ["不信", "怀疑", "官方", "来源", "等等看", "求证", "核实", "确认"]):
            priv = "skeptical"
        elif any(kw in priv_note for kw in ["真的", "宁可信", "小心", "赶紧", "囤"]):
            priv = "support"
        else:
            priv = "neutral"
        # public stance
        if act == "no_action":
            pub = "silent"
        elif act in ("repost", "comment") and any(kw in (pub_text + priv_note) for kw in ["质疑", "官方", "求证", "辟谣", "来源", "真的吗"]):
            pub = "query"
        elif act in ("repost", "comment", "like"):
            pub = "support"
        else:
            pub = "silent"
        stances[(aid, step)] = (priv, pub, act)
    return stances


def draw_frame(G, stances_this_round, step, name_map, total_steps):
    fig, ax = plt.subplots(figsize=(10, 8))
    pos = nx.spring_layout(G, seed=42, k=0.6, iterations=30)
    node_colors, node_sizes, edgecols = [], [], []
    labels = {}
    for n in G.nodes():
        priv, pub, act = stances_this_round.get((str(n), step), ("neutral", "silent", "no_action"))
        node_colors.append(PRIV_COLORS.get(priv, "#ccc"))
        node_sizes.append(300 if act == "no_action" else 600)
        labels[n] = name_map.get(str(n), str(n))[:4]
        edgecols.append(PUB_EDGE_COLORS.get(pub, "#aaa"))
    nx.draw_networkx_edges(G, pos, alpha=0.15, edge_color="#ccc", ax=ax)
    nx.draw_networkx_nodes(G, pos, node_color=node_colors, node_size=node_sizes, alpha=0.9, ax=ax)
    nx.draw_networkx_labels(G, pos, labels, font_size=6, ax=ax)
    # legend
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    legend_elements = [
        Patch(facecolor="#e53e3e", label="Private: believe/support"),
        Patch(facecolor="#2b6cb0", label="Private: skeptical"),
        Patch(facecolor="#718096", label="Private: neutral"),
        Line2D([0], [0], marker='o', color='w', markerfacecolor='w', markeredgecolor="#e53e3e", markeredgewidth=2, label="Public: support voiced"),
        Line2D([0], [0], marker='o', color='w', markerfacecolor='w', markeredgecolor="#aaa", markeredgewidth=1, markersize=8, label="Public: silent"),
    ]
    ax.legend(handles=legend_elements, loc="lower left", fontsize=7)
    ax.set_title(f"Round {step}/{total_steps} — Social network + private vs public stance", fontsize=12)
    ax.axis("off")
    plt.tight_layout()
    fig.canvas.draw()
    img = np.frombuffer(fig.canvas.buffer_rgba(), dtype=np.uint8)
    img = img.reshape((fig.canvas.get_width_height()[::-1] + (4,)))[:,:,:3]
    plt.close(fig)
    return Image.fromarray(img)


def main():
    dp, data, edges, name_map = latest_decisions_and_rels()
    steps = sorted(set(x.get("step", 1) for x in data))
    stances = classify_stances(data)
    print(f"数据: {os.path.basename(dp)}, {len(set(x.get("agent_id") for x in data))} agent, {len(steps)} rounds")

    G = nx.Graph()
    agent_ids = set()
    for x in data:
        aid = x.get("agent_id")
        atype = x.get("agent_type", "")
        if aid and "Ordinary" in atype or "Leader" in atype:
            agent_ids.add(aid)
    G.add_nodes_from(agent_ids)
    for s, t in edges:
        if s in agent_ids and t in agent_ids:
            G.add_edge(s, t)

    frames = []
    for step in steps:
        img = draw_frame(G, stances, step, name_map, max(steps))
        frames.append(img)
        print(f"  round {step} frame rendered")

    out = os.path.join(HERE, "network_evolution.gif")
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=800, loop=0)
    print(f"GIF 已保存: {out} ({len(frames)} frames)")


if __name__ == "__main__":
    main()
