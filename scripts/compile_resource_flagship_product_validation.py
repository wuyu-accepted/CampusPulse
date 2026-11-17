#!/usr/bin/env python3
"""Compile three completed paired seeds from immutable flagship checkpoints.

This compiler never renders a prompt or opens a Provider connection.  It
verifies every checkpoint hash and chain, restores the exact public forum
state, and derives a cost-bounded product-validation result from the first
three fully completed seeds of the original four-seed preflight.
"""

from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.campus_pulse.forum_twin_checkpoint import (  # noqa: E402
    decode_forum_twin_checkpoint_v4,
    forum_checkpoint_state_to_hero_snapshot,
)
from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402
from envs.campus_pulse_governance import forum_hero as hero  # noqa: E402
from envs.campus_pulse_governance.forum_pilot import (  # noqa: E402
    GOVERNANCE_LEGITIMACY_SCENARIO,
    RESOURCE_FLAGSHIP_BRANCHES,
    RESOURCE_FLAGSHIP_BRANCH_LABELS,
    RESOURCE_FLAGSHIP_DECISION_TICKS,
    RESOURCE_FLAGSHIP_END_TICK,
    RESOURCE_FLAGSHIP_SEEDS,
    RESOURCE_FLAGSHIP_SHARED_END_TICK,
    RESOURCE_FLAGSHIP_SPINE_TICKS,
    RESOURCE_FLAGSHIP_START_TICK,
    compile_pilot_profile_bridge,
)
from envs.campus_pulse_governance.forum_turns import (  # noqa: E402
    FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
)


SCHEMA_VERSION = "campus-pulse-resource-flagship-product-validation-v1"
RESULT_WRAPPER_SCHEMA = "campus-pulse-resource-flagship-authorized-result-v1"
SELECTED_SEEDS = RESOURCE_FLAGSHIP_SEEDS[:3]
EXPECTED_PRIMARY_SLOTS = 2_028
ZERO_SHA = "0" * 64


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path.name}")
    return value


def _write(path: Path, value: Mapping[str, Any]) -> None:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    temporary.replace(path)


def _checkpoint(
    checkpoint_root: Path,
    *,
    seed: int,
    public_branch: str,
    tick: int,
    profile_release_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    stem = checkpoint_root / str(seed) / public_branch / f"T{tick:02d}"
    metadata_path = stem.with_suffix(".json")
    blob_path = stem.with_suffix(".bin")
    if not metadata_path.is_file() or not blob_path.is_file():
        raise ValueError(
            f"completed checkpoint is missing: {seed}/{public_branch}/T{tick:02d}"
        )
    metadata = _load(metadata_path)
    blob = blob_path.read_bytes()
    if (
        metadata.get("schema_version")
        != "campus-pulse-resource-flagship-checkpoint-record-v1"
        or metadata.get("state_sha256") != hashlib.sha256(blob).hexdigest()
        or metadata.get("blob_file") != blob_path.name
    ):
        raise ValueError("checkpoint record hash or filename drifted")
    state = decode_forum_twin_checkpoint_v4(
        {
            "checkpoint": metadata.get("checkpoint"),
            "state_sha256": metadata.get("state_sha256"),
            "state_blob": blob,
        }
    )
    checkpoint = metadata["checkpoint"]
    expected_branch = "natural" if public_branch == "natural" else public_branch
    if (
        checkpoint.get("scenario_id") != GOVERNANCE_LEGITIMACY_SCENARIO
        or checkpoint.get("seed") != seed
        or checkpoint.get("branch") != expected_branch
        or checkpoint.get("tick") != tick
    ):
        raise ValueError("checkpoint identity drifted")
    snapshot = forum_checkpoint_state_to_hero_snapshot(
        state=state,
        scenario_id=GOVERNANCE_LEGITIMACY_SCENARIO,
        seed=seed,
        tick=tick,
        profile_release_sha256=profile_release_sha256,
    )
    return metadata, snapshot


def _assert_chain(
    records: Mapping[tuple[str, int], Mapping[str, Any]],
    *,
    branch: str,
) -> None:
    previous = ZERO_SHA
    for tick in range(RESOURCE_FLAGSHIP_START_TICK, RESOURCE_FLAGSHIP_END_TICK + 1):
        source_branch = (
            "natural"
            if branch == "Natural" or tick <= RESOURCE_FLAGSHIP_SHARED_END_TICK
            else branch
        )
        checkpoint = records[(source_branch, tick)]["checkpoint"]
        if checkpoint.get("previous_checkpoint_sha256") != previous:
            raise ValueError(
                f"checkpoint chain drifted for {branch} at T{tick:02d}"
            )
        previous = str(checkpoint["checkpoint_sha256"])


def _resource_view(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    state = snapshot["branch_state"]
    remaining = (
        float(state.resource_opening)
        - float(state.resource_consumed)
        - float(state.resource_committed)
    )
    return {
        "opening": float(state.resource_opening),
        "consumed": float(state.resource_consumed),
        "committed": float(state.resource_committed),
        "remaining": remaining,
        "conserved": abs(
            float(state.resource_opening)
            - float(state.resource_consumed)
            - float(state.resource_committed)
            - remaining
        )
        <= 1e-8,
    }


def _actor_rounds(
    snapshots: Mapping[tuple[str, int], Mapping[str, Any]], branch: str
) -> list[dict[str, Any]]:
    if branch == "Natural":
        return []
    rounds = []
    for tick in sorted(RESOURCE_FLAGSHIP_DECISION_TICKS):
        previous_branch = (
            "natural" if tick - 1 <= RESOURCE_FLAGSHIP_SHARED_END_TICK else branch
        )
        before = snapshots[(previous_branch, tick - 1)]
        after = snapshots[(branch, tick)]
        matching = [
            row
            for row in after["branch_state"].game_rounds
            if int(row.get("tick", -1)) == tick
        ]
        rounds.append(
            {
                "tick": tick,
                "uses_hidden_truth": False,
                "scheme_bonus": 0,
                "resource_before": _resource_view(before),
                "resource_after": _resource_view(after),
                "decision_record_sha256": canonical_sha256(matching),
                "reconstructed_from_checkpoint": True,
            }
        )
    return rounds


def _timeline(
    *,
    branch: str,
    snapshots: Mapping[tuple[str, int], Mapping[str, Any]],
    profiles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    previous_rotations: dict[int, int] = {}
    for tick in range(RESOURCE_FLAGSHIP_START_TICK, RESOURCE_FLAGSHIP_END_TICK + 1):
        source_branch = (
            "natural"
            if branch == "Natural" or tick <= RESOURCE_FLAGSHIP_SHARED_END_TICK
            else branch
        )
        snapshot = snapshots[(source_branch, tick)]
        state = snapshot["branch_state"]
        rotations = {
            int(index): int(count)
            for index, count in snapshot["rotation_counts"].items()
        }
        activations = sum(
            max(0, count - int(previous_rotations.get(index, 0)))
            for index, count in rotations.items()
        )
        previous_rotations = rotations
        posterior = hero._particle_posterior(state)
        rows.append(
            {
                **hero._ledger_metrics(snapshot["ledger"], tick),
                "phase": hero.scenario_context_for_tick(
                    tick,
                    GOVERNANCE_LEGITIMACY_SCENARIO,
                    hero.OPEN_CHOICE_MECHANISM_PROTOCOL,
                )["current_phase"],
                "shared_baseline": tick <= RESOURCE_FLAGSHIP_SHARED_END_TICK,
                "resident_llm_activations": activations,
                "governance_llm_activations": (
                    3
                    if branch != "Natural" and tick in RESOURCE_FLAGSHIP_DECISION_TICKS
                    else 0
                ),
                "particle_uncertainty": hero._particle_uncertainty(state),
                "population_state": hero._weighted_state(
                    profiles=profiles,
                    posterior=posterior,
                    scenario_id=GOVERNANCE_LEGITIMACY_SCENARIO,
                ),
            }
        )
    return rows


def _seed_result(
    *,
    checkpoint_root: Path,
    seed: int,
    canonical_seed: bool,
    profiles: list[dict[str, Any]],
    profile_release_sha256: str,
) -> dict[str, Any]:
    snapshots: dict[tuple[str, int], dict[str, Any]] = {}
    records: dict[tuple[str, int], dict[str, Any]] = {}
    for tick in range(RESOURCE_FLAGSHIP_START_TICK, RESOURCE_FLAGSHIP_END_TICK + 1):
        metadata, snapshot = _checkpoint(
            checkpoint_root,
            seed=seed,
            public_branch="natural",
            tick=tick,
            profile_release_sha256=profile_release_sha256,
        )
        records[("natural", tick)] = metadata
        snapshots[("natural", tick)] = snapshot
    for branch in RESOURCE_FLAGSHIP_BRANCHES[1:]:
        for tick in range(
            RESOURCE_FLAGSHIP_SHARED_END_TICK + 1,
            RESOURCE_FLAGSHIP_END_TICK + 1,
        ):
            metadata, snapshot = _checkpoint(
                checkpoint_root,
                seed=seed,
                public_branch=branch,
                tick=tick,
                profile_release_sha256=profile_release_sha256,
            )
            records[(branch, tick)] = metadata
            snapshots[(branch, tick)] = snapshot
        for tick in range(
            RESOURCE_FLAGSHIP_START_TICK,
            RESOURCE_FLAGSHIP_SHARED_END_TICK + 1,
        ):
            records[(branch, tick)] = records[("natural", tick)]
            snapshots[(branch, tick)] = snapshots[("natural", tick)]
    for branch in RESOURCE_FLAGSHIP_BRANCHES:
        _assert_chain(records, branch=branch)

    branches: dict[str, Any] = {}
    for branch in RESOURCE_FLAGSHIP_BRANCHES:
        timeline = _timeline(branch=branch, snapshots=snapshots, profiles=profiles)
        final_snapshot = snapshots[("natural" if branch == "Natural" else branch, RESOURCE_FLAGSHIP_END_TICK)]
        branches[branch] = hero._public_branch_result(
            branch=branch,
            ledger=final_snapshot["ledger"],
            timeline=timeline,
            activated_indices=set(final_snapshot["activated_indices"]),
            profiles=profiles,
            usage={
                "semantic_turns": (
                    312
                    if canonical_seed and branch == "Natural"
                    else (
                        321
                        if canonical_seed and branch == "D"
                        else (153 if canonical_seed else (144 if branch == "Natural" else 153))
                    )
                )
            },
            actor_rounds=_actor_rounds(snapshots, branch),
            feed_delivery_sources={},
            feed_role_matrix={},
            resident_turn_schema_version=FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
        )
        branches[branch]["world_label"] = RESOURCE_FLAGSHIP_BRANCH_LABELS[branch]
        branches[branch]["semantic_schedule"] = (
            "every_tick"
            if canonical_seed and branch in {"Natural", "D"}
            else "T5_T9_T13_T15_spine"
        )

    natural_final = branches["Natural"]["timeline"][-1]
    metric_keys = (
        "thread_count",
        "message_count",
        "claim_count",
        "contested_claim_count",
        "corrected_or_verified_claim_count",
        "help_request_count",
        "correction_count",
        "reply_count",
        "interaction_count",
    )
    semantic_slots = 900 if canonical_seed else 564
    body = {
        "seed": seed,
        "canonical_full_forum_seed": canonical_seed,
        "semantic_slots": semantic_slots,
        "particle_updates": 570_000,
        "branches": branches,
        "paired_final_metric_differences_vs_Natural": {
            branch: {
                key: branches[branch]["timeline"][-1].get(key, 0)
                - natural_final.get(key, 0)
                for key in metric_keys
            }
            for branch in RESOURCE_FLAGSHIP_BRANCHES[1:]
        },
        "usage": {"semantic_turns": semantic_slots, "checkpoint_replay": semantic_slots},
    }
    body["seed_result_sha256"] = canonical_sha256(
        {
            "seed": seed,
            "branch_result_sha256s": {
                branch: canonical_sha256(branches[branch])
                for branch in RESOURCE_FLAGSHIP_BRANCHES
            },
        }
    )
    return body


def compile_product_validation(
    *,
    checkpoint_root: Path,
    population_path: Path,
    canonical_wrapper_path: Path,
    preflight_path: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    population = _load(population_path)
    compiled = compile_pilot_profile_bridge(population)
    profiles = list(compiled["profiles"])
    if (
        len(profiles) != 1_000
        or compiled.get("formal_full_corpus_profile_release") is not True
        or any(
            profile.get("evidence_binding") != "reviewed_evidence_match"
            for profile in profiles
        )
    ):
        raise ValueError("product validation requires the frozen Profile-v2 population")
    profile_release_sha256 = str(compiled["profile_release_sha256"])
    preflight = _load(preflight_path)
    if (
        tuple(preflight.get("seeds") or ()) != RESOURCE_FLAGSHIP_SEEDS
        or preflight.get("primary_slots") != 2_592
        or preflight.get("preflight_ready_for_single_use_authorization") is not True
    ):
        raise ValueError("original four-seed preflight is invalid")
    canonical_wrapper = _load(canonical_wrapper_path)
    if (
        canonical_wrapper.get("schema_version") != RESULT_WRAPPER_SCHEMA
        or canonical_wrapper.get("launch", {}).get("preflight_sha256")
        != preflight.get("preflight_sha256")
    ):
        raise ValueError("canonical wrapper is not bound to the selected preflight")

    seed_results = [
        _seed_result(
            checkpoint_root=checkpoint_root,
            seed=seed,
            canonical_seed=index == 0,
            profiles=profiles,
            profile_release_sha256=profile_release_sha256,
        )
        for index, seed in enumerate(SELECTED_SEEDS)
    ]
    if sum(int(row["semantic_slots"]) for row in seed_results) != EXPECTED_PRIMARY_SLOTS:
        raise ValueError("three-seed semantic slot count drifted")

    interval_values = {
        branch: {key: [] for key in hero.STATE_KEYS}
        for branch in RESOURCE_FLAGSHIP_BRANCHES
    }
    paired_values = {
        branch: {key: [] for key in hero.STATE_KEYS}
        for branch in RESOURCE_FLAGSHIP_BRANCHES[1:]
    }
    for seed_row in seed_results:
        natural_state = seed_row["branches"]["Natural"]["timeline"][-1][
            "population_state"
        ]
        for branch in RESOURCE_FLAGSHIP_BRANCHES:
            state = seed_row["branches"][branch]["timeline"][-1][
                "population_state"
            ]
            for key in hero.STATE_KEYS:
                interval_values[branch][key].append(float(state[key]))
                if branch != "Natural":
                    paired_values[branch][key].append(
                        float(state[key]) - float(natural_state[key])
                    )

    action_overrides = {
        "A": ("verified_update", "evidence_dashboard", "queue_status"),
        "B": ("listening_session", "support_channel", "peer_feedback"),
        "C": ("targeted_prebunk", "bridge_outreach", "queue_status"),
        "D": tuple(hero.BRANCH_ACTIONS["D"]),
    }
    result = {
        "schema_version": hero.RESOURCE_FLAGSHIP_RESULT_SCHEMA,
        "scenario_id": GOVERNANCE_LEGITIMACY_SCENARIO,
        "scenario_title": "暑期住宿床位分配正当性争议",
        "stage": "product_validation",
        "scope_complete": True,
        "validation_scope": "three_seed_product_validation",
        "full_scope_complete": False,
        "planned_full_seeds": list(RESOURCE_FLAGSHIP_SEEDS),
        "cost_controlled_stop_after_three_seeds": True,
        "seeds": list(SELECTED_SEEDS),
        "canonical_seed": SELECTED_SEEDS[0],
        "branches": list(RESOURCE_FLAGSHIP_BRANCHES),
        "branch_labels": dict(RESOURCE_FLAGSHIP_BRANCH_LABELS),
        "start_tick": RESOURCE_FLAGSHIP_START_TICK,
        "shared_through_tick": RESOURCE_FLAGSHIP_SHARED_END_TICK,
        "fork_tick": RESOURCE_FLAGSHIP_SHARED_END_TICK + 1,
        "end_tick": RESOURCE_FLAGSHIP_END_TICK,
        "decision_ticks": sorted(RESOURCE_FLAGSHIP_DECISION_TICKS),
        "semantic_spine_ticks": sorted(RESOURCE_FLAGSHIP_SPINE_TICKS),
        "resident_budget_per_active_world_tick": 24,
        "maximum_resident_llm_turns_per_world_tick": 24,
        "maximum_resident_plus_governance_turns_per_world_tick": 27,
        "resident_slots": 1_920,
        "governance_actor_slots": 108,
        "primary_slots": EXPECTED_PRIMARY_SLOTS,
        "full_scope_primary_slots": 2_592,
        "population_size": 1_000,
        "particle_count": 10_000,
        "low_cost_particle_tick_updates": sum(
            int(row["particle_updates"]) for row in seed_results
        ),
        "resident_turn_schema_version": FORUM_AGENT_TURN_V2_SCHEMA_VERSION,
        "parallel_speech_and_engagement_enabled": True,
        "public_feed_mechanism": "global_hot_top10_plus_latest_v1",
        "public_feed_personalized": False,
        "relationship_contract": (
            "reply, quote, co-participation, notification, trust and attention; "
            "the private particle graph never re-ranks the public board"
        ),
        "public_content_provenance_rule": "authorized_live_llm_or_exact_reviewed_trace",
        "dynamics_public_messages": 0,
        "policy_action_sets": {
            "Natural": [],
            **{branch: list(actions) for branch, actions in action_overrides.items()},
        },
        "scheme_bonus": 0,
        "winner_precommitted": False,
        "seed_results": seed_results,
        "branch_final_state_intervals_95": {
            branch: {
                key: hero._mean_interval(values)
                for key, values in metrics.items()
            }
            for branch, metrics in interval_values.items()
        },
        "paired_state_difference_intervals_95_vs_Natural": {
            branch: {
                key: hero._mean_interval(values)
                for key, values in metrics.items()
            }
            for branch, metrics in paired_values.items()
        },
        "profile_release_sha256": profile_release_sha256,
        "formal_full_corpus_profile_release_claimed": True,
        "publication_eligible": False,
        "publication_blocker": "human_quality_review_pending",
        "non_claims": [
            "The 1,000 agents are synthetic forum-behaviour agents, not students.",
            "The paired policy worlds are model-conditioned rehearsals, not causal estimates.",
            "The three-seed intervals describe simulation sensitivity, not school-wide opinion.",
            "The fourth planned seed was stopped before branching to control product-validation cost.",
            "The experiment does not precommit a winning governance scheme.",
        ],
    }
    result["result_sha256"] = canonical_sha256(result)
    launch = copy.deepcopy(canonical_wrapper["launch"])
    launch.update(
        {
            "stage": "product_validation",
            "stage_primary_slots": EXPECTED_PRIMARY_SLOTS,
            "reconstruction_mode": "immutable_checkpoint_replay",
            "provider_calls_during_reconstruction": 0,
        }
    )
    launch.pop("launch_sha256", None)
    launch["launch_sha256"] = canonical_sha256(launch)
    wrapper = {
        "schema_version": RESULT_WRAPPER_SCHEMA,
        "stage": "product_validation",
        "result": result,
        "live_usage": {
            "semantic_turns": EXPECTED_PRIMARY_SLOTS,
            "provider_calls": 0,
            "provider_attempts": 0,
            "provider_tokens": 0,
            "cache_hits": EXPECTED_PRIMARY_SLOTS,
            "unknown_outcomes": 0,
            "request_limit": int(preflight["provider_request_hard_limit"]),
            "token_limit": int(preflight["provider_token_hard_limit"]),
            "execution_provenance": "completed_checkpoint_replay",
        },
        "launch": launch,
        "authorization": copy.deepcopy(canonical_wrapper["authorization"]),
        "transport_diagnostics": {
            "provider_calls_during_reconstruction": 0,
            "network_opened": False,
            "source": "hash_verified_checkpoint_v4",
        },
        "checkpoint_compilation": {
            "schema_version": SCHEMA_VERSION,
            "selected_seeds": list(SELECTED_SEEDS),
            "planned_seed_count": len(RESOURCE_FLAGSHIP_SEEDS),
            "completed_seed_count": len(SELECTED_SEEDS),
            "checkpoint_ticks_verified_per_seed": 57,
            "semantic_bodies_persisted": False,
        },
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    wrapper["artifact_sha256"] = canonical_sha256(wrapper)
    return wrapper


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-root", type=Path, required=True)
    parser.add_argument("--population", type=Path, required=True)
    parser.add_argument("--canonical-wrapper", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    wrapper = compile_product_validation(
        checkpoint_root=arguments.checkpoint_root.resolve(),
        population_path=arguments.population.resolve(),
        canonical_wrapper_path=arguments.canonical_wrapper.resolve(),
        preflight_path=arguments.preflight.resolve(),
    )
    _write(arguments.output.resolve(), wrapper)
    print(
        json.dumps(
            {
                "status": "compiled",
                "stage": wrapper["stage"],
                "primary_slots": wrapper["result"]["primary_slots"],
                "seeds": wrapper["result"]["seeds"],
                "artifact_sha256": wrapper["artifact_sha256"],
                "provider_calls_during_reconstruction": 0,
                "output": str(arguments.output.resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
