#!/usr/bin/env python3
"""Operator-only P3.2 controls.

The ``run-fixture`` command is an offline deterministic orchestration check.
It never authorizes, enables or calls a model Provider, and its output is
explicitly non-exportable and unsuitable as a governance, causal or forecast
finding.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Sequence

from backend.campus_pulse.live_fixture_harness import (
    LiveFixtureHarnessError,
    materialize_and_run_live_fixture,
    plan_live_fixture_script,
)
from backend.campus_pulse.live_runtime_worker import LiveWorkerStep
from backend.campus_pulse.service import (
    WorkbenchError,
    WorkbenchService,
)
from backend.campus_pulse.store import (
    StoreConflictError,
    WorkbenchStore,
)


DEFAULT_DATABASE = "data/campus_pulse_workspace/workbench.sqlite3"
LIVE_CONFIRMATION_FLAG = "live_provider_spend_confirmed"
REVOCATION_REASONS = (
    "operator_revoked",
    "superseded_run",
    "budget_revision",
    "security_hold",
)


def _strict_environment_boolean(name: str, default: str) -> bool:
    value = os.environ.get(name, default).strip().lower()
    if value not in {"true", "false"}:
        raise WorkbenchError(f"{name} must be true or false")
    return value == "true"


def _service(database: Path) -> WorkbenchService:
    configured_cap = os.environ.get(
        "CAMPUS_PULSE_RUNTIME_CONCURRENCY_CAP",
        "32",
    ).strip()
    try:
        concurrency_cap = int(configured_cap)
    except ValueError as exc:
        raise WorkbenchError(
            "CAMPUS_PULSE_RUNTIME_CONCURRENCY_CAP must be an integer"
        ) from exc
    return WorkbenchService(
        WorkbenchStore(database.expanduser().resolve()),
        runtime_concurrency_cap=concurrency_cap,
        live_llm_enabled=_strict_environment_boolean(
            "CAMPUS_PULSE_LIVE_LLM_ENABLED",
            "false",
        ),
    )


def _database_for_service(value: Path | None) -> Path:
    if value is not None:
        return value
    return Path(
        os.environ.get("CAMPUS_PULSE_WORKBENCH_DB", DEFAULT_DATABASE)
    )


def _expiry(minutes: int) -> str:
    if not 1 <= minutes <= 1_440:
        raise WorkbenchError(
            "Authorization expiry must be between 1 and 1440 minutes"
        )
    return (
        datetime.now(timezone.utc) + timedelta(minutes=minutes)
    ).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _safe_authorization(value: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "authorization_id",
        "authorization_fingerprint",
        "tenant_id",
        "project_id",
        "run_id",
        "plan_fingerprint",
        "extension_fingerprint",
        "asset_bundle_fingerprint",
        "provider_profile_fingerprint",
        "template_mapping_fingerprint",
        "algorithm_fingerprint",
        "provider_call_cap",
        "token_cap",
        "state",
        "created_at",
        "expires_at",
        "consumed_at",
        "revoked_at",
        "revocation_reason_code",
        "lifecycle_version",
        "created",
    )
    return {
        field: value[field]
        for field in fields
        if field in value
    }


def _safe_worker_outcome(step: LiveWorkerStep) -> dict[str, Any]:
    """Project a worker result onto its privacy-safe public contract."""

    return {
        "outcome": step.outcome,
        "run_id": step.run_id,
        "status": step.status,
        "error_code": step.error_code,
    }


def _run_fixture(args: argparse.Namespace) -> dict[str, Any]:
    """Plan and execute one deterministic fixture without service/env gates."""

    if args.database is None:
        raise WorkbenchError(
            "--database is required for the offline run-fixture command"
        )
    try:
        store = WorkbenchStore(args.database.expanduser().resolve())
        plan = plan_live_fixture_script(
            store,
            args.tenant_id,
            args.project_id,
            args.run_id,
        )
        step = materialize_and_run_live_fixture(
            store,
            plan,
            idempotency_key=args.idempotency_key,
            worker_id=args.worker_id,
            lease_seconds=args.lease_seconds,
        )
        if step.run_id is not None and step.run_id != plan.run_id:
            raise WorkbenchError(
                "Deterministic fixture worker settled an unexpected run"
            )
    except WorkbenchError:
        raise
    except LiveFixtureHarnessError as exc:
        raise WorkbenchError(
            "Deterministic fixture operation was refused "
            f"({exc.code})"
        ) from None
    except (StoreConflictError, TypeError, ValueError):
        raise WorkbenchError(
            "Deterministic fixture could not be materialized or "
            "executed safely"
        ) from None
    except Exception:
        raise WorkbenchError(
            "Deterministic fixture failed without exposing private "
            "runtime details"
        ) from None

    return {
        "public_summary": plan.public_summary(),
        "worker_outcome": _safe_worker_outcome(step),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Manage the network-free CampusPulse P3.2a qualification gate."
        )
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help=(
            "Workbench SQLite path. Required explicitly by run-fixture; "
            "other commands may use CAMPUS_PULSE_WORKBENCH_DB."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    status = commands.add_parser("status")
    status.add_argument("--run-id")

    commands.add_parser("install-reviewed-inputs")
    commands.add_parser("enable-provider")
    commands.add_parser("disable-provider")

    authorize = commands.add_parser("authorize-run")
    authorize.add_argument("--run-id", required=True)
    authorize.add_argument(
        "--provider-call-cap",
        required=True,
        type=int,
    )
    authorize.add_argument("--token-cap", required=True, type=int)
    authorize.add_argument(
        "--expires-minutes",
        type=int,
        default=30,
    )
    authorize.add_argument(
        "--confirm-live-provider-spend",
        action="store_true",
        help=(
            "Required controlled confirmation. This does not read or "
            "validate a credential."
        ),
    )

    revoke = commands.add_parser("revoke-run")
    revoke.add_argument("--run-id", required=True)
    revoke.add_argument("--authorization-id", required=True)
    revoke.add_argument(
        "--reason-code",
        required=True,
        choices=REVOCATION_REASONS,
    )

    fixture = commands.add_parser(
        "run-fixture",
        help=(
            "Run an offline deterministic, non-exportable orchestration "
            "fixture. This never calls a model Provider."
        ),
    )
    fixture.add_argument(
        "--database",
        dest="database",
        type=Path,
        default=argparse.SUPPRESS,
        help=(
            "Explicit workbench SQLite path (may also be placed before "
            "the subcommand)."
        ),
    )
    fixture.add_argument("--tenant-id", required=True)
    fixture.add_argument("--project-id", required=True)
    fixture.add_argument("--run-id", required=True)
    fixture.add_argument("--idempotency-key", required=True)
    fixture.add_argument("--worker-id", required=True)
    fixture.add_argument(
        "--lease-seconds",
        type=int,
        default=300,
    )
    return parser


def execute(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "run-fixture":
        return _run_fixture(args)

    service = _service(_database_for_service(args.database))
    if args.command == "status":
        return service.live_runtime_capability(args.run_id)
    if args.command == "install-reviewed-inputs":
        return service.install_reviewed_live_runtime_inputs()
    if args.command == "enable-provider":
        return service.set_reviewed_live_provider_enabled(True)
    if args.command == "disable-provider":
        return service.set_reviewed_live_provider_enabled(False)
    if args.command == "authorize-run":
        if not args.confirm_live_provider_spend:
            raise WorkbenchError(
                "--confirm-live-provider-spend is required"
            )
        authorization = service.authorize_live_run(
            args.run_id,
            provider_call_cap=args.provider_call_cap,
            token_cap=args.token_cap,
            expires_at=_expiry(args.expires_minutes),
            confirmation_code=LIVE_CONFIRMATION_FLAG,
        )
        return _safe_authorization(authorization)
    if args.command == "revoke-run":
        authorization = service.revoke_live_run_authorization(
            args.run_id,
            args.authorization_id,
            reason_code=args.reason_code,
        )
        return _safe_authorization(authorization)
    raise WorkbenchError("Unsupported operator command")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = execute(args)
    except WorkbenchError as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "code": type(exc).__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
