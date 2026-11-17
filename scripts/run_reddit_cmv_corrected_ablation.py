"""Corrected four-cell continuation from an identical frozen warm-up horizon.

Reuse each condition's committed Tick 1; later old-protocol outputs are not
merged. Tick 2-9 uses text-observation assimilation uniformly in all cells.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time

import run_reddit_cmv_input_ablation as base
from backend.campus_pulse.reddit_cmv_execution import CmvRunRepository
from backend.campus_pulse.runtime_contract import canonical_sha256
from envs.campus_pulse_governance.reddit.expression_observer import observe_expression_batch

PARENT = base.OUT
base.OUT = base.ROOT / "data/private/reddit/cmv/input-ablation-natural-t9-corrected"
base.REQUEST_LIMIT = 1600
base.TOKEN_LIMIT = 5_000_000
base.CONCURRENCY = 1


def dynamic_inputs():
    """Small fixed-person stress test; assumptions are not historical facts."""
    from copy import deepcopy
    from envs.campus_pulse_governance.reddit.run_plan import _select_panel
    world, context, particles, _ = base.inputs()
    selected, probabilities = _select_panel(world["agents"], sample_size=12, seed=20261002, tick=1)
    panel = [row["agent_id"] for row in selected]
    updates = [
        {"released_tick": 1, "provenance": "scenario_assumption", "text": (
            "Hypothetical policy exercise, not a factual claim about a real country: tax-funded healthcare "
            "and education have a combined annual budget of 100 units. Estimated essential-care and basic-education "
            "cost is 100 units. No promise of unlimited elective services is made. Assess the same free-access "
            "principle in this setting; you can support, reject, qualify it, or remain undecided.")},
        {"released_tick": 2, "provenance": "scenario_assumption", "text": (
            "New hypothetical estimate: expected cost for the same essential-care and basic-education package "
            "rises to 130 units. The existing budget remains 100. Candidate responses include raising collective "
            "revenue, reducing coverage, delaying rollout, or charging users. These choices have different access "
            "and fiscal costs. No response has yet been implemented and the estimate is provisional.")},
        {"released_tick": 3, "provenance": "scenario_assumption", "text": (
            "The hypothetical estimate is revised to 120 units after correcting double-counting. A 20-unit "
            "grant covers the gap for the first year only. Longer-term funding remains unresolved; no actual "
            "service improvement or official policy decision is claimed. Consider which earlier concern this "
            "changes, if any. Maintaining your previous position is valid.")},
    ]
    contexts = {}
    for tick in range(1, 4):
        contexts[tick] = deepcopy(context)
        contexts[tick]["scenario_updates"] = deepcopy(updates[:tick])
    return world, context, particles, panel, probabilities, contexts


def dynamic_preflight(*, structure: bool = False):
    world, context, particles, panel, probabilities, contexts = dynamic_inputs()
    plan = {"schema": "cmv-small-dynamic-state-comparison-v1", "world_sha256": world["world_sha256"],
            "initial_particles_sha256": hashlib.sha256(base.INITIAL.read_bytes()).hexdigest(),
            "seed": 20261002, "ticks": [1, 2, 3], "conditions": ["full", "static_state", "no_state"],
            "panel": panel, "panel_probabilities": probabilities,
            "contexts_by_tick": {str(t): c for t, c in contexts.items()},
            "event_proposition_constant": context["decision_question"],
            "new_information_provenance": "synthetic_scenario_assumptions_not_historical_continuation",
            "input_conditions": {"full": "full", "static_state": "full", "no_state": "profile_only"},
            "observer_scope": "whole_proposition_v2", "private_actions_enabled": False,
            "evaluation": ["independent text stance and coverage", "prior prediction vs majority",
                           "new-information uptake", "state uncertainty", "within-minus-between style"],
            "go_no_go": "Do not expand if all cells only repeat one stance or complete input has no predictive gain.",
            "runtime_files": {name: hashlib.sha256((base.ROOT/name).read_bytes()).hexdigest() for name in (
                "scripts/run_reddit_cmv_corrected_ablation.py", "scripts/run_reddit_cmv_input_ablation.py", "scripts/cmv_standing_resume.py",
                "src/backend/campus_pulse/reddit_cmv_execution.py", "src/envs/campus_pulse_governance/reddit/runtime.py",
                "src/envs/campus_pulse_governance/forum_turns.py", "src/envs/campus_pulse_governance/reddit/expression_observer.py",
                "src/envs/campus_pulse_governance/forum_v2_execution.py")}}
    body = {"plan": plan, "plan_sha256": canonical_sha256(plan), "model": "gpt-6-luna", "reasoning_effort": "none",
            "endpoint": base.ENDPOINT, "request_limit": 260, "token_limit": 1000000, "timeout_seconds": 180,
            "primary_slots": 108, "auxiliary_expression_batches": 9, "final_tick": 3, "max_concurrency": 1,
            "authorization_scope": "small dynamic fixed-person state comparison, hypothetical updates explicitly labeled"}
    if structure:
        plan.update(schema="cmv-three-chain-structure-comparison-v3", input_revision=3,
                    particle_observation_model="scope-calibrated-v3",
                    attention_history="committed_per_agent_recent_64; common_public_ranking",
                    evaluation_labels="independent_review_never_used_for_assimilation")
        for name in ("src/envs/campus_pulse_governance/reddit/particle_model.py",
                     "src/envs/campus_pulse_governance/reddit/evaluation.py",
                     "scripts/audit_reddit_cmv_pilot.py"):
            plan["runtime_files"][name] = hashlib.sha256((base.ROOT/name).read_bytes()).hexdigest()
        body.update(plan_sha256=canonical_sha256(plan),
                    format_repair_limit=1, controlled_semantic_repairs_per_slot=3,
                    minimum_request_interval_seconds=15, settled_transient_retry_limit=3,
                    unknown="stop_no_retry",
                    authorization_scope="new three-chain structure; no reuse of changed-input live responses")
    return {**body, "preflight_sha256": canonical_sha256(body)}


def dynamic_fixture(*, structure: bool = False):
    """Exercise production execution and recovery, with no language generation."""
    from copy import deepcopy
    import tempfile
    from unittest.mock import patch
    from backend.tests.test_reddit_cmv_execution import _NoopSemanticSource
    from backend.campus_pulse.reddit_cmv_execution import CmvBranchState, execute_cmv_resident_tick
    from envs.campus_pulse_governance.forum_twin import AgentMemory
    from envs.campus_pulse_governance.reddit.runtime import initialize_cmv_event_ledger
    world, event, particles, panel, probs, contexts = dynamic_inputs()
    pf = dynamic_preflight(structure=structure)
    run_id = "cmv-run-" + pf["preflight_sha256"][:24]
    result = {}
    with tempfile.TemporaryDirectory() as directory, patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
        for condition in pf["plan"]["conditions"]:
            repo = CmvRunRepository(Path(directory)/condition, run_id=run_id, plan_sha256=pf["plan_sha256"], world_sha256=world["world_sha256"])
            state = CmvBranchState(scenario_id=event["episode_id"], branch="natural", tick=0,
                particles=deepcopy(particles), ledger=initialize_cmv_event_ledger(event),
                memories_by_agent={aid: AgentMemory(agent_id=aid) for aid in particles.agent_ids})
            repo.commit_checkpoint(state)
            source = _NoopSemanticSource()
            def silent_observer(**kwargs):
                # A contract fixture, not empirical text labels or LLM evidence.
                rows = [{"agent_id": aid, "stance": "insufficient_evidence", "confidence": 0.0}
                        for aid in kwargs["rendered"]]
                return {row["agent_id"]: row for row in rows}, {
                    "records": rows, "fixture_only": True, "scope_definition": "whole_proposition_v2"}
            for tick in range(1, 4):
                execute_cmv_resident_tick(world=world, state=state, episode_context=contexts[tick],
                    relationship_release=world["relationships"], source=source, expected_model="fixture-model",
                    run_id=run_id, seed=20261002, plan_sha256=pf["plan_sha256"], world_sha256=world["world_sha256"],
                    activation_slots=[{"slot_kind": "resident", "tick": tick, "branch": "natural", "agent_id": aid,
                                       "inclusion_probability": probs[aid]} for aid in panel],
                    repository=repo, max_workers=1, private_actions_enabled=False,
                    particle_observation_model="scope-calibrated-v3" if structure else "directional-v2",
                    input_condition=pf["plan"]["input_conditions"][condition],
                    expression_observer=silent_observer if structure else None,
                    input_revision=3 if structure else 2,
                    frozen_state_input=particles if condition == "static_state" else None)
                for record in repo.runtime_artifacts("resident_input_batch", scenario_id=event["episode_id"], branch="natural", tick=tick)[0]["records"]:
                    payload = json.loads(record["agent_prompt"])
                    if len(payload["scenario_context"]["scenario_updates"]) != tick:
                        raise ValueError("dynamic information lost or leaked")
                    if condition == "no_state" and "posterior_state" in payload:
                        raise ValueError("no-state control leaked")
                    if condition == "static_state" and payload["posterior_state"].get("source_tick") != 0:
                        raise ValueError("static control lost source provenance")
                restored = repo.restore_latest("natural")
                if restored.tick != tick or not (restored.particles.values == state.particles.values).all():
                    raise ValueError("dynamic checkpoint recovery differs")
                if structure and restored.attention_history_by_agent != state.attention_history_by_agent:
                    raise ValueError("attention history recovery differs")
                state = restored
            result[condition] = {"tick": state.tick, "fixture_calls": source.calls, "public_messages_generated": 0}
    return {"status": "passed", "provider_calls": 0, "fixture_not_live_evidence": True, "cells": result}


def frozen_warmup():
    original = base.read(PARENT / "preflight.private.json")
    world, _, _, _ = base.inputs()
    states, events = {}, {}
    for condition in base.CONDITIONS:
        repo = CmvRunRepository(PARENT / condition,
            run_id="cmv-run-" + original["preflight_sha256"][:24],
            plan_sha256=original["plan_sha256"], world_sha256=world["world_sha256"])
        matches = [event for event in repo._read_events() if event["event_type"] == "tick_checkpoint_committed"
                   and event["branch"] == "natural" and event["tick"] == 1 and event["checkpoint_label"] == "tick"]
        if len(matches) != 1:
            raise ValueError("all four cells require one committed Tick1 warm-up")
        event = matches[0]
        repo.latest_checkpoint_event = lambda branch, checkpoint_label=None, selected=event: selected
        states[condition] = repo.restore_latest("natural")
        if states[condition].tick != 1:
            raise ValueError("warm-up horizon mismatch")
        events[condition] = event["event_sha256"]
    return states, events


def preflight():
    states, events = frozen_warmup()
    pf = base.preflight()
    pf.pop("preflight_sha256")
    plan = pf["plan"]
    plan.update(schema_version="cmv-corrected-natural-ablation-v2", warmup_tick=1,
        formal_ticks=list(range(2, 10)), warmup_event_sha256=events,
        expression_observer="cmv-expression-observer-v1", input_revision=2,
        observation_confidence_floor=0.60,
        measurement="read actual expression, not self-reported stance; no forced posterior assignment")
    for path in (Path(__file__), base.ROOT / "src/envs/campus_pulse_governance/reddit/expression_observer.py", base.ROOT / "scripts/cmv_standing_resume.py"):
        plan["runtime_files"][path.relative_to(base.ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    pf.update(plan_sha256=canonical_sha256(plan), primary_slots=320,
        auxiliary_expression_batches=32, total_new_primary_requests=352,
        reused_warmup_slots=40, semantic_repairs_per_slot=3, timeout_seconds=240,
        total_campaign_token_safety_limit=6_000_000,
        old_protocol_later_ticks="preserved separately, excluded from corrected comparison",
        sent_fields=pf["sent_fields"] + ["anonymous expression batches without profile/state/condition"])
    # Old unknown risks remain charged. The new 5M cap plus all old charges
    # must fit the user-authorized 6M envelope before any new request is sent.
    old_usage = base.read(PARENT / "progress.private.json")["provider"]
    if old_usage["provider_reported_tokens"] + old_usage["risk_charged_tokens"] + base.TOKEN_LIMIT > 6_000_000:
        raise ValueError("combined old/new conservative budget would exceed 6M")
    return {**pf, "preflight_sha256": canonical_sha256(pf)}, states


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--until-tick", type=int, default=9)
    parser.add_argument("--api-key-env", default="TIANTOKEN_API_KEY")
    parser.add_argument("--dynamic-preflight", action="store_true")
    parser.add_argument("--dynamic-fixture", action="store_true")
    parser.add_argument("--dynamic-run", action="store_true")
    parser.add_argument("--dynamic-repair-resume", action="store_true")
    parser.add_argument("--structure-preflight", action="store_true")
    parser.add_argument("--structure-fixture", action="store_true")
    parser.add_argument("--structure-run", action="store_true")
    parser.add_argument("--authorized-preflight-sha256")
    args = parser.parse_args()
    if args.structure_preflight or args.structure_fixture or args.structure_run:
        if args.dynamic_run or args.dynamic_preflight or args.dynamic_fixture or args.run or args.dynamic_repair_resume:
            parser.error("new structure uses its own frozen run; old experiments cannot resume into it")
        pf = dynamic_preflight(structure=True)
        base.OUT = base.ROOT / "data/private/reddit/cmv/structure-three-chains"
        path = base.OUT / "preflight.private.json"
        if path.exists() and base.read(path) != pf:
            if args.structure_run or (base.OUT / "provider-ledger.private.sqlite3").exists() or (base.OUT / "authorization.private.json").exists():
                raise ValueError("structure preflight changed; use a new release directory")
            # Preparation is replaceable until authorized; no live receipt or
            # frozen authorized run can be rewritten by this path.
        if args.structure_run:
            if not path.exists() or args.authorized_preflight_sha256 != pf["preflight_sha256"]:
                raise ValueError("live structure run requires the separately authorized exact preflight")
            from functools import partial
            world, context, particles, panel, probabilities, contexts = dynamic_inputs()
            base.REQUEST_LIMIT, base.TOKEN_LIMIT, base.CONCURRENCY = pf["request_limit"], pf["token_limit"], 1
            base.MODEL, base.ENDPOINT = pf["model"], pf["endpoint"]
            policy_body = {"user_authorized": True, "preflight_sha256": pf["preflight_sha256"],
                           "unknown_policy": "stop_no_retry", "rate_limit_pacing": True,
                           "max_known_transient_replacements": 3,
                           "scope": "bounded settled transient retries; every request charged; unknown stops",
                           "request_limit": pf["request_limit"], "token_limit": pf["token_limit"]}
            policy = {**policy_body, "authorization_fingerprint": canonical_sha256(policy_body)}
            base.write(base.OUT / "standing-continuation-authorization.private.json", policy)
            for restart in range(4):
                try:
                    base.run(pf, until_tick=min(args.until_tick, 3), api_key_env=args.api_key_env,
                        input_bundle=(world, context, particles, {str(t): panel for t in range(1, 4)}),
                        execution_conditions=pf["plan"]["conditions"], contexts_by_tick=contexts,
                        conditions_by_cell=pf["plan"]["input_conditions"], frozen_state_inputs={"static_state": particles},
                        controlled_repairs=True, standing_unknown_policy=policy,
                        expression_observer=partial(observe_expression_batch, scope_definition="whole_proposition_v2"))
                    break
                except SystemExit:
                    progress = base.read(base.OUT / "progress.private.json")
                    if progress.get("error_code") != "v7_live_known_provider_failure" or restart == 3:
                        raise
                    print(json.dumps({"status": "bounded_known_failure_resume", "restart": restart+1,
                                      "cached_outputs_reused": True}), flush=True)
            return
        base.write(path, pf)
        result = dynamic_fixture(structure=True) if args.structure_fixture else {
            "status": "ready_not_started", "resident_slots": 108, "provider_calls_started": 0,
            "preflight_sha256": pf["preflight_sha256"]}
        base.write(base.OUT / ("fixture-result.private.json" if args.structure_fixture else "preparation.private.json"), result)
        print(json.dumps(result))
        return
    if args.dynamic_run:
        from functools import partial
        if args.run or args.dynamic_fixture or args.dynamic_preflight:
            parser.error("select one dynamic dispatch mode")
        pf = dynamic_preflight()
        base.OUT = base.ROOT / "data/private/reddit/cmv/dynamic-state-comparison"
        base.REQUEST_LIMIT, base.TOKEN_LIMIT, base.CONCURRENCY = 260, 1000000, 1
        path = base.OUT / "preflight.private.json"
        if not path.exists():
            raise ValueError("prepare the exact dynamic plan before dispatch")
        old = base.read(path)
        if old != pf:
            from copy import deepcopy
            allowed = {"scripts/run_reddit_cmv_corrected_ablation.py", "scripts/run_reddit_cmv_input_ablation.py",
                       "src/envs/campus_pulse_governance/forum_v2_execution.py"}
            compared = []
            for candidate in (old, pf):
                view = deepcopy(candidate)
                view.pop("preflight_sha256")
                view.pop("plan_sha256")
                for name in allowed:
                    view["plan"]["runtime_files"].pop(name, None)
                compared.append(view)
            if not args.dynamic_repair_resume or compared[0] != compared[1]:
                raise ValueError("dynamic inputs, model, limits or non-recovery runtime changed")
            amendment = {"user_authorized": True, "old_preflight_sha256": old["preflight_sha256"],
                "current_runtime_files": pf["plan"]["runtime_files"],
                "scope": "serial fail-fast and bounded settled-format recovery; fixed inputs, successful cache, ledger and budgets unchanged"}
            base.write(base.OUT / "resume-runtime-amendment.private.json",
                {**amendment, "authorization_fingerprint": canonical_sha256(amendment)})
        # Keep the original authorization/cache/checkpoint identity for exact reuse.
        pf = old
        used = 0
        for directory in (PARENT, PARENT.parent / "input-ablation-natural-t9-corrected",
                          PARENT.parent / "input-ablation-natural-t9-corrected/independent-review"):
            if (directory / "progress.private.json").exists():
                usage = base.read(directory / "progress.private.json")["provider"]
                used += usage["provider_reported_tokens"] + usage["risk_charged_tokens"]
        if used + base.TOKEN_LIMIT > 6000000:
            raise ValueError("combined campaign dynamic budget insufficient")
        world, context, particles, panel, probabilities, contexts = dynamic_inputs()
        policy_body = {"user_authorized": True, "preflight_sha256": pf["preflight_sha256"],
                       "max_unknown_replacements_per_prompt": 2, "max_unknown_attempts_total": 8,
                       "max_transient_failures_per_prompt": 3, "rate_limit_pacing": True,
                       "late_results": "permanently_excluded", "both_requests_may_be_billed": True}
        policy = {**policy_body, "authorization_fingerprint": canonical_sha256(policy_body)}
        base.write(base.OUT / "standing-continuation-authorization.private.json", policy)
        for restart in range(7):
            try:
                base.run(pf, until_tick=3, api_key_env=args.api_key_env, standing_unknown_policy=policy,
                    controlled_repairs=args.dynamic_repair_resume,
                    input_bundle=(world, context, particles, {str(t): panel for t in range(1, 4)}),
                    execution_conditions=pf["plan"]["conditions"], contexts_by_tick=contexts,
                    conditions_by_cell=pf["plan"]["input_conditions"], frozen_state_inputs={"static_state": particles},
                    expression_observer=partial(observe_expression_batch, scope_definition="whole_proposition_v2"))
                break
            except SystemExit:
                progress = base.read(base.OUT / "progress.private.json")
                transient = progress.get("error_code") == "v7_live_known_provider_failure"
                if (progress.get("error_type") != "V7LiveUnknownOutcome" and not transient) or restart == 6:
                    raise
                if transient:
                    print(json.dumps({"status": "transient_failure_cooldown", "restart": restart+1,
                                      "minimum_seconds": 120}), flush=True)
                else:
                    time.sleep(10)
        return
    if args.dynamic_preflight or args.dynamic_fixture:
        if args.run:
            parser.error("dynamic preparation excludes old-run dispatch")
        pf = dynamic_preflight()
        output = base.ROOT / "data/private/reddit/cmv/dynamic-state-comparison"
        path = output / "preflight.private.json"
        if path.exists() and base.read(path) != pf:
            raise ValueError("dynamic frozen preflight changed")
        base.write(path, pf)
        result = dynamic_fixture() if args.dynamic_fixture else {"status": "ready_not_started", "resident_slots": 108,
            "auxiliary_batches": 9, "token_limit": pf["token_limit"]}
        base.write(output / ("fixture-result.private.json" if args.dynamic_fixture else "preparation.private.json"), result)
        print(json.dumps(result))
        return
    pf, states = preflight()
    path = base.OUT / "preflight.private.json"
    if path.exists() and base.read(path) != pf:
        old = base.read(path)
        before, after = json.loads(json.dumps(old)), json.loads(json.dumps(pf))
        interface_files = ("scripts/run_reddit_cmv_corrected_ablation.py", "src/envs/campus_pulse_governance/reddit/expression_observer.py",
                           "scripts/run_reddit_cmv_input_ablation.py", "scripts/cmv_standing_resume.py")
        for version in (before, after):
            version.pop("preflight_sha256")
            version.pop("plan_sha256")
            for name in interface_files:
                version["plan"]["runtime_files"].pop(name, None)
        if before != after:
            raise ValueError("corrected simulation inputs changed; refuse continuation")
        base.write(base.OUT / "observer-interface-fix.private.json", {
            "old_preflight_sha256": old["preflight_sha256"],
            "current_files": {name: pf["plan"]["runtime_files"][name] for name in interface_files},
            "change": "pass required prompt_sha256 to existing acquire interface; prompts and simulation unchanged"})
        pf = old
    base.write(path, pf)
    if args.run:
        policy_body = {"user_authorized": True, "preflight_sha256": pf["preflight_sha256"],
            "scope": "standing permission to replace unknowns and resume this corrected ablation",
            "max_unknown_replacements_per_prompt": 2, "max_unknown_attempts_total": 8,
            "max_supervisor_restarts": 6, "late_results": "permanently_excluded",
            "both_requests_may_be_billed": True, "aggregate_token_limit": 6000000}
        policy = {**policy_body, "authorization_fingerprint": canonical_sha256(policy_body)}
        base.write(base.OUT / "standing-continuation-authorization.private.json", policy)
        for restart in range(policy["max_supervisor_restarts"] + 1):
            try:
                base.run(pf, until_tick=args.until_tick, api_key_env=args.api_key_env,
                         controlled_repairs=True, warmup_states=states,
                         expression_observer=observe_expression_batch, standing_unknown_policy=policy)
                break
            except SystemExit:
                progress = base.read(base.OUT / "progress.private.json")
                # Do not treat coding errors, credential errors, or exhausted
                # settled-format repairs as a reason to buy more requests.
                if progress.get("error_type") != "V7LiveUnknownOutcome" or restart == policy["max_supervisor_restarts"]:
                    raise
                print({"bounded_auto_resume": restart + 1, "reason": "unknown",
                       "cached_outputs_reused": True}, flush=True)
                time.sleep(10)
    else:
        print({"new_resident_slots": 320, "auxiliary_batches": 32,
               "warmup_slots_reused": 40, "final_tick": 9,
               "request_limit": base.REQUEST_LIMIT, "token_limit": base.TOKEN_LIMIT})


if __name__ == "__main__":
    main()
