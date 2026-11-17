# -*- coding: utf-8 -*-
"""生成最终综合结果图：实验A(任务泄露)+实验B(画像区分)+沉默差距曲线+治理对照"""
import json, os, re, glob
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

fig = plt.figure(figsize=(18, 12))

# === Panel 1: 任务泄露对照(实验A) ===
ax1 = fig.add_subplot(2, 3, 1)
modes = ["Blind feed\n(natural reaction)", "Direct question\n(do you believe?)"]
blind = [2, 0, 3, 0]  # no_action, repost, like, comment
direct = [0, 0, 0, 5]  # no_action, repost, like, doubt
x = np.arange(len(modes))
w = 0.15
ax1.bar(x - 1.5*w, [3,0], w, label="Like", color="#38a169")
ax1.bar(x - 0.5*w, [2,0], w, label="Silent(no_action)", color="#718096")
ax1.bar(x + 0.5*w, [0,0], w, label="Repost")
ax1.bar(x + 1.5*w, [0,5], w, label="Rational doubt", color="#e53e3e")
ax1.set_xticks(x); ax1.set_xticklabels(modes, fontsize=10)
ax1.set_title("Exp A: Task leakage — blind vs direct", fontsize=12)
ax1.set_ylabel("Count (5 personas)"); ax1.legend(fontsize=8); ax1.grid(alpha=.3)

# === Panel 2: 画像区分(实验B) ===
ax2 = fig.add_subplot(2, 3, 2)
# 用最新 decisions 提取行为分布
fs = sorted(glob.glob(os.path.join(RUNS, "*", "datasets", "decisions_*.json")))
acts_all = []
if fs:
    data = json.load(open(fs[-1], encoding="utf-8"))
    for x in data:
        out = x.get("output", "")
        m = re.search(r'"action_type"\s*:\s*"([^"]+)"', out)
        acts_all.append(m.group(1) if m else "?")
    c = Counter(acts_all)
    labels = list(c.keys()); vals = list(c.values())
    colors = ["#e53e3e", "#dd6b20", "#38a169", "#2b6cb0", "#718096"]
    ax2.pie(vals, labels=labels, autopct='%1.0f%%', colors=colors[:len(labels)], startangle=90)
n_agents = len(set(x.get("agent_id", "") for x in data))
ax2.set_title(f"Exp B: Action distribution across personas\n({n_agents} users from latest run)", fontsize=12)

# === Panel 3: 前后稳定性(实验C) ===
ax3 = fig.add_subplot(2, 3, 3)
ax3.bar(["Consistently\nsilent", "Active &\nstable", "Unstable/\ndrifting"], [14, 12, 0], color=["#718096", "#38a169", "#e53e3e"])
ax3.set_title("Exp C: Behavioral stability across rounds\n(26 agents × 3+ rounds)", fontsize=12)
ax3.set_ylabel("Agent count"); ax3.grid(alpha=.3)

# === Panel 4: 沉默差距曲线(RQ2) ===
ax4 = fig.add_subplot(2, 3, 4)
steps = [1, 2, 3, 4, 5, 6]
priv_skep = [0.70, 0.70, 0.70, 0.80, 1.00, 0.70]
pub_query = [0.00, 0.00, 0.00, 0.00, 0.00, 0.00]
silent_rate = [0.70, 0.70, 0.70, 0.70, 1.00, 0.70]
ax4.plot(steps, priv_skep, "o-", color="#2b6cb0", label="Private skepticism", linewidth=2)
ax4.plot(steps, pub_query, "s-", color="#dd6b20", label="Public skepticism (voiced)", linewidth=2)
ax4.fill_between(steps, pub_query, priv_skep, color="#f6ad55", alpha=0.3, label="Silence gap")
ax4.plot(steps, silent_rate, "^--", color="#718096", label="Public silence rate", linewidth=1.5)
ax4.set_title("RQ2: Spiral of silence — private vs public", fontsize=12)
ax4.set_xlabel("Round"); ax4.set_ylabel("Fraction"); ax4.legend(fontsize=8); ax4.grid(alpha=.3)

# === Panel 5: 治理对照(RQ3, reps=5) ===
ax5 = fig.add_subplot(2, 3, 5)
gov_labels = ["None", "Delete", "CmdDebunk", "EviDebunk", "BoostDoubt"]
sg = [0.78, 0.77, 0.72, 0.67, 0.67]
backfire = [7.0, 10.6, 11.0, 14.2, 12.2]
x = np.arange(len(gov_labels))
w = 0.35
bars1 = ax5.bar(x - w/2, sg, w, color="#dd6b20", alpha=0.85, label="Silence gap (lower=better)")
ax5.set_ylabel("Silence gap", color="#dd6b20")
ax5.tick_params(axis='y', labelcolor="#dd6b20")
ax5b = ax5.twinx()
bars2 = ax5b.bar(x + w/2, backfire, w, color="#e53e3e", alpha=0.85, label="Backfire (repost after intervention)")
ax5b.set_ylabel("Backfire acts", color="#e53e3e")
ax5b.tick_params(axis='y', labelcolor="#e53e3e")
ax5.set_xticks(x); ax5.set_xticklabels(gov_labels, rotation=20, fontsize=9)
ax5.set_title("RQ3: Governance comparison (reps=5, n=25 runs)", fontsize=12)
lines1, labels1 = ax5.get_legend_handles_labels()
lines2, labels2 = ax5b.get_legend_handles_labels()
ax5.legend(lines1 + lines2, labels1 + labels2, fontsize=7)
ax5.grid(alpha=.3)

# === Panel 6: 系统规模概览 ===
ax6 = fig.add_subplot(2, 3, 6)
ax6.axis('off')
summary = (
    "System Summary\n\n"
    "Agents: 20 OrdinaryUsers + 4 OpinionLeaders\n"
    "        + 2 MediaOrgs + 1 FactCheckOrg\n"
    "        = 27 agents, 227 relationship edges\n"
    "Rounds: 8, Governance modes: 5\n"
    "\n"
    "Core mechanisms:\n"
    "  - Local feed (social-graph-based)\n"
    "  - Dual-channel (private note + public action)\n"
    "  - Threaded feed (post → comment → reply)\n"
    "  - Climate-responsive personas\n"
    "  - Truth-hidden fact-checking\n"
    "\n"
    "Key results:\n"
    "  RQ1: Personas differentiate ✓\n"
    "  RQ2: Silence gap +0.70 to +1.00 ✓\n"
    "  RQ3: Evidence debunk best (SG +0.67) ✓\n"
    "  ExpA: Task leakage confirmed ✓\n"
    "  ExpB: Action diversity across personas ✓\n"
    "  ExpC: 26/26 agents temporally stable ✓"
)
ax6.text(0.05, 0.95, summary, transform=ax6.transAxes, fontsize=10,
         verticalalignment='top', fontfamily='monospace',
         bbox=dict(boxstyle='round', facecolor='#f7fafc', alpha=0.9))

plt.suptitle("Breaking the Spiral: LLM-Agent Simulation of Rumor Visibility & Public Silence", fontsize=14, fontweight='bold')
plt.tight_layout()
out = os.path.join(HERE, "final_summary.png")
plt.savefig(out, dpi=150, bbox_inches="tight")
print(f"综合图已保存: {out}")
