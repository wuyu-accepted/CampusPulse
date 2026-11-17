"""196-slot Natural common-observation run using the existing CMV executor.

No baseline is dispatched. Freeze inputs before calls, commit complete ticks,
and export inputs and CampusPulse decisions in the existing handoff layout.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from contextlib import closing
from functools import partial
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time

import run_reddit_cmv_corrected_ablation as corrected
import backend.campus_pulse.yulan_scale_live as live
from backend.campus_pulse.runtime_contract import canonical_sha256
from backend.campus_pulse.reddit_cmv_execution import CmvRunRepository
from envs.campus_pulse_governance.reddit.expression_observer import observe_expression_batch
from envs.campus_pulse_governance.reddit.run_plan import _select_panel
from resume_reddit_cmv_structure import (
    clarified_repair_renderer, diagnosed_shape_normalizer, format_exhaustion_repairs, policy_for)

base = corrected.base
OUT = base.ROOT / "data/private/reddit/cmv/natural-common-196"
SEED = 20261002
SIZES = (20, 20, 20, 24, 24, 24, 24, 20, 20)


def inputs():
    world, event, particles, _, _, early_contexts = corrected.dynamic_inputs()
    panels, probabilities = {}, {}
    for tick, size in enumerate(SIZES, 1):
        selected, probs = _select_panel(world["agents"], sample_size=size, seed=SEED, tick=tick)
        panels[str(tick)] = [row["agent_id"] for row in selected]
        probabilities[str(tick)] = probs
    contexts = {tick: deepcopy(early_contexts[min(tick, 3)]) for tick in range(1, 10)}
    return world, event, particles, panels, probabilities, contexts


def preflight():
    world, event, particles, panels, probs, contexts = inputs()
    inherited = corrected.dynamic_preflight(structure=True)
    paths = set(inherited["plan"]["runtime_files"]) | {
        "scripts/run_reddit_cmv_natural_panel.py", "scripts/resume_reddit_cmv_structure.py",
        "src/backend/campus_pulse/yulan_scale_live.py", "src/backend/campus_pulse/yulan_scale_live_runtime.py",
    }
    plan = {
        "schema": "cmv-natural-common-observation-panel-v1", "world_sha256": world["world_sha256"],
        "initial_particles_sha256": hashlib.sha256(base.INITIAL.read_bytes()).hexdigest(),
        "seed": SEED, "ticks": list(range(1, 10)), "conditions": ["full"],
        "panels_by_tick": panels, "panel_probabilities_by_tick": probs,
        "sizes_by_tick": list(SIZES), "contexts_by_tick": {str(k):v for k,v in contexts.items()},
        "input_revision": 3, "particle_observation_model": "scope-calibrated-v3",
        "observer_scope": "whole_proposition_v2", "private_actions_enabled": False,
        "governance_slots": 0, "event_proposition_constant": event["decision_question"],
        "new_information_provenance": "synthetic_scenario_assumptions_not_historical_continuation",
        "after_tick_3": "No new exogenous facts; discussion continues from the same first-year grant information",
        "same_tick_visibility": "frozen input; all validated outputs committed together in Agent order",
        "old_run_cache": "not imported across authorization identities; new run keeps its own exact cache",
        "runtime_files": {p:hashlib.sha256((base.ROOT/p).read_bytes()).hexdigest() for p in sorted(paths)},
    }
    body = {"plan": plan, "plan_sha256": canonical_sha256(plan), "model": "gpt-6-luna",
        "reasoning_effort": "none", "endpoint": base.ENDPOINT, "primary_slots": sum(SIZES),
        "auxiliary_expression_batches": 9, "final_tick": 9, "request_limit": 500,
        "token_limit": 2_000_000, "timeout_seconds": 180, "max_concurrency": 2,
        "minimum_request_interval_seconds": 5, "format_repair_limit": 1,
        "authorization_scope": "User approved latest-code Natural 196 residents through Tick9 within 2M tokens; bounded resume; no baselines"}
    return {**body, "preflight_sha256": canonical_sha256(body)}


def fixture(pf):
    from unittest.mock import patch
    from backend.tests.test_reddit_cmv_execution import _NoopSemanticSource
    from backend.campus_pulse.reddit_cmv_execution import CmvBranchState, execute_cmv_resident_tick
    from envs.campus_pulse_governance.forum_twin import AgentMemory
    from envs.campus_pulse_governance.reddit.runtime import initialize_cmv_event_ledger
    world, event, particles, panels, probs, contexts = inputs()
    with tempfile.TemporaryDirectory() as folder, patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
        rid = "cmv-run-" + pf["preflight_sha256"][:24]
        repo = CmvRunRepository(folder, run_id=rid, plan_sha256=pf["plan_sha256"], world_sha256=world["world_sha256"])
        state = CmvBranchState(scenario_id=event["episode_id"], branch="natural", tick=0,
            particles=deepcopy(particles), ledger=initialize_cmv_event_ledger(event),
            memories_by_agent={aid:AgentMemory(agent_id=aid) for aid in particles.agent_ids})
        repo.commit_checkpoint(state)
        def observer(**kwargs):
            rows = {aid:{"agent_id":aid,"stance":"insufficient_evidence","confidence":0.0}
                    for aid in kwargs["rendered"]}
            return rows, {"records":list(rows.values()),"fixture_only":True,"scope_definition":"whole_proposition_v2"}
        for tick in range(1, 10):
            execute_cmv_resident_tick(world=world, state=state, episode_context=contexts[tick],
                relationship_release=world["relationships"], source=_NoopSemanticSource(), expected_model="fixture-model",
                run_id=rid, seed=SEED, plan_sha256=pf["plan_sha256"], world_sha256=world["world_sha256"],
                activation_slots=[{"slot_kind":"resident","tick":tick,"branch":"natural","agent_id":aid,
                    "inclusion_probability":probs[str(tick)][aid]} for aid in panels[str(tick)]],
                repository=repo, max_workers=2, private_actions_enabled=False, particle_observation_model="scope-calibrated-v3",
                input_condition="full", expression_observer=observer, input_revision=3)
            if repo.restore_latest("natural").tick != tick:
                raise ValueError("fixture restore frontier mismatch")
    return {"status":"fixture_passed", "residents":196,"ticks":9,"provider_calls":0,
            "language_outputs":"none; fixture is not an experimental result"}


def export_completed(pf):
    from package_reddit_baseline_handoff import write_lines
    from envs.campus_pulse_governance.reddit.particle_model import save_particle_snapshot, STATE_FIELDS
    progress = base.read(OUT / "progress.private.json")
    if progress["status"] != "completed" or progress["tick_frontiers"] != {"full":9}:
        raise ValueError("cannot export incomplete 196-slot run")
    repo = CmvRunRepository(OUT / "full", run_id=progress["provider"]["run_id"],
        plan_sha256=pf["plan_sha256"], world_sha256=pf["plan"]["world_sha256"])
    events = repo._read_events()
    with closing(sqlite3.connect((OUT/"provider-ledger.private.sqlite3").as_uri()+"?mode=ro", uri=True)) as conn:
        cache = {sha:json.loads(raw) for sha,raw in conn.execute("SELECT prompt_sha256,validated_decision_json FROM v7_live_cache")}
    observations, results = [], []
    for e in events:
        if e.get("artifact_type") != "resident_input_batch":
            continue
        for r in e["artifact"]["records"]:
            identity = {"case_id":hashlib.sha256(r["prompt_sha256"].encode()).hexdigest()[:20],
                        "agent_id":r["agent_id"],"branch":"natural","tick":e["tick"]}
            decision = cache[r["prompt_sha256"]]
            observations.append({**identity,"input":json.loads(r["agent_prompt"]),"system_prompt":r["system_prompt"],
                "previous_tick_state":r["previous_tick_state"],"decision_input_state":r["decision_input_state"],
                "previous_stance_probabilities":r["previous_stance_probabilities"],
                "pre_decision_stance_probabilities":r["pre_decision_stance_probabilities"]})
            results.append({**identity,"method":"CampusPulse","output":decision})
    if len(observations) != 196 or len({r["case_id"] for r in observations}) != 196:
        raise ValueError("Natural input/output panel count mismatch")
    export = OUT / "handoff"
    write_lines(export / "inputs/paired-observations.jsonl.gz", observations)
    write_lines(export / "evaluation-only/ours-decisions.jsonl.gz", results)
    population, particles = [], []
    selector = repo.latest_checkpoint_event
    try:
        for e in events:
            if e.get("event_type") != "tick_checkpoint_committed":
                continue
            repo.latest_checkpoint_event = lambda branch, checkpoint_label=None, chosen=e: chosen
            state = repo.restore_latest("natural")
            snapshot = state.particles
            save_particle_snapshot(snapshot, export / "evaluation-only/particles" / f"natural-tick-{e['tick']}.npz")
            means = (snapshot.values*snapshot.conditional_weights[:,:,None]).sum(axis=1)
            for i,aid in enumerate(snapshot.agent_ids):
                identity = {"agent_id":aid,"branch":"natural","tick":e["tick"],"condition":"full"}
                population.append({**identity,**{f:float(means[i,k]) for k,f in enumerate(STATE_FIELDS)}})
                for j in range(10):
                    particles.append({**identity,"particle_index":j,"conditional_weight":float(snapshot.conditional_weights[i,j]),
                        "design_weight":float(snapshot.design_weights[i]),
                        **{f:float(snapshot.values[i,j,k]) for k,f in enumerate(STATE_FIELDS)}})
    finally:
        repo.latest_checkpoint_event = selector
    write_lines(export / "evaluation-only/combined_population_states.jsonl.gz", population)
    write_lines(export / "evaluation-only/full-particles.jsonl.gz", particles)
    result = {"status":"completed", "natural_observations":196,"paired_CampusPulse_decisions":196,
              "particle_checkpoints":10,"parent_state_rows":len(population),"particle_rows":len(particles),
              "provider":progress["provider"],"new_analysis_provider_calls":0}
    base.write(OUT / "completed-results.private.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--api-key-env", default="TIANTOKEN_API_KEY")
    args = parser.parse_args()
    base.OUT = OUT
    completed = OUT / "completed-results.private.json"
    if completed.exists():
        print(json.dumps({"status":"already_completed","provider_calls":0,"residents":196}))
        return
    pf = preflight()
    path = OUT / "preflight.private.json"
    if path.exists() and base.read(path) != pf:
        raise ValueError("frozen runtime changed; refuse continuation")
    base.write(path, pf)
    if args.fixture:
        result = fixture(pf)
        base.write(OUT / "fixture-result.private.json", result)
        print(json.dumps(result))
        return
    if not args.run:
        print(json.dumps({"status":"prepared","residents":196,"observation_batches":9,"provider_calls":0,
                          "concurrency":2,"request_limit":500,"token_limit":2_000_000}))
        return
    if not (OUT / "fixture-result.private.json").is_file():
        raise ValueError("run requires prior zero-call fixture")
    base.SEED, base.REQUEST_LIMIT, base.TOKEN_LIMIT, base.CONCURRENCY = SEED, 500, 2_000_000, 2
    base.MODEL, base.ENDPOINT = pf["model"], pf["endpoint"]
    policy = policy_for(pf)
    base.write(OUT / "standing-unknown-continuation-authorization.private.json", policy)
    world, event, particles, panels, probs, contexts = inputs()
    from cmv_standing_resume import RateLimitedV7Transport
    RateLimitedV7Transport.minimum_interval_seconds = 5.0
    source_factory = base.AuthorizedV7SemanticSource
    live._semantic_repair_turn = clarified_repair_renderer(live._semantic_repair_turn)
    lock, diagnostics = threading.Lock(), []
    def diagnostic(record):
        with lock:
            diagnostics.append(record)
            base.write(OUT / "response-shape-diagnostics.private.json", {"records":diagnostics,"provider_text_saved":False})
    live._normalize_forum_resident_v4_provider_shape = diagnosed_shape_normalizer(
        live._normalize_forum_resident_v4_provider_shape, diagnostic)
    workers, history = 2, []
    for restart in range(policy["max_supervisor_restarts"] + 1):
        db = OUT / "provider-ledger.private.sqlite3"
        repairs = format_exhaustion_repairs(db, policy) if db.exists() else ()
        base.AuthorizedV7SemanticSource = partial(source_factory, manual_format_exhaustion_repairs=repairs)
        started = time.monotonic()
        try:
            base.run(pf, until_tick=9, api_key_env=args.api_key_env, input_bundle=(world,event,particles,panels),
                execution_conditions=("full",), contexts_by_tick=contexts, conditions_by_cell={"full":"full"},
                expression_observer=partial(observe_expression_batch, scope_definition="whole_proposition_v2"),
                controlled_repairs=True, standing_unknown_policy=policy, resident_max_workers=workers)
        except SystemExit:
            progress = base.read(OUT / "progress.private.json")
            history.append({"restart":restart,"seconds":round(time.monotonic()-started,2),"progress":progress})
            base.write(OUT / "continuation-supervisor.private.json", {"history":history})
            if progress.get("error_code") not in {"v7_live_unknown_outcome","v7_live_known_provider_failure","v7_live_format_repair_exhausted"} or restart >= policy["max_supervisor_restarts"]:
                raise
            if progress.get("error_code") == "v7_live_known_provider_failure":
                workers = 1
                RateLimitedV7Transport.minimum_interval_seconds = 15.0
            print(json.dumps({"status":"bounded_resume","restart":restart+1,"workers":workers,"cache_reused":True}),flush=True)
            time.sleep(15)
        else:
            history.append({"restart":restart,"seconds":round(time.monotonic()-started,2),"progress":base.read(OUT / "progress.private.json")})
            base.write(OUT / "continuation-supervisor.private.json", {"history":history})
            print(json.dumps(export_completed(pf)), flush=True)
            return


if __name__ == "__main__":
    main()
