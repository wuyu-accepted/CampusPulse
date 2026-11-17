"""Local operator service for cost-bounded live ForumTwin runs.

The browser chooses a reviewed scenario and run scope.  This service freezes
the matching immutable preflight, records an explicit operator acknowledgement,
and launches the existing authorized runner.  Prompt bodies and credentials
never enter the run API and are never returned to the browser.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
from typing import Any
from uuid import uuid4

from backend.campus_pulse.provider_config import (
    ProviderConfigError,
    provider_chat_completions_url,
    provider_config_paths,
    provider_config_status,
)
from backend.campus_pulse.runtime_contract import canonical_sha256


QUICK_RUN_SCOPE = "quick_closed_loop"
RESOURCE_RUN_SCOPE = "resource_public_private_closed_loop"
RUN_SCOPES = {QUICK_RUN_SCOPE, RESOURCE_RUN_SCOPE}
PREFLIGHT_SCOPE = "shared_T3_cost_bounded_live_vignette"
PREFLIGHT_SCHEMA = "campus-pulse-forum-hero-preflight-v1"
RESOURCE_PREFLIGHT_SCOPE = (
    "one_scenario_one_seed_Natural_D_T3_T10_public_private_closed_loop"
)
RESOURCE_PREFLIGHT_SCHEMA = "campus-pulse-forum-v4-resource-hero-preflight-v1"
RESOURCE_ENDPOINT = "https://yunwu.ai/v1/chat/completions"
RESOURCE_MODEL = "gpt-5.6-luna"
SESSION_SCHEMA = "campus-pulse-forum-live-vignette-session-v1"
STATUS_SCHEMA = "campus-pulse-forum-live-vignette-status-v1"
SESSION_PATTERN = re.compile(r"^forum-vignette-[a-z0-9]{12}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SUPPORTED_SCENARIOS = {
    "century_gym_ghost_booking_dispute": {
        "label_zh": "世纪馆“幽灵预约”争议",
        "label_en": "Century Gym ghost-booking dispute",
    },
    "governance_legitimacy_dispute": {
        "label_zh": "暑期住宿床位分配正当性争议",
        "label_en": "Summer housing allocation legitimacy dispute",
    },
    "lecture_external_incident_shock": {
        "label_zh": "讲座冲突事件传播",
        "label_en": "Lecture conflict incident propagation",
    },
}
AUTHORIZATION_STATEMENT = (
    "I approve this single cost-bounded live ForumTwin run. The configured "
    "OpenAI-compatible Provider may receive only the de-identified synthetic "
    "fields listed in the displayed preflight, within its displayed request "
    "and token limits. A settled Provider-known failure may receive one "
    "attempt-bound replacement after body-free evidence is recorded; an "
    "unknown outcome must pause for a separate operator decision."
)
AUTHORIZATION_STATEMENT_SHA256 = hashlib.sha256(
    AUTHORIZATION_STATEMENT.encode("utf-8")
).hexdigest()
UNKNOWN_RESOLUTION_STATEMENT = (
    "I abandon the selected unknown Provider attempt and permanently reject "
    "any late result. I authorize exactly one replacement using the same "
    "hash-bound de-identified synthetic prompt and accept that both requests "
    "may be billed. A second unknown outcome must remain stopped."
)
UNKNOWN_RESOLUTION_STATEMENT_SHA256 = hashlib.sha256(
    UNKNOWN_RESOLUTION_STATEMENT.encode("utf-8")
).hexdigest()


class ForumLiveVignetteError(ValueError):
    """Raised when a quick live run cannot proceed without contract drift."""


_lock = threading.Lock()
_processes: dict[str, subprocess.Popen] = {}
_log_handles: dict[str, Any] = {}


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def _live_root() -> Path:
    return (_root() / "_run" / "forum-twin-live-sessions").resolve()


def _session_dir(session_id: str) -> Path:
    if SESSION_PATTERN.fullmatch(session_id) is None:
        raise ForumLiveVignetteError("live vignette session id is invalid")
    root = _live_root()
    path = (root / session_id).resolve()
    if root not in path.parents:
        raise ForumLiveVignetteError("live vignette session escaped its run root")
    return path


def _paths(session_id: str) -> dict[str, Path]:
    session = _session_dir(session_id)
    control = session / "control"
    return {
        "root": _root(),
        "session": session,
        "control": control,
        "preflight": control / "forum-hero-preflight-v1.json",
        "resource_preflight": control / "forum-v4-resource-hero-preflight-v1.json",
        "authorization": control / "forum-hero-authorization-v1.json",
        "session_record": control / "forum-live-vignette-session-v1.json",
        "process_record": control / "operator-process-v1.json",
        "population": (
            _root()
            / "evidence"
            / "century-gym-live-v1"
            / "semantic-forum-pilot-profile-bridge-v5.json"
        ),
        "relationships": (
            _root()
            / "data"
            / "private"
            / "campus-pulse"
            / "forumtwin-v2"
            / "synthetic-relationship-release-v1.json"
        ),
        "stakes": (
            _root()
            / "data"
            / "private"
            / "campus-pulse"
            / "forumtwin-v2"
            / "scenario-stake-release-v1.json"
        ),
        "manifest": session / "public-progress" / "latest.json",
        "ledger": session / "authorized-live-ledger.sqlite3",
        "log": session / "operator-run.log",
        "result": session / "forum-hero-result-through-T6.json",
        "resource_result": session / "forum-v4-resource-hero-result.json",
    }


def _scope_artifacts(paths: dict[str, Path], run_scope: str) -> tuple[Path, Path]:
    if run_scope == QUICK_RUN_SCOPE:
        return paths["preflight"], paths["result"]
    if run_scope == RESOURCE_RUN_SCOPE:
        return paths["resource_preflight"], paths["resource_result"]
    raise ForumLiveVignetteError("live vignette run scope is invalid")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ForumLiveVignetteError(
            f"live vignette artifact is unreadable: {path.name}"
        ) from exc
    if not isinstance(value, dict):
        raise ForumLiveVignetteError(
            f"live vignette artifact is invalid: {path.name}"
        )
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
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


def _subprocess_environment() -> dict[str, str]:
    root = _root()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (
            str(root),
            str(root / "src"),
            environment.get("PYTHONPATH", ""),
        )
    ).strip(os.pathsep)
    if not environment.get("HTTPS_PROXY"):
        try:
            with socket.create_connection(("127.0.0.1", 7897), timeout=0.08):
                environment["HTTPS_PROXY"] = "http://127.0.0.1:7897"
                environment["HTTP_PROXY"] = "http://127.0.0.1:7897"
        except OSError:
            pass
    return environment


def _run_control_command(command: list[str], *, timeout: float = 180.0) -> None:
    try:
        completed = subprocess.run(
            command,
            cwd=_root(),
            env=_subprocess_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ForumLiveVignetteError(
            "live vignette control command could not complete"
        ) from exc
    if completed.returncode != 0:
        # The child output can contain local paths.  Keep it in the operator
        # log boundary instead of reflecting it through the browser API.
        raise ForumLiveVignetteError(
            "live vignette control command rejected the requested contract"
        )


def _validated_config() -> dict[str, Any]:
    try:
        config = provider_config_status()
    except ProviderConfigError as exc:
        raise ForumLiveVignetteError(
            "local Provider configuration is unreadable"
        ) from exc
    if not config.get("configured"):
        raise ForumLiveVignetteError(
            "请先在“系统设置 → 模型与 API”保存本机配置"
        )
    if config.get("reasoning_effort") != "none":
        raise ForumLiveVignetteError(
            "快速真实推演要求 reasoning effort 为 none"
        )
    return config


def _validate_preflight(
    preflight: dict[str, Any],
    *,
    scenario_id: str | None = None,
    config: dict[str, Any] | None = None,
    run_scope: str = QUICK_RUN_SCOPE,
) -> None:
    body = {
        key: value
        for key, value in preflight.items()
        if key != "preflight_sha256"
    }
    expected_endpoint = (
        provider_chat_completions_url(str(config["base_url"]))
        if config is not None
        else preflight.get("endpoint")
    )
    common_invalid = (
        run_scope not in RUN_SCOPES
        or preflight.get("preflight_sha256") != canonical_sha256(body)
        or preflight.get("branches") != ["Natural", "D"]
        or preflight.get("shared_prefix_end_tick") != 3
        or preflight.get("safe_preconnect_retry_limit") != 2
        or preflight.get("network_retry_limit") != 0
        or preflight.get("privacy_scan", {}).get("passed") is not True
        or preflight.get("endpoint") != expected_endpoint
        or (scenario_id is not None and preflight.get("scenario_id") != scenario_id)
        or (config is not None and preflight.get("model") != config.get("model_name"))
    )
    if run_scope == QUICK_RUN_SCOPE:
        scope_invalid = (
            preflight.get("schema_version") != PREFLIGHT_SCHEMA
            or preflight.get("scope") != PREFLIGHT_SCOPE
            or preflight.get("ready") is not False
            or preflight.get("authorization_present") is not False
            or preflight.get("ticks") != [3, 4, 5, 6]
            or preflight.get("resident_slots") != 112
            or preflight.get("governance_actor_slots") != 6
            or preflight.get("primary_slots") != 118
            or preflight.get("provider_request_hard_limit") != 236
            or preflight.get("provider_token_hard_limit") != 2_000_000
            or preflight.get("resident_turn_schema_version")
            != "campus-pulse-forum-agent-turn-v2"
            or preflight.get("emulator_public_messages") != 0
        )
    else:
        channels = preflight.get("channels") or {}
        scope_invalid = (
            preflight.get("schema_version") != RESOURCE_PREFLIGHT_SCHEMA
            or preflight.get("scope") != RESOURCE_PREFLIGHT_SCOPE
            or preflight.get("preflight_ready_for_single_use_authorization")
            is not True
            or preflight.get("provider_calls_executed") != 0
            or preflight.get("scenario_id") != "governance_legitimacy_dispute"
            or preflight.get("endpoint") != RESOURCE_ENDPOINT
            or preflight.get("model") != RESOURCE_MODEL
            or preflight.get("ticks") != list(range(3, 11))
            or preflight.get("decision_ticks") != [4, 7]
            or preflight.get("resident_slots") != 352
            or preflight.get("governance_actor_slots") != 6
            or preflight.get("primary_slots") != 358
            or preflight.get("provider_request_hard_limit") != 1_790
            or preflight.get("provider_token_hard_limit") != 8_000_000
            or preflight.get("resident_turn_schema_version")
            != "campus-pulse-forum-agent-turn-v4"
            or preflight.get("max_concurrency") != 8
            or channels.get("public_global_hot_top10_plus_latest") is not True
            or channels.get("relationship_attention") is not True
            or channels.get("friend_private_chat") is not True
            or channels.get("dynamic_groups") is not True
            or channels.get("same_forum_optional_anonymity") is not True
            or channels.get("dynamics_generated_language") is not False
        )
    if common_invalid or scope_invalid:
        raise ForumLiveVignetteError(
            "live ForumTwin preflight is invalid or no longer matches local configuration"
        )


def _public_preflight(
    *,
    session_id: str,
    preflight: dict[str, Any],
    config: dict[str, Any],
    run_scope: str,
) -> dict[str, Any]:
    scenario_id = str(preflight["scenario_id"])
    scenario = SUPPORTED_SCENARIOS[scenario_id]
    return {
        "schema_version": STATUS_SCHEMA,
        "session_id": session_id,
        "prepared": True,
        "scenario_id": scenario_id,
        "scenario_label_zh": scenario["label_zh"],
        "scenario_label_en": scenario["label_en"],
        "run_scope": run_scope,
        "scope": (
            "共享 Tick 3 + Natural/D Tick 4–6"
            if run_scope == QUICK_RUN_SCOPE
            else "共享 Tick 3 + Natural/D Tick 4–10（公域、私聊、匿名与风险闭环）"
        ),
        "preflight_sha256": preflight["preflight_sha256"],
        "model": preflight["model"],
        "endpoint": preflight["endpoint"],
        "reasoning_effort": "none",
        "resident_slots": int(preflight["resident_slots"]),
        "governance_actor_slots": int(preflight["governance_actor_slots"]),
        "primary_slots": int(preflight["primary_slots"]),
        "provider_request_hard_limit": int(
            preflight["provider_request_hard_limit"]
        ),
        "provider_token_hard_limit": int(
            preflight["provider_token_hard_limit"]
        ),
        "max_residents_one_tick": 16 if run_scope == QUICK_RUN_SCOPE else 24,
        "max_residents_plus_governance_one_tick": (
            19 if run_scope == QUICK_RUN_SCOPE else 27
        ),
        "expected_committed_ticks": 7 if run_scope == QUICK_RUN_SCOPE else 15,
        "channels": dict(preflight.get("channels") or {}),
        "sent_fields": list(preflight.get("sent_fields") or []),
        "excluded_fields": list(preflight.get("excluded_fields") or []),
        "privacy_scan": dict(preflight.get("privacy_scan") or {}),
        "authorization_statement": AUTHORIZATION_STATEMENT,
        "authorization_statement_sha256": AUTHORIZATION_STATEMENT_SHA256,
        "provider_configured": bool(config.get("configured")),
        "contains_secret": False,
        "contains_prompt_or_provider_body": False,
    }


def prepare(
    scenario_id: str,
    *,
    run_scope: str = QUICK_RUN_SCOPE,
) -> dict[str, Any]:
    """Create an immutable, body-free preflight without sending any prompt."""

    if scenario_id not in SUPPORTED_SCENARIOS:
        raise ForumLiveVignetteError("quick live scenario is unsupported")
    if run_scope not in RUN_SCOPES:
        raise ForumLiveVignetteError("live ForumTwin run scope is unsupported")
    if (
        run_scope == RESOURCE_RUN_SCOPE
        and scenario_id != "governance_legitimacy_dispute"
    ):
        raise ForumLiveVignetteError(
            "the public/private resource run is available for the housing allocation scenario"
        )
    config = _validated_config()
    session_id = "forum-vignette-" + uuid4().hex[:12]
    paths = _paths(session_id)
    if not paths["population"].is_file():
        raise ForumLiveVignetteError(
            "reviewed 1,000-Agent Profile-v2 population is missing"
        )
    if run_scope == QUICK_RUN_SCOPE:
        command = [
            sys.executable,
            str(paths["root"] / "scripts" / "build_forum_hero_preflight.py"),
            "--population",
            str(paths["population"]),
            "--output-dir",
            str(paths["control"]),
            "--endpoint",
            provider_chat_completions_url(str(config["base_url"])),
            "--model",
            str(config["model_name"]),
            "--scenario-id",
            scenario_id,
            "--branches",
            "Natural,D",
            "--start-tick",
            "3",
            "--end-tick",
            "6",
            "--resident-budget-override",
            "16",
            "--shared-prefix-end-tick",
            "3",
            "--provider-token-hard-limit",
            "2000000",
            "--safe-preconnect-retry-limit",
            "2",
            "--resident-turn-schema-version",
            "campus-pulse-forum-agent-turn-v2",
        ]
    else:
        if not paths["relationships"].is_file() or not paths["stakes"].is_file():
            raise ForumLiveVignetteError(
                "reviewed relationship or scenario-stake release is missing"
            )
        command = [
            sys.executable,
            str(
                paths["root"]
                / "scripts"
                / "build_forum_v4_resource_hero_preflight.py"
            ),
            "--population",
            str(paths["population"]),
            "--relationships",
            str(paths["relationships"]),
            "--stakes",
            str(paths["stakes"]),
            "--output-dir",
            str(paths["control"]),
            "--endpoint",
            provider_chat_completions_url(str(config["base_url"])),
            "--model",
            str(config["model_name"]),
            "--safe-preconnect-retry-limit",
            "2",
        ]
    try:
        _run_control_command(command)
        preflight_path, _ = _scope_artifacts(paths, run_scope)
        preflight = _read_json(preflight_path)
        _validate_preflight(
            preflight,
            scenario_id=scenario_id,
            config=config,
            run_scope=run_scope,
        )
        record = {
            "schema_version": SESSION_SCHEMA,
            "session_id": session_id,
            "scenario_id": scenario_id,
            "run_scope": run_scope,
            "preflight_sha256": preflight["preflight_sha256"],
            "prepared_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "authorization_statement_sha256": AUTHORIZATION_STATEMENT_SHA256,
            "contains_secret": False,
            "contains_prompt_or_provider_body": False,
        }
        record["content_sha256"] = canonical_sha256(record)
        _write_json(paths["session_record"], record)
    except Exception:
        # Preserve a successful preflight for audit.  A half-created directory
        # without a valid session record is never startable and exposes no key.
        raise
    return status(session_id)


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _process_running(session_id: str, paths: dict[str, Path]) -> bool:
    process = _processes.get(session_id)
    if process is not None:
        if process.poll() is None:
            return True
        handle = _log_handles.pop(session_id, None)
        if handle is not None:
            handle.close()
        _processes.pop(session_id, None)
        return False
    if paths["process_record"].is_file():
        try:
            record = _read_json(paths["process_record"])
            return _pid_is_running(int(record.get("pid") or 0))
        except (ForumLiveVignetteError, TypeError, ValueError):
            return False
    return False


def _latest_known_failure_attempt_id(ledger_path: Path) -> str:
    """Read only the latest settled known-failure identity from a local ledger."""

    if not ledger_path.is_file():
        raise ForumLiveVignetteError("live vignette ledger is unavailable")
    try:
        connection = sqlite3.connect(
            f"{ledger_path.resolve().as_uri()}?mode=ro&immutable=1",
            uri=True,
        )
        connection.execute("PRAGMA query_only=ON")
        try:
            row = connection.execute(
                """
                SELECT attempt_id FROM v7_live_attempts
                WHERE state = 'settled' AND outcome = 'known_failure'
                ORDER BY request_occurrence DESC LIMIT 1
                """
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise ForumLiveVignetteError(
            "live vignette known-failure evidence could not be inspected"
        ) from exc
    attempt_id = str(row[0] if row else "")
    if re.fullmatch(r"ylva_[0-9a-f]{24}", attempt_id) is None:
        raise ForumLiveVignetteError(
            "live vignette has no safely settled known failure to replace"
        )
    return attempt_id


def _latest_unknown_attempt_id(ledger_path: Path) -> str:
    """Read only the latest settled unknown identity from a local ledger."""

    if not ledger_path.is_file():
        raise ForumLiveVignetteError("live vignette ledger is unavailable")
    try:
        connection = sqlite3.connect(
            f"{ledger_path.resolve().as_uri()}?mode=ro&immutable=1",
            uri=True,
        )
        connection.execute("PRAGMA query_only=ON")
        try:
            row = connection.execute(
                """
                SELECT attempt_id FROM v7_live_attempts
                WHERE state = 'settled' AND outcome = 'unknown'
                ORDER BY request_occurrence DESC LIMIT 1
                """
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise ForumLiveVignetteError(
            "live vignette unknown outcome could not be inspected"
        ) from exc
    attempt_id = str(row[0] if row else "")
    if re.fullmatch(r"ylva_[0-9a-f]{24}", attempt_id) is None:
        raise ForumLiveVignetteError(
            "live vignette has no settled unknown outcome to resolve"
        )
    return attempt_id


def _unknown_resolution_files(paths: dict[str, Path]) -> list[Path]:
    return sorted(paths["control"].glob("unknown-resolution-ylva_*.json"))


def _resolved_unknown_attempt_ids(paths: dict[str, Path]) -> set[str]:
    resolved: set[str] = set()
    for evidence_path in _unknown_resolution_files(paths):
        evidence = _read_json(evidence_path)
        evidence_body = {
            key: value
            for key, value in evidence.items()
            if key != "evidence_sha256"
        }
        attempt_id = str(evidence.get("failed_attempt_id") or "")
        if (
            evidence.get("schema_version")
            != "campus-pulse-forum-hero-unknown-outcome-resolution-v1"
            or evidence.get("evidence_sha256") != canonical_sha256(evidence_body)
            or re.fullmatch(r"ylva_[0-9a-f]{24}", attempt_id) is None
            or evidence.get("resolution") != "abandon_and_replace"
            or evidence.get("replacement_limit") != 1
            or evidence.get("late_provider_result_integration_forbidden") is not True
        ):
            raise ForumLiveVignetteError(
                "live vignette unknown-resolution evidence is invalid"
            )
        resolved.add(attempt_id)
    return resolved


def _unknown_resolution_evidence(
    *,
    paths: dict[str, Path],
    preflight_path: Path,
    attempt_id: str,
) -> Path:
    """Create exactly one body-free, operator-approved unknown resolution."""

    existing = _unknown_resolution_files(paths)
    if existing:
        raise ForumLiveVignetteError(
            "this live run already consumed its one unknown-outcome replacement"
        )
    evidence_path = paths["control"] / f"unknown-resolution-{attempt_id}.json"
    _run_control_command(
        [
            sys.executable,
            str(
                paths["root"]
                / "scripts"
                / "build_forum_hero_unknown_resolution.py"
            ),
            "--ledger",
            str(paths["ledger"]),
            "--preflight",
            str(preflight_path),
            "--authorization",
            str(paths["authorization"]),
            "--statement-sha256",
            UNKNOWN_RESOLUTION_STATEMENT_SHA256,
            "--attempt-id",
            attempt_id,
            "--output",
            str(evidence_path),
        ]
    )
    return evidence_path


def _known_failure_replacement_evidence(
    *,
    paths: dict[str, Path],
    preflight_path: Path,
) -> Path:
    """Create or reuse attempt-bound, body-free replacement evidence."""

    attempt_id = _latest_known_failure_attempt_id(paths["ledger"])
    evidence_path = paths["control"] / f"known-failure-replacement-{attempt_id}.json"
    if not evidence_path.is_file():
        _run_control_command(
            [
                sys.executable,
                str(
                    paths["root"]
                    / "scripts"
                    / "build_forum_hero_known_failure_evidence.py"
                ),
                "--ledger",
                str(paths["ledger"]),
                "--preflight",
                str(preflight_path),
                "--authorization",
                str(paths["authorization"]),
                "--attempt-id",
                attempt_id,
                "--output",
                str(evidence_path),
            ]
        )
    return evidence_path


def _public_result_summary(
    result_path: Path,
    *,
    manifest_result_sha256: str | None,
) -> dict[str, Any]:
    """Return a body-free outcome summary after verifying both result hashes."""

    wrapper = _read_json(result_path)
    wrapper_body = {
        key: value for key, value in wrapper.items() if key != "artifact_sha256"
    }
    if wrapper.get("artifact_sha256") != canonical_sha256(wrapper_body):
        raise ForumLiveVignetteError("live vignette result wrapper hash mismatch")
    result = wrapper.get("result")
    if not isinstance(result, dict):
        raise ForumLiveVignetteError("live vignette domain result is missing")
    result_body = {
        key: value for key, value in result.items() if key != "result_sha256"
    }
    result_sha256 = str(result.get("result_sha256") or "")
    if (
        SHA256_PATTERN.fullmatch(result_sha256) is None
        or result_sha256 != canonical_sha256(result_body)
        or (
            manifest_result_sha256 is not None
            and result_sha256 != manifest_result_sha256
        )
    ):
        raise ForumLiveVignetteError("live vignette domain result hash mismatch")
    branches = result.get("branches")
    if not isinstance(branches, dict):
        raise ForumLiveVignetteError("live vignette result branches are missing")

    def branch_summary(branch_name: str) -> dict[str, Any]:
        branch = branches.get(branch_name)
        if not isinstance(branch, dict):
            raise ForumLiveVignetteError(
                "live vignette result lacks a paired branch"
            )
        timeline = branch.get("timeline") or []
        messages = branch.get("messages") or []
        if not isinstance(timeline, list) or not timeline or not isinstance(messages, list):
            raise ForumLiveVignetteError(
                "live vignette public branch projection is invalid"
            )
        final = timeline[-1]
        if not isinstance(final, dict):
            raise ForumLiveVignetteError(
                "live vignette final timeline point is invalid"
            )
        uptake = branch.get("governance_uptake_map") or {}
        uptake_summary = uptake.get("summary") or {}
        chains = branch.get("mechanism_chains") or {}
        correction_chains = chains.get("correction_chains") or []
        governance_chains = chains.get("governance_uptake_chains") or []
        return {
            "public_messages": len(messages),
            "reply_count": int(final.get("reply_count", 0)),
            "claim_count": len(branch.get("claims") or []),
            "correction_count": int(final.get("correction_count", 0)),
            "interaction_count": int(final.get("interaction_count", 0)),
            "governance_message_count": int(
                final.get("governance_message_count", 0)
            ),
            "direct_governance_response_count": int(
                uptake_summary.get("direct_resident_response_count", 0)
            ),
            "complete_governance_uptake_chains": sum(
                row.get("complete") is True
                for row in governance_chains
                if isinstance(row, dict)
            ),
            "complete_correction_chains": sum(
                row.get("complete") is True
                for row in correction_chains
                if isinstance(row, dict)
            ),
            "private_conversation_count": int(
                final.get("private_conversation_count", 0)
            ),
            "private_message_count": int(final.get("private_message_count", 0)),
            "private_group_count": int(final.get("private_group_count", 0)),
            "anonymous_public_message_count": int(
                final.get("anonymous_public_message_count", 0)
            ),
            "risk_signals": {
                str(row.get("signal_name")): float(row.get("value", 0.0))
                for row in (final.get("governance_risk", {}).get("signals") or [])
                if isinstance(row, dict) and row.get("signal_name")
            },
            "final_population_state": dict(final.get("population_state") or {}),
        }

    natural = branch_summary("Natural")
    governed = branch_summary("D")
    state_delta = {
        key: round(
            float(governed["final_population_state"].get(key, 0.0))
            - float(natural["final_population_state"].get(key, 0.0)),
            6,
        )
        for key in ("trust", "concern", "satisfaction")
    }
    usage = wrapper.get("live_usage") or {}
    return {
        "result_sha256": result_sha256,
        "start_tick": int(result.get("start_tick", -1)),
        "end_tick": int(result.get("end_tick", -1)),
        "natural": natural,
        "governed": governed,
        "state_delta_D_minus_Natural": state_delta,
        "provider_calls": int(usage.get("provider_calls", 0)),
        "provider_tokens": int(usage.get("provider_tokens", 0)),
        "format_repairs": int(usage.get("format_repairs", 0)),
        "unknown_outcomes": int(usage.get("unknown_outcomes", 0)),
    }


def status(session_id: str) -> dict[str, Any]:
    paths = _paths(session_id)
    if not paths["session_record"].is_file():
        raise ForumLiveVignetteError("live vignette session was not prepared")
    record = _read_json(paths["session_record"])
    record_body = {
        key: value for key, value in record.items() if key != "content_sha256"
    }
    if (
        record.get("schema_version") != SESSION_SCHEMA
        or record.get("session_id") != session_id
        or record.get("content_sha256") != canonical_sha256(record_body)
    ):
        raise ForumLiveVignetteError("live vignette session record is invalid")
    run_scope = str(record.get("run_scope") or QUICK_RUN_SCOPE)
    preflight_path, result_path = _scope_artifacts(paths, run_scope)
    if not preflight_path.is_file():
        raise ForumLiveVignetteError("live vignette preflight is missing")
    preflight = _read_json(preflight_path)
    _validate_preflight(
        preflight,
        scenario_id=str(record["scenario_id"]),
        run_scope=run_scope,
    )
    try:
        config = provider_config_status()
    except ProviderConfigError:
        config = {"configured": False, "model_name": "", "base_url": ""}
    public = _public_preflight(
        session_id=session_id,
        preflight=preflight,
        config=config,
        run_scope=run_scope,
    )
    running = _process_running(session_id, paths)
    manifest_status = "not_started"
    committed_ticks = 0
    latest_tick = None
    latest_branch = None
    stop_code = None
    result_sha256 = None
    if paths["manifest"].is_file():
        manifest = _read_json(paths["manifest"])
        manifest_body = {
            key: value
            for key, value in manifest.items()
            if key != "content_sha256"
        }
        if manifest.get("content_sha256") != canonical_sha256(manifest_body):
            raise ForumLiveVignetteError("public progress manifest hash mismatch")
        manifest_status = str(manifest.get("status") or "running")
        rows = manifest.get("committed_ticks") or []
        committed_ticks = len(rows) if isinstance(rows, list) else 0
        latest = manifest.get("latest_committed") or {}
        latest_tick = latest.get("tick")
        latest_branch = latest.get("branch")
        stop_code = manifest.get("stop_code")
        result_sha256 = manifest.get("result_sha256")
    config_matches = bool(
        config.get("configured")
        and config.get("reasoning_effort") == "none"
        and config.get("model_name") == preflight.get("model")
        and provider_chat_completions_url(str(config.get("base_url")))
        == preflight.get("endpoint")
    )
    unknown_stop = bool(
        stop_code
        and re.search(r"unknown|ambiguous", str(stop_code), flags=re.IGNORECASE)
    )
    unknown_attempt_id = None
    if unknown_stop:
        try:
            unknown_attempt_id = _latest_unknown_attempt_id(paths["ledger"])
        except ForumLiveVignetteError:
            unknown_attempt_id = None
    resolved_unknown_attempt_ids = _resolved_unknown_attempt_ids(paths)
    unknown_resolution_recorded = bool(
        unknown_attempt_id in resolved_unknown_attempt_ids
    )
    unknown_replacement_exhausted = bool(
        unknown_stop
        and resolved_unknown_attempt_ids
        and not unknown_resolution_recorded
    )
    known_failure_stop = bool(
        stop_code
        and re.search(
            r"known.*failure|failure.*known",
            str(stop_code),
            flags=re.IGNORECASE,
        )
    )
    authorization_exists = paths["authorization"].is_file()
    succeeded = manifest_status == "succeeded" and result_path.is_file()
    result_summary = (
        _public_result_summary(
            result_path,
            manifest_result_sha256=(
                str(result_sha256) if result_sha256 is not None else None
            ),
        )
        if succeeded
        else None
    )
    can_resume = bool(
        authorization_exists
        and not running
        and not succeeded
        and (not unknown_stop or unknown_resolution_recorded)
        and not unknown_replacement_exhausted
        and config_matches
    )
    public.update(
        {
            "authorization_recorded": authorization_exists,
            "running": running,
            "manifest_status": manifest_status,
            "committed_ticks": committed_ticks,
            "latest_tick": latest_tick,
            "latest_branch": latest_branch,
            "stop_code": stop_code,
            "known_failure_replacement_required": known_failure_stop,
            "unknown_resolution_required": bool(
                unknown_stop and not unknown_resolution_recorded
            ),
            "unknown_resolution_recorded": unknown_resolution_recorded,
            "unknown_replacement_exhausted": unknown_replacement_exhausted,
            "unknown_attempt_id": unknown_attempt_id,
            "unknown_resolution_statement": UNKNOWN_RESOLUTION_STATEMENT,
            "unknown_resolution_statement_sha256": (
                UNKNOWN_RESOLUTION_STATEMENT_SHA256
            ),
            "result_sha256": result_sha256,
            "succeeded": succeeded,
            "result_summary": result_summary,
            "can_start": bool(
                not authorization_exists
                and not running
                and not paths["ledger"].exists()
                and config_matches
            ),
            "can_resume": can_resume,
            "progress_session_id": session_id,
            "progress_base_url": "http://127.0.0.1:8766",
        }
    )
    return public


def resolve_unknown(
    session_id: str,
    *,
    preflight_sha256: str,
    resolution_statement_sha256: str,
) -> dict[str, Any]:
    """Persist one explicit unknown-abandonment decision without a Provider call."""

    if resolution_statement_sha256 != UNKNOWN_RESOLUTION_STATEMENT_SHA256:
        raise ForumLiveVignetteError(
            "unknown-outcome acknowledgement does not match the displayed contract"
        )
    with _lock:
        current = status(session_id)
        if current["running"] or current["succeeded"]:
            raise ForumLiveVignetteError(
                "unknown outcome cannot be resolved while this run is active or complete"
            )
        if current["preflight_sha256"] != preflight_sha256:
            raise ForumLiveVignetteError(
                "preflight SHA-256 does not match this session"
            )
        if not current.get("unknown_resolution_required"):
            raise ForumLiveVignetteError(
                "this run has no eligible unknown outcome to replace"
            )
        attempt_id = str(current.get("unknown_attempt_id") or "")
        paths = _paths(session_id)
        run_scope = str(current.get("run_scope") or QUICK_RUN_SCOPE)
        preflight_path, _ = _scope_artifacts(paths, run_scope)
        _unknown_resolution_evidence(
            paths=paths,
            preflight_path=preflight_path,
            attempt_id=attempt_id,
        )
        return status(session_id)


def start(
    session_id: str,
    *,
    preflight_sha256: str,
    authorization_statement_sha256: str,
) -> dict[str, Any]:
    """Record one explicit acknowledgement and launch/resume the bound run."""

    if SHA256_PATTERN.fullmatch(preflight_sha256) is None:
        raise ForumLiveVignetteError("preflight SHA-256 is invalid")
    if authorization_statement_sha256 != AUTHORIZATION_STATEMENT_SHA256:
        raise ForumLiveVignetteError("operator acknowledgement does not match the preflight")
    with _lock:
        current = status(session_id)
        if current["running"]:
            return current
        if current["preflight_sha256"] != preflight_sha256:
            raise ForumLiveVignetteError("preflight SHA-256 does not match this session")
        if not current["can_start"] and not current["can_resume"]:
            raise ForumLiveVignetteError(
                "live vignette is complete, blocked by an unknown result, or no longer matches local Provider configuration"
            )
        config = _validated_config()
        paths = _paths(session_id)
        run_scope = str(current.get("run_scope") or QUICK_RUN_SCOPE)
        preflight_path, _ = _scope_artifacts(paths, run_scope)
        if not paths["authorization"].is_file():
            _run_control_command(
                [
                    sys.executable,
                    str(paths["root"] / "scripts" / "attach_forum_hero_authorization.py"),
                    "--preflight",
                    str(preflight_path),
                    "--statement-sha256",
                    AUTHORIZATION_STATEMENT_SHA256,
                    "--output",
                    str(paths["authorization"]),
                ]
            )
        _, key_path = provider_config_paths()
        if current.get("known_failure_replacement_required"):
            _known_failure_replacement_evidence(
                paths=paths,
                preflight_path=preflight_path,
            )
        replacement_arguments = [
            item
            for evidence_path in sorted(
                paths["control"].glob("known-failure-replacement-ylva_*.json")
            )
            for item in (
                "--manual-known-failure-replacement-evidence",
                str(evidence_path),
            )
        ]
        unknown_resolution_arguments = [
            item
            for evidence_path in _unknown_resolution_files(paths)
            for item in (
                "--manual-unknown-resolution-evidence",
                str(evidence_path),
            )
        ]
        if run_scope == QUICK_RUN_SCOPE:
            command = [
                sys.executable,
                str(paths["root"] / "scripts" / "run_forum_hero_authorized.py"),
                "--preflight",
                str(preflight_path),
                "--authorization",
                str(paths["authorization"]),
                "--population",
                str(paths["population"]),
                "--output-dir",
                str(paths["session"]),
                "--public-progress-dir",
                str(paths["session"] / "public-progress"),
                "--api-key-file",
                str(key_path),
                "--scenario-id",
                str(current["scenario_id"]),
                "--end-tick",
                "6",
                "--timeout-seconds",
                str(config["timeout_seconds"]),
                "--controlled-repair-max-per-slot",
                "1",
                *replacement_arguments,
                *unknown_resolution_arguments,
                "--execute",
            ]
        else:
            command = [
                sys.executable,
                str(
                    paths["root"]
                    / "scripts"
                    / "run_forum_v4_resource_hero_authorized.py"
                ),
                "--preflight",
                str(preflight_path),
                "--authorization",
                str(paths["authorization"]),
                "--population",
                str(paths["population"]),
                "--relationships",
                str(paths["relationships"]),
                "--stakes",
                str(paths["stakes"]),
                "--output-dir",
                str(paths["session"]),
                "--public-progress-dir",
                str(paths["session"] / "public-progress"),
                "--api-key-file",
                str(key_path),
                "--timeout-seconds",
                str(config["timeout_seconds"]),
                *replacement_arguments,
                *unknown_resolution_arguments,
                "--execute",
            ]
        paths["session"].mkdir(parents=True, exist_ok=True)
        log_handle = paths["log"].open("ab")
        try:
            process = subprocess.Popen(
                command,
                cwd=paths["root"],
                env=_subprocess_environment(),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
        except OSError:
            log_handle.close()
            raise ForumLiveVignetteError(
                "authorized ForumTwin runner could not start"
            )
        _processes[session_id] = process
        _log_handles[session_id] = log_handle
        process_record = {
            "schema_version": "campus-pulse-forum-live-vignette-process-v1",
            "session_id": session_id,
            "preflight_sha256": preflight_sha256,
            "run_scope": run_scope,
            "pid": process.pid,
            "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "contains_secret": False,
            "contains_prompt_or_provider_body": False,
        }
        process_record["content_sha256"] = canonical_sha256(process_record)
        _write_json(paths["process_record"], process_record)
        return status(session_id)
