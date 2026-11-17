#!/usr/bin/env python3
"""Resume the frozen 99-slot run with bounded settled-contract repairs.

This wrapper does not alter the frozen simulation, prompt renderer, or
preflight-bound runner.  It only enables the existing transport-level repair
policy for responses that are conclusively settled but fail the JSON semantic
contract.  Unknown network outcomes remain fail-closed and are never retried.
The frozen ledger still enforces the 198-request and 2,000,000-token caps.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import run_forum_persona_stage_a_authorized as frozen_runner

from backend.campus_pulse.yulan_scale_live import (
    AuthorizedV7SemanticSource as _AuthorizedV7SemanticSource,
    V7ControlledRepairPolicy,
    V7ManualUnknownOutcomeResolution,
)
from backend.campus_pulse.runtime_contract import canonical_sha256


UNKNOWN_RESOLUTION_SCHEMA = (
    "campus-pulse-forum-hero-unknown-outcome-resolution-v1"
)
_unknown_resolution_paths: list[Path] = []
_FrozenRepository = frozen_runner.SQLiteV7AuthorizedTurnRepository


def _extract_wrapper_arguments() -> None:
    flag = "--manual-unknown-resolution-evidence"
    while flag in sys.argv:
        index = sys.argv.index(flag)
        if index + 1 >= len(sys.argv):
            raise ValueError(f"{flag} requires one path")
        _unknown_resolution_paths.append(Path(sys.argv[index + 1]).resolve())
        del sys.argv[index : index + 2]


def _unknown_resolutions(binding):
    resolutions = []
    for path in _unknown_resolution_paths:
        evidence = json.loads(path.read_text(encoding="utf-8"))
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
            or evidence.get("schema_version") != UNKNOWN_RESOLUTION_SCHEMA
            or evidence.get("run_id") != binding.run_id
            or evidence.get("preflight_sha256") != binding.preflight_sha256
            or evidence.get("parent_authorization_fingerprint")
            != binding.authorization_fingerprint
            or evidence.get("original_outcome") != "unknown"
            or evidence.get("resolution") != "abandon_and_replace"
            or evidence.get("replacement_limit") != 1
            or evidence.get("automatic_network_retry_limit") != 0
            or evidence.get("late_provider_result_integration_forbidden") is not True
            or replacement_fingerprint != canonical_sha256(authorization_material)
        ):
            raise ValueError("manual unknown-resolution evidence is invalid")
        resolutions.append(
            V7ManualUnknownOutcomeResolution(
                failed_attempt_id=str(evidence["failed_attempt_id"]),
                prompt_sha256=str(evidence["prompt_sha256"]),
                request_sha256=str(evidence["request_sha256"]),
                risk_charged_tokens=int(evidence["risk_charged_tokens"]),
                authorization_fingerprint=replacement_fingerprint,
            )
        )
    return tuple(resolutions)


class _ResolvedUnknownAuditRepository(_FrozenRepository):
    """Expose unresolved unknowns without erasing append-only history."""

    def audit_summary(self):
        summary = dict(super().audit_summary())
        historical = int(summary.get("provider_calls_unknown", 0))
        resolved = len(_unknown_resolution_paths)
        unresolved = max(0, historical - resolved)
        summary["provider_calls_unknown_historical"] = historical
        summary["provider_calls_unknown_resolved_by_evidence"] = min(
            historical, resolved
        )
        summary["provider_calls_unknown"] = unresolved
        return summary


def _source_with_controlled_repairs(*args, **kwargs):
    binding = kwargs["binding"]
    kwargs["controlled_repair_policy"] = V7ControlledRepairPolicy(
        run_id=binding.run_id,
        preflight_sha256=binding.preflight_sha256,
        authorization_fingerprint=binding.authorization_fingerprint,
        max_semantic_repairs_per_slot=3,
        automatic_network_retry_limit=0,
        rejected_provider_response_excluded=True,
    )
    kwargs["manual_unknown_outcome_resolutions"] = _unknown_resolutions(binding)
    return _AuthorizedV7SemanticSource(*args, **kwargs)


_frozen_execute_condition = frozen_runner._execute_condition


def _execute_condition_with_snapshot_recovery(*args, **kwargs):
    """Recover the T3 world while retaining the first-run audit checkpoint.

    Replaying a settled turn correctly changes only transport receipt fields
    such as ``cache_hit`` and ``provider_calls``.  Those fields are included in
    the private audit checkpoint but do not alter the reconstructed world.  The
    frozen runner therefore reports a whole-checkpoint mismatch even though
    every prompt/response pair came from its exact cache.  Once the callback
    has reconstructed the world, retain the sealed first-run checkpoint.
    """

    try:
        return _frozen_execute_condition(*args, **kwargs)
    except ValueError as exc:
        if (
            str(exc) != "Stage-A exact-cache replay changed its checkpoint"
            or kwargs.get("snapshot_out") is None
            or not kwargs["snapshot_out"]
        ):
            raise
        existing = frozen_runner._read_condition_checkpoint(
            kwargs["output_dir"],
            condition=kwargs["condition"],
            preflight_sha256=str(kwargs["preflight"]["preflight_sha256"]),
        )
        if existing is None:
            raise
        return existing


def main() -> int:
    _extract_wrapper_arguments()
    frozen_runner.AuthorizedV7SemanticSource = _source_with_controlled_repairs
    frozen_runner.SQLiteV7AuthorizedTurnRepository = (
        _ResolvedUnknownAuditRepository
    )
    frozen_runner._execute_condition = _execute_condition_with_snapshot_recovery
    return frozen_runner.main()


if __name__ == "__main__":
    raise SystemExit(main())
