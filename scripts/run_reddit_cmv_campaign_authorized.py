"""Authorized, resumable Reddit CMV live campaign and corrected pilot.

This runner uses the existing ForumTwin resident/governance contracts, the v7
authorized transport ledger, and the CMV tick-checkpoint repository. It never
logs prompts, raw Provider bodies, credentials, or source account identifiers.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import ssl
import sys
import tempfile
import threading
import time
import traceback
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from backend.campus_pulse.reddit_cmv_execution import (  # noqa: E402
    CmvBranchState,
    CmvRunRepository,
    execute_cmv_small_run,
    execute_cmv_resident_tick,
    validate_cmv_execution_plan,
)
from backend.campus_pulse.yulan_scale_live import (  # noqa: E402
    AuthorizedV7SemanticSource,
    ForumPilotLiveFactoryContext,
    V7ControlledRepairPolicy,
    V7LiveAuthorizationBinding,
    V7LiveError,
    V7LiveUnknownOutcome,
    V7ManualFormatExhaustionRepair,
    V7ManualKnownFailureReplacement,
    require_formal_authorized_source,
)
from backend.campus_pulse.yulan_scale_live_runtime import (  # noqa: E402
    SQLiteV7AuthorizedTurnRepository,
    VectorEngineV7Transport,
    vectorengine_transport_fingerprint,
)
from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402
from envs.campus_pulse_governance.forum_twin import AgentMemory, ForumLedger  # noqa: E402
from envs.campus_pulse_governance.yulan_scale import TurnEnvelope  # noqa: E402
from envs.campus_pulse_governance.reddit.episodes import (  # noqa: E402
    build_episode_scenario_context,
)
from envs.campus_pulse_governance.reddit.evaluation import (  # noqa: E402
    evaluate_cmv_paired_run,
)
from envs.campus_pulse_governance.reddit.lightweight import (  # noqa: E402
    validate_lightweight_release,
)
from envs.campus_pulse_governance.reddit.particle_model import (  # noqa: E402
    initialize_episode_particles,
    load_particle_snapshot,
    predict_tick,
)
from envs.campus_pulse_governance.reddit.personas import (  # noqa: E402
    CAMPAIGN_BUDGET_SCHEMA,
    CAMPAIGN_MAX_REQUESTS,
    CAMPAIGN_MAX_TOKENS,
    _campaign_budget,
)
from envs.campus_pulse_governance.reddit.run_plan import (  # noqa: E402
    cmv_runtime_fingerprint,
    build_cmv_small_run_plan,
    persist_cmv_small_run_plan,
)
from envs.campus_pulse_governance.reddit.runtime import (  # noqa: E402
    initialize_cmv_event_ledger,
    build_cmv_feed_snapshot,
    render_cmv_population_turns,
)
from envs.campus_pulse_governance.reddit.world import (  # noqa: E402
    verify_fixed_world_release,
)


CAMPAIGN_ID = "cmv-fixed-world-512-gpt6-luna-r2"
CAMPAIGN_DIR = Path("data/private/reddit/cmv/campaigns") / CAMPAIGN_ID
PRIOR_REPLAY_LEDGERS: tuple[Path, ...] = ()
FINAL_TICK = 3
SHARED_PANEL_SIZE = 24
PUBLIC_ONLY = False
AUDIT_AFTER_SHARED_TICK = False
BUDGET_PATH = Path("data/private/reddit/cmv/budget/cmv-implementation/provider-budget.private.json")
RELEASE_DIR = Path("data/private/reddit/cmv/lightweight-v3")
EVENT_OVERLAY_PATH: Path | None = None
COMPILED_DIR = RELEASE_DIR / "world-runtime-r2"
PROXY = "http://127.0.0.1:7897"
MODEL = "gpt-6-luna"
ENDPOINT = "https://api.openlux.ai/v1/chat/completions"
SEEDS_AND_PANELS = ((20260924, 14), (20260925, 14), (20260926, 14),
                    (20260927, 14), (20260928, 14), (20260929, 13))
PROJECT_ID = "reddit-cmv-512"
STAGE_ID = "reddit_cmv_512"
AUTHORIZATION_STATEMENT = (
    "The user authorized the six-run 512-primary-slot Reddit CMV development campaign, "
    "using gpt-6-luna through OpenLux with reasoning none, a shared 2,000,000-token "
    "all-stage ceiling and a 1,056-request all-stage ceiling. Existing usage counts "
    "against both ceilings. Unknown Provider outcomes must pause without retry."
)
_SENSITIVE = re.compile(
    r"(?:\b(?:t1|t3)_[a-z0-9]{5,}\b|\bsk-[A-Za-z0-9_-]{12,}\b|"
    r"[A-Za-z]:\\|/(?:Users|home|etc)/)",
    re.I,
)


class _ExactResidentReplaySource:
    """Replay validated resident turns only when their prompt hash is exact."""

    def __init__(self, source: Any, ledger: Path, *, expected_model: str) -> None:
        self._source = source
        self.provenance = source.provenance
        self.formal_evidence = source.formal_evidence
        self._replay_count = 0
        path = ledger.resolve()
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=ro&immutable=1",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        try:
            control = connection.execute(
                "SELECT provider_calls_reserved, provider_calls_unknown, tokens_in_flight "
                "FROM v7_live_control WHERE singleton=1"
            ).fetchone()
            if control is None or any(
                int(control[field])
                for field in (
                    "provider_calls_reserved",
                    "provider_calls_unknown",
                    "tokens_in_flight",
                )
            ):
                raise CampaignError("prior_replay_ledger_not_safely_settled")
            rows = connection.execute(
                "SELECT prompt_sha256,response_sha256,returned_model,prompt_tokens,"
                "completion_tokens,total_tokens,content_json FROM v7_live_cache "
                "WHERE slot_kind='resident' ORDER BY prompt_sha256"
            ).fetchall()
        finally:
            connection.close()
        self._entries: dict[str, TurnEnvelope] = {}
        for row in rows:
            prompt_sha = str(row["prompt_sha256"])
            if prompt_sha in self._entries or row["returned_model"] != expected_model:
                raise CampaignError("prior_replay_cache_ambiguous")
            self._entries[prompt_sha] = TurnEnvelope(
                slot_kind="resident",
                prompt_sha256=prompt_sha,
                response_sha256=str(row["response_sha256"]),
                provenance="authorized_live_llm",
                content=str(row["content_json"]),
                returned_model=str(row["returned_model"]),
                prompt_tokens=int(row["prompt_tokens"]),
                completion_tokens=int(row["completion_tokens"]),
                total_tokens=int(row["total_tokens"]),
                provider_calls=0,
                cache_hit=True,
            )

    @property
    def usage_snapshot(self) -> dict[str, Any]:
        body = dict(self._source.usage_snapshot)
        body.pop("usage_sha256", None)
        body["cache_hits"] = int(body.get("cache_hits", 0)) + self._replay_count
        body["semantic_turns"] = int(body.get("semantic_turns", 0)) + self._replay_count
        return {**body, "usage_sha256": canonical_sha256(body)}

    def acquire(self, **kwargs: Any) -> Any:
        if kwargs.get("slot_kind") == "resident":
            prompt_sha = str(kwargs.get("prompt_sha256") or "")
            envelope = self._entries.get(prompt_sha)
            if envelope is not None:
                validator = kwargs.get("validator")
                if validator is None:
                    raise CampaignError("prior_replay_validator_missing")
                validator(envelope)
                self._replay_count += 1
                return envelope
        return self._source.acquire(**kwargs)


class _SharedTickAuditSource:
    """Pause before the first branch slot, after the planned shared turns commit."""

    def __init__(self, source: Any, shared_panel_size: int) -> None:
        self._source = source
        self._shared_panel_size = shared_panel_size
        self._resident_slots = 0
        self._lock = threading.Lock()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._source, name)

    def acquire(self, **kwargs: Any) -> Any:
        if kwargs.get("slot_kind") == "resident":
            with self._lock:
                if self._resident_slots >= self._shared_panel_size:
                    raise CampaignError("shared_tick_audit_pause")
                self._resident_slots += 1
        return self._source.acquire(**kwargs)


KNOWN_FAILURE_REPLACEMENT_SCHEMA = (
    "campus-pulse-forum-hero-known-failure-replacement-v1"
)
FORMAT_EXHAUSTION_REPAIR_SCHEMA = (
    "campus-pulse-forum-hero-format-exhaustion-repair-v1"
)


def _controlled_semantic_repair_policy(
    run_dir: Path,
    *,
    binding: V7LiveAuthorizationBinding,
) -> V7ControlledRepairPolicy:
    body = {
        "schema_version": "reddit-cmv-controlled-semantic-repair-v1",
        "run_id": binding.run_id,
        "preflight_sha256": binding.preflight_sha256,
        "parent_authorization_fingerprint": binding.authorization_fingerprint,
        "max_semantic_repairs_per_slot": 2,
        "automatic_network_retry_limit": 0,
        "rejected_provider_response_excluded": True,
    }
    authorization_fingerprint = canonical_sha256(body)
    record = {**body, "authorization_fingerprint": authorization_fingerprint}
    path = run_dir / "controlled-semantic-repair-policy.private.json"
    if path.exists():
        if _read_json(path, "semantic_repair_policy_unreadable") != record:
            raise CampaignError("semantic_repair_policy_drift")
    else:
        _write_json(path, record)
    return V7ControlledRepairPolicy(
        run_id=binding.run_id,
        preflight_sha256=binding.preflight_sha256,
        authorization_fingerprint=authorization_fingerprint,
        prior_authorization_fingerprint=binding.authorization_fingerprint,
        max_semantic_repairs_per_slot=2,
    )


def _load_format_exhaustion_repairs(
    run_dir: Path,
    *,
    binding: V7LiveAuthorizationBinding,
) -> tuple[V7ManualFormatExhaustionRepair, ...]:
    repairs = []
    for path in sorted(run_dir.glob("format-exhaustion-repair-*.private.json")):
        evidence = _read_json(path, "format_repair_evidence_unreadable")
        evidence_sha = str(evidence.pop("evidence_sha256", ""))
        if (
            evidence_sha != canonical_sha256(evidence)
            or evidence.get("schema_version") != FORMAT_EXHAUSTION_REPAIR_SCHEMA
            or evidence.get("run_id") != binding.run_id
            or evidence.get("preflight_sha256") != binding.preflight_sha256
            or evidence.get("parent_authorization_fingerprint")
            != binding.authorization_fingerprint
            or evidence.get("automatic_network_retry_limit") != 0
            or evidence.get("rejected_provider_response_excluded") is not True
        ):
            raise CampaignError("format_repair_evidence_invalid")
        repairs.append(
            V7ManualFormatExhaustionRepair(
                failed_attempt_id=str(evidence["failed_attempt_id"]),
                prompt_sha256=str(evidence["prompt_sha256"]),
                response_sha256=str(evidence["response_sha256"]),
                controlled_reason_code=str(evidence["controlled_reason_code"]),
                authorization_fingerprint=str(
                    evidence["repair_authorization_fingerprint"]
                ),
                repair_generation=int(evidence["repair_generation"]),
            )
        )
    return tuple(repairs)


def _load_known_failure_replacements(
    run_dir: Path,
    *,
    binding: V7LiveAuthorizationBinding,
) -> tuple[V7ManualKnownFailureReplacement, ...]:
    replacements = []
    for path in sorted(run_dir.glob("known-failure-replacement-*.private.json")):
        evidence = _read_json(path, "known_failure_evidence_unreadable")
        evidence_sha = str(evidence.pop("evidence_sha256", ""))
        replacement_fingerprint = str(
            evidence.get("replacement_authorization_fingerprint") or ""
        )
        authorization_material = {
            key: value
            for key, value in evidence.items()
            if key != "replacement_authorization_fingerprint"
        }
        if (
            evidence_sha != canonical_sha256(evidence)
            or evidence.get("schema_version") != KNOWN_FAILURE_REPLACEMENT_SCHEMA
            or evidence.get("run_id") != binding.run_id
            or evidence.get("preflight_sha256") != binding.preflight_sha256
            or evidence.get("parent_authorization_fingerprint")
            != binding.authorization_fingerprint
            or evidence.get("original_outcome") != "known_failure"
            or evidence.get("resolution") != "replace_known_failure"
            or evidence.get("replacement_limit") != 1
            or evidence.get("automatic_network_retry_limit") != 0
            or evidence.get("original_response_integration_forbidden") is not True
            or replacement_fingerprint != canonical_sha256(authorization_material)
        ):
            raise CampaignError("known_failure_evidence_invalid")
        replacements.append(
            V7ManualKnownFailureReplacement(
                failed_attempt_id=str(evidence["failed_attempt_id"]),
                prompt_sha256=str(evidence["prompt_sha256"]),
                request_sha256=str(evidence["request_sha256"]),
                authorization_fingerprint=replacement_fingerprint,
            )
        )
    return tuple(replacements)


def enforce_cmv_quality_guard(summary: Mapping[str, Any], policy: Mapping[str, Any]) -> None:
    primary = int(summary.get("primary_requests_settled", 0))
    successful = int(summary.get("primary_requests_successful", 0))
    resolved = int(summary.get("cache_entries", successful))
    outcomes = summary.get("outcomes", {})
    # Definite HTTP failures have no model decision to evaluate. They are
    # accounted as requests and may be replaced once, but must not depress the
    # resident JSON-contract rate.
    evaluable_primary = max(0, primary - int(outcomes.get("known_failure", 0)))
    invalid = sum(int(outcomes.get(key, 0)) for key in ("format_invalid", "response_invalid"))
    completed = int(summary.get("provider_calls_completed", 0))
    minimum = int(policy["minimum_primary_observations"])
    first_pass_rate = float(policy["minimum_first_pass_rate"])
    # A model that resolves fewer than half of its first eight resident turns
    # is unsuitable for this strict contract. Stop before repairs multiply the
    # cost of a systematically failing provider/model combination.
    if evaluable_primary >= max(8, minimum) and resolved / evaluable_primary < 0.5:
        raise CampaignError("quality_gate_severe_contract_failure")
    # At 90%, fewer than ten observations cannot distinguish "one repair was
    # needed" from a sustained contract-compliance problem.  Wait until the
    # requested rate admits at least one discrete miss before enforcing it.
    rate_denominator = max(1, round(1.0 / max(1e-9, 1.0 - first_pass_rate)))
    first_pass_minimum = max(minimum, rate_denominator, 20)
    if (
        evaluable_primary >= first_pass_minimum
        and successful / evaluable_primary < first_pass_rate
        and resolved / evaluable_primary < first_pass_rate
    ):
        raise CampaignError("quality_gate_first_pass_rate_stop_before_next_request")
    invalid_rate_minimum = max(minimum, 20)
    unresolved = max(0, evaluable_primary - resolved)
    if (
        evaluable_primary >= invalid_rate_minimum
        and unresolved / evaluable_primary
        > float(policy["maximum_invalid_share"])
    ):
        raise CampaignError("quality_gate_invalid_request_share_stop_before_next_request")


class CampaignError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _private(value: str | Path) -> Path:
    path = Path(value)
    resolved = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    root = (ROOT / "data" / "private" / "reddit" / "cmv").resolve()
    if resolved != root and root not in resolved.parents:
        raise CampaignError("private_path_contract")
    return resolved


def _read_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CampaignError(code) from exc
    if not isinstance(value, dict):
        raise CampaignError(code)
    return value


def _read_json_value(path: Path, code: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CampaignError(code) from exc


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                          allow_nan=False) + "\n").encode("utf-8")
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _load_config(path: Path) -> dict[str, Any]:
    value = _read_json(path, "config_unreadable")
    if value.get("community") != "changemyview":
        raise CampaignError("wrong_corpus_contract")
    if MODEL == "gpt-5.4-mini":
        # This is an isolated model comparison. Keep the pinned world/data
        # configuration and older gpt-6 campaign artifacts byte-for-byte intact.
        provider = dict(value.get("provider") or {})
        if provider.get("model") != "gpt-6-luna":
            raise CampaignError("model_comparison_base_config_drift")
        value = {**value, "provider": {**provider, "model": MODEL}}
    return value


def _episode(observations: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [row for row in observations if row.get("period") == "development"]
    if len(matches) != 1:
        raise CampaignError("development_episode_not_unique")
    if EVENT_OVERLAY_PATH is not None:
        overlay = _read_json(_private(EVENT_OVERLAY_PATH), "event_overlay_unreadable")
        body = {key: value for key, value in overlay.items() if key != "overlay_sha256"}
        release = _read_json(_private(RELEASE_DIR) / "lightweight-release.private.json", "lightweight_release_unreadable")
        episode = overlay.get("episode")
        if (
            overlay.get("schema_version") != "reddit-cmv-development-event-overlay-v1"
            or overlay.get("overlay_sha256") != canonical_sha256(body)
            or overlay.get("source_lightweight_release_sha256") != release.get("release_sha256")
            or overlay.get("source_archive_sha256") != release.get("source_archive_sha256")
            or not isinstance(episode, dict)
            or overlay.get("episode_sha256") != canonical_sha256(episode)
            or episode.get("period") != "development"
            or episode.get("topic_family") != matches[0].get("topic_family")
            or episode.get("episode_id") == matches[0].get("episode_id")
            or len(episode.get("historical_seed_messages") or []) != 4
        ):
            raise CampaignError("event_overlay_contract")
        return episode
    return matches[0]


def _plan_set(
    world: Mapping[str, Any], episode_id: str, runs_root: Path, *, final_tick: int = 3,
    shared_panel_size: int = 24,
    public_only: bool = False,
) -> list[dict[str, Any]]:
    results = []
    for seed, panel_size in SEEDS_AND_PANELS:
        plan = build_cmv_small_run_plan(
            world=world, episode_id=episode_id, seed=seed, panel_size=panel_size,
            final_tick=final_tick, expected_model=MODEL,
            shared_panel_size=shared_panel_size,
            public_only=public_only,
        )
        persisted = persist_cmv_small_run_plan(plan, runs_root)
        plan_path = Path(persisted["plan_path"])
        checked = validate_cmv_execution_plan(_read_json(plan_path, "plan_unreadable"))
        if checked["plan_sha256"] != plan["plan_sha256"]:
            raise CampaignError("persisted_plan_drift")
        results.append({
            "seed": seed,
            "panel_size": panel_size,
            "run_id": persisted["run_id"],
            "plan_sha256": plan["plan_sha256"],
            "run_dir": str(plan_path.parent.relative_to(ROOT)),
            "plan_path": str(plan_path.relative_to(ROOT)),
            "slot_counts": dict(plan["slot_counts"]),
            "request_ceiling": int(plan["provider_policy"]["maximum_http_requests"]),
        })
    expected_slots = sum(shared_panel_size + 6 + 2 * (final_tick - 1) * panel_size for _, panel_size in SEEDS_AND_PANELS)
    if sum(int(row["slot_counts"]["primary_total"]) for row in results) != expected_slots:
        raise CampaignError("campaign_slot_reconciliation")
    if sum(int(row["request_ceiling"]) for row in results) != 2 * expected_slots:
        raise CampaignError("campaign_request_reconciliation")
    return results


def _render_preflight_sample(
    *, world: Mapping[str, Any], episode_row: Mapping[str, Any],
    plans: list[dict[str, Any]], release_dir: Path,
) -> dict[str, Any]:
    particle_path = _private(COMPILED_DIR) / "initial-particles.private.npz"
    meta = _read_json(_private(COMPILED_DIR) / "fixed-world.private.json", "world_unreadable")
    observations = _read_json_value(release_dir / "episode-observations.private.json", "episodes_unreadable")
    episode_context = build_episode_scenario_context(episode_row)
    sizes: list[int] = []
    resident_prompt_count = 0
    for item in plans:
        seed = int(item["seed"])
        neutral = load_particle_snapshot(
            particle_path, expected_sha256=str(meta.get("particle_asset_sha256") or "")
        )
        particles = initialize_episode_particles(
            neutral, {}, event_id=str(episode_row["episode_id"]),
            world_seed=int(world.get("world_seed", 20260924)),
        )
        seed_ledger = initialize_cmv_event_ledger(episode_context)
        feed = build_cmv_feed_snapshot(
            world=world, ledger=seed_ledger, scenario_id=str(episode_row["episode_id"]),
            branch="shared_baseline", tick=1, relationship_release=world["relationships"],
        )
        exposures = {
            agent_id: min(1.0, sum(row.rank_score for row in feed.exposures_by_agent[agent_id][:6]) / 2.0)
            for agent_id in particles.agent_ids
        }
        prior = predict_tick(particles, tick=1, exposures=exposures,
                             contested_claim_signal={}, correction_signal={},
                             service_resolution={}, seed=seed)
        rendered = render_cmv_population_turns(
            world=world, particles=prior, episode_context=episode_context,
            ledger=seed_ledger, scenario_id=str(episode_row["episode_id"]),
            branch="shared_baseline", tick=1,
            expected_model=MODEL,
            activated_agent_ids=tuple(sorted(str(row["agent_id"]) for row in world["agents"])),
            exposures_by_agent=feed.exposures_by_agent,
            memories_by_agent={agent_id: AgentMemory(agent_id=agent_id) for agent_id in particles.agent_ids},
            public_board=feed.public_board,
            private_actions_enabled=not PUBLIC_ONLY,
        )
        if len(rendered) != 24:
            raise CampaignError("preflight_prompt_render_count")
        for turn in rendered.values():
            text = turn.system_prompt + "\n" + turn.agent_prompt
            if _SENSITIVE.search(text):
                raise CampaignError("preflight_privacy_scan")
            sizes.append(len(text.encode("utf-8")))
        resident_prompt_count += len(rendered)
    if len(observations) != 3:
        raise CampaignError("episode_release_count")
    return {
        "development_episode_id": str(episode_row["episode_id"]),
        "held_out_episode_count": sum(row.get("period") == "held_out" for row in observations),
        "first_tick_rendered_resident_turns": resident_prompt_count,
        "first_tick_prompt_utf8_bytes_min": min(sizes),
        "first_tick_prompt_utf8_bytes_max": max(sizes),
        "prompt_text_persisted_in_preflight": False,
        "privacy_scan": "passed",
    }


def _direct_budget(path: Path) -> dict[str, Any]:
    value = _read_json(path, "shared_budget_unreadable")
    if (value.get("schema_version") != CAMPAIGN_BUDGET_SCHEMA
            or value.get("campaign_id") != "cmv-implementation"
            or value.get("max_total_tokens") != CAMPAIGN_MAX_TOKENS
            or value.get("max_http_generation_requests") != CAMPAIGN_MAX_REQUESTS
            or not isinstance(value.get("attempts"), list)
            or not isinstance(value.get("stages"), dict)):
        raise CampaignError("shared_budget_contract")
    return value


def _new_preflight(
    *, config: Mapping[str, Any], world: Mapping[str, Any],
    episode_row: Mapping[str, Any], plans: list[dict[str, Any]],
    prompt_sample: Mapping[str, Any], budget: Mapping[str, Any],
    replay_proof: Mapping[str, Any] | None,
) -> dict[str, Any]:
    total_slots = sum(int(row["slot_counts"]["primary_total"]) for row in plans)
    replay_count = int((replay_proof or {}).get("validated_resident_slots", 0))
    if replay_count < 0 or replay_count > total_slots:
        raise CampaignError("exact_replay_count_invalid")
    calls = 2 * (total_slots - replay_count)
    used_tokens = int(budget.get("tokens_committed", 0))
    reserved_tokens = int(budget.get("tokens_reserved", 0))
    used_calls = int(budget.get("requests_dispatched", 0))
    token_room = CAMPAIGN_MAX_TOKENS - used_tokens - reserved_tokens
    request_room = CAMPAIGN_MAX_REQUESTS - used_calls
    provider = dict(config.get("provider") or {})
    if (provider.get("endpoint") != ENDPOINT or provider.get("model") != MODEL
            or provider.get("reasoning") != "none"):
        raise CampaignError("provider_config_mismatch")
    if calls > request_room or token_room <= 0:
        raise CampaignError("campaign_budget_insufficient")
    body = {
        "schema_version": "reddit-cmv-live-campaign-preflight-v1",
        "campaign_id": CAMPAIGN_ID,
        "created_at_utc": _now(),
        "model": MODEL,
        "endpoint": ENDPOINT,
        "reasoning_effort": "none",
        "proxy": PROXY,
        "safe_preconnect_retry_limit": 2,
        "post_transmission_retry_limit": 0,
        "max_concurrency": 1,
        "total_primary_slots": total_slots,
        "request_upper_bound": calls,
        "exact_replay_proof": dict(replay_proof or {}),
        "all_stage_token_limit": CAMPAIGN_MAX_TOKENS,
        "all_stage_request_limit": CAMPAIGN_MAX_REQUESTS,
        "preexisting_tokens_committed": used_tokens,
        "preexisting_tokens_reserved": reserved_tokens,
        "preexisting_requests_dispatched": used_calls,
        "remaining_token_room_at_preflight": token_room,
        "remaining_request_room_at_preflight": request_room,
        "runs": plans,
        "fixed_world_sha256": str(world.get("world_sha256") or ""),
        "episode_and_prompt_checks": dict(prompt_sample),
        "token_accounting": {
            "successful_response": "charge Provider-reported input plus output usage",
            "unknown_or_invalid_response": "charge the existing v7 conservative reservation",
            "before_each_send": "atomic token/request reservation; stop if the remaining cap cannot reserve this turn",
            "model_tokenizer_endpoint": "not used; no unsupported token-count route is assumed",
        },
        "fields_in_scope": [
            "synthetic persona and selected pre-event history",
            "qualitative particle state and scenario stake",
            "time-visible synthetic forum content and eligible private context",
            "role-limited aggregate governance observation",
            "strict resident and governance JSON contracts",
        ],
        "fields_excluded": [
            "source account names and record IDs",
            "held-out continuation and evaluator-only labels",
            "hidden population truth",
            "filesystem paths and credentials",
        ],
        "provider_calls_started": False,
    }
    return {**body, "preflight_sha256": canonical_sha256(body)}


def _prove_exact_resident_replay(
    *, world: Mapping[str, Any], episode_row: Mapping[str, Any],
    plans: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reexecute the shared and Natural slots offline from prior exact turns."""
    if len(plans) != 1 or len(PRIOR_REPLAY_LEDGERS) != 1:
        raise CampaignError("exact_replay_scope_invalid")
    ledger_path = _private(PRIOR_REPLAY_LEDGERS[0])
    if not ledger_path.is_file():
        raise CampaignError("exact_replay_ledger_missing")

    class ReplayOnlySource:
        provenance = "authorized_live_llm"
        formal_evidence = False
        usage_snapshot = {"cache_hits": 0, "semantic_turns": 0}

        def acquire(self, **_kwargs: Any) -> Any:
            raise CampaignError("exact_replay_slot_missing")

    source = _ExactResidentReplaySource(
        ReplayOnlySource(), ledger_path, expected_model=MODEL
    )
    run = plans[0]
    plan = validate_cmv_execution_plan(
        _read_json(_private(str(run["plan_path"])), "plan_unreadable")
    )
    if plan["execution_runtime_sha256"] != cmv_runtime_fingerprint():
        raise CampaignError("exact_replay_runtime_changed")
    run_id = "cmv-run-" + str(plan["plan_sha256"])[:20]
    context = build_episode_scenario_context(episode_row)
    with tempfile.TemporaryDirectory(prefix="cmv-replay-proof-") as directory:
        repository = CmvRunRepository(
            directory, run_id=run_id,
            plan_sha256=str(plan["plan_sha256"]),
            world_sha256=str(world["world_sha256"]),
        )
        shared = _initial_state(
            plan=plan, world=world, episode_row=episode_row,
            release_dir=_private(RELEASE_DIR),
        )
        shared.ledger = initialize_cmv_event_ledger(context)
        repository.commit_checkpoint(shared)
        for branch, ticks in (("shared_baseline", (1,)), ("natural", (2, 3))):
            state = shared if branch == "shared_baseline" else shared.fork("natural")
            if branch == "natural":
                repository.commit_checkpoint(state)
            for tick in ticks:
                slots = [
                    row for row in plan["resident_slots"]
                    if row["branch"] == branch and row["tick"] == tick
                ]
                execute_cmv_resident_tick(
                    world=world, state=state, episode_context=context,
                    relationship_release=world["relationships"],
                    activation_slots=slots, source=source,
                    expected_model=MODEL, run_id=run_id,
                    seed=int(plan["seed"]),
                    plan_sha256=str(plan["plan_sha256"]),
                    world_sha256=str(world["world_sha256"]),
                    repository=repository, max_workers=1,
                )
    count = len([row for row in plan["resident_slots"] if row["branch"] in {"shared_baseline", "natural"}])
    if source._replay_count != count or count != 48:
        raise CampaignError("exact_replay_count_mismatch")
    return {
        "validated_resident_slots": count,
        "source_ledger_sha256": hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
        "execution_runtime_sha256": cmv_runtime_fingerprint(),
        "plan_sha256": str(plan["plan_sha256"]),
        "network_requests_for_proof": 0,
    }


def _prepare_campaign(config_path: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], Path]:
    config = _load_config(config_path)
    release_dir = _private(RELEASE_DIR)
    light = validate_lightweight_release(release_dir)
    if light.get("verified") is not True:
        raise CampaignError("lightweight_release_invalid")
    world_path = _private(COMPILED_DIR) / "fixed-world.private.json"
    particle_path = _private(COMPILED_DIR) / "initial-particles.private.npz"
    verify_fixed_world_release(world_path, particle_path)
    world = _read_json(world_path, "world_unreadable")
    observations = _read_json_value(release_dir / "episode-observations.private.json", "episodes_unreadable")
    episode_row = _episode(observations)
    campaign_dir = _private(CAMPAIGN_DIR)
    plans = _plan_set(
        world, str(episode_row["episode_id"]), _private("data/private/reddit/cmv/runs"),
        final_tick=FINAL_TICK, shared_panel_size=SHARED_PANEL_SIZE,
        public_only=PUBLIC_ONLY,
    )
    sample = _render_preflight_sample(
        world=world, episode_row=episode_row, plans=plans, release_dir=release_dir
    )
    replay_proof = _prove_exact_resident_replay(
        world=world, episode_row=episode_row, plans=plans
    ) if PRIOR_REPLAY_LEDGERS else None
    preflight_path = campaign_dir / "campaign-preflight.private.json"
    if preflight_path.exists():
        preflight = _read_json(preflight_path, "campaign_preflight_unreadable")
        if (preflight.get("campaign_id") != CAMPAIGN_ID
                or preflight.get("fixed_world_sha256") != world.get("world_sha256")
                or preflight.get("runs") != plans
                or preflight.get("exact_replay_proof") != dict(replay_proof or {})):
            raise CampaignError("campaign_preflight_drift")
    else:
        budget = _direct_budget(_private(BUDGET_PATH))
        preflight = _new_preflight(
            config=config, world=world, episode_row=episode_row, plans=plans,
            prompt_sample=sample, budget=budget, replay_proof=replay_proof,
        )
        campaign_dir.mkdir(parents=True, exist_ok=True)
        _write_json(preflight_path, preflight)
    authorization_path = campaign_dir / "campaign-authorization.private.json"
    if authorization_path.exists():
        authorization = _read_json(authorization_path, "campaign_authorization_unreadable")
        body = {key: value for key, value in authorization.items() if key != "authorization_fingerprint"}
        if (authorization.get("preflight_sha256") != preflight.get("preflight_sha256")
                or authorization.get("authorization_fingerprint") != canonical_sha256(body)):
            raise CampaignError("campaign_authorization_drift")
    else:
        authorization_body = {
            "schema_version": "reddit-cmv-live-campaign-authorization-v1",
            "campaign_id": CAMPAIGN_ID,
            "preflight_sha256": preflight["preflight_sha256"],
            "authorization_statement_sha256": hashlib.sha256(AUTHORIZATION_STATEMENT.encode("utf-8")).hexdigest(),
            "scope_acknowledged": True,
            "single_use": True,
            "provider_calling_enabled": True,
            "max_primary_slots": int(preflight["total_primary_slots"]),
            "max_requests_all_stages": CAMPAIGN_MAX_REQUESTS,
            "max_tokens_all_stages": CAMPAIGN_MAX_TOKENS,
        }
        authorization = {**authorization_body, "authorization_fingerprint": canonical_sha256(authorization_body)}
        _write_json(authorization_path, authorization)
    state_path = campaign_dir / "campaign-state.private.json"
    if state_path.exists():
        state = _read_json(state_path, "campaign_state_unreadable")
        if state.get("preflight_sha256") != preflight["preflight_sha256"]:
            raise CampaignError("campaign_state_drift")
    else:
        state = {
            "schema_version": "reddit-cmv-live-campaign-state-v1",
            "campaign_id": CAMPAIGN_ID,
            "preflight_sha256": preflight["preflight_sha256"],
            "phase": "prepared",
            "updated_at_utc": _now(),
            "completed_runs": [],
            "active_run_id": None,
        }
        _write_json(state_path, state)
    return preflight, state, plans, campaign_dir


def _as_binding(value: Mapping[str, Any]) -> V7LiveAuthorizationBinding:
    return V7LiveAuthorizationBinding(**dict(value))


def _as_context(value: Mapping[str, Any]) -> ForumPilotLiveFactoryContext:
    return ForumPilotLiveFactoryContext(**dict(value))


def _load_run_control(run_dir: Path) -> tuple[ForumPilotLiveFactoryContext, V7LiveAuthorizationBinding] | None:
    path = run_dir / "live-runtime-binding.private.json"
    if not path.exists():
        return None
    value = _read_json(path, "run_binding_unreadable")
    return _as_context(value["context"]), _as_binding(value["binding"])


def _create_run_control(
    *, run: Mapping[str, Any], preflight: Mapping[str, Any], remaining_tokens: int,
    remaining_requests: int, timeout: float,
) -> tuple[ForumPilotLiveFactoryContext, V7LiveAuthorizationBinding]:
    run_dir = _private(str(run["run_dir"]))
    existing = _load_run_control(run_dir)
    if existing is not None:
        return existing
    if (remaining_tokens <= 0 or remaining_tokens > 10_000_000
            or remaining_requests <= 0 or remaining_requests > 2_008):
        raise CampaignError("run_budget_out_of_bounds")
    run_scope = canonical_sha256({
        "campaign_preflight_sha256": preflight["preflight_sha256"],
        "plan_sha256": run["plan_sha256"],
        "seed": run["seed"],
    })
    run_id = "cmv-" + run_scope[:28]
    project_id = PROJECT_ID
    input_fingerprint = canonical_sha256({
        "plan_sha256": run["plan_sha256"],
        "world_sha256": preflight["fixed_world_sha256"],
        "episode_id": preflight["episode_and_prompt_checks"]["development_episode_id"],
    })
    launch_fingerprint = canonical_sha256({
        "run_id": run_id,
        "input_fingerprint": input_fingerprint,
        "provider_request_limit": remaining_requests,
        "provider_token_limit": remaining_tokens,
        "max_concurrency": 1,
    })
    endpoint_fingerprint = canonical_sha256({"endpoint": ENDPOINT})
    transport_fingerprint = vectorengine_transport_fingerprint(
        endpoint=ENDPOINT,
        model_name=MODEL,
        timeout_seconds=timeout,
        safe_preconnect_retry_limit=2,
        local_https_proxy=PROXY,
    )
    run_preflight_body = {
        "schema_version": "reddit-cmv-single-run-live-preflight-v1",
        "campaign_preflight_sha256": preflight["preflight_sha256"],
        "run_id": run_id,
        "plan_sha256": run["plan_sha256"],
        "provider_request_limit": remaining_requests,
        "provider_token_limit": remaining_tokens,
        "timeout_seconds": timeout,
        "safe_preconnect_retry_limit": 2,
        "post_transmission_retry_limit": 0,
        "transport_config_fingerprint": transport_fingerprint,
    }
    run_preflight = {**run_preflight_body, "preflight_sha256": canonical_sha256(run_preflight_body)}
    auth_body = {
        "schema_version": "reddit-cmv-single-run-live-authorization-v1",
        "preflight_sha256": run_preflight["preflight_sha256"],
        "campaign_authorization_fingerprint": _read_json(
            _private(CAMPAIGN_DIR) / "campaign-authorization.private.json",
            "campaign_authorization_unreadable",
        )["authorization_fingerprint"],
        "single_use": True,
        "scope_acknowledged": True,
    }
    auth_fingerprint = canonical_sha256(auth_body)
    context = ForumPilotLiveFactoryContext(
        run_id=run_id,
        project_id=project_id,
        input_fingerprint=input_fingerprint,
        launch_fingerprint=launch_fingerprint,
        plan_sha256=str(run["plan_sha256"]),
        model_name=MODEL,
        endpoint_fingerprint=endpoint_fingerprint,
        reasoning_effort="none",
        provider_request_limit=remaining_requests,
        provider_token_limit=remaining_tokens,
        max_concurrency=1,
        network_retry_limit=0,
        format_repair_limit=1,
    )
    binding = V7LiveAuthorizationBinding(
        tenant_id="tenant-campus-pulse",
        project_id=project_id,
        run_id=run_id,
        input_fingerprint=input_fingerprint,
        launch_fingerprint=launch_fingerprint,
        plan_sha256=str(run["plan_sha256"]),
        lease_id="lease-" + run_scope[:24],
        lease_generation=1,
        authorization_id="auth-" + auth_fingerprint[:24],
        authorization_fingerprint=auth_fingerprint,
        preflight_sha256=str(run_preflight["preflight_sha256"]),
        transport_config_fingerprint=transport_fingerprint,
        model_name=MODEL,
        endpoint_fingerprint=endpoint_fingerprint,
    )
    _write_json(run_dir / "live-preflight.private.json", run_preflight)
    _write_json(run_dir / "live-authorization.private.json", {
        **auth_body,
        "authorization_fingerprint": auth_fingerprint,
        "authorization_statement_sha256": hashlib.sha256(AUTHORIZATION_STATEMENT.encode("utf-8")).hexdigest(),
    })
    _write_json(run_dir / "live-runtime-binding.private.json", {
        "context": asdict(context), "binding": asdict(binding),
    })
    return context, binding


def _format_repair_policy(
    *, preflight: Mapping[str, Any], campaign_dir: Path,
    remaining_tokens: int, remaining_requests: int,
) -> tuple[str, dict[str, Any]]:
    """Record the single format repair already bound by the run authorization."""
    auth_path = campaign_dir / "campaign-authorization.private.json"
    authorization = _read_json(auth_path, "campaign_authorization_unreadable")
    auth_body = {
        key: value for key, value in authorization.items()
        if key != "authorization_fingerprint"
    }
    auth_fingerprint = str(authorization.get("authorization_fingerprint") or "")
    if (
        authorization.get("preflight_sha256") != preflight.get("preflight_sha256")
        or canonical_sha256(auth_body) != auth_fingerprint
        or authorization.get("max_requests_all_stages") != CAMPAIGN_MAX_REQUESTS
        or authorization.get("max_tokens_all_stages") != CAMPAIGN_MAX_TOKENS
    ):
        raise CampaignError("repair_policy_authorization_mismatch")
    policy_body = {
        "schema_version": "reddit-cmv-choice-preserving-format-repair-v1",
        "campaign_id": CAMPAIGN_ID,
        "campaign_preflight_sha256": preflight["preflight_sha256"],
        "campaign_authorization_fingerprint": auth_fingerprint,
        "scope": "settled_contract_invalid_responses_only",
        "max_format_repairs_per_slot": 1,
        "max_semantic_repairs_per_slot": 0,
        "preserve_behavioral_choice": True,
        "forced_silence_or_noop_fallback": False,
        "rejected_provider_response_included_in_repair": False,
        "automatic_network_retry_limit": 0,
        "unknown_outcome_policy": "pause_without_retry",
        "all_stage_max_requests": CAMPAIGN_MAX_REQUESTS,
        "all_stage_max_tokens": CAMPAIGN_MAX_TOKENS,
    }
    policy_sha256 = canonical_sha256(policy_body)
    path = campaign_dir / "format-repair-policy.private.json"
    record = {
        **policy_body,
        "policy_sha256": policy_sha256,
        "remaining_requests_at_issue": remaining_requests,
        "remaining_tokens_at_issue": remaining_tokens,
        "issued_at_utc": _now(),
    }
    if path.exists():
        saved = _read_json(path, "repair_policy_unreadable")
        saved_body = {
            key: saved[key] for key in policy_body
            if key in saved
        }
        if (
            saved_body != policy_body
            or saved.get("policy_sha256") != canonical_sha256(saved_body)
            or saved.get("remaining_requests_at_issue", 0) > remaining_requests
            or saved.get("remaining_tokens_at_issue", 0) > remaining_tokens
        ):
            # A later resume naturally has less budget than the original issue.
            # A larger live remainder is a ledger inconsistency, not a reason
            # to widen the saved continuation authority.
            if (
                saved_body != policy_body
                or saved.get("policy_sha256") != canonical_sha256(saved_body)
            ):
                raise CampaignError("repair_policy_drift")
        record = saved
    else:
        _write_json(path, record)
    return policy_sha256, record


def _all_live_controls(preflight: Mapping[str, Any]) -> list[tuple[Mapping[str, Any], Any]]:
    found = []
    for run in preflight["runs"]:
        run_dir = _private(str(run["run_dir"]))
        control = _load_run_control(run_dir)
        db = run_dir / "provider-ledger.private.sqlite3"
        if control is None:
            if db.exists():
                raise CampaignError("provider_ledger_has_no_binding")
            continue
        context, binding = control
        if db.exists():
            repository = SQLiteV7AuthorizedTurnRepository(
                db, binding=binding, context=context, initialize=False
            )
            found.append((run, repository.probe()))
    return found


def _budget_totals(preflight: Mapping[str, Any]) -> dict[str, int]:
    controls = _all_live_controls(preflight)
    return {
        "tokens_reported": sum(int(s.provider_reported_tokens) for _, s in controls),
        "tokens_risk_charged": sum(int(s.risk_charged_tokens) for _, s in controls),
        "tokens_in_flight": sum(int(s.tokens_in_flight) for _, s in controls),
        "requests_reserved": sum(int(s.provider_calls_reserved) for _, s in controls),
        "requests_completed": sum(int(s.provider_calls_completed) for _, s in controls),
        "unknown_outcomes": sum(int(s.provider_calls_unknown) for _, s in controls),
    }


def _sync_shared_budget(
    *, preflight: Mapping[str, Any], campaign_state: Mapping[str, Any],
    status: str, lock: threading.Lock | None = None,
) -> dict[str, int]:
    if lock is not None:
        lock.acquire()
    try:
        totals = _budget_totals(preflight)
        path = _private(BUDGET_PATH)
        budget = _direct_budget(path)
        baseline_tokens = int(preflight["preexisting_tokens_committed"])
        baseline_requests = int(preflight["preexisting_requests_dispatched"])
        used_tokens = baseline_tokens + totals["tokens_reported"] + totals["tokens_risk_charged"]
        used_requests = baseline_requests + totals["requests_reserved"] + totals["requests_completed"]
        if used_tokens + totals["tokens_in_flight"] > CAMPAIGN_MAX_TOKENS or used_requests > CAMPAIGN_MAX_REQUESTS:
            raise CampaignError("shared_campaign_hard_cap_reconciliation")
        stage_status = "unknown" if totals["unknown_outcomes"] else status
        budget["tokens_committed"] = used_tokens
        budget["tokens_reserved"] = totals["tokens_in_flight"]
        budget["requests_dispatched"] = used_requests
        budget.setdefault("stages", {})[STAGE_ID] = {
            "status": stage_status,
            "primary_slots": int(preflight["total_primary_slots"]),
            "completed_runs": len(campaign_state.get("completed_runs") or []),
            "request_count": totals["requests_reserved"] + totals["requests_completed"],
            "token_count": totals["tokens_reported"] + totals["tokens_risk_charged"],
            "unknown_count": totals["unknown_outcomes"],
            "updated_at_utc": _now(),
        }
        _write_json(path, budget)
        return totals
    finally:
        if lock is not None:
            lock.release()


def _remaining(preflight: Mapping[str, Any]) -> tuple[int, int]:
    totals = _budget_totals(preflight)
    used_tokens = int(preflight["preexisting_tokens_committed"]) + totals["tokens_reported"] + totals["tokens_risk_charged"]
    used_requests = int(preflight["preexisting_requests_dispatched"]) + totals["requests_reserved"] + totals["requests_completed"]
    token_room = CAMPAIGN_MAX_TOKENS - used_tokens - totals["tokens_in_flight"]
    request_room = CAMPAIGN_MAX_REQUESTS - used_requests
    if token_room <= 0 or request_room <= 0:
        raise CampaignError("campaign_budget_exhausted")
    return token_room, request_room


def _check_proxy(timeout: float, api_key: str) -> dict[str, Any]:
    opener = build_opener(ProxyHandler({"https": PROXY}))
    request = Request(
        "https://api.openlux.ai/v1/models",
        headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with opener.open(request, timeout=min(8.0, timeout)) as response:
            doc = json.loads(response.read(2_000_001).decode("utf-8"))
            ids = {str(row.get("id")) for row in doc.get("data", []) if isinstance(row, dict)}
            ok = int(response.status) == 200 and MODEL in ids
    except (HTTPError, URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        reason = exc.reason if isinstance(exc, URLError) else exc
        if isinstance(reason, (socket.gaierror, ConnectionRefusedError, ssl.SSLEOFError)):
            code = "proxy_preconnect_unavailable"
        else:
            code = "model_catalog_health_check_failed"
        raise CampaignError(code) from exc
    if not ok:
        raise CampaignError("configured_model_not_listed")
    return {"status": "ok", "endpoint": "https://api.openlux.ai/v1/models", "model_available": True, "proxy": PROXY}


def _initial_state(
    *, plan: Mapping[str, Any], world: Mapping[str, Any], episode_row: Mapping[str, Any],
    release_dir: Path,
) -> CmvBranchState:
    world_meta = _read_json(_private(COMPILED_DIR) / "fixed-world.private.json", "world_unreadable")
    particles = initialize_episode_particles(
        load_particle_snapshot(
            _private(COMPILED_DIR) / "initial-particles.private.npz",
            expected_sha256=str(world_meta.get("particle_asset_sha256") or ""),
        ),
        {},
        event_id=str(episode_row["episode_id"]),
        world_seed=int(world.get("world_seed", 20260924)),
    )
    return CmvBranchState(
        scenario_id=str(episode_row["episode_id"]),
        branch="shared_baseline",
        tick=0,
        particles=particles,
        ledger=initialize_cmv_event_ledger(build_episode_scenario_context(episode_row)),
        memories_by_agent={agent_id: AgentMemory(agent_id=agent_id) for agent_id in particles.agent_ids},
    )


def _evaluation_targets(release_dir: Path, episode_id: str) -> list[dict[str, Any]]:
    # Development overlays may deliberately have no held-out continuation.
    # Preserve that missing reference as run_metrics_only, never borrow targets
    # from another episode or fail after all paid decisions have completed.
    if EVENT_OVERLAY_PATH is not None:
        overlay = _read_json(_private(EVENT_OVERLAY_PATH), "event_overlay_unreadable")
        if overlay.get("episode", {}).get("episode_id") == episode_id:
            evaluation = _read_json(_private(EVENT_OVERLAY_PATH).parent / "evaluation-only.private.json", "evaluation_targets_unreadable")
            if evaluation.get("episode_id") != episode_id:
                raise CampaignError("evaluation_episode_mismatch")
            targets = evaluation.get("targets")
            if not isinstance(targets, list):
                raise CampaignError("evaluation_targets_invalid")
            return targets
    path = release_dir / "episode-evaluation-only.private.jsonl.gz"
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if isinstance(row, dict) and row.get("episode_id") == episode_id:
                    targets = row.get("targets")
                    if isinstance(targets, list):
                        return targets
                    break
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CampaignError("evaluation_targets_unreadable") from exc
    raise CampaignError("evaluation_targets_missing")


def _run_one(
    *, run: Mapping[str, Any], plan: Mapping[str, Any], world: Mapping[str, Any],
    episode_row: Mapping[str, Any], preflight: Mapping[str, Any], state: dict[str, Any],
    campaign_dir: Path, timeout: float, api_key_env: str,
) -> dict[str, Any]:
    if plan.get("execution_runtime_sha256") != cmv_runtime_fingerprint():
        raise CampaignError("runtime_changed_new_preflight_required")
    run_dir = _private(str(run["run_dir"]))
    run_plan_path = _private(str(run["plan_path"]))
    persisted_plan = validate_cmv_execution_plan(_read_json(run_plan_path, "plan_unreadable"))
    if persisted_plan["plan_sha256"] != run["plan_sha256"]:
        raise CampaignError("plan_scope_mismatch")
    control_pair = _load_run_control(run_dir)
    db_path = run_dir / "provider-ledger.private.sqlite3"
    if control_pair is None:
        remaining_tokens, remaining_requests = _remaining(preflight)
        control_pair = _create_run_control(
            run=run, preflight=preflight, remaining_tokens=remaining_tokens,
            remaining_requests=remaining_requests, timeout=timeout,
        )
    context, binding = control_pair
    repository = SQLiteV7AuthorizedTurnRepository(
        db_path, binding=binding, context=context, initialize=not db_path.exists()
    )
    prior = repository.probe()
    if prior.provider_calls_unknown or prior.tokens_in_flight:
        raise CampaignError("unknown_or_inflight_provider_attempt")
    if prior.provider_calls_reserved:
        raise CampaignError("unresolved_reserved_provider_attempt")
    transport = VectorEngineV7Transport(
        endpoint=ENDPOINT,
        model_name=MODEL,
        timeout_seconds=timeout,
        api_key_env=api_key_env,
        expected_fingerprint=binding.transport_config_fingerprint,
        safe_preconnect_retry_limit=2,
        local_https_proxy=PROXY,
    )
    source = require_formal_authorized_source(
        AuthorizedV7SemanticSource(
            context=context,
            binding=binding,
            repository=repository,
            transport=transport,
            controlled_repair_policy=_controlled_semantic_repair_policy(
                run_dir,
                binding=binding,
            ),
            manual_format_exhaustion_repairs=_load_format_exhaustion_repairs(
                run_dir,
                binding=binding,
            ),
            manual_known_failure_replacements=_load_known_failure_replacements(
                run_dir,
                binding=binding,
            ),
            request_guard=lambda: enforce_cmv_quality_guard(repository.audit_summary(), plan["quality_policy"]),
        )
    )
    for prior_ledger in PRIOR_REPLAY_LEDGERS:
        source = _ExactResidentReplaySource(
            source,
            _private(prior_ledger),
            expected_model=MODEL,
        )
    run_repo = CmvRunRepository(
        run_dir, run_id="cmv-run-" + str(plan["plan_sha256"])[:20],
        plan_sha256=str(plan["plan_sha256"]), world_sha256=str(plan["world_sha256"]),
    )
    shared_checkpoint = run_repo.restore_latest("shared_baseline")
    if AUDIT_AFTER_SHARED_TICK and (shared_checkpoint is None or shared_checkpoint.tick == 0):
        source = _SharedTickAuditSource(source, int(plan["shared_prefix"]["resident_slots"]))
    episode_context = build_episode_scenario_context(episode_row)
    stop_monitor = threading.Event()
    budget_lock = threading.Lock()

    def monitor() -> None:
        while not stop_monitor.wait(20):
            current = repository.probe()
            totals = _sync_shared_budget(
                preflight=preflight, campaign_state=state, status="running", lock=budget_lock
            )
            campaign_requests_completed = (
                int(preflight["preexisting_requests_dispatched"])
                + totals["requests_completed"]
            )
            campaign_tokens_used = (
                int(preflight["preexisting_tokens_committed"])
                + totals["tokens_reported"]
                + totals["tokens_risk_charged"]
            )
            print(json.dumps({
                "event": "provider_progress",
                "seed": int(run["seed"]),
                "run_requests_completed": current.provider_calls_completed,
                "run_requests_reserved": current.provider_calls_reserved,
                "campaign_requests_completed": campaign_requests_completed,
                "campaign_requests_reserved": totals["requests_reserved"],
                "unknown": totals["unknown_outcomes"],
                "run_reported_tokens": current.provider_reported_tokens,
                "campaign_reported_tokens": totals["tokens_reported"],
                "campaign_risk_charged_tokens": totals["tokens_risk_charged"],
                "campaign_tokens_in_flight": totals["tokens_in_flight"],
                "remaining_campaign_tokens": max(
                    0, CAMPAIGN_MAX_TOKENS - campaign_tokens_used
                    - totals["tokens_in_flight"]
                ),
            }, ensure_ascii=False), flush=True)

    monitor_thread = threading.Thread(target=monitor, name="cmv-budget-watch", daemon=True)
    monitor_thread.start()
    try:
        result = execute_cmv_small_run(
            plan=plan,
            world=world,
            initial_state=_initial_state(
                plan=plan, world=world, episode_row=episode_row,
                release_dir=_private(RELEASE_DIR),
            ),
            episode_context=episode_context,
            relationship_release=world["relationships"],
            source=source,
            expected_model=MODEL,
            run_id="cmv-run-" + str(plan["plan_sha256"])[:20],
            repository=run_repo,
            max_workers=1,
        )
    finally:
        stop_monitor.set()
        monitor_thread.join(timeout=2)
    natural = run_repo.restore_latest("natural")
    governed = run_repo.restore_latest("D")
    if natural is None or governed is None:
        raise CampaignError("completed_run_checkpoints_missing")
    targets = _evaluation_targets(_private(RELEASE_DIR), str(plan["episode_id"]))
    report = evaluate_cmv_paired_run(
        natural_state=natural,
        governed_state=governed,
        world=world,
        reference_targets=targets,
    )
    eval_artifact = {
        "run_id": result["run_id"],
        "episode_id": result["episode_id"],
        "seed": int(run["seed"]),
        "report": report,
    }
    event = run_repo.append_runtime_artifact(
        "evaluation_report", eval_artifact,
        scenario_id=str(plan["episode_id"]), branch="D",
        tick=int(plan["timeline"]["ticks"][-1]),
    )
    final_control = repository.probe()
    if final_control.provider_calls_unknown or final_control.tokens_in_flight:
        raise CampaignError("unknown_or_inflight_provider_attempt")
    output = {
        "run_summary": result,
        "evaluation": report,
        "evaluation_event_sha256": event["event_sha256"],
        "provider_usage": {
            "requests_completed": final_control.provider_calls_completed,
            "tokens_reported": final_control.provider_reported_tokens,
            "risk_charged_tokens": final_control.risk_charged_tokens,
            "unknown": final_control.provider_calls_unknown,
        },
    }
    _write_json(run_dir / "run-summary.private.json", output)
    return output


def _run_campaign(
    *, config_path: Path, timeout: float, api_key_env: str,
) -> int:
    preflight, state, plans, campaign_dir = _prepare_campaign(config_path)
    if state.get("phase") == "paused_quality_review":
        raise CampaignError("no_resident_discussion_requires_review")
    api_key = os.environ.get(api_key_env, "")
    if not api_key.strip():
        raise CampaignError("provider_credential_unavailable")
    health = _check_proxy(timeout, api_key)
    _write_json(campaign_dir / "provider-health.private.json", health)
    remaining_tokens, remaining_requests = _remaining(preflight)
    repair_policy_sha256, repair_policy = _format_repair_policy(
        preflight=preflight,
        campaign_dir=campaign_dir,
        remaining_tokens=remaining_tokens,
        remaining_requests=remaining_requests,
    )
    state["format_repair_policy_sha256"] = repair_policy_sha256
    state["phase"] = "running"
    state["started_at_utc"] = state.get("started_at_utc") or _now()
    state["updated_at_utc"] = _now()
    _write_json(campaign_dir / "campaign-state.private.json", state)
    _sync_shared_budget(preflight=preflight, campaign_state=state, status="running")
    print(json.dumps({
        "event": "campaign_started",
        "campaign_id": CAMPAIGN_ID,
        "primary_slots": int(preflight["total_primary_slots"]),
        "request_upper_bound": int(preflight["request_upper_bound"]),
        "token_cap_all_stages": CAMPAIGN_MAX_TOKENS,
        "remaining_tokens_at_preflight": preflight["remaining_token_room_at_preflight"],
        "remaining_requests_now": remaining_requests,
        "remaining_tokens_now": remaining_tokens,
        "model": MODEL,
        "proxy": PROXY,
        "model_health": "passed",
        "unknown_retry_policy": "stop_without_retry",
        "settled_contract_repair_policy": {
            "max_format_repairs_per_slot": repair_policy["max_format_repairs_per_slot"],
            "max_semantic_repairs_per_slot": repair_policy["max_semantic_repairs_per_slot"],
            "policy_sha256": repair_policy_sha256,
        },
    }, ensure_ascii=False), flush=True)
    release_dir = _private(RELEASE_DIR)
    world = _read_json(_private(COMPILED_DIR) / "fixed-world.private.json", "world_unreadable")
    episode_row = _episode(_read_json_value(release_dir / "episode-observations.private.json", "episodes_unreadable"))
    for run in plans:
        if run["run_id"] in set(state.get("completed_runs") or []):
            continue
        run_dir = _private(str(run["run_dir"]))
        plan = validate_cmv_execution_plan(_read_json(_private(str(run["plan_path"])), "plan_unreadable"))
        state["active_run_id"] = str(run["run_id"])
        state["updated_at_utc"] = _now()
        _write_json(campaign_dir / "campaign-state.private.json", state)
        print(json.dumps({"event": "run_started", "seed": run["seed"], "primary_slots": run["slot_counts"]["primary_total"], "run_id": run["run_id"]}, ensure_ascii=False), flush=True)
        try:
            output = _run_one(
                run=run, plan=plan, world=world, episode_row=episode_row,
                preflight=preflight, state=state, campaign_dir=campaign_dir,
                timeout=timeout, api_key_env=api_key_env,
            )
        except V7LiveUnknownOutcome:
            _sync_shared_budget(preflight=preflight, campaign_state=state, status="unknown")
            state["phase"] = "paused_unknown"
            state["last_error_code"] = "provider_outcome_unknown"
            state["updated_at_utc"] = _now()
            _write_json(campaign_dir / "campaign-state.private.json", state)
            raise CampaignError("provider_outcome_unknown")
        except Exception as exc:
            code_value = getattr(exc, "code", None)
            if isinstance(code_value, str) and code_value:
                code = code_value
            else:
                frames = traceback.extract_tb(exc.__traceback__)
                frame = frames[-1] if frames else None
                source = Path(frame.filename).name if frame is not None else "unknown"
                line = frame.lineno if frame is not None else 0
                code = f"runtime_{type(exc).__name__}_{source}_{line}"
            status = (
                "paused_budget"
                if code in {
                    "v7_live_budget_invalid",
                    "v7_live_budget_exhausted",
                    "shared_campaign_hard_cap_reconciliation",
                }
                else "paused"
            )
            current = _budget_totals(preflight)
            if current["unknown_outcomes"]:
                status = "unknown"
            _sync_shared_budget(preflight=preflight, campaign_state=state, status=status)
            state["phase"] = (
                "paused_unknown" if status == "unknown"
                else "paused_budget" if status == "paused_budget"
                else "paused"
            )
            state["last_error_code"] = code
            state["updated_at_utc"] = _now()
            _write_json(campaign_dir / "campaign-state.private.json", state)
            raise CampaignError(code) from exc
        _write_json(run_dir / "run-summary.private.json", output)
        completed = list(state.get("completed_runs") or [])
        if run["run_id"] not in completed:
            completed.append(str(run["run_id"]))
        state["completed_runs"] = completed
        state["active_run_id"] = None
        state["updated_at_utc"] = _now()
        _write_json(campaign_dir / "campaign-state.private.json", state)
        _sync_shared_budget(preflight=preflight, campaign_state=state, status="running")
        print(json.dumps({
            "event": "run_completed",
            "seed": run["seed"],
            "branch_counts": output["run_summary"]["branches"],
            "provider_usage": output["provider_usage"],
            "evaluation_status": {key: value.get("status") for key, value in output["evaluation"].get("dimensions", {}).items()},
        }, ensure_ascii=False), flush=True)
        forum = output["evaluation"]["dimensions"]["forum_realism"]
        if not any(forum[key]["unique_resident_speakers"] > 0 for key in ("simulated_natural", "simulated_governed")):
            # Silence is a possible outcome, not a reason to fabricate speech.
            # Save the complete run and request inspection before buying seeds.
            state["phase"] = "paused_quality_review"
            state["last_error_code"] = "no_resident_discussion_requires_review"
            _write_json(campaign_dir / "campaign-state.private.json", state)
            _sync_shared_budget(preflight=preflight, campaign_state=state, status="paused")
            raise CampaignError("no_resident_discussion_requires_review")
    state["phase"] = "completed"
    state["active_run_id"] = None
    state["completed_at_utc"] = _now()
    state["updated_at_utc"] = _now()
    _write_json(campaign_dir / "campaign-state.private.json", state)
    totals = _sync_shared_budget(preflight=preflight, campaign_state=state, status="succeeded")
    _write_json(campaign_dir / "campaign-result.private.json", {
        "schema_version": "reddit-cmv-live-campaign-result-v1",
        "campaign_id": CAMPAIGN_ID,
        "preflight_sha256": preflight["preflight_sha256"],
        "completed_run_ids": list(state["completed_runs"]),
        "primary_slots_completed": int(preflight["total_primary_slots"]),
        "provider_totals": totals,
        "all_runs": [
            _read_json(_private(str(row["run_dir"])) / "run-summary.private.json", "run_summary_unreadable")
            for row in plans
        ],
        "completed_at_utc": state["completed_at_utc"],
    })
    print(json.dumps({
        "event": "campaign_completed",
        "primary_slots": int(preflight["total_primary_slots"]),
        "tokens_reported": totals["tokens_reported"],
        "risk_charged_tokens": totals["tokens_risk_charged"],
        "all_stage_tokens_committed": int(preflight["preexisting_tokens_committed"]) + totals["tokens_reported"] + totals["tokens_risk_charged"],
        "all_stage_requests_dispatched": int(preflight["preexisting_requests_dispatched"]) + totals["requests_completed"] + totals["requests_reserved"],
        "result_path": str((campaign_dir / "campaign-result.private.json").relative_to(ROOT)),
    }, ensure_ascii=False), flush=True)
    return 0


def _preflight_only(config_path: Path) -> int:
    preflight, state, plans, campaign_dir = _prepare_campaign(config_path)
    totals = _budget_totals(preflight)
    status = {
        "status": "ready_to_run_with_dynamic_hard_cap",
        "provider_calls_started": False,
        "campaign_id": CAMPAIGN_ID,
        "preflight_sha256": preflight["preflight_sha256"],
        "phase": state.get("phase"),
        "primary_slots": int(preflight["total_primary_slots"]),
        "request_upper_bound": int(preflight["request_upper_bound"]),
        "remaining_tokens_now": max(
            0, CAMPAIGN_MAX_TOKENS - int(preflight["preexisting_tokens_committed"])
            - totals["tokens_reported"] - totals["tokens_risk_charged"] - totals["tokens_in_flight"]
        ),
        "remaining_requests_now": max(
            0, CAMPAIGN_MAX_REQUESTS - int(preflight["preexisting_requests_dispatched"])
            - totals["requests_completed"] - totals["requests_reserved"]
        ),
        "prompt_sample": preflight["episode_and_prompt_checks"],
        "health_check": "GET /v1/models will be checked through local Clash immediately before execution",
        "campaign_directory": str(campaign_dir.relative_to(ROOT)),
    }
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


def _configure_profile(profile: str) -> None:
    global CAMPAIGN_ID, CAMPAIGN_DIR, CAMPAIGN_MAX_TOKENS, CAMPAIGN_MAX_REQUESTS
    global SEEDS_AND_PANELS, AUTHORIZATION_STATEMENT, PROJECT_ID, STAGE_ID
    global PRIOR_REPLAY_LEDGERS, MODEL, FINAL_TICK, SHARED_PANEL_SIZE, PUBLIC_ONLY, AUDIT_AFTER_SHARED_TICK, EVENT_OVERLAY_PATH
    if profile == "full":
        return
    if profile == "main-r18-clean":
        MODEL = "gpt-6-luna"
        CAMPAIGN_ID = "cmv-main-r18-clean-gpt6-luna"
        CAMPAIGN_DIR = Path("data/private/reddit/cmv/campaigns") / CAMPAIGN_ID
        CAMPAIGN_MAX_TOKENS = 5_443_701
        CAMPAIGN_MAX_REQUESTS = 1_500
        SEEDS_AND_PANELS = ((20261004, 10),)
        FINAL_TICK = 6
        SHARED_PANEL_SIZE = 10
        PUBLIC_ONLY = True
        AUDIT_AFTER_SHARED_TICK = True
        EVENT_OVERLAY_PATH = Path("data/private/reddit/cmv/event-overlays/healthcare-education-v2/development-event.private.json")
        PRIOR_REPLAY_LEDGERS = ()
        PROJECT_ID = "reddit-cmv-main-r18"
        STAGE_ID = "reddit_cmv_main_r18"
        AUTHORIZATION_STATEMENT = (
            "The user authorized fixing the diagnosed event/attention/evaluation defects "
            "and bounded verification before the main experiment. Preserve the cumulative "
            "October 1 additional 1000000-token authorization, starting at 4443701 "
            "committed tokens; cumulative ceilings 1500 requests and 5443701 tokens. "
            "Audit the ten shared decisions first. "
            "The six-tick main plan must not be purchased unless the remaining budget "
            "can fund it. Unknown stops without retry."
        )
        return
    if profile == "event-probe-r17-public-repair":
        MODEL = "gpt-6-luna"
        CAMPAIGN_ID = "cmv-event-probe-r17-public-repair-gpt6-luna"
        CAMPAIGN_DIR = Path("data/private/reddit/cmv/campaigns") / CAMPAIGN_ID
        CAMPAIGN_MAX_TOKENS = 4_700_000
        CAMPAIGN_MAX_REQUESTS = 1_275
        SEEDS_AND_PANELS = ((20261004, 10),)
        FINAL_TICK = 3
        SHARED_PANEL_SIZE = 10
        PUBLIC_ONLY = True
        AUDIT_AFTER_SHARED_TICK = True
        EVENT_OVERLAY_PATH = Path("data/private/reddit/cmv/event-overlays/healthcare-education-v1/development-event.private.json")
        PRIOR_REPLAY_LEDGERS = ()
        PROJECT_ID = "reddit-cmv-event-probe-r17"
        STAGE_ID = "reddit_cmv_event_probe_r17"
        AUTHORIZATION_STATEMENT = (
            "The user authorized a bounded gpt-6-luna public CMV contract verification. "
            "This run preserves the r16 fixed world, event, ten-person stratified shared "
            "panel and public-only channel. The declared compact defaults now cover the "
            "opened reply target and an empty memory field; all semantic validation stays "
            "strict. Pause before branching and stop on unknown. Cumulative hard cap is "
            "1,275 requests and 4,700,000 tokens."
        )
        return
    if profile == "event-probe-r16-public":
        MODEL = "gpt-6-luna"
        CAMPAIGN_ID = "cmv-event-probe-r16-public-gpt6-luna"
        CAMPAIGN_DIR = Path("data/private/reddit/cmv/campaigns") / CAMPAIGN_ID
        CAMPAIGN_MAX_TOKENS = 4_700_000
        CAMPAIGN_MAX_REQUESTS = 1_260
        SEEDS_AND_PANELS = ((20261004, 10),)
        FINAL_TICK = 3
        SHARED_PANEL_SIZE = 10
        PUBLIC_ONLY = True
        AUDIT_AFTER_SHARED_TICK = True
        EVENT_OVERLAY_PATH = Path("data/private/reddit/cmv/event-overlays/healthcare-education-v1/development-event.private.json")
        PRIOR_REPLAY_LEDGERS = ()
        PROJECT_ID = "reddit-cmv-event-probe-r16"
        STAGE_ID = "reddit_cmv_event_probe_r16"
        AUTHORIZATION_STATEMENT = (
            "The user authorized gpt-6-luna validation after compacting the resident "
            "contract and redesigning activation. CMV is a public-thread corpus with no "
            "observed private chat; this benchmark disables private actions while retaining "
            "synthetic relationships for attention. Ten stratified shared residents are "
            "audited before branching. Unknown stops without retry. Cumulative hard cap "
            "is 1,260 requests and 4,700,000 tokens."
        )
        return
    if profile == "event-probe-r15-compact":
        MODEL = "gpt-6-luna"
        CAMPAIGN_ID = "cmv-event-probe-r15-compact-gpt6-luna"
        CAMPAIGN_DIR = Path("data/private/reddit/cmv/campaigns") / CAMPAIGN_ID
        CAMPAIGN_MAX_TOKENS = 4_700_000
        CAMPAIGN_MAX_REQUESTS = 1_230
        SEEDS_AND_PANELS = ((20261004, 10),)
        FINAL_TICK = 3
        SHARED_PANEL_SIZE = 10
        AUDIT_AFTER_SHARED_TICK = True
        EVENT_OVERLAY_PATH = Path("data/private/reddit/cmv/event-overlays/healthcare-education-v1/development-event.private.json")
        PRIOR_REPLAY_LEDGERS = ()
        PROJECT_ID = "reddit-cmv-event-probe-r15"
        STAGE_ID = "reddit_cmv_event_probe_r15"
        AUTHORIZATION_STATEMENT = (
            "The user authorized a bounded gpt-6-luna CMV follow-up after r14 contract "
            "failure. This run uses the same fixed world and development event with ten "
            "positive-probability shared activations, paired branch panels, and a compact "
            "resident decision form whose declared bookkeeping defaults are validated by "
            "the original ForumTwin V3 contract. Pause after shared Tick 1. Unknown stops "
            "without retry. Cumulative cap: 1,230 requests and 4,700,000 tokens."
        )
        return
    if profile == "event-probe-r14-structured":
        MODEL = "gpt-6-luna"
        CAMPAIGN_ID = "cmv-event-probe-r14-structured-gpt6-luna"
        CAMPAIGN_DIR = Path("data/private/reddit/cmv/campaigns") / CAMPAIGN_ID
        CAMPAIGN_MAX_TOKENS = 4_700_000
        CAMPAIGN_MAX_REQUESTS = 1_210
        SEEDS_AND_PANELS = ((20261004, 10),)
        FINAL_TICK = 3
        SHARED_PANEL_SIZE = 12
        AUDIT_AFTER_SHARED_TICK = True
        EVENT_OVERLAY_PATH = Path("data/private/reddit/cmv/event-overlays/healthcare-education-v1/development-event.private.json")
        PRIOR_REPLAY_LEDGERS = ()
        PROJECT_ID = "reddit-cmv-event-probe-r14"
        STAGE_ID = "reddit_cmv_event_probe_r14"
        AUTHORIZATION_STATEMENT = (
            "The user authorized a bounded gpt-6-luna CMV contract and activation repair. "
            "This run retains the fixed 24-person world and alternative development event; "
            "a positive-probability stratified panel of 12 residents enters the shared first "
            "tick, with paired ten-person panels later. The English resident request uses "
            "a JSON-schema envelope while the existing semantic validator remains strict. "
            "Pause after 12 shared turns; unknown outcomes stop without retry. The shared "
            "cumulative budget remains capped at 1,210 requests and 4,700,000 tokens."
        )
        return
    if profile == "followup-r11":
        MODEL = "gpt-6-luna"
        CAMPAIGN_ID = "cmv-followup-r11-gpt6-luna"
        CAMPAIGN_DIR = Path("data/private/reddit/cmv/campaigns") / CAMPAIGN_ID
        CAMPAIGN_MAX_TOKENS = 4_700_000
        CAMPAIGN_MAX_REQUESTS = 1_200
        SEEDS_AND_PANELS = ((20261002, 10),)
        FINAL_TICK = 5
        AUDIT_AFTER_SHARED_TICK = True
        PRIOR_REPLAY_LEDGERS = ()
        PROJECT_ID = "reddit-cmv-followup-r11"
        STAGE_ID = "reddit_cmv_followup_r11"
        AUTHORIZATION_STATEMENT = (
            "The user authorized a bounded corrected Reddit CMV run using gpt-6-luna "
            "through OpenLux with reasoning none. The paired five-tick plan has 110 primary "
            "slots and at most 220 new requests; pause after the 24 shared resident turns "
            "for behavioral audit before continuing the same plan. The cumulative shared "
            "budget is capped at 1,200 requests and 4,700,000 tokens. Unknown results "
            "stop without retry and prior runs remain immutable."
        )
        return
    if profile == "event-probe-r12":
        MODEL = "gpt-6-luna"
        CAMPAIGN_ID = "cmv-event-probe-r12-gpt6-luna"
        CAMPAIGN_DIR = Path("data/private/reddit/cmv/campaigns") / CAMPAIGN_ID
        CAMPAIGN_MAX_TOKENS = 4_700_000
        CAMPAIGN_MAX_REQUESTS = 1_200
        SEEDS_AND_PANELS = ((20261003, 10),)
        FINAL_TICK = 3
        AUDIT_AFTER_SHARED_TICK = True
        EVENT_OVERLAY_PATH = Path("data/private/reddit/cmv/event-overlays/healthcare-education-v1/development-event.private.json")
        PRIOR_REPLAY_LEDGERS = ()
        PROJECT_ID = "reddit-cmv-event-probe-r12"
        STAGE_ID = "reddit_cmv_event_probe_r12"
        AUTHORIZATION_STATEMENT = (
            "The user authorized rapid bounded diagnosis of the CMV pilot and sufficient paid "
            "model usage. This new event probe uses the same fixed 24-person world and a "
            "separately frozen development-period healthcare/education thread, with gpt-6-luna "
            "and reasoning none through OpenLux. The paired three-tick plan has 70 primary "
            "slots and at most 140 new requests, but pauses after the 24 shared resident "
            "turns for a behavioral audit. The cumulative shared budget stays capped at "
            "1,200 requests and 4,700,000 tokens. Unknown outcomes stop without retry."
        )
        return
    if profile == "event-probe-r13-mini":
        MODEL = "gpt-5.4-mini"
        CAMPAIGN_ID = "cmv-event-probe-r13-gpt54-mini"
        CAMPAIGN_DIR = Path("data/private/reddit/cmv/campaigns") / CAMPAIGN_ID
        CAMPAIGN_MAX_TOKENS = 4_700_000
        CAMPAIGN_MAX_REQUESTS = 1_200
        SEEDS_AND_PANELS = ((20261003, 10),)
        FINAL_TICK = 3
        AUDIT_AFTER_SHARED_TICK = True
        EVENT_OVERLAY_PATH = Path("data/private/reddit/cmv/event-overlays/healthcare-education-v1/development-event.private.json")
        PRIOR_REPLAY_LEDGERS = ()
        PROJECT_ID = "reddit-cmv-event-probe-r13"
        STAGE_ID = "reddit_cmv_event_probe_r13"
        AUTHORIZATION_STATEMENT = (
            "The user authorized switching the CMV development-event experiment to "
            "gpt-5.4-mini through the configured OpenLux endpoint to test strict output "
            "compliance and then repair the remaining simulation defects. This probe uses "
            "the same 24-person world, healthcare/education event, seed and sanitized fields "
            "as r12; it pauses after the first 24 shared resident turns before any branch "
            "calls. The full frozen plan has 70 primary slots and at most 140 requests. "
            "The cumulative shared ceiling remains 1,200 requests and 4,700,000 tokens. "
            "Unknown outcomes stop without retry."
        )
        return
    MODEL = "gpt-6-luna"
    CAMPAIGN_ID = "cmv-corrected-pilot-r10-gpt6-luna"
    CAMPAIGN_DIR = Path("data/private/reddit/cmv/campaigns") / CAMPAIGN_ID
    # The current authorization uses the previously established cumulative cap.
    CAMPAIGN_MAX_TOKENS = 3_968_888
    # Preserve every earlier settled request; the remaining 85 requests must
    # cover only the 30 slots that cannot be exact-replayed.
    CAMPAIGN_MAX_REQUESTS = 1_000
    # A fresh deterministic seed keeps the already-settled r3 ledger immutable
    # while preserving the same population, event, panel size and run design.
    SEEDS_AND_PANELS = ((20261001, 12),)
    PROJECT_ID = "reddit-cmv-corrected-pilot"
    STAGE_ID = "reddit_cmv_corrected_pilot_r10"
    PRIOR_REPLAY_LEDGERS = (
        Path("data/private/reddit/cmv/runs/cmv-run-ceb174ad2be777140ae5/provider-ledger.private.sqlite3"),
    )
    AUTHORIZATION_STATEMENT = (
        "The user authorized completing the corrected Reddit CMV experiment with gpt-6-luna "
        "through OpenLux after the gpt-5.6-luna contract failure. Forty-eight previously "
        "validated resident turns must exact-replay; only 30 remaining primary slots may call "
        "the provider. The cumulative ceiling remains 1,000 requests and 3,968,888 tokens, "
        "with at most 85 requests and 913,617 tokens remaining at preflight. Unknown stops."
    )


def main(argv: list[str] | None = None) -> int:
    global COMPILED_DIR
    parser = argparse.ArgumentParser(description="Run/resume an authorized Reddit CMV campaign.")
    parser.add_argument("--mode", choices=("preflight", "run"), default="preflight")
    parser.add_argument("--profile", choices=("full", "corrected-pilot", "followup-r11", "event-probe-r12", "event-probe-r13-mini", "event-probe-r14-structured", "event-probe-r15-compact", "event-probe-r16-public", "event-probe-r17-public-repair", "main-r18-clean"), default="full")
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "reddit" / "cmv.json")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--api-key-env", default="OPENLUX_API_KEY")
    args = parser.parse_args(argv)
    try:
        _configure_profile(args.profile)
        COMPILED_DIR = Path(_load_config(args.config)["world"].get("compiled_dir", str(COMPILED_DIR)))
        if not 1 <= args.timeout <= 300:
            raise CampaignError("timeout_out_of_bounds")
        if args.mode == "preflight":
            return _preflight_only(args.config.resolve())
        return _run_campaign(
            config_path=args.config.resolve(), timeout=args.timeout,
            api_key_env=args.api_key_env,
        )
    except CampaignError as exc:
        print(json.dumps({"status": "stopped", "diagnostic_code": exc.code}, ensure_ascii=False), file=sys.stderr)
        return 2
    except V7LiveError as exc:
        print(json.dumps({"status": "stopped", "diagnostic_code": exc.code}, ensure_ascii=False), file=sys.stderr)
        return 2
    except Exception as exc:
        # Never print exception text from a Provider/runtime path.
        print(json.dumps({"status": "stopped", "diagnostic_code": type(exc).__name__}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
