#!/usr/bin/env python3
"""Publish aggregate-only evidence for the completed 99-slot diagnostic.

The publisher consumes only the already-redacted public summary.  It emits a
human-readable report and an SVG story board; neither artifact contains agent
identifiers, private messages, prompts, provider bodies, or relationship edges.
"""

from __future__ import annotations

import argparse
import hashlib
from html import escape
import json
from pathlib import Path
import shutil
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from envs.campus_pulse_governance.mass_population import canonical_hash


DEFAULT_SUMMARY = (
    ROOT
    / "_run"
    / "persona-four-dimension-99-v2"
    / "authorized-live"
    / "public-four-dimension-summary-v1.json"
)
DEFAULT_OUTPUT = ROOT / "evidence" / "persona-four-dimension-99-v1"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("public summary must be one JSON object")
    expected = str(value.get("summary_sha256") or "")
    actual = canonical_hash(
        {key: child for key, child in value.items() if key != "summary_sha256"}
    )
    if expected != actual:
        raise ValueError("public summary hash mismatch")
    privacy = value.get("privacy") or {}
    if any(bool(item) for item in privacy.values()):
        raise ValueError("public summary privacy boundary is not clean")
    return value


def _report(data: Mapping[str, Any]) -> str:
    natural = data["branches"]["Natural"]
    governed = data["branches"]["D"]
    delta = data["paired_D_minus_Natural"]
    audit = data["execution_audit"]
    profile = data["persona_input_gate"]
    full = profile["full_natural_profile"]
    ablated = profile["context_ablation"]
    blind = profile.get("blind_prescreen") or {}
    uptake = governed["governance"]["strict_direct_uptake"]
    natural_risk = natural["risk_signals"]
    governed_risk = governed["risk_signals"]
    return "\n".join(
        [
            "# 99 槽四维诊断：结果说明",
            "",
            "## 做了什么",
            "",
            "同一场暑期住宿床位争议进入两个共享起点的平行论坛：Natural 不新增治理，D 在有限观测下由三个 LLM 治理主体分别选择证据更新、服务通道和跨群触达。整次实验完成 96 个居民 LLM 槽位和 3 个治理 LLM 槽位；普通 Dynamics 没有生成任何公开帖子。",
            "",
            "## 发生了什么",
            "",
            f"- 完整人物输入下，12 人中 {full['public_action_count']} 人公开表达，安全消融条件为 {ablated['public_action_count']} 人；处境承接分别为 {full['situation_term_uptake_count']} 与 {ablated['situation_term_uptake_count']}；私聊动作分别为 {full['private_action_count']} 与 {ablated['private_action_count']}。完整输入的文本平均相似度从 {ablated['mean_pair_text_similarity']:.3f} 降至 {full['mean_pair_text_similarity']:.3f}，方向上更有差异，但严格四选一人物盲审仍待独立完成。",
            (
                f"- 内部盲筛在封存判断后才读取答案：四选一识别 "
                f"{blind.get('four_choice_correct_count', 0)}/{blind.get('four_choice_row_count', 12)}，"
                f"未达 9/12 门槛；但配对比较中完整人物输入被判为更好 "
                f"{(blind.get('paired_preference_counts') or {}).get('full_natural_profile', 0)}/12。"
                "该结果由内部助手预审得出，不是独立人类正式盲审。"
            ),
            f"- 到 Tick 6，Natural 形成 {natural['final_counts']['message_count']} 条消息、{natural['final_counts']['reply_count']} 条回复和 {natural['final_counts']['interaction_count']} 次互动；D 分支形成 {governed['final_counts']['message_count']} 条消息、{governed['final_counts']['reply_count']} 条回复和 {governed['final_counts']['interaction_count']} 次互动。治理没有把论坛简单压静，而是让更多质疑进入可追踪的讨论串。",
            f"- 直接受影响群体的 concern 为 {100 * governed['population_heterogeneity']['stake_groups']['directly_affected']['state_means']['concern']:.1f}%，有替代方案群体为 {100 * governed['population_heterogeneity']['stake_groups']['alternatives_available']['state_means']['concern']:.1f}%，两类利益位置的最大状态差为 {100 * governed['population_heterogeneity']['maximum_between_stake_group_gap']:.1f} 个百分点。",
            f"- D 分支把服务积压/SLA 风险从 {natural_risk['help_backlog_and_sla_breach']['value']:.2f} 降至 {governed_risk['help_backlog_and_sla_breach']['value']:.2f}，争议 Claim 占比从 {natural_risk['contested_claim_share']['value']:.3f} 降至 {governed_risk['contested_claim_share']['value']:.3f}，治理观测差从 {natural_risk['governance_observation_gap']['value']:.3f} 降至 {governed_risk['governance_observation_gap']['value']:.3f}。",
            f"- 同时，跨群曝光差从 {natural_risk['cross_group_exposure_gap']['value']:.3f} 升至 {governed_risk['cross_group_exposure_gap']['value']:.3f}；三条治理消息只有 {uptake['messages_with_direct_uptake']} 条在短时间窗内得到严格直接回复。证据卡得到 3 条直接质疑，没有形成接受纠错。这说明‘把信息发出来’不等于‘信息已经穿透并被相信’。",
            (
                "- 行为丰富度仍是当前主要短板：公开主动作已有发帖、回复和求助，"
                f"但 Natural 和 D 的独立互动均只观察到点赞（"
                f"{natural['interaction_counts'].get('like', 0)} / "
                f"{governed['interaction_counts'].get('like', 0)}），"
                "未观察到转发、举报、小群或私聊回流公开论坛。"
                "这些缺口不被当成零发生率，而被定位为当时 Prompt 没有把互动与私聊机会讲清。"
            ),
            "",
            "## 治理意义",
            "",
            "这次推演没有预设治理必胜。它暴露出一个可操作的决策顺序：先用服务工单关闭可处理的积压，再让证据卡明确材料清单、更新时间和复核入口，最后针对仍未触达的利益群体做桥接；否则公开解释可能增加可追责讨论，却仍留下跨群传播盲区。",
            "",
            "## 证据边界",
            "",
            "这是 99 槽、Tick 3–6 的短时诊断，不是正式多种子政策效果结论。论坛生态只在 13 个小面板线程上与全量语料聚合分布对照；内部盲筛不替代独立人类盲审。上述 99 槽结果仍绑定旧渲染器；本次修复的自然行为机会输入属于新模型版本，需在后续小样本 live 对照中单独验证。",
            "",
            "## 运行审计",
            "",
            f"- 成功主槽：{data['completed_primary_slots']}（居民 {data['resident_slots']}，治理 {data['governance_actor_slots']}）",
            f"- Provider 请求：{audit['provider_requests_completed']} / {audit['request_hard_limit']}",
            f"- Provider 报告 Token：{audit['provider_reported_tokens']:,}",
            f"- 保守计入 unknown 风险后的 Token 暴露：{audit['conservative_token_exposure']:,} / {audit['token_hard_limit']:,}",
            f"- 未解决 unknown：{audit['unresolved_unknown_outcomes']}",
            f"- 公共摘要 SHA-256：`{data['summary_sha256']}`",
            "",
        ]
    )


def _story_svg(data: Mapping[str, Any]) -> str:
    natural = data["branches"]["Natural"]
    governed = data["branches"]["D"]
    full = data["persona_input_gate"]["full_natural_profile"]
    ablated = data["persona_input_gate"]["context_ablation"]
    natural_risk = natural["risk_signals"]
    governed_risk = governed["risk_signals"]
    groups = governed["population_heterogeneity"]["stake_groups"]

    colors = {
        "bg": "#100A0D",
        "panel": "#1B1317",
        "panel2": "#21171B",
        "ink": "#FFF8F2",
        "muted": "#BDAFB1",
        "line": "#3C292F",
        "red": "#DF164C",
        "gold": "#E4BF69",
        "green": "#65C18C",
        "blue": "#63A9D9",
        "grey": "#81777A",
    }

    def text(x: float, y: float, value: Any, size: int, color: str, weight: int = 400) -> str:
        return (
            f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" '
            f'fill="{color}">{escape(str(value))}</text>'
        )

    def panel(x: int, y: int, w: int, h: int, number: str, title: str) -> list[str]:
        return [
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="22" fill="{colors["panel"]}" stroke="{colors["line"]}"/>',
            text(x + 30, y + 46, number, 16, colors["red"], 700),
            text(x + 70, y + 48, title, 25, colors["ink"], 650),
        ]

    def horizontal_bar(x: int, y: int, width: int, share: float, color: str) -> str:
        return f'<rect x="{x}" y="{y}" width="{max(4, width * share):.1f}" height="12" rx="6" fill="{color}"/>'

    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1800 1120" width="100%" role="img" aria-labelledby="title desc" preserveAspectRatio="xMidYMid meet">',
        '<title id="title">CampusPulse 99 槽四维治理诊断</title>',
        '<desc id="desc">人物输入、论坛生态、利益群体和治理机制四个维度的真实 LLM 小规模诊断。</desc>',
        '<style>text{font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif}.mono{font-family:Consolas,monospace}</style>',
        f'<rect width="1800" height="1120" fill="{colors["bg"]}"/>',
        '<defs><radialGradient id="halo"><stop offset="0" stop-color="#DF164C" stop-opacity=".35"/><stop offset="1" stop-color="#DF164C" stop-opacity="0"/></radialGradient></defs>',
        '<circle cx="1640" cy="95" r="260" fill="url(#halo)"/>',
        text(72, 65, "CAMPUSPULSE · FORUMTWIN", 17, colors["gold"], 700),
        text(72, 124, "床位减配，不是一句解释就能解决", 43, colors["ink"], 700),
        text(72, 166, "同一事件进入 Natural 与 D 两个平行论坛 · 99 个真实 LLM 槽位 · Tick 3–6", 19, colors["muted"], 450),
        f'<rect x="72" y="194" width="1656" height="88" rx="18" fill="{colors["panel2"]}" stroke="{colors["line"]}"/>',
        text(104, 230, "事件", 16, colors["red"], 700),
        text(104, 262, "暑期可用床位由 520 减至 320，536 份有效申请竞争有限资源；原说明缺少排序、复核、申诉与答复时限。", 21, colors["ink"], 500),
    ]

    parts += panel(72, 312, 806, 310, "01", "人物输入是否真的改变表达？")
    rows = [
        ("公开行动", full["public_action_count"] / 12, ablated["public_action_count"] / 12, f'{full["public_action_count"]}/12', f'{ablated["public_action_count"]}/12'),
        ("处境承接", full["situation_term_uptake_count"] / 12, ablated["situation_term_uptake_count"] / 12, f'{full["situation_term_uptake_count"]}/12', f'{ablated["situation_term_uptake_count"]}/12'),
        ("私下互动", full["private_action_count"] / 12, ablated["private_action_count"] / 12, str(full["private_action_count"]), str(ablated["private_action_count"])),
    ]
    for index, (label, fval, aval, flabel, alabel) in enumerate(rows):
        y = 395 + index * 64
        parts.extend(
            [
                text(104, y, label, 17, colors["muted"], 500),
                horizontal_bar(240, y - 15, 390, fval, colors["red"]),
                horizontal_bar(240, y + 8, 390, aval, colors["grey"]),
                text(650, y - 3, flabel, 17, colors["ink"], 650),
                text(650, y + 20, alabel, 15, colors["muted"]),
            ]
        )
    parts.extend(
        [
            text(104, 588, "完整人物输入", 15, colors["red"], 650),
            text(240, 588, "安全消融", 15, colors["grey"], 650),
            text(405, 588, f"文本相似度 {full['mean_pair_text_similarity']:.3f} vs {ablated['mean_pair_text_similarity']:.3f}（低更好）", 15, colors["muted"]),
        ]
    )

    parts += panel(922, 312, 806, 310, "02", "治理是否只是把论坛压静？")
    forum_rows = [
        ("公开消息", natural["final_counts"]["message_count"], governed["final_counts"]["message_count"]),
        ("直接回复", natural["final_counts"]["reply_count"], governed["final_counts"]["reply_count"]),
        ("互动", natural["final_counts"]["interaction_count"], governed["final_counts"]["interaction_count"]),
        ("有回复线程", round(100 * natural["thread_ecology"]["reply_thread_share"]), round(100 * governed["thread_ecology"]["reply_thread_share"])),
    ]
    max_forum = max(value for _, left, right in forum_rows for value in (left, right))
    for index, (label, left, right) in enumerate(forum_rows):
        y = 395 + index * 47
        parts.extend(
            [
                text(954, y, label, 17, colors["muted"], 500),
                horizontal_bar(1100, y - 17, 420, left / max_forum, colors["grey"]),
                horizontal_bar(1100, y + 3, 420, right / max_forum, colors["red"]),
                text(1542, y - 5, left, 16, colors["muted"], 600),
                text(1603, y + 15, right, 17, colors["ink"], 700),
            ]
        )
    parts.extend(
        [
            text(954, 590, "Natural", 15, colors["grey"], 650),
            text(1060, 590, "D 治理", 15, colors["red"], 650),
            text(1180, 590, "治理后讨论更多，但内容仍保持 44/44 唯一文本", 15, colors["muted"]),
        ]
    )

    parts += panel(72, 654, 806, 300, "03", "同一政策，谁最担心？")
    group_rows = [
        ("直接受影响", groups["directly_affected"]["state_means"]["concern"], colors["red"]),
        ("旁观 / 混合", groups["bystander_or_mixed"]["state_means"]["concern"], colors["gold"]),
        ("有替代方案", groups["alternatives_available"]["state_means"]["concern"], colors["blue"]),
    ]
    for index, (label, value, color) in enumerate(group_rows):
        y = 742 + index * 58
        parts.extend(
            [
                text(104, y, label, 17, colors["muted"], 500),
                horizontal_bar(260, y - 15, 430, value, color),
                text(712, y - 3, f"{100 * value:.1f}%", 19, colors["ink"], 700),
            ]
        )
    parts.extend(
        [
            text(104, 920, "1,000 个父 Agent · 10,000 个状态粒子", 16, colors["muted"]),
            text(470, 920, f"最大利益组差 {100 * governed['population_heterogeneity']['maximum_between_stake_group_gap']:.1f} pp", 16, colors["gold"], 650),
        ]
    )

    parts += panel(922, 654, 806, 300, "04", "治理解决了什么，又留下什么？")
    risk_rows = [
        ("服务积压 / SLA", natural_risk["help_backlog_and_sla_breach"]["value"], governed_risk["help_backlog_and_sla_breach"]["value"]),
        ("争议 Claim 占比", natural_risk["contested_claim_share"]["value"], governed_risk["contested_claim_share"]["value"]),
        ("治理观测差", natural_risk["governance_observation_gap"]["value"], governed_risk["governance_observation_gap"]["value"]),
        ("跨群曝光差", natural_risk["cross_group_exposure_gap"]["value"], governed_risk["cross_group_exposure_gap"]["value"]),
    ]
    for index, (label, left, right) in enumerate(risk_rows):
        y = 735 + index * 48
        change = right - left
        change_color = colors["green"] if change < 0 else colors["red"]
        parts.extend(
            [
                text(954, y, label, 16, colors["muted"], 500),
                text(1188, y, f"{left:.3f}", 17, colors["grey"], 650),
                text(1273, y, "→", 18, colors["muted"]),
                text(1317, y, f"{right:.3f}", 17, colors["ink"], 700),
                text(1432, y, f"{change:+.3f}", 17, change_color, 700),
            ]
        )
    parts.extend(
        [
            text(954, 925, "证据卡收到 3 条直接质疑；3 条治理消息仅 1 条被直接承接", 16, colors["gold"], 600),
        ]
    )

    parts.extend(
        [
            f'<rect x="72" y="984" width="1656" height="84" rx="18" fill="{colors["panel2"]}" stroke="{colors["line"]}"/>',
            text(104, 1022, "核心洞察", 16, colors["red"], 700),
            text(230, 1022, "治理让问题更可处理、更可追责，但公开证据没有自动穿透群体边界。", 22, colors["ink"], 650),
            text(230, 1052, "下一步不是继续堆公告，而是把材料、时限、复核入口与定向桥接连成同一条服务链。", 17, colors["muted"], 500),
        ]
    )
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    summary = _load(args.summary.resolve())
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.summary.resolve(), output_dir / "public-four-dimension-summary-v1.json")
    (output_dir / "FOUR_DIMENSION_99_RESULT.md").write_text(
        _report(summary), encoding="utf-8"
    )
    (output_dir / "four-dimension-story-v1.svg").write_text(
        _story_svg(summary), encoding="utf-8"
    )
    published = (
        "public-four-dimension-summary-v1.json",
        "FOUR_DIMENSION_99_RESULT.md",
        "four-dimension-story-v1.svg",
    )
    (output_dir / "SHA256SUMS.txt").write_text(
        "".join(
            f"{hashlib.sha256((output_dir / name).read_bytes()).hexdigest()}  {name}\n"
            for name in published
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "summary_sha256": summary["summary_sha256"],
                "files": [*published, "SHA256SUMS.txt"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
