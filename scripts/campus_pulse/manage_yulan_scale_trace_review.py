#!/usr/bin/env python3
"""Offline YuLan-Scale live-ledger review and trace-release utility.

This command never calls a Provider.  It reads an already committed v11
authorized-live ledger in SQLite and fails closed unless the formal evidence
and direct-review contracts are complete.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.campus_pulse.llm_population_review import (  # noqa: E402
    TraceReviewError,
    build_pending_review_verdict,
    build_private_review_pack,
    compile_reviewed_trace_release,
    load_authorized_live_ledger,
    seal_review_verdict,
    write_private_json,
)


def _read_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TraceReviewError(f"{label} is not readable JSON") from exc
    if not isinstance(value, dict):
        raise TraceReviewError(f"{label} must contain a JSON object")
    return value


def _add_ledger_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--run-id", required=True)


def _load(args: argparse.Namespace):
    return load_authorized_live_ledger(
        args.database,
        tenant_id=args.tenant_id,
        project_id=args.project_id,
        run_id=args.run_id,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export a private 640-item review pack or compile an already "
            "approved 13,056-slot YuLan-Scale trace release. No Provider "
            "transport is available in this command."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)

    export = commands.add_parser("export", help="create pack and pending verdict")
    _add_ledger_arguments(export)
    export.add_argument("--review-pack-output", required=True, type=Path)
    export.add_argument("--verdict-template-output", required=True, type=Path)

    seal = commands.add_parser("seal-verdict", help="validate and seal human verdicts")
    seal.add_argument("--review-pack", required=True, type=Path)
    seal.add_argument("--filled-verdict", required=True, type=Path)
    seal.add_argument("--output", required=True, type=Path)

    compile_parser = commands.add_parser(
        "compile", help="compile the formal reviewed trace release"
    )
    _add_ledger_arguments(compile_parser)
    compile_parser.add_argument("--review-pack", required=True, type=Path)
    compile_parser.add_argument("--sealed-verdict", required=True, type=Path)
    compile_parser.add_argument("--release-id", required=True)
    compile_parser.add_argument("--created-at", required=True)
    compile_parser.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "export":
        snapshot = _load(args)
        pack = build_private_review_pack(snapshot)
        verdict = build_pending_review_verdict(pack)
        write_private_json(args.review_pack_output, pack)
        write_private_json(args.verdict_template_output, verdict)
        summary = {
            "status": "pending_review",
            "provider_calls_executed": 0,
            "ledger_sha256": snapshot.ledger_sha256,
            "selected_count": pack["selection"]["selected_count"],
            "review_pack_sha256": pack["review_pack_sha256"],
            "review_pack_output": str(args.review_pack_output),
            "verdict_template_output": str(args.verdict_template_output),
        }
    elif args.command == "seal-verdict":
        pack = _read_object(args.review_pack, "review pack")
        verdict = _read_object(args.filled_verdict, "filled verdict")
        sealed = seal_review_verdict(pack, verdict)
        write_private_json(args.output, sealed)
        summary = {
            "status": sealed["status"],
            "provider_calls_executed": 0,
            **sealed["summary"],
            "review_verdict_sha256": sealed["review_verdict_sha256"],
            "output": str(args.output),
        }
    else:
        snapshot = _load(args)
        pack = _read_object(args.review_pack, "review pack")
        verdict = _read_object(args.sealed_verdict, "sealed verdict")
        release = compile_reviewed_trace_release(
            snapshot,
            review_pack=pack,
            review_verdict=verdict,
            release_id=args.release_id,
            created_at=args.created_at,
        )
        write_private_json(args.output, release)
        summary = {
            "status": "compiled",
            "provider_calls_executed": 0,
            "entry_count": release["entry_count"],
            "reviewed_entry_count": release["review"]["reviewed_entry_count"],
            "release_sha256": release["release_sha256"],
            "output": str(args.output),
        }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TraceReviewError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, sort_keys=True))
        raise SystemExit(2) from exc

