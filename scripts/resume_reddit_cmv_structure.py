"""Resume the frozen three-chain experiment under bounded standing permission.

Only retry orchestration changes. Successful prompt-bound cache, original
attempt settlements, model, inputs, checkpoints and budgets remain unchanged.
"""
import argparse
import json
import time
import sqlite3
import re
from collections import defaultdict
from contextlib import closing
from dataclasses import replace
from functools import partial

import run_reddit_cmv_corrected_ablation as corrected
from backend.campus_pulse.runtime_contract import canonical_sha256
from backend.campus_pulse.yulan_scale_live import V7ManualFormatExhaustionRepair
import backend.campus_pulse.yulan_scale_live as live
from envs.campus_pulse_governance.forum_turns import OUTPUT_FIELDS, FORUM_AGENT_TURN_V3_SCHEMA_VERSION
from envs.campus_pulse_governance.reddit.expression_observer import observe_expression_batch


def policy_for(preflight):
    body = {
        "user_authorized": True,
        "preflight_sha256": preflight["preflight_sha256"],
        "scope": "User authorized abandoning late unknown results and bounded exact replacements; reuse successful cache",
        "unknown_policy": "abandon_late_results_then_bounded_exact_replacement",
        "max_unknown_replacements_per_prompt": 2,
        "max_unknown_attempts_total": 8,
        "max_transient_failures_per_prompt": 3,
        "max_supervisor_restarts": 6,
        "max_extra_format_repairs_per_prompt": 6,
        "format_clarification_revision": 2,
        "rate_limit_pacing": True,
        "late_results": "permanently_excluded",
        "both_original_and_replacement_may_be_billed": True,
        "request_limit": preflight["request_limit"],
        "token_limit": preflight["token_limit"],
    }
    return {**body, "authorization_fingerprint": canonical_sha256(body)}


def format_exhaustion_repairs(db_path, policy):
    """Hash-bound extra repairs for settled invalid outputs, never new facts."""
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        successful = {row[0] for row in conn.execute("SELECT prompt_sha256 FROM v7_live_cache")}
        rows = conn.execute("SELECT attempt_id,prompt_sha256,settlement_json FROM v7_live_attempts "
                            "WHERE state='settled' AND outcome='format_invalid' ORDER BY rowid").fetchall()
    grouped = defaultdict(list)
    for row in rows:
        if row[1] not in successful:
            grouped[row[1]].append(row)
    repairs = []
    # Initial request, ordinary format repair, and two standard semantic repairs
    # are already charged. Two further calls use the original repair renderer;
    # the last two clarify field names/enums, never action or opinion choices.
    for prompt_sha, failures in grouped.items():
        if len(failures) >= 4 + policy["max_extra_format_repairs_per_prompt"]:
            raise ValueError("extra settled-format repair limit reached")
        for index, (attempt, _, raw) in enumerate(failures[3:], start=3):
            settlement = json.loads(raw)
            evidence = {"parent_authorization": policy["authorization_fingerprint"],
                        "failed_attempt_id": attempt, "prompt_sha256": prompt_sha,
                        "response_sha256": settlement["response_sha256"],
                        "controlled_reason_code": "decision_contract_" + settlement["diagnostic_validation_code"],
                        "repair_generation": index, "replacement_limit": 1}
            if index >= 5:
                evidence["format_clarification_revision"] = policy["format_clarification_revision"]
            repairs.append(V7ManualFormatExhaustionRepair(
                failed_attempt_id=attempt, prompt_sha256=prompt_sha,
                response_sha256=evidence["response_sha256"],
                controlled_reason_code=evidence["controlled_reason_code"],
                authorization_fingerprint=canonical_sha256(evidence), repair_generation=index))
    return tuple(repairs)


def repair_shape_hints(output_contract):
    return {
        "required_fields": ["action", "topic", "stance", "visible_text"],
        "allowed_top_level_fields": sorted(OUTPUT_FIELDS | {"engagements", "private_actions", "public_private_origin_claim_id"}),
        "field_rule": "Use these exact field names. Do not return prompt/input sections, schema, commentary, decision wrappers or invented fields. Optional fields may be omitted as declared in output_contract.",
        "emotion_rule": {"allowed_values": output_contract.get("emotions", []), "rule": "Choose only a listed value if this optional field is included."},
        "claim_none_rule": "If you choose claim_operation=none, set claim_summary to empty text and target_claim_id and correction_target_claim_id to null.",
        "choice_rule": "Correct serialization only. Your natural action, text and opinion remain your decision, including speech or silence.",
    }


def clarified_repair_renderer(original_renderer):
    def render(rendered, **kwargs):
        repaired = original_renderer(rendered, **kwargs)
        if (kwargs.get("repair_generation", 1) < 5 or not repaired.scenario_id.startswith("cmv-")
                or not isinstance(repaired, live.RenderedForumAgentTurnV3)):
            return repaired
        payload = json.loads(repaired.agent_prompt)
        if "output_contract" not in payload or "semantic_repair_contract" not in payload:
            return repaired
        payload["semantic_repair_contract"]["serialization_clarification"] = repair_shape_hints(payload["output_contract"])
        prompt = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return replace(repaired, agent_prompt=prompt, prompt_sha256=canonical_sha256({
            "renderer": FORUM_AGENT_TURN_V3_SCHEMA_VERSION, "expected_model": repaired.expected_model,
            "system_prompt": repaired.system_prompt, "agent_prompt": prompt}))
    return render


def diagnosed_shape_normalizer(original_normalizer, write_diagnostic):
    """Observe only structural flags; never log Provider text or field values."""
    def normalize(envelope, *, rendered):
        content = envelope.content
        fenced = re.fullmatch(r"\s*```(?:json)?\s*(\{.*\})\s*```\s*", content, re.DOTALL)
        if fenced and rendered.scenario_id.startswith("cmv-"):
            envelope = replace(envelope, content=fenced.group(1))
        diagnostic = {"agent_id": rendered.agent_id, "tick": rendered.tick,
                      "fence_unwrapped": bool(fenced), "content_characters": len(content)}
        try:
            payload = json.loads(envelope.content)
            diagnostic["json_object"] = isinstance(payload, dict)
            if isinstance(payload, dict):
                allowed = OUTPUT_FIELDS | {"engagements", "private_actions", "public_private_origin_claim_id"}
                diagnostic["unknown_field_names"] = [k for k in payload if k not in allowed and re.fullmatch(r"[a-z_]{1,32}", k)]
                diagnostic["required_fields_present"] = all(k in payload for k in ("action","topic","stance","visible_text"))
                contract = json.loads(rendered.agent_prompt).get("output_contract", {})
                diagnostic["emotion_present_and_invalid"] = "emotion" in payload and payload["emotion"] not in contract.get("emotions", [])
        except (TypeError, ValueError):
            diagnostic["json_object"] = False
        normalized = original_normalizer(envelope, rendered=rendered)
        try:
            parsed = json.loads(normalized.content)
            diagnostic["normalized_field_set_exact"] = set(parsed) == OUTPUT_FIELDS | {"engagements","private_actions","public_private_origin_claim_id"}
        except (TypeError, ValueError):
            diagnostic["normalized_field_set_exact"] = False
        write_diagnostic(diagnostic)
        return normalized
    return normalize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--api-key-env", default="TIANTOKEN_API_KEY")
    args = parser.parse_args()
    base = corrected.base
    base.OUT = base.ROOT / "data/private/reddit/cmv/structure-three-chains"
    pf = base.read(base.OUT / "preflight.private.json")
    if corrected.dynamic_preflight(structure=True) != pf:
        raise ValueError("Frozen runtime or simulation changed; continuation refused")
    policy = policy_for(pf)
    if not args.run:
        print(json.dumps({"status": "validated_no_provider_calls", "request_limit": policy["request_limit"],
                          "token_limit": policy["token_limit"], "max_restarts": policy["max_supervisor_restarts"]}))
        return
    base.REQUEST_LIMIT, base.TOKEN_LIMIT, base.CONCURRENCY = pf["request_limit"], pf["token_limit"], 1
    base.MODEL, base.ENDPOINT = pf["model"], pf["endpoint"]
    base.write(base.OUT / "standing-unknown-continuation-authorization.private.json", policy)
    world, context, particles, panel, probabilities, contexts = corrected.dynamic_inputs()
    history_path = base.OUT / "continuation-supervisor.private.json"
    history = base.read(history_path)["history"] if history_path.exists() else []
    original_source_factory = base.AuthorizedV7SemanticSource
    live._semantic_repair_turn = clarified_repair_renderer(live._semantic_repair_turn)
    diagnostics = []
    def write_diagnostic(record):
        diagnostics.append(record)
        base.write(base.OUT / "response-shape-diagnostics.private.json", {"records": diagnostics, "provider_text_saved": False})
    live._normalize_forum_resident_v4_provider_shape = diagnosed_shape_normalizer(
        live._normalize_forum_resident_v4_provider_shape, write_diagnostic)
    for restart in range(policy["max_supervisor_restarts"] + 1):
        extra_repairs = format_exhaustion_repairs(base.OUT / "provider-ledger.private.sqlite3", policy)
        base.AuthorizedV7SemanticSource = partial(original_source_factory,
                                                 manual_format_exhaustion_repairs=extra_repairs)
        before = time.monotonic()
        try:
            base.run(pf, until_tick=3, api_key_env=args.api_key_env,
                     input_bundle=(world, context, particles, {str(t): panel for t in range(1, 4)}),
                     execution_conditions=pf["plan"]["conditions"], contexts_by_tick=contexts,
                     conditions_by_cell=pf["plan"]["input_conditions"], frozen_state_inputs={"static_state": particles},
                     controlled_repairs=True, standing_unknown_policy=policy,
                     expression_observer=partial(observe_expression_batch, scope_definition="whole_proposition_v2"))
        except SystemExit:
            progress = base.read(base.OUT / "progress.private.json")
            history.append({"restart": restart, "seconds": round(time.monotonic() - before, 2), "progress": progress})
            base.write(base.OUT / "continuation-supervisor.private.json", {"history": history})
            if progress.get("error_code") not in {"v7_live_unknown_outcome", "v7_live_known_provider_failure", "v7_live_format_repair_exhausted"}:
                raise
            if restart >= policy["max_supervisor_restarts"]:
                raise
            print(json.dumps({"status": "bounded_exact_resume", "restart": restart + 1,
                              "successful_cache_reused": True, "original_risk_still_charged": True}), flush=True)
            # No duplicate concurrent attempt; base.run has settled the original.
            # Exact replacement evidence and per-prompt limits are checked by
            # the existing standing-resume loader before the next dispatch.
            time.sleep(15)
        else:
            history.append({"restart": restart, "seconds": round(time.monotonic() - before, 2),
                            "progress": base.read(base.OUT / "progress.private.json")})
            base.write(base.OUT / "continuation-supervisor.private.json", {"history": history})
            return


if __name__ == "__main__":
    main()
