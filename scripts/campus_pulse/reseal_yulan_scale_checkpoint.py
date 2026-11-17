#!/usr/bin/env python3
"""Reseal a serialized v3 checkpoint whose outer hash alone is stale.

The source is never modified.  The tool requires the exact legacy hash,
validates every nested checkpoint contract after recomputing the serialized
outer hash, and writes a sidecar that binds both compressed artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping
import zlib


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for candidate in (ROOT, SRC):
    value = str(candidate)
    if value not in sys.path:
        sys.path.insert(0, value)

from envs.campus_pulse_governance.mass_population import (  # noqa: E402
    canonical_hash,
)
from envs.campus_pulse_governance.yulan_scale_engine import (  # noqa: E402
    decode_yulan_scale_checkpoint_v3,
)


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--expected-legacy-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    source_path = args.checkpoint.resolve()
    source_bytes = source_path.read_bytes()
    try:
        checkpoint = json.loads(zlib.decompress(source_bytes).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("checkpoint compression or JSON is invalid") from exc
    if not isinstance(checkpoint, dict):
        raise RuntimeError("checkpoint must be a JSON object")
    legacy_sha = str(checkpoint.get("checkpoint_sha256") or "")
    if legacy_sha != args.expected_legacy_sha256:
        raise RuntimeError("legacy checkpoint hash does not match explicit expectation")
    body = {
        key: value
        for key, value in checkpoint.items()
        if key != "checkpoint_sha256"
    }
    serialized_sha = canonical_hash(body)
    if serialized_sha == legacy_sha:
        raise RuntimeError("checkpoint outer hash is already valid; reseal refused")
    resealed = {**body, "checkpoint_sha256": serialized_sha}
    runtime = decode_yulan_scale_checkpoint_v3(
        resealed,
        population_sha256=str(resealed["population_sha256"]),
        graph_sha256=str(resealed["graph_sha256"]),
    )
    if (
        resealed.get("branch") != "shared_baseline"
        or int(resealed.get("tick", -1)) != 2
        or len(runtime.timeline) != 3
        or len(runtime.activation_rounds) != 1
        or len(runtime.resident_slots) != 48
    ):
        raise RuntimeError("reseal source is not the committed 48-slot shared T2 frontier")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / (
        f"{resealed['scenario_id']}-shared_baseline-T2-{serialized_sha}.json.zlib"
    )
    encoded = zlib.compress(_canonical_bytes(resealed), level=9)
    target.write_bytes(encoded)
    sidecar = {
        "schema_version": "campus-pulse-checkpoint-outer-reseal-v1",
        "status": "nested_contracts_valid_outer_hash_resealed",
        "source_checkpoint_sha256": legacy_sha,
        "resealed_checkpoint_sha256": serialized_sha,
        "source_compressed_file_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "resealed_compressed_file_sha256": hashlib.sha256(encoded).hexdigest(),
        "source_modified": False,
        "validated_state": {
            "timeline_points": len(runtime.timeline),
            "activation_rounds": len(runtime.activation_rounds),
            "resident_slots": len(runtime.resident_slots),
        },
        "reason": "serialized_outer_hash_mismatch_with_all_nested_contracts_valid",
    }
    sidecar["reseal_evidence_sha256"] = canonical_hash(sidecar)
    (output_dir / "checkpoint-outer-reseal-v1.json").write_bytes(
        _canonical_bytes(sidecar)
    )
    print(
        json.dumps(
            {
                "status": sidecar["status"],
                "source_checkpoint_sha256": legacy_sha,
                "resealed_checkpoint_sha256": serialized_sha,
                "reseal_evidence_sha256": sidecar["reseal_evidence_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
