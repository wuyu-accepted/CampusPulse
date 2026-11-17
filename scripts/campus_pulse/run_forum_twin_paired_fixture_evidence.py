"""Run and persist aggregate-only ForumTwin paired-suite engineering evidence.

This command deliberately uses the deterministic contract source from the
ForumTwin test suite.  It never contacts a Provider and its output is never
publication eligible.  Public messages and microscopic identities are removed
from the persisted evidence; only workload counts, aggregate intervals, hashes
and scheduler gates remain.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for candidate in (ROOT, SRC):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402
from envs.campus_pulse_governance.forum_hero import (  # noqa: E402
    FORUM_TWIN_FORMAL_SEEDS,
    SHARED_BASELINE_BRANCH,
    run_forum_hero_experiment,
    run_forum_twin_paired_suite,
)
from envs.campus_pulse_governance.forum_pilot import (  # noqa: E402
    FORUM_TWIN_SCENARIOS,
)
from envs.campus_pulse_governance.tests.test_forum_pilot import (  # noqa: E402
    ReplaySource,
    TRACE_SHA,
)


DEFAULT_POPULATION = (
    ROOT / "_run" / "yulan-scale-v1" / "semantic-llm-agent-population-v1.json"
)
DEFAULT_OUTPUT = (
    ROOT.parent
    / "forumtwin-today"
    / "unified-engine-r1"
    / "paired-suite-fixture-evidence.json"
)


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


def aggregate_only_evidence(
    result: Mapping[str, Any], *, source_calls: int
) -> dict[str, Any]:
    if result.get("publication_eligible") is not False:
        raise RuntimeError("fixture suite unexpectedly became publication eligible")
    if int(result.get("semantic_slots", -1)) != source_calls:
        raise RuntimeError("fixture source call count does not match semantic slots")
    scenario_evidence: dict[str, Any] = {}
    for scenario_id, scenario in result["scenarios_results"].items():
        canonical = scenario["canonical_results"]["natural_and_adaptive"]
        branches = canonical["branches"]
        scenario_evidence[scenario_id] = {
            "seed_count": scenario["seed_count"],
            "seed_summaries": scenario["seed_summaries"],
            "branch_intervals_95": scenario["branch_intervals_95"],
            "paired_difference_intervals_95": scenario[
                "paired_difference_intervals_95"
            ],
            "canonical_natural_d_result_sha256": canonical["result_sha256"],
            "canonical_unique_activated_agents": canonical[
                "unique_activated_agents_across_branches"
            ],
            "canonical_activation_coverage_gate": canonical[
                "activation_coverage_gate"
            ],
            "canonical_scheduler_recall": {
                branch: branches[branch]["population_recovery"]["scheduler_recall"]
                for branch in ("Natural", "D")
            },
        }
    body: dict[str, Any] = {
        "schema_version": "campus-pulse-forum-twin-paired-fixture-evidence-v1",
        "evidence_kind": "zero_provider_deterministic_contract_fixture",
        "formal_llm_evidence": False,
        "publication_eligible": False,
        "source_disclosure": (
            "The deterministic test source exercises the reviewed-trace contract "
            "shape but is not a reviewed trace or an LLM result."
        ),
        "public_messages_persisted": 0,
        "microscopic_identities_persisted": False,
        "full_result_sha256": result["result_sha256"],
        "scenarios": result["scenarios"],
        "seeds": result["seeds"],
        "branches": result["branches"],
        "population_size": result["population_size"],
        "particle_count": result["particle_count"],
        "semantic_slots": result["semantic_slots"],
        "fixture_source_calls": source_calls,
        "particle_updates": result["particle_updates"],
        "maximum_resident_turns_per_tick": result[
            "maximum_resident_turns_per_tick"
        ],
        "maximum_total_llm_turns_per_tick": result[
            "maximum_total_llm_turns_per_tick"
        ],
        "dynamics_public_messages": result["dynamics_public_messages"],
        "publication_blocker": result["publication_blocker"],
        "scenario_evidence": scenario_evidence,
    }
    body["evidence_sha256"] = canonical_sha256(body)
    return body


def scheduler_diagnostic(
    population: Mapping[str, Any], *, scenario_id: str
) -> dict[str, Any]:
    """Run only the canonical paired hero needed to tune frozen scheduler gates."""

    source = ReplaySource()
    seed = FORUM_TWIN_FORMAL_SEEDS[0]
    snapshot_box: dict[str, Mapping[str, Any]] = {}
    run_forum_hero_experiment(
        population_release=population,
        resident_source=source,
        governance_source=source,
        expected_model="gpt-5.4-mini",
        scenario_id=scenario_id,
        seed=seed,
        start_tick=0,
        end_tick=2,
        branches=(SHARED_BASELINE_BRANCH,),
        resident_budget_by_tick={0: 24, 1: 24, 2: 48},
        reviewed_trace_release_sha256=TRACE_SHA,
        snapshot_callback=lambda _branch, value: snapshot_box.update(
            {"shared": value}
        ),
        interaction_activation_enabled=False,
    )
    hero = run_forum_hero_experiment(
        population_release=population,
        resident_source=source,
        governance_source=source,
        expected_model="gpt-5.4-mini",
        scenario_id=scenario_id,
        seed=seed,
        start_tick=3,
        end_tick=23,
        branches=("Natural", "D"),
        resident_budget_by_tick={
            tick: 64 if tick <= 7 else 48 if tick <= 17 else 32
            for tick in range(3, 24)
        },
        reviewed_trace_release_sha256=TRACE_SHA,
        initial_snapshot=snapshot_box["shared"],
    )
    return {
        "scenario": scenario_id,
        "fixture_source_calls": source.calls,
        "unique_activated_agents": hero["unique_activated_agents_across_branches"],
        "coverage_gate": hero["activation_coverage_gate"],
        "scheduler_recall": {
            branch: hero["branches"][branch]["population_recovery"][
                "scheduler_recall"
            ]
            for branch in ("Natural", "D")
        },
        "provider_calls": 0,
        "publication_eligible": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--population", type=Path, default=DEFAULT_POPULATION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--scheduler-diagnostic-scenario",
        choices=FORUM_TWIN_SCENARIOS,
    )
    arguments = parser.parse_args()

    population = _load_object(arguments.population.resolve())
    if arguments.scheduler_diagnostic_scenario:
        print(
            json.dumps(
                scheduler_diagnostic(
                    population,
                    scenario_id=arguments.scheduler_diagnostic_scenario,
                ),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    source = ReplaySource()
    result = run_forum_twin_paired_suite(
        population_release=population,
        resident_source=source,
        governance_source=source,
        expected_model="gpt-5.4-mini",
        reviewed_trace_release_sha256=TRACE_SHA,
    )
    evidence = aggregate_only_evidence(result, source_calls=source.calls)
    _atomic_json(arguments.output.resolve(), evidence)
    print(
        json.dumps(
            {
                "output": str(arguments.output.resolve()),
                "full_result_sha256": evidence["full_result_sha256"],
                "evidence_sha256": evidence["evidence_sha256"],
                "semantic_slots": evidence["semantic_slots"],
                "particle_updates": evidence["particle_updates"],
                "provider_calls": 0,
                "publication_eligible": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
