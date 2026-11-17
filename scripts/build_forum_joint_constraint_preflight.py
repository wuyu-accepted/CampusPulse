"""Freeze a four-view Profile x previous-state paired resident experiment.

The builder performs no network I/O and never reads a credential.  It reuses
the existing 12-resident diagnostic panel and the production V5 renderer, then
binds four views of the same Tick-3 world:

* context only;
* context plus stable Profile/stake;
* context plus previous committed particle state;
* context plus both Profile/stake and previous state.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402
from envs.campus_pulse_governance.forum_turns import (  # noqa: E402
    V5_CONTEXT_ABLATION,
    V5_CONTEXT_ONLY,
    V5_CONTEXT_PLUS_PROFILE,
    V5_FULL_NATURAL_PROFILE,
)
from envs.campus_pulse_governance.mass_population import canonical_hash  # noqa: E402
from build_forum_persona_stage_a_preflight import (  # noqa: E402
    DEFAULT_ECOLOGY,
    DEFAULT_FORMAL_POPULATION,
    DEFAULT_POPULATION,
    _atomic_write,
    _file_sha256,
    build as build_base,
)


PREFLIGHT_SCHEMA = "campus-pulse-joint-constraint-preflight-v1"
PLAN_SCHEMA = "campus-pulse-joint-constraint-plan-v1"
CONDITIONS = (
    V5_CONTEXT_ONLY,
    V5_CONTEXT_PLUS_PROFILE,
    V5_CONTEXT_ABLATION,
    V5_FULL_NATURAL_PROFILE,
)
PANEL_SIZE = 12
PRIMARY_SLOTS = PANEL_SIZE * len(CONDITIONS)
REQUEST_LIMIT = PRIMARY_SLOTS * 2
TOKEN_LIMIT = 1_200_000
MAX_CONCURRENCY = 2
DEFAULT_OUTPUT = ROOT / "_run" / "joint-profile-state-v1"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return value


def build(
    *,
    population_path: Path,
    formal_population_path: Path,
    ecology_path: Path,
    output_dir: Path,
    endpoint: str,
    model: str,
) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    with tempfile.TemporaryDirectory(prefix="joint-constraint-base-") as temporary:
        base_dir = Path(temporary)
        build_base(
            population_path=population_path.resolve(),
            formal_population_path=formal_population_path.resolve(),
            ecology_path=ecology_path.resolve(),
            output_dir=base_dir,
            endpoint=endpoint,
            model=model,
        )
        base_plan = _read(base_dir / "plan.private.json")
        base_preflight = _read(base_dir / "preflight-v2.json")

    plan_body = {
        key: value
        for key, value in base_plan.items()
        if key not in {"plan_sha256", "stage_b", "conditions", "stage_a_primary_slots"}
    }
    plan_body.update(
        {
            "schema_version": PLAN_SCHEMA,
            "conditions": list(CONDITIONS),
            "primary_slots": PRIMARY_SLOTS,
            "paired_factor_design": {
                V5_CONTEXT_ONLY: {"profile": False, "previous_state": False},
                V5_CONTEXT_PLUS_PROFILE: {"profile": True, "previous_state": False},
                V5_CONTEXT_ABLATION: {"profile": False, "previous_state": True},
                V5_FULL_NATURAL_PROFILE: {"profile": True, "previous_state": True},
            },
            "previous_state_source": "exact_pre_decision_tick_3_particle_posterior",
            "state_leakage_forbidden": True,
        }
    )
    plan = {**plan_body, "plan_sha256": canonical_hash(plan_body)}

    bindings = dict(base_preflight.get("component_bindings") or {})
    runner_path = ROOT / "scripts" / "run_forum_joint_constraint_authorized.py"
    if not runner_path.is_file():
        raise ValueError("joint-constraint runner is absent")
    bindings["joint_constraint_runner"] = _file_sha256(runner_path)
    bindings["forum_turn_renderer"] = _file_sha256(
        ROOT / "src/envs/campus_pulse_governance/forum_turns.py"
    )

    preflight_body = {
        key: value
        for key, value in base_preflight.items()
        if key
        not in {
            "preflight_sha256",
            "schema_version",
            "scope",
            "conditions",
            "ticks",
            "stage_a_resident_slots",
            "stage_b_resident_slots",
            "resident_slots",
            "governance_actor_slots",
            "primary_slots",
            "provider_request_hard_limit",
            "provider_token_hard_limit",
            "plan_sha256",
            "component_bindings",
            "sent_fields",
            "paired_world_rule",
            "stage_b_rule",
        }
    }
    preflight_body.update(
        {
            "schema_version": PREFLIGHT_SCHEMA,
            "scope": "paired_profile_previous_state_joint_constraint_gate",
            "ticks": [3],
            "conditions": list(CONDITIONS),
            "stage_a_resident_slots": PRIMARY_SLOTS,
            "stage_b_resident_slots": 0,
            "resident_slots": PRIMARY_SLOTS,
            "governance_actor_slots": 0,
            "primary_slots": PRIMARY_SLOTS,
            "provider_request_hard_limit": REQUEST_LIMIT,
            "provider_token_hard_limit": TOKEN_LIMIT,
            "max_concurrency": MAX_CONCURRENCY,
            "plan_sha256": plan["plan_sha256"],
            "component_bindings": dict(sorted(bindings.items())),
            "sent_fields": [
                "natural-language reviewed Profile and up to three reviewed histories when enabled",
                "natural-language synthetic stake and relationship description when enabled",
                "qualitative previous committed particle state when enabled",
                "time-visible synthetic forum context, scenario, branch, Tick and JSON receipt contract",
            ],
            "excluded_fields": [
                "raw forum text, source IDs or real identities",
                "raw particle arrays, raw particle values or hidden population truth",
                "future messages, target outcomes, local paths, credentials or Provider bodies",
            ],
            "paired_world_rule": (
                "All four views reuse the same 12 Agents, Tick-3 event, public board, "
                "relationships, action contract and random seed. Only Profile/stake and "
                "previous-state wording are independently included or held out."
            ),
            "unknown_outcome_policy": "pause_without_automatic_retry",
            "publication_eligible": False,
        }
    )
    preflight = {
        **preflight_body,
        "preflight_sha256": canonical_sha256(preflight_body),
    }
    _atomic_write(output_dir / "plan.private.json", plan)
    _atomic_write(output_dir / "preflight-v1.json", preflight)
    return {
        "preflight_sha256": preflight["preflight_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "model": model,
        "conditions": list(CONDITIONS),
        "paired_agents": PANEL_SIZE,
        "primary_slots": PRIMARY_SLOTS,
        "request_hard_limit": REQUEST_LIMIT,
        "token_hard_limit": TOKEN_LIMIT,
        "ready": True,
        "provider_calls": 0,
        "output_dir": str(output_dir),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", type=Path, default=DEFAULT_POPULATION)
    parser.add_argument("--formal-population", type=Path, default=DEFAULT_FORMAL_POPULATION)
    parser.add_argument("--ecology", type=Path, default=DEFAULT_ECOLOGY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--endpoint", default="https://api.openlux.ai/v1")
    parser.add_argument("--model", default="gpt-5.6-luna")
    arguments = parser.parse_args()
    print(
        json.dumps(
            build(
                population_path=arguments.population,
                formal_population_path=arguments.formal_population,
                ecology_path=arguments.ecology,
                output_dir=arguments.output_dir,
                endpoint=arguments.endpoint,
                model=arguments.model,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
