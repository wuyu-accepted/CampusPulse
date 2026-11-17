#!/usr/bin/env python3
"""Build a reusable, anonymous particle panel from the private fidelity snapshot.

The private snapshot contains runtime parent indices, settled-turn fingerprints and
assimilation records.  The public panel keeps only state values, weights and a
stable *anonymous* parent slot.  This is sufficient to reproduce population
heterogeneity statistics without exposing an Agent ID or a join key to profiles,
messages, prompts, source records or Provider responses.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np


SCHEMA_VERSION = "campus-pulse-anonymous-particle-panel-v1"
SEED_LABEL = b"campus-pulse-public-particle-panel-v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_public_panel(source: Path, output: Path, manifest_path: Path) -> dict[str, Any]:
    with np.load(source, allow_pickle=False) as private:
        metadata = json.loads(str(private["metadata_json"].item()))
        frames = metadata.get("frames")
        state_fields = private["state_fields"].astype("U32")
        if not isinstance(frames, list) or len(frames) != 14:
            raise ValueError("private snapshot must contain the frozen 14-frame panel")
        if state_fields.shape != (12,):
            raise ValueError("private snapshot must contain 12 state fields")

        # A deterministic permutation severs the runtime parent index while keeping
        # a stable anonymous longitudinal panel for statistical reuse.
        seed = int.from_bytes(hashlib.sha256(SEED_LABEL).digest()[:8], "little")
        rng = np.random.default_rng(seed)
        parent_permutation = rng.permutation(1000)
        particle_permutations = np.stack([rng.permutation(10) for _ in range(1000)])

        arrays: dict[str, Any] = {
            "state_fields": state_fields,
            "anonymous_parent_slot": np.arange(1000, dtype=np.int32),
            "anonymous_particle_slot": np.arange(10, dtype=np.int8),
        }
        public_frames: list[dict[str, Any]] = []
        for frame_index, frame in enumerate(frames):
            prefix = str(frame["prefix"])
            values = np.asarray(private[f"{prefix}_values"], dtype=np.float32)
            weights = np.asarray(private[f"{prefix}_weights"], dtype=np.float64)
            parent_weights = np.asarray(
                private[f"{prefix}_parent_weights"], dtype=np.float64
            )
            if values.shape != (1000, 10, 12) or weights.shape != (1000, 10):
                raise ValueError(f"invalid particle shape for {prefix}")
            if parent_weights.shape != (1000,):
                raise ValueError(f"invalid parent-weight shape for {prefix}")

            values = values[parent_permutation]
            weights = weights[parent_permutation]
            parent_weights = parent_weights[parent_permutation]
            values = np.take_along_axis(values, particle_permutations[:, :, None], axis=1)
            weights = np.take_along_axis(weights, particle_permutations, axis=1)

            public_prefix = f"frame_{frame_index:02d}"
            arrays[f"{public_prefix}_values"] = values
            arrays[f"{public_prefix}_weights"] = weights
            arrays[f"{public_prefix}_parent_weights"] = parent_weights
            if not np.allclose(weights.sum(axis=1), parent_weights, atol=1e-12):
                raise ValueError(f"particle weights do not conserve parent weights: {prefix}")
            if not np.isclose(weights.sum(), 1.0, atol=1e-12):
                raise ValueError(f"particle weights do not sum to one: {prefix}")
            public_frames.append(
                {
                    "prefix": public_prefix,
                    "tick": int(frame["tick"]),
                    "branch": str(frame["branch"]),
                    "anonymous_parent_count": 1000,
                    "particles_per_parent": 10,
                }
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output, **arrays)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "panel_file": output.name,
        "panel_sha256": _sha256(output),
        "frames": public_frames,
        "state_fields": state_fields.tolist(),
        "shape_per_frame": [1000, 10, 12],
        "weight_contract": {
            "particle_weights_sum_to_parent_weight": True,
            "all_particle_weights_sum_to_one_per_frame": True,
        },
        "anonymization": {
            "stable_anonymous_parent_slot": True,
            "runtime_parent_indices_removed": True,
            "agent_ids_removed": True,
            "profile_join_keys_removed": True,
            "prompt_and_response_fingerprints_removed": True,
            "assimilation_records_removed": True,
            "source_ids_and_text_absent": True,
        },
        "allowed_uses": [
            "per-frame particle distributions",
            "within-parent particle uncertainty",
            "longitudinal anonymous-parent change",
            "Natural versus D state comparisons",
        ],
        "interpretation": (
            "The 10 particles under one anonymous parent are uncertainty states for "
            "one synthetic Agent, not ten people. Anonymous slots cannot be joined to "
            "profiles, forum messages or source records."
        ),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_public_panel(args.source, args.output, args.manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
