"""Validated workbench operations independent from the HTTP framework."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .evidence_catalog import (
    EvidenceManifestError,
    REVIEWED_RELEASE_KEY,
    get_installed_release,
    list_installed_releases,
)
from .live_asset_catalog import (
    REVIEWED_LIVE_ASSET_BUNDLE_FINGERPRINT,
    REVIEWED_LIVE_ASSET_BUNDLE_ID,
    LiveAssetCatalogError,
    qualify_reviewed_live_asset_bundle,
)
from .live_report_export import (
    LiveReportArtifacts,
    LiveReportExportError,
    build_live_report_artifacts,
)
from .live_runtime import (
    LIVE_AUTHORIZED_ADAPTER_MODE,
    LIVE_FIXTURE_ADAPTER_MODE,
)
from .live_runtime_contract import (
    DEFAULT_LIVE_SERVER_CONCURRENCY_CAP,
    DETERMINISTIC_TRANSPORT_PROVENANCE,
    LIVE_AGGREGATE_RESULT_SCHEMA_VERSION,
    LIVE_ALGORITHM_FINGERPRINT,
    LIVE_CACHE_ALGORITHM_VERSION,
    LIVE_CHECKPOINT_ALGORITHM_VERSION,
    LIVE_CLAIM_BOUNDARY_FINGERPRINT,
    LIVE_LLM_ADAPTER_MODE,
    LIVE_LLM_EXECUTION_PROVENANCE,
    LIVE_POPULATION_ALLOCATION_ALGORITHM_VERSION,
    LIVE_PLAN_SCHEMA_VERSION,
    LIVE_RESULT_LIMITATION_CODES,
    LIVE_RUNTIME_ADAPTER_VERSION,
    REVIEWED_PROVIDER_PROFILE_FINGERPRINT,
    REVIEWED_PROVIDER_PROFILE_ID,
    REVIEWED_RANDOM_SEEDS,
    REVIEWED_TEMPLATE_MAPPING_FINGERPRINT,
    REVIEWED_TEMPLATE_MAPPING_ID,
    REVIEWED_LECTURE_TOKEN_LIMIT,
    REVIEWED_TONGZHOU_TOKEN_LIMIT,
    LiveRuntimeContractError,
    assert_no_public_provider_configuration,
    build_live_cost_contract,
    build_reviewed_live_plan_extension,
    expected_live_turns,
    get_live_result_claim_boundary,
    get_reviewed_provider_profile,
    resolve_reviewed_template_mapping,
)
from .sensing_catalog import (
    REVIEWED_SENSING_RELEASE_KEY,
    SensingReleaseError,
    get_installed_sensing_release,
    list_installed_sensing_releases,
)
from .runtime import (
    RuntimeRuleError,
    validate_cancel_reason,
)
from .runtime_contract import (
    FAKE_ADAPTER_MODE,
    FIXTURE_STATUS,
    MASS_EXECUTION_MODE,
    MASS_RUN_PLAN_SCHEMA_VERSION,
    GOVERNANCE_V2_EXECUTION_MODE,
    GOVERNANCE_V2_RUN_PLAN_SCHEMA_VERSION,
    LLM_POPULATION_EXECUTION_MODE,
    LLM_POPULATION_RUN_PLAN_SCHEMA_VERSION,
    FORUM_TWIN_EXECUTION_MODE,
    FORUM_TWIN_RUN_PLAN_SCHEMA_VERSION,
    FORUM_TWIN_V2_EXECUTION_MODE,
    FORUM_TWIN_V2_RUN_PLAN_SCHEMA_VERSION,
    RuntimeContractError,
    build_launch_contract,
    canonical_sha256,
    required_turn_units,
    validate_aggregate_result,
    validate_frozen_run_plan,
)
from .governance_v2_contract import (
    GOVERNANCE_V2_DEFAULT_SEEDS,
    build_governance_v2_plan,
)
from .llm_population_contract import (
    LLM_POPULATION_DEFAULT_SEEDS,
    build_llm_population_plan,
    validate_reviewed_llm_trace_release,
)
from .forum_twin_contract import (
    FORUM_TWIN_DEFAULT_SEEDS,
    build_forum_twin_plan,
)
from .forum_twin_v2_contract import (
    FORUM_TWIN_V2_MODEL_NAME,
    FORUM_TWIN_V2_POPULATION_RELEASE_ID,
)
from envs.campus_pulse_governance.forum_relationships import (
    compile_scenario_stake_release_v1,
    compile_synthetic_relationship_release_v1,
)
from envs.campus_pulse_governance.forum_v2_execution import (
    build_forum_twin_run_plan_v9,
)
from envs.campus_pulse_governance.forum_visibility import (
    ACTIVATION_ALGORITHM_VERSION as VISIBILITY_ACTIVATION_ALGORITHM_VERSION,
    build_forum_twin_run_plan_v10,
)
from envs.campus_pulse_governance.mass_engine import (
    build_run_plan_v5,
)
from envs.campus_pulse_governance.mass_population import (
    canonical_hash as mass_canonical_hash,
)
from envs.campus_pulse_governance.forum_trace import (
    validate_reviewed_forum_trace_release_v2,
)
from .store import SCHEMA_VERSION, StoreConflictError, WorkbenchStore
from .templates import (
    CENTURY_GYM_POLICY_TEMPLATES,
    CENTURY_GYM_SCENARIO_TEMPLATES,
    ANALYSIS_SNAPSHOT_ID,
    POPULATION_RELEASE_ID,
    REVIEWED_POLICY_TEMPLATES,
    REVIEWED_SCENARIO_TEMPLATES,
)
from .windowed_sensing_catalog import (
    REVIEWED_WINDOWED_SENSING_RELEASE_KEY,
    WINDOWED_SENSING_MANIFEST_SCHEMA_VERSION,
)


TENANT_ID = "local-default"
PROJECT_MODES = {"simulation_stress_test", "descriptive_pilot"}
SHOCK_ORIGINS = {"governance_action", "external_event", "synthetic_stress"}
EVALUATION_LEVELS = {"model_conditional", "empirical_candidate"}
EXECUTION_MODES = {
    "audited_replay",
    "live_llm",
    MASS_EXECUTION_MODE,
    GOVERNANCE_V2_EXECUTION_MODE,
    LLM_POPULATION_EXECUTION_MODE,
    FORUM_TWIN_EXECUTION_MODE,
    FORUM_TWIN_V2_EXECUTION_MODE,
}
MASS_POPULATION_RELEASE_ID = "semantic-population-v2-reviewed-1000"
GOVERNANCE_V2_POPULATION_RELEASE_ID = (
    "semantic-population-v3-reviewed-1000"
)
LLM_POPULATION_RELEASE_ID = "semantic-llm-agent-population-v1-reviewed"
FORUM_TWIN_POPULATION_RELEASE_ID = "semantic-llm-forum-agent-population-v2-reviewed"
FORUM_TWIN_V2_SCENARIO_BY_TEMPLATE = {
    "tongzhou_governance_notice_v2": "governance_legitimacy_dispute",
    "lecture_external_incident_v2": "lecture_external_incident_shock",
}
MASS_SCENARIO_BY_TEMPLATE = {
    "lecture_external_incident_v2": "lecture_external_incident_shock",
    "tongzhou_governance_notice_v2": (
        "tongzhou_governance_information_shock"
    ),
}


def _load_yulan_scale_release_manifest() -> dict[str, Any]:
    """Load only the public, hash-bound v1 model release identity."""

    release_dir = Path(
        os.environ.get(
            "CAMPUS_PULSE_YULAN_SCALE_RELEASE_DIR",
            "_run/yulan-scale-v1",
        )
    )
    try:
        manifest = json.loads(
            (release_dir / "model-release.json").read_text(encoding="utf-8")
        )
        status = json.loads(
            (release_dir / "release-status.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise WorkbenchConflictError(
            "YuLan-Scale model release is not installed"
        ) from exc
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"model_sha256", "components"}
        or not isinstance(status, dict)
        or not isinstance(manifest.get("components"), dict)
        or manifest.get("model_sha256")
        # YuLan-Scale v1 predates ForumTwin and freezes the component object
        # itself as the model identity.  Keep that legacy byte-level contract;
        # ForumTwin uses its own v8 release validator.
        != canonical_sha256(manifest.get("components"))
        or status.get("model_sha256") != manifest.get("model_sha256")
        or status.get("status_sha256")
        != canonical_sha256(
            {key: value for key, value in status.items() if key != "status_sha256"}
        )
        or status.get("development_engine_eligible") is not True
    ):
        raise WorkbenchConflictError(
            "YuLan-Scale model release identity or zero-Provider gate is "
            "invalid"
        )
    return manifest


def _require_yulan_scale_reviewed_trace_release() -> None:
    release_dir = Path(
        os.environ.get(
            "CAMPUS_PULSE_YULAN_SCALE_RELEASE_DIR",
            "_run/yulan-scale-v1",
        )
    )
    try:
        release = json.loads(
            (release_dir / "reviewed-trace-release.json").read_text(
                encoding="utf-8"
            )
        )
        validate_reviewed_llm_trace_release(release)
    except (OSError, ValueError, RuntimeContractError) as exc:
        raise WorkbenchConflictError(
            "Complete reviewed YuLan-Scale trace release is not installed"
        ) from exc


def _yulan_scale_provider_profile() -> dict[str, Any]:
    return {
        "model_name": "gpt-5.6-sol",
        "endpoint_fingerprint": hashlib.sha256(
            b"campus-pulse-provider-endpoint-profile-v1"
        ).hexdigest(),
        "primary_semantic_slots": 13_056,
        "provider_request_limit": 26_112,
        "provider_token_limit": 40_000_000,
        "resident_max_output_tokens": 512,
        "actor_max_output_tokens": 384,
        "max_concurrency": 32,
        "network_retry_limit": 0,
        "format_repair_limit": 1,
    }


def _load_forum_twin_release_manifest() -> dict[str, Any]:
    """Load only the public, hash-bound ForumTwin model identity."""

    release_dir = Path(
        os.environ.get(
            "CAMPUS_PULSE_FORUM_TWIN_RELEASE_DIR",
            "_run/forum-twin-v1",
        )
    )
    try:
        manifest = json.loads(
            (release_dir / "model-release.json").read_text(encoding="utf-8")
        )
        status = json.loads(
            (release_dir / "release-status.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise WorkbenchConflictError(
            "ForumTwin model release is not installed"
        ) from exc
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"model_sha256", "components"}
        or not isinstance(status, dict)
        or status.get("model_sha256") != manifest.get("model_sha256")
        or status.get("development_engine_eligible") is not True
    ):
        raise WorkbenchConflictError(
            "ForumTwin model identity or zero-Provider gate is invalid"
        )
    return manifest


def _require_forum_twin_reviewed_trace_release() -> dict[str, Any]:
    release_dir = Path(
        os.environ.get(
            "CAMPUS_PULSE_FORUM_TWIN_RELEASE_DIR",
            "_run/forum-twin-v1",
        )
    )
    try:
        release = json.loads(
            (release_dir / "reviewed-trace-release.json").read_text(
                encoding="utf-8"
            )
        )
        return validate_reviewed_forum_trace_release_v2(release)
    except (OSError, ValueError, RuntimeContractError) as exc:
        raise WorkbenchConflictError(
            "Complete reviewed ForumTwin trace release is not installed"
        ) from exc


def _forum_twin_provider_profile() -> dict[str, Any]:
    """Frozen budget identity, not an authorization to contact a Provider."""

    endpoint = os.environ.get(
        "CAMPUS_PULSE_FORUM_TWIN_ENDPOINT",
        "https://yunwu.ai/v1",
    ).rstrip("/")
    return {
        "model_name": "gpt-5.4-mini",
        "endpoint_fingerprint": canonical_sha256({"endpoint": endpoint}),
        "primary_slots": 16_544,
        "request_limit": 33_088,
        "token_limit": 80_000_000,
        "resident_max_output_tokens": 512,
        "actor_max_output_tokens": 384,
        "max_concurrency": 32,
        "network_retry_limit": 0,
        "format_repair_limit": 1,
    }


def _load_forum_twin_v2_release_inputs() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, str],
]:
    """Load or deterministically rebuild v2 private inputs from reviewed data."""

    public_dir = Path(
        os.environ.get(
            "CAMPUS_PULSE_FORUM_TWIN_V2_PUBLIC_RELEASE_DIR",
            "data/campus_pulse_workspace/forumtwin/releases",
        )
    )
    private_dir = Path(
        os.environ.get(
            "CAMPUS_PULSE_FORUM_TWIN_V2_PRIVATE_RELEASE_DIR",
            "data/private/campus-pulse/forumtwin-v2",
        )
    )
    try:
        population = json.loads(
            (public_dir / "semantic-llm-forum-agent-population-v2.json").read_text(
                encoding="utf-8"
            )
        )
        public_manifest = json.loads(
            (public_dir / "forum-v2-input-release-manifest.json").read_text(
                encoding="utf-8"
            )
        )
        model_release = json.loads(
            (public_dir / "model-release.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise WorkbenchConflictError(
            "ForumTwin v2 reviewed population release is not installed"
        ) from exc
    expected_population = mass_canonical_hash(
        {
            key: value
            for key, value in population.items()
            if key != "population_sha256"
        }
    )
    if (
        population.get("schema_version")
        != "semantic-llm-forum-agent-population-v2"
        or population.get("population_size") != 1_000
        or population.get("population_sha256") != expected_population
        or public_manifest.get("population_sha256") != expected_population
        or public_manifest.get("manifest_sha256")
        != mass_canonical_hash(
            {
                key: value
                for key, value in public_manifest.items()
                if key != "manifest_sha256"
            }
        )
        or not isinstance(model_release.get("components"), dict)
    ):
        raise WorkbenchConflictError("ForumTwin v2 release identity drifted")
    try:
        stakes = json.loads(
            (private_dir / "scenario-stake-release-v1.json").read_text(
                encoding="utf-8"
            )
        )
        relationships = json.loads(
            (private_dir / "synthetic-relationship-release-v1.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        # A fresh code checkout intentionally excludes record-level edges.
        # Rebuilding them from the reviewed synthetic population is exact and
        # does not contact a Provider or touch the raw forum corpus.
        stakes = compile_scenario_stake_release_v1(
            population=population,
            scenario_ids=public_manifest["scenario_ids"],
            seed=20260815,
        )
        relationships = compile_synthetic_relationship_release_v1(
            population=population,
            stake_release=stakes,
            seed=20260815,
        )
    if (
        stakes.get("stake_release_sha256")
        != public_manifest.get("stake_release_sha256")
        or relationships.get("relationship_release_sha256")
        != public_manifest.get("relationship_release_sha256")
        or relationships.get("edge_count") != 8_000
        or relationships.get("privacy", {}).get("record_level_edges_public")
        is not False
    ):
        raise WorkbenchConflictError("ForumTwin v2 private release identity drifted")
    component_hashes = {
        str(kind): str(component["sha256"])
        for kind, component in model_release["components"].items()
    }
    component_hashes.update(
        {
            "relationship_attention_v1": canonical_sha256(
                {
                    "relationship_schema": relationships["schema_version"],
                    "degree": [2, 4, 2],
                    "public_ranking": "global_unchanged",
                }
            ),
            "private_turn_v3": canonical_sha256(
                {
                    "schema": "campus-pulse-forum-agent-turn-v3",
                    "max_public_messages": 1,
                    "max_interactions": 6,
                    "max_private_actions": 2,
                }
            ),
            "risk_radar_v1": canonical_sha256(
                {
                    "schema": "campus-pulse-governance-risk-result-v1",
                    "signal_count": 7,
                    "combined_score": False,
                }
            ),
        }
    )
    return population, stakes, relationships, component_hashes


def _load_mass_sim_release() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    release_dir = Path(
        os.environ.get(
            "CAMPUS_PULSE_MASS_SIM_RELEASE_DIR",
            "_run/mass-sim-v1",
        )
    )
    try:
        population = json.loads(
            (release_dir / "semantic-population-v2.json").read_text(
                encoding="utf-8"
            )
        )
        graph = json.loads(
            (
                release_dir / "synthetic-exposure-graph-v1.json"
            ).read_text(encoding="utf-8")
        )
        writeback = json.loads(
            (
                release_dir / "writeback-evaluation-v1.json"
            ).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise WorkbenchConflictError(
            "MassSim v1 release assets are not installed"
        ) from exc
    population_body = {
        key: value
        for key, value in population.items()
        if key != "population_sha256"
    }
    graph_body = {
        key: value
        for key, value in graph.items()
        if key != "graph_sha256"
    }
    if (
        population.get("schema_version") != "semantic-population-v2"
        or population.get("population_size") != 1000
        or population.get("population_sha256")
        != mass_canonical_hash(population_body)
        or graph.get("node_count") != 1000
        or graph.get("edge_count") != 12_000
        or graph.get("population_sha256")
        != population.get("population_sha256")
        or graph.get("graph_sha256") != mass_canonical_hash(graph_body)
        or writeback.get("formal_mode") not in {
            "observation_only",
            "stance_only",
            "full_state",
        }
    ):
        raise WorkbenchConflictError(
            "MassSim v1 release asset identity is invalid"
        )
    return population, graph, writeback


def _load_governance_v2_release_manifest() -> dict[str, Any]:
    """Load a hash-only v2 release manifest without importing runtime state."""

    configured = os.environ.get(
        "CAMPUS_PULSE_GOVERNANCE_V2_RELEASE_MANIFEST"
    )
    if configured:
        try:
            document = json.loads(
                Path(configured).read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise WorkbenchConflictError(
                "Governance v2 release manifest is unavailable"
            ) from exc
    else:
        try:
            from envs.campus_pulse_governance.governance_arena_v2 import (
                build_governance_v2_release_manifest,
            )

            document = build_governance_v2_release_manifest()
        except (
            ImportError,
            AttributeError,
            KeyError,
            RuntimeError,
            ValueError,
        ) as exc:
            raise WorkbenchConflictError(
                "Governance v2 release manifest is unavailable"
            ) from exc
    if not isinstance(document, dict):
        raise WorkbenchConflictError(
            "Governance v2 release manifest is invalid"
        )
    return document
RUN_STATUSES = {
    "draft",
    "queued",
    "leased",
    "running",
    "cancel_requested",
    "succeeded",
    "failed",
    "cancelled",
}
RUNTIME_ADAPTER_MODES = {"disabled", FAKE_ADAPTER_MODE}
WORKER_HEARTBEAT_SCHEMA_VERSION = "campus-pulse-worker-heartbeat-v1"
WORKER_HEARTBEAT_INTERVAL_SECONDS = 1.0
WORKER_HEARTBEAT_MAX_AGE_SECONDS = 5.0
WORKER_HEARTBEAT_FUTURE_SKEW_SECONDS = 5.0
WORKER_HEARTBEAT_STATUSES = {
    "current",
    "stale",
    "missing",
    "invalid",
}
DEMO_RUNTIME_TEMPLATE_WHITELIST = {
    "century_gym_ghost_booking_v1": {
        "natural_evolution_v2",
        "century_gym_combined_governance_v1",
    },
    "tongzhou_governance_notice_v2": {
        "natural_evolution_v2",
        "combined_governance_v2",
    },
    "lecture_external_incident_v2": {
        "natural_evolution_v2",
    },
}
MASS_RUNTIME_TEMPLATE_WHITELIST = {
    scenario_key: {
        "natural_evolution_v2",
        "combined_governance_v2",
    }
    for scenario_key in MASS_SCENARIO_BY_TEMPLATE
}
LIVE_PLAN_REQUEST_FIELDS = {
    "scenario_id",
    "execution_mode",
    "population_release_id",
    "policy_ids",
    "agent_count",
    "seeds",
    "provider_profile_id",
    "provider_call_limit",
    "token_budget",
    "max_workers",
}
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,79}$")
SNAPSHOT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
EVIDENCE_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
RELEASE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]{7,79}$")
ID_PATTERN = re.compile(r"^(prj|scn|pol|run|evt)_[0-9a-f]{24}$")

ISSUE_DOMAIN_LABELS = {
    "academic_and_learning": "学业与学习支持",
    "campus_services_and_rules": "校园服务与规则",
    "career_and_opportunity": "生涯与机会",
    "consumption_and_exchange": "消费与交换",
    "leisure_and_culture": "休闲与文化体验",
    "other_or_ambiguous": "其他或语境不明",
    "public_discourse_and_norms": "公共讨论与规范",
    "relationships_and_belonging": "关系与归属",
    "wellbeing_and_support": "身心状态与支持",
}


class WorkbenchError(ValueError):
    status_code = 400


class WorkbenchNotFoundError(WorkbenchError):
    status_code = 404


class WorkbenchConflictError(WorkbenchError):
    status_code = 409


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _bounded_text(
    value: Any,
    field: str,
    *,
    minimum: int = 1,
    maximum: int = 500,
) -> str:
    if not isinstance(value, str):
        raise WorkbenchError(f"{field} must be a string")
    normalized = " ".join(value.strip().split())
    if not minimum <= len(normalized) <= maximum:
        raise WorkbenchError(
            f"{field} length must be between {minimum} and {maximum}"
        )
    if "\x00" in normalized:
        raise WorkbenchError(f"{field} contains an invalid character")
    return normalized


def _identifier(value: Any, field: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise WorkbenchError(f"{field} has an invalid format")
    return value


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _public_forum_agent_node(agent: dict[str, Any]) -> dict[str, Any]:
    """Return the small, source-detached identity used by the world canvas.

    The display id is generated by the reviewed population compiler.  The
    projection deliberately excludes the internal Agent id, source evidence,
    profile bundle id and record-level relationship neighbours.
    """

    attention = float(agent.get("attention_budget") or 0.0)
    tendencies = agent.get("forum_tendencies") or {}
    uncertainty_seed = sum(
        float(value)
        for value in (agent.get("trust_style") or {}).values()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    )
    return {
        "display_id": str(agent["synthetic_display_id"]),
        "role_id": str(agent.get("primary_macro_role") or ""),
        "role_label": str(
            agent.get("secondary_macro_role")
            or agent.get("primary_macro_role")
            or ""
        ),
        "micro_role": str(
            agent.get("micro_interaction_role")
            or agent.get("micro_role_id")
            or ""
        ),
        "attention_budget": round(attention, 6),
        "activity_tendency": round(
            max(
                (
                    float(value)
                    for value in tendencies.values()
                    if isinstance(value, (int, float))
                    and not isinstance(value, bool)
                ),
                default=0.0,
            ),
            6,
        ),
        # A deterministic display-only uncertainty radius.  Runtime frames
        # replace this with the parent Agent's actual ten-particle dispersion.
        "particle_uncertainty": round(
            min(1.0, max(0.08, 0.18 + 0.35 * attention + 0.03 * uncertainty_seed)),
            6,
        ),
        "particle_count": int(agent.get("particle_count") or 0),
    }


def _public_forum_agent_dossier(agent: dict[str, Any]) -> dict[str, Any]:
    """Build one inspectable synthetic dossier without private lineage."""

    node = _public_forum_agent_node(agent)
    language_style = agent.get("language_style") or {}
    trust_style = agent.get("trust_style") or {}
    tendencies = agent.get("forum_tendencies") or {}
    return {
        "display_id": node["display_id"],
        "role_id": node["role_id"],
        "role_label": node["role_label"],
        "micro_role": node["micro_role"],
        "profile": {
            "historical_episode_focus": str(agent.get("episode_focus") or ""),
            "topic_portfolio": [
                str(item) for item in agent.get("topic_portfolio", ())
            ],
            "need_portfolio": [
                str(item) for item in agent.get("need_portfolio", ())
            ],
            "evidence_binding": str(agent.get("evidence_binding") or ""),
        },
        "persona": {
            "activity_rhythm": str(agent.get("activity_rhythm") or ""),
            "attention_budget": node["attention_budget"],
            "language_style": {
                "language_form": str(language_style.get("language_form") or ""),
                "reply_function": str(language_style.get("reply_function") or ""),
                "information_density": str(
                    language_style.get("information_density") or ""
                ),
            },
            "trust_style": {
                str(name): round(float(score), 6)
                for name, score in sorted(trust_style.items())
                if isinstance(score, (int, float)) and not isinstance(score, bool)
            },
            "uncertainty_handling": str(
                agent.get("uncertainty_handling") or ""
            ),
            "forum_tendencies": {
                str(name): round(float(score), 6)
                for name, score in sorted(tendencies.items())
                if isinstance(score, (int, float)) and not isinstance(score, bool)
            },
            "behavior_boundaries": [
                str(item) for item in agent.get("behavior_boundaries", ())
            ],
            "safety_gates": [
                str(item) for item in agent.get("safety_gates", ())
            ],
        },
        "memory": {
            "long_term_seed": str(agent.get("long_term_memory_seed") or ""),
            "recent_llm_episodes": [],
            "runtime_history_available": False,
        },
        "particle_state": {
            "count": node["particle_count"],
            "uncertainty": node["particle_uncertainty"],
            "semantics": (
                "ten SMC hypotheses for this Agent's latent state; "
                "not additional people"
            ),
        },
        "prompt_blueprint": {
            "resident_turn_contract": "campus-pulse-forum-agent-turn-v4",
            "profile_and_persona_bound": True,
            "recent_llm_memory_max": 2,
            "visible_public_context_max": 3,
            "visible_private_context_max": 4,
            "public_primary_actions_max": 1,
            "independent_public_interactions_max": 6,
            "parallel_private_actions_max": 2,
            "public_anonymity_enabled": True,
            "public_anonymity_semantics": "same_forum_optional_author_face",
            "same_tick_actions_are_parallel": True,
            "same_tick_commit_is_two_phase": True,
        },
    }


PROJECT_AGENT_PROFILE_OVERRIDE_FIELDS = frozenset(
    {
        "roleDescription",
        "stableBackground",
        "topics",
        "needs",
        "expressionStyle",
        "judgementStyle",
        "participationStyle",
        "activityStyle",
        "behaviorBoundaries",
        "longTermMemory",
        "projectInstructions",
    }
)


def _validate_project_agent_profile_override(
    payload: dict[str, Any],
) -> dict[str, str]:
    if set(payload) != PROJECT_AGENT_PROFILE_OVERRIDE_FIELDS:
        raise WorkbenchError("Project Agent profile fields do not match the contract")
    result: dict[str, str] = {}
    for field in sorted(PROJECT_AGENT_PROFILE_OVERRIDE_FIELDS):
        minimum = 0 if field == "projectInstructions" else 1
        result[field] = _bounded_text(
            payload[field],
            field,
            minimum=minimum,
            maximum=2_000,
        )
    return result


def worker_heartbeat_path(database_path: str | Path) -> Path:
    """Return the private, fixed single-node heartbeat path."""

    path = Path(database_path)
    return path.with_suffix(path.suffix + ".worker-heartbeat.json")


def _worker_heartbeat_payload(observed_at_unix_ms: int) -> dict[str, Any]:
    core = {
        "schema_version": WORKER_HEARTBEAT_SCHEMA_VERSION,
        "adapter_mode": FAKE_ADAPTER_MODE,
        "observed_at_unix_ms": observed_at_unix_ms,
    }
    return {
        **core,
        "integrity_sha256": _canonical_hash(core),
    }


def write_worker_heartbeat(
    database_path: str | Path,
    *,
    observed_at_unix_ms: int | None = None,
) -> None:
    """Atomically publish a minimal heartbeat without worker identifiers."""

    if observed_at_unix_ms is None:
        observed_at_unix_ms = time.time_ns() // 1_000_000
    if (
        isinstance(observed_at_unix_ms, bool)
        or not isinstance(observed_at_unix_ms, int)
        or observed_at_unix_ms <= 0
    ):
        raise ValueError("observed_at_unix_ms must be a positive integer")

    heartbeat_path = worker_heartbeat_path(database_path)
    encoded = json.dumps(
        _worker_heartbeat_payload(observed_at_unix_ms),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{heartbeat_path.name}.",
            suffix=".tmp",
            dir=heartbeat_path.parent,
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows can briefly reject an atomic replacement while a readiness
        # reader has the previous heartbeat open.  Keep the retry bounded and
        # local to that sharing violation; other persistence errors must still
        # fail the worker closed.
        for attempt in range(3):
            try:
                os.replace(temporary_path, heartbeat_path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 2:
                    raise
                time.sleep(0.005 * (attempt + 1))
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def remove_worker_heartbeat(database_path: str | Path) -> None:
    """Remove a cleanly stopped worker heartbeat, if present."""

    try:
        worker_heartbeat_path(database_path).unlink()
    except FileNotFoundError:
        pass


def worker_heartbeat_status(
    database_path: str | Path,
    *,
    now_unix_ms: int | None = None,
    max_age_seconds: float = WORKER_HEARTBEAT_MAX_AGE_SECONDS,
) -> str:
    """Return only a controlled heartbeat state; never return file content."""

    if now_unix_ms is None:
        now_unix_ms = time.time_ns() // 1_000_000
    if (
        isinstance(now_unix_ms, bool)
        or not isinstance(now_unix_ms, int)
        or now_unix_ms <= 0
        or isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, (int, float))
        or max_age_seconds <= 0
    ):
        return "invalid"

    heartbeat_path = worker_heartbeat_path(database_path)
    try:
        metadata = heartbeat_path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > 1024
        ):
            return "invalid"
        with heartbeat_path.open("rb") as handle:
            encoded = handle.read(1025)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "invalid"
    if len(encoded) > 1024:
        return "invalid"

    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "invalid"
    expected_fields = {
        "schema_version",
        "adapter_mode",
        "observed_at_unix_ms",
        "integrity_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        return "invalid"
    observed_at = payload.get("observed_at_unix_ms")
    integrity = payload.get("integrity_sha256")
    if (
        payload.get("schema_version")
        != WORKER_HEARTBEAT_SCHEMA_VERSION
        or payload.get("adapter_mode") != FAKE_ADAPTER_MODE
        or isinstance(observed_at, bool)
        or not isinstance(observed_at, int)
        or observed_at <= 0
        or not isinstance(integrity, str)
        or not re.fullmatch(r"[0-9a-f]{64}", integrity)
    ):
        return "invalid"
    core = {
        "schema_version": payload["schema_version"],
        "adapter_mode": payload["adapter_mode"],
        "observed_at_unix_ms": observed_at,
    }
    if not hmac.compare_digest(integrity, _canonical_hash(core)):
        return "invalid"

    age_ms = now_unix_ms - observed_at
    if age_ms < -(WORKER_HEARTBEAT_FUTURE_SKEW_SECONDS * 1000):
        return "invalid"
    if age_ms > max_age_seconds * 1000:
        return "stale"
    return "current"


class WorkerHeartbeatPublisher:
    """Publish process readiness independently from a long executor call."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        interval_seconds: float = WORKER_HEARTBEAT_INTERVAL_SECONDS,
    ):
        if (
            isinstance(interval_seconds, bool)
            or not isinstance(interval_seconds, (int, float))
            or not 0.05 <= interval_seconds <= 2.0
        ):
            raise ValueError(
                "interval_seconds must be between 0.05 and 2.0"
            )
        self.database_path = Path(database_path)
        self.interval_seconds = float(interval_seconds)
        self._stop = threading.Event()
        self._failed = threading.Event()
        self._thread = threading.Thread(
            target=self._publish_loop,
            name="campus-pulse-worker-readiness",
            daemon=True,
        )
        self._started = False

    def start(self) -> None:
        if self._started:
            raise RuntimeError("worker heartbeat publisher already started")
        try:
            write_worker_heartbeat(self.database_path)
        except Exception as exc:
            raise RuntimeError("worker heartbeat unavailable") from exc
        self._started = True
        self._thread.start()

    def raise_if_failed(self) -> None:
        if self._failed.is_set():
            raise RuntimeError("worker heartbeat unavailable")

    def stop(self, *, remove: bool = True) -> None:
        if self._started:
            self._stop.set()
            self._thread.join(timeout=self.interval_seconds + 2.0)
            if self._thread.is_alive():
                raise RuntimeError("worker heartbeat did not stop")
        if remove:
            try:
                remove_worker_heartbeat(self.database_path)
            except OSError:
                # Readiness will naturally become stale after an unclean stop.
                pass

    def _publish_loop(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                write_worker_heartbeat(self.database_path)
            except Exception:
                self._failed.set()
                return


class WorkbenchService:
    def __init__(
        self,
        store: WorkbenchStore,
        tenant_id: str = TENANT_ID,
        *,
        runtime_adapter_mode: str = "disabled",
        runtime_concurrency_cap: int = 32,
        live_llm_enabled: bool = False,
    ):
        self.store = store
        self.tenant_id = _bounded_text(
            tenant_id,
            "tenant_id",
            minimum=3,
            maximum=64,
        )
        if runtime_adapter_mode not in RUNTIME_ADAPTER_MODES:
            raise WorkbenchError(
                "runtime_adapter_mode is not installed or enabled"
            )
        if (
            isinstance(runtime_concurrency_cap, bool)
            or not isinstance(runtime_concurrency_cap, int)
            or not 1 <= runtime_concurrency_cap <= 48
        ):
            raise WorkbenchError(
                "runtime_concurrency_cap must be between 1 and 48"
            )
        if not isinstance(live_llm_enabled, bool):
            raise WorkbenchError("live_llm_enabled must be boolean")
        self.runtime_adapter_mode = runtime_adapter_mode
        self.runtime_concurrency_cap = runtime_concurrency_cap
        self._forum_twin_v2_agent_world_cache: dict[str, Any] | None = None
        self._forum_twin_v2_agent_world_lock = threading.Lock()
        self.live_llm_enabled = live_llm_enabled

    def health(self) -> dict[str, Any]:
        return {
            "status": "healthy",
            "service": "campus-pulse-workbench",
            "schema_version": self.store.schema_version(),
            "storage_journal_mode": self.store.journal_mode(),
            "deployment_tier": "internal_single_node",
            "internet_ready": False,
            "runtime_adapter_mode": self.runtime_adapter_mode,
            "runtime_inline_execution": False,
            "live_llm_gate": (
                "enabled" if self.live_llm_enabled else "disabled"
            ),
        }

    def readiness(self) -> dict[str, Any]:
        """Report safe dependency readiness without exposing local details."""

        schema_version = self.store.schema_version()
        journal_mode = self.store.journal_mode()
        database_ready = (
            schema_version == SCHEMA_VERSION
            and journal_mode.lower() == "wal"
        )
        runtime_enabled = self.runtime_adapter_mode == FAKE_ADAPTER_MODE
        heartbeat_status = (
            worker_heartbeat_status(self.store.database_path)
            if runtime_enabled
            else "not_required"
        )
        runtime_ready = (
            runtime_enabled and heartbeat_status == "current"
        )
        return {
            "status": (
                "ready"
                if database_ready and (not runtime_enabled or runtime_ready)
                else "not_ready"
            ),
            "service": "campus-pulse-workbench",
            "checks": {
                "database": "ready" if database_ready else "not_ready",
                "schema": (
                    "current"
                    if schema_version == SCHEMA_VERSION
                    else "migration_required"
                ),
                "runtime_adapter": (
                    "enabled"
                    if self.runtime_adapter_mode == FAKE_ADAPTER_MODE
                    else "disabled_by_configuration"
                ),
                "worker_heartbeat": heartbeat_status,
            },
            "schema_version": schema_version,
            "expected_schema_version": SCHEMA_VERSION,
            "runtime_adapter_mode": self.runtime_adapter_mode,
            "runtime_enqueue_ready": database_ready and runtime_ready,
            "runtime_inline_execution": False,
            "live_llm_gate": (
                "enabled" if self.live_llm_enabled else "disabled"
            ),
        }

    def install_reviewed_live_runtime_inputs(self) -> dict[str, Any]:
        """Operator-side, network-free installation of fixed P3.2a inputs."""

        try:
            bundle = qualify_reviewed_live_asset_bundle()
            self.store.install_live_asset_bundle(bundle)
            self.store.register_live_provider_profile(
                get_reviewed_provider_profile(internal=True)
            )
        except (LiveAssetCatalogError, StoreConflictError) as exc:
            raise WorkbenchConflictError(
                "Reviewed live runtime inputs failed qualification"
            ) from exc
        return self.live_runtime_capability()

    def set_reviewed_live_provider_enabled(
        self,
        enabled: bool,
        *,
        expected_state_version: int | None = None,
    ) -> dict[str, Any]:
        """Operator-side profile gate; this never creates an authorization."""

        try:
            self.store.set_live_provider_profile_enabled(
                REVIEWED_PROVIDER_PROFILE_ID,
                enabled,
                expected_state_version=expected_state_version,
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc
        return self.live_runtime_capability()

    def live_runtime_capability(
        self,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Return only controlled readiness and immutable public identities."""

        bundle_row = self.store.get_live_asset_bundle(
            REVIEWED_LIVE_ASSET_BUNDLE_ID
        )
        bundle_ready = bool(
            bundle_row is not None
            and bundle_row.get("status") == "qualified"
            and bundle_row.get("bundle_fingerprint")
            == REVIEWED_LIVE_ASSET_BUNDLE_FINGERPRINT
            and bundle_row.get("manifest", {}).get("bundle_fingerprint")
            == REVIEWED_LIVE_ASSET_BUNDLE_FINGERPRINT
        )
        provider_row = self.store.get_live_provider_profile(
            REVIEWED_PROVIDER_PROFILE_ID
        )
        provider_registered = bool(
            provider_row is not None
            and provider_row.get("provider_profile_fingerprint")
            == REVIEWED_PROVIDER_PROFILE_FINGERPRINT
        )
        provider_enabled = bool(
            provider_registered and provider_row.get("enabled") is True
        )
        planning_ready = bundle_ready and provider_registered

        authorization: dict[str, Any] = {
            "state": "not_applicable",
            "provider_call_cap": None,
            "token_cap": None,
        }
        plan: dict[str, Any] | None = None
        if run_id is not None:
            run = self.get_run(run_id)
            extension = self.store.get_run_live_plan_extension(
                self.tenant_id,
                run["project_id"],
                run["run_id"],
            )
            if extension is None:
                authorization["state"] = "not_live_plan"
            else:
                plan = {
                    key: extension[key]
                    for key in (
                        "run_plan_schema_version",
                        "input_fingerprint",
                        "asset_bundle_fingerprint",
                        "provider_profile_fingerprint",
                        "template_mapping_fingerprint",
                        "expected_agent_turns",
                        "expected_cold_provider_calls",
                        "provider_call_limit",
                        "token_limit",
                        "effective_max_workers",
                        "extension_fingerprint",
                    )
                }
                stored_authorization = self.store.get_live_authorization(
                    self.tenant_id,
                    run["project_id"],
                    run["run_id"],
                )
                if stored_authorization is None:
                    authorization["state"] = "missing"
                else:
                    authorization = {
                        "state": stored_authorization["state"],
                        "provider_call_cap": stored_authorization[
                            "provider_call_cap"
                        ],
                        "token_cap": stored_authorization["token_cap"],
                    }

        return {
            "capability_schema_version": (
                "campus-pulse-live-capability-v1"
            ),
            "phase": "p3.2b_deterministic_transport",
            "qualification": (
                "ready" if planning_ready else "not_ready"
            ),
            "planning_ready": planning_ready,
            "live_gate": (
                "enabled" if self.live_llm_enabled else "disabled"
            ),
            "provider_profile_state": (
                "enabled"
                if provider_enabled
                else (
                    "disabled"
                    if provider_registered
                    else "not_installed"
                )
            ),
            # Preserve the legacy field as the authorized-provider executor
            # state.  P3.2b deliberately installs only the deterministic
            # fixture path; it must never make a live Provider run appear
            # enqueueable.
            "executor_state": "not_installed",
            "deterministic_fixture": {
                "executor_state": "installed",
                "operator_entrypoint": "available",
                "provider_calls_permitted": False,
                "external_download_permitted": False,
                "governance_conclusion_permitted": False,
                "causal_effect_permitted": False,
                "forecast_permitted": False,
            },
            "live_enqueue_ready": False,
            "live_enqueue_blocker": (
                "authorized_provider_runtime_not_installed"
            ),
            "asset_bundle": {
                "bundle_id": REVIEWED_LIVE_ASSET_BUNDLE_ID,
                "bundle_fingerprint": (
                    REVIEWED_LIVE_ASSET_BUNDLE_FINGERPRINT
                ),
                "state": "qualified" if bundle_ready else "not_installed",
            },
            "provider_profile": {
                "provider_profile_id": REVIEWED_PROVIDER_PROFILE_ID,
                "provider_profile_fingerprint": (
                    REVIEWED_PROVIDER_PROFILE_FINGERPRINT
                ),
                "requested_model": "gpt-5.6-sol",
                "credential_state": "not_evaluated",
            },
            "template_mapping": {
                "mapping_id": REVIEWED_TEMPLATE_MAPPING_ID,
                "mapping_fingerprint": (
                    REVIEWED_TEMPLATE_MAPPING_FINGERPRINT
                ),
            },
            "claim_boundary": get_live_result_claim_boundary(),
            "authorization": authorization,
            "plan": plan,
            "provider_calls_performed": 0,
        }

    def authorize_live_run(
        self,
        run_id: str,
        *,
        provider_call_cap: int,
        token_cap: int,
        expires_at: str,
        confirmation_code: str,
    ) -> dict[str, Any]:
        """Create one operator grant bound to an already frozen v4 run."""

        if not self.live_llm_enabled:
            raise WorkbenchConflictError(
                "The server-wide live LLM gate is disabled"
            )
        run = self.get_run(run_id)
        extension = self.store.get_run_live_plan_extension(
            self.tenant_id,
            run["project_id"],
            run["run_id"],
        )
        profile = self.store.get_live_provider_profile(
            REVIEWED_PROVIDER_PROFILE_ID
        )
        if (
            extension is None
            or profile is None
            or profile.get("enabled") is not True
        ):
            raise WorkbenchConflictError(
                "Live plan qualification or provider gate is unavailable"
            )
        request = {
            "plan_fingerprint": extension["input_fingerprint"],
            "input_fingerprint": extension["input_fingerprint"],
            "extension_fingerprint": extension[
                "extension_fingerprint"
            ],
            "asset_bundle_fingerprint": extension[
                "asset_bundle_fingerprint"
            ],
            "provider_profile_fingerprint": extension[
                "provider_profile_fingerprint"
            ],
            "template_mapping_fingerprint": extension[
                "template_mapping_fingerprint"
            ],
            "population_release_fingerprint": extension[
                "population_release_fingerprint"
            ],
            "population_evidence_fingerprint": extension[
                "population_evidence_fingerprint"
            ],
            "algorithm_fingerprint": extension[
                "algorithm_fingerprint"
            ],
            "provider_call_cap": provider_call_cap,
            "token_cap": token_cap,
            "confirmation_code": confirmation_code,
            "expires_at": expires_at,
        }
        try:
            return self.store.create_live_authorization(
                self.tenant_id,
                run["project_id"],
                run["run_id"],
                request,
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc

    def revoke_live_run_authorization(
        self,
        run_id: str,
        authorization_id: str,
        *,
        reason_code: str,
    ) -> dict[str, Any]:
        run = self.get_run(run_id)
        try:
            return self.store.revoke_live_authorization(
                self.tenant_id,
                run["project_id"],
                run["run_id"],
                authorization_id,
                reason_code=reason_code,
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc

    def overview(self) -> dict[str, Any]:
        return {
            **self.store.overview(self.tenant_id),
            "evidence_boundary": (
                "当前在线层管理仿真压力测试记录；真实因果效果只有在独立试点"
                "数据和审查通过后才能升级。"
            ),
        }

    def get_forum_twin_v2_agent_world(self) -> dict[str, Any]:
        """Return the fixed Agent world through an aggregate-only contract.

        The reviewed releases are immutable for the lifetime of a service
        instance.  Building their public projection is intentionally strict
        but expensive, so cache the verified projection after first use.
        Callers receive a copy and cannot mutate the cached contract.
        """

        cached = self._forum_twin_v2_agent_world_cache
        if cached is not None:
            return deepcopy(cached)
        with self._forum_twin_v2_agent_world_lock:
            cached = self._forum_twin_v2_agent_world_cache
            if cached is None:
                cached = self._build_forum_twin_v2_agent_world()
                self._forum_twin_v2_agent_world_cache = cached
        return deepcopy(cached)

    def _build_forum_twin_v2_agent_world(self) -> dict[str, Any]:
        """Build and validate the immutable public Agent-world projection.

        The reviewed population and private relationship releases are loaded
        through the same hash checks as run planning.  This projection exposes
        macro-role composition, scenario-level stake summaries and the public
        relationship matrix; it deliberately never returns an Agent id,
        private conversation, or record-level relationship edge.
        """

        population, stakes, relationships, component_hashes = (
            _load_forum_twin_v2_release_inputs()
        )
        agents = population["agents"]
        public_release_dir = Path(
            os.environ.get(
                "CAMPUS_PULSE_FORUM_TWIN_V2_PUBLIC_RELEASE_DIR",
                "data/campus_pulse_workspace/forumtwin/releases",
            )
        )
        try:
            ecology = json.loads(
                (public_release_dir / "forum-ecology-release-v1.json").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, ValueError) as exc:
            raise WorkbenchConflictError(
                "ForumTwin v2 ecology release is not installed"
            ) from exc
        ecology_body = {
            key: value
            for key, value in ecology.items()
            if key != "ecology_release_sha256"
        }
        micro_role_pool_count = ecology.get("micro_role_count")
        if (
            ecology.get("ecology_release_sha256")
            != mass_canonical_hash(ecology_body)
            or isinstance(micro_role_pool_count, bool)
            or not isinstance(micro_role_pool_count, int)
            or not 80 <= micro_role_pool_count <= 120
        ):
            raise WorkbenchConflictError(
                "ForumTwin v2 ecology release identity drifted"
            )
        role_groups: dict[str, list[dict[str, Any]]] = {}
        for agent in agents:
            role_groups.setdefault(
                str(agent["primary_macro_role"]), []
            ).append(agent)

        roles: list[dict[str, Any]] = []
        for role_id in sorted(role_groups):
            members = role_groups[role_id]
            label_counts: dict[str, int] = {}
            topic_counts: dict[str, int] = {}
            need_counts: dict[str, int] = {}
            micro_roles = set()
            attention_total = 0.0
            for member in members:
                label = str(member.get("secondary_macro_role") or role_id)
                label_counts[label] = label_counts.get(label, 0) + 1
                micro_roles.add(str(member.get("micro_role_id") or ""))
                attention_total += float(member.get("attention_budget") or 0.0)
                for topic in member.get("topic_portfolio", ()):
                    text = str(topic).strip()
                    if text:
                        topic_counts[text] = topic_counts.get(text, 0) + 1
                for need in member.get("need_portfolio", ()):
                    text = str(need).strip()
                    if text:
                        need_counts[text] = need_counts.get(text, 0) + 1

            def top_values(counts: dict[str, int]) -> list[str]:
                return [
                    value
                    for value, _count in sorted(
                        counts.items(), key=lambda item: (-item[1], item[0])
                    )[:3]
                ]

            role_label = sorted(
                label_counts.items(), key=lambda item: (-item[1], item[0])
            )[0][0]
            roles.append(
                {
                    "role_id": role_id,
                    "label": role_label,
                    "agent_count": len(members),
                    "population_share": round(len(members) / len(agents), 6),
                    "micro_role_count": len(micro_roles - {""}),
                    "anchor_eligible": role_id != "archetype-16",
                    "mean_attention_budget": round(
                        attention_total / len(members), 6
                    ),
                    "top_topics": top_values(topic_counts),
                    "top_needs": top_values(need_counts),
                }
            )

        agent_roles = {
            str(agent["agent_id"]): str(agent["primary_macro_role"])
            for agent in agents
        }
        stake_accumulators: dict[str, dict[str, list[float]]] = {}
        affected_counts: dict[str, int] = {}
        position_counts: dict[str, int] = {}
        for position in stakes["positions"]:
            scenario_id = str(position["scenario_id"])
            position_counts[scenario_id] = position_counts.get(scenario_id, 0) + 1
            scenario_fields = stake_accumulators.setdefault(scenario_id, {})
            for field_name, field in position["fields"].items():
                value = field["value"]
                if field_name == "directly_affected":
                    affected_counts[scenario_id] = affected_counts.get(
                        scenario_id, 0
                    ) + int(bool(value))
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    scenario_fields.setdefault(str(field_name), []).append(
                        float(value)
                    )

        scenario_stakes = []
        for scenario_id in sorted(position_counts):
            count = position_counts[scenario_id]
            scenario_stakes.append(
                {
                    "scenario_id": scenario_id,
                    "position_count": count,
                    "directly_affected_share": round(
                        affected_counts.get(scenario_id, 0) / count, 6
                    ),
                    "mean_dimensions": {
                        field_name: round(sum(values) / len(values), 6)
                        for field_name, values in sorted(
                            stake_accumulators[scenario_id].items()
                        )
                        if values
                    },
                }
            )

        relationship_matrix = [
            {
                "source_role_id": str(row["source_macro_role"]),
                "target_role_id": str(row["target_macro_role"]),
                "relation_type": str(row["relation_type"]),
                "directed_edge_count": int(row["directed_edge_count"]),
            }
            for row in relationships["public_aggregate_matrix"]
        ]
        relation_type_counts: dict[str, int] = {}
        for row in relationship_matrix:
            relation_type = row["relation_type"]
            relation_type_counts[relation_type] = (
                relation_type_counts.get(relation_type, 0)
                + row["directed_edge_count"]
            )

        # Publish one stable, privacy-safe representative for every represented
        # micro role.  These are synthetic display profiles, not source records:
        # internal Agent ids, evidence refs, bundle ids, private relationships
        # and Provider bodies never cross this boundary.  The UI uses them to
        # make every population node inspectable without inventing personas in
        # the browser.
        representative_by_micro_role: dict[
            tuple[str, str], dict[str, Any]
        ] = {}
        for agent in sorted(
            agents,
            key=lambda value: (
                str(value.get("primary_macro_role") or ""),
                str(value.get("micro_role_id") or ""),
                str(value.get("synthetic_display_id") or ""),
            ),
        ):
            role_id = str(agent.get("primary_macro_role") or "")
            micro_role_id = str(agent.get("micro_role_id") or "")
            key = (role_id, micro_role_id)
            if not role_id or not micro_role_id or key in representative_by_micro_role:
                continue
            representative_by_micro_role[key] = _public_forum_agent_dossier(agent)
        representative_agents = [
            representative_by_micro_role[key]
            for key in sorted(representative_by_micro_role)
        ]

        body = {
            "schema_version": "campus-pulse-forum-agent-world-public-v1",
            "release_identity": {
                "population_sha256": population["population_sha256"],
                "profile_release_sha256": population["profile_release_sha256"],
                "relationship_release_sha256": relationships[
                    "relationship_release_sha256"
                ],
                "stake_release_sha256": stakes["stake_release_sha256"],
            },
            "population": {
                "agent_count": len(agents),
                "particle_count": sum(
                    int(agent.get("particle_count") or 0) for agent in agents
                ),
                "macro_role_count": len(roles),
                "micro_role_count": micro_role_pool_count,
                "micro_role_pool_count": micro_role_pool_count,
                "represented_micro_role_count": len(
                    {
                        str(agent.get("micro_role_id") or "")
                        for agent in agents
                    }
                    - {""}
                ),
                "profile_bundle_count": len(
                    {
                        str(agent.get("profile_bundle_id") or "")
                        for agent in agents
                    }
                    - {""}
                ),
                "fixed_across_projects": True,
                "roles": roles,
                "display_nodes": [
                    _public_forum_agent_node(agent)
                    for agent in sorted(
                        agents,
                        key=lambda value: str(
                            value.get("synthetic_display_id") or ""
                        ),
                    )
                ],
                "representative_agents": representative_agents,
            },
            "relationships": {
                "directed_edge_count": int(relationships["edge_count"]),
                "degree_per_agent": {
                    "close_friend": 2,
                    "familiar_peer": 4,
                    "cross_group_acquaintance": 2,
                },
                "relation_type_counts": dict(sorted(relation_type_counts.items())),
                "aggregate_matrix": relationship_matrix,
                "record_level_edges_public": False,
                "fully_synthetic": True,
            },
            "stakes": {
                "field_provenance_contract": stakes[
                    "field_provenance_contract"
                ],
                "scenarios": scenario_stakes,
            },
            "channels": {
                "public_forum": {
                    "ranking": "global_top_ten_plus_latest",
                    "personalized_ranking": False,
                    "public_messages_per_agent_tick_max": 1,
                    "independent_interactions_per_agent_tick_max": 6,
                    "optional_anonymous_author_face": True,
                    "anonymous_posts_use_same_ranking_and_reply_graph": True,
                    "anonymous_author_visible_to_governance": False,
                },
                "private": {
                    "direct_chat": True,
                    "dynamic_groups": True,
                    "private_actions_per_agent_tick_max": 2,
                    "reviewed_excerpts_only_in_public_views": True,
                },
                "service_desk": {
                    "auditable_tickets": True,
                    "receipts_can_return_to_public_or_private_channels": True,
                },
            },
            "activation": {
                "algorithm_id": VISIBILITY_ACTIVATION_ALGORITHM_VERSION,
                "modes": [
                    "budgeted_pps",
                    "full_population_keyframes",
                    "full_population_every_tick",
                ],
                "anchors_per_budgeted_tick": 16,
                "budget_schedule": [24, 24, 48, 64, 64, 64, 64, 64]
                + [48] * 10
                + [32] * 6,
                "interaction_priority": [
                    "direct_reply_quote_correction_service_receipt",
                    "risk_report_help_request",
                    "active_claim_participant",
                    "novel_bridge_exposure",
                    "high_attention_silent",
                ],
                "pps_factors": [
                    "design_weight",
                    "speaking_probability",
                    "state_uncertainty",
                    "rotation_penalty",
                ],
                "private_origin_share_of_non_anchor_slots_max": 0.40,
                "full_population_uses_two_phase_commit": True,
            },
            "risk_signals": [
                "unsupported_claim_velocity",
                "private_to_public_spillover",
                "contested_claim_share",
                "cross_group_exposure_gap",
                "correction_penetration_and_delay",
                "help_backlog_and_sla_breach",
                "governance_observation_gap",
            ],
            "visibility_harness": {
                "channels": [
                    "public_forum",
                    "friend_chat",
                    "dynamic_group",
                    "service_desk",
                ],
                "public_anonymity": {
                    "enabled": True,
                    "semantics": "same_forum_optional_author_face",
                    "hidden_author_visible_to_governance": False,
                },
                "cross_channel_risk_signals": [
                    "anonymous_claim_velocity",
                    "private_to_public_spillover",
                    "visible_hidden_opinion_divergence",
                    "correction_channel_penetration",
                    "service_conversion_gap",
                ],
                "governance_reads_private_or_anonymous_authors": False,
            },
            "governance_actors": [
                "governance_authority",
                "service_operator",
                "community_bridge",
            ],
            "component_hashes": dict(sorted(component_hashes.items())),
            "privacy": {
                "contains_agent_ids": False,
                "contains_record_level_relationship_edges": False,
                "contains_private_text": False,
                "contains_source_ids": False,
                "representatives_are_synthetic_display_profiles": True,
                "display_nodes_are_all_synthetic_agents": True,
            },
        }
        # Prove that role aggregation used the full population without leaking
        # the private mapping in the response.
        if len(agent_roles) != len(agents):
            raise WorkbenchConflictError("ForumTwin v2 Agent identity drifted")
        return {**body, "world_sha256": canonical_sha256(body)}

    def get_forum_twin_v2_agent_dossier(
        self,
        display_id: str,
        *,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Return one source-detached dossier for an inspectable world node."""

        display_id = _bounded_text(
            display_id,
            "display_id",
            minimum=3,
            maximum=96,
        )
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", display_id):
            raise WorkbenchError("display_id has an invalid format")
        profile_release: dict[str, Any] | None = None
        if project_id is not None:
            self.get_project(project_id)
            profile_release = (
                self.store.get_forum_project_profile_override_release(
                    self.tenant_id,
                    project_id,
                )
            )

        population, _stakes, _relationships, _hashes = (
            _load_forum_twin_v2_release_inputs()
        )
        for agent in population["agents"]:
            if str(agent.get("synthetic_display_id") or "") != display_id:
                continue
            dossier = _public_forum_agent_dossier(agent)
            project_override = (
                profile_release.get("overrides", {}).get(display_id)
                if profile_release is not None
                else None
            )
            dossier["project_override"] = project_override
            body = {
                "schema_version": "campus-pulse-forum-agent-dossier-public-v1",
                "population_sha256": population["population_sha256"],
                "project_id": project_id,
                "profile_source": (
                    "project_override_release"
                    if project_override is not None
                    else "reviewed_population_release"
                ),
                "profile_override_release": (
                    {
                        "release_sha256": profile_release["release_sha256"],
                        "release_version": profile_release["release_version"],
                    }
                    if profile_release is not None
                    else None
                ),
                "dossier": dossier,
                "privacy": {
                    "contains_source_id": False,
                    "contains_internal_agent_id": False,
                    "contains_record_level_relationships": False,
                    "contains_private_messages": False,
                },
            }
            return {**body, "content_sha256": canonical_sha256(body)}
        raise WorkbenchNotFoundError("Synthetic Agent display id was not found")

    def save_forum_twin_v2_agent_profile_override(
        self,
        project_id: str,
        display_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Freeze one project-specific Agent specification as a new release."""

        self.get_project(project_id)
        validated = _validate_project_agent_profile_override(payload)
        population, _stakes, _relationships, _hashes = (
            _load_forum_twin_v2_release_inputs()
        )
        known = {
            str(agent.get("synthetic_display_id") or "")
            for agent in population["agents"]
        }
        if display_id not in known:
            raise WorkbenchNotFoundError("Synthetic Agent display id was not found")
        try:
            release = self.store.append_forum_project_profile_override_release(
                self.tenant_id,
                project_id,
                population_sha256=population["population_sha256"],
                display_id=display_id,
                override=validated,
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc
        dossier = self.get_forum_twin_v2_agent_dossier(
            display_id,
            project_id=project_id,
        )
        return {**dossier, "saved_release_sha256": release["release_sha256"]}

    def restore_forum_twin_v2_agent_profile(
        self,
        project_id: str,
        display_id: str,
    ) -> dict[str, Any]:
        """Append a release that removes one override and restores the base profile."""

        self.get_project(project_id)
        population, _stakes, _relationships, _hashes = (
            _load_forum_twin_v2_release_inputs()
        )
        known = {
            str(agent.get("synthetic_display_id") or "")
            for agent in population["agents"]
        }
        if display_id not in known:
            raise WorkbenchNotFoundError("Synthetic Agent display id was not found")
        try:
            release = self.store.append_forum_project_profile_override_release(
                self.tenant_id,
                project_id,
                population_sha256=population["population_sha256"],
                display_id=display_id,
                override=None,
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc
        dossier = self.get_forum_twin_v2_agent_dossier(
            display_id,
            project_id=project_id,
        )
        return {**dossier, "saved_release_sha256": release["release_sha256"]}

    def list_installed_evidence_releases(self) -> list[dict[str, Any]]:
        return list_installed_releases()

    def register_reviewed_evidence_release(
        self,
        release_key: str,
    ) -> dict[str, Any]:
        release_key = _identifier(
            release_key,
            "release_key",
            RELEASE_KEY_PATTERN,
        )
        try:
            release = get_installed_release(release_key)
        except EvidenceManifestError as exc:
            raise WorkbenchError(str(exc)) from exc
        if release is None:
            raise WorkbenchNotFoundError("Reviewed evidence release not found")

        source_snapshot = release["source_snapshot"]
        now = _now()
        snapshot = {
            "snapshot_id": source_snapshot["snapshot_id"],
            "release_key": release["release_key"],
            "label": source_snapshot["label"],
            "manifest_schema_version": release["manifest_schema_version"],
            "manifest_sha256": release["manifest_sha256"],
            "status": source_snapshot["status"],
            "privacy_mode": release["privacy_mode"],
            "parser_contract_version": source_snapshot[
                "parser_contract_version"
            ],
            "time_start": source_snapshot["time_start"],
            "time_end_exclusive": source_snapshot["time_end_exclusive"],
            "source_rows": sum(
                source["rows"] for source in source_snapshot["sources"]
            ),
            "source_bytes": sum(
                source["bytes"] for source in source_snapshot["sources"]
            ),
            "quality": release["quality"],
            "claim_coverage": release["claim_coverage"],
            "limitation_codes": release["limitation_codes"],
            "created_at": now,
        }
        artifacts = [
            {
                **artifact,
                "release_key": release["release_key"],
                "created_at": now,
            }
            for artifact in release["analysis_artifacts"]
        ]
        try:
            created = self.store.register_evidence_release(
                self.tenant_id,
                snapshot,
                source_snapshot["sources"],
                artifacts,
                self._audit(
                    None,
                    "evidence_snapshot",
                    snapshot["snapshot_id"],
                    "registered",
                    {
                        "release_key": release["release_key"],
                        "manifest_sha256": release["manifest_sha256"],
                        "privacy_mode": release["privacy_mode"],
                    },
                    now,
                ),
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc
        return {
            "created": created,
            "snapshot": self.get_evidence_snapshot(snapshot["snapshot_id"]),
        }

    def list_evidence_snapshots(self) -> list[dict[str, Any]]:
        return self.store.list_evidence_snapshots(self.tenant_id)

    def list_installed_sensing_releases(self) -> list[dict[str, Any]]:
        return list_installed_sensing_releases()

    def register_reviewed_sensing_release(
        self,
        release_key: str,
    ) -> dict[str, Any]:
        release_key = _identifier(
            release_key,
            "release_key",
            RELEASE_KEY_PATTERN,
        )
        try:
            release = get_installed_sensing_release(release_key)
        except SensingReleaseError as exc:
            raise WorkbenchError(str(exc)) from exc
        if release is None:
            raise WorkbenchNotFoundError(
                "Reviewed sensing release not found"
            )
        if (
            release["manifest_schema_version"]
            == WINDOWED_SENSING_MANIFEST_SCHEMA_VERSION
        ):
            return self._register_reviewed_windowed_sensing_release(release)

        source_lineage = release["source_lineage"]
        source_snapshot = self.store.get_evidence_snapshot(
            self.tenant_id,
            source_lineage["source_snapshot_id"],
        )
        if source_snapshot is None or source_snapshot["status"] != "sealed":
            raise WorkbenchConflictError(
                "The parent source-evidence release must be registered first"
            )

        scope = release["sample_scope"]
        claims = release["claim_boundaries"]
        now = _now()
        snapshot = {
            "snapshot_id": release["snapshot_id"],
            "release_key": release["release_key"],
            "label": release["label"],
            "generated_at_utc": release["generated_at_utc"],
            "schema_version": release["manifest_schema_version"],
            "manifest_sha256": release["manifest_sha256"],
            "artifact_sha256": release["artifact_sha256"],
            "status": release["status"],
            "source_snapshot_id": source_lineage["source_snapshot_id"],
            "source_manifest_sha256": source_snapshot["manifest_sha256"],
            "taxonomy_release_sha256": source_lineage["taxonomy_sha256"],
            "persona_evidence_sha256": source_lineage[
                "persona_evidence_sha256"
            ],
            "persona_release_sha256": source_lineage[
                "persona_release_sha256"
            ],
            "evidence_stage": scope["evidence_stage"],
            "estimation_mode": scope["estimation_mode"],
            "causal_status": claims["causal_status"],
            "forecast_status": claims["forecast_status"],
            "risk_validity": claims["risk_validity"],
            "unit_of_analysis": scope["unit_of_analysis"],
            "stance_target_status": claims["stance_target_status"],
            "sample_size": scope["record_count"],
            "source_lineage": source_lineage,
            "scope": scope,
            "distributions": release["distributions"],
            "review_summary": release["review"],
            "privacy": release["privacy"],
            "claim_boundary": claims,
            "limitation_codes": release["limitation_codes"],
            "created_at": now,
        }
        windows = [
            {
                "window_id": "representative-reference-202512-202607",
                "label": "代表性人群语义参照样本",
                "window_role": "representative_population_reference",
                "start_inclusive": scope["time_start"],
                "end_exclusive": scope["time_end_exclusive"],
                "timezone": "Asia/Shanghai",
                "sample_count": scope["record_count"],
                "denominator_count": scope["record_count"],
                "coverage_status": "reviewed_sample_scope",
                "estimation_mode": scope["estimation_mode"],
                "month_support": release["month_support"],
            }
        ]
        issues = [
            self._normalize_sensing_aggregate(
                item,
                is_archetype=False,
            )
            for item in release["issue_domains"]
        ]
        archetypes = [
            self._normalize_sensing_aggregate(
                item,
                is_archetype=True,
            )
            for item in release["archetypes"]
        ]
        try:
            created = self.store.register_sensing_release(
                self.tenant_id,
                snapshot,
                windows,
                issues,
                archetypes,
                self._audit(
                    None,
                    "sensing_snapshot",
                    snapshot["snapshot_id"],
                    "registered",
                    {
                        "release_key": snapshot["release_key"],
                        "manifest_sha256": snapshot["manifest_sha256"],
                        "source_snapshot_id": snapshot[
                            "source_snapshot_id"
                        ],
                        "publication_mode": snapshot["privacy"][
                            "publication_mode"
                        ],
                    },
                    now,
                ),
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc
        return {
            "created": created,
            "snapshot": self.get_sensing_snapshot(snapshot["snapshot_id"]),
        }

    def _register_reviewed_windowed_sensing_release(
        self,
        release: dict[str, Any],
    ) -> dict[str, Any]:
        """Normalize one already-validated fixed aggregate package."""

        if release["release_key"] != REVIEWED_WINDOWED_SENSING_RELEASE_KEY:
            raise WorkbenchError("Unsupported windowed sensing release")
        lineage = release["source_lineage"]
        source_snapshot = self.store.get_evidence_snapshot(
            self.tenant_id,
            lineage["source_dataset_snapshot_id"],
        )
        if (
            source_snapshot is None
            or source_snapshot["status"] != "sealed"
            or source_snapshot["manifest_sha256"]
            != lineage["source_evidence_manifest_sha256"]
        ):
            raise WorkbenchConflictError(
                "The exact parent source-evidence release must be "
                "registered first"
            )

        now = _now()
        limitations = [
            "classification_error_excluded_from_interval",
            "comments_cross_regime_not_comparable",
            "descriptive_window_contrasts_are_not_causal_effects",
            "historical_post_episodes_are_not_unique_people",
            "model_coding_is_not_complete_human_annotation",
            "not_forecast_or_real_time_monitoring",
            "sampling_intervals_only",
        ]
        snapshot = {
            "snapshot_id": release["snapshot_id"],
            "release_key": release["release_key"],
            "label": "Reviewed three-window forum sensing release",
            "generated_at_utc": release["review_completed_at_utc"],
            "schema_version": release["manifest_schema_version"],
            "manifest_sha256": release["manifest_sha256"],
            "artifact_sha256": release["artifact_sha256"],
            "status": "reviewed_aggregate",
            "source_snapshot_id": lineage["source_dataset_snapshot_id"],
            "source_manifest_sha256": lineage[
                "source_evidence_manifest_sha256"
            ],
            "taxonomy_release_sha256": lineage["taxonomy_sha256"],
            # These two columns are v3 physical-schema compatibility slots.
            # Windowed API responses remove the aliases and expose canonical
            # source_lineage instead.
            "persona_evidence_sha256": lineage["source_semantic_sha256"],
            "persona_release_sha256": lineage["classification_sha256"],
            "evidence_stage": "retrospective_reviewed_windowed_sensing",
            "estimation_mode": "stratified_hajek_probability_sample",
            "causal_status": "descriptive_not_identified",
            "forecast_status": "not_claimed",
            "risk_validity": "historical_model_coded_only",
            "unit_of_analysis": "eligible_forum_post_episode",
            "stance_target_status": "controlled_target_coded",
            "sample_size": release["manifest"][
                "estimation_sample_count"
            ],
            "source_lineage": lineage,
            "scope": {
                "estimand": release["claim_contract"]["estimand"],
                "timezone": "Asia/Shanghai",
                "window_keys": release["manifest"]["window_keys"],
                "estimation_sample_count": release["manifest"][
                    "estimation_sample_count"
                ],
                "package_novelty_or_no_fit_ppm": release["manifest"][
                    "package_novelty_or_no_fit_ppm"
                ],
                "population_is_unique_people_or_students": False,
                "real_time_claimed": False,
            },
            "distributions": {
                "taxonomy": release["taxonomy"],
                "comparison_contract": release["comparison_contract"],
            },
            "review_summary": {
                "status": "review_complete",
                "windows": {
                    window["window_key"]: window["review"]
                    for window in release["windows"]
                },
                "artifact_audit": release["artifact_audit"],
            },
            "privacy": release["privacy"],
            "claim_boundary": release["claim_contract"],
            "limitation_codes": limitations,
            "created_at": now,
        }

        windows: list[dict[str, Any]] = []
        profiles: list[dict[str, Any]] = []
        segments: list[dict[str, Any]] = []
        issue_aggregates: list[dict[str, Any]] = []
        archetype_aggregates: list[dict[str, Any]] = []
        stance_aggregates: list[dict[str, Any]] = []
        response_aggregates: list[dict[str, Any]] = []
        event_aggregates: list[dict[str, Any]] = []
        archetype_domains = {
            item["archetype_id"]: item["broad_domain"]
            for item in release["taxonomy"]["archetypes"]
        }
        comparisons = release["comparison_contract"][
            "allowed_pairwise_comparisons"
        ]
        for window in release["windows"]:
            window_key = window["window_key"]
            first_segment = window["segments"][0]
            last_segment = window["segments"][-1]
            coverage_status = (
                "complete_non_contiguous_segments"
                if len(window["segments"]) > 1
                else "complete_half_open_window"
            )
            windows.append(
                {
                    "window_id": window_key,
                    "label": window_key,
                    "window_role": window["window_role"],
                    "start_inclusive": first_segment["start_inclusive"],
                    "end_exclusive": last_segment["end_exclusive"],
                    "timezone": "Asia/Shanghai",
                    "sample_count": window["sample_count"],
                    "denominator_count": window[
                        "eligible_episode_count"
                    ],
                    "coverage_status": coverage_status,
                    "estimation_mode": "stratified_hajek_share",
                    "month_support": [],
                }
            )
            profile = window["profile"]
            review = window["review"]
            relevant_comparisons = [
                item
                for item in comparisons
                if window_key
                in {
                    item["shock_window_key"],
                    item["reference_window_key"],
                }
            ]
            distribution_contracts = {
                key: {
                    contract_key: contract_value
                    for contract_key, contract_value in profile[key].items()
                    if contract_key != "cells"
                }
                for key in (
                    "archetype_distribution",
                    "issue_distribution",
                    "stance_distribution",
                    "response_distribution",
                    "event_relation_distribution",
                )
            }
            profiles.append(
                {
                    "window_id": window_key,
                    "eligible_count": window["eligible_episode_count"],
                    "estimate_sample_count": window["sample_count"],
                    "supplemental_review_count": review[
                        "supplemental_review_count"
                    ],
                    "kish_effective_sample_size_milli": window[
                        "effective_sample_size_milli"
                    ],
                    "review_status": "review_complete",
                    "direct_review_count": review["direct_review_count"],
                    "unresolved_count": review["unresolved_count"],
                    "direct_review_coverage_ppm": review[
                        "direct_review_coverage_ppm"
                    ],
                    "window_novelty_or_no_fit_ppm": profile[
                        "novelty_or_no_fit_ppm"
                    ],
                    "max_temporal_block_novelty_or_no_fit_ppm": profile[
                        "max_temporal_block_novelty_or_no_fit_ppm"
                    ],
                    "package_novelty_or_no_fit_ppm": release["manifest"][
                        "package_novelty_or_no_fit_ppm"
                    ],
                    "segment_standardization": {
                        "mode": window["segment_standardization"],
                        "segment_count": len(window["segments"]),
                    },
                    "sampling_contract": {
                        "sampling_strata": window["sampling_strata"],
                        "probability_sample": True,
                    },
                    "uncertainty_contract": {
                        "interval_scope": profile["interval_scope"],
                        "classification_error_in_interval": profile[
                            "classification_error_in_interval"
                        ],
                    },
                    "review_route_counts": review,
                    "comparison_eligibility": relevant_comparisons,
                    "window_distributions": distribution_contracts,
                    "limitation_codes": limitations,
                }
            )
            for segment in window["segments"]:
                is_reference = len(window["segments"]) > 1
                segment_comparisons = [
                    item
                    for item in relevant_comparisons
                    if (
                        item["reference_segment_id"]
                        == segment["segment_id"]
                        or item["shock_window_key"] == window_key
                    )
                ]
                segments.append(
                    {
                        "window_id": window_key,
                        "segment_id": segment["segment_id"],
                        "label": segment["segment_id"],
                        "segment_role": (
                            "matched_reference"
                            if is_reference
                            else "observed_shock_window"
                        ),
                        "matched_scenario_id": segment[
                            "matched_scenario_id"
                        ],
                        "start_inclusive": segment["start_inclusive"],
                        "end_exclusive": segment["end_exclusive"],
                        "timezone": "Asia/Shanghai",
                        "comment_regime": segment[
                            "comment_collection_regime"
                        ],
                        "eligible_count": segment[
                            "eligible_episode_count"
                        ],
                        "estimate_sample_count": segment["sample_count"],
                        "supplemental_review_count": 0,
                        "standardization_weight_numerator": 1,
                        "standardization_weight_denominator": (
                            len(window["segments"])
                        ),
                        "coverage_status": "complete_probability_sample",
                        "temporal_blocks": segment["temporal_blocks"],
                        "sampling_contract": {
                            "anchor_resolution": segment[
                                "anchor_resolution"
                            ],
                            "profile": segment["profile"],
                        },
                        "comparison_eligibility": segment_comparisons,
                    }
                )

            dimensions = (
                (
                    "issue_distribution",
                    ("broad_domain",),
                    issue_aggregates,
                    lambda cell: {
                        "issue_domain_id": cell["broad_domain"],
                        "label": cell["broad_domain"],
                    },
                ),
                (
                    "archetype_distribution",
                    ("archetype_id",),
                    archetype_aggregates,
                    lambda cell: {
                        "archetype_id": cell["archetype_id"],
                        "issue_domain_id": archetype_domains[
                            cell["archetype_id"]
                        ],
                        "label": cell["archetype_id"],
                    },
                ),
                (
                    "stance_distribution",
                    ("stance_target", "stance_orientation"),
                    stance_aggregates,
                    lambda cell: {
                        "stance_target": cell["stance_target"],
                        "stance_orientation": cell[
                            "stance_orientation"
                        ],
                    },
                ),
                (
                    "response_distribution",
                    ("response_need",),
                    response_aggregates,
                    lambda cell: {
                        "response_need_type": cell["response_need"],
                        "label": cell["response_need"],
                    },
                ),
                (
                    "event_relation_distribution",
                    ("event_relation",),
                    event_aggregates,
                    lambda cell: {
                        "event_relation": cell["event_relation"],
                        "label": cell["event_relation"],
                    },
                ),
            )
            for dimension, _, target, category_values in dimensions:
                distribution = profile[dimension]
                estimation_contract = {
                    key: value
                    for key, value in distribution.items()
                    if key != "cells"
                }
                for cell in distribution["cells"]:
                    suppressed = cell["suppressed"]
                    target.append(
                        {
                            "window_id": window_key,
                            **category_values(cell),
                            "sample_count": (
                                0
                                if suppressed
                                else cell["support_count"]
                            ),
                            "denominator_count": window["sample_count"],
                            "estimate_ppm": (
                                0
                                if suppressed
                                else cell["weighted_share_ppm"]
                            ),
                            "lower_bound_ppm": (
                                0
                                if suppressed
                                else cell["interval_95_lower_ppm"]
                            ),
                            "upper_bound_ppm": (
                                0
                                if suppressed
                                else cell["interval_95_upper_ppm"]
                            ),
                            "effective_sample_size_milli": window[
                                "effective_sample_size_milli"
                            ],
                            "suppressed": suppressed,
                            "estimation_contract": estimation_contract,
                            "review_support": {
                                "suppression_code": cell[
                                    "suppression_code"
                                ],
                                "review_status": "review_complete",
                            },
                        }
                    )

        try:
            created = self.store.register_windowed_sensing_release(
                self.tenant_id,
                snapshot,
                windows,
                profiles,
                segments,
                issue_aggregates,
                archetype_aggregates,
                stance_aggregates,
                response_aggregates,
                event_aggregates,
                self._audit(
                    None,
                    "sensing_snapshot",
                    snapshot["snapshot_id"],
                    "registered",
                    {
                        "release_key": snapshot["release_key"],
                        "manifest_sha256": snapshot["manifest_sha256"],
                        "source_snapshot_id": snapshot[
                            "source_snapshot_id"
                        ],
                        "publication_mode": snapshot["privacy"][
                            "publication_mode"
                        ],
                        "window_count": 3,
                    },
                    now,
                ),
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc
        return {
            "created": created,
            "snapshot": self.get_sensing_snapshot(snapshot["snapshot_id"]),
        }

    @staticmethod
    def _normalize_sensing_aggregate(
        item: dict[str, Any],
        *,
        is_archetype: bool,
    ) -> dict[str, Any]:
        domain = item["broad_domain"]
        distributions = item["distributions"]
        review = item["review"]
        normalized = {
            "issue_domain_id": domain,
            "label": (
                item["label"]
                if is_archetype
                else ISSUE_DOMAIN_LABELS[domain]
            ),
            "sample_count": item["sample_count"],
            "denominator_count": item["denominator_count"],
            "share_ppm": item["share_ppm"],
            "suppressed": False,
            "stance_counts": distributions["stance"],
            "semantic_determinacy_counts": distributions[
                "semantic_determinacy"
            ],
            "language_form_counts": distributions["language_form"],
            "risk_signal_counts": distributions["risk_level"],
            "speaker_relation_counts": distributions["speaker_relation"],
            "speech_act_counts": distributions["speech_act"],
            "boundary_case_count": review["base_model_boundary_count"],
            "review_basis_counts": review["release_basis_counts"],
            "reviewer_certainty_counts": review[
                "reviewer_certainty_counts"
            ],
        }
        if not is_archetype:
            return {
                **normalized,
                "archetype_ids": item["archetype_ids"],
            }
        return {
            **normalized,
            "archetype_id": item["archetype_id"],
            "interaction_role": item["interaction_role"],
            "core_need": item["core_need"],
            "expected_response": item["expected_response"],
            "governance_relevance": item["governance_relevance"],
            "simulatable": domain != "other_or_ambiguous",
            "month_support": item["month_support"],
        }

    def list_sensing_snapshots(self) -> list[dict[str, Any]]:
        return self.store.list_sensing_snapshots(self.tenant_id)

    def get_sensing_snapshot(
        self,
        snapshot_id: str,
    ) -> dict[str, Any]:
        snapshot_id = _identifier(
            snapshot_id,
            "snapshot_id",
            EVIDENCE_HASH_PATTERN,
        )
        snapshot = self.store.get_sensing_snapshot(
            self.tenant_id,
            snapshot_id,
        )
        if snapshot is None:
            raise WorkbenchNotFoundError("Sensing snapshot not found")
        return snapshot

    def attach_project_sensing(
        self,
        project_id: str,
        snapshot_id: str,
    ) -> dict[str, Any]:
        self.get_project(project_id)
        snapshot = self.get_sensing_snapshot(snapshot_id)
        now = _now()
        try:
            created = self.store.attach_project_sensing(
                self.tenant_id,
                project_id,
                snapshot["snapshot_id"],
                self._audit(
                    project_id,
                    "project_sensing_binding",
                    snapshot["snapshot_id"],
                    "attached",
                    {
                        "manifest_sha256": snapshot["manifest_sha256"],
                        "activated": False,
                    },
                    now,
                ),
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc
        bindings = self.list_project_sensing(project_id)
        binding = next(
            (
                item
                for item in bindings
                if item["snapshot_id"] == snapshot["snapshot_id"]
            ),
            None,
        )
        if binding is None:
            raise WorkbenchNotFoundError(
                "Project or sensing snapshot not found"
            )
        return {
            "created": created,
            "binding": binding,
        }

    def list_project_sensing(
        self,
        project_id: str,
    ) -> list[dict[str, Any]]:
        self.get_project(project_id)
        return self.store.list_project_sensing(
            self.tenant_id,
            project_id,
        )

    def get_project_sensing_state(
        self,
        project_id: str,
    ) -> dict[str, Any]:
        self.get_project(project_id)
        state = self.store.get_project_sensing_state(
            self.tenant_id,
            project_id,
        )
        if state is None:
            return {
                "status": "not_configured",
                "state_version": 0,
                "active_snapshot_id": None,
                "activated_at": None,
                "snapshot": None,
            }
        snapshot = self.get_sensing_snapshot(
            state["active_snapshot_id"]
        )
        return {
            "status": "active",
            "state_version": state["state_version"],
            "active_snapshot_id": state["active_snapshot_id"],
            "activated_at": state["activated_at"],
            "snapshot": snapshot,
        }

    def activate_project_sensing(
        self,
        project_id: str,
        snapshot_id: str,
        expected_state_version: int,
    ) -> dict[str, Any]:
        self.get_project(project_id)
        snapshot = self.get_sensing_snapshot(snapshot_id)
        if (
            not isinstance(expected_state_version, int)
            or isinstance(expected_state_version, bool)
            or expected_state_version < 0
        ):
            raise WorkbenchError(
                "expected_state_version must be a non-negative integer"
            )
        if not self.store.project_has_sensing(
            self.tenant_id,
            project_id,
            snapshot["snapshot_id"],
        ):
            raise WorkbenchConflictError(
                "Sensing snapshot must be attached before activation"
            )
        now = _now()
        try:
            changed = self.store.activate_project_sensing(
                self.tenant_id,
                project_id,
                snapshot["snapshot_id"],
                expected_state_version,
                self._audit(
                    project_id,
                    "project_sensing_state",
                    snapshot["snapshot_id"],
                    "activated",
                    {
                        "expected_state_version": expected_state_version,
                        "manifest_sha256": snapshot["manifest_sha256"],
                    },
                    now,
                ),
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc
        return {
            **self.get_project_sensing_state(project_id),
            "changed": changed,
        }

    def get_evidence_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        snapshot_id = _identifier(
            snapshot_id,
            "snapshot_id",
            EVIDENCE_HASH_PATTERN,
        )
        snapshot = self.store.get_evidence_snapshot(
            self.tenant_id,
            snapshot_id,
        )
        if snapshot is None:
            raise WorkbenchNotFoundError("Evidence snapshot not found")
        return snapshot

    def attach_project_evidence(
        self,
        project_id: str,
        snapshot_id: str,
        make_primary: bool = True,
    ) -> dict[str, Any]:
        self.get_project(project_id)
        snapshot = self.get_evidence_snapshot(snapshot_id)
        if not isinstance(make_primary, bool):
            raise WorkbenchError("make_primary must be a boolean")
        now = _now()
        changed = self.store.attach_project_evidence(
            self.tenant_id,
            project_id,
            snapshot["snapshot_id"],
            make_primary,
            self._audit(
                project_id,
                "project_evidence_binding",
                snapshot["snapshot_id"],
                "attached",
                {
                    "make_primary": make_primary,
                    "manifest_sha256": snapshot["manifest_sha256"],
                },
                now,
            ),
        )
        if (
            not changed
            and not self.store.project_has_evidence(
                self.tenant_id,
                project_id,
                snapshot["snapshot_id"],
            )
        ):
            raise WorkbenchNotFoundError(
                "Project or evidence snapshot not found"
            )
        bindings = self.list_project_evidence(project_id)
        selected = next(
            item
            for item in bindings
            if item["snapshot_id"] == snapshot["snapshot_id"]
        )
        return {
            "created_or_changed": changed,
            "binding": selected,
        }

    def list_project_evidence(
        self,
        project_id: str,
    ) -> list[dict[str, Any]]:
        self.get_project(project_id)
        return self.store.list_project_evidence(
            self.tenant_id,
            project_id,
        )

    def _evidence_binding_for_analysis(
        self,
        project_id: str,
        analysis_id: str,
    ) -> dict[str, Any]:
        analysis_id = _identifier(
            analysis_id,
            "analysis_id",
            EVIDENCE_HASH_PATTERN,
        )
        artifact = self.store.get_analysis_artifact(
            self.tenant_id,
            analysis_id,
        )
        if artifact is None:
            raise WorkbenchNotFoundError(
                "Registered analysis evidence not found"
            )
        snapshot = self.get_evidence_snapshot(artifact["snapshot_id"])
        if not self.store.project_has_evidence(
            self.tenant_id,
            project_id,
            snapshot["snapshot_id"],
        ):
            raise WorkbenchConflictError(
                "Evidence snapshot must be attached to the project first"
            )
        return {
            "tenant_id": self.tenant_id,
            "project_id": project_id,
            "snapshot_id": snapshot["snapshot_id"],
            "analysis_id": artifact["analysis_id"],
            "manifest_sha256": snapshot["manifest_sha256"],
        }

    def _with_scenario_evidence(
        self,
        scenario: dict[str, Any],
    ) -> dict[str, Any]:
        binding = self.store.get_scenario_evidence(
            scenario["scenario_id"]
        )
        if (
            binding is None
            or binding["tenant_id"] != self.tenant_id
            or binding["project_id"] != scenario["project_id"]
            or binding["status"] != "sealed"
            or scenario["evidence_snapshot_id"] != binding["analysis_id"]
        ):
            return {
                **scenario,
                "analysis_id": scenario["evidence_snapshot_id"],
                "evidence_binding_status": "legacy_unbound",
                "source_evidence": None,
            }
        return {
            **scenario,
            "analysis_id": binding["analysis_id"],
            "evidence_binding_status": "sealed",
            "source_evidence": {
                key: binding[key]
                for key in (
                    "snapshot_id",
                    "analysis_id",
                    "manifest_sha256",
                    "release_key",
                    "snapshot_label",
                    "status",
                    "privacy_mode",
                    "parser_contract_version",
                    "time_start",
                    "time_end_exclusive",
                    "source_rows",
                    "source_bytes",
                    "quality",
                    "claim_coverage",
                    "limitation_codes",
                    "analysis_label",
                    "artifact_kind",
                    "artifact_sha256",
                    "evidence_stage",
                    "quality_grade",
                    "causal_status",
                )
            },
        }

    def create_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        mode = payload.get("evaluation_mode", "simulation_stress_test")
        if mode not in PROJECT_MODES:
            raise WorkbenchError("evaluation_mode is not allowed")
        project = {
            "project_id": _new_id("prj"),
            "tenant_id": self.tenant_id,
            "name": _bounded_text(payload.get("name"), "name", maximum=120),
            "governance_domain": _bounded_text(
                payload.get("governance_domain"),
                "governance_domain",
                maximum=80,
            ),
            "objective": _bounded_text(
                payload.get("objective"),
                "objective",
                maximum=800,
            ),
            "evaluation_mode": mode,
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        try:
            return self.store.create_project(
                project,
                self._audit(
                    project["project_id"],
                    "project",
                    project["project_id"],
                    "created",
                    {"evaluation_mode": mode},
                    now,
                ),
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc

    def list_projects(self) -> list[dict[str, Any]]:
        return self.store.list_projects(self.tenant_id)

    def get_project(self, project_id: str) -> dict[str, Any]:
        project_id = _identifier(project_id, "project_id", ID_PATTERN)
        project = self.store.get_project(self.tenant_id, project_id)
        if project is None:
            raise WorkbenchNotFoundError("Project not found")
        return project

    def update_project(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.get_project(project_id)
        allowed_fields = {
            "name",
            "governance_domain",
            "objective",
            "evaluation_mode",
        }
        if (
            not isinstance(payload, dict)
            or not payload
            or not set(payload).issubset(allowed_fields)
        ):
            raise WorkbenchError(
                "Project update must contain at least one allowed field"
            )

        updates: dict[str, Any] = {}
        if "name" in payload:
            updates["name"] = _bounded_text(
                payload["name"],
                "name",
                maximum=120,
            )
        if "governance_domain" in payload:
            updates["governance_domain"] = _bounded_text(
                payload["governance_domain"],
                "governance_domain",
                maximum=80,
            )
        if "objective" in payload:
            updates["objective"] = _bounded_text(
                payload["objective"],
                "objective",
                maximum=800,
            )
        if "evaluation_mode" in payload:
            mode = payload["evaluation_mode"]
            if mode not in PROJECT_MODES:
                raise WorkbenchError("evaluation_mode is not allowed")
            updates["evaluation_mode"] = mode

        now = _now()
        updates["updated_at"] = now
        try:
            project = self.store.update_project(
                self.tenant_id,
                project_id,
                updates,
                self._audit(
                    project_id,
                    "project",
                    project_id,
                    "updated",
                    {
                        "changed_fields": sorted(
                            field
                            for field in updates
                            if field != "updated_at"
                        )
                    },
                    now,
                ),
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc
        if project is None:
            raise WorkbenchNotFoundError("Project not found")
        return project

    def archive_project(self, project_id: str) -> None:
        project_id = _identifier(project_id, "project_id", ID_PATTERN)
        self.get_project(project_id)
        now = _now()
        archived = self.store.archive_project(
            self.tenant_id,
            project_id,
            now,
            self._audit(
                project_id,
                "project",
                project_id,
                "archived",
                {"user_visible_deletion": True},
                now,
            ),
        )
        if not archived:
            raise WorkbenchNotFoundError("Project not found")

    def bootstrap_reviewed_templates(self, project_id: str, setup: dict[str, Any] | None = None) -> dict[str, Any]:
        project = self.get_project(project_id)
        scenario_templates, policy_templates = self._templates_for_project(project)
        if setup is not None:
            # The wizard owns a single scenario. Legacy bootstrap callers retain
            # their original templates; new projects use an explicit family.
            family = setup["family"]
            if family == "century_gym":
                policy_templates = CENTURY_GYM_POLICY_TEMPLATES
            else:
                policy_templates = REVIEWED_POLICY_TEMPLATES
            scenario_templates = ({
                "template_key": f"project_setup_{family}_v1",
                "name": project["name"],
                "description": setup["description"],
                "shock_origin": "external_event",
                "evidence_snapshot_id": ANALYSIS_SNAPSHOT_ID,
                "claim_boundary": setup["scope"],
                "phases": [
                    {"phase_id": "baseline", "label": "事件前", "window": "Tick 0–2"},
                    {"phase_id": "burst", "label": "事件出现", "window": "Tick 3–4"},
                    {"phase_id": "spread", "label": "讨论扩散", "window": "Tick 5–8"},
                    {"phase_id": "decay", "label": "后续反馈", "window": "Tick 9–10"},
                ],
            },)
        registered = self.register_reviewed_evidence_release(
            REVIEWED_RELEASE_KEY
        )
        source_snapshot = registered["snapshot"]
        attachment = self.attach_project_evidence(
            project_id,
            source_snapshot["snapshot_id"],
            make_primary=True,
        )
        sensing_registered = self.register_reviewed_sensing_release(
            REVIEWED_WINDOWED_SENSING_RELEASE_KEY
        )
        sensing_attachment = self.attach_project_sensing(
            project_id,
            sensing_registered["snapshot"]["snapshot_id"],
        )
        sensing_state = self.get_project_sensing_state(project_id)
        target_sensing_snapshot_id = sensing_registered["snapshot"][
            "snapshot_id"
        ]
        if (
            sensing_state["status"] == "not_configured"
            or sensing_state["active_snapshot_id"]
            != target_sensing_snapshot_id
        ):
            try:
                sensing_state = self.activate_project_sensing(
                    project_id,
                    target_sensing_snapshot_id,
                    sensing_state["state_version"],
                )
            except WorkbenchConflictError:
                # Concurrent bootstrap may have activated this exact current
                # release after both callers observed the same state version.
                # Treat only that same resulting state as idempotent success.
                sensing_state = self.get_project_sensing_state(project_id)
                if (
                    sensing_state["status"] != "active"
                    or sensing_state["active_snapshot_id"]
                    != target_sensing_snapshot_id
                ):
                    raise
        evidence_binding = self._evidence_binding_for_analysis(
            project_id,
            ANALYSIS_SNAPSHOT_ID,
        )
        scenarios = []
        policies = []

        for template in scenario_templates:
            scenario = self._build_scenario_record(project_id, template)
            scenarios.append(
                (
                    scenario,
                    self._audit(
                        project_id,
                        "scenario",
                        scenario["scenario_id"],
                        "created",
                        {"template_key": scenario["template_key"]},
                        scenario["created_at"],
                    ),
                )
            )

        for template in policy_templates:
            policy = self._build_policy_record(project_id, template)
            policies.append(
                (
                    policy,
                    self._audit(
                        project_id,
                        "policy",
                        policy["policy_id"],
                        "created",
                        {
                            "template_key": policy["template_key"],
                            "evaluation_level": policy["evaluation_level"],
                        },
                        policy["created_at"],
                    ),
                )
            )

        try:
            created = self.store.bootstrap_templates(
                scenarios,
                policies,
                evidence_binding,
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc

        return {
            "project": project,
            "created": created,
            "evidence_registration": registered["created"],
            "evidence_binding": attachment,
            "evidence_snapshots": self.list_project_evidence(project_id),
            "sensing_registration": sensing_registered["created"],
            "sensing_binding": sensing_attachment,
            "sensing_state": sensing_state,
            "sensing_snapshots": self.list_project_sensing(project_id),
            "scenarios": self.list_scenarios(project_id),
            "policies": self.list_policies(project_id),
        }

    def create_scenario(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.get_project(project_id)
        scenario = self._build_scenario_record(project_id, payload)
        evidence_binding = self._evidence_binding_for_analysis(
            project_id,
            scenario["evidence_snapshot_id"],
        )
        try:
            created = self.store.create_scenario(
                scenario,
                {
                    **evidence_binding,
                    "bound_at": scenario["created_at"],
                },
                self._audit(
                    project_id,
                    "scenario",
                    scenario["scenario_id"],
                    "created",
                    {"template_key": scenario.get("template_key")},
                    scenario["created_at"],
                ),
            )
            return self._with_scenario_evidence(created)
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc

    def list_scenarios(self, project_id: str) -> list[dict[str, Any]]:
        project = self.get_project(project_id)
        selected_templates = self._templates_for_project(project)[0]
        selected_keys = {template["template_key"] for template in selected_templates}
        reviewed_keys = {
            template["template_key"]
            for template in (*REVIEWED_SCENARIO_TEMPLATES, *CENTURY_GYM_SCENARIO_TEMPLATES)
        }
        scenarios = [
            self._with_scenario_evidence(scenario)
            for scenario in self.store.list_scenarios(project_id)
            if scenario.get("template_key") in selected_keys
            or scenario.get("template_key") not in reviewed_keys
        ]
        template_order = {
            template["template_key"]: index
            for index, template in enumerate(selected_templates)
        }
        return sorted(
            scenarios,
            key=lambda scenario: (
                template_order.get(
                    scenario.get("template_key"),
                    len(template_order),
                ),
                scenario["created_at"],
                scenario["scenario_id"],
            ),
        )

    def create_policy(
        self,
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.get_project(project_id)
        policy = self._build_policy_record(project_id, payload)
        try:
            return self.store.create_policy(
                policy,
                self._audit(
                    project_id,
                    "policy",
                    policy["policy_id"],
                    "created",
                    {
                        "template_key": policy.get("template_key"),
                        "evaluation_level": policy["evaluation_level"],
                    },
                    policy["created_at"],
                ),
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc

    def list_policies(self, project_id: str) -> list[dict[str, Any]]:
        project = self.get_project(project_id)
        selected_templates = self._templates_for_project(project)[1]
        selected_keys = {template["template_key"] for template in selected_templates}
        reviewed_keys = {
            template["template_key"]
            for template in (*REVIEWED_POLICY_TEMPLATES, *CENTURY_GYM_POLICY_TEMPLATES)
        }
        policies = [
            policy
            for policy in self.store.list_policies(project_id)
            if policy.get("template_key") in selected_keys
            or policy.get("template_key") not in reviewed_keys
        ]
        template_order = {
            template["template_key"]: index
            for index, template in enumerate(selected_templates)
        }
        return sorted(
            policies,
            key=lambda policy: (
                template_order.get(
                    policy.get("template_key"),
                    len(template_order),
                ),
                policy["created_at"],
                policy["policy_id"],
            ),
        )

    def _templates_for_project(
        self,
        project: dict[str, Any],
    ) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
        keys = {s.get("template_key") for s in self.store.list_scenarios(project["project_id"])}
        if "project_setup_century_gym_v1" in keys:
            return CENTURY_GYM_SCENARIO_TEMPLATES, CENTURY_GYM_POLICY_TEMPLATES
        if any(str(key).startswith("project_setup_") for key in keys):
            return REVIEWED_SCENARIO_TEMPLATES, REVIEWED_POLICY_TEMPLATES
        corpus = " ".join(
            str(project.get(field, ""))
            for field in ("name", "governance_domain", "objective")
        )
        if any(marker in corpus for marker in ("世纪馆", "体育场地预约", "幽灵预约")):
            return CENTURY_GYM_SCENARIO_TEMPLATES, CENTURY_GYM_POLICY_TEMPLATES
        return REVIEWED_SCENARIO_TEMPLATES, REVIEWED_POLICY_TEMPLATES

    @staticmethod
    def _reviewed_scenario_template(
        template_key: str | None,
    ) -> dict[str, Any] | None:
        return next(
            (
                template
                for template in (*REVIEWED_SCENARIO_TEMPLATES, *CENTURY_GYM_SCENARIO_TEMPLATES)
                if template["template_key"] == template_key
            ),
            None,
        )

    @staticmethod
    def _reviewed_policy_template(
        template_key: str | None,
    ) -> dict[str, Any] | None:
        return next(
            (
                template
                for template in (*REVIEWED_POLICY_TEMPLATES, *CENTURY_GYM_POLICY_TEMPLATES)
                if template["template_key"] == template_key
            ),
            None,
        )

    def _assert_exact_reviewed_scenario(
        self,
        scenario: dict[str, Any],
    ) -> None:
        template = self._reviewed_scenario_template(
            scenario.get("template_key")
        )
        if template is None:
            raise WorkbenchConflictError(
                "Scenario is not an installed reviewed demo template"
            )
        expected = self._build_scenario_record(
            scenario["project_id"],
            template,
        )
        identity_fields = (
            "template_key",
            "name",
            "description",
            "shock_origin",
            "phases",
            "evidence_snapshot_id",
            "claim_boundary",
            "status",
        )
        if any(
            scenario.get(field) != expected.get(field)
            for field in identity_fields
        ):
            raise WorkbenchConflictError(
                "Reviewed scenario template identity does not match its "
                "installed version"
            )

    def _assert_exact_reviewed_policy(
        self,
        policy: dict[str, Any],
    ) -> None:
        template = self._reviewed_policy_template(
            policy.get("template_key")
        )
        if template is None:
            raise WorkbenchConflictError(
                "Policy is not an installed reviewed demo template"
            )
        expected = self._build_policy_record(
            policy["project_id"],
            template,
        )
        identity_fields = (
            "template_key",
            "name",
            "description",
            "is_baseline",
            "actions",
            "evaluation_level",
            "status",
        )
        if any(
            policy.get(field) != expected.get(field)
            for field in identity_fields
        ):
            raise WorkbenchConflictError(
                "Reviewed policy template identity does not match its "
                "installed version"
            )

    def _validate_reviewed_demo_selection(
        self,
        scenario: dict[str, Any],
        policies: list[dict[str, Any]],
        *,
        require_demo_scenario: bool,
    ) -> None:
        scenario_key = scenario.get("template_key")
        allowed_policy_keys = DEMO_RUNTIME_TEMPLATE_WHITELIST.get(
            scenario_key
        )
        if allowed_policy_keys is None:
            if require_demo_scenario:
                raise WorkbenchConflictError(
                    "Run is outside the reviewed demo runtime whitelist"
                )
            return

        self._assert_exact_reviewed_scenario(scenario)
        for policy in policies:
            policy_key = policy.get("template_key")
            if policy_key not in allowed_policy_keys:
                raise WorkbenchConflictError(
                    "Policy combination is outside the reviewed demo "
                    "runtime whitelist"
                )
            self._assert_exact_reviewed_policy(policy)

    def _frozen_demo_template_identity(
        self,
        run: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve v3 identities from exact fingerprinted content.

        Run-plan v3 has a strict executable schema and cannot safely gain
        template-key fields in place. Its executable template content is
        already frozen by ``input_fingerprint``; this compatibility projection
        resolves the versioned keys only when every executable field matches.
        """

        snapshot = run.get("input_snapshot")
        if not isinstance(snapshot, dict):
            return {
                "contract": "demo-runtime-template-identity-v1",
                "compatibility_mode": (
                    "run_plan_v3_exact_content_projection"
                ),
                "status": "unrecognized",
                "scenario_template_key": None,
                "policy_template_keys": [],
                "frozen_by_input_fingerprint": False,
            }

        frozen_scenario = snapshot.get("scenario")
        scenario_key = None
        if isinstance(frozen_scenario, dict):
            for template in REVIEWED_SCENARIO_TEMPLATES:
                expected = self._build_scenario_record(
                    run["project_id"],
                    template,
                )
                fields = (
                    "name",
                    "shock_origin",
                    "phases",
                    "evidence_snapshot_id",
                    "claim_boundary",
                )
                if all(
                    frozen_scenario.get(field) == expected.get(field)
                    for field in fields
                ):
                    scenario_key = template["template_key"]
                    break

        policy_keys: list[str] = []
        frozen_policies = snapshot.get("policies")
        if isinstance(frozen_policies, list):
            for frozen_policy in frozen_policies:
                matched_key = None
                if isinstance(frozen_policy, dict):
                    for template in REVIEWED_POLICY_TEMPLATES:
                        expected = self._build_policy_record(
                            run["project_id"],
                            template,
                        )
                        fields = (
                            "name",
                            "is_baseline",
                            "actions",
                            "evaluation_level",
                        )
                        if all(
                            frozen_policy.get(field) == expected.get(field)
                            for field in fields
                        ):
                            matched_key = template["template_key"]
                            break
                if matched_key is None:
                    policy_keys = []
                    break
                policy_keys.append(matched_key)

        snapshot_schema = snapshot.get("schema_version")
        allowed = (
            MASS_RUNTIME_TEMPLATE_WHITELIST.get(scenario_key)
            if snapshot_schema
            in {
                MASS_RUN_PLAN_SCHEMA_VERSION,
                GOVERNANCE_V2_RUN_PLAN_SCHEMA_VERSION,
                LLM_POPULATION_RUN_PLAN_SCHEMA_VERSION,
                FORUM_TWIN_RUN_PLAN_SCHEMA_VERSION,
                FORUM_TWIN_V2_RUN_PLAN_SCHEMA_VERSION,
            }
            else DEMO_RUNTIME_TEMPLATE_WHITELIST.get(scenario_key)
        )
        recognized = (
            snapshot_schema
            in {
                "campus-pulse-run-plan-v3",
                LIVE_PLAN_SCHEMA_VERSION,
                MASS_RUN_PLAN_SCHEMA_VERSION,
                GOVERNANCE_V2_RUN_PLAN_SCHEMA_VERSION,
                LLM_POPULATION_RUN_PLAN_SCHEMA_VERSION,
                FORUM_TWIN_RUN_PLAN_SCHEMA_VERSION,
                FORUM_TWIN_V2_RUN_PLAN_SCHEMA_VERSION,
            }
            and allowed is not None
            and bool(policy_keys)
            and len(policy_keys) == len(frozen_policies or [])
            and all(key in allowed for key in policy_keys)
        )
        return {
            "contract": "demo-runtime-template-identity-v1",
            "compatibility_mode": (
                "run_plan_v4_explicit_mapping"
                if snapshot_schema == LIVE_PLAN_SCHEMA_VERSION
                else (
                    "run_plan_v9_llm_forum_twin_v2"
                    if snapshot_schema == FORUM_TWIN_V2_RUN_PLAN_SCHEMA_VERSION
                    else (
                    "run_plan_v8_llm_forum_twin"
                    if snapshot_schema == FORUM_TWIN_RUN_PLAN_SCHEMA_VERSION
                    else (
                    "run_plan_v7_budgeted_llm_agent_population"
                    if snapshot_schema
                    == LLM_POPULATION_RUN_PLAN_SCHEMA_VERSION
                    else (
                    "run_plan_v6_adaptive_particle_population"
                    if snapshot_schema
                    == GOVERNANCE_V2_RUN_PLAN_SCHEMA_VERSION
                    else (
                        "run_plan_v5_budgeted_population"
                        if snapshot_schema
                        == MASS_RUN_PLAN_SCHEMA_VERSION
                        else "run_plan_v3_exact_content_projection"
                    )
                    )
                    )
                    )
                )
            ),
            "status": "recognized" if recognized else "unrecognized",
            "scenario_template_key": scenario_key,
            "policy_template_keys": policy_keys,
            "frozen_by_input_fingerprint": bool(
                recognized and run.get("input_fingerprint")
            ),
        }

    def _with_run_runtime_summary(
        self,
        run: dict[str, Any],
    ) -> dict[str, Any]:
        enriched = self._with_run_evidence(run)
        snapshot = enriched.get("input_snapshot")
        if (
            isinstance(snapshot, dict)
            and snapshot.get("schema_version") == LIVE_PLAN_SCHEMA_VERSION
        ):
            public_snapshot = deepcopy(snapshot)
            live_runtime = public_snapshot.get("live_runtime")
            if isinstance(live_runtime, dict):
                live_runtime["provider_profile"] = (
                    get_reviewed_provider_profile()
                )
            enriched = {
                **enriched,
                "input_snapshot": public_snapshot,
            }
        runtime = self.store.get_run_runtime(
            self.tenant_id,
            run["project_id"],
            run["run_id"],
        )
        runtime_summary = (
            self._public_runtime(runtime) if runtime is not None else None
        )
        plan_status = run["status"]
        return {
            **enriched,
            "plan_status": plan_status,
            "runtime_summary": runtime_summary,
            "effective_runtime_status": (
                runtime_summary["status"]
                if runtime_summary is not None
                else plan_status
            ),
            "template_identity": self._frozen_demo_template_identity(run),
        }

    def plan_run(
        self,
        project_id: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        project = self.get_project(project_id)
        if idempotency_key is not None:
            idempotency_key = _bounded_text(
                idempotency_key,
                "idempotency_key",
                minimum=8,
                maximum=128,
            )
        execution_mode = payload.get("execution_mode", "audited_replay")
        if execution_mode not in EXECUTION_MODES:
            raise WorkbenchError("execution_mode is not allowed")
        if execution_mode == LIVE_LLM_ADAPTER_MODE:
            unknown_live_fields = set(payload) - LIVE_PLAN_REQUEST_FIELDS
            if unknown_live_fields:
                raise WorkbenchError(
                    "Live planning fields are incomplete or unsupported"
                )
            try:
                assert_no_public_provider_configuration(payload)
            except LiveRuntimeContractError as exc:
                raise WorkbenchError(str(exc)) from exc
        elif any(
            field in payload
            for field in ("provider_profile_id", "provider_call_limit")
        ):
            raise WorkbenchError(
                "Live provider fields require execution_mode live_llm"
            )

        scenario_id = _identifier(
            payload.get("scenario_id"),
            "scenario_id",
            ID_PATTERN,
        )
        scenario = self.store.get_scenario(scenario_id)
        if scenario is None or scenario["project_id"] != project_id:
            raise WorkbenchNotFoundError("Scenario does not belong to project")
        evidence = self.store.get_scenario_evidence(scenario_id)
        if (
            evidence is None
            or evidence["tenant_id"] != self.tenant_id
            or evidence["project_id"] != project_id
        ):
            raise WorkbenchConflictError(
                "Scenario has no sealed source-evidence binding"
            )
        sensing_state = self.get_project_sensing_state(project_id)
        if (
            sensing_state["status"] != "active"
            or sensing_state["snapshot"] is None
        ):
            raise WorkbenchConflictError(
                "Project has no explicitly active sensing snapshot"
            )
        sensing = sensing_state["snapshot"]
        if (
            sensing["source_snapshot_id"] != evidence["snapshot_id"]
            or sensing["source_manifest_sha256"]
            != evidence["manifest_sha256"]
        ):
            raise WorkbenchConflictError(
                "Active sensing and scenario evidence do not share the "
                "same sealed source snapshot"
            )

        policy_ids = payload.get("policy_ids")
        if (
            not isinstance(policy_ids, list)
            or not 1 <= len(policy_ids) <= 8
            or len(set(policy_ids)) != len(policy_ids)
        ):
            raise WorkbenchError("policy_ids must contain 1 to 8 unique IDs")
        policies = []
        for policy_id in policy_ids:
            _identifier(policy_id, "policy_id", ID_PATTERN)
            policy = self.store.get_policy(policy_id)
            if policy is None or policy["project_id"] != project_id:
                raise WorkbenchNotFoundError("Policy does not belong to project")
            policies.append(policy)

        # Custom research drafts remain plannable, but a reviewed demo
        # scenario may never be paired with an unreviewed or disallowed policy
        # through a direct API call.
        if execution_mode in {
            MASS_EXECUTION_MODE,
            GOVERNANCE_V2_EXECUTION_MODE,
            LLM_POPULATION_EXECUTION_MODE,
            FORUM_TWIN_EXECUTION_MODE,
            FORUM_TWIN_V2_EXECUTION_MODE,
        }:
            self._assert_exact_reviewed_scenario(scenario)
            for policy in policies:
                self._assert_exact_reviewed_policy(policy)
            expected_policy_keys = MASS_RUNTIME_TEMPLATE_WHITELIST.get(
                scenario.get("template_key"),
                set(),
            )
            selected_policy_keys = {
                policy.get("template_key") for policy in policies
            }
            if (
                len(policies) != 2
                or selected_policy_keys != expected_policy_keys
            ):
                raise WorkbenchError(
                    "Population runtimes require the reviewed natural and "
                    "governance wrapper policy pair"
                )
        else:
            self._validate_reviewed_demo_selection(
                scenario,
                policies,
                require_demo_scenario=(
                    execution_mode == LIVE_LLM_ADAPTER_MODE
                ),
            )

        default_population_release_id = (
            FORUM_TWIN_V2_POPULATION_RELEASE_ID
            if execution_mode == FORUM_TWIN_V2_EXECUTION_MODE
            else (
            FORUM_TWIN_POPULATION_RELEASE_ID
            if execution_mode == FORUM_TWIN_EXECUTION_MODE
            else (
                LLM_POPULATION_RELEASE_ID
                if execution_mode == LLM_POPULATION_EXECUTION_MODE
                else (
                    GOVERNANCE_V2_POPULATION_RELEASE_ID
                    if execution_mode == GOVERNANCE_V2_EXECUTION_MODE
                    else (
                        MASS_POPULATION_RELEASE_ID
                        if execution_mode == MASS_EXECUTION_MODE
                        else POPULATION_RELEASE_ID
                    )
                )
            )
            )
        )
        population_release_id = _identifier(
            payload.get(
                "population_release_id",
                default_population_release_id,
            ),
            "population_release_id",
            SNAPSHOT_PATTERN,
        )
        if execution_mode == LIVE_LLM_ADAPTER_MODE:
            if population_release_id != POPULATION_RELEASE_ID:
                raise WorkbenchError(
                    "Live planning requires the reviewed population release"
                )
            agent_count = self._bounded_int(
                payload.get("agent_count", 48),
                "agent_count",
                16,
                48,
            )
            seeds = payload.get("seeds", list(REVIEWED_RANDOM_SEEDS))
            maximum_seed_count = 3
        elif execution_mode == MASS_EXECUTION_MODE:
            if population_release_id != MASS_POPULATION_RELEASE_ID:
                raise WorkbenchError(
                    "MassSim planning requires semantic-population-v2"
                )
            agent_count = self._bounded_int(
                payload.get("agent_count", 1000),
                "agent_count",
                1000,
                1000,
            )
            seeds = payload.get("seeds", [20260722, 20261731])
            maximum_seed_count = 2
        elif execution_mode == GOVERNANCE_V2_EXECUTION_MODE:
            if (
                population_release_id
                != GOVERNANCE_V2_POPULATION_RELEASE_ID
            ):
                raise WorkbenchError(
                    "Governance v2 planning requires semantic-population-v3"
                )
            agent_count = self._bounded_int(
                payload.get("agent_count", 1000),
                "agent_count",
                1000,
                1000,
            )
            seeds = payload.get(
                "seeds", list(GOVERNANCE_V2_DEFAULT_SEEDS)
            )
            maximum_seed_count = 8
        elif execution_mode == LLM_POPULATION_EXECUTION_MODE:
            if population_release_id != LLM_POPULATION_RELEASE_ID:
                raise WorkbenchError(
                    "YuLan-Scale planning requires the reviewed v1 LLM "
                    "agent population"
                )
            agent_count = self._bounded_int(
                payload.get("agent_count", 1000),
                "agent_count",
                1000,
                1000,
            )
            seeds = payload.get(
                "seeds", list(LLM_POPULATION_DEFAULT_SEEDS)
            )
            maximum_seed_count = 8
        elif execution_mode == FORUM_TWIN_EXECUTION_MODE:
            if population_release_id != FORUM_TWIN_POPULATION_RELEASE_ID:
                raise WorkbenchError(
                    "ForumTwin planning requires the reviewed v2 forum "
                    "agent population"
                )
            agent_count = self._bounded_int(
                payload.get("agent_count", 1000),
                "agent_count",
                1000,
                1000,
            )
            seeds = payload.get("seeds", list(FORUM_TWIN_DEFAULT_SEEDS))
            maximum_seed_count = 8
        elif execution_mode == FORUM_TWIN_V2_EXECUTION_MODE:
            if population_release_id != FORUM_TWIN_V2_POPULATION_RELEASE_ID:
                raise WorkbenchError(
                    "ForumTwin v2 planning requires the reviewed v2 forum "
                    "agent population"
                )
            agent_count = self._bounded_int(
                payload.get("agent_count", 1000),
                "agent_count",
                1,
                1000,
            )
            seeds = payload.get("seeds", [20260722])
            maximum_seed_count = 8
        else:
            agent_count = self._bounded_int(
                payload.get("agent_count", 48),
                "agent_count",
                1,
                200,
            )
            seeds = payload.get("seeds", [20260722, 20261731])
            maximum_seed_count = 5
        if (
            not isinstance(seeds, list)
            or not 1 <= len(seeds) <= maximum_seed_count
            or len(set(seeds)) != len(seeds)
            or (
                execution_mode
                in {
                    GOVERNANCE_V2_EXECUTION_MODE,
                        LLM_POPULATION_EXECUTION_MODE,
                        FORUM_TWIN_EXECUTION_MODE,
                        FORUM_TWIN_V2_EXECUTION_MODE,
                    }
                    and execution_mode != FORUM_TWIN_V2_EXECUTION_MODE
                    and len(seeds) != 8
            )
        ):
            raise WorkbenchError(
                f"seeds must contain 1 to {maximum_seed_count} "
                "unique integers"
            )
        seeds = [
            self._bounded_int(
                seed,
                "seed",
                (
                    1
                    if execution_mode
                    in {
                        LIVE_LLM_ADAPTER_MODE,
                        MASS_EXECUTION_MODE,
                        GOVERNANCE_V2_EXECUTION_MODE,
                        LLM_POPULATION_EXECUTION_MODE,
                        FORUM_TWIN_EXECUTION_MODE,
                        FORUM_TWIN_V2_EXECUTION_MODE,
                    }
                    else 0
                ),
                2_147_483_647,
            )
            for seed in seeds
        ]
        if execution_mode == LIVE_LLM_ADAPTER_MODE:
            provider_profile_id = payload.get(
                "provider_profile_id",
                REVIEWED_PROVIDER_PROFILE_ID,
            )
            if provider_profile_id != REVIEWED_PROVIDER_PROFILE_ID:
                raise WorkbenchError(
                    "Provider profile is not live-qualified"
                )
            model_name = "gpt-5.6-sol"
        elif execution_mode == MASS_EXECUTION_MODE:
            provider_profile_id = None
            model_name = "deterministic-glm-state-space-v1"
        elif execution_mode == GOVERNANCE_V2_EXECUTION_MODE:
            provider_profile_id = None
            model_name = "message-aware-particle-state-space-qre-v2"
        elif execution_mode == LLM_POPULATION_EXECUTION_MODE:
            provider_profile_id = None
            model_name = "gpt-5.6-sol"
        elif execution_mode == FORUM_TWIN_EXECUTION_MODE:
            provider_profile_id = None
            model_name = "gpt-5.4-mini"
        elif execution_mode == FORUM_TWIN_V2_EXECUTION_MODE:
            provider_profile_id = None
            model_name = FORUM_TWIN_V2_MODEL_NAME
        else:
            provider_profile_id = None
            model_name = _identifier(
                payload.get("model_name", "gpt-5.6-sol"),
                "model_name",
                MODEL_PATTERN,
            )
        max_workers = self._bounded_int(
            payload.get("max_workers", 32),
            "max_workers",
            1,
            48,
        )
        default_token_budget = (
            1_000
            if execution_mode == FORUM_TWIN_V2_EXECUTION_MODE
            else (
            80_000_000
            if execution_mode == FORUM_TWIN_EXECUTION_MODE
            else (
                40_000_000
                if execution_mode == LLM_POPULATION_EXECUTION_MODE
                else (
                    1_000
                    if execution_mode
                    in {MASS_EXECUTION_MODE, GOVERNANCE_V2_EXECUTION_MODE}
                    else (
                        REVIEWED_LECTURE_TOKEN_LIMIT
                        if (
                            execution_mode == LIVE_LLM_ADAPTER_MODE
                            and scenario.get("template_key")
                            == "lecture_external_incident_v2"
                        )
                        else REVIEWED_TONGZHOU_TOKEN_LIMIT
                    )
                )
            )
            )
        )
        token_budget = self._bounded_int(
            payload.get("token_budget", default_token_budget),
            "token_budget",
            1_000,
            (
                80_000_000
                if execution_mode
                in {FORUM_TWIN_EXECUTION_MODE, FORUM_TWIN_V2_EXECUTION_MODE}
                else (
                    40_000_000
                    if execution_mode == LLM_POPULATION_EXECUTION_MODE
                    else 5_000_000
                )
            ),
        )
        if max_workers > agent_count:
            raise WorkbenchError("max_workers cannot exceed agent_count")

        common_sensing_keys = (
            "snapshot_id",
            "release_key",
            "schema_version",
            "manifest_sha256",
            "artifact_sha256",
            "source_snapshot_id",
            "source_manifest_sha256",
            "taxonomy_release_sha256",
            "evidence_stage",
            "estimation_mode",
            "causal_status",
            "forecast_status",
            "risk_validity",
            "unit_of_analysis",
            "stance_target_status",
            "sample_size",
            "source_lineage",
            "scope",
            "review_summary",
            "privacy",
            "claim_boundary",
            "limitation_codes",
        )
        frozen_sensing = {
            key: sensing[key] for key in common_sensing_keys
        }
        if (
            sensing["schema_version"]
            == WINDOWED_SENSING_MANIFEST_SCHEMA_VERSION
        ):
            frozen_sensing.update(
                {
                    "window_count": sensing["window_count"],
                    "composition_status": sensing["composition_status"],
                    "distributions": sensing["distributions"],
                    "windows": sensing["windows"],
                }
            )
        else:
            frozen_sensing.update(
                {
                    key: sensing[key]
                    for key in (
                        "persona_evidence_sha256",
                        "persona_release_sha256",
                    )
                }
            )

        input_schema_version = "campus-pulse-run-plan-v3"
        execution_snapshot: dict[str, Any] = {
            "execution_mode": execution_mode,
            "population_release_id": population_release_id,
            "agent_count": agent_count,
            "seeds": seeds,
            "model_name": model_name,
            "max_workers": max_workers,
            "token_budget": token_budget,
        }
        live_runtime_snapshot: dict[str, Any] | None = None
        live_plan_extension: dict[str, Any] | None = None
        mass_plan_snapshot: dict[str, Any] | None = None
        governance_v2_snapshot: dict[str, Any] | None = None
        governance_v2_manifest: dict[str, Any] | None = None
        llm_population_snapshot: dict[str, Any] | None = None
        llm_population_manifest: dict[str, Any] | None = None
        forum_twin_snapshot: dict[str, Any] | None = None
        forum_twin_manifest: dict[str, Any] | None = None
        forum_twin_v2_snapshot: dict[str, Any] | None = None
        forum_visibility_snapshot: dict[str, Any] | None = None
        reviewed_bundle_manifest: dict[str, Any] | None = None
        reviewed_mapping: dict[str, Any] | None = None
        reviewed_provider_profile: dict[str, Any] | None = None
        provider_call_limit: int | None = None

        if execution_mode == LIVE_LLM_ADAPTER_MODE:
            capability = self.live_runtime_capability()
            if not capability["planning_ready"]:
                raise WorkbenchConflictError(
                    "Reviewed live runtime inputs are not installed"
                )
            bundle_row = self.store.get_live_asset_bundle(
                REVIEWED_LIVE_ASSET_BUNDLE_ID
            )
            provider_row = self.store.get_live_provider_profile(
                REVIEWED_PROVIDER_PROFILE_ID
            )
            if (
                bundle_row is None
                or provider_row is None
                or bundle_row.get("bundle_fingerprint")
                != REVIEWED_LIVE_ASSET_BUNDLE_FINGERPRINT
                or provider_row.get("provider_profile_fingerprint")
                != REVIEWED_PROVIDER_PROFILE_FINGERPRINT
            ):
                raise WorkbenchConflictError(
                    "Reviewed live runtime catalog identity drifted"
                )
            reviewed_bundle_manifest = bundle_row["manifest"]
            reviewed_provider_profile = get_reviewed_provider_profile(
                internal=True
            )
            try:
                reviewed_mapping = resolve_reviewed_template_mapping(
                    scenario["template_key"],
                    [policy["template_key"] for policy in policies],
                )
                cold_turns = expected_live_turns(
                    agent_count=agent_count,
                    seed_count=len(seeds),
                    policy_count=len(policies),
                )
                provider_call_limit = self._bounded_int(
                    payload.get("provider_call_limit", cold_turns * 2),
                    "provider_call_limit",
                    cold_turns,
                    cold_turns * 4,
                )
                cost_contract = build_live_cost_contract(
                    agent_count=agent_count,
                    random_seeds=seeds,
                    policy_count=len(policies),
                    provider_call_limit=provider_call_limit,
                    token_limit=token_budget,
                    max_workers=max_workers,
                    server_concurrency_cap=(
                        self.runtime_concurrency_cap
                    ),
                )
            except LiveRuntimeContractError as exc:
                raise WorkbenchError(str(exc)) from exc

            input_schema_version = LIVE_PLAN_SCHEMA_VERSION
            execution_snapshot = {
                "execution_mode": LIVE_LLM_ADAPTER_MODE,
                "population_release_id": population_release_id,
                "agent_count": agent_count,
                "seeds": seeds,
                "provider_profile_id": provider_profile_id,
                "model_name": model_name,
                "max_workers": max_workers,
                "provider_call_limit": provider_call_limit,
                "token_budget": token_budget,
            }
            live_runtime_snapshot = {
                "asset_bundle": reviewed_bundle_manifest,
                "population": {
                    "population_release_id": population_release_id,
                    "population_release_fingerprint": (
                        reviewed_bundle_manifest["population"][
                            "population_release_fingerprint"
                        ]
                    ),
                    "population_evidence_fingerprint": (
                        reviewed_bundle_manifest["population"][
                            "population_evidence_fingerprint"
                        ]
                    ),
                    "allocation_algorithm_version": (
                        LIVE_POPULATION_ALLOCATION_ALGORITHM_VERSION
                    ),
                    "synthetic_population_status": (
                        "reviewed_behavioral_archetypes_not_"
                        "population_estimate"
                    ),
                },
                "template_mapping": reviewed_mapping,
                "provider_profile": reviewed_provider_profile,
                "algorithms": {
                    "runtime_adapter_version": (
                        LIVE_RUNTIME_ADAPTER_VERSION
                    ),
                    "prompt_contract_version": (
                        reviewed_provider_profile[
                            "prompt_contract_version"
                        ]
                    ),
                    "decision_validator_version": (
                        reviewed_provider_profile[
                            "decision_validator_version"
                        ]
                    ),
                    "checkpoint_algorithm_version": (
                        LIVE_CHECKPOINT_ALGORITHM_VERSION
                    ),
                    "cache_algorithm_version": (
                        LIVE_CACHE_ALGORITHM_VERSION
                    ),
                    "algorithm_fingerprint": (
                        LIVE_ALGORITHM_FINGERPRINT
                    ),
                },
                "cost_contract": cost_contract,
                "claim_boundary": get_live_result_claim_boundary(),
                "claim_boundary_fingerprint": (
                    LIVE_CLAIM_BOUNDARY_FINGERPRINT
                ),
                "limitation_codes": list(
                    LIVE_RESULT_LIMITATION_CODES
                ),
            }
        elif execution_mode == MASS_EXECUTION_MODE:
            population, graph, writeback = _load_mass_sim_release()
            scenario_key = scenario.get("template_key")
            mass_scenario_id = MASS_SCENARIO_BY_TEMPLATE.get(scenario_key)
            if mass_scenario_id is None:
                raise WorkbenchConflictError(
                    "Reviewed scenario has no MassSim shock mapping"
                )
            mass_plan_snapshot = build_run_plan_v5(
                population=population,
                graph=graph,
                scenario_ids=(mass_scenario_id,),
                seeds=seeds,
                writeback_mode=writeback["formal_mode"],
            )
            input_schema_version = MASS_RUN_PLAN_SCHEMA_VERSION
            execution_snapshot = {
                "execution_mode": MASS_EXECUTION_MODE,
                "population_release_id": population_release_id,
                "agent_count": agent_count,
                "seeds": seeds,
                "model_name": model_name,
                "max_workers": max_workers,
                "token_budget": token_budget,
            }
        elif execution_mode == GOVERNANCE_V2_EXECUTION_MODE:
            governance_v2_manifest = (
                _load_governance_v2_release_manifest()
            )
            governance_v2_snapshot = build_governance_v2_plan(
                release_manifest=governance_v2_manifest,
                seeds=seeds,
                formal_writeback_mode="observation_only",
            )
            input_schema_version = (
                GOVERNANCE_V2_RUN_PLAN_SCHEMA_VERSION
            )
            execution_snapshot = {
                "execution_mode": GOVERNANCE_V2_EXECUTION_MODE,
                "population_release_id": population_release_id,
                "agent_count": agent_count,
                "seeds": seeds,
                "model_name": model_name,
                "max_workers": max_workers,
                "token_budget": token_budget,
            }
        elif execution_mode == LLM_POPULATION_EXECUTION_MODE:
            execution_provenance = payload.get(
                "execution_provenance",
                "emulator_only_development",
            )
            if execution_provenance not in {
                "emulator_only_development",
                "reviewed_trace_replay",
            }:
                raise WorkbenchError(
                    "Authorized live YuLan-Scale requires a separate "
                    "single-use Provider preflight and authorization"
                )
            if execution_provenance == "reviewed_trace_replay":
                _require_yulan_scale_reviewed_trace_release()
            llm_population_manifest = (
                _load_yulan_scale_release_manifest()
            )
            llm_population_snapshot = build_llm_population_plan(
                release_manifest=llm_population_manifest,
                execution_provenance=execution_provenance,
                provider_profile=_yulan_scale_provider_profile(),
                seeds=seeds,
                formal_writeback_mode="observation_only",
            )
            input_schema_version = LLM_POPULATION_RUN_PLAN_SCHEMA_VERSION
            execution_snapshot = {
                "execution_mode": LLM_POPULATION_EXECUTION_MODE,
                "execution_provenance": execution_provenance,
                "population_release_id": population_release_id,
                "agent_count": agent_count,
                "seeds": seeds,
                "model_name": model_name,
                "max_workers": max_workers,
                "token_budget": token_budget,
            }
        elif execution_mode == FORUM_TWIN_EXECUTION_MODE:
            forum_trace_release = None
            execution_provenance = payload.get(
                "execution_provenance",
                "emulator_only_development",
            )
            if execution_provenance not in {
                "emulator_only_development",
                "reviewed_trace_replay",
            }:
                raise WorkbenchError(
                    "Authorized live ForumTwin requires a separate "
                    "single-use Provider preflight and authorization"
            )
            if execution_provenance == "reviewed_trace_replay":
                forum_trace_release = _require_forum_twin_reviewed_trace_release()
            forum_twin_manifest = _load_forum_twin_release_manifest()
            provider_profile = _forum_twin_provider_profile()
            forum_twin_snapshot = build_forum_twin_plan(
                release_manifest=forum_twin_manifest,
                execution_provenance=execution_provenance,
                provider_profile=provider_profile,
                seeds=seeds,
                formal_writeback_mode="observation_only",
            )
            if forum_trace_release is not None and (
                forum_trace_release.get("model") != provider_profile["model_name"]
                or forum_trace_release.get("endpoint_fingerprint")
                != provider_profile["endpoint_fingerprint"]
                or forum_trace_release.get("run_plan_sha256")
                != forum_twin_snapshot["plan_sha256"]
                or forum_trace_release.get("renderer_sha256")
                != forum_twin_snapshot["model_release"]["components"]["renderer"]["sha256"]
            ):
                raise WorkbenchConflictError(
                    "Reviewed ForumTwin trace does not bind the frozen v8 plan"
                )
            input_schema_version = FORUM_TWIN_RUN_PLAN_SCHEMA_VERSION
            execution_snapshot = {
                "execution_mode": FORUM_TWIN_EXECUTION_MODE,
                "execution_provenance": execution_provenance,
                "population_release_id": population_release_id,
                "agent_count": agent_count,
                "seeds": seeds,
                "model_name": model_name,
                "max_workers": max_workers,
                "token_budget": token_budget,
            }
        elif execution_mode == FORUM_TWIN_V2_EXECUTION_MODE:
            if agent_count != 1_000:
                raise WorkbenchConflictError(
                    "ForumTwin v2 runs use the fixed reviewed 1,000-Agent world; "
                    "choose an activation mode instead of changing population size"
                )
            execution_provenance = payload.get(
                "execution_provenance",
                "emulator_only_development",
            )
            if execution_provenance != "emulator_only_development":
                raise WorkbenchError(
                    "ForumTwin v2 live/replay runs require the separate "
                    "hash-bound census or population preflight"
                )
            activation_mode = payload.get(
                "forum_activation_mode",
                "budgeted_pps",
            )
            keyframe_ticks = payload.get("forum_keyframe_ticks", [])
            scenario_code = FORUM_TWIN_V2_SCENARIO_BY_TEMPLATE.get(
                scenario.get("template_key")
            )
            if scenario_code is None:
                raise WorkbenchConflictError(
                    "Reviewed scenario has no ForumTwin v2 mapping"
                )
            population_v2, stakes_v2, relationships_v2, component_hashes = (
                _load_forum_twin_v2_release_inputs()
            )
            self.store.register_forum_twin_v2_releases(
                self.tenant_id,
                project_id,
                stake_release=stakes_v2,
                relationship_release=relationships_v2,
            )
            profile_override_release = (
                self.store.get_forum_project_profile_override_release(
                    self.tenant_id,
                    project_id,
                )
            )
            if profile_override_release is None:
                first_display_id = str(
                    population_v2["agents"][0]["synthetic_display_id"]
                )
                profile_override_release = (
                    self.store.append_forum_project_profile_override_release(
                        self.tenant_id,
                        project_id,
                        population_sha256=population_v2["population_sha256"],
                        display_id=first_display_id,
                        override=None,
                    )
                )
            forum_twin_v2_snapshot = build_forum_twin_run_plan_v9(
                population_sha256=population_v2["population_sha256"],
                profile_release_sha256=population_v2[
                    "profile_release_sha256"
                ],
                relationship_release_sha256=relationships_v2[
                    "relationship_release_sha256"
                ],
                stake_release_sha256=stakes_v2["stake_release_sha256"],
                model_component_hashes=component_hashes,
                scenario_ids=[scenario_code],
                branches=["natural", "D"],
                seeds=seeds,
                activation_mode=activation_mode,
                keyframe_ticks=keyframe_ticks,
                execution_provenance=execution_provenance,
                provider_slot_limit=0,
                provider_request_limit=0,
                provider_token_limit=0,
            )
            def visibility_flag(name: str) -> bool:
                value = payload.get(name, True)
                if not isinstance(value, bool):
                    raise WorkbenchError(f"{name} must be boolean")
                return value

            channel_configuration = {
                "public_forum": True,
                "friend_chat": visibility_flag("forum_friend_chat_enabled"),
                "dynamic_group": visibility_flag(
                    "forum_dynamic_group_enabled"
                ),
                "service_desk": visibility_flag("forum_service_desk_enabled"),
            }
            if (
                channel_configuration["dynamic_group"]
                and not channel_configuration["friend_chat"]
            ):
                raise WorkbenchError(
                    "Dynamic groups require the friend-chat channel"
                )
            forum_visibility_snapshot = build_forum_twin_run_plan_v10(
                parent_plan_v9=forum_twin_v2_snapshot,
                profile_override_release_sha256=profile_override_release[
                    "release_sha256"
                ],
                channel_configuration=channel_configuration,
                public_anonymity_enabled=visibility_flag(
                    "forum_public_anonymity_enabled"
                ),
                relationship_attention_enabled=visibility_flag(
                    "forum_relationship_attention_enabled"
                ),
            )
            input_schema_version = FORUM_TWIN_V2_RUN_PLAN_SCHEMA_VERSION
            execution_snapshot = {
                "execution_mode": FORUM_TWIN_V2_EXECUTION_MODE,
                "execution_provenance": execution_provenance,
                "activation_mode": activation_mode,
                "population_release_id": population_release_id,
                "agent_count": agent_count,
                "seeds": seeds,
                "model_name": model_name,
                "max_workers": max_workers,
                "token_budget": token_budget,
            }

        input_snapshot = {
            "schema_version": input_schema_version,
            "project": {
                key: project[key]
                for key in (
                    "project_id",
                    "governance_domain",
                    "evaluation_mode",
                )
            },
            "scenario": {
                key: scenario[key]
                for key in (
                    "scenario_id",
                    "name",
                    "shock_origin",
                    "phases",
                    "evidence_snapshot_id",
                    "claim_boundary",
                )
            },
            "evidence": {
                key: evidence[key]
                for key in (
                    "snapshot_id",
                    "analysis_id",
                    "manifest_sha256",
                    "privacy_mode",
                    "parser_contract_version",
                    "artifact_kind",
                    "artifact_sha256",
                    "evidence_stage",
                    "quality_grade",
                    "causal_status",
                )
            },
            "sensing": frozen_sensing,
            "policies": [
                {
                    key: policy[key]
                    for key in (
                        "policy_id",
                        "name",
                        "is_baseline",
                        "actions",
                        "evaluation_level",
                    )
                }
                for policy in policies
            ],
            "execution": execution_snapshot,
        }
        if live_runtime_snapshot is not None:
            input_snapshot["live_runtime"] = live_runtime_snapshot
        if mass_plan_snapshot is not None:
            input_snapshot["mass_sim"] = mass_plan_snapshot
        if governance_v2_snapshot is not None:
            input_snapshot["governance_arena_v2"] = (
                governance_v2_snapshot
            )
        if llm_population_snapshot is not None:
            input_snapshot["llm_agent_population"] = (
                llm_population_snapshot
            )
        if forum_twin_snapshot is not None:
            input_snapshot["forum_twin"] = forum_twin_snapshot
        if forum_twin_v2_snapshot is not None:
            input_snapshot["forum_twin_v2"] = forum_twin_v2_snapshot
        if forum_visibility_snapshot is not None:
            input_snapshot["forum_visibility"] = forum_visibility_snapshot
        now = _now()
        run = {
            "run_id": _new_id("run"),
            "project_id": project_id,
            "scenario_id": scenario_id,
            "idempotency_key": idempotency_key,
            "execution_mode": execution_mode,
            "population_release_id": population_release_id,
            "policy_ids": policy_ids,
            "agent_count": agent_count,
            "seeds": seeds,
            "model_name": model_name,
            "max_workers": max_workers,
            "token_budget": token_budget,
            "evaluation_mode": project["evaluation_mode"],
            "input_snapshot": input_snapshot,
            "input_fingerprint": _canonical_hash(input_snapshot),
            "status": "draft",
            "created_at": now,
            "updated_at": now,
        }
        if execution_mode == LIVE_LLM_ADAPTER_MODE:
            assert (
                reviewed_bundle_manifest is not None
                and reviewed_mapping is not None
                and reviewed_provider_profile is not None
                and provider_call_limit is not None
            )
            try:
                live_plan_extension = build_reviewed_live_plan_extension(
                    input_fingerprint=run["input_fingerprint"],
                    asset_bundle=reviewed_bundle_manifest,
                    template_mapping=reviewed_mapping,
                    provider_profile=reviewed_provider_profile,
                    agent_count=agent_count,
                    random_seeds=seeds,
                    provider_call_limit=provider_call_limit,
                    token_limit=token_budget,
                    max_workers=max_workers,
                    server_concurrency_cap=(
                        self.runtime_concurrency_cap
                    ),
                )
            except LiveRuntimeContractError as exc:
                raise WorkbenchConflictError(str(exc)) from exc
        if idempotency_key is not None:
            existing = self.store.get_run_by_idempotency_key(
                project_id,
                idempotency_key,
            )
            if existing is not None:
                return self._with_run_runtime_summary(
                    self._resolve_idempotent_run(existing, run)
                )
        try:
            created = self.store.create_run(
                run,
                {
                    "tenant_id": self.tenant_id,
                    "snapshot_id": evidence["snapshot_id"],
                    "analysis_id": evidence["analysis_id"],
                    "manifest_sha256": evidence["manifest_sha256"],
                },
                self._audit(
                    project_id,
                    "run",
                    run["run_id"],
                    "planned",
                    {
                        "execution_mode": execution_mode,
                        "input_fingerprint": run["input_fingerprint"],
                    },
                    now,
                ),
                sensing_binding={
                    "tenant_id": self.tenant_id,
                    "snapshot_id": sensing["snapshot_id"],
                    "manifest_sha256": sensing["manifest_sha256"],
                },
                live_plan_extension=live_plan_extension,
            )
            if (
                execution_mode == MASS_EXECUTION_MODE
                and mass_plan_snapshot is not None
            ):
                self.store.bind_mass_sim_run(
                    self.tenant_id,
                    project_id,
                    run["run_id"],
                    population=population,
                    graph=graph,
                    plan=mass_plan_snapshot,
                    created_at=now,
                )
            if (
                execution_mode == GOVERNANCE_V2_EXECUTION_MODE
                and governance_v2_snapshot is not None
            ):
                self.store.bind_governance_v2_run(
                    self.tenant_id,
                    project_id,
                    run["run_id"],
                    plan=governance_v2_snapshot,
                    created_at=now,
                )
            return self._with_run_runtime_summary(created)
        except StoreConflictError as exc:
            if idempotency_key is not None:
                existing = self.store.get_run_by_idempotency_key(
                    project_id,
                    idempotency_key,
                )
                if existing is not None:
                    return self._with_run_runtime_summary(
                        self._resolve_idempotent_run(existing, run)
                    )
            raise WorkbenchConflictError(str(exc)) from exc

    def list_runs(self, project_id: str) -> list[dict[str, Any]]:
        self.get_project(project_id)
        return [
            self._with_run_runtime_summary(run)
            for run in self.store.list_runs(project_id)
        ]

    def get_run(self, run_id: str) -> dict[str, Any]:
        run_id = _identifier(run_id, "run_id", ID_PATTERN)
        run = self.store.get_run(run_id)
        if run is None:
            raise WorkbenchNotFoundError("Run not found")
        project = self.store.get_project(self.tenant_id, run["project_id"])
        if project is None:
            raise WorkbenchNotFoundError("Run not found")
        return self._with_run_runtime_summary(run)

    def enqueue_run_runtime(
        self,
        run_id: str,
        *,
        idempotency_key: str | None,
        expected_state_version: int,
        turn_budget: int,
        max_attempts: int,
    ) -> dict[str, Any]:
        """Persist a launch contract without executing work inline."""

        run = self.get_run(run_id)
        if run["template_identity"]["status"] != "recognized":
            raise WorkbenchConflictError(
                "Run is outside the reviewed demo runtime whitelist"
            )
        if run["execution_mode"] == LIVE_LLM_ADAPTER_MODE:
            raise WorkbenchConflictError(
                "The live LLM executor is not installed in P3.2a"
            )
        if self.runtime_adapter_mode == "disabled":
            raise WorkbenchConflictError(
                "The asynchronous runtime adapter is disabled by the server"
            )
        if idempotency_key is None:
            raise WorkbenchError("Idempotency-Key is required")
        idempotency_key = _bounded_text(
            idempotency_key,
            "idempotency_key",
            minimum=8,
            maximum=128,
        )
        if (
            isinstance(expected_state_version, bool)
            or not isinstance(expected_state_version, int)
            or expected_state_version != 0
        ):
            raise WorkbenchError(
                "A first enqueue requires expected_state_version 0"
            )
        if (
            isinstance(turn_budget, bool)
            or not isinstance(turn_budget, int)
            or turn_budget < 1
        ):
            raise WorkbenchError("turn_budget must be a positive integer")
        if (
            isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or not 1 <= max_attempts <= 5
        ):
            raise WorkbenchError("max_attempts must be between 1 and 5")
        try:
            plan = validate_frozen_run_plan(run)
            required_turns = required_turn_units(plan)
            if turn_budget != required_turns:
                raise WorkbenchError(
                    "turn_budget must equal the frozen run's required "
                    f"turn count ({required_turns})"
                )
            launch = build_launch_contract(
                plan,
                turn_limit=turn_budget,
                max_attempts=max_attempts,
                server_concurrency_cap=self.runtime_concurrency_cap,
                adapter_mode=self.runtime_adapter_mode,
            )
        except RuntimeContractError as exc:
            raise WorkbenchConflictError(str(exc)) from exc
        try:
            stored = self.store.enqueue_run_runtime(
                self.tenant_id,
                run["project_id"],
                run["run_id"],
                idempotency_key,
                launch,
                expected_state_version,
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc
        return self._public_runtime(stored["runtime"])

    def get_run_runtime(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        runtime = self.store.get_run_runtime(
            self.tenant_id,
            run["project_id"],
            run["run_id"],
        )
        if runtime is None:
            raise WorkbenchNotFoundError("Run has not been enqueued")
        return self._public_runtime(runtime)

    def list_run_runtime_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        run = self.get_run(run_id)
        runtime = self.store.get_run_runtime(
            self.tenant_id,
            run["project_id"],
            run["run_id"],
        )
        if runtime is None:
            raise WorkbenchNotFoundError("Run has not been enqueued")
        if (
            isinstance(after_sequence, bool)
            or not isinstance(after_sequence, int)
            or after_sequence < 0
        ):
            raise WorkbenchError(
                "after_sequence must be a non-negative integer"
            )
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 200
        ):
            raise WorkbenchError("limit must be between 1 and 200")
        try:
            page = self.store.list_run_runtime_events(
                self.tenant_id,
                run["project_id"],
                run["run_id"],
                after_sequence=after_sequence,
                limit=limit + 1,
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc
        has_more = len(page) > limit
        items = [
            self._public_runtime_event(event) for event in page[:limit]
        ]
        return {
            "items": items,
            "after_sequence": after_sequence,
            "next_after_sequence": (
                items[-1]["sequence"] if items else after_sequence
            ),
            "has_more": has_more,
        }

    def get_run_runtime_result(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["execution_mode"] == LIVE_LLM_ADAPTER_MODE:
            try:
                material = (
                    self.store.get_live_aggregate_v2_report_material(
                        self.tenant_id,
                        run["project_id"],
                        run["run_id"],
                    )
                )
            except StoreConflictError as exc:
                raise WorkbenchConflictError(str(exc)) from exc
            if material is None:
                raise WorkbenchNotFoundError("Run not found")
            stored = material["result_record"]
            return {
                "run_id": run["run_id"],
                "result_schema_version": stored[
                    "result_schema_version"
                ],
                "launch_fingerprint": stored[
                    "launch_fingerprint"
                ],
                "result_sha256": stored["result_sha256"],
                "completeness_ppm": stored["completeness_ppm"],
                "created_at": stored["created_at"],
                "result": stored["result"],
            }
        runtime = self.store.get_run_runtime(
            self.tenant_id,
            run["project_id"],
            run["run_id"],
        )
        if runtime is None:
            raise WorkbenchNotFoundError("Run has not been enqueued")
        stored = self.store.get_run_aggregate_result(
            self.tenant_id,
            run["project_id"],
            run["run_id"],
        )
        if stored is None:
            raise WorkbenchConflictError(
                "A complete aggregate result is not available"
            )
        try:
            plan = validate_frozen_run_plan(run)
            validated = validate_aggregate_result(
                stored["result"],
                run_plan=plan,
                launch_contract=runtime["launch_contract"],
            )
        except RuntimeContractError as exc:
            raise WorkbenchConflictError(
                "Persisted aggregate result failed its release contract"
            ) from exc
        if (
            runtime["status"] != "succeeded"
            or stored["launch_fingerprint"]
            != runtime["launch_fingerprint"]
            or stored["result_sha256"] != validated["result_sha256"]
            or stored["completeness_ppm"] != 1_000_000
        ):
            raise WorkbenchConflictError(
                "Aggregate result is not bound to a complete successful run"
            )
        return {
            "run_id": run["run_id"],
            "result_schema_version": stored["result_schema_version"],
            "launch_fingerprint": stored["launch_fingerprint"],
            "result_sha256": stored["result_sha256"],
            "completeness_ppm": stored["completeness_ppm"],
            "created_at": stored["created_at"],
            "result": validated,
        }

    def get_governance_v2_visualization(
        self,
        run_id: str,
    ) -> dict[str, Any]:
        """Read the hash-bound public visualization for a succeeded v2 run."""

        run = self.get_run(run_id)
        if run["execution_mode"] != GOVERNANCE_V2_EXECUTION_MODE:
            raise WorkbenchConflictError(
                "Run has no Governance Arena v2 visualization"
            )
        try:
            asset = self.store.get_governance_v2_visualization(
                self.tenant_id,
                run["project_id"],
                run["run_id"],
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc
        if asset is None:
            raise WorkbenchConflictError(
                "A complete successful visualization is not available"
            )
        return asset

    def get_run_visualization(
        self,
        run_id: str,
    ) -> dict[str, Any]:
        """Dispatch hash-bound visualization assets by frozen run contract."""

        run = self.get_run(run_id)
        if run["execution_mode"] == GOVERNANCE_V2_EXECUTION_MODE:
            return self.get_governance_v2_visualization(run_id)
        if run["execution_mode"] == LLM_POPULATION_EXECUTION_MODE:
            try:
                asset = self.store.get_llm_population_visualization(
                    self.tenant_id,
                    run["project_id"],
                    run["run_id"],
                )
            except StoreConflictError as exc:
                raise WorkbenchConflictError(str(exc)) from exc
            if asset is None:
                raise WorkbenchConflictError(
                    "A complete successful YuLan-Scale visualization is "
                    "not available"
                )
            return asset
        raise WorkbenchConflictError(
            "Run has no public particle visualization"
        )

    def get_forum_twin_manifest(self, run_id: str) -> dict[str, Any]:
        """Read the formal, hash-verified ForumTwin v4 manifest only."""

        run = self.get_run(run_id)
        if run["execution_mode"] != FORUM_TWIN_EXECUTION_MODE:
            raise WorkbenchConflictError("Run has no ForumTwin manifest")
        try:
            asset = self.store.get_forum_twin_public_manifest(
                self.tenant_id,
                run["project_id"],
                run["run_id"],
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc
        if asset is None:
            raise WorkbenchConflictError(
                "A formal successful ForumTwin manifest is not available"
            )
        return asset

    @staticmethod
    def _forum_public_code(value: Any, field: str) -> str:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value)
            is None
        ):
            raise WorkbenchError(f"{field} is not a safe public identifier")
        return value

    def _get_formal_forum_twin_domain(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["execution_mode"] != FORUM_TWIN_EXECUTION_MODE:
            raise WorkbenchConflictError("Run has no public ForumTwin result")
        record = self.get_run_runtime_result(run_id)
        result = record["result"]
        if (
            result.get("publication_eligible") is not True
            or result.get("execution_provenance")
            not in {"reviewed_trace_replay", "authorized_live_llm"}
            or result.get("completeness", {}).get("completed_primary_slots")
            != 16_544
            or result.get("privacy", {}).get("scan_passed") is not True
        ):
            raise WorkbenchConflictError(
                "Development, incomplete or unreviewed ForumTwin data is not public"
            )
        domain = result.get("domain_result")
        if not isinstance(domain, dict):
            raise WorkbenchConflictError("ForumTwin domain result is invalid")
        return domain

    def list_forum_twin_threads(
        self,
        run_id: str,
        *,
        offset: int = 0,
        limit: int = 50,
        scenario_id: str | None = None,
        branch: str | None = None,
    ) -> dict[str, Any]:
        if (
            isinstance(offset, bool)
            or not isinstance(offset, int)
            or offset < 0
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise WorkbenchError("ForumTwin pagination is invalid")
        if scenario_id is not None:
            scenario_id = self._forum_public_code(scenario_id, "scenario_id")
        if branch is not None and branch not in {"natural", "D", "A", "B", "C"}:
            raise WorkbenchError("ForumTwin branch is invalid")
        domain = self._get_formal_forum_twin_domain(run_id)
        threads = domain.get("public_threads")
        if not isinstance(threads, list) or any(not isinstance(item, dict) for item in threads):
            raise WorkbenchConflictError("ForumTwin public thread index is invalid")
        filtered = [
            item
            for item in threads
            if (scenario_id is None or item.get("scenario_id") == scenario_id)
            and (branch is None or item.get("branch_id", item.get("branch")) == branch)
        ]
        filtered.sort(
            key=lambda item: (
                int(item.get("created_tick", 0)),
                str(item.get("thread_id", "")),
            )
        )
        items = deepcopy(filtered[offset : offset + limit])
        return {
            "items": items,
            "offset": offset,
            "limit": limit,
            "total": len(filtered),
            "has_more": offset + len(items) < len(filtered),
        }

    def get_forum_twin_thread(self, run_id: str, thread_id: str) -> dict[str, Any]:
        thread_id = self._forum_public_code(thread_id, "thread_id")
        domain = self._get_formal_forum_twin_domain(run_id)
        threads = domain.get("public_threads", [])
        messages = domain.get("public_messages", [])
        claims = domain.get("claims", [])
        thread = next(
            (item for item in threads if isinstance(item, dict) and item.get("thread_id") == thread_id),
            None,
        )
        if thread is None:
            raise WorkbenchNotFoundError("ForumTwin thread not found")
        thread_messages = [
            item for item in messages
            if isinstance(item, dict) and item.get("thread_id") == thread_id
        ]
        claim_ids = set(thread.get("claim_ids", []))
        thread_claims = [
            item for item in claims
            if isinstance(item, dict) and item.get("claim_id") in claim_ids
        ]
        return deepcopy(
            {"thread": thread, "messages": thread_messages, "claims": thread_claims}
        )

    def get_forum_twin_claim(self, run_id: str, claim_id: str) -> dict[str, Any]:
        claim_id = self._forum_public_code(claim_id, "claim_id")
        domain = self._get_formal_forum_twin_domain(run_id)
        claims = domain.get("claims", [])
        messages = domain.get("public_messages", [])
        claim_by_id = {
            item.get("claim_id"): item for item in claims if isinstance(item, dict)
        }
        claim = claim_by_id.get(claim_id)
        if claim is None:
            raise WorkbenchNotFoundError("ForumTwin claim not found")
        descendants = []
        frontier = [claim_id]
        seen = {claim_id}
        while frontier:
            parent = frontier.pop(0)
            for candidate_id, candidate in claim_by_id.items():
                if candidate_id in seen or candidate.get("parent_claim_id") != parent:
                    continue
                seen.add(candidate_id)
                frontier.append(candidate_id)
                descendants.append(candidate)
        message_ids = set(claim.get("supporting_message_ids", [])) | set(
            claim.get("challenging_message_ids", [])
        )
        linked_messages = [
            item for item in messages
            if isinstance(item, dict) and item.get("message_id") in message_ids
        ]
        return deepcopy(
            {"claim": claim, "descendants": descendants, "messages": linked_messages}
        )

    def get_forum_twin_display_profile(
        self, run_id: str, display_id: str
    ) -> dict[str, Any]:
        display_id = self._forum_public_code(display_id, "display_id")
        domain = self._get_formal_forum_twin_domain(run_id)
        profiles = domain.get("audit", {}).get("public_display_profiles", [])
        profile = next(
            (item for item in profiles if isinstance(item, dict) and item.get("display_id") == display_id),
            None,
        )
        if profile is None:
            raise WorkbenchNotFoundError("ForumTwin display profile not found")
        return deepcopy(profile)

    def get_forum_twin_tick_asset(
        self,
        run_id: str,
        *,
        asset_kind: str,
        scenario_id: str,
        branch: str,
        tick: int,
    ) -> dict[str, Any]:
        run = self.get_run(run_id)
        if run["execution_mode"] != FORUM_TWIN_EXECUTION_MODE:
            raise WorkbenchConflictError("Run has no ForumTwin assets")
        scenario_id = self._forum_public_code(scenario_id, "scenario_id")
        if branch not in {"natural", "D", "A", "B", "C"}:
            raise WorkbenchError("ForumTwin branch is invalid")
        if isinstance(tick, bool) or not isinstance(tick, int) or not 0 <= tick <= 23:
            raise WorkbenchError("ForumTwin tick is invalid")
        if asset_kind not in {"particle_frame", "propagation_matrix"}:
            raise WorkbenchError("ForumTwin asset kind is invalid")
        role = f"{scenario_id}:{branch}:T{tick}"
        try:
            asset = self.store.get_forum_twin_public_asset(
                self.tenant_id,
                run["project_id"],
                run["run_id"],
                asset_kind=asset_kind,
                asset_role=role,
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc
        if asset is None:
            raise WorkbenchNotFoundError("ForumTwin tick asset not found")
        return asset

    def list_run_ticks(self, run_id: str) -> dict[str, Any]:
        """Project the committed public ForumTwin timeline without private rows."""

        domain = self._get_formal_forum_twin_domain(run_id)
        messages = [
            item for item in domain.get("public_messages", [])
            if isinstance(item, dict)
        ]
        governance = [
            item for item in domain.get("governance_rounds", [])
            if isinstance(item, dict)
        ]
        items = []
        for tick in range(24):
            tick_messages = [item for item in messages if item.get("created_tick") == tick]
            items.append({
                "tick": tick,
                "committed": True,
                "public_message_count": len(tick_messages),
                "active_agent_count": len({
                    item.get("agent_display_id", item.get("source_agent_display_id"))
                    for item in tick_messages
                }),
                "governance_action_count": len([
                    item for item in governance
                    if item.get("tick", item.get("created_tick")) == tick
                ]),
                "private_access": "aggregate_only",
            })
        return {"run_id": run_id, "items": items, "total": len(items)}

    def get_run_tick_world(self, run_id: str, tick: int) -> dict[str, Any]:
        if isinstance(tick, bool) or not isinstance(tick, int) or not 0 <= tick <= 23:
            raise WorkbenchError("ForumTwin tick is invalid")
        domain = self._get_formal_forum_twin_domain(run_id)
        messages = [item for item in domain.get("public_messages", []) if isinstance(item, dict)]
        active = sorted({
            item.get("agent_display_id", item.get("source_agent_display_id"))
            for item in messages if item.get("created_tick") == tick
        })
        pairs = sorted({
            (str(item.get("scenario_id")), str(item.get("branch")))
            for item in messages if item.get("created_tick") == tick
        })
        return {
            "run_id": run_id, "tick": tick, "committed": True,
            "population_size": 1_000, "active_display_ids": active,
            "record_level_relationships_public": False,
            "assets": [
                {
                    "scenario_id": scenario, "branch": branch,
                    "particle_frame": f"/runs/{run_id}/forum-twin/particle-frame?scenario_id={scenario}&branch={branch}&tick={tick}",
                    "propagation_matrix": f"/runs/{run_id}/forum-twin/propagation-matrix?scenario_id={scenario}&branch={branch}&tick={tick}",
                } for scenario, branch in pairs
            ],
        }

    def get_run_tick_forum(self, run_id: str, tick: int) -> dict[str, Any]:
        if isinstance(tick, bool) or not isinstance(tick, int) or not 0 <= tick <= 23:
            raise WorkbenchError("ForumTwin tick is invalid")
        domain = self._get_formal_forum_twin_domain(run_id)
        messages = [
            deepcopy(item) for item in domain.get("public_messages", [])
            if isinstance(item, dict) and item.get("created_tick") == tick
        ]
        thread_ids = {item.get("thread_id") for item in messages}
        threads = [
            deepcopy(item) for item in domain.get("public_threads", [])
            if isinstance(item, dict) and item.get("thread_id") in thread_ids
        ]
        return {"run_id": run_id, "tick": tick, "threads": threads, "messages": messages}

    def get_run_tick_private_conversations(self, run_id: str, tick: int) -> dict[str, Any]:
        if isinstance(tick, bool) or not isinstance(tick, int) or not 0 <= tick <= 23:
            raise WorkbenchError("ForumTwin tick is invalid")
        self._get_formal_forum_twin_domain(run_id)
        return {
            "run_id": run_id, "tick": tick, "access": "aggregate_only",
            "items": [], "contains_conversation_ids": False,
            "boundary": "The public result contains no record-level private conversation text.",
        }

    def get_run_private_conversation(self, run_id: str, conversation_id: str) -> dict[str, Any]:
        self._forum_public_code(conversation_id, "conversation_id")
        self._get_formal_forum_twin_domain(run_id)
        raise WorkbenchNotFoundError(
            "No operator-authorized private conversation record is available"
        )

    def get_run_governance(self, run_id: str) -> dict[str, Any]:
        domain = self._get_formal_forum_twin_domain(run_id)
        return {
            "run_id": run_id,
            "rounds": deepcopy(domain.get("governance_rounds", [])),
            "artifacts": deepcopy(domain.get("governance_artifacts", [])),
            "uptake": deepcopy(domain.get("governance_uptake_map", [])),
        }

    def get_run_risks(self, run_id: str) -> dict[str, Any]:
        domain = self._get_formal_forum_twin_domain(run_id)
        return {
            "run_id": run_id,
            "items": deepcopy(domain.get("risk_results", [])),
            "private_text_included": False,
        }

    def get_live_run_report_artifacts(
        self,
        run_id: str,
        *,
        external_download: bool,
    ) -> LiveReportArtifacts:
        """Build a report only from the Store's atomic aggregate-v2 read."""

        if not isinstance(external_download, bool):
            raise WorkbenchError("external_download must be a boolean")
        run = self.get_run(run_id)
        if run["execution_mode"] == FORUM_TWIN_EXECUTION_MODE:
            import html

            # This helper performs the formal provenance/completeness/privacy
            # gate and therefore rejects every development or fixture run.
            self._get_formal_forum_twin_domain(run_id)
            result_record = self.get_run_runtime_result(run_id)
            document = result_record["result"]
            json_bytes = (
                json.dumps(
                    document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
            domain = document["domain_result"]
            usage = domain.get("llm_usage", {})
            completeness = document["completeness"]
            html_bytes = (
                "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
                "<title>CampusPulse YuLan ForumTwin v1</title>"
                "<style>body{font:16px/1.65 system-ui;max-width:920px;"
                "margin:40px auto;padding:0 20px;color:#173042}"
                "code{background:#edf5f7;padding:2px 5px}"
                "table{border-collapse:collapse}td,th{padding:8px 12px;"
                "border:1px solid #cadde4}</style>"
                "<h1>CampusPulse YuLan ForumTwin v1</h1>"
                "<p>1,000 个数据驱动的合成 LLM 论坛智能体；所有公开论坛文字均来自"
                "真实 LLM 或精确审阅 trace。10,000 粒子仅表达状态不确定性。"
                "结果不代表真实学生、真实关系或因果政策效果。</p><table>"
                "<tr><th>正式语义槽位</th><td>"
                f"{completeness['completed_primary_slots']}/16544</td></tr>"
                "<tr><th>居民 LLM turns</th><td>"
                f"{int(usage.get('resident_turns', 0))}</td></tr>"
                "<tr><th>治理 LLM turns</th><td>"
                f"{int(usage.get('governance_turns', 0))}</td></tr>"
                "<tr><th>公开论坛消息</th><td>"
                f"{len(domain.get('public_messages', []))}</td></tr>"
                "<tr><th>真实治理动作</th><td>0</td></tr></table>"
                "<p>result_sha256: <code>"
                f"{html.escape(document['result_sha256'])}</code></p></html>"
            ).encode("utf-8")
            return LiveReportArtifacts(
                document=document,
                json_bytes=json_bytes,
                html_bytes=html_bytes,
                json_sha256=hashlib.sha256(json_bytes).hexdigest(),
                html_sha256=hashlib.sha256(html_bytes).hexdigest(),
            )
        if run["execution_mode"] == LLM_POPULATION_EXECUTION_MODE:
            import html

            result_record = self.get_run_runtime_result(run_id)
            document = result_record["result"]
            if document.get("publication_eligible") is not True:
                raise WorkbenchConflictError(
                    "Development-only YuLan-Scale runs cannot generate a "
                    "formal report or release artifact"
                )
            json_bytes = (
                json.dumps(
                    document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
            usage = document["usage"]
            completeness = document["completeness"]
            html_bytes = (
                "<!doctype html><html lang=\"zh-CN\">"
                "<meta charset=\"utf-8\">"
                "<title>CampusPulse YuLan-Scale v1</title>"
                "<style>body{font:16px/1.65 system-ui;max-width:920px;"
                "margin:40px auto;padding:0 20px;color:#173042}"
                "code{background:#edf5f7;padding:2px 5px}"
                "table{border-collapse:collapse}td,th{padding:8px 12px;"
                "border:1px solid #cadde4}</style>"
                "<h1>CampusPulse YuLan-Scale v1</h1>"
                "<p>1,000 个 episode 驱动的合成 LLM 智能体；"
                "10,000 粒子表示状态不确定性，不是独立主体。"
                "传播图与政策差异均为模型条件下的合成机制。</p>"
                "<table><tr><th>居民 LLM turns</th><td>"
                f"{usage['resident_llm_turns']}</td></tr>"
                "<tr><th>治理 LLM turns</th><td>"
                f"{usage['governance_llm_turns']}</td></tr>"
                "<tr><th>语义槽位完成</th><td>"
                f"{completeness['completed_semantic_slots']}/13056"
                "</td></tr><tr><th>Provider calls</th><td>"
                f"{usage['live_provider_calls']}</td></tr>"
                "<tr><th>真实治理动作</th><td>0</td></tr></table>"
                "<p>result_sha256: <code>"
                f"{html.escape(document['result_sha256'])}"
                "</code></p></html>"
            ).encode("utf-8")
            return LiveReportArtifacts(
                document=document,
                json_bytes=json_bytes,
                html_bytes=html_bytes,
                json_sha256=hashlib.sha256(json_bytes).hexdigest(),
                html_sha256=hashlib.sha256(html_bytes).hexdigest(),
            )
        if run["execution_mode"] == GOVERNANCE_V2_EXECUTION_MODE:
            import html

            result_record = self.get_run_runtime_result(run_id)
            document = result_record["result"]
            json_bytes = (
                json.dumps(
                    document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
            visual = document["visualization_asset"]
            html_bytes = (
                "<!doctype html><html lang=\"zh-CN\">"
                "<meta charset=\"utf-8\">"
                "<title>CampusPulse Governance Arena v2</title>"
                "<style>body{font:16px/1.65 system-ui;max-width:920px;"
                "margin:40px auto;padding:0 20px;color:#173042}"
                "code{background:#edf5f7;padding:2px 5px}"
                "table{border-collapse:collapse}td,th{padding:8px 12px;"
                "border:1px solid #cadde4}</style>"
                "<h1>CampusPulse Governance Arena v2</h1>"
                "<p>千体混合驱动的模型条件模拟；episode 不是独立学生，"
                "粒子不是独立证据主体，暴露图为完全合成图，"
                "结果不构成因果政策效果。</p><table>"
                "<tr><th>父 episode</th><td>1000</td></tr>"
                "<tr><th>状态粒子</th><td>10000</td></tr>"
                "<tr><th>冻结语义槽位</th><td>8192</td></tr>"
                f"<tr><th>可视化帧</th><td>{visual['frame_count']}</td></tr>"
                "<tr><th>Provider 调用</th><td>0</td></tr>"
                "<tr><th>真实治理动作</th><td>0</td></tr>"
                "</table>"
                "<p>result_sha256: <code>"
                f"{html.escape(document['result_sha256'])}"
                "</code></p></html>"
            ).encode("utf-8")
            return LiveReportArtifacts(
                document=document,
                json_bytes=json_bytes,
                html_bytes=html_bytes,
                json_sha256=hashlib.sha256(json_bytes).hexdigest(),
                html_sha256=hashlib.sha256(html_bytes).hexdigest(),
            )
        if run["execution_mode"] == MASS_EXECUTION_MODE:
            import html

            result_record = self.get_run_runtime_result(run_id)
            document = result_record["result"]
            json_bytes = (
                json.dumps(
                    document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
            summary = document["sampling_summary"]
            html_bytes = (
                "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
                "<title>CampusPulse MassSim v1</title>"
                "<style>body{font:16px/1.65 system-ui;max-width:920px;"
                "margin:40px auto;padding:0 20px;color:#173042}"
                "code{background:#edf5f7;padding:2px 5px}"
                "table{border-collapse:collapse}td,th{padding:8px 12px;"
                "border:1px solid #cadde4}</style>"
                "<h1>CampusPulse MassSim v1</h1>"
                "<p>预算化人口模型条件推演；非真实学生人口、非真实关系图、"
                "非因果识别。</p><table>"
                f"<tr><th>微智能体</th><td>{document['population_identity']['population_size']}</td></tr>"
                f"<tr><th>视觉粒子</th><td>{document['population_identity']['particle_count']}</td></tr>"
                f"<tr><th>探针预算</th><td>{summary['budget_per_round']}</td></tr>"
                f"<tr><th>平均 Kish ESS</th><td>{summary['mean_kish_ess']}</td></tr>"
                f"<tr><th>Provider 调用</th><td>{document['usage']['provider_calls']}</td></tr>"
                "</table>"
                f"<p>result_sha256: <code>{html.escape(document['result_sha256'])}</code></p>"
                "</html>"
            ).encode("utf-8")
            return LiveReportArtifacts(
                document=document,
                json_bytes=json_bytes,
                html_bytes=html_bytes,
                json_sha256=hashlib.sha256(json_bytes).hexdigest(),
                html_sha256=hashlib.sha256(html_bytes).hexdigest(),
            )
        if run["execution_mode"] != LIVE_LLM_ADAPTER_MODE:
            raise WorkbenchConflictError(
                "Run is not an eligible run-plan-v4 live result"
            )
        try:
            material = self.store.get_live_aggregate_v2_report_material(
                self.tenant_id,
                run["project_id"],
                run["run_id"],
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc
        if material is None:
            raise WorkbenchNotFoundError("Run not found")
        return self.build_live_run_report_artifacts_from_material(
            run["run_id"],
            run_material=material["run"],
            extension_material=material["extension"],
            runtime=material["runtime"],
            usage=material["usage"],
            result_record=material["result_record"],
            external_download=external_download,
        )

    def build_live_run_report_artifacts_from_material(
        self,
        run_id: str,
        *,
        run_material: dict[str, Any] | None = None,
        extension_material: dict[str, Any] | None = None,
        runtime: dict[str, Any] | None,
        usage: dict[str, Any] | None,
        result_record: dict[str, Any] | None,
        external_download: bool,
    ) -> LiveReportArtifacts:
        """Validate Store-owned material before report serialization.

        The mounted download path supplies all six objects from the atomic
        schema-v7 Store read.  Explicit material arguments remain available
        for the pure service-boundary tests and fail closed on any identity
        disagreement.
        """

        if not isinstance(external_download, bool):
            raise WorkbenchError("external_download must be a boolean")
        scoped_run = self.get_run(run_id)
        if (run_material is None) != (extension_material is None):
            raise WorkbenchConflictError(
                "Atomic live run and extension material must be paired"
            )
        raw_run = (
            self.store.get_run(scoped_run["run_id"])
            if run_material is None
            else deepcopy(run_material)
        )
        if (
            raw_run is None
            or raw_run.get("project_id") != scoped_run["project_id"]
            or raw_run.get("execution_mode") != LIVE_LLM_ADAPTER_MODE
            or raw_run.get("input_snapshot", {}).get("schema_version")
            != LIVE_PLAN_SCHEMA_VERSION
        ):
            raise WorkbenchConflictError(
                "Run is not an eligible run-plan-v4 live result"
            )
        if extension_material is None:
            extension_row = self.store.get_run_live_plan_extension(
                self.tenant_id,
                raw_run["project_id"],
                raw_run["run_id"],
            )
            if (
                extension_row is None
                or not isinstance(
                    extension_row.get("extension"),
                    dict,
                )
            ):
                raise WorkbenchConflictError(
                    "Live plan extension is not available"
                )
            extension = extension_row["extension"]
        elif isinstance(extension_material, dict):
            extension = deepcopy(extension_material)
        else:
            raise WorkbenchConflictError(
                "Live plan extension is not available"
            )
        if not all(
            isinstance(item, dict)
            for item in (runtime, usage, result_record)
        ):
            raise WorkbenchConflictError(
                "A complete atomic live result read is not available"
            )
        assert runtime is not None
        assert usage is not None
        assert result_record is not None

        ownership = {
            "tenant_id": self.tenant_id,
            "project_id": raw_run["project_id"],
            "run_id": raw_run["run_id"],
        }
        if any(
            any(item.get(key) != expected for key, expected in ownership.items())
            for item in (runtime, usage, result_record)
        ):
            raise WorkbenchConflictError(
                "Live report material is outside the scoped run"
            )
        if runtime.get("status") != "succeeded":
            raise WorkbenchConflictError(
                "Live aggregate report requires a succeeded runtime"
            )

        provenance = usage.get("execution_provenance")
        expected_adapter = {
            DETERMINISTIC_TRANSPORT_PROVENANCE: (
                LIVE_FIXTURE_ADAPTER_MODE
            ),
            LIVE_LLM_EXECUTION_PROVENANCE: (
                LIVE_AUTHORIZED_ADAPTER_MODE
            ),
        }.get(provenance)
        if (
            expected_adapter is None
            or runtime.get("adapter_mode") != expected_adapter
        ):
            raise WorkbenchConflictError(
                "Live runtime provenance and adapter identity do not match"
            )
        if (
            external_download
            and provenance == DETERMINISTIC_TRANSPORT_PROVENANCE
        ):
            raise WorkbenchConflictError(
                "Deterministic fixture reports are not externally downloadable"
            )

        launch_fingerprint = usage.get("launch_fingerprint")
        expected_turns = extension["expected_agent_turns"]
        if (
            runtime.get("launch_fingerprint") != launch_fingerprint
            or result_record.get("launch_fingerprint")
            != launch_fingerprint
            or runtime.get("input_fingerprint")
            != extension["input_fingerprint"]
            or usage.get("input_fingerprint")
            != extension["input_fingerprint"]
            or usage.get("extension_fingerprint")
            != extension["extension_fingerprint"]
            or runtime.get("turn_limit") != expected_turns
            or usage.get("expected_agent_turns") != expected_turns
            or usage.get("turns_completed") != expected_turns
            or runtime.get("effective_concurrency_limit")
            != extension["effective_max_workers"]
            or usage.get("effective_concurrency_limit")
            != extension["effective_max_workers"]
            or runtime.get("token_limit") != extension["token_limit"]
            or usage.get("provider_calls_reserved") != 0
            or usage.get("tokens_in_flight") != 0
            or usage.get("active_attempts") != 0
        ):
            raise WorkbenchConflictError(
                "Live runtime, usage and frozen plan identities do not match"
            )

        result = result_record.get("result")
        if (
            result_record.get("result_schema_version")
            != LIVE_AGGREGATE_RESULT_SCHEMA_VERSION
            or result_record.get("completeness_ppm") != 1_000_000
            or not isinstance(result, dict)
            or result_record.get("result_sha256")
            != result.get("result_fingerprint")
            or result.get("result_fingerprint")
            != result_record.get("result_sha256")
        ):
            raise WorkbenchConflictError(
                "Persisted aggregate-result-v2 identity is invalid"
            )

        launch_identity = {
            "launch_fingerprint": launch_fingerprint,
            "authorization_id": usage.get("authorization_id"),
            "authorization_fingerprint": usage.get(
                "authorization_fingerprint"
            ),
            "transport_config_fingerprint": usage.get(
                "transport_config_fingerprint"
            ),
        }
        slot_set_identity = {
            "slot_set_schema_version": (
                "campus-pulse-live-turn-slot-set-v1"
            ),
            "slot_set_fingerprint": usage.get(
                "slot_set_fingerprint"
            ),
            "slot_count": expected_turns,
        }
        try:
            return build_live_report_artifacts(
                result,
                run=raw_run,
                extension=extension,
                launch_identity=launch_identity,
                slot_set_identity=slot_set_identity,
            )
        except LiveReportExportError as exc:
            raise WorkbenchConflictError(
                "Persisted aggregate-result-v2 failed report validation"
            ) from exc

    def cancel_run_runtime(
        self,
        run_id: str,
        *,
        expected_state_version: int,
        reason_code: str,
    ) -> dict[str, Any]:
        run = self.get_run(run_id)
        if (
            isinstance(expected_state_version, bool)
            or not isinstance(expected_state_version, int)
            or expected_state_version < 0
        ):
            raise WorkbenchError(
                "expected_state_version must be a non-negative integer"
            )
        try:
            validate_cancel_reason(reason_code)
        except RuntimeRuleError as exc:
            raise WorkbenchError(str(exc)) from exc
        if (
            self.store.get_run_runtime(
                self.tenant_id,
                run["project_id"],
                run["run_id"],
            )
            is None
        ):
            raise WorkbenchNotFoundError("Run has not been enqueued")
        try:
            result = self.store.request_run_runtime_cancel(
                self.tenant_id,
                run["project_id"],
                run["run_id"],
                expected_state_version=expected_state_version,
                reason_code=reason_code,
            )
        except StoreConflictError as exc:
            raise WorkbenchConflictError(str(exc)) from exc
        return self._public_runtime(result["runtime"])

    @staticmethod
    def _public_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
        fields = (
            "run_id",
            "project_id",
            "status",
            "state_version",
            "progress_version",
            "launch_fingerprint",
            "adapter_mode",
            "input_fingerprint",
            "token_limit",
            "effective_concurrency_limit",
            "turn_limit",
            "max_attempts",
            "attempt_count",
            "turns_reserved",
            "turns_completed",
            "tokens_reserved",
            "tokens_used",
            "active_units",
            "peak_concurrency",
            "current_attempt_number",
            "lease_generation",
            "lease_expires_at",
            "last_heartbeat_at",
            "checkpoint_sha256",
            "error_code",
            "cancel_reason_code",
            "enqueued_at",
            "started_at",
            "finished_at",
            "updated_at",
        )
        if not isinstance(runtime, dict) or any(
            field not in runtime for field in fields
        ):
            raise WorkbenchConflictError(
                "Persisted runtime does not match the public contract"
            )
        public = {field: runtime[field] for field in fields}
        public["fixture_status"] = (
            FIXTURE_STATUS
            if runtime["adapter_mode"] == FAKE_ADAPTER_MODE
            else None
        )
        return public

    @staticmethod
    def _public_runtime_event(event: dict[str, Any]) -> dict[str, Any]:
        fields = (
            "run_id",
            "sequence",
            "event_type",
            "state_version",
            "progress_version",
            "detail",
            "created_at",
        )
        if not isinstance(event, dict) or any(
            field not in event for field in fields
        ):
            raise WorkbenchConflictError(
                "Persisted runtime event does not match the public contract"
            )
        detail = event["detail"]
        if not isinstance(detail, dict):
            raise WorkbenchConflictError(
                "Persisted runtime event detail is invalid"
            )
        serialized = json.dumps(
            detail,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).lower()
        if any(
            forbidden in serialized
            for forbidden in (
                "prompt",
                "utterance",
                "raw_text",
                "raw_record",
                "source_identifier",
                "persona_record",
                "api_key",
                "password",
                "credential",
                "secret",
                "traceback",
                "provider_body",
                "request_body",
                "response_body",
                "/home/",
                "c:\\",
            )
        ):
            raise WorkbenchConflictError(
                "Persisted runtime event violates the privacy contract"
            )
        return {field: event[field] for field in fields}

    def _with_run_evidence(
        self,
        run: dict[str, Any],
    ) -> dict[str, Any]:
        binding = self.store.get_run_evidence(run["run_id"])
        frozen_evidence = run.get("input_snapshot", {}).get("evidence", {})
        if (
            binding is None
            or binding["tenant_id"] != self.tenant_id
            or binding["project_id"] != run["project_id"]
            or binding["scenario_id"] != run["scenario_id"]
            or frozen_evidence.get("snapshot_id") != binding["snapshot_id"]
            or frozen_evidence.get("analysis_id") != binding["analysis_id"]
            or frozen_evidence.get("manifest_sha256")
            != binding["manifest_sha256"]
        ):
            result = {
                **run,
                "evidence_binding_status": "legacy_unbound",
                "source_evidence": None,
            }
        else:
            result = {
                **run,
                "evidence_binding_status": "sealed",
                "source_evidence": {
                    key: binding[key]
                    for key in (
                        "snapshot_id",
                        "analysis_id",
                        "manifest_sha256",
                        "bound_at",
                    )
                },
            }

        sensing_binding = self.store.get_run_sensing(run["run_id"])
        frozen_sensing = run.get("input_snapshot", {}).get("sensing", {})
        if (
            sensing_binding is None
            or not isinstance(frozen_sensing, dict)
            or frozen_sensing.get("snapshot_id")
            != sensing_binding["snapshot_id"]
            or frozen_sensing.get("manifest_sha256")
            != sensing_binding["manifest_sha256"]
        ):
            has_v3_sensing = (
                run.get("input_snapshot", {}).get("schema_version")
                == "campus-pulse-run-plan-v3"
                or bool(frozen_sensing)
            )
            return {
                **result,
                "sensing_binding_status": (
                    "invalid_unbound"
                    if has_v3_sensing
                    else "legacy_unbound"
                ),
                "sensing_snapshot": None,
            }
        sealed_sensing = {
            key: sensing_binding[key]
            for key in (
                "snapshot_id",
                "manifest_sha256",
                "bound_at",
            )
        }
        if (
            frozen_sensing.get("schema_version")
            == WINDOWED_SENSING_MANIFEST_SCHEMA_VERSION
        ):
            sealed_sensing.update(
                {
                    "schema_version": frozen_sensing["schema_version"],
                    "window_count": frozen_sensing["window_count"],
                    "composition_status": frozen_sensing[
                        "composition_status"
                    ],
                    "windows": frozen_sensing["windows"],
                }
            )
        return {
            **result,
            "sensing_binding_status": "sealed",
            "sensing_snapshot": sealed_sensing,
        }

    @staticmethod
    def _resolve_idempotent_run(
        existing: dict[str, Any],
        requested: dict[str, Any],
    ) -> dict[str, Any]:
        if existing["input_fingerprint"] != requested["input_fingerprint"]:
            raise WorkbenchConflictError(
                "Idempotency key is already bound to a different run plan"
            )
        return existing

    @staticmethod
    def _build_scenario_record(
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise WorkbenchError("Scenario payload must be an object")
        shock_origin = payload.get("shock_origin")
        if shock_origin not in SHOCK_ORIGINS:
            raise WorkbenchError("shock_origin is not allowed")
        phases = WorkbenchService._validate_phases(payload.get("phases"))
        snapshot = _identifier(
            payload.get("evidence_snapshot_id"),
            "evidence_snapshot_id",
            EVIDENCE_HASH_PATTERN,
        )
        template_key = payload.get("template_key")
        if template_key is not None:
            template_key = _bounded_text(
                template_key,
                "template_key",
                maximum=80,
            )
        now = _now()
        return {
            "scenario_id": _new_id("scn"),
            "project_id": project_id,
            "template_key": template_key,
            "name": _bounded_text(payload.get("name"), "name", maximum=140),
            "description": _bounded_text(
                payload.get("description"),
                "description",
                maximum=1000,
            ),
            "shock_origin": shock_origin,
            "phases": phases,
            "evidence_snapshot_id": snapshot,
            "claim_boundary": _bounded_text(
                payload.get("claim_boundary"),
                "claim_boundary",
                maximum=1000,
            ),
            "status": "frozen",
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _build_policy_record(
        project_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise WorkbenchError("Policy payload must be an object")
        evaluation_level = payload.get(
            "evaluation_level",
            "model_conditional",
        )
        if evaluation_level not in EVALUATION_LEVELS:
            raise WorkbenchError("evaluation_level is not publicly assignable")
        actions = WorkbenchService._validate_actions(
            payload.get("actions", [])
        )
        is_baseline = payload.get("is_baseline", False)
        if not isinstance(is_baseline, bool):
            raise WorkbenchError("is_baseline must be a boolean")
        if is_baseline and actions:
            raise WorkbenchError(
                "A baseline policy cannot contain interventions"
            )
        template_key = payload.get("template_key")
        if template_key is not None:
            template_key = _bounded_text(
                template_key,
                "template_key",
                maximum=80,
            )
        now = _now()
        return {
            "policy_id": _new_id("pol"),
            "project_id": project_id,
            "template_key": template_key,
            "name": _bounded_text(payload.get("name"), "name", maximum=120),
            "description": _bounded_text(
                payload.get("description"),
                "description",
                maximum=1000,
            ),
            "is_baseline": is_baseline,
            "actions": actions,
            "evaluation_level": evaluation_level,
            "status": "frozen",
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise WorkbenchError(f"{field} must be an integer")
        if not minimum <= value <= maximum:
            raise WorkbenchError(
                f"{field} must be between {minimum} and {maximum}"
            )
        return value

    @staticmethod
    def _validate_phases(value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list) or len(value) != 4:
            raise WorkbenchError("phases must contain exactly four stages")
        expected_ids = ["baseline", "burst", "spread", "decay"]
        phases = []
        for expected_id, phase in zip(expected_ids, value):
            if not isinstance(phase, dict) or set(phase) != {
                "phase_id",
                "label",
                "window",
            }:
                raise WorkbenchError("Each phase has an invalid contract")
            if phase["phase_id"] != expected_id:
                raise WorkbenchError(
                    "Phase order must be baseline, burst, spread, decay"
                )
            phases.append(
                {
                    "phase_id": expected_id,
                    "label": _bounded_text(
                        phase["label"],
                        "phase.label",
                        maximum=40,
                    ),
                    "window": _bounded_text(
                        phase["window"],
                        "phase.window",
                        maximum=80,
                    ),
                }
            )
        return phases

    @staticmethod
    def _validate_actions(value: Any) -> list[dict[str, str]]:
        if not isinstance(value, list) or len(value) > 12:
            raise WorkbenchError("actions must be a list with at most 12 items")
        actions = []
        seen_ids = set()
        action_id_pattern = re.compile(r"^[a-z][a-z0-9_]{2,39}$")
        for action in value:
            if not isinstance(action, dict) or set(action) != {
                "action_id",
                "label",
                "commitment",
            }:
                raise WorkbenchError("Each action has an invalid contract")
            action_id = _identifier(
                action["action_id"],
                "action.action_id",
                action_id_pattern,
            )
            if action_id in seen_ids:
                raise WorkbenchError("action_id values must be unique")
            seen_ids.add(action_id)
            actions.append(
                {
                    "action_id": action_id,
                    "label": _bounded_text(
                        action["label"],
                        "action.label",
                        maximum=80,
                    ),
                    "commitment": _bounded_text(
                        action["commitment"],
                        "action.commitment",
                        maximum=500,
                    ),
                }
            )
        return actions

    def _audit(
        self,
        project_id: str | None,
        entity_type: str,
        entity_id: str,
        action: str,
        detail: dict[str, Any],
        created_at: str,
    ) -> dict[str, Any]:
        return {
            "event_id": _new_id("evt"),
            "tenant_id": self.tenant_id,
            "project_id": project_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "action": action,
            "detail": detail,
            "created_at": created_at,
        }
