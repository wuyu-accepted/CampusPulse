#!/usr/bin/env python3
"""Freeze the 80-Agent relationship/private-chat ablation experiment.

The preflight is deliberately blocked until the 200-Agent census public gate
passes.  Building it performs no network I/O and never reads credentials.
Record-level panel membership stays under ``data/private``.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from envs.campus_pulse_governance.mass_population import canonical_hash  # noqa: E402


PANEL_SIZE = 80
TICKS = tuple(range(3, 9))
CONDITIONS = (
    {
        "condition": "full_system",
        "private_chat_enabled": True,
        "relationship_attention_enabled": True,
        "cross_group_bridge_enabled": True,
    },
    {
        "condition": "private_chat_disabled",
        "private_chat_enabled": False,
        "relationship_attention_enabled": True,
        "cross_group_bridge_enabled": True,
    },
    {
        "condition": "relationship_attention_disabled",
        "private_chat_enabled": True,
        "relationship_attention_enabled": False,
        "cross_group_bridge_enabled": True,
    },
    {
        "condition": "cross_group_bridge_disabled",
        "private_chat_enabled": True,
        "relationship_attention_enabled": True,
        "cross_group_bridge_enabled": False,
    },
)
RESIDENT_SLOTS = PANEL_SIZE * len(TICKS) * len(CONDITIONS)
ACTOR_SLOTS = 2 * 3 * len(CONDITIONS)  # Tick 3 and Tick 7, three actors.
PRIMARY_SLOTS = RESIDENT_SLOTS + ACTOR_SLOTS
REQUEST_LIMIT = PRIMARY_SLOTS * 2
TOKEN_LIMIT = 20_000_000


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def _write_json(path: Path, value: Any) -> None:
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


def _select_panel(members: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if len(members) != 200:
        raise ValueError("ablation selection requires the frozen 200-Agent panel")
    strata: dict[tuple[str, str, bool, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in members:
        key = (
            str(row.get("macro_role") or ""),
            str(row.get("micro_role") or ""),
            bool(row.get("directly_affected")),
            str(row.get("activity_bin") or ""),
        )
        strata[key].append(row)
    ordered: list[Mapping[str, Any]] = []
    queues = {
        key: sorted(
            rows,
            key=lambda row: canonical_hash(
                ["relationship-ablation-panel-v1", row["agent_id"]]
            ),
        )
        for key, rows in strata.items()
    }
    keys = sorted(queues, key=lambda key: canonical_hash(["stratum", *key]))
    while len(ordered) < PANEL_SIZE:
        progressed = False
        for key in keys:
            if queues[key]:
                ordered.append(queues[key].pop(0))
                progressed = True
                if len(ordered) == PANEL_SIZE:
                    break
        if not progressed:
            break
    if len(ordered) != PANEL_SIZE or len({row["agent_id"] for row in ordered}) != PANEL_SIZE:
        raise ValueError("ablation panel selection did not produce 80 unique agents")
    return [dict(row) for row in sorted(ordered, key=lambda row: row["agent_id"])]


def build(
    *,
    census_panel_path: Path,
    census_plan_path: Path,
    census_result_path: Path,
    private_output_dir: Path,
    public_output_path: Path,
    endpoint: str,
    model: str,
) -> dict[str, Any]:
    census_panel = _read_object(census_panel_path)
    census_plan = _read_object(census_plan_path)
    members = census_panel.get("members")
    if (
        census_panel.get("schema_version") != "campus-pulse-forum-census-panel-v1"
        or census_plan.get("schema_version") != "campus-pulse-forum-census-plan-v1"
        or census_plan.get("panel_sha256") != census_panel.get("panel_sha256")
        or not isinstance(members, list)
    ):
        raise ValueError("ablation inputs are not bound to the census panel")
    panel = _select_panel(members)
    panel_body = {
        "schema_version": "campus-pulse-forum-relationship-ablation-panel-v1",
        "source_census_panel_sha256": census_panel["panel_sha256"],
        "panel_size": PANEL_SIZE,
        "members": panel,
    }
    panel_release = {
        **panel_body,
        "panel_sha256": canonical_hash(panel_body),
    }
    census_gate = None
    if census_result_path.is_file():
        census_gate = _read_object(census_result_path)
    census_passed = bool(
        census_gate
        and census_gate.get("schema_version")
        == "campus-pulse-forum-census-public-result-v1"
        and census_gate.get("plan_sha256") == census_plan.get("plan_sha256")
        and census_gate.get("panel_sha256") == census_panel.get("panel_sha256")
        and census_gate.get("status") == "pass"
    )
    plan_body = {
        "schema_version": "campus-pulse-forum-relationship-ablation-plan-v1",
        "parent_contract": "campus-pulse-run-plan-v9",
        "scenario_id": "governance_legitimacy_dispute",
        "branch": "D",
        "seed": 20260722,
        "panel_sha256": panel_release["panel_sha256"],
        "source_census_plan_sha256": census_plan["plan_sha256"],
        "source_census_result_sha256": (
            census_gate.get("public_result_sha256") if census_passed else None
        ),
        "ticks": list(TICKS),
        "conditions": list(CONDITIONS),
        "resident_slots": RESIDENT_SLOTS,
        "governance_actor_slots": ACTOR_SLOTS,
        "primary_slots": PRIMARY_SLOTS,
        "shared_population_event_baseline_and_exogenous_randomness": True,
        "resident_turn_schema_version": "campus-pulse-forum-agent-turn-v3",
        "dynamics_generates_language": False,
        "metrics": [
            "semantic_diversity",
            "persona_consistency",
            "friend_tone_naturalness",
            "direct_context_uptake",
            "private_to_public_spillover",
            "claim_drift",
            "cross_group_reach",
            "correction_penetration",
            "cascade_depth",
            "template_rate",
        ],
    }
    plan = {**plan_body, "plan_sha256": canonical_hash(plan_body)}
    preflight_body = {
        "schema_version": "campus-pulse-forum-relationship-ablation-preflight-v1",
        "plan_sha256": plan["plan_sha256"],
        "panel_sha256": panel_release["panel_sha256"],
        "endpoint": endpoint,
        "model": model,
        "reasoning": "none",
        "primary_slots": PRIMARY_SLOTS,
        "request_hard_limit": REQUEST_LIMIT,
        "token_hard_limit": TOKEN_LIMIT,
        "format_repair_limit": 1,
        "network_retry_limit": 0,
        "maximum_concurrency": 32,
        "ready": census_passed,
        "blocking_gates": [] if census_passed else ["census_calibration_not_passed"],
        "contains_api_key": False,
    }
    preflight = {
        **preflight_body,
        "preflight_sha256": canonical_hash(preflight_body),
    }
    _write_json(private_output_dir / "ablation-panel-80.private.json", panel_release)
    _write_json(private_output_dir / "ablation-plan-v1.private.json", plan)
    _write_json(private_output_dir / "ablation-preflight-v1.json", preflight)
    public_body = {
        "schema_version": "campus-pulse-forum-relationship-ablation-preflight-public-v1",
        "scenario": "校园稀缺公共资源分配正当性争议",
        "panel_size": PANEL_SIZE,
        "ticks": [3, 4, 5, 6, 7, 8],
        "conditions": [row["condition"] for row in CONDITIONS],
        "resident_slots": RESIDENT_SLOTS,
        "governance_actor_slots": ACTOR_SLOTS,
        "primary_slots": PRIMARY_SLOTS,
        "request_hard_limit": REQUEST_LIMIT,
        "token_hard_limit": TOKEN_LIMIT,
        "model": model,
        "reasoning": "none",
        "plan_sha256": plan["plan_sha256"],
        "panel_sha256": panel_release["panel_sha256"],
        "preflight_sha256": preflight["preflight_sha256"],
        "ready": census_passed,
        "blocking_gates": preflight["blocking_gates"],
        "execution_status": "not_started",
        "provider_calls": 0,
        "contains_agent_ids": False,
        "contains_private_messages": False,
        "contains_credentials": False,
    }
    public = {
        **public_body,
        "public_summary_sha256": canonical_hash(public_body),
    }
    _write_json(public_output_path, public)
    return public


def main() -> None:
    private_root = Path("data/private/campus-pulse/forumtwin-v2")
    census_root = private_root / "census-200"
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--census-panel",
        type=Path,
        default=census_root / "census-panel-200.private.json",
    )
    parser.add_argument(
        "--census-plan",
        type=Path,
        default=census_root / "census-plan-v1.private.json",
    )
    parser.add_argument(
        "--census-result",
        type=Path,
        default=Path("evidence/forumtwin-v2-census/census-result-public-v1.json"),
    )
    parser.add_argument(
        "--private-output-dir",
        type=Path,
        default=private_root / "relationship-ablation-80",
    )
    parser.add_argument(
        "--public-output",
        type=Path,
        default=Path(
            "evidence/forumtwin-v2-census/relationship-ablation-preflight-public-v1.json"
        ),
    )
    parser.add_argument(
        "--endpoint", default="https://yunwu.ai/v1/chat/completions"
    )
    parser.add_argument("--model", default="gpt-5.6-luna")
    args = parser.parse_args()
    result = build(
        census_panel_path=args.census_panel,
        census_plan_path=args.census_plan,
        census_result_path=args.census_result,
        private_output_dir=args.private_output_dir,
        public_output_path=args.public_output,
        endpoint=args.endpoint,
        model=args.model,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
