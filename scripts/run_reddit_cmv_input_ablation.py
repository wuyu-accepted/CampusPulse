"""Four-cell Natural free-evolution input ablation using the CMV live ledger.

No shared generated trajectory: every cell owns its messages and memory.
Credentials are read only from a process environment variable.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import random
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from backend.campus_pulse.runtime_contract import canonical_sha256
from backend.campus_pulse.reddit_cmv_execution import CmvBranchState, CmvRunRepository, execute_cmv_resident_tick
from backend.campus_pulse.yulan_scale_live import AuthorizedV7SemanticSource, ForumPilotLiveFactoryContext, V7LiveAuthorizationBinding, V7ManualUnknownOutcomeResolution, V7ControlledRepairPolicy, require_formal_authorized_source
from backend.campus_pulse.yulan_scale_live_runtime import SQLiteV7AuthorizedTurnRepository, VectorEngineV7Transport, vectorengine_transport_fingerprint
from envs.campus_pulse_governance.forum_twin import AgentMemory
from envs.campus_pulse_governance.reddit.episodes import build_episode_scenario_context
from envs.campus_pulse_governance.reddit.particle_model import load_particle_snapshot
from envs.campus_pulse_governance.reddit.runtime import initialize_cmv_event_ledger

CONDITIONS = ("full", "context_only", "profile_only", "state_only")
AUTHORIZED_UNKNOWNS = ("ylva_b92e4324d6148b9cda6889b0", "ylva_35793987c3dd630d192e594b")
OUT = ROOT / "data/private/reddit/cmv/input-ablation-natural-t9"
WORLD = ROOT / "data/private/reddit/cmv/lightweight-v3/world-runtime-r2/fixed-world.private.json"
EVENT = ROOT / "data/private/reddit/cmv/event-overlays/healthcare-education-v2/development-event.private.json"
INITIAL = ROOT / "output/Reddit-Baseline-Ready-20261001/world/initial-particles.npz"
ENDPOINT = "https://api.tiantoken.com/v1/chat/completions"
MODEL = "gpt-6-luna"
REQUEST_LIMIT, TOKEN_LIMIT, CONCURRENCY = 720, 2_000_000, 2
SEED = 20261001


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".tmp")
    with pending.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(pending, path)


def inputs():
    world = read(WORLD)
    if canonical_sha256({k: v for k, v in world.items() if k != "world_sha256"}) != world["world_sha256"]:
        raise ValueError("world checksum mismatch")
    context = build_episode_scenario_context(read(EVENT)["episode"])
    particles = load_particle_snapshot(INITIAL)
    ids = sorted(row["agent_id"] for row in world["agents"])
    if set(ids) != set(particles.agent_ids) or particles.tick != 0:
        raise ValueError("initial particles do not match the fixed world")
    panels = {}
    for tick in range(1, 10):
        panels[str(tick)] = sorted(random.Random(f"{SEED}:{tick}").sample(ids, 10))
    return world, context, particles, panels


def preflight():
    world, context, particles, panels = inputs()
    source_files = [Path(__file__), ROOT / "src/envs/campus_pulse_governance/reddit/runtime.py",
                    ROOT / "src/backend/campus_pulse/reddit_cmv_execution.py",
                    ROOT / "src/envs/campus_pulse_governance/forum_turns.py",
                    ROOT / "src/envs/campus_pulse_governance/reddit/particle_model.py"]
    plan = {
        "schema_version": "cmv-natural-input-ablation-v1", "conditions": list(CONDITIONS),
        "ticks": list(range(1, 10)), "branch": "natural", "seed": SEED,
        "activation_panels": panels, "inclusion_probability": 10 / len(particles.agent_ids),
        "world_sha256": world["world_sha256"], "event_context_sha256": canonical_sha256(context),
        "initial_particles_sha256": hashlib.sha256(INITIAL.read_bytes()).hexdigest(),
        "runtime_files": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files},
        "private_actions_enabled": False, "particle_observation_model": "directional-v2",
        "memory": "own_previous_llm_turns", "state_disabled": "shadow_diagnostics_not_llm_input",
        "attention": "same_frozen_profile_based_feed_mechanism_in_all_cells",
    }
    body = {
        "plan": plan, "plan_sha256": canonical_sha256(plan), "endpoint": ENDPOINT, "model": MODEL,
        "reasoning_effort": "none", "primary_slots": 360, "request_limit": REQUEST_LIMIT,
        "token_limit": TOKEN_LIMIT, "max_concurrency": CONCURRENCY, "format_repair_limit": 1,
        "network_retry_limit": 0, "safe_preconnect_retry_limit": 2, "unknown": "stop_no_retry",
        "timeout_seconds": 120,
        "sent_fields": ["condition_enabled_profile", "condition_enabled_particle_state", "synthetic_memory", "time_visible_forum", "redacted_event", "output_contract"],
    }
    return {**body, "preflight_sha256": canonical_sha256(body)}


def live_identities(pf):
    auth = {"user_authorized": True, "scope": pf.get("authorization_scope", "four input ablations Natural through Tick9"),
            "preflight_sha256": pf["preflight_sha256"]}
    auth_sha = canonical_sha256(auth)
    run_id = "cmv-run-" + pf["preflight_sha256"][:24]
    project_id = "cmv-ablation-" + pf["plan_sha256"][:24]
    input_sha = canonical_sha256({"plan": pf["plan_sha256"], "authorization": auth_sha})
    launch_sha = canonical_sha256({"run": run_id, "input": input_sha, "concurrency": CONCURRENCY})
    endpoint_sha = canonical_sha256({"endpoint": ENDPOINT})
    fp = vectorengine_transport_fingerprint(endpoint=ENDPOINT, model_name=MODEL,
        timeout_seconds=pf["timeout_seconds"], safe_preconnect_retry_limit=2, local_https_proxy=None)
    shared = dict(run_id=run_id, project_id=project_id, input_fingerprint=input_sha,
                  launch_fingerprint=launch_sha, plan_sha256=pf["plan_sha256"],
                  model_name=MODEL, endpoint_fingerprint=endpoint_sha)
    context = ForumPilotLiveFactoryContext(**shared, reasoning_effort="none",
        provider_request_limit=REQUEST_LIMIT, provider_token_limit=TOKEN_LIMIT,
        max_concurrency=CONCURRENCY, network_retry_limit=0, format_repair_limit=1)
    binding = V7LiveAuthorizationBinding(**shared, tenant_id="tenant-campus-pulse",
        lease_id="lease_" + auth_sha[:24], lease_generation=1,
        authorization_id="auth_" + auth_sha[:24], authorization_fingerprint=auth_sha,
        preflight_sha256=pf["preflight_sha256"], transport_config_fingerprint=fp)
    return auth, context, binding


def run(pf, *, until_tick, api_key_env, authorized_unknown=None, controlled_repairs=False,
        warmup_states=None, expression_observer=None, standing_unknown_policy=None,
        input_bundle=None, execution_conditions=None, contexts_by_tick=None,
        conditions_by_cell=None, frozen_state_inputs=None, resident_max_workers=None):
    if not 1 <= until_tick <= 9:
        raise ValueError("until-tick outside frozen plan")
    workers = CONCURRENCY if resident_max_workers is None else resident_max_workers
    if type(workers) is not int or not 1 <= workers <= CONCURRENCY:
        raise ValueError("resident workers exceed authorized concurrency")
    world, event, particles, panels = inputs() if input_bundle is None else input_bundle
    conditions = tuple(CONDITIONS if execution_conditions is None else execution_conditions)
    if not conditions or len(set(conditions)) != len(conditions):
        raise ValueError("experiment cells must be unique and nonempty")
    auth, context, binding = live_identities(pf)
    repair_policy = None
    if controlled_repairs:
        amendment = {"user_authorized": True, "preflight_sha256": pf["preflight_sha256"],
            "request_limit": REQUEST_LIMIT, "token_limit": TOKEN_LIMIT, "semantic_repairs_per_slot": 3,
            "unknown_policy": "stop_no_blind_retry", "scope": "settled-format repair within existing request and token limits"}
        amendment_sha = canonical_sha256(amendment)
        write(OUT / "budget-repair-amendment.private.json", {**amendment, "authorization_fingerprint": amendment_sha})
        # Existing ledger identity is immutable. The larger allowance belongs
        # to the corrected successor release; finish its frozen warm-up here
        # under the original (still ample) budget, without rewriting context.
        repair_policy = V7ControlledRepairPolicy(run_id=binding.run_id,
            preflight_sha256=binding.preflight_sha256, authorization_fingerprint=amendment_sha,
            prior_authorization_fingerprint=binding.authorization_fingerprint,
            max_semantic_repairs_per_slot=3)
    write(OUT / "authorization.private.json", auth)
    db = OUT / "provider-ledger.private.sqlite3"
    provider = SQLiteV7AuthorizedTurnRepository(db, binding=binding, context=context, initialize=not db.exists())
    prior = provider.probe()
    resolutions = []
    transient_replacements = ()
    if standing_unknown_policy is not None:
        from cmv_standing_resume import load_authorized_unknown_resolutions, load_authorized_transient_failures
        if standing_unknown_policy.get("unknown_policy") != "stop_no_retry":
            resolutions.extend(load_authorized_unknown_resolutions(
                db_path=db, output_dir=OUT, preflight=pf, policy=standing_unknown_policy))
        transient_replacements = load_authorized_transient_failures(db_path=db, preflight=pf, policy=standing_unknown_policy)
    if authorized_unknown:
        with sqlite3.connect(db) as connection:
            unknowns = connection.execute("SELECT attempt_id,prompt_sha256,request_sha256,settlement_json FROM v7_live_attempts WHERE outcome='unknown'").fetchall()
        if authorized_unknown not in {row[0] for row in unknowns} or any(row[0] not in AUTHORIZED_UNKNOWNS for row in unknowns):
            raise ValueError("unknown authorization does not match the exact unresolved attempt")
        for attempt, prompt_sha, request_sha, settlement in unknowns:
            if attempt != authorized_unknown:
                # An old unknown remains in the immutable billing ledger even
                # after its replacement committed. Never reauthorize it here.
                completed = CmvRunRepository(OUT / "profile_only", run_id=context.run_id,
                    plan_sha256=pf["plan_sha256"], world_sha256=world["world_sha256"]).restore_latest("natural")
                if attempt != "ylva_b92e4324d6148b9cda6889b0" or completed is None or completed.tick < 1:
                    raise ValueError("historical unknown is not covered by a committed replacement")
                continue
            evidence = {"failed_attempt_id": attempt, "prompt_sha256": prompt_sha,
                "request_sha256": request_sha, "risk_charged_tokens": json.loads(settlement)["risk_charged_tokens"],
                "user_authorized": True, "resolution": "abandon_and_replace", "replacement_limit": 1,
                "late_result_policy": "permanently_excluded", "both_requests_may_be_billed": True,
                "preflight_sha256": pf["preflight_sha256"]}
            fingerprint = canonical_sha256(evidence)
            write(OUT / f"unknown-resolution-{attempt}.private.json", {**evidence, "authorization_fingerprint": fingerprint})
            repair_fields = {}
            with sqlite3.connect(db) as connection:
                invalid = connection.execute("SELECT attempt_id,settlement_json FROM v7_live_attempts WHERE prompt_sha256=? AND outcome='format_invalid' AND attempt_ordinal=1 ORDER BY rowid DESC LIMIT 1", (prompt_sha,)).fetchone()
            if invalid:
                failed_response = json.loads(invalid[1])["response_sha256"]
                repair_evidence = {"failed_attempt_id": invalid[0], "response_sha256": failed_response,
                    "parent_authorization": fingerprint, "format_repair_limit": 1,
                    "scope": "one existing preflight format repair for the settled replacement"}
                repair_sha = canonical_sha256(repair_evidence)
                write(OUT / f"replacement-format-repair-{attempt}.private.json", {**repair_evidence, "authorization_fingerprint": repair_sha})
                repair_fields = {"format_repair_authorization_fingerprint": repair_sha,
                    "format_repair_failed_attempt_id": invalid[0],
                    "format_repair_failed_response_sha256": failed_response, "format_repair_limit": 1}
            resolutions.append(V7ManualUnknownOutcomeResolution(failed_attempt_id=attempt,
                prompt_sha256=prompt_sha, request_sha256=request_sha,
                risk_charged_tokens=evidence["risk_charged_tokens"], authorization_fingerprint=fingerprint,
                **repair_fields))
    if (prior.provider_calls_unknown and not resolutions) or prior.tokens_in_flight or prior.provider_calls_reserved:
        raise ValueError("unresolved provider attempt; automatic continuation forbidden")
    from cmv_standing_resume import RateLimitedV7Transport
    transport_type = RateLimitedV7Transport if standing_unknown_policy and standing_unknown_policy.get("rate_limit_pacing") else VectorEngineV7Transport
    transport = transport_type(endpoint=ENDPOINT, model_name=MODEL, timeout_seconds=pf["timeout_seconds"],
        api_key_env=api_key_env, expected_fingerprint=binding.transport_config_fingerprint,
        safe_preconnect_retry_limit=2, local_https_proxy=None)
    source = require_formal_authorized_source(AuthorizedV7SemanticSource(
        context=context, binding=binding, repository=provider, transport=transport,
        controlled_repair_policy=repair_policy,
        manual_known_failure_replacements=transient_replacements,
        manual_unknown_outcome_resolutions=tuple(resolutions)))
    states, repositories = {}, {}
    for condition in conditions:
        repo = CmvRunRepository(OUT / condition, run_id=context.run_id,
            plan_sha256=pf["plan_sha256"], world_sha256=world["world_sha256"])
        state = repo.restore_latest("natural")
        if state is None:
            # load separately so no cell can mutate another cell's baseline.
            if warmup_states is not None:
                from copy import deepcopy
                state = deepcopy(warmup_states[condition])
                state.previous_checkpoint_sha256 = ""
                state.previous_domain_sha256 = ""
                state.checkpoint_label = "frozen-warmup"
            else:
                state = CmvBranchState(scenario_id=event["episode_id"], branch="natural", tick=0,
                    particles=load_particle_snapshot(INITIAL), ledger=initialize_cmv_event_ledger(event),
                    memories_by_agent={aid: AgentMemory(agent_id=aid) for aid in particles.agent_ids})
            repo.commit_checkpoint(state)
        states[condition], repositories[condition] = state, repo
    started = time.monotonic()
    progress = {"status": "running", "tick_frontiers": {k: v.tick for k, v in states.items()}, "provider": provider.audit_summary()}
    write(OUT / "progress.private.json", progress)
    try:
        for tick in range(1, until_tick + 1):
            for condition in conditions:
                state = states[condition]
                if state.tick >= tick:
                    continue
                before = time.monotonic()
                slots = [{"slot_kind": "resident", "tick": tick, "branch": "natural",
                          "agent_id": aid, "inclusion_probability": pf["plan"].get("panel_probabilities_by_tick", {}).get(str(tick),
                              pf["plan"].get("panel_probabilities", {})).get(aid, 10 / len(particles.agent_ids))}
                         for aid in panels[str(tick)]]
                tick_context = (contexts_by_tick or {}).get(tick, event)
                summary = execute_cmv_resident_tick(world=world, state=state, episode_context=tick_context,
                    relationship_release=world["relationships"], activation_slots=slots, source=source,
                    expected_model=MODEL, run_id=context.run_id, seed=SEED, plan_sha256=pf["plan_sha256"],
                    world_sha256=world["world_sha256"], repository=repositories[condition],
                    max_workers=workers, private_actions_enabled=False,
                    particle_observation_model=pf["plan"].get("particle_observation_model", "directional-v2"),
                    input_condition=(conditions_by_cell or {}).get(condition, condition),
                    expression_observer=expression_observer,
                    frozen_state_input=(frozen_state_inputs or {}).get(condition),
                    input_revision=pf["plan"].get("input_revision", 2 if expression_observer is not None else 1))
                progress = {"status": "running", "tick_frontiers": {k: v.tick for k, v in states.items()},
                            "last_tick_seconds": round(time.monotonic() - before, 2),
                            "elapsed_seconds": round(time.monotonic() - started, 2),
                            "provider": provider.audit_summary()}
                write(OUT / "progress.private.json", progress)
                print(json.dumps({"condition": condition, "tick": tick,
                    "seconds": progress["last_tick_seconds"], "calls": progress["provider"]["provider_calls_completed"],
                    "tokens": progress["provider"]["provider_reported_tokens"]}), flush=True)
        progress["status"] = "completed" if until_tick == pf.get("final_tick", 9) else "audit_batch_completed"
        write(OUT / "progress.private.json", progress)
    except Exception as exc:
        write(OUT / "progress.private.json", {"status": "stopped", "error_type": type(exc).__name__, "error_code": getattr(exc, "code", None),
            "tick_frontiers": {k: v.tick for k, v in states.items()}, "provider": provider.audit_summary()})
        # Never emit exception text: Provider failures may embed a response body.
        print(json.dumps({"status": "stopped", "error_type": type(exc).__name__, "code": getattr(exc, "code", None)}), flush=True)
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--until-tick", type=int, default=9)
    parser.add_argument("--api-key-env", default="TIANTOKEN_API_KEY")
    parser.add_argument("--authorized-unknown", choices=AUTHORIZED_UNKNOWNS)
    parser.add_argument("--controlled-repairs", action="store_true")
    args = parser.parse_args()
    pf = preflight()
    path = OUT / "preflight.private.json"
    if path.exists() and read(path) != pf:
        old = read(path)
        old_comparison = json.loads(json.dumps(old))
        new_comparison = json.loads(json.dumps(pf))
        for comparison in (old_comparison, new_comparison):
            comparison.pop("preflight_sha256")
            comparison.pop("plan_sha256")
            comparison["plan"]["runtime_files"].pop("scripts/run_reddit_cmv_input_ablation.py")
        if not (args.authorized_unknown or args.controlled_repairs) or old_comparison != new_comparison:
            raise ValueError("frozen simulation changed; continuation forbidden")
        write(OUT / "continuation-runtime.private.json", {"old_preflight_sha256": old["preflight_sha256"],
            "runner_sha256": pf["plan"]["runtime_files"]["scripts/run_reddit_cmv_input_ablation.py"],
            "change": "exact authorized unknown replacement and resume; simulation inputs unchanged"})
        pf = old
    write(path, pf)
    if args.run:
        run(pf, until_tick=args.until_tick, api_key_env=args.api_key_env,
            authorized_unknown=args.authorized_unknown, controlled_repairs=args.controlled_repairs)
    else:
        print(json.dumps({"primary_slots": 360, "ticks": "1-9", "conditions": CONDITIONS,
                          "request_limit": REQUEST_LIMIT, "token_limit": TOKEN_LIMIT}))


if __name__ == "__main__":
    main()
