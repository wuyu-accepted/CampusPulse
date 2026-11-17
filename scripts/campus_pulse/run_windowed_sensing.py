#!/usr/bin/env python3
"""Operator CLI for the private three-window sensing pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from src.envs.campus_pulse_governance.llm_agents import (
    DEFAULT_API_KEY_ENV,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
)
from src.envs.campus_pulse_governance.semantic_population import (
    _atomic_write_json,
)
from src.envs.campus_pulse_governance.windowed_sensing import (
    DEFAULT_SAMPLE_PER_WINDOW,
    DEFAULT_WINDOW_SEED,
    REVIEWED_TAXONOMY_RELEASE_SHA256,
    build_windowed_review_queue,
    run_windowed_classification_pilot,
    run_windowed_context_refinement,
    run_windowed_semantic_pilot,
    sample_windowed_posts,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compile private, checkpointed three-window sensing evidence. "
            "No record-level output is printed."
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)

    sample = commands.add_parser(
        "sample-audit", help="write only the safe aggregate sampling audit"
    )
    sample.add_argument("--posts", required=True)
    sample.add_argument("--output", required=True)
    sample.add_argument(
        "--sample-per-window", type=int, default=DEFAULT_SAMPLE_PER_WINDOW
    )
    sample.add_argument("--seed", type=int, default=DEFAULT_WINDOW_SEED)

    semantic = commands.add_parser(
        "semantic", help="run or resume blinded semantic normalization"
    )
    semantic.add_argument("--posts", required=True)
    semantic.add_argument("--output", required=True)
    semantic.add_argument(
        "--sample-per-window", type=int, default=DEFAULT_SAMPLE_PER_WINDOW
    )
    semantic.add_argument("--seed", type=int, default=DEFAULT_WINDOW_SEED)
    semantic.add_argument("--batch-size", type=int, default=20)
    semantic.add_argument("--workers", type=int, default=16)
    semantic.add_argument("--max-total-tokens", type=int, default=1_400_000)
    semantic.add_argument(
        "--budget-extension-reason",
        choices=(
            "complete_preregistered_stage",
            "retry_after_contract_hardening",
            "operator_approved_extension",
        ),
    )
    semantic.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    semantic.add_argument("--base-url", default=DEFAULT_BASE_URL)
    semantic.add_argument("--model", default=DEFAULT_MODEL)
    semantic.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high"),
        default="none",
    )
    semantic.add_argument("--max-output-tokens", type=int, default=8000)
    semantic.add_argument("--timeout-seconds", type=float, default=120.0)
    semantic.add_argument(
        "--allow-provider-text",
        action="store_true",
        help="required explicit opt-in for contact-redacted forum text",
    )

    context = commands.add_parser(
        "context", help="refine only routed cases with bounded comments"
    )
    context.add_argument("--posts", required=True)
    context.add_argument("--comments", required=True)
    context.add_argument("--semantic", required=True)
    context.add_argument("--output", required=True)
    context.add_argument("--batch-size", type=int, default=5)
    context.add_argument("--max-comments-per-post", type=int, default=5)
    context.add_argument("--workers", type=int, default=4)
    context.add_argument("--max-total-tokens", type=int, default=250_000)
    context.add_argument(
        "--budget-extension-reason",
        choices=(
            "complete_preregistered_stage",
            "retry_after_contract_hardening",
            "operator_approved_extension",
        ),
    )
    context.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    context.add_argument("--base-url", default=DEFAULT_BASE_URL)
    context.add_argument("--model", default=DEFAULT_MODEL)
    context.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high"),
        default="none",
    )
    context.add_argument("--max-output-tokens", type=int, default=8000)
    context.add_argument("--timeout-seconds", type=float, default=120.0)
    context.add_argument(
        "--allow-provider-text",
        action="store_true",
        help="required explicit opt-in for redacted posts and comments",
    )

    classify = commands.add_parser(
        "classify", help="run or resume frozen-taxonomy controlled coding"
    )
    classify.add_argument("--semantic", required=True)
    classify.add_argument("--context")
    classify.add_argument("--taxonomy", required=True)
    classify.add_argument("--output", required=True)
    classify.add_argument(
        "--expected-taxonomy-sha256",
        default=REVIEWED_TAXONOMY_RELEASE_SHA256,
    )
    classify.add_argument("--batch-size", type=int, default=20)
    classify.add_argument("--workers", type=int, default=16)
    classify.add_argument("--max-total-tokens", type=int, default=1_000_000)
    classify.add_argument(
        "--budget-extension-reason",
        choices=(
            "complete_preregistered_stage",
            "retry_after_contract_hardening",
            "operator_approved_extension",
        ),
    )
    classify.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    classify.add_argument("--base-url", default=DEFAULT_BASE_URL)
    classify.add_argument("--model", default=DEFAULT_MODEL)
    classify.add_argument(
        "--reasoning-effort",
        choices=("none", "low", "medium", "high"),
        default="none",
    )
    classify.add_argument("--max-output-tokens", type=int, default=8000)
    classify.add_argument("--timeout-seconds", type=float, default=120.0)
    classify.add_argument(
        "--allow-provider-text",
        action="store_true",
        help=(
            "required explicit opt-in for privacy-bounded semantic cards"
        ),
    )

    review = commands.add_parser(
        "review-queue",
        help="build the private mandatory plus deterministic audit queue",
    )
    review.add_argument("--semantic", required=True)
    review.add_argument("--context")
    review.add_argument("--classification", required=True)
    review.add_argument("--output", required=True)
    review.add_argument("--audit-fraction-ppm", type=int, default=100_000)
    review.add_argument("--seed", type=int, default=20260727)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "sample-audit":
        _, audit = sample_windowed_posts(
            args.posts,
            sample_per_window=args.sample_per_window,
            seed=args.seed,
        )
        _atomic_write_json(audit, Path(args.output))
        summary = {
            "status": "complete",
            "sample_manifest_fingerprint": audit[
                "sample_manifest_fingerprint"
            ],
            "windows": [
                {
                    "window_key": item["window_key"],
                    "eligible_count": item["eligible_count"],
                    "selected_count": item["selected_count"],
                }
                for item in audit["windows"]
            ],
        }
    elif args.command == "semantic":
        result = run_windowed_semantic_pilot(
            posts_path=args.posts,
            output_path=args.output,
            sample_per_window=args.sample_per_window,
            seed=args.seed,
            batch_size=args.batch_size,
            max_concurrency=args.workers,
            max_total_tokens=args.max_total_tokens,
            api_key_env=args.api_key_env,
            base_url=args.base_url,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            max_output_tokens=args.max_output_tokens,
            timeout_seconds=args.timeout_seconds,
            allow_provider_text=args.allow_provider_text,
            budget_extension_reason=args.budget_extension_reason,
        )
        summary = {
            "status": result["status"],
            "run_fingerprint": result["run_fingerprint"],
            "summary": result.get("summary", {}),
            "usage": result["execution"]["usage"],
        }
    elif args.command == "context":
        result = run_windowed_context_refinement(
            posts_path=args.posts,
            comments_path=args.comments,
            semantic_result_path=args.semantic,
            output_path=args.output,
            batch_size=args.batch_size,
            max_comments_per_post=args.max_comments_per_post,
            max_concurrency=args.workers,
            max_total_tokens=args.max_total_tokens,
            api_key_env=args.api_key_env,
            base_url=args.base_url,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            max_output_tokens=args.max_output_tokens,
            timeout_seconds=args.timeout_seconds,
            allow_provider_text=args.allow_provider_text,
            budget_extension_reason=args.budget_extension_reason,
        )
        summary = {
            "status": result["status"],
            "run_fingerprint": result["run_fingerprint"],
            "summary": result.get("summary", {}),
            "usage": result["execution"]["usage"],
        }
    elif args.command == "classify":
        result = run_windowed_classification_pilot(
            semantic_result_path=args.semantic,
            context_result_path=args.context,
            frozen_taxonomy_path=args.taxonomy,
            output_path=args.output,
            expected_taxonomy_sha256=args.expected_taxonomy_sha256,
            batch_size=args.batch_size,
            max_concurrency=args.workers,
            max_total_tokens=args.max_total_tokens,
            api_key_env=args.api_key_env,
            base_url=args.base_url,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            max_output_tokens=args.max_output_tokens,
            timeout_seconds=args.timeout_seconds,
            allow_provider_text=args.allow_provider_text,
            budget_extension_reason=args.budget_extension_reason,
        )
        summary = {
            "status": result["status"],
            "run_fingerprint": result["run_fingerprint"],
            "summary": result.get("summary", {}),
            "usage": result["execution"]["usage"],
        }
    else:
        result = build_windowed_review_queue(
            semantic_result_path=args.semantic,
            context_result_path=args.context,
            classification_result_path=args.classification,
            output_path=args.output,
            audit_fraction_ppm=args.audit_fraction_ppm,
            seed=args.seed,
        )
        summary = {
            "status": result["status"],
            "run_fingerprint": result["run_fingerprint"],
            "selection": result["selection"],
        }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
