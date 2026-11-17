"""Record body-free timing telemetry for an authorized Century Gym live run.

The monitor never reads prompts, responses, cache content, agent identities, or
credentials.  It samples only ledger counters and the public tick manifest so
an interrupted run retains an auditable wall-clock timeline.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Any


TERMINAL_STATUSES = {"succeeded", "paused_fail_closed"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(payload + "\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def load_manifest(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def ledger_snapshot(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        uri = f"file:{path.as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=1.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        control = connection.execute(
            """
            SELECT authorization_consumed,authorization_active,lease_active,
                   provider_profile_enabled,cancellation_requested,
                   provider_calls_reserved,provider_calls_completed,
                   provider_calls_unknown,tokens_in_flight,
                   provider_reported_tokens,risk_charged_tokens,
                   next_request_occurrence
              FROM v7_live_control WHERE singleton = 1
            """
        ).fetchone()
        attempts = connection.execute(
            """
            SELECT slot_kind,state,COALESCE(outcome,'pending') AS outcome,
                   COUNT(*) AS count
              FROM v7_live_attempts
          GROUP BY slot_kind,state,COALESCE(outcome,'pending')
          ORDER BY slot_kind,state,outcome
            """
        ).fetchall()
        cache = connection.execute(
            """
            SELECT slot_kind,COUNT(*) AS count,
                   COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens),0) AS completion_tokens,
                   COALESCE(SUM(total_tokens),0) AS total_tokens
              FROM v7_live_cache GROUP BY slot_kind ORDER BY slot_kind
            """
        ).fetchall()
        events = connection.execute(
            """
            SELECT event_kind,COUNT(*) AS count
              FROM v7_live_events GROUP BY event_kind ORDER BY event_kind
            """
        ).fetchall()
        connection.close()
    except (OSError, sqlite3.Error):
        return None
    attempt_rows = [dict(row) for row in attempts]
    cache_rows = [dict(row) for row in cache]
    return {
        "control": dict(control) if control is not None else None,
        "attempts": attempt_rows,
        "attempt_total": sum(int(row["count"]) for row in attempt_rows),
        "cache": cache_rows,
        "cache_total": sum(int(row["count"]) for row in cache_rows),
        "events": [dict(row) for row in events],
        "ledger_bytes": path.stat().st_size,
        "wal_bytes": (
            Path(str(path) + "-wal").stat().st_size
            if Path(str(path) + "-wal").is_file()
            else 0
        ),
    }


def public_snapshot(manifest: dict[str, Any] | None) -> dict[str, Any] | None:
    if manifest is None:
        return None
    return {
        "preflight_sha256": manifest.get("preflight_sha256"),
        "run_id": manifest.get("run_id"),
        "scenario_id": manifest.get("scenario_id"),
        "status": manifest.get("status"),
        "stop_code": manifest.get("stop_code"),
        "result_sha256": manifest.get("result_sha256"),
        "committed_ticks": [
            {
                "branch": row.get("branch"),
                "tick": row.get("tick"),
                "content_sha256": row.get("content_sha256"),
            }
            for row in manifest.get("committed_ticks", [])
            if isinstance(row, dict)
        ],
    }


def signature(snapshot: dict[str, Any]) -> str:
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-dir", type=Path, required=True)
    parser.add_argument("--public-progress-dir", type=Path, required=True)
    parser.add_argument("--expected-preflight", required=True)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--heartbeat-seconds", type=float, default=15.0)
    parser.add_argument("--max-seconds", type=float, default=14400.0)
    arguments = parser.parse_args()

    session_dir = arguments.session_dir.resolve()
    progress_dir = arguments.public_progress_dir.resolve()
    monitor_dir = session_dir / "monitoring"
    monitor_dir.mkdir(parents=True, exist_ok=True)
    started_epoch = time.time()
    started_at = utc_now()
    monitor_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    timeline_path = monitor_dir / f"timing-snapshots-{monitor_id}.jsonl"
    summary_path = monitor_dir / "timing-summary.json"
    ledger_path = session_dir / "authorized-live-ledger.sqlite3"
    manifest_path = progress_dir / "latest.json"
    milestones: dict[str, Any] = {"monitor_started": {"at": started_at, "elapsed_seconds": 0.0}}
    tick_timings: dict[str, dict[str, Any]] = {}
    previous_tick_elapsed = 0.0
    previous_signature = ""
    last_write_epoch = 0.0
    terminal_status: str | None = None

    while True:
        now_epoch = time.time()
        elapsed = round(now_epoch - started_epoch, 3)
        ledger = ledger_snapshot(ledger_path)
        manifest = public_snapshot(load_manifest(manifest_path))
        snapshot = {
            "observed_at": utc_now(),
            "elapsed_seconds": elapsed,
            "ledger": ledger,
            "public_progress": manifest,
        }

        if ledger is not None and "ledger_created" not in milestones:
            milestones["ledger_created"] = {
                "at": snapshot["observed_at"],
                "elapsed_seconds": elapsed,
            }
        if ledger is not None:
            cache_by_kind = {
                str(row["slot_kind"]): int(row["count"])
                for row in ledger.get("cache", [])
            }
            if ledger.get("attempt_total", 0) and "first_attempt" not in milestones:
                milestones["first_attempt"] = {
                    "at": snapshot["observed_at"],
                    "elapsed_seconds": elapsed,
                }
            if cache_by_kind.get("resident", 0) and "first_resident_settled" not in milestones:
                milestones["first_resident_settled"] = {
                    "at": snapshot["observed_at"],
                    "elapsed_seconds": elapsed,
                }
            if cache_by_kind.get("governance_actor", 0) and "first_governance_settled" not in milestones:
                milestones["first_governance_settled"] = {
                    "at": snapshot["observed_at"],
                    "elapsed_seconds": elapsed,
                }

        if manifest is not None:
            if manifest.get("preflight_sha256") != arguments.expected_preflight:
                raise RuntimeError("public progress belongs to another preflight")
            for row in manifest.get("committed_ticks", []):
                key = f"{row['branch']}:T{int(row['tick']):02d}"
                if key in tick_timings:
                    continue
                tick_timings[key] = {
                    "branch": row["branch"],
                    "tick": int(row["tick"]),
                    "first_observed_at": snapshot["observed_at"],
                    "elapsed_seconds": elapsed,
                    "seconds_since_previous_commit": round(elapsed - previous_tick_elapsed, 3),
                    "ledger_counters": ledger,
                    "content_sha256": row.get("content_sha256"),
                }
                previous_tick_elapsed = elapsed
            if manifest.get("status") in TERMINAL_STATUSES:
                terminal_status = str(manifest["status"])
                milestones["terminal"] = {
                    "at": snapshot["observed_at"],
                    "elapsed_seconds": elapsed,
                    "status": terminal_status,
                    "stop_code": manifest.get("stop_code"),
                }

        current_signature = signature(snapshot)
        should_write = (
            current_signature != previous_signature
            or now_epoch - last_write_epoch >= arguments.heartbeat_seconds
            or terminal_status is not None
        )
        if should_write:
            append_jsonl(timeline_path, snapshot)
            summary = {
                "schema_version": "campus-pulse-century-gym-timing-v1",
                "preflight_sha256": arguments.expected_preflight,
                "monitor_id": monitor_id,
                "monitor_started_at": started_at,
                "updated_at": snapshot["observed_at"],
                "elapsed_seconds": elapsed,
                "status": terminal_status or "monitoring",
                "milestones": milestones,
                "tick_timings": sorted(
                    tick_timings.values(),
                    key=lambda row: (row["elapsed_seconds"], row["branch"]),
                ),
                "latest": snapshot,
                "timeline_file": timeline_path.name,
                "contains_prompt_or_provider_body": False,
                "contains_credentials": False,
            }
            write_json_atomic(summary_path, summary)
            previous_signature = current_signature
            last_write_epoch = now_epoch
            print(
                json.dumps(
                    {
                        "elapsed_seconds": elapsed,
                        "attempts": ledger.get("attempt_total", 0) if ledger else 0,
                        "settled_slots": ledger.get("cache_total", 0) if ledger else 0,
                        "committed_ticks": len(manifest.get("committed_ticks", [])) if manifest else 0,
                        "status": terminal_status or (manifest.get("status") if manifest else "waiting"),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                flush=True,
            )
        if terminal_status is not None:
            return 0
        if elapsed >= arguments.max_seconds:
            milestones["monitor_timeout"] = {
                "at": snapshot["observed_at"],
                "elapsed_seconds": elapsed,
            }
            summary["status"] = "monitor_timeout"
            summary["milestones"] = milestones
            write_json_atomic(summary_path, summary)
            return 2
        time.sleep(max(0.25, arguments.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
