"""Local operator control for the fixed Tick 3--10 Century Gym demo.

The API never accepts or returns credentials, prompts, or provider bodies.  It
starts only the reviewed full-run contract bundled with this checkout and reports
process state for the local competition console.
"""

from __future__ import annotations

import json
from pathlib import Path
import os
import socket
import subprocess
import sys
import threading
import re
import sqlite3
import hashlib
from datetime import datetime, timezone
from contextlib import closing
from typing import Any

from backend.campus_pulse.provider_config import (
    ProviderConfigError,
    provider_config_paths,
    provider_config_status,
)
from backend.campus_pulse.runtime_contract import canonical_sha256


SESSION_ID = "century-gym-full"
EXPECTED_PREFLIGHT_SHA256 = (
    "52db468d5eec0ec99d61b70e74ef94d25c8340bf027e93d6297469e9e60d132b"
)
EXPECTED_MODEL = "gpt-5.6-luna"
EXPECTED_BASE_URL = "https://yunwu.ai/v1"
SOCIAL_WORLD_SCHEMA = "campus-pulse-century-gym-social-world-v1"
_ROLE_ID = re.compile(r"^archetype-(?:0[1-9]|1[0-7])$")
_SOCIAL_WORLD_BRANCHES = {"shared_baseline", "Natural", "D"}

_lock = threading.Lock()
_process: subprocess.Popen | None = None
_log_handle = None


class CenturyGymDemoError(ValueError):
    """Raised when the fixed demo cannot be started safely."""


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def _paths() -> dict[str, Path]:
    root = _root()
    frozen = root / "evidence" / "century-gym-live-v1" / "frozen-runtime"
    evidence = root / "evidence" / "century-gym-live-v1"
    session = root / "_run" / "forum-twin-live-sessions" / SESSION_ID
    return {
        "root": root,
        "preflight": evidence / "preflight-full-T3-T10.json",
        "authorization": evidence / "authorization-full-T3-T10.json",
        "population": evidence / "semantic-forum-pilot-profile-bridge-v5.json",
        "session": session,
        "manifest": session / "public-progress" / "latest.json",
        "ledger": session / "authorized-live-ledger.sqlite3",
        "log": session / "operator-run.log",
        "frozen": frozen,
        "demo_manifest": root
        / "evidence"
        / "century-gym-demo-v2"
        / "public-progress"
        / "latest.json",
        "social_world": root
        / "evidence"
        / "century-gym-demo-v2"
        / "social-world-v1.json",
    }


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CenturyGymDemoError(f"required demo artifact is unreadable: {path.name}") from exc
    if not isinstance(value, dict):
        raise CenturyGymDemoError(f"required demo artifact is invalid: {path.name}")
    return value


def replay_workspace() -> dict[str, Any]:
    return {"demo": os.environ.get("CAMPUS_PULSE_DEMO_WORKSPACE") == "1",
            "execution_source": "recorded_trace", "new_provider_calls": 0}


def _replay_db() -> Path:
    # Replay cursors are isolated from both experiment ledgers and project rows.
    workspace = Path(os.environ.get("CAMPUS_PULSE_WORKBENCH_DB", str(
        _root() / "data" / "campus_pulse_workspace" / "workbench.sqlite3")))
    return workspace.with_name(workspace.stem + "-replay-sessions.sqlite3")


def _replay_connection():
    path = _replay_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.execute("CREATE TABLE IF NOT EXISTS replay_sessions (project_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    return connection


def replay_session(project_id: str, *, start: bool = False,
                   progress: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Persist a playback cursor, never a simulated result or Provider attempt."""
    source = _paths()["demo_manifest"]
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = _load_json(source)
    if manifest.get("status") != "succeeded":
        raise CenturyGymDemoError("Saved run is incomplete")
    with _lock, closing(_replay_connection()) as connection, connection:
        row = connection.execute("SELECT payload FROM replay_sessions WHERE project_id=?", (project_id,)).fetchone()
        result = json.loads(row[0]) if row else None
        if result and result["source_sha256"] != source_hash:
            raise CenturyGymDemoError("Saved run changed; playback session cannot continue")
        if result is None and start:
            result = {"project_id": project_id, "session_id": "century-gym-demo",
                      "execution_source": "recorded_trace", "source_sha256": source_hash,
                      "status": "paused", "tick": 0, "branch": "D", "revision": 0,
                      "new_provider_calls": 0, "created_at": datetime.now(timezone.utc).isoformat()}
        if result is None:
            return None
        if progress is not None:
            if progress["revision"] != result["revision"]:
                raise CenturyGymDemoError("Playback cursor changed; reload its current state")
            tick, branch = progress["tick"], progress["branch"]
            if progress["status"] == "completed" and tick != 10:
                raise CenturyGymDemoError("Playback completes at time step 10")
            if not any(e["tick"] == tick and e["branch"] in (branch, "shared_baseline")
                       for e in manifest["committed_ticks"]):
                raise CenturyGymDemoError("Time step is absent from the saved run")
            result.update(progress)
            result["revision"] += 1
        connection.execute("INSERT INTO replay_sessions VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET payload=excluded.payload",
                           (project_id, json.dumps(result, ensure_ascii=False)))
        return result


def _validate_contract() -> tuple[dict[str, Any], dict[str, Path]]:
    paths = _paths()
    preflight = _load_json(paths["preflight"])
    authorization = _load_json(paths["authorization"])
    if preflight.get("preflight_sha256") != EXPECTED_PREFLIGHT_SHA256:
        raise CenturyGymDemoError("Century Gym full-run preflight hash does not match the reviewed release")
    if authorization.get("preflight_sha256") != EXPECTED_PREFLIGHT_SHA256:
        raise CenturyGymDemoError("Century Gym authorization does not match the reviewed preflight")
    if preflight.get("primary_slots") != 230 or preflight.get("ticks") != list(range(3, 11)):
        raise CenturyGymDemoError(
            "Century Gym scope is not the fixed 230-slot Tick 3--10 contract"
        )
    if preflight.get("model") != EXPECTED_MODEL:
        raise CenturyGymDemoError("Century Gym model differs from the reviewed contract")
    if not paths["population"].is_file():
        raise CenturyGymDemoError("Century Gym reviewed population is missing")
    frozen_runner = paths["frozen"] / "scripts" / "run_forum_hero_authorized.py"
    if not frozen_runner.is_file():
        raise CenturyGymDemoError("Century Gym frozen runtime snapshot is missing")
    config = provider_config_status()
    if not config["configured"]:
        raise CenturyGymDemoError("请先在“系统设置 → 模型与 API”保存本机配置")
    if config["base_url"] != EXPECTED_BASE_URL or config["model_name"] != EXPECTED_MODEL:
        raise CenturyGymDemoError(
            "世纪馆完整运行固定使用 https://yunwu.ai/v1 与 gpt-5.6-luna，请先更新本机配置"
        )
    if config["reasoning_effort"] != "none":
        raise CenturyGymDemoError("世纪馆完整运行要求 reasoning effort 为 none")
    return config, paths


def status() -> dict[str, Any]:
    global _log_handle
    paths = _paths()
    process = _process
    manifest_status = "not_started"
    committed_ticks = 0
    latest_tick = None
    stop_code = None
    if paths["manifest"].is_file():
        try:
            manifest = _load_json(paths["manifest"])
            manifest_status = str(manifest.get("status") or "running")
            committed_ticks = len(manifest.get("committed_ticks") or [])
            latest = manifest.get("latest_committed") or {}
            latest_tick = latest.get("tick")
            stop_code = manifest.get("stop_code")
        except CenturyGymDemoError:
            manifest_status = "invalid_progress"
    running = process is not None and process.poll() is None
    exit_code = None if process is None or running else process.returncode
    if not running and _log_handle is not None:
        _log_handle.close()
        _log_handle = None
    try:
        config = provider_config_status()
    except ProviderConfigError:
        config = {"configured": False, "model_name": "", "base_url": ""}
    return {
        "schema_version": "campus-pulse-century-gym-demo-status-v1",
        "session_id": SESSION_ID,
        "scope": "完整演化 · Tick 3–10 · 224 个居民槽位 + 6 个治理槽位",
        "provider_configured": bool(config.get("configured")),
        "provider_model": config.get("model_name", ""),
        "running": running,
        "manifest_status": manifest_status,
        "committed_ticks": committed_ticks,
        "latest_tick": latest_tick,
        "stop_code": stop_code,
        "exit_code": exit_code,
        "can_start": not running and not paths["manifest"].is_file(),
        "contains_secret": False,
    }


def social_world() -> dict[str, Any]:
    """Return the hash-bound, aggregate-only Century Gym social-world overlay.

    The overlay adds reviewed synthetic private-chat excerpts for product
    demonstration.  It is deliberately separate from the authorized LLM run,
    does not alter any old result hash, and contains neither record-level
    relationships nor private runtime identifiers.
    """

    paths = _paths()
    value = _load_json(paths["social_world"])
    manifest = _load_json(paths["demo_manifest"])
    expected = value.get("content_sha256")
    actual = canonical_sha256(
        {key: child for key, child in value.items() if key != "content_sha256"}
    )
    if expected != actual:
        raise CenturyGymDemoError("Century Gym social-world content hash mismatch")
    if value.get("schema_version") != SOCIAL_WORLD_SCHEMA:
        raise CenturyGymDemoError("Century Gym social-world schema is invalid")
    if value.get("scenario_id") != "century_gym_ghost_booking_dispute":
        raise CenturyGymDemoError("Century Gym social-world scenario is invalid")
    if value.get("source_manifest_sha256") != manifest.get("content_sha256"):
        raise CenturyGymDemoError("Century Gym social-world source binding is invalid")

    provenance = value.get("provenance")
    privacy = value.get("privacy")
    if not isinstance(provenance, dict) or (
        provenance.get("kind") != "reviewed_synthetic_mechanism_overlay"
        or provenance.get("provider_calls") != 0
    ):
        raise CenturyGymDemoError("Century Gym social-world provenance is invalid")
    if not isinstance(privacy, dict) or any(
        privacy.get(key) is not expected_value
        for key, expected_value in {
            "public_safe": True,
            "reviewed_excerpts_only": True,
            "contains_agent_ids": False,
            "contains_conversation_ids": False,
            "contains_record_level_relationship_edges": False,
            "contains_raw_private_runtime_text": False,
        }.items()
    ):
        raise CenturyGymDemoError("Century Gym social-world privacy contract is invalid")

    frames = value.get("frames")
    excerpts = value.get("reviewed_excerpts")
    if not isinstance(frames, list) or not isinstance(excerpts, list):
        raise CenturyGymDemoError("Century Gym social-world collections are invalid")
    seen_frames: set[tuple[str, int]] = set()
    excerpt_ids: set[str] = set()
    for excerpt in excerpts:
        if not isinstance(excerpt, dict):
            raise CenturyGymDemoError("Century Gym reviewed excerpt is invalid")
        excerpt_id = excerpt.get("excerpt_id")
        branch = excerpt.get("branch")
        tick = excerpt.get("tick")
        if (
            not isinstance(excerpt_id, str)
            or not excerpt_id.startswith("reviewed-excerpt-")
            or excerpt_id in excerpt_ids
            or branch not in _SOCIAL_WORLD_BRANCHES
            or not isinstance(tick, int)
            or not 0 <= tick <= 10
            or excerpt.get("review_status") != "approved"
            or excerpt.get("provenance_kind")
            != "human_reviewed_synthetic_mechanism_excerpt"
            or not _ROLE_ID.fullmatch(str(excerpt.get("source_role_id") or ""))
            or not _ROLE_ID.fullmatch(str(excerpt.get("target_role_id") or ""))
            or not isinstance(excerpt.get("text_zh"), str)
            or not isinstance(excerpt.get("text_en"), str)
            or not isinstance(excerpt.get("effect_zh"), str)
            or not isinstance(excerpt.get("effect_en"), str)
            or not str(excerpt.get("public_source_message_id") or "").startswith(
                ("forum-message-", "forum-governance-message-")
            )
        ):
            raise CenturyGymDemoError("Century Gym reviewed excerpt contract is invalid")
        excerpt_ids.add(excerpt_id)

    for frame in frames:
        if not isinstance(frame, dict):
            raise CenturyGymDemoError("Century Gym social-world frame is invalid")
        branch = frame.get("branch")
        tick = frame.get("tick")
        key = (str(branch), int(tick) if isinstance(tick, int) else -1)
        if (
            branch not in _SOCIAL_WORLD_BRANCHES
            or not isinstance(tick, int)
            or not 0 <= tick <= 10
            or key in seen_frames
            or not isinstance(frame.get("edges"), list)
        ):
            raise CenturyGymDemoError("Century Gym social-world frame cursor is invalid")
        seen_frames.add(key)
        for edge in frame["edges"]:
            if (
                not isinstance(edge, dict)
                or not _ROLE_ID.fullmatch(str(edge.get("source_role_id") or ""))
                or not _ROLE_ID.fullmatch(str(edge.get("target_role_id") or ""))
                or edge.get("channel") not in {"private_direct", "private_group"}
                or edge.get("relation_type")
                not in {"close_friend", "familiar_peer", "cross_group_acquaintance"}
                or not isinstance(edge.get("message_count"), int)
                or edge["message_count"] < 1
                or not isinstance(edge.get("excerpt_ids"), list)
                or any(item not in excerpt_ids for item in edge["excerpt_ids"])
            ):
                raise CenturyGymDemoError("Century Gym social-world edge is invalid")
    return value


def start() -> dict[str, Any]:
    global _process, _log_handle
    with _lock:
        current = status()
        if current["running"]:
            return current
        config, paths = _validate_contract()
        if paths["manifest"].is_file() or paths["ledger"].is_file():
            raise CenturyGymDemoError(
                "本次单次授权已有运行记录；请回放现有结果，不会重复发送 LLM 请求"
            )
        paths["session"].mkdir(parents=True, exist_ok=True)
        _, key_path = provider_config_paths()
        command = [
            sys.executable,
            str(paths["frozen"] / "scripts" / "run_forum_hero_authorized.py"),
            "--preflight", str(paths["preflight"]),
            "--authorization", str(paths["authorization"]),
            "--population", str(paths["population"]),
            "--output-dir", str(paths["session"]),
            "--public-progress-dir", str(paths["session"] / "public-progress"),
            "--api-key-file", str(key_path),
            "--scenario-id", "century_gym_ghost_booking_dispute",
            "--end-tick", "10",
            "--timeout-seconds", str(config["timeout_seconds"]),
            "--execute",
        ]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = os.pathsep.join(
            (
                str(paths["frozen"]),
                str(paths["frozen"] / "src"),
                str(paths["root"]),
                str(paths["root"] / "src"),
            )
        )
        if not environment.get("HTTPS_PROXY"):
            # Use the developer's local proxy when it is actually listening,
            # otherwise let the Provider transport connect directly.  A
            # teammate must never inherit a dead machine-specific proxy.
            try:
                with socket.create_connection(("127.0.0.1", 7897), timeout=0.08):
                    environment["HTTPS_PROXY"] = "http://127.0.0.1:7897"
                    environment["HTTP_PROXY"] = "http://127.0.0.1:7897"
            except OSError:
                pass
        _log_handle = paths["log"].open("ab")
        _process = subprocess.Popen(
            command,
            cwd=paths["root"],
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=_log_handle,
            stderr=subprocess.STDOUT,
        )
        return status()
