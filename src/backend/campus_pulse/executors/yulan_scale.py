"""Fail-closed runtime adapter for the YuLan-Scale v7 domain engine.

The module deliberately keeps result sealing independent from Provider
transport.  Development and reviewed-trace replay can use the same immutable
result/visualization contracts; authorized live execution is installed only
when a separately authorized transport is supplied by the runtime layer.
"""

from __future__ import annotations

import base64
import copy
import importlib
import json
import zlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..llm_population_contract import (
    LLM_POPULATION_ACTOR_SLOTS,
    LLM_POPULATION_BRANCHES,
    LLM_POPULATION_DOMAIN_RESULT_SCHEMA_VERSION,
    LLM_POPULATION_PARTICLE_UPDATES,
    LLM_POPULATION_PROVIDER_REQUEST_LIMIT,
    LLM_POPULATION_PROVIDER_TOKEN_LIMIT,
    LLM_POPULATION_REQUIRED_UNITS,
    LLM_POPULATION_RESIDENT_SLOTS,
    LLM_POPULATION_RESULT_SCHEMA_VERSION,
    LLM_POPULATION_SCENARIOS,
    LLM_POPULATION_SEMANTIC_SLOTS,
    LLM_POPULATION_VISUALIZATION_SCHEMA_VERSION,
    bytes_sha256,
    canonical_json_bytes,
    validate_reviewed_llm_trace_release,
    validate_llm_population_result,
    validate_llm_population_visualization_manifest,
)
from ..runtime import RuntimeProgress
from ..yulan_scale_live import (
    V7LiveFactoryContext,
    require_formal_authorized_source,
)
from ..runtime_contract import (
    FAKE_ADAPTER_MODE,
    FrozenRunPlan,
    RuntimeContractError,
    canonical_sha256,
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


DEVELOPMENT_PROVENANCE = "emulator_only_development"
REPLAY_PROVENANCE = "reviewed_trace_replay"
LIVE_PROVENANCE = "authorized_live_llm"
PRIVATE_ARTIFACT_SCHEMA_VERSION = (
    "campus-pulse-llm-population-runtime-artifacts-v1"
)


class _ExactReplayTransport:
    """Expose a non-acquiring resolver for already committed trace turns."""

    provenance = REPLAY_PROVENANCE

    def __init__(self, source) -> None:
        self._source = source

    def acquire(self, *, slot_kind: str, prompt_sha256: str):
        return self._source.acquire(
            slot_kind=slot_kind,
            prompt_sha256=prompt_sha256,
        )

    def acquire_committed(self, *, slot_kind: str, prompt_sha256: str):
        # This is an immutable in-memory release lookup, not a Provider/cache
        # request.  Keeping it separate lets the engine prove that recovery
        # did not invoke the acquisition path or emit a second attempt.
        return self._source.acquire(
            slot_kind=slot_kind,
            prompt_sha256=prompt_sha256,
        )


def _visual_bytes(frame: Mapping[str, Any]) -> bytes:
    payload = frame.get("payload")
    if not isinstance(payload, str) or frame.get("encoding") != "zlib+base64":
        raise ValueError("YuLan-Scale visual frame encoding is invalid")
    try:
        compressed = base64.b64decode(payload, validate=True)
        content = zlib.decompress(compressed)
    except (ValueError, TypeError, zlib.error) as exc:
        raise ValueError("YuLan-Scale visual frame payload is corrupt") from exc
    if not content or bytes_sha256(content) != frame.get("content_sha256"):
        raise ValueError("YuLan-Scale visual frame hash is invalid")
    return content


def _timeline_projection(
    scenarios: Mapping[str, Any],
    *fields: str,
) -> dict[str, Any]:
    projection: dict[str, Any] = {}
    for scenario_id in LLM_POPULATION_SCENARIOS:
        scenario = scenarios.get(scenario_id)
        if not isinstance(scenario, Mapping):
            raise ValueError("YuLan-Scale scenario result is incomplete")
        projected_branches: dict[str, Any] = {}
        branches = scenario.get("branches")
        if not isinstance(branches, Mapping):
            raise ValueError("YuLan-Scale branch result is incomplete")
        for branch in LLM_POPULATION_BRANCHES:
            branch_result = branches.get(branch)
            timeline = (
                branch_result.get("timeline")
                if isinstance(branch_result, Mapping)
                else None
            )
            if not isinstance(timeline, list) or len(timeline) != 24:
                raise ValueError("YuLan-Scale branch timeline is incomplete")
            projected_branches[branch] = [
                {
                    "tick": point["tick"],
                    "phase": point["phase"],
                    **{
                        field: copy.deepcopy(point[field])
                        for field in fields
                        if field in point
                    },
                }
                for point in timeline
            ]
        projection[scenario_id] = projected_branches
    return projection


def build_public_domain_result_v3(
    engine_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Project engine output into the eight explicitly separated result layers."""

    if (
        engine_result.get("schema_version")
        != LLM_POPULATION_DOMAIN_RESULT_SCHEMA_VERSION
        or engine_result.get("execution_mode")
        != "budgeted_llm_agent_population"
    ):
        raise ValueError("YuLan-Scale domain result identity is invalid")
    scenarios = engine_result.get("scenarios")
    if not isinstance(scenarios, Mapping):
        raise ValueError("YuLan-Scale scenarios are unavailable")

    timeline_aggregates = _timeline_projection(scenarios, "truth")
    governance_visible = _timeline_projection(
        scenarios, "observed", "probe"
    )
    uncertainty = _timeline_projection(
        scenarios, "uncertainty", "seed_interval"
    )
    messaging = _timeline_projection(
        scenarios, "diffusion", "cascade_groups"
    )
    branch_differences: dict[str, Any] = {}
    governance: dict[str, Any] = {}
    for scenario_id in LLM_POPULATION_SCENARIOS:
        scenario = scenarios[scenario_id]
        branch_differences[scenario_id] = copy.deepcopy(
            scenario["policy_comparison"]
        )
        governance[scenario_id] = {
            branch: {
                "game_rounds": copy.deepcopy(
                    scenario["branches"][branch]["game_rounds"]
                ),
                "resource": copy.deepcopy(
                    scenario["branches"][branch]["resource"]
                ),
            }
            for branch in LLM_POPULATION_BRANCHES
        }

    pps_posteriors = engine_result.get("pps_posteriors", [])
    if not isinstance(pps_posteriors, list):
        raise ValueError("YuLan-Scale PPS posterior section is invalid")
    speaker_rounds = [
        {
            "scenario_id": row["scenario_id"],
            "seed": row["seed"],
            "branch": row["branch"],
            "tick": row["tick"],
            "conditional_opinion": copy.deepcopy(row["partial_pooling"]),
        }
        for row in pps_posteriors
    ]
    speaker_opinion = {
        "status": (
            "estimated_from_rotating_pps_llm_turns"
            if speaker_rounds
            else "not_observed_in_emulator_only_development"
        ),
        "unobserved_agents_are_missing_not_zero": True,
        "rounds": speaker_rounds,
    }
    observed_posterior = {
        "governance_visible_by_tick": governance_visible,
        "pps_population_rounds": [
            {
                "scenario_id": row["scenario_id"],
                "seed": row["seed"],
                "branch": row["branch"],
                "tick": row["tick"],
                "budget": row["budget"],
                "kish_ess": row["kish_ess"],
                "inclusion_probability_range": copy.deepcopy(
                    row["inclusion_probability_range"]
                ),
                "corrected": copy.deepcopy(row["corrected"]),
            }
            for row in pps_posteriors
        ],
    }
    anchor_trajectories = copy.deepcopy(
        engine_result.get(
            "anchor_trajectories",
            {
                "status": "semantic_anchor_trajectory_unavailable",
                "identity_public": False,
                "trajectory_count": 0,
            },
        )
    )
    emulator_residuals = copy.deepcopy(
        engine_result.get(
            "emulator_residuals",
            {
                "status": "no_llm_observations_in_development_run",
                "observed_turn_count": 0,
            },
        )
    )
    usage = engine_result.get("usage")
    if not isinstance(usage, Mapping):
        raise ValueError("YuLan-Scale usage is unavailable")
    return {
        "schema_version": LLM_POPULATION_DOMAIN_RESULT_SCHEMA_VERSION,
        "population_identity": copy.deepcopy(
            engine_result["population_identity"]
        ),
        "timeline_aggregates": timeline_aggregates,
        "speaker_opinion": speaker_opinion,
        "observed_posterior": observed_posterior,
        "anchor_trajectories": anchor_trajectories,
        "emulator_residuals": emulator_residuals,
        "branch_differences": branch_differences,
        "uncertainty": uncertainty,
        "messaging": messaging,
        "governance": governance,
        "audit": {
            "execution_provenance": engine_result["execution_provenance"],
            "resident_slots_planned": int(
                usage.get("resident_slots_planned", 0)
            ),
            "governance_slots_planned": int(
                usage.get("governance_actor_slots_planned", 0)
            ),
            "provider_calls": int(usage.get("provider_calls", 0)),
            "provider_tokens": int(usage.get("provider_tokens", 0)),
            "fixture_turns": int(usage.get("fixture_turns", 0)),
            "real_governance_actions": 0,
            "micro_identity_public": False,
            "selected_identity_public": False,
            "record_level_edges_public": False,
            "unreviewed_free_text_public": False,
        },
    }


def _normalize_visual_frames(
    frames: Sequence[Mapping[str, Any]],
) -> list[tuple[dict[str, Any], bytes]]:
    scenario_order = {
        value: index for index, value in enumerate(LLM_POPULATION_SCENARIOS)
    }
    branch_order = {
        value: index for index, value in enumerate(LLM_POPULATION_BRANCHES)
    }
    normalized: list[tuple[dict[str, Any], bytes]] = []
    identities: set[tuple[str, str, int]] = set()
    for raw in frames:
        if not isinstance(raw, Mapping):
            raise ValueError("YuLan-Scale visual frame is invalid")
        frame = dict(raw)
        scenario_id = frame.get("scenario_id")
        branch = frame.get("branch", frame.get("policy_branch"))
        tick = frame.get("tick")
        identity = (str(scenario_id), str(branch), int(tick))
        if (
            scenario_id not in scenario_order
            or branch not in branch_order
            or isinstance(tick, bool)
            or not isinstance(tick, int)
            or not 0 <= tick < 24
            or identity in identities
        ):
            raise ValueError("YuLan-Scale visual frame identity is invalid")
        identities.add(identity)
        normalized.append((frame, _visual_bytes(frame)))
    if len(normalized) != 240:
        raise ValueError("YuLan-Scale requires 240 canonical display frames")
    normalized.sort(
        key=lambda item: (
            scenario_order[item[0]["scenario_id"]],
            branch_order[
                item[0].get("branch", item[0].get("policy_branch"))
            ],
            int(item[0]["tick"]),
        )
    )
    return normalized


def seal_yulan_scale_result_v5(
    engine_result: Mapping[str, Any],
    *,
    run_plan: Mapping[str, Any] | FrozenRunPlan,
    launch_contract: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    """Seal result-v5 plus a hash-checked 240-frame public visualization."""

    plan = (
        run_plan
        if isinstance(run_plan, FrozenRunPlan)
        else validate_frozen_run_plan(run_plan)
    )
    launch = validate_launch_contract(launch_contract, run_plan=plan)
    extension = plan.input_snapshot["llm_agent_population"]
    provenance = extension["execution_provenance"]
    if provenance not in {
        DEVELOPMENT_PROVENANCE,
        REPLAY_PROVENANCE,
        LIVE_PROVENANCE,
    }:
        raise ValueError("YuLan-Scale result provenance is unsupported")
    if engine_result.get("execution_provenance") != provenance:
        raise ValueError("YuLan-Scale execution provenance drifted")

    domain = build_public_domain_result_v3(engine_result)
    visual = engine_result.get("visualization")
    if not isinstance(visual, Mapping):
        raise ValueError("YuLan-Scale visualization is unavailable")
    canonical_seed = visual.get("canonical_seed")
    normalized = _normalize_visual_frames(visual.get("frames", ()))
    stride_values = {
        int(item[0].get("stride", item[0].get("record_stride_bytes", 0)))
        for item in normalized
    }
    if len(stride_values) != 1 or next(iter(stride_values)) <= 0:
        raise ValueError("YuLan-Scale visual record stride is invalid")
    stride = next(iter(stride_values))
    if any(len(content) % stride for _frame, content in normalized):
        raise ValueError("YuLan-Scale visual bytes are not stride aligned")

    binary = b"".join(content for _frame, content in normalized)
    binary_sha = bytes_sha256(binary)
    domain_sha = canonical_sha256(domain)
    offset = 0
    frame_index: list[dict[str, Any]] = []
    for index, (frame, content) in enumerate(normalized):
        branch = frame.get("branch", frame.get("policy_branch"))
        frame_index.append(
            {
                "index": index,
                "scenario_id": frame["scenario_id"],
                "policy_branch": branch,
                "tick": int(frame["tick"]),
                "offset": offset,
                "length": len(content),
            }
        )
        offset += len(content)
    manifest_core = {
        "schema_version": LLM_POPULATION_VISUALIZATION_SCHEMA_VERSION,
        "run_id": plan.run_id,
        "domain_result_sha256": domain_sha,
        "binary_sha256": binary_sha,
        "byte_length": len(binary),
        "record_stride_bytes": stride,
        "frame_count": len(frame_index),
        "canonical_seed": canonical_seed,
        "frames": frame_index,
    }
    # Hash the stable manifest body.  outer_result_sha256 is intentionally
    # excluded because the outer result itself binds this fingerprint.
    manifest_sha = canonical_sha256(manifest_core)

    usage_source = engine_result.get("usage", {})
    if provenance == DEVELOPMENT_PROVENANCE:
        resident_turns = actor_turns = replay_turns = semantic_slots = 0
        publication_eligible = False
    else:
        resident_turns = int(
            usage_source.get("resident_llm_turns_completed", -1)
        )
        actor_turns = int(
            usage_source.get("governance_actor_llm_turns_completed", -1)
        )
        replay_turns = int(usage_source.get("trace_replay_turns", -1))
        semantic_slots = resident_turns + actor_turns
        publication_eligible = bool(engine_result.get("publication_eligible"))
    live_provider_calls = (
        int(usage_source.get("provider_calls", 0))
        if provenance == LIVE_PROVENANCE
        else 0
    )
    provider_tokens = (
        int(usage_source.get("provider_tokens", 0))
        if provenance == LIVE_PROVENANCE
        else 0
    )
    cache_hits = (
        int(usage_source.get("cache_hits", 0))
        if provenance == LIVE_PROVENANCE
        else 0
    )
    body = {
        "schema_version": LLM_POPULATION_RESULT_SCHEMA_VERSION,
        "run_id": plan.run_id,
        "launch_fingerprint": launch["launch_fingerprint"],
        "result_kind": "budgeted_llm_agent_population_simulation",
        "plan_sha256": extension["plan_sha256"],
        "execution_provenance": provenance,
        "publication_eligible": publication_eligible,
        "domain_result": domain,
        "visualization_asset": {
            "schema_version": LLM_POPULATION_VISUALIZATION_SCHEMA_VERSION,
            "manifest_sha256": manifest_sha,
            "binary_sha256": binary_sha,
            "byte_length": len(binary),
            "frame_count": len(frame_index),
            "record_stride_bytes": stride,
            "domain_result_sha256": domain_sha,
        },
        "completeness": {
            "status": "complete",
            "completed_work_units": LLM_POPULATION_REQUIRED_UNITS,
            "required_work_units": LLM_POPULATION_REQUIRED_UNITS,
            "completed_semantic_slots": semantic_slots,
            "required_semantic_slots": LLM_POPULATION_SEMANTIC_SLOTS,
        },
        "budget_usage": {
            "provider_request_limit": LLM_POPULATION_PROVIDER_REQUEST_LIMIT,
            "provider_requests_used": live_provider_calls,
            "provider_token_limit": LLM_POPULATION_PROVIDER_TOKEN_LIMIT,
            "provider_tokens_used": provider_tokens,
            "effective_concurrency_limit": launch[
                "effective_concurrency_limit"
            ],
            "peak_concurrency": 1,
        },
        "usage": {
            "resident_llm_turns": resident_turns,
            "governance_llm_turns": actor_turns,
            "reviewed_trace_replays": replay_turns,
            "live_provider_calls": live_provider_calls,
            "cache_hits": cache_hits,
            "provider_tokens": provider_tokens,
            "emulator_updates": LLM_POPULATION_PARTICLE_UPDATES,
            "real_governance_actions": 0,
        },
        "privacy": {
            "raw_text_included": False,
            "source_identifiers_included": False,
            "record_level_edges_included": False,
            "sample_identities_included": False,
            "parent_particle_mapping_included": False,
            "provider_secrets_included": False,
            "runtime_paths_included": False,
            "private_actor_observations_included": False,
            "unreviewed_free_text_included": False,
        },
        "non_claims": [
            "The 1,000 agents are episode-grounded synthetic LLM agents, not identified students.",
            "Budgeted activation does not mean every agent invokes a Provider at every tick.",
            "Unactivated semantic outcomes are emulator predictions, not LLM statements.",
            "Particles are uncertainty hypotheses, not agents or evidence subjects.",
            "The exposure graph and propagation timing are synthetic mechanisms.",
            "Particle, PPS, and paired-seed intervals have different meanings.",
            "Policy differences are model-conditional, not causal policy effects.",
            "No real governance action was executed.",
        ],
    }
    sealed = {**body, "result_sha256": canonical_sha256(body)}
    manifest = {
        **manifest_core,
        "outer_result_sha256": sealed["result_sha256"],
    }
    validated = validate_llm_population_result(
        sealed,
        run_plan=plan,
        launch_contract=launch,
    )
    validate_llm_population_visualization_manifest(
        manifest,
        run_id=plan.run_id,
        result_sha256=sealed["result_sha256"],
        domain_result_sha256=domain_sha,
        binary_sha256=binary_sha,
        byte_length=len(binary),
    )
    return validated, manifest, binary


class BudgetedLLMAgentPopulationExecutor:
    """Run the unified 1,000-agent engine without owning Provider transport.

    Development and replay are network-free.  Authorized live execution is
    accepted only through an explicitly injected, consumed-authorization
    source factory; the default constructor has no Provider capability and
    fails closed.  The executor binds the immutable release before the first
    domain tick, persists complete domain checkpoints as they are emitted,
    and seals visualization/result artifacts only after all 1,728 work units
    complete.

    Recovery validates the hash-bound global cursor, reuses completed
    scenario/seed/branch shards, resumes only after each branch's latest
    checkpoint, and deterministically rebuilds the paired aggregate and
    canonical visualization order.
    """

    adapter_mode = FAKE_ADAPTER_MODE
    network_enabled = False
    provider_enabled = False

    _POPULATION_FILE = "semantic-llm-agent-population-v1.json"
    _GRAPH_FILE = "synthetic-exposure-graph-v1.json"
    _EMULATOR_FILE = "llm-behavior-emulator-v1.json"
    _EMULATOR_VALIDATION_FILE = "llm-behavior-emulator-validation-v1.json"
    _MODEL_FILE = "model-release.json"
    _TRACE_FILE = "reviewed-trace-release.json"

    def __init__(
        self,
        release_dir: str | Path,
        *,
        authorized_live_source_factory=None,
    ):
        self.release_dir = Path(release_dir)
        if (
            authorized_live_source_factory is not None
            and not callable(authorized_live_source_factory)
        ):
            raise TypeError("authorized_live_source_factory must be callable")
        self._authorized_live_source_factory = (
            authorized_live_source_factory
        )
        # The provider-free worker rejects executors exposing either flag.
        # An injected live source therefore cannot be smuggled through the
        # ordinary fake-adapter worker even though the domain API is shared.
        self.network_enabled = authorized_live_source_factory is not None
        self.provider_enabled = authorized_live_source_factory is not None

    @staticmethod
    def _live_factory_context(
        *,
        plan: FrozenRunPlan,
        launch: Mapping[str, Any],
        extension: Mapping[str, Any],
    ) -> V7LiveFactoryContext:
        provider = extension["provider_profile"]
        return V7LiveFactoryContext(
            run_id=plan.run_id,
            project_id=plan.project_id,
            input_fingerprint=plan.input_fingerprint,
            launch_fingerprint=launch["launch_fingerprint"],
            plan_sha256=extension["plan_sha256"],
            model_name=provider["model_name"],
            endpoint_fingerprint=provider["endpoint_fingerprint"],
            reasoning_effort="none",
            provider_request_limit=provider["provider_request_limit"],
            provider_token_limit=provider["provider_token_limit"],
            max_concurrency=provider["max_concurrency"],
            network_retry_limit=provider["network_retry_limit"],
            format_repair_limit=provider["format_repair_limit"],
        )

    @staticmethod
    def _engine_module():
        module = importlib.import_module(
            "envs.campus_pulse_governance.yulan_scale_engine"
        )
        if not callable(getattr(module, "run_yulan_scale_engine", None)):
            raise RuntimeError("YuLan-Scale domain engine is unavailable")
        return module

    @staticmethod
    def _turn_module():
        module = importlib.import_module(
            "envs.campus_pulse_governance.yulan_scale"
        )
        if not callable(getattr(module, "ReviewedTraceReplaySource", None)):
            raise RuntimeError("YuLan-Scale turn replay adapter is unavailable")
        return module

    @staticmethod
    def _population_module():
        module = importlib.import_module(
            "envs.campus_pulse_governance.llm_population"
        )
        if not callable(getattr(module, "predict_llm_behavior", None)):
            raise RuntimeError("LLM behavior emulator API is unavailable")
        return module

    def _read_json(self, name: str) -> dict[str, Any]:
        try:
            value = json.loads(
                (self.release_dir / name).read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"YuLan-Scale release asset is unavailable: {name}"
            ) from exc
        if not isinstance(value, dict):
            raise RuntimeError(
                f"YuLan-Scale release asset is invalid: {name}"
            )
        return value

    @staticmethod
    def _sha_field(
        value: Mapping[str, Any],
        *names: str,
    ) -> str | None:
        for name in names:
            candidate = value.get(name)
            if (
                isinstance(candidate, str)
                and len(candidate) == 64
                and all(character in "0123456789abcdef" for character in candidate)
            ):
                return candidate
        return None

    def _load_runtime_inputs(
        self,
        *,
        extension: Mapping[str, Any],
        provenance: str,
    ) -> dict[str, Any]:
        population = self._read_json(self._POPULATION_FILE)
        graph = self._read_json(self._GRAPH_FILE)
        emulator = self._read_json(self._EMULATOR_FILE)
        validation = self._read_json(self._EMULATOR_VALIDATION_FILE)
        model_manifest = self._read_json(self._MODEL_FILE)

        frozen_model = extension["model_release"]
        expected_manifest = {
            "model_sha256": frozen_model["model_sha256"],
            "components": frozen_model["components"],
        }
        if model_manifest != expected_manifest:
            raise RuntimeContractError(
                "YuLan-Scale model release identity drifted",
                code="llm_population_release_drift",
            )
        component_hashes = {
            "population": self._sha_field(
                population, "population_sha256", "release_sha256"
            ),
            "behavior_emulator": self._sha_field(
                emulator, "emulator_sha256", "release_sha256"
            ),
        }
        for kind, actual_sha in component_hashes.items():
            if (
                actual_sha is None
                or frozen_model["components"][kind]["sha256"] != actual_sha
            ):
                raise RuntimeContractError(
                    f"YuLan-Scale {kind} release drifted",
                    code="llm_population_release_drift",
                )
        source_graph_sha = self._sha_field(
            graph, "graph_sha256", "release_sha256"
        )
        compiled_graph_component_sha = canonical_sha256(
            {
                "source_graph_sha256": source_graph_sha,
                "compiler": "semantic-neighbor-8-bridge-4-v2",
            }
        )
        if (
            source_graph_sha is None
            or frozen_model["components"]["exposure_graph"]["sha256"]
            != compiled_graph_component_sha
        ):
            raise RuntimeContractError(
                "YuLan-Scale exposure_graph release drifted",
                code="llm_population_release_drift",
            )
        if (
            population.get("schema_version")
            != "semantic-llm-agent-population-v1"
            or population.get("population_size") != 1_000
            or not isinstance(population.get("agents"), list)
            or len(population["agents"]) != 1_000
            or extension["population"]["population_sha256"]
            != population.get("population_sha256")
        ):
            raise RuntimeContractError(
                "YuLan-Scale population release is invalid",
                code="llm_population_release_invalid",
            )
        if (
            validation.get("schema_version")
            != "llm-behavior-emulator-validation-v1"
            or validation.get("passed") is not True
            or validation.get("publication_eligible") is not True
        ):
            raise RuntimeContractError(
                "YuLan-Scale emulator has not passed its frozen gate",
                code="llm_population_emulator_gate_failed",
            )
        emulator_for_engine = copy.deepcopy(emulator)
        emulator_for_engine["validation_gate"] = {
            "status": "passed",
            "passed": True,
            "validation_sha256": validation.get("validation_sha256"),
        }

        trace_release = None
        semantic_source = None
        if provenance == REPLAY_PROVENANCE:
            try:
                trace_release = validate_reviewed_llm_trace_release(
                    self._read_json(self._TRACE_FILE)
                )
            except RuntimeContractError:
                raise
            if (
                trace_release["population_sha256"]
                != population["population_sha256"]
                or trace_release["model_release_sha256"]
                != frozen_model["model_sha256"]
                or trace_release["run_plan_sha256"]
                != extension["plan_sha256"]
                or trace_release["renderer_sha256"]
                != frozen_model["components"]["renderer"]["sha256"]
                or trace_release["model_name"]
                != extension["provider_profile"]["model_name"]
            ):
                raise RuntimeContractError(
                    "Reviewed trace release does not bind the frozen run",
                    code="llm_population_trace_binding_drift",
                )
            semantic_source = _ExactReplayTransport(
                self._turn_module().ReviewedTraceReplaySource(
                    trace_release["entries"],
                    expected_release_sha256=trace_release["release_sha256"],
                )
            )
        return {
            "population": population,
            "graph": graph,
            "emulator": emulator_for_engine,
            "model_release": copy.deepcopy(frozen_model),
            "trace_release": trace_release,
            "semantic_source": semantic_source,
        }

    @staticmethod
    def _emulator_predictor(
        emulator_release: Mapping[str, Any],
    ):
        """Adapt the compiler's batch API to the engine's SoA prediction API."""

        population_module = (
            BudgetedLLMAgentPopulationExecutor._population_module()
        )
        prediction_cache: dict[
            tuple[str, str, str, str], dict[str, Any]
        ] = {}

        def predict(
            *,
            population: Mapping[str, Any],
            state,
            features: Mapping[str, Any],
            scenario_id: str,
            branch: str,
            tick: int,
        ) -> dict[str, Any]:
            del features
            import numpy as np

            parent = state.ensemble.parent_means()
            phase = (
                "baseline"
                if tick <= 2
                else "burst"
                if tick <= 7
                else "spread"
                if tick <= 17
                else "decay"
            )
            model_scenario = (
                "tongzhou_governance_information_shock"
                if scenario_id == "governance_legitimacy_dispute"
                else scenario_id
            )
            model_policy = (
                "shared_baseline"
                if branch == "shared_baseline"
                else "baseline"
                if branch == "natural"
                else "combined"
            )
            cache_key = (
                str(population.get("population_sha256") or ""),
                model_scenario,
                phase,
                model_policy,
            )
            base = prediction_cache.get(cache_key)
            if base is None:
                population_count = len(population["agents"])
                rows = []
                for agent in population["agents"]:
                    scenario_weights = agent.get("scenario_weights", {})
                    scenario_weight = (
                        scenario_weights.get(scenario_id)
                        if isinstance(scenario_weights, Mapping)
                        else None
                    )
                    # Historical decisions came from a 48-role LLM panel.
                    # Preserve the relative scenario design weight while
                    # expressing it on that training scale.
                    scaled_weight = (
                        float(scenario_weight)
                        * population_count
                        / 48.0
                        if scenario_weight is not None
                        else float(agent.get("population_weight", 0.0))
                        * population_count
                        / 48.0
                    )
                    rows.append(
                        {
                            "archetype_id": agent.get(
                                "primary_archetype_id"
                            ),
                            "persona_variant_id": agent.get(
                                "persona_variant_id"
                            ),
                            "scenario_id": model_scenario,
                            "phase_id": phase,
                            "policy_id": model_policy,
                            "semantic_contract_id": (
                                population_module.BUDGETED_SEMANTIC_CONTRACT_ID
                            ),
                            "population_weight": scaled_weight,
                        }
                    )
                predicted = population_module.predict_llm_behavior(
                    emulator_release,
                    rows,
                )
                if len(predicted) != len(rows):
                    raise RuntimeError(
                        "LLM behavior emulator prediction count drifted"
                    )

                def categorical(source_key: str) -> dict[str, Any]:
                    labels = sorted(
                        {
                            str(label)
                            for row in predicted
                            for label in row[source_key]
                        }
                    )
                    return {
                        label: np.asarray(
                            [
                                float(row[source_key].get(label, 0.0))
                                for row in predicted
                            ],
                            dtype=np.float64,
                        )
                        for label in labels
                    }

                deltas = [
                    row.get("continuous_deltas", {})
                    for row in predicted
                ]
                base = {
                    "action": categorical("action_probabilities"),
                    "stance": categorical("stance_probabilities"),
                    "topic_id": categorical("topic_probabilities"),
                    "urgency_delta": np.asarray(
                        [
                            float(value.get("urgency", 0.0))
                            for value in deltas
                        ]
                    ),
                    "trust_delta": np.asarray(
                        [
                            float(value.get("trust_delta", 0.0))
                            for value in deltas
                        ]
                    ),
                    "satisfaction_target": np.asarray(
                        [
                            float(value.get("satisfaction", 0.0))
                            for value in deltas
                        ]
                    ),
                }
                prediction_cache[cache_key] = base

            field_names = (
                "attention",
                "concern",
                "trust",
                "satisfaction",
                "support",
                "opposition",
                "activity",
            )
            positions = {name: index for index, name in enumerate(field_names)}
            urgency_delta = base["urgency_delta"]
            trust_delta = base["trust_delta"]
            satisfaction_target = base["satisfaction_target"]
            concern = np.clip(
                parent[:, positions["concern"]] + 0.10 * urgency_delta,
                0.0,
                1.0,
            )
            trust = np.clip(
                parent[:, positions["trust"]] + trust_delta,
                0.0,
                1.0,
            )
            satisfaction = np.clip(
                0.80 * parent[:, positions["satisfaction"]]
                + 0.20 * satisfaction_target,
                0.0,
                1.0,
            )
            activity = np.clip(
                parent[:, positions["activity"]]
                + 0.05 * np.abs(urgency_delta),
                0.0,
                1.0,
            )
            return {
                "action": base["action"],
                "stance": base["stance"],
                "topic_id": base["topic_id"],
                "numeric": {
                    "urgency": concern,
                    "concern": concern,
                    "trust": trust,
                    "satisfaction": satisfaction,
                    "activity": activity,
                },
            }

        return predict

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
    def _progress(completed_units: int) -> RuntimeProgress:
        return RuntimeProgress(
            turns_reserved=completed_units,
            turns_completed=completed_units,
            tokens_reserved=0,
            tokens_used=0,
            active_units=0,
            peak_concurrency=1 if completed_units else 0,
            progress_version=completed_units * 2,
        )

    @staticmethod
    def _public_engine_result(
        engine_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Remove private state and normalize engine branch ids to v7 ids."""

        value = {
            key: copy.deepcopy(item)
            for key, item in engine_result.items()
            if not str(key).startswith("_")
        }
        branch_names = {"a": "A", "b": "B", "c": "C", "d": "D"}
        scenarios = value.get("scenarios")
        if isinstance(scenarios, Mapping):
            for scenario in scenarios.values():
                branches = (
                    scenario.get("branches")
                    if isinstance(scenario, Mapping)
                    else None
                )
                if not isinstance(branches, Mapping):
                    continue
                scenario["branches"] = {
                    branch_names.get(str(branch), str(branch)): result
                    for branch, result in branches.items()
                }
        visual = value.get("visualization")
        frames = visual.get("frames") if isinstance(visual, Mapping) else None
        if isinstance(frames, list):
            for frame in frames:
                if isinstance(frame, dict):
                    key = "branch" if "branch" in frame else "policy_branch"
                    frame[key] = branch_names.get(
                        str(frame.get(key)), str(frame.get(key))
                    )
        return value

    @staticmethod
    def _resume_frontier(
        checkpoint: ExecutorCheckpoint | None,
        resume_state: Mapping[str, Any] | None,
        *,
        canonical_seed: int,
    ) -> int:
        completed = checkpoint.completed_units if checkpoint else 0
        if (
            completed < 0
            or completed > LLM_POPULATION_REQUIRED_UNITS
            or (
                checkpoint is not None
                and (
                    checkpoint.turns_used != completed
                    or checkpoint.tokens_used != 0
                )
            )
        ):
            raise ValueError("YuLan-Scale runtime checkpoint is inconsistent")
        committed = (
            resume_state.get("checkpoints", ())
            if isinstance(resume_state, Mapping)
            else ()
        )
        if not isinstance(committed, (list, tuple)):
            raise ValueError("YuLan-Scale resume checkpoint set is invalid")
        durable_completed = completed
        for item in committed:
            if not isinstance(item, Mapping):
                raise ValueError("YuLan-Scale resume checkpoint is invalid")
            body = {
                key: value
                for key, value in item.items()
                if key != "checkpoint_sha256"
            }
            if item.get("checkpoint_sha256") != canonical_sha256(body):
                raise ValueError(
                    "YuLan-Scale resume checkpoint hash drifted"
                )
            cursor = item.get("aggregate_cursor")
            if not isinstance(cursor, Mapping):
                raise ValueError(
                    "YuLan-Scale checkpoint lacks aggregate cursor"
                )
            cursor_body = {
                key: value
                for key, value in cursor.items()
                if key != "cursor_sha256"
            }
            if (
                cursor.get("cursor_sha256")
                != canonical_sha256(cursor_body)
                or cursor.get("scenario_id") != item.get("scenario_id")
                or cursor.get("seed") != item.get("seed")
                or cursor.get("branch") != item.get("branch")
                or cursor.get("tick") != item.get("tick")
            ):
                raise ValueError(
                    "YuLan-Scale aggregate cursor hash drifted"
                )
            cursor_completed = cursor.get("completed_units")
            if (
                isinstance(cursor_completed, bool)
                or not isinstance(cursor_completed, int)
                or not 0
                < cursor_completed
                <= LLM_POPULATION_REQUIRED_UNITS
            ):
                raise ValueError(
                    "YuLan-Scale aggregate cursor progress is invalid"
                )
            durable_completed = max(
                durable_completed,
                cursor_completed,
            )
            frames = item.get("visual_frame_prefix", ())
            if not isinstance(frames, list):
                raise ValueError(
                    "YuLan-Scale checkpoint visual prefix is invalid"
                )
            seed = item.get("seed")
            if seed != canonical_seed and frames:
                raise ValueError(
                    "Non-canonical checkpoint exposed visual frames"
                )
            if seed == canonical_seed:
                previous_tick = -1
                for frame in frames:
                    if not isinstance(frame, Mapping):
                        raise ValueError(
                            "YuLan-Scale visual prefix frame is invalid"
                        )
                    frame_tick = frame.get("tick")
                    if (
                        isinstance(frame_tick, bool)
                        or not isinstance(frame_tick, int)
                        or frame_tick != previous_tick + 1
                    ):
                        raise ValueError(
                            "YuLan-Scale visual prefix is not contiguous"
                        )
                    _visual_bytes(frame)
                    previous_tick = frame_tick
                if frames and previous_tick != int(item["tick"]):
                    raise ValueError(
                        "YuLan-Scale visual prefix frontier drifted"
                    )
        return durable_completed

    @staticmethod
    def _committed_semantic_turns(
        resume_state: Mapping[str, Any] | None,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        rows = (
            resume_state.get("committed_semantic_turns", ())
            if isinstance(resume_state, Mapping)
            else ()
        )
        if not isinstance(rows, (list, tuple)):
            raise ValueError(
                "YuLan-Scale committed semantic frontier is invalid"
            )
        result: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError(
                    "YuLan-Scale committed semantic turn is invalid"
                )
            slot_kind = str(row.get("slot_kind") or "")
            prompt = str(
                row.get("prompt_fingerprint")
                or row.get("prompt_sha256")
                or ""
            )
            if (
                slot_kind not in {"resident", "governance_actor"}
                or len(prompt) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in prompt
                )
            ):
                raise ValueError(
                    "YuLan-Scale committed semantic identity is invalid"
                )
            decision = row.get("validated_decision")
            if not isinstance(decision, Mapping):
                raise ValueError(
                    "YuLan-Scale committed semantic decision is unavailable"
                )
            result[(slot_kind, prompt)] = copy.deepcopy(dict(row))
        if isinstance(resume_state, Mapping):
            slot_index: dict[str, tuple[str, str]] = {}
            for section in ("activation_rounds", "actor_rounds"):
                rounds = resume_state.get(section, ())
                if not isinstance(rounds, (list, tuple)):
                    raise ValueError(
                        "YuLan-Scale committed round frontier is invalid"
                    )
                for round_ in rounds:
                    if not isinstance(round_, Mapping):
                        raise ValueError(
                            "YuLan-Scale committed round is invalid"
                        )
                    for slot in round_.get("slots", ()):
                        if not isinstance(slot, Mapping):
                            raise ValueError(
                                "YuLan-Scale committed slot is invalid"
                            )
                        stored_kind = str(slot.get("slot_kind") or "")
                        slot_kind = (
                            "governance_actor"
                            if stored_kind == "governance_actor"
                            else "resident"
                        )
                        slot_id = str(slot.get("slot_id") or "")
                        prompt = str(
                            slot.get("prompt_fingerprint")
                            or slot.get("prompt_sha256")
                            or ""
                        )
                        if slot_id and len(prompt) == 64:
                            slot_index[slot_id] = (slot_kind, prompt)
            attempts = resume_state.get("attempts", ())
            if not isinstance(attempts, (list, tuple)):
                raise ValueError(
                    "YuLan-Scale committed attempt frontier is invalid"
                )
            accounting: dict[tuple[str, str], dict[str, int]] = {}
            for attempt in attempts:
                if not isinstance(attempt, Mapping):
                    raise ValueError(
                        "YuLan-Scale committed attempt is invalid"
                    )
                identity = slot_index.get(str(attempt.get("slot_id") or ""))
                if identity is None:
                    continue
                row = accounting.setdefault(
                    identity,
                    {
                        "provider_call_count": 0,
                        "provider_attempt_count": 0,
                        "format_repair_count": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                    },
                )
                if attempt.get("transport_kind") == "authorized_provider":
                    row["provider_call_count"] += 1
                    row["provider_attempt_count"] += 1
                    row["input_tokens"] += int(
                        attempt.get("input_tokens", 0)
                    )
                    row["output_tokens"] += int(
                        attempt.get("output_tokens", 0)
                    )
                    if int(attempt.get("attempt_ordinal", 0)) == 1:
                        row["format_repair_count"] = 1
            for attempt in attempts:
                if (
                    not isinstance(attempt, Mapping)
                    or attempt.get("status") != "succeeded"
                ):
                    continue
                identity = slot_index.get(str(attempt.get("slot_id") or ""))
                if identity is not None:
                    decision = attempt.get("validated_decision")
                    response = attempt.get("response_fingerprint")
                    expected_schema = (
                        "campus-pulse-governance-actor-turn-v1"
                        if identity[0] == "governance_actor"
                        else "campus-pulse-budgeted-llm-agent-turn-v1"
                    )
                    if (
                        not isinstance(decision, Mapping)
                        or decision.get("schema_version")
                        != expected_schema
                        or not isinstance(response, str)
                        or len(response) != 64
                    ):
                        raise ValueError(
                            "YuLan-Scale succeeded attempt lacks a "
                            "validated structured decision"
                        )
                    usage = accounting.get(identity, {})
                    provider_call_count = int(
                        usage.get("provider_call_count", 0)
                    )
                    record = {
                        "validated_decision": copy.deepcopy(dict(decision)),
                        "response_fingerprint": response,
                        "input_tokens": int(
                            usage.get("input_tokens", 0)
                            if provider_call_count
                            else attempt.get("input_tokens", 0)
                        ),
                        "output_tokens": int(
                            usage.get("output_tokens", 0)
                            if provider_call_count
                            else attempt.get("output_tokens", 0)
                        ),
                        "transport_kind": attempt.get("transport_kind"),
                    }
                    if provider_call_count:
                        record.update(
                            {
                                "provider_call_count": provider_call_count,
                                "provider_attempt_count": int(
                                    usage.get("provider_attempt_count", 0)
                                ),
                                "format_repair_count": int(
                                    usage.get("format_repair_count", 0)
                                ),
                            }
                        )
                    existing = result.get(identity)
                    if existing is not None and existing != record:
                        raise ValueError(
                            "YuLan-Scale committed semantic decision conflicts"
                        )
                    result[identity] = record
        return result

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
        if plan.execution_mode != "budgeted_llm_agent_population":
            raise ValueError(
                "BudgetedLLMAgentPopulationExecutor requires run-plan-v7"
            )
        launch = validate_launch_contract(
            launch_contract,
            run_plan=plan,
        )
        if launch["turn_limit"] != LLM_POPULATION_REQUIRED_UNITS:
            raise RuntimeContractError(
                "YuLan-Scale launch must reserve all 1,728 work units",
                code="turn_budget_exhausted",
            )
        extension = plan.input_snapshot["llm_agent_population"]
        durable_completed = self._resume_frontier(
            checkpoint,
            resume_state,
            canonical_seed=int(extension["seeds"][0]),
        )
        resume_checkpoints = (
            resume_state.get("checkpoints", ())
            if isinstance(resume_state, Mapping)
            else ()
        )
        if not isinstance(resume_checkpoints, (list, tuple)):
            raise ValueError("YuLan-Scale resume checkpoint set is invalid")
        committed_semantic_turns = self._committed_semantic_turns(
            resume_state
        )
        provenance = extension["execution_provenance"]
        if provenance not in {
            DEVELOPMENT_PROVENANCE,
            REPLAY_PROVENANCE,
            LIVE_PROVENANCE,
        }:
            raise ValueError("YuLan-Scale execution provenance is unsupported")
        if (
            provenance == LIVE_PROVENANCE
            and self._authorized_live_source_factory is None
        ):
            raise RuntimeContractError(
                "Authorized live LLM requires an explicitly injected "
                "consumed-authorization source factory",
                code="authorized_live_transport_unavailable",
            )

        runtime_inputs = self._load_runtime_inputs(
            extension=extension,
            provenance=provenance,
        )
        if provenance == LIVE_PROVENANCE:
            source = self._authorized_live_source_factory(
                self._live_factory_context(
                    plan=plan,
                    launch=launch,
                    extension=extension,
                )
            )
            runtime_inputs["semantic_source"] = (
                require_formal_authorized_source(source)
            )
        cancel = cancellation_probe or (lambda: False)
        emit = artifact_callback or (lambda _kind, _payload: None)
        if cancel():
            empty_progress = self._progress(durable_completed)
            raise ExecutorCancelled(
                "YuLan-Scale cancelled before release binding",
                checkpoint=self._checkpoint(
                    durable_completed,
                    empty_progress,
                ),
                budget=empty_progress,
            )

        # Binding is the first durable v11 artifact.  No engine state exists
        # before this exact release/run association is accepted.
        emit(
            "binding",
            {
                "input_fingerprint": plan.input_fingerprint,
                "plan": copy.deepcopy(extension),
                "model_release": copy.deepcopy(
                    runtime_inputs["model_release"]
                ),
                "trace_release": copy.deepcopy(
                    runtime_inputs["trace_release"]
                ),
            },
        )

        completed = durable_completed
        progress = self._progress(completed)
        artifact_counts = {
            "binding": 1,
            "activation_round": 0,
            "attempt": 0,
            "message_batch": 0,
            "actor_round": 0,
            "checkpoint": 0,
            "visual_frames": 0,
            "visual_manifest": 0,
            "result": 0,
        }
        resident_slots: dict[
            tuple[str, int, str, int],
            list[dict[str, str]],
        ] = {}
        actor_slots: dict[
            tuple[str, int, str, int],
            list[dict[str, str]],
        ] = {}

        def engine_artifact(
            kind: str,
            payload: Mapping[str, Any],
        ) -> None:
            if cancel():
                raise ExecutorCancelled(
                    "YuLan-Scale cancelled at an artifact boundary",
                    checkpoint=self._checkpoint(completed, progress),
                    budget=progress,
                )
            if kind == "activation_round":
                item = copy.deepcopy(dict(payload))
                emit(kind, item)
                key = (
                    str(item["scenario_id"]),
                    int(item["seed"]),
                    str(item["branch"]),
                    int(item["tick"]),
                )
                round_id = (
                    "ylr_"
                    + canonical_sha256(
                        [
                            plan.run_id,
                            key[0],
                            key[1],
                            key[2],
                            key[3],
                        ]
                    )[:24]
                )
                slots = item.get("slots")
                if not isinstance(slots, list) or len(slots) != 48:
                    raise ValueError(
                        "YuLan-Scale activation slot set is invalid"
                    )
                resident_slots[key] = [
                    {
                        "slot_id": (
                            "yls_"
                            + canonical_sha256(
                                [plan.run_id, round_id, ordinal]
                            )[:24]
                        ),
                        "prompt_fingerprint": str(
                            slot.get(
                                "prompt_fingerprint",
                                slot.get("prompt_sha256"),
                            )
                        ),
                    }
                    for ordinal, slot in enumerate(slots)
                ]
                artifact_counts[kind] += 1
                return
            if kind == "attempt":
                item = dict(payload)
                key = (
                    str(item["scenario_id"]),
                    int(item["seed"]),
                    str(item["branch"]),
                    int(item["tick"]),
                )
                slot_kind = str(item.get("slot_kind") or "resident")
                slots = (
                    actor_slots.get(key)
                    if slot_kind == "governance_actor"
                    else resident_slots.get(key)
                )
                ordinal = int(item.get("slot_ordinal", -1))
                if (
                    slots is None
                    or not 0 <= ordinal < len(slots)
                    or slots[ordinal]["prompt_fingerprint"]
                    != item.get("request_fingerprint")
                ):
                    raise ValueError(
                        "YuLan-Scale attempt precedes its durable slot"
                    )
                emit(
                    "attempt",
                    {
                        "slot_id": slots[ordinal]["slot_id"],
                        "attempt_ordinal": int(
                            item["attempt_ordinal"]
                        ),
                        "transport_kind": item["transport_kind"],
                        "status": item["status"],
                        "request_fingerprint": item[
                            "request_fingerprint"
                        ],
                        "response_fingerprint": item[
                            "response_fingerprint"
                        ],
                        "input_tokens": int(item["input_tokens"]),
                        "output_tokens": int(item["output_tokens"]),
                        "validated_decision": copy.deepcopy(
                            item["validated_decision"]
                        ),
                    },
                )
                artifact_counts[kind] += 1
                return
            if kind == "actor_round":
                item = copy.deepcopy(dict(payload))
                emit(kind, item)
                key = (
                    str(item["scenario_id"]),
                    int(item["seed"]),
                    str(item["branch"]),
                    int(item["tick"]),
                )
                round_id = (
                    "ylg_"
                    + canonical_sha256(
                        [
                            plan.run_id,
                            key[0],
                            key[1],
                            key[2],
                            key[3],
                        ]
                    )[:24]
                )
                slots = item.get("slots")
                if not isinstance(slots, list) or len(slots) != 3:
                    raise ValueError(
                        "YuLan-Scale actor slot set is invalid"
                    )
                actor_slots[key] = [
                    {
                        "slot_id": (
                            "yls_"
                            + canonical_sha256(
                                [plan.run_id, round_id, ordinal]
                            )[:24]
                        ),
                        "prompt_fingerprint": str(
                            slot.get(
                                "prompt_fingerprint",
                                slot.get("prompt_sha256"),
                            )
                        ),
                    }
                    for ordinal, slot in enumerate(slots)
                ]
                artifact_counts[kind] += 1
                return
            if kind != "message_batch":
                raise ValueError(
                    "YuLan-Scale engine emitted an unsupported artifact"
                )
            emit(kind, copy.deepcopy(dict(payload)))
            artifact_counts[kind] += 1

        def on_checkpoint(private_checkpoint: Mapping[str, Any]) -> None:
            if cancel():
                raise ExecutorCancelled(
                    "YuLan-Scale cancelled at a checkpoint boundary",
                    checkpoint=self._checkpoint(completed, progress),
                    budget=progress,
                )
            emit(
                "checkpoint",
                {"private_checkpoint": copy.deepcopy(dict(private_checkpoint))},
            )
            artifact_counts["checkpoint"] += 1

        def on_progress(
            completed_units: int,
            _checkpoint_index: Mapping[str, Any] | None,
        ) -> None:
            nonlocal completed, progress
            if completed_units != completed + 1:
                raise RuntimeError(
                    "YuLan-Scale engine progress is not contiguous"
                )
            completed = completed_units
            progress = self._progress(completed)
            if progress_callback is not None:
                progress_callback(
                    ExecutorProgressUpdate(
                        completed_units=completed,
                        total_units=LLM_POPULATION_REQUIRED_UNITS,
                        checkpoint=self._checkpoint(completed, progress),
                        budget=progress,
                    )
                )

        engine = self._engine_module()
        try:
            engine_kwargs = {
                "population": runtime_inputs["population"],
                "graph": runtime_inputs["graph"],
                "execution_provenance": provenance,
                "semantic_source": runtime_inputs["semantic_source"],
                "emulator_predictor": self._emulator_predictor(
                    runtime_inputs["emulator"]
                ),
                "emulator_release": runtime_inputs["emulator"],
                "scenario_ids": tuple(extension["scenario_ids"]),
                "seeds": tuple(extension["seeds"]),
                "strict_scale": True,
                "capture_visual_frames": True,
                "checkpoint_callback": on_checkpoint,
                "artifact_callback": engine_artifact,
                "committed_semantic_turns": committed_semantic_turns,
                "retain_checkpoint_payloads": False,
                "progress_callback": on_progress,
                "cancellation_probe": cancel,
            }
            if durable_completed or resume_checkpoints:
                resume_engine = getattr(
                    engine,
                    "resume_yulan_scale_engine_from_frontier",
                    None,
                )
                if not callable(resume_engine):
                    raise RuntimeContractError(
                        "YuLan-Scale engine lacks aggregate frontier recovery",
                        code="llm_population_resume_engine_unavailable",
                    )
                engine_result = resume_engine(
                    **engine_kwargs,
                    checkpoints=resume_checkpoints,
                    persisted_completed_units=(
                        checkpoint.completed_units if checkpoint else 0
                    ),
                )
            else:
                engine_result = engine.run_yulan_scale_engine(
                    **engine_kwargs,
                )
        except getattr(engine, "YuLanScaleEngineCancelled", RuntimeError) as exc:
            raise ExecutorCancelled(
                "YuLan-Scale cancelled before the next work unit",
                checkpoint=self._checkpoint(completed, progress),
                budget=progress,
            ) from exc
        if (
            not isinstance(engine_result, Mapping)
            or completed != LLM_POPULATION_REQUIRED_UNITS
        ):
            raise RuntimeError(
                "YuLan-Scale engine did not complete all frozen work units"
            )

        public_result = self._public_engine_result(engine_result)
        sealed, manifest, binary = seal_yulan_scale_result_v5(
            public_result,
            run_plan=plan,
            launch_contract=launch,
        )
        manifest_bytes = canonical_json_bytes(manifest)
        emit(
            "visual_frames",
            {
                "content_sha256": bytes_sha256(binary),
                "content": binary,
                "metadata": sealed["visualization_asset"],
            },
        )
        artifact_counts["visual_frames"] += 1
        emit(
            "visual_manifest",
            {
                "content_sha256": bytes_sha256(manifest_bytes),
                "content": manifest_bytes,
                "metadata": {
                    "outer_result_sha256": sealed["result_sha256"],
                    "domain_result_sha256": sealed[
                        "visualization_asset"
                    ]["domain_result_sha256"],
                },
            },
        )
        artifact_counts["visual_manifest"] += 1
        emit("result", sealed)
        artifact_counts["result"] += 1
        return ExecutorOutcome(
            result=sealed,
            checkpoint=self._checkpoint(completed, progress),
            budget=progress,
            private_artifacts={
                "schema_version": PRIVATE_ARTIFACT_SCHEMA_VERSION,
                "artifact_counts": artifact_counts,
                "resume_capability": (
                    "checkpoint_v3_cross_branch_aggregate_frontier"
                ),
            },
        )
