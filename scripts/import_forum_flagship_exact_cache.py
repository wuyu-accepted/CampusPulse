#!/usr/bin/env python3
"""Import only exact, primary successful ForumTwin cache entries into a new run.

The source ledger is opened read-only.  Failed, repaired, unknown and in-flight
attempts are never copied.  Cache keys are rebound to the target immutable
preflight/run identity, while response fingerprints and validated structured
content stay byte-identical.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
RUNNER_PATH = ROOT / "scripts" / "run_forum_resource_flagship_authorized.py"
SPEC = importlib.util.spec_from_file_location("_resource_flagship_runner", RUNNER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("resource flagship runner import failed")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

from backend.campus_pulse.yulan_scale_live_runtime import (  # noqa: E402
    SQLiteV7AuthorizedTurnRepository,
)


SCHEMA = "campus-pulse-resource-flagship-exact-cache-import-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _source_rows(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        control = connection.execute(
            "SELECT * FROM v7_live_control WHERE singleton = 1"
        ).fetchone()
        if control is None:
            raise ValueError("source live ledger control is missing")
        if int(control["provider_calls_reserved"]) or int(control["tokens_in_flight"]):
            raise ValueError("source live ledger still has an in-flight request")
        binding = json.loads(str(control["binding_json"]))
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT c.*
                FROM v7_live_cache AS c
                JOIN v7_live_attempts AS a ON a.cache_key = c.cache_key
                WHERE a.state = 'settled'
                  AND a.outcome = 'success'
                  AND a.attempt_ordinal = 0
                ORDER BY c.cache_key
                """
            ).fetchall()
        ]
        all_success_cache_count = int(
            connection.execute("SELECT COUNT(*) FROM v7_live_cache").fetchone()[0]
        )
    finally:
        connection.close()
    return binding, rows, all_success_cache_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-ledger", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--population", type=Path, default=runner.DEFAULT_POPULATION)
    parser.add_argument("--target-output-dir", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    arguments = parser.parse_args()

    population_path = arguments.population.resolve()
    population = runner._load(population_path)
    runner._validate_population(population)
    preflight = runner._load(arguments.preflight.resolve())
    runner._validate_preflight(
        preflight, population_path=population_path, population=population
    )
    authorization = runner._load(arguments.authorization.resolve())
    authorization_fingerprint = runner._validate_authorization(
        authorization, preflight=preflight
    )
    context, binding, _endpoint = runner._identities(
        preflight=preflight,
        authorization_fingerprint=authorization_fingerprint,
        timeout_seconds=arguments.timeout_seconds,
    )

    source_binding, source_rows, source_cache_count = _source_rows(
        arguments.source_ledger
    )
    if (
        source_binding.get("model_name") != context.model_name
        or source_binding.get("endpoint_fingerprint") != context.endpoint_fingerprint
        or source_binding.get("transport_config_fingerprint")
        != binding.transport_config_fingerprint
    ):
        raise ValueError("source cache transport/model binding differs from target")

    rebound_rows: list[dict[str, Any]] = []
    release_rows: list[dict[str, Any]] = []
    for row in source_rows:
        for key in ("prompt_sha256", "request_sha256", "response_sha256"):
            if _SHA256.fullmatch(str(row.get(key) or "")) is None:
                raise ValueError("source cache fingerprint is invalid")
        if row.get("slot_kind") not in {"resident", "governance_actor"}:
            raise ValueError("source cache slot kind is invalid")
        content = json.loads(str(row["content_json"]))
        validated = json.loads(str(row["validated_decision_json"]))
        if not isinstance(content, dict) or not isinstance(validated, dict):
            raise ValueError("source cache structured result is invalid")
        cache_identity = {
            "namespace": "authorized_live_llm",
            "run_id": context.run_id,
            "plan_sha256": context.plan_sha256,
            "transport_config_fingerprint": binding.transport_config_fingerprint,
            "model_name": context.model_name,
            "slot_kind": row["slot_kind"],
            "prompt_sha256": row["prompt_sha256"],
            "request_sha256": row["request_sha256"],
            "attempt_ordinal": 0,
        }
        target_cache_key = runner.canonical_sha256(cache_identity)
        rebound_rows.append({**row, "cache_key": target_cache_key})
        release_rows.append(
            {
                "slot_kind": row["slot_kind"],
                "prompt_sha256": row["prompt_sha256"],
                "request_sha256": row["request_sha256"],
                "response_sha256": row["response_sha256"],
                "content_sha256": hashlib.sha256(
                    str(row["content_json"]).encode("utf-8")
                ).hexdigest(),
                "validated_decision_sha256": hashlib.sha256(
                    str(row["validated_decision_json"]).encode("utf-8")
                ).hexdigest(),
            }
        )

    source_release_sha256 = runner.canonical_sha256(release_rows)
    body = {
        "schema_version": SCHEMA,
        "source_run_id": source_binding.get("run_id"),
        "source_preflight_sha256": source_binding.get("preflight_sha256"),
        "source_authorization_fingerprint": source_binding.get(
            "authorization_fingerprint"
        ),
        "source_exact_primary_cache_release_sha256": source_release_sha256,
        "target_run_id": context.run_id,
        "target_preflight_sha256": binding.preflight_sha256,
        "target_authorization_fingerprint": binding.authorization_fingerprint,
        "imported_primary_success_count": len(rebound_rows),
        "skipped_nonprimary_or_repaired_success_count": (
            source_cache_count - len(rebound_rows)
        ),
        "source_prompt_or_response_bodies_read": False,
        "validated_structured_results_only": True,
        "unknown_or_failed_attempts_imported": 0,
    }
    evidence = {**body, "evidence_sha256": runner.canonical_sha256(body)}

    output_dir = arguments.target_output_dir.resolve()
    ledger_path = output_dir / "authorized-live-ledger.sqlite3"
    repository = SQLiteV7AuthorizedTurnRepository(
        ledger_path,
        binding=binding,
        context=context,
        initialize=not ledger_path.exists(),
    )
    with repository._connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            attempt_count = int(
                connection.execute("SELECT COUNT(*) FROM v7_live_attempts").fetchone()[0]
            )
            if attempt_count:
                raise ValueError("target ledger already contains Provider attempts")
            for row in rebound_rows:
                values = (
                    row["cache_key"],
                    row["slot_kind"],
                    row["prompt_sha256"],
                    row["request_sha256"],
                    row["response_sha256"],
                    row["returned_model"],
                    int(row["prompt_tokens"]),
                    int(row["completion_tokens"]),
                    int(row["total_tokens"]),
                    row["content_json"],
                    row["validated_decision_json"],
                )
                existing = connection.execute(
                    "SELECT * FROM v7_live_cache WHERE cache_key = ?",
                    (row["cache_key"],),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        "INSERT INTO v7_live_cache VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        values,
                    )
                elif tuple(existing) != values:
                    raise ValueError("target exact-cache import identity drifted")
            event_exists = connection.execute(
                "SELECT 1 FROM v7_live_events WHERE event_kind = ? AND event_sha256 = ?",
                (
                    "exact_primary_cache_imported",
                    runner.canonical_sha256(
                        {
                            "event_kind": "exact_primary_cache_imported",
                            "attempt_id": None,
                            "evidence_sha256": evidence["evidence_sha256"],
                            "imported_primary_success_count": len(rebound_rows),
                        }
                    ),
                ),
            ).fetchone()
            if event_exists is None:
                repository._event(
                    connection,
                    "exact_primary_cache_imported",
                    None,
                    {
                        "evidence_sha256": evidence["evidence_sha256"],
                        "imported_primary_success_count": len(rebound_rows),
                    },
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise

    runner._write_json(output_dir / "exact-cache-import-v1.json", evidence)
    print(
        json.dumps(
            {
                "target_run_id": context.run_id,
                "imported_primary_success_count": len(rebound_rows),
                "skipped_nonprimary_or_repaired_success_count": (
                    source_cache_count - len(rebound_rows)
                ),
                "evidence_sha256": evidence["evidence_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
