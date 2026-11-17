#!/usr/bin/env python3
"""Rebind settled exact v4 cache rows after a hash-bound local code repair."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (str(SRC), str(SCRIPTS)):
    if path not in sys.path:
        sys.path.insert(0, path)

import run_forum_v4_resource_hero_authorized as runner
from backend.campus_pulse.yulan_scale_live_runtime import (
    SQLiteV7AuthorizedTurnRepository,
)

SCHEMA = "campus-pulse-forum-v4-exact-cache-rebind-v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _load_unknown_resolution(path: Path) -> dict[str, Any]:
    evidence = runner._load(path.resolve())
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
        evidence_sha != runner.canonical_sha256(evidence)
        or evidence.get("schema_version")
        != "campus-pulse-forum-hero-unknown-outcome-resolution-v1"
        or evidence.get("original_outcome") != "unknown"
        or evidence.get("resolution") != "abandon_and_replace"
        or evidence.get("replacement_limit") != 1
        or evidence.get("automatic_network_retry_limit") != 0
        or evidence.get("late_provider_result_integration_forbidden") is not True
        or replacement_fingerprint
        != runner.canonical_sha256(authorization_material)
    ):
        raise ValueError("source unknown-resolution evidence is invalid")
    return {**evidence, "evidence_sha256": evidence_sha}


def _source_rows(
    path: Path,
    *,
    unknown_resolution: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        control = connection.execute(
            "SELECT * FROM v7_live_control WHERE singleton = 1"
        ).fetchone()
        if control is None:
            raise ValueError("source ledger control is missing")
        if int(control["provider_calls_reserved"]) or int(control["tokens_in_flight"]):
            raise ValueError("source ledger still contains in-flight requests")
        binding = json.loads(str(control["binding_json"]))
        unknown_count = int(control["provider_calls_unknown"])
        if unknown_count:
            if unknown_count != 1 or unknown_resolution is None:
                raise ValueError("source ledger contains an unresolved unknown outcome")
            unknown = connection.execute(
                """
                SELECT attempt_id, prompt_sha256, request_sha256, settlement_json
                FROM v7_live_attempts
                WHERE state = 'settled' AND outcome = 'unknown'
                """
            ).fetchone()
            if unknown is None:
                raise ValueError("source unknown attempt is missing")
            settlement = json.loads(str(unknown["settlement_json"]))
            if (
                unknown_resolution.get("run_id") != binding.get("run_id")
                or unknown_resolution.get("preflight_sha256")
                != binding.get("preflight_sha256")
                or unknown_resolution.get("failed_attempt_id")
                != unknown["attempt_id"]
                or unknown_resolution.get("prompt_sha256")
                != unknown["prompt_sha256"]
                or unknown_resolution.get("request_sha256")
                != unknown["request_sha256"]
                or int(unknown_resolution.get("risk_charged_tokens", -1))
                != int(settlement.get("risk_charged_tokens", -2))
            ):
                raise ValueError("source unknown-resolution evidence does not match ledger")
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT c.*, a.attempt_ordinal
                FROM v7_live_cache AS c
                JOIN v7_live_attempts AS a ON a.cache_key = c.cache_key
                WHERE a.state = 'settled' AND a.outcome = 'success'
                ORDER BY c.cache_key
                """
            ).fetchall()
        ]
    finally:
        connection.close()
    return binding, rows, unknown_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-ledger", type=Path, required=True)
    parser.add_argument("--source-unknown-resolution-evidence", type=Path)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--population", type=Path, default=runner.DEFAULT_POPULATION)
    parser.add_argument("--relationships", type=Path, default=runner.DEFAULT_RELATIONSHIPS)
    parser.add_argument("--stakes", type=Path, default=runner.DEFAULT_STAKES)
    parser.add_argument("--target-output-dir", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    parser.add_argument(
        "--keep-existing-on-conflict",
        action="store_true",
        help=(
            "keep an already rebound settled success when a later historical "
            "run produced a different valid response for the same exact prompt"
        ),
    )
    args = parser.parse_args()

    population_path = args.population.resolve()
    relationship_path = args.relationships.resolve()
    stake_path = args.stakes.resolve()
    population = runner._load(population_path)
    relationships = runner._load(relationship_path)
    stakes = runner._load(stake_path)
    preflight = runner._load(args.preflight.resolve())
    runner._validate_preflight(
        preflight,
        population_path=population_path,
        relationship_path=relationship_path,
        stake_path=stake_path,
        population=population,
        relationships=relationships,
        stakes=stakes,
    )
    authorization = runner._load(args.authorization.resolve())
    authorization_fingerprint = runner._validate_authorization(
        authorization, preflight=preflight
    )
    context, binding, _endpoint = runner._identities(
        preflight=preflight,
        authorization_fingerprint=authorization_fingerprint,
        timeout_seconds=args.timeout_seconds,
    )
    unknown_resolution = (
        _load_unknown_resolution(args.source_unknown_resolution_evidence)
        if args.source_unknown_resolution_evidence is not None
        else None
    )
    source_binding, rows, source_unknown_count = _source_rows(
        args.source_ledger,
        unknown_resolution=unknown_resolution,
    )
    if (
        source_binding.get("model_name") != context.model_name
        or source_binding.get("endpoint_fingerprint") != context.endpoint_fingerprint
        or source_binding.get("transport_config_fingerprint")
        != binding.transport_config_fingerprint
    ):
        raise ValueError("source and target transport/model bindings differ")

    rebound = []
    for row in rows:
        for key in ("prompt_sha256", "request_sha256", "response_sha256"):
            if _SHA256.fullmatch(str(row.get(key) or "")) is None:
                raise ValueError("source exact-cache fingerprint is invalid")
        if row.get("slot_kind") not in {"resident", "governance_actor"}:
            raise ValueError("source exact-cache slot kind is invalid")
        for key in ("content_json", "validated_decision_json"):
            if not isinstance(json.loads(str(row[key])), dict):
                raise ValueError("source validated structured result is invalid")
        cache_identity = {
            "namespace": "authorized_live_llm",
            "run_id": context.run_id,
            "plan_sha256": context.plan_sha256,
            "transport_config_fingerprint": binding.transport_config_fingerprint,
            "model_name": context.model_name,
            "slot_kind": row["slot_kind"],
            "prompt_sha256": row["prompt_sha256"],
            "request_sha256": row["request_sha256"],
            "attempt_ordinal": int(row["attempt_ordinal"]),
        }
        rebound.append({
            **row,
            "cache_key": runner.canonical_sha256(cache_identity),
        })

    output_dir = args.target_output_dir.resolve()
    ledger_path = output_dir / "authorized-live-ledger.sqlite3"
    repository = SQLiteV7AuthorizedTurnRepository(
        ledger_path,
        binding=binding,
        context=context,
        initialize=not ledger_path.exists(),
    )
    inserted = 0
    already_identical = 0
    kept_existing_conflicts = 0
    with repository._connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            if int(connection.execute(
                "SELECT COUNT(*) FROM v7_live_attempts"
            ).fetchone()[0]):
                raise ValueError("target ledger already contains Provider attempts")
            for row in rebound:
                values = (
                    row["cache_key"], row["slot_kind"], row["prompt_sha256"],
                    row["request_sha256"], row["response_sha256"],
                    row["returned_model"], int(row["prompt_tokens"]),
                    int(row["completion_tokens"]), int(row["total_tokens"]),
                    row["content_json"], row["validated_decision_json"],
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
                    inserted += 1
                elif tuple(existing) == values:
                    already_identical += 1
                elif args.keep_existing_on_conflict:
                    # Both rows are independently settled, strictly validated
                    # LLM results for the same exact prompt.  Preserve the
                    # first imported result to keep replay deterministic; do
                    # not overwrite or merge semantic content.
                    kept_existing_conflicts += 1
                else:
                    raise ValueError("target exact-cache identity drifted")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    body = {
        "schema_version": SCHEMA,
        "source_run_id": source_binding.get("run_id"),
        "source_preflight_sha256": source_binding.get("preflight_sha256"),
        "target_run_id": context.run_id,
        "target_preflight_sha256": binding.preflight_sha256,
        "settled_success_rows_rebound": len(rebound),
        "settled_success_rows_inserted": inserted,
        "already_identical_rows": already_identical,
        "kept_existing_valid_response_conflicts": kept_existing_conflicts,
        "unknown_or_failed_rows_rebound": 0,
        "source_unknown_rows_skipped": source_unknown_count,
        "source_unknown_resolution_evidence_sha256": (
            unknown_resolution.get("evidence_sha256")
            if unknown_resolution is not None
            else None
        ),
        "prompt_or_provider_bodies_persisted": False,
        "validated_structured_results_only": True,
    }
    evidence = {**body, "evidence_sha256": runner.canonical_sha256(body)}
    source_suffix = str(source_binding.get("run_id") or "unknown")[-24:]
    runner._write_json(
        output_dir / f"exact-cache-rebind-{source_suffix}.json", evidence
    )
    print(json.dumps(evidence, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
