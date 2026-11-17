#!/usr/bin/env python3
"""Audit the current natural resident view without calling a Provider.

The audit reuses the frozen 12-Agent panel and exact Tick-3 world from the
99-slot diagnostic design.  A deterministic contract exerciser is used only
to let the runtime complete; generated fixture speech is never scored or
published.  The artifact reports aggregate properties of the model-visible
opportunity set and contains no prompt, Agent ID, relationship edge or text.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
import sys

for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from build_forum_persona_stage_a_preflight import (  # noqa: E402
    ANCHOR_COUNT,
    PANEL_SIZE,
)
from envs.campus_pulse_governance.forum_ecology_calibration import (  # noqa: E402
    validate_forum_ecology_calibration,
)
from envs.campus_pulse_governance.forum_hero import (  # noqa: E402
    run_forum_hero_experiment,
)
from envs.campus_pulse_governance.forum_pilot import (  # noqa: E402
    COST_AWARE_ACTIVATION_POLICY,
    FULL_SYSTEM_ABLATION_MODE,
    GOVERNANCE_LEGITIMACY_SCENARIO,
    OPEN_CHOICE_MECHANISM_PROTOCOL,
    PILOT_SEED,
    compile_pilot_profile_bridge,
)
from envs.campus_pulse_governance.forum_relationships import (  # noqa: E402
    compile_scenario_stake_release_v1,
    compile_synthetic_relationship_release_v1,
)
from envs.campus_pulse_governance.forum_turns import (  # noqa: E402
    FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
    V5_FULL_NATURAL_PROFILE,
)
from envs.campus_pulse_governance.mass_population import canonical_hash  # noqa: E402
from run_forum_four_dimension_structural_fixture import (  # noqa: E402
    FIXTURE_MODEL,
    FIXTURE_TRACE_SHA256,
    StructuralSemanticSource,
)


DEFAULT_POPULATION = (
    ROOT
    / "data/campus_pulse_workspace/forumtwin/releases/hero-pilot-bridge-v6.json"
)
DEFAULT_FORMAL_POPULATION = (
    ROOT
    / "data/campus_pulse_workspace/forumtwin/releases/semantic-llm-forum-agent-population-v2.json"
)
DEFAULT_ECOLOGY = (
    ROOT
    / "data/campus_pulse_workspace/forumtwin/releases/forum-ecology-calibration-v2.json"
)
DEFAULT_PLAN = ROOT / "_run/persona-four-dimension-99-v2/plan.private.json"
DEFAULT_OUTPUT = (
    ROOT
    / "evidence/persona-opportunity-renderer-v1/renderer-opportunity-audit-v1.json"
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain one JSON object")
    return value


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(payload + b"\n")
        temporary = Path(handle.name)
    temporary.replace(path)


class OpportunityAuditSource:
    """Observe aggregate prompt affordances, then delegate to a test source."""

    provenance = "reviewed_trace_replay"
    formal_evidence = False
    max_concurrency = 1

    def __init__(self) -> None:
        self.delegate = StructuralSemanticSource()
        self.rows: list[dict[str, Any]] = []

    def acquire(self, **kwargs: Any) -> Any:
        rendered = kwargs.get("rendered")
        if kwargs.get("slot_kind") == "resident" and rendered is not None:
            prompt = json.loads(str(rendered.agent_prompt))
            serialized = str(rendered.agent_prompt)
            resident = prompt.get("resident_context") or {}
            actions = prompt.get("forum_action_context") or {}
            private = (prompt.get("private_channel") or {}).get(
                "current_private_opportunity"
            ) or {}
            group = private.get("small_group") or {}
            situation = str(
                (prompt.get("scenario_context") or {}).get(
                    "your_current_situation"
                )
                or ""
            )
            self.rows.append(
                {
                    "tick": int(rendered.tick),
                    "typical_thread_entry": str(
                        resident.get("typical_thread_entry") or ""
                    ),
                    "visible_opportunity_count": len(
                        actions.get("visible_message_opportunities") or ()
                    ),
                    "parallel_choices_present": bool(
                        actions.get("parallel_choices")
                    ),
                    "like_rule_present": bool(actions.get("like")),
                    "repost_rule_present": bool(actions.get("repost")),
                    "report_rule_present": bool(actions.get("report")),
                    "private_motive_count": len(private.get("possible_motives") or ()),
                    "private_contact_count": len(
                        private.get("contact_agent_ids") or ()
                    ),
                    "small_group_plausible": bool(group.get("plausible_now")),
                    "housing_position_explicit": (
                        "申请暑期床位" in situation
                        or "不申请床位" in situation
                    ),
                    "forbidden_internal_material_absent": not any(
                        token in serialized
                        for token in (
                            "profile_prompt_sha256",
                            "reviewed_evidence_refs",
                            "profile_bundle_id",
                            "micro_role_id",
                            "posterior_state",
                            "current_synthetic_stake",
                        )
                    ),
                }
            )
        return self.delegate.acquire(**kwargs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", type=Path, default=DEFAULT_POPULATION)
    parser.add_argument(
        "--formal-population", type=Path, default=DEFAULT_FORMAL_POPULATION
    )
    parser.add_argument("--ecology", type=Path, default=DEFAULT_ECOLOGY)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    population = _load(args.population.resolve())
    formal_population = _load(args.formal_population.resolve())
    ecology = _load(args.ecology.resolve())
    plan = _load(args.plan.resolve())
    validate_forum_ecology_calibration(ecology)
    if plan.get("scenario_id") != GOVERNANCE_LEGITIMACY_SCENARIO:
        raise ValueError("opportunity audit requires the frozen housing panel")
    profile_release = compile_pilot_profile_bridge(population)
    if len(profile_release.get("profiles") or ()) != 1_000:
        raise ValueError("opportunity audit requires the frozen 1,000-Agent world")
    stakes = compile_scenario_stake_release_v1(
        population=formal_population,
        scenario_ids=(GOVERNANCE_LEGITIMACY_SCENARIO,),
    )
    relationships = compile_synthetic_relationship_release_v1(
        population=formal_population,
        stake_release=stakes,
    )
    source = OpportunityAuditSource()
    result = run_forum_hero_experiment(
        population_release=population,
        resident_source=source,
        governance_source=source,
        expected_model=FIXTURE_MODEL,
        scenario_id=GOVERNANCE_LEGITIMACY_SCENARIO,
        seed=PILOT_SEED,
        start_tick=3,
        end_tick=4,
        branches=("Natural",),
        resident_budget_by_tick={3: PANEL_SIZE, 4: PANEL_SIZE},
        reviewed_trace_release_sha256=FIXTURE_TRACE_SHA256,
        mechanism_protocol=OPEN_CHOICE_MECHANISM_PROTOCOL,
        ablation_mode=FULL_SYSTEM_ABLATION_MODE,
        interaction_activation_enabled=False,
        activation_selection_salt=str(plan["selection_salt"]),
        governance_decision_ticks=(),
        resident_turn_schema_version=FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
        require_scenario_entry_post=False,
        relationship_release=relationships,
        scenario_stake_release=stakes,
        relationship_attention_enabled=True,
        private_actions_enabled=True,
        public_anonymity_enabled=True,
        ecology_calibration_release=ecology,
        activation_policy=COST_AWARE_ACTIVATION_POLICY,
        resident_model_view_condition=V5_FULL_NATURAL_PROFILE,
        anchor_indices_override=tuple(int(value) for value in plan["anchor_indices"]),
        opening_public_action_mode="diagnostic_open_choice",
    )
    rows = source.rows
    expected_prompts = PANEL_SIZE * 2
    if len(rows) != expected_prompts or source.delegate.calls != expected_prompts:
        raise ValueError("opportunity audit did not render both frozen panel ticks")
    tick_prompt_counts = {
        str(tick): sum(row["tick"] == tick for row in rows) for tick in (3, 4)
    }
    tick_group_candidates = {
        str(tick): sum(
            row["tick"] == tick and row["small_group_plausible"] for row in rows
        )
        for tick in (3, 4)
    }
    tick_minimum_visible_opportunities = {
        str(tick): min(
            row["visible_opportunity_count"]
            for row in rows
            if row["tick"] == tick
        )
        for tick in (3, 4)
    }
    body = {
        "schema_version": "campus-pulse-persona-opportunity-renderer-audit-v1",
        "scenario_id": GOVERNANCE_LEGITIMACY_SCENARIO,
        "ticks": [3, 4],
        "panel_size_per_tick": PANEL_SIZE,
        "resident_prompt_count": len(rows),
        "resident_prompt_count_by_tick": tick_prompt_counts,
        "resident_turn_schema_version": FORUM_AGENT_TURN_V5_SCHEMA_VERSION,
        "distinct_typical_thread_entry_count": len(
            {row["typical_thread_entry"] for row in rows if row["typical_thread_entry"]}
        ),
        "all_have_parallel_public_choices": all(
            row["parallel_choices_present"] for row in rows
        ),
        "all_have_like_repost_report_rules": all(
            row["like_rule_present"]
            and row["repost_rule_present"]
            and row["report_rule_present"]
            for row in rows
        ),
        "minimum_visible_message_opportunity_count": min(
            row["visible_opportunity_count"] for row in rows
        ),
        "minimum_visible_message_opportunity_count_by_tick": (
            tick_minimum_visible_opportunities
        ),
        "all_have_natural_private_motive": all(
            row["private_motive_count"] > 0 for row in rows
        ),
        "minimum_private_contact_count": min(
            row["private_contact_count"] for row in rows
        ),
        "small_group_candidate_prompt_count": sum(
            row["small_group_plausible"] for row in rows
        ),
        "small_group_candidate_prompt_count_by_tick": tick_group_candidates,
        "all_have_explicit_housing_position": all(
            row["housing_position_explicit"] for row in rows
        ),
        "all_exclude_internal_material": all(
            row["forbidden_internal_material_absent"] for row in rows
        ),
        "fixture_public_text_scored": False,
        "provider_calls": 0,
        "publication_eligible": False,
        "runtime_result_sha256": result["result_sha256"],
        "record_level_prompts_included": False,
        "record_level_agent_ids_included": False,
    }
    output = {**body, "audit_sha256": canonical_hash(body)}
    _write(args.output.resolve(), output)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "audit_sha256": output["audit_sha256"],
                "resident_prompt_count": output["resident_prompt_count"],
                "distinct_typical_thread_entry_count": output[
                    "distinct_typical_thread_entry_count"
                ],
                "small_group_candidate_prompt_count": output[
                    "small_group_candidate_prompt_count"
                ],
                "provider_calls": 0,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
