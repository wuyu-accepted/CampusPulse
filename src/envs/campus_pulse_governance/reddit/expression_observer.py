"""Read the expressed proposition stance before updating latent particles.

The observer sees the event question and visible text, never the resident's
profile, prior particles, self-reported stance, branch or input condition.
It changes no public text and does not force the posterior to match a label.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from typing import Mapping

from backend.campus_pulse.runtime_contract import canonical_sha256


LABELS = frozenset({"support", "oppose", "mixed", "uncertain", "insufficient_evidence"})


@dataclass(frozen=True)
class ExpressionObservationDecision:
    """The existing authorized source persists dataclass decisions, not dicts."""
    observations: list[dict]


def validate_expression_observations(envelope, expected_records):
    body = json.loads(envelope.content)
    if not isinstance(body, dict) or set(body) != {"observations"}:
        raise ValueError("expression observer requires observations only")
    rows = body["observations"]
    if not isinstance(rows, list) or len(rows) != len(expected_records):
        raise ValueError("expression observer count mismatch")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"record", "stance", "confidence", "reason"}:
            raise ValueError("expression observer fields mismatch")
        record = row["record"]
        if record not in expected_records or record in result or row["stance"] not in LABELS:
            raise ValueError("expression observer identity or label invalid")
        confidence = row["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("expression observer confidence invalid")
        if not isinstance(row["reason"], str) or len(row["reason"]) > 600:
            raise ValueError("expression observer reason invalid")
        result[record] = dict(row)
    return {"observations": [result[key] for key in sorted(result)]}


def observe_expression_batch(*, source, rendered, outputs, episode_context, scope_definition="legacy_v1"):
    if scope_definition not in {"legacy_v1", "whole_proposition_v2"}:
        raise ValueError("unknown expression scope definition")
    by_agent = {str(row["agent_id"]): row["validated_output"]["decision"] for row in outputs}
    selected = sorted(by_agent)
    aliases = {f"Record{i+1}": agent for i, agent in enumerate(selected)}
    records = [{"record": key, "text": str(by_agent[agent].get("visible_text") or "")}
               for key, agent in aliases.items()]
    payload = {"task": "interpret_expressed_stance", "event": {
        key: episode_context[key] for key in ("event_title", "decision_question") if key in episode_context},
        "records": records,
        "output": {"observations": [{"record": "Record1", "stance": "support|oppose|mixed|uncertain|insufficient_evidence",
                                    "confidence": "number 0..1", "reason": "short semantic reason"}]}}
    system = (
        "Classify each visible expression toward the event proposition. Output JSON only, one observation "
        "per exact record. Qualified support remains support even with concerns about implementation or cost. "
        "Qualified rejection remains oppose. Mixed means genuinely competing positions, not merely a polite "
        "reply or mention of tradeoffs. Uncertain means the writer explicitly cannot decide. Empty text, "
        "questions with no recoverable position, and mere acknowledgment are insufficient_evidence. "
        "Do not infer personality or reward any condition. Confidence expresses interpretive certainty. "
        "Keep each reason under 600 characters. Exact fields: record, stance, confidence, reason."
    )
    if scope_definition == "whole_proposition_v2":
        system += (
            " Evaluate every clause of the whole proposition. For a healthcare AND education question, "
            "medical-only allocation arguments cannot establish the education position: use insufficient_evidence. "
            "Agreement with a parent or support for public funding does not by itself establish the full "
            "stated coverage promise. Do not fill an unexpressed position from the surrounding discussion."
        )
    template = rendered[selected[0]]
    prompt = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    renderer = "cmv-expression-observer-v1" if scope_definition == "legacy_v1" else "cmv-expression-observer-v2"
    digest = canonical_sha256({"renderer": renderer, "expected_model": template.expected_model,
                              "system_prompt": system, "agent_prompt": prompt})
    turn = replace(template, system_prompt=system, agent_prompt=prompt, prompt_sha256=digest)
    validator = lambda envelope: ExpressionObservationDecision(
        observations=validate_expression_observations(envelope, aliases)["observations"])
    envelope = source.acquire(slot_kind="resident", prompt_sha256=digest, rendered=turn, validator=validator)
    validated = validate_expression_observations(envelope, aliases)
    observations = {aliases[row["record"]]: row for row in validated["observations"]}
    return observations, {"schema_version": "cmv-expression-observation-v1", "observer_model": template.expected_model,
        **({"scope_definition": scope_definition} if scope_definition != "legacy_v1" else {}),
        "prompt_sha256": digest, "response_sha256": envelope.response_sha256,
        "blinded_to": ["profile", "prior_state", "self_reported_stance", "condition", "branch"],
        "records": [{"agent_id": agent, "self_reported_stance": by_agent[agent]["stance"],
                     "expression_observation": observations[agent]} for agent in selected]}
