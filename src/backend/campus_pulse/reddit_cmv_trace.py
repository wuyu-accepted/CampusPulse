"""Exact, prompt-bound replay for reviewed CMV semantic decisions.

The release stores validated structured decisions rather than raw Provider
request/response bodies.  Every entry is bound to the exact rendered prompt
fingerprint and model, and replay never opens a network connection.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping

from envs.campus_pulse_governance.yulan_scale import TurnEnvelope
from .runtime_contract import canonical_sha256


TRACE_SCHEMA = "reddit-cmv-reviewed-trace-release-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SLOT_KINDS = {"resident", "governance_actor"}
_ENTRY_FIELDS = {
    "slot_kind",
    "prompt_sha256",
    "response_sha256",
    "response_content",
    "returned_model",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
}


class CmvTraceError(ValueError):
    """A reviewed CMV trace is malformed, mismatched or incomplete."""


def _canonical_content(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CmvTraceError("trace response content is not strict JSON") from exc


def build_reviewed_cmv_trace_release(
    *,
    model: str,
    plan_sha256: str,
    world_sha256: str,
    entries: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Create a content-addressed release from reviewed structured entries."""
    if not model.strip() or _SHA256.fullmatch(plan_sha256) is None or _SHA256.fullmatch(world_sha256) is None:
        raise CmvTraceError("trace release model or run binding is invalid")
    if not entries:
        raise CmvTraceError("trace release must contain at least one reviewed entry")
    normalized_entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for source in entries:
        if not isinstance(source, Mapping) or set(source) != _ENTRY_FIELDS:
            raise CmvTraceError("trace entry fields do not match the release contract")
        slot_kind = str(source["slot_kind"])
        prompt_sha = str(source["prompt_sha256"])
        response_sha = str(source["response_sha256"])
        returned_model = str(source["returned_model"])
        content = source["response_content"]
        if (
            slot_kind not in _SLOT_KINDS
            or _SHA256.fullmatch(prompt_sha) is None
            or _SHA256.fullmatch(response_sha) is None
            or not returned_model.strip()
            or returned_model != model
            or not isinstance(content, Mapping)
            or (slot_kind, prompt_sha) in seen
        ):
            raise CmvTraceError("trace entry identity is invalid or duplicated")
        serialized = _canonical_content(content)
        if hashlib.sha256(serialized.encode("utf-8")).hexdigest() != response_sha:
            raise CmvTraceError("trace response content hash does not match")
        usage: dict[str, int] = {}
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = source[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CmvTraceError("trace token usage is invalid")
            usage[field] = value
        if usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]:
            raise CmvTraceError("trace token usage does not reconcile")
        seen.add((slot_kind, prompt_sha))
        normalized_entries.append({
            "slot_kind": slot_kind,
            "prompt_sha256": prompt_sha,
            "response_sha256": response_sha,
            "response_content": dict(content),
            "returned_model": returned_model,
            **usage,
        })
    normalized_entries.sort(key=lambda row: (row["slot_kind"], row["prompt_sha256"]))
    body = {
        "schema_version": TRACE_SCHEMA,
        "model": model,
        "plan_sha256": plan_sha256,
        "world_sha256": world_sha256,
        "entries": normalized_entries,
    }
    return {**body, "release_sha256": canonical_sha256(body)}


def validate_reviewed_cmv_trace_release(
    release: Mapping[str, Any],
    *,
    expected_model: str,
    expected_plan_sha256: str,
    expected_world_sha256: str,
) -> dict[str, Any]:
    if not isinstance(release, Mapping) or set(release) != {
        "schema_version", "model", "plan_sha256", "world_sha256", "entries", "release_sha256"
    }:
        raise CmvTraceError("trace release envelope is malformed")
    body = {key: value for key, value in release.items() if key != "release_sha256"}
    if (
        body.get("schema_version") != TRACE_SCHEMA
        or body.get("model") != expected_model
        or body.get("plan_sha256") != expected_plan_sha256
        or body.get("world_sha256") != expected_world_sha256
        or canonical_sha256(body) != release.get("release_sha256")
        or not isinstance(body.get("entries"), list)
    ):
        raise CmvTraceError("trace release hash or run binding does not match")
    rebuilt = build_reviewed_cmv_trace_release(
        model=expected_model,
        plan_sha256=expected_plan_sha256,
        world_sha256=expected_world_sha256,
        entries=body["entries"],
    )
    if rebuilt != dict(release):
        raise CmvTraceError("trace release entries are not canonical")
    return dict(release)


@dataclass
class CmvExactTraceSource:
    """Resolve a response only when the exact prompt and model are present."""

    release: Mapping[str, Any]
    expected_model: str
    expected_plan_sha256: str
    expected_world_sha256: str

    provenance = "reviewed_trace_replay"

    def __post_init__(self) -> None:
        validated = validate_reviewed_cmv_trace_release(
            self.release,
            expected_model=self.expected_model,
            expected_plan_sha256=self.expected_plan_sha256,
            expected_world_sha256=self.expected_world_sha256,
        )
        self.trace_release_sha256 = str(validated["release_sha256"])
        self.network_requests = 0
        self.provider_calls = 0
        self._entries = {
            (str(row["slot_kind"]), str(row["prompt_sha256"])): row
            for row in validated["entries"]
        }

    def acquire(
        self,
        *,
        slot_kind: str,
        prompt_sha256: str,
        rendered: Any | None = None,
        validator: Any | None = None,
    ) -> TurnEnvelope:
        if slot_kind not in _SLOT_KINDS or _SHA256.fullmatch(prompt_sha256) is None:
            raise CmvTraceError("trace lookup identity is invalid")
        if rendered is not None:
            if getattr(rendered, "prompt_sha256", None) != prompt_sha256:
                raise CmvTraceError("rendered prompt does not match trace lookup")
            if getattr(rendered, "expected_model", self.expected_model) != self.expected_model:
                raise CmvTraceError("rendered model does not match trace release")
        row = self._entries.get((slot_kind, prompt_sha256))
        if row is None:
            raise CmvTraceError("exact reviewed CMV trace entry is missing")
        content = _canonical_content(row["response_content"])
        envelope = TurnEnvelope(
            slot_kind=slot_kind,
            prompt_sha256=prompt_sha256,
            response_sha256=str(row["response_sha256"]),
            provenance=self.provenance,
            content=content,
            returned_model=str(row["returned_model"]),
            prompt_tokens=int(row["prompt_tokens"]),
            completion_tokens=int(row["completion_tokens"]),
            total_tokens=int(row["total_tokens"]),
            provider_calls=0,
            cache_hit=True,
            provider_attempts=0,
            provider_billed_tokens=0,
        )
        if validator is not None:
            validator(envelope)
        return envelope


__all__ = [
    "TRACE_SCHEMA",
    "CmvExactTraceSource",
    "CmvTraceError",
    "build_reviewed_cmv_trace_release",
    "validate_reviewed_cmv_trace_release",
]
