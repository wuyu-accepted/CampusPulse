#!/usr/bin/env python3
"""Freeze a low-cost private-channel supplement for a sealed ForumTwin Hero.

This command performs no network I/O and reads no credential.  It binds a
24-Agent panel to the already sealed public Natural/D timeline.  The later
authorized run may add private/group actions only; it cannot mutate, replace,
or retroactively explain the frozen public result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from envs.campus_pulse_governance.mass_population import canonical_hash  # noqa: E402


PANEL_SIZE = 24
SHARED_TICKS = (3, 4)
BRANCH_TICKS = tuple(range(5, 11))
BRANCHES = ("Natural", "D")
RESIDENT_SLOTS = PANEL_SIZE * (
    len(SHARED_TICKS) + len(BRANCHES) * len(BRANCH_TICKS)
)
REQUEST_LIMIT = RESIDENT_SLOTS * 2
TOKEN_LIMIT = 4_000_000
SCENARIO_ID = "century_gym_ghost_booking_dispute"
SCENARIO_LABEL = "世纪馆‘幽灵预约’"


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        encoding="utf-8",
    )


def _select_panel(
    *,
    population: Mapping[str, Any],
    relationships: Mapping[str, Any],
    public_result: Mapping[str, Any],
    branches: tuple[str, ...],
    panel_size: int,
    selection_salt: str,
) -> list[dict[str, str]]:
    agents = list(population.get("agents") or ())
    by_display = {
        str(row.get("synthetic_display_id") or ""): row for row in agents
    }
    speaker_counts: dict[str, int] = {}
    speaker_branches: dict[str, set[str]] = {}
    for branch in branches:
        branch_value = public_result["result"]["branches"][branch]
        for row in branch_value.get("messages", ()):
            display_id = str(row.get("source_agent_display_id") or "")
            if not display_id.startswith("FT-"):
                continue
            speaker_counts[display_id] = speaker_counts.get(display_id, 0) + 1
            speaker_branches.setdefault(display_id, set()).add(branch)
    speakers = set(speaker_counts)
    missing = speakers - set(by_display)
    if missing:
        raise ValueError("sealed public timeline contains displays outside population v2")
    ranked_speakers = sorted(
        speakers,
        key=lambda display_id: (
            -len(speaker_branches.get(display_id, set())),
            -speaker_counts.get(display_id, 0),
            canonical_hash([selection_salt, "speaker", display_id]),
        ),
    )
    public_target = min(len(ranked_speakers), max(1, panel_size // 2))
    selected: set[str] = set()
    represented_macros: set[str] = set()
    for display_id in ranked_speakers:
        macro = str(by_display[display_id].get("primary_macro_role") or "")
        if macro in represented_macros:
            continue
        selected.add(str(by_display[display_id]["agent_id"]))
        represented_macros.add(macro)
        if len(selected) == public_target:
            break
    if len(selected) < public_target:
        for display_id in ranked_speakers:
            selected.add(str(by_display[display_id]["agent_id"]))
            if len(selected) == public_target:
                break
    outgoing: dict[str, list[Mapping[str, Any]]] = {}
    for edge in relationships.get("edges", ()):
        outgoing.setdefault(str(edge["source_agent_id"]), []).append(edge)
    relation_rank = {
        "close_friend": 0,
        "familiar_peer": 1,
        "cross_group_acquaintance": 2,
    }
    frontier = list(sorted(selected))
    while frontier and len(selected) < panel_size:
        source = frontier.pop(0)
        edges = sorted(
            outgoing.get(source, ()),
            key=lambda row: (
                relation_rank.get(str(row.get("relation_type") or ""), 9),
                -float(row.get("strength") or 0.0),
                str(row.get("target_agent_id") or ""),
            ),
        )
        for edge in edges:
            target = str(edge["target_agent_id"])
            if target in selected:
                continue
            selected.add(target)
            frontier.append(target)
            if len(selected) == panel_size:
                break
    if len(selected) < panel_size:
        remainder = sorted(
            (
                str(row["agent_id"])
                for row in agents
                if str(row["agent_id"]) not in selected
            ),
            key=lambda value: canonical_hash([selection_salt, "fallback", value]),
        )
        selected.update(remainder[: panel_size - len(selected)])
    if len(selected) != panel_size:
        raise ValueError("private shadow panel did not produce the requested unique Agents")
    by_agent = {str(row["agent_id"]): row for row in agents}
    return [
        {
            "agent_id": agent_id,
            "synthetic_display_id": str(by_agent[agent_id]["synthetic_display_id"]),
            "macro_role": str(by_agent[agent_id]["primary_macro_role"]),
            "micro_role": str(by_agent[agent_id]["micro_role_id"]),
            "selection_reason": (
                "sealed_public_speaker"
                if str(by_agent[agent_id]["synthetic_display_id"]) in speakers
                else "synthetic_relationship_contact"
            ),
        }
        for agent_id in sorted(selected)
    ]


def _coordination_score(agent: Mapping[str, Any]) -> float:
    tendencies = agent.get("forum_tendencies")
    tendencies = tendencies if isinstance(tendencies, Mapping) else {}

    def value(name: str) -> float:
        raw = tendencies.get(name, 0.0)
        return (
            max(0.0, float(raw))
            if isinstance(raw, (int, float)) and not isinstance(raw, bool)
            else 0.0
        )

    return min(
        1.0,
        value("request")
        + value("help_request")
        + 0.35 * value("information_share")
        + 0.20 * (value("question") + value("discussion")),
    )


def _select_group_lifecycle_panel(
    *,
    population: Mapping[str, Any],
    relationships: Mapping[str, Any],
    public_result: Mapping[str, Any],
    branches: tuple[str, ...],
    panel_size: int,
    selection_salt: str,
) -> list[dict[str, Any]]:
    """Select a small closed subgraph with real group-formation affordances.

    Candidate creators are data-driven public participants with a high reviewed
    coordination tendency.  Each candidate brings at least two of its existing
    synthetic contacts into the panel, so the LLM can actually invite a closed
    set and those contacts can independently accept or decline on later ticks.
    """

    if panel_size < 3:
        raise ValueError("group lifecycle panel requires at least three Agents")
    agents = list(population.get("agents") or ())
    by_id = {str(row["agent_id"]): row for row in agents}
    by_display = {
        str(row.get("synthetic_display_id") or ""): row for row in agents
    }
    speaker_counts: dict[str, int] = {}
    for branch in branches:
        for message in public_result["result"]["branches"][branch].get(
            "messages", ()
        ):
            display_id = str(message.get("source_agent_display_id") or "")
            if display_id in by_display:
                agent_id = str(by_display[display_id]["agent_id"])
                speaker_counts[agent_id] = speaker_counts.get(agent_id, 0) + 1
    outgoing: dict[str, list[Mapping[str, Any]]] = {}
    for edge in relationships.get("edges", ()):
        source = str(edge.get("source_agent_id") or "")
        target = str(edge.get("target_agent_id") or "")
        if source in by_id and target in by_id and source != target:
            outgoing.setdefault(source, []).append(edge)
    relation_rank = {
        "close_friend": 0,
        "familiar_peer": 1,
        "cross_group_acquaintance": 2,
    }
    candidates = sorted(
        (
            agent_id
            for agent_id, edges in outgoing.items()
            if len({str(edge["target_agent_id"]) for edge in edges}) >= 2
            and by_id[agent_id].get("evidence_binding")
            == "reviewed_evidence_match"
        ),
        key=lambda agent_id: (
            0 if agent_id in speaker_counts else 1,
            -_coordination_score(by_id[agent_id]),
            -speaker_counts.get(agent_id, 0),
            canonical_hash([selection_salt, "group-candidate", agent_id]),
        ),
    )
    selected: set[str] = set()
    creator_candidates: set[str] = set()
    candidate_contacts: dict[str, tuple[str, ...]] = {}
    target_creator_count = min(2, max(1, panel_size // 4))
    for creator in candidates:
        if creator in selected:
            continue
        contacts = sorted(
            outgoing.get(creator, ()),
            key=lambda edge: (
                relation_rank.get(str(edge.get("relation_type") or ""), 9),
                -float(edge.get("trust") or 0.0),
                -float(edge.get("strength") or 0.0),
                canonical_hash(
                    [selection_salt, creator, str(edge.get("target_agent_id") or "")]
                ),
            ),
        )
        contact_ids = list(
            dict.fromkeys(
                str(edge["target_agent_id"])
                for edge in contacts
                if str(edge["target_agent_id"]) != creator
                and by_id[str(edge["target_agent_id"])].get("evidence_binding")
                == "reviewed_evidence_match"
            )
        )
        available = [value for value in contact_ids if value not in selected]
        if len(available) < 2:
            continue
        remaining = panel_size - len(selected)
        if remaining < 3:
            break
        close_available = [
            str(edge["target_agent_id"])
            for edge in contacts
            if edge.get("relation_type") == "close_friend"
            and str(edge["target_agent_id"]) in available
        ]
        chosen = (
            list(dict.fromkeys(close_available))[:2]
            if len(set(close_available)) >= 2
            else available[:2]
        )
        if len(chosen) < 2:
            continue
        selected.add(creator)
        selected.update(chosen)
        creator_candidates.add(creator)
        candidate_contacts[creator] = tuple(chosen)
        if len(creator_candidates) >= target_creator_count:
            break
    if not creator_candidates:
        raise ValueError("no group-capable relationship cluster could be selected")
    if len(selected) < panel_size:
        remainder = sorted(
            (
                agent_id
                for agent_id, agent in by_id.items()
                if agent_id not in selected
                and agent.get("evidence_binding") == "reviewed_evidence_match"
            ),
            key=lambda agent_id: (
                0 if agent_id in speaker_counts else 1,
                -_coordination_score(by_id[agent_id]),
                canonical_hash([selection_salt, "fill", agent_id]),
            ),
        )
        for agent_id in remainder:
            selected.add(agent_id)
            if len(selected) == panel_size:
                break
    if len(selected) != panel_size:
        raise ValueError("group lifecycle panel did not produce the requested size")
    return [
        {
            "agent_id": agent_id,
            "synthetic_display_id": str(by_id[agent_id]["synthetic_display_id"]),
            "macro_role": str(by_id[agent_id]["primary_macro_role"]),
            "micro_role": str(by_id[agent_id]["micro_role_id"]),
            "selection_reason": (
                "group_creator_candidate"
                if agent_id in creator_candidates
                else "closed_group_contact"
            ),
            "group_candidate": agent_id in creator_candidates,
            "group_candidate_contact_ids": list(
                candidate_contacts.get(agent_id, ())
            ),
            "coordination_score": round(_coordination_score(by_id[agent_id]), 6),
        }
        for agent_id in sorted(selected)
    ]


def build(
    *,
    population_path: Path,
    relationships_path: Path,
    public_result_path: Path,
    private_output_dir: Path,
    public_output_path: Path,
    endpoint: str,
    model: str,
    scenario_id: str = SCENARIO_ID,
    scenario_label: str = SCENARIO_LABEL,
    frozen_prefix_ticks: tuple[int, ...] = (),
    shared_ticks: tuple[int, ...] = SHARED_TICKS,
    branch_ticks: tuple[int, ...] = BRANCH_TICKS,
    branches: tuple[str, ...] = BRANCHES,
    panel_size: int = PANEL_SIZE,
    selection_mode: str = "standard",
    shadow_focus: str = "organic_private_channel",
    token_limit: int = TOKEN_LIMIT,
    request_limit: int | None = None,
) -> dict[str, Any]:
    population = _read_object(population_path)
    relationships = _read_object(relationships_path)
    public_result = _read_object(public_result_path)
    result = public_result.get("result") or {}
    if (
        len(population.get("agents") or ()) != 1_000
        or relationships.get("population_sha256") != population.get("population_sha256")
        or result.get("scenario_id") != scenario_id
        or result.get("end_tick") < max((*frozen_prefix_ticks, *shared_ticks, *branch_ticks))
        or set((result.get("branches") or {}).keys()) != set(branches)
        or result.get("public_content_provenance_rule")
        != "authorized_live_llm_or_exact_reviewed_trace"
    ):
        raise ValueError("sealed public result or ForumTwin v2 lineage drifted")
    public_bytes_sha256 = hashlib.sha256(public_result_path.read_bytes()).hexdigest()
    if selection_mode not in {"standard", "group_lifecycle"}:
        raise ValueError("private shadow selection mode is invalid")
    if shadow_focus not in {
        "organic_private_channel",
        "group_lifecycle_observation",
    }:
        raise ValueError("private shadow focus is invalid")
    if (selection_mode == "group_lifecycle") != (
        shadow_focus == "group_lifecycle_observation"
    ):
        raise ValueError("group lifecycle selection and shadow focus must be paired")
    selector = (
        _select_group_lifecycle_panel
        if selection_mode == "group_lifecycle"
        else _select_panel
    )
    panel_rows = selector(
        population=population,
        relationships=relationships,
        public_result=public_result,
        branches=branches,
        panel_size=panel_size,
        selection_salt=f"{scenario_id}-private-shadow-panel-v3-{selection_mode}",
    )
    resident_slots = panel_size * (
        len(shared_ticks) + len(branches) * len(branch_ticks)
    )
    resolved_request_limit = request_limit or resident_slots * 2
    if resident_slots <= 0 or resolved_request_limit < resident_slots or token_limit <= 0:
        raise ValueError("private shadow workload and budgets must be positive")
    renderer_source_sha256 = hashlib.sha256(
        (ROOT / "src/envs/campus_pulse_governance/forum_turns.py").read_bytes()
    ).hexdigest()
    runner_source_sha256 = hashlib.sha256(
        (ROOT / "scripts/run_forum_private_shadow_authorized.py").read_bytes()
    ).hexdigest()
    panel_body = {
        "schema_version": "campus-pulse-forum-private-shadow-panel-v1",
        "population_sha256": population["population_sha256"],
        "relationship_release_sha256": relationships["relationship_release_sha256"],
        "frozen_public_result_bytes_sha256": public_bytes_sha256,
        "panel_size": panel_size,
        "selection_mode": selection_mode,
        "group_candidate_count": sum(
            bool(row.get("group_candidate")) for row in panel_rows
        ),
        "members": panel_rows,
    }
    panel = {**panel_body, "panel_sha256": canonical_hash(panel_body)}
    plan_body = {
        "schema_version": "campus-pulse-forum-private-shadow-plan-v1",
        "parent_contract": "campus-pulse-run-plan-v9",
        "resident_turn_schema_version": "campus-pulse-forum-agent-turn-v3",
        "shadow_renderer": "campus-pulse-forum-private-shadow-turn-v5",
        "shadow_focus": shadow_focus,
        "panel_selection_mode": selection_mode,
        "renderer_source_sha256": renderer_source_sha256,
        "runner_source_sha256": runner_source_sha256,
        "scenario_id": scenario_id,
        "scenario_label": scenario_label,
        "seed": int(result["seed"]),
        "frozen_prefix_ticks": list(frozen_prefix_ticks),
        "shared_ticks": list(shared_ticks),
        "branch_ticks": list(branch_ticks),
        "branches": list(branches),
        "panel_sha256": panel["panel_sha256"],
        "frozen_public_result_bytes_sha256": public_bytes_sha256,
        "panel_size": panel_size,
        "resident_slots": resident_slots,
        "governance_actor_slots": 0,
        "primary_slots": resident_slots,
        "public_timeline_is_exogenous_and_immutable": True,
        "public_primary_actions_allowed": ["read", "ignore"],
        "public_engagements_allowed": 0,
        "private_actions_per_agent_tick_max": 2,
        "same_tick_two_phase_commit": True,
        "dynamics_generates_language": False,
        "claim_boundary": (
            "This supplement measures private interpretation, group formation and correction "
            "reach conditioned on the sealed public trace. It cannot claim that the generated "
            "private layer caused the already frozen public Natural/D divergence."
        ),
    }
    plan = {**plan_body, "plan_sha256": canonical_hash(plan_body)}
    preflight_body = {
        "schema_version": "campus-pulse-forum-private-shadow-preflight-v1",
        "plan_sha256": plan["plan_sha256"],
        "panel_sha256": panel["panel_sha256"],
        "endpoint": endpoint,
        "model": model,
        "reasoning": "none",
        "primary_slots": resident_slots,
        "request_hard_limit": resolved_request_limit,
        "token_hard_limit": token_limit,
        "format_repair_limit": 1,
        "network_retry_limit": 0,
        "maximum_concurrency": min(24, panel_size),
        "scope": {
            "scenario_id": scenario_id,
            "panel_size": panel_size,
            "frozen_prefix_ticks": list(frozen_prefix_ticks),
            "shared_ticks": list(shared_ticks),
            "branch_ticks": list(branch_ticks),
            "branches": list(branches),
            "resident_slots": resident_slots,
            "governance_actor_slots": 0,
            "frozen_public_timeline": True,
            "private_channel_only": True,
            "shadow_focus": shadow_focus,
            "panel_selection_mode": selection_mode,
        },
        "ready": True,
        "blocking_gates": [],
        "contains_api_key": False,
    }
    preflight = {**preflight_body, "preflight_sha256": canonical_hash(preflight_body)}
    _write_json(private_output_dir / "private-shadow-panel-v1.private.json", panel)
    _write_json(private_output_dir / "private-shadow-plan-v1.private.json", plan)
    _write_json(private_output_dir / "private-shadow-preflight-v1.json", preflight)
    public_body = {
        "schema_version": "campus-pulse-forum-private-shadow-preflight-public-v1",
        "scenario_id": scenario_id,
        "scenario": scenario_label,
        "purpose": "在冻结公开 Natural/D 轨迹上补充真实 LLM 好友私聊与动态小群",
        "panel_size": panel_size,
        "shadow_focus": shadow_focus,
        "panel_selection_mode": selection_mode,
        "group_candidate_count": panel["group_candidate_count"],
        "frozen_prefix_ticks": list(frozen_prefix_ticks),
        "shared_ticks": list(shared_ticks),
        "branch_ticks": list(branch_ticks),
        "branches": list(branches),
        "primary_slots": resident_slots,
        "request_hard_limit": resolved_request_limit,
        "token_hard_limit": token_limit,
        "model": model,
        "reasoning": "none",
        "preflight_sha256": preflight["preflight_sha256"],
        "ready": True,
        "execution_status": "not_started",
        "provider_calls": 0,
        "public_timeline_will_be_modified": False,
        "causal_effect_on_frozen_public_timeline_claimed": False,
        "contains_agent_ids": False,
        "contains_private_messages": False,
        "contains_credentials": False,
    }
    public = {**public_body, "public_summary_sha256": canonical_hash(public_body)}
    _write_json(public_output_path, public)
    return public


def main() -> None:
    private_root = Path("data/private/campus-pulse/forumtwin-v2/century-gym-private-shadow")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--population",
        type=Path,
        default=Path("data/campus_pulse_workspace/forumtwin/releases/semantic-llm-forum-agent-population-v2.json"),
    )
    parser.add_argument(
        "--relationships",
        type=Path,
        default=Path("data/private/campus-pulse/forumtwin-v2/synthetic-relationship-release-v1.json"),
    )
    parser.add_argument(
        "--public-result",
        type=Path,
        default=Path("evidence/century-gym-live-v1/final/forum-hero-result-through-T10.json"),
    )
    parser.add_argument("--private-output-dir", type=Path, default=private_root)
    parser.add_argument(
        "--public-output",
        type=Path,
        default=Path("evidence/century-gym-demo-v2/private-shadow-preflight-public-v1.json"),
    )
    parser.add_argument("--endpoint", default="https://yunwu.ai/v1/chat/completions")
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--scenario-id", default=SCENARIO_ID)
    parser.add_argument("--scenario-label", default=SCENARIO_LABEL)
    parser.add_argument("--frozen-prefix-ticks", default="")
    parser.add_argument("--shared-ticks", default=",".join(map(str, SHARED_TICKS)))
    parser.add_argument("--branch-ticks", default=",".join(map(str, BRANCH_TICKS)))
    parser.add_argument("--branches", default=",".join(BRANCHES))
    parser.add_argument("--panel-size", type=int, default=PANEL_SIZE)
    parser.add_argument(
        "--selection-mode",
        choices=("standard", "group_lifecycle"),
        default="standard",
    )
    parser.add_argument(
        "--shadow-focus",
        choices=("organic_private_channel", "group_lifecycle_observation"),
        default="organic_private_channel",
    )
    parser.add_argument("--token-limit", type=int, default=TOKEN_LIMIT)
    parser.add_argument("--request-limit", type=int)
    args = parser.parse_args()
    parse_ints = lambda value: tuple(
        int(item.strip()) for item in value.split(",") if item.strip()
    )
    parse_strings = lambda value: tuple(
        item.strip() for item in value.split(",") if item.strip()
    )
    result = build(
        population_path=args.population,
        relationships_path=args.relationships,
        public_result_path=args.public_result,
        private_output_dir=args.private_output_dir,
        public_output_path=args.public_output,
        endpoint=args.endpoint,
        model=args.model,
        scenario_id=args.scenario_id,
        scenario_label=args.scenario_label,
        frozen_prefix_ticks=parse_ints(args.frozen_prefix_ticks),
        shared_ticks=parse_ints(args.shared_ticks),
        branch_ticks=parse_ints(args.branch_ticks),
        branches=parse_strings(args.branches),
        panel_size=args.panel_size,
        selection_mode=args.selection_mode,
        shadow_focus=args.shadow_focus,
        token_limit=args.token_limit,
        request_limit=args.request_limit,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
