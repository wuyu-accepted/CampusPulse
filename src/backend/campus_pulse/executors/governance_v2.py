"""Zero-Provider incremental executor for Governance Arena run-plan-v6."""

from __future__ import annotations

import base64
import importlib
import zlib
from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

from ..governance_v2_contract import (
    GOVERNANCE_V2_DOMAIN_RESULT_SCHEMA_VERSION,
    GOVERNANCE_V2_EXECUTION_MODE,
    GOVERNANCE_V2_FIXTURE_SLOTS,
    GOVERNANCE_V2_PRIVATE_ARTIFACT_SET_SCHEMA_VERSION,
    GOVERNANCE_V2_REQUIRED_UNITS,
    GOVERNANCE_V2_RESULT_SCHEMA_VERSION,
    GOVERNANCE_V2_VISUALIZATION_SCHEMA_VERSION,
    bytes_sha256,
    canonical_json_bytes,
)
from ..runtime import (
    RuntimeBudgetExceeded,
    RuntimeBudgetLimits,
    RuntimeProgress,
    complete_budget_reservation,
    reserve_budget,
)
from ..runtime_contract import (
    FAKE_ADAPTER_MODE,
    FrozenRunPlan,
    RuntimeContractError,
    canonical_sha256,
    required_turn_units,
    validate_aggregate_result,
    validate_frozen_run_plan,
    validate_launch_contract,
)
from .base import (
    ArtifactCallback,
    CancellationProbe,
    ExecutorCancelled,
    ExecutorCheckpoint,
    ExecutorOutcome,
    ExecutorProgressUpdate,
    ProgressCallback,
)


def seal_governance_v2_result_v4(
    domain_result: Mapping[str, Any],
    visual_frames,
    *,
    run_plan: Mapping[str, Any] | FrozenRunPlan,
    launch_contract: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    """Purely seal the public v4 envelope, manifest and frame binary."""

    plan = (
        run_plan
        if isinstance(run_plan, FrozenRunPlan)
        else validate_frozen_run_plan(run_plan)
    )
    launch = validate_launch_contract(
        launch_contract, run_plan=plan
    )
    if (
        plan.execution_mode != GOVERNANCE_V2_EXECUTION_MODE
        or not isinstance(domain_result, Mapping)
        or domain_result.get("schema_version")
        != GOVERNANCE_V2_DOMAIN_RESULT_SCHEMA_VERSION
    ):
        raise ValueError("Governance v2 sealing input is invalid")
    normalized: list[tuple[dict[str, Any], bytes]] = []
    for item in visual_frames:
        if (
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], Mapping)
            and isinstance(item[1], bytes)
        ):
            metadata, content = dict(item[0]), item[1]
        elif isinstance(item, Mapping):
            metadata = dict(item)
            content = AdaptiveParticlePopulationExecutor._visual_bytes(
                metadata
            )
        else:
            raise ValueError("Governance v2 visual frame is invalid")
        normalized.append((metadata, content))
    if not normalized:
        raise ValueError("Governance v2 visual frame set is empty")
    normalized.sort(
        key=lambda item: (
            str(item[0].get("scenario_id")),
            str(item[0].get("policy_branch", item[0].get("branch"))),
            int(item[0].get("tick", -1)),
            int(item[0].get("frame_index", 0)),
        )
    )
    stride_values = {
        int(
            metadata.get(
                "record_stride_bytes", metadata.get("stride", 0)
            )
        )
        for metadata, _content in normalized
    }
    if len(stride_values) != 1 or next(iter(stride_values)) <= 0:
        raise ValueError(
            "Governance v2 visual frame stride is inconsistent"
        )
    record_stride = next(iter(stride_values))
    if any(len(content) % record_stride for _metadata, content in normalized):
        raise ValueError(
            "Governance v2 visual frame length is not stride-aligned"
        )
    binary = b"".join(content for _metadata, content in normalized)
    binary_sha = bytes_sha256(binary)
    domain_document = dict(domain_result)
    domain_sha = canonical_sha256(domain_document)
    body = {
        "schema_version": GOVERNANCE_V2_RESULT_SCHEMA_VERSION,
        "run_id": plan.run_id,
        "launch_fingerprint": launch["launch_fingerprint"],
        "result_kind": (
            "adaptive_particle_population_model_conditional_simulation"
        ),
        "plan_sha256": plan.input_snapshot[
            "governance_arena_v2"
        ]["plan_sha256"],
        "domain_result": domain_document,
        "visualization_asset": {
            "domain_result_sha256": domain_sha,
            "binary_sha256": binary_sha,
            "byte_length": len(binary),
            "record_stride_bytes": record_stride,
            "frame_count": len(normalized),
        },
        "completeness": {
            "status": "complete",
            "completed_turns": GOVERNANCE_V2_REQUIRED_UNITS,
            "required_turns": GOVERNANCE_V2_REQUIRED_UNITS,
        },
        "budget_usage": {
            "turn_limit": launch["turn_limit"],
            "turns_used": GOVERNANCE_V2_REQUIRED_UNITS,
            "token_limit": launch["token_limit"],
            "provider_tokens_used": 0,
            "effective_concurrency_limit": launch[
                "effective_concurrency_limit"
            ],
            "peak_concurrency": 1,
        },
        "usage": {
            "provider_calls": 0,
            "provider_tokens": 0,
            "real_governance_actions": 0,
            "fixture_semantic_slots": GOVERNANCE_V2_FIXTURE_SLOTS,
        },
        "privacy": {
            "raw_text_included": False,
            "source_identifiers_included": False,
            "record_level_edges_included": False,
            "sample_identities_included": False,
            "parent_particle_mapping_included": False,
            "provider_secrets_included": False,
            "runtime_paths_included": False,
        },
        "non_claims": [
            "episodes_are_not_unique_students",
            "particles_are_not_independent_evidence_subjects",
            "exposure_graph_is_fully_synthetic",
            "policy_effects_are_not_causally_identified",
            "no_real_governance_action_was_executed",
            "fixture_probes_are_not_provider_observations",
        ],
    }
    sealed = {**body, "result_sha256": canonical_sha256(body)}
    offset = 0
    frame_index = []
    for index, (metadata, content) in enumerate(normalized):
        frame_index.append(
            {
                "index": index,
                "scenario_id": metadata["scenario_id"],
                "policy_branch": metadata.get(
                    "policy_branch", metadata.get("branch")
                ),
                "tick": int(metadata["tick"]),
                "offset": offset,
                "length": len(content),
            }
        )
        offset += len(content)
    manifest = {
        "schema_version": GOVERNANCE_V2_VISUALIZATION_SCHEMA_VERSION,
        "run_id": plan.run_id,
        "outer_result_sha256": sealed["result_sha256"],
        "domain_result_sha256": domain_sha,
        "binary_sha256": binary_sha,
        "byte_length": len(binary),
        "record_stride_bytes": record_stride,
        "frame_count": len(frame_index),
        "frames": frame_index,
    }
    validated = validate_aggregate_result(
        sealed,
        run_plan=plan,
        launch_contract=launch,
    )
    return validated, manifest, binary


class AdaptiveParticlePopulationExecutor:
    """Drive the vectorized v2 engine through a narrow delayed import."""

    adapter_mode = FAKE_ADAPTER_MODE
    network_enabled = False
    provider_enabled = False

    def __init__(self, release_dir: str | Path):
        self.release_dir = Path(release_dir)

    @staticmethod
    def _engine_module():
        module = importlib.import_module(
            "envs.campus_pulse_governance.governance_arena_v2"
        )
        if not callable(
            getattr(module, "run_governance_arena_v2", None)
        ) or not callable(getattr(module, "build_run_plan_v6", None)):
            raise RuntimeError(
                "Governance Arena v2 engine protocol is unavailable"
            )
        return module

    def _load_runtime_inputs(
        self,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        try:
            population = json.loads(
                (self.release_dir / "semantic-population-v2.json").read_text(
                    encoding="utf-8"
                )
            )
            graph = json.loads(
                (
                    self.release_dir / "synthetic-exposure-graph-v1.json"
                ).read_text(encoding="utf-8")
            )
            writeback = json.loads(
                (
                    self.release_dir / "writeback-evaluation-v1.json"
                ).read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                "Governance Arena v2 private release inputs are unavailable"
            ) from exc
        return population, graph, writeback

    @staticmethod
    def _checkpoint(
        completed_units: int,
        progress: RuntimeProgress,
    ) -> ExecutorCheckpoint:
        return ExecutorCheckpoint(
            completed_units=completed_units,
            turns_used=progress.turns_completed,
            tokens_used=progress.tokens_used,
        )

    @staticmethod
    def _durable_completed_units(
        resume_state: Mapping[str, Any] | None,
    ) -> int:
        checkpoints = (
            resume_state.get("branch_checkpoints", {})
            if isinstance(resume_state, Mapping)
            else {}
        )
        if not isinstance(checkpoints, Mapping):
            return 0
        completed = 0
        for key, checkpoint in checkpoints.items():
            if not isinstance(key, str) or not isinstance(
                checkpoint, Mapping
            ):
                raise ValueError(
                    "Governance v2 resume checkpoint is invalid"
                )
            branch = key.rsplit("|", 1)[-1]
            tick = int(checkpoint.get("tick", -1))
            if branch == "shared_baseline":
                completed += min(3, tick + 1)
            elif branch in {"natural", "A", "B", "C", "D"}:
                completed += max(0, min(21, tick - 2))
            else:
                raise ValueError(
                    "Governance v2 resume branch is invalid"
                )
        return completed

    @staticmethod
    def _visual_bytes(payload: Mapping[str, Any]) -> bytes:
        value = payload.get(
            "content",
            payload.get("content_base64", payload.get("payload")),
        )
        if isinstance(value, bytes):
            content = value
        elif isinstance(value, str):
            try:
                content = base64.b64decode(value, validate=True)
            except (ValueError, TypeError):
                content = value.encode("utf-8")
        else:
            raise ValueError("visual_frame content is unavailable")
        if not content:
            raise ValueError("visual_frame content is empty")
        if payload.get("encoding") == "zlib+base64":
            try:
                content = zlib.decompress(content)
            except zlib.error as exc:
                raise ValueError(
                    "visual_frame compression is invalid"
                ) from exc
        expected = payload.get("content_sha256")
        if expected != bytes_sha256(content):
            raise ValueError("visual_frame content hash is invalid")
        return content

    @classmethod
    def _resume_visual_frames(
        cls,
        resume_state: Mapping[str, Any] | None,
        *,
        canonical_seed: int,
    ) -> list[tuple[dict[str, Any], bytes]]:
        """Restore canonical display frames already sealed by checkpoints."""

        prefixes = (
            resume_state.get("visual_frames", {})
            if isinstance(resume_state, Mapping)
            else {}
        )
        if not isinstance(prefixes, Mapping):
            raise ValueError(
                "Governance v2 resume visual prefixes are invalid"
            )
        restored: dict[
            tuple[str, str, int], tuple[dict[str, Any], bytes]
        ] = {}
        for resume_key, frames in prefixes.items():
            if not isinstance(resume_key, str) or not isinstance(
                frames, list
            ):
                raise ValueError(
                    "Governance v2 resume visual prefix is invalid"
                )
            try:
                scenario_id, seed_text, branch = resume_key.rsplit(
                    "|", 2
                )
                seed = int(seed_text)
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    "Governance v2 resume visual key is invalid"
                ) from exc
            if branch == "shared_baseline":
                # Baseline frames are relabelled and emitted for each policy
                # branch that has not itself reached a checkpoint.
                continue
            if branch not in {"natural", "A", "B", "C", "D"}:
                raise ValueError(
                    "Governance v2 resume visual branch is invalid"
                )
            if seed != canonical_seed:
                if frames:
                    raise ValueError(
                        "Non-canonical seed exposed visual frames"
                    )
                continue
            for frame in frames:
                if not isinstance(frame, Mapping):
                    raise ValueError(
                        "Governance v2 resume visual frame is invalid"
                    )
                metadata = dict(frame)
                frame_branch = metadata.get(
                    "policy_branch", metadata.get("branch")
                )
                tick = metadata.get("tick")
                if (
                    metadata.get("scenario_id") != scenario_id
                    or frame_branch != branch
                    or isinstance(tick, bool)
                    or not isinstance(tick, int)
                    or tick not in range(24)
                ):
                    raise ValueError(
                        "Governance v2 resume visual identity drifted"
                    )
                content = cls._visual_bytes(metadata)
                identity = (scenario_id, branch, tick)
                previous = restored.get(identity)
                if previous is not None and (
                    previous[0].get("content_sha256")
                    != metadata.get("content_sha256")
                    or previous[1] != content
                ):
                    raise ValueError(
                        "Governance v2 resume visual frame conflicts"
                    )
                restored[identity] = (metadata, content)
        return list(restored.values())

    def execute(
        self,
        *,
        run_plan: Mapping[str, Any] | FrozenRunPlan,
        launch_contract: Mapping[str, Any],
        checkpoint: ExecutorCheckpoint | None = None,
        cancellation_probe: CancellationProbe | None = None,
        progress_callback: ProgressCallback | None = None,
        artifact_callback: ArtifactCallback | None = None,
        resume_state: Mapping[str, Any] | None = None,
    ) -> ExecutorOutcome:
        plan = (
            run_plan
            if isinstance(run_plan, FrozenRunPlan)
            else validate_frozen_run_plan(run_plan)
        )
        if plan.execution_mode != GOVERNANCE_V2_EXECUTION_MODE:
            raise ValueError(
                "AdaptiveParticlePopulationExecutor requires run-plan-v6"
            )
        launch = validate_launch_contract(
            launch_contract, run_plan=plan
        )
        total_units = required_turn_units(plan)
        if (
            total_units != GOVERNANCE_V2_REQUIRED_UNITS
            or launch["turn_limit"] != total_units
        ):
            raise RuntimeBudgetExceeded(
                "Governance v2 launch must reserve all frozen work units",
                code="turn_budget_exhausted",
            )
        completed = checkpoint.completed_units if checkpoint else 0
        if (
            completed < 0
            or completed > total_units
            or (checkpoint and checkpoint.turns_used != completed)
            or (checkpoint and checkpoint.tokens_used != 0)
        ):
            raise ValueError(
                "Governance v2 runtime checkpoint is inconsistent"
            )
        progress = RuntimeProgress(
            turns_reserved=completed,
            turns_completed=completed,
            tokens_reserved=0,
            tokens_used=0,
            active_units=0,
            peak_concurrency=1 if completed else 0,
            progress_version=completed * 2,
        )
        cancel = cancellation_probe or (lambda: False)
        emit_artifact = artifact_callback or (
            lambda _kind, _payload: None
        )
        restored_visual_frames = self._resume_visual_frames(
            resume_state,
            canonical_seed=int(plan.seeds[0]),
        )
        visual_frames: dict[
            tuple[str, str, int], tuple[dict[str, Any], bytes]
        ] = {}
        for metadata, content in restored_visual_frames:
            visual_frames[
                (
                    str(metadata["scenario_id"]),
                    str(
                        metadata.get(
                            "policy_branch", metadata.get("branch")
                        )
                    ),
                    int(metadata["tick"]),
                )
            ] = (metadata, content)
        artifact_counts = {
            "probe_round": 0,
            "message_batch": 0,
            "decision_round": 0,
            "checkpoint": 0,
            "visual_frame": 0,
            "work_unit_completed": 0,
        }
        durable_completed = self._durable_completed_units(resume_state)
        if durable_completed > total_units:
            raise ValueError(
                "Governance v2 resume frontier exceeds the plan"
            )
        if durable_completed > completed:
            for _index in range(durable_completed - completed):
                progress, reservation = reserve_budget(
                    progress,
                    RuntimeBudgetLimits.from_launch_contract(launch),
                    turn_units=1,
                    token_units=0,
                    concurrent_units=1,
                )
                progress = complete_budget_reservation(
                    progress,
                    RuntimeBudgetLimits.from_launch_contract(launch),
                    reservation,
                    actual_tokens=0,
                )
                completed += 1
            if progress_callback is not None:
                progress_callback(
                    ExecutorProgressUpdate(
                        completed_units=completed,
                        total_units=total_units,
                        checkpoint=self._checkpoint(completed, progress),
                        budget=progress,
                    )
                )
        replay_units_to_skip = max(0, completed - durable_completed)

        def engine_artifact(kind: str, payload: Mapping[str, Any]) -> None:
            nonlocal completed, progress, replay_units_to_skip
            if cancel():
                raise ExecutorCancelled(
                    "Governance v2 cancelled at an artifact boundary",
                    checkpoint=self._checkpoint(completed, progress),
                    budget=progress,
                )
            if kind not in artifact_counts or not isinstance(
                payload, Mapping
            ):
                raise ValueError(
                    "Governance v2 engine emitted an invalid artifact"
                )
            item = dict(payload)
            artifact_counts[kind] += 1
            if kind == "work_unit_completed":
                if replay_units_to_skip:
                    replay_units_to_skip -= 1
                    return
                if completed >= total_units:
                    raise ValueError(
                        "Governance v2 emitted excess work units"
                    )
                progress, reservation = reserve_budget(
                    progress,
                    RuntimeBudgetLimits.from_launch_contract(launch),
                    turn_units=1,
                    token_units=0,
                    concurrent_units=1,
                )
                progress = complete_budget_reservation(
                    progress,
                    RuntimeBudgetLimits.from_launch_contract(launch),
                    reservation,
                    actual_tokens=0,
                )
                completed += 1
                if progress_callback is not None:
                    progress_callback(
                        ExecutorProgressUpdate(
                            completed_units=completed,
                            total_units=total_units,
                            checkpoint=self._checkpoint(
                                completed, progress
                            ),
                            budget=progress,
                        )
                    )
                return
            if kind == "visual_frame":
                content = self._visual_bytes(item)
                identity = (
                    str(item.get("scenario_id")),
                    str(
                        item.get(
                            "policy_branch", item.get("branch")
                        )
                    ),
                    int(item.get("tick", -1)),
                )
                previous = visual_frames.get(identity)
                if previous is not None:
                    if (
                        previous[0].get("content_sha256")
                        != item.get("content_sha256")
                        or previous[1] != content
                    ):
                        raise ValueError(
                            "Governance v2 visual frame conflicts"
                        )
                    return
                visual_frames[identity] = (item, content)
                return
            emit_artifact(kind, item)

        extension = plan.input_snapshot["governance_arena_v2"]
        engine_module = self._engine_module()
        population, graph, writeback = self._load_runtime_inputs()
        writeback_evidence = writeback.get(
            "mode_results", writeback.get("evidence")
        )
        if not isinstance(writeback_evidence, Mapping):
            writeback_evidence = None
        scale_gap_path = self.release_dir / "scale-gap-v1.json"
        if not scale_gap_path.is_file():
            raise RuntimeContractError(
                "Governance v2 Scale-Gap evidence is unavailable",
                code="governance_v2_scale_gap_missing",
            )
        try:
            scale_gap_evidence = json.loads(
                scale_gap_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise RuntimeContractError(
                "Governance v2 Scale-Gap evidence is invalid",
                code="governance_v2_scale_gap_invalid",
            ) from exc
        private_engine_plan = engine_module.build_run_plan_v6(
            population=population,
            graph=graph,
            scenario_ids=tuple(extension["scenario_ids"]),
            seeds=tuple(extension["seeds"]),
            writeback_evidence=writeback_evidence,
            scale_gap_evidence=scale_gap_evidence,
            strict_scale=True,
        )
        if not isinstance(private_engine_plan, Mapping):
            raise ValueError(
                "Governance v2 private engine plan is invalid"
            )
        private_model = private_engine_plan.get("model_release")
        frozen_model = extension["model_release"]
        private_components = (
            private_model.get("components", {})
            if isinstance(private_model, Mapping)
            else {}
        )
        projected_components = {
            kind: {
                "schema_version": descriptor.get("schema_version"),
                "sha256": descriptor.get("sha256"),
            }
            for kind, descriptor in private_components.items()
            if isinstance(descriptor, Mapping)
        }
        if (
            not isinstance(private_model, Mapping)
            or private_model.get("model_sha256")
            != frozen_model["model_sha256"]
            or projected_components != frozen_model["components"]
            or private_engine_plan.get("formal_writeback_mode")
            != extension["writeback"]["formal_mode"]
        ):
            raise ValueError(
                "Governance v2 private engine release identity drifted"
            )
        engine_result = engine_module.run_governance_arena_v2(
            plan=private_engine_plan,
            resume=resume_state,
            artifact_callback=engine_artifact,
            cancellation_probe=cancel,
        )
        if not isinstance(engine_result, Mapping):
            raise ValueError(
                "Governance v2 engine returned an invalid domain result"
            )
        domain_result = {
            key: value
            for key, value in engine_result.items()
            if key != "_private_artifacts"
        }
        if (
            domain_result.get("schema_version")
            != GOVERNANCE_V2_DOMAIN_RESULT_SCHEMA_VERSION
        ):
            raise ValueError(
                "Governance v2 engine returned the wrong domain schema"
            )
        if completed != total_units:
            raise ValueError(
                "Governance v2 engine did not complete all frozen work units"
            )
        validated, manifest, binary = seal_governance_v2_result_v4(
            domain_result,
            list(visual_frames.values()),
            run_plan=plan,
            launch_contract=launch,
        )
        manifest_bytes = canonical_json_bytes(manifest)
        emit_artifact(
            "visual_frames",
            {
                "schema_version": GOVERNANCE_V2_VISUALIZATION_SCHEMA_VERSION,
                "content_sha256": bytes_sha256(binary),
                "content": binary,
                "metadata": validated["visualization_asset"],
            },
        )
        emit_artifact(
            "visual_manifest",
            {
                "schema_version": GOVERNANCE_V2_VISUALIZATION_SCHEMA_VERSION,
                "content_sha256": bytes_sha256(manifest_bytes),
                "content": manifest_bytes,
                "metadata": {
                    "outer_result_sha256": validated["result_sha256"],
                    "domain_result_sha256": validated[
                        "visualization_asset"
                    ]["domain_result_sha256"],
                },
            },
        )
        private_summary = engine_result.get("_private_artifacts")
        if not isinstance(private_summary, Mapping):
            private_summary = {}
        return ExecutorOutcome(
            result=validated,
            checkpoint=self._checkpoint(completed, progress),
            budget=progress,
            private_artifacts={
                "schema_version": (
                    GOVERNANCE_V2_PRIVATE_ARTIFACT_SET_SCHEMA_VERSION
                ),
                "artifact_counts": artifact_counts,
                "engine_index": dict(private_summary),
            },
        )
