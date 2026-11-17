"""Versioned HTTP API for the isolated CampusPulse workbench domain."""

from __future__ import annotations

import os
import threading
import asyncio
import ipaddress
import time
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Header, HTTPException, Path as ApiPath, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from backend.campus_pulse.service import (
    WorkbenchError,
    WorkbenchService,
)
from backend.campus_pulse.store import WorkbenchStore
from backend.campus_pulse.provider_config import (
    ProviderConfigError,
    delete_provider_config,
    load_provider_credentials,
    provider_chat_completions_url,
    provider_config_status,
    save_provider_config,
)
from backend.campus_pulse.translation import (
    TranslationUnavailable,
    translate_public_synthetic_text,
)
from backend.campus_pulse.century_gym_demo import (
    CenturyGymDemoError,
    social_world as century_gym_social_world,
    start as start_century_gym_demo,
    status as century_gym_demo_status,
    replay_workspace,
    replay_session,
)
from backend.campus_pulse.forum_live_vignette import (
    ForumLiveVignetteError,
    prepare as prepare_forum_live_vignette,
    resolve_unknown as resolve_forum_live_vignette_unknown,
    start as start_forum_live_vignette,
    status as forum_live_vignette_status,
)
from backend.campus_pulse.resource_private_channels import (
    ResourcePrivateChannelError,
    private_channel_summary as resource_private_channel_summary,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ProjectCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    governance_domain: str = Field(min_length=1, max_length=80)
    objective: str = Field(min_length=1, max_length=800)
    evaluation_mode: Literal[
        "simulation_stress_test",
        "descriptive_pilot",
    ] = "simulation_stress_test"


class ProjectSetup(StrictModel):
    family: Literal["century_gym", "housing", "lecture", "custom"]
    description: str = Field(min_length=1, max_length=1000)
    scope: str = Field(min_length=1, max_length=1000)


class ProjectUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    governance_domain: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
    )
    objective: str | None = Field(
        default=None,
        min_length=1,
        max_length=800,
    )
    evaluation_mode: Literal[
        "simulation_stress_test",
        "descriptive_pilot",
    ] | None = None

    @model_validator(mode="after")
    def require_non_null_change(self):
        if not self.model_fields_set:
            raise ValueError("At least one project field is required")
        if any(
            getattr(self, field) is None for field in self.model_fields_set
        ):
            raise ValueError("Project fields cannot be null")
        return self


class AgentProfileOverridePut(StrictModel):
    roleDescription: str = Field(min_length=1, max_length=2_000)
    stableBackground: str = Field(min_length=1, max_length=2_000)
    topics: str = Field(min_length=1, max_length=2_000)
    needs: str = Field(min_length=1, max_length=2_000)
    expressionStyle: str = Field(min_length=1, max_length=2_000)
    judgementStyle: str = Field(min_length=1, max_length=2_000)
    participationStyle: str = Field(min_length=1, max_length=2_000)
    activityStyle: str = Field(min_length=1, max_length=2_000)
    behaviorBoundaries: str = Field(min_length=1, max_length=2_000)
    longTermMemory: str = Field(min_length=1, max_length=2_000)
    projectInstructions: str = Field(default="", max_length=2_000)


class PhaseInput(StrictModel):
    phase_id: Literal["baseline", "burst", "spread", "decay"]
    label: str = Field(min_length=1, max_length=40)
    window: str = Field(min_length=1, max_length=80)


class ScenarioCreate(StrictModel):
    name: str = Field(min_length=1, max_length=140)
    description: str = Field(min_length=1, max_length=1000)
    shock_origin: Literal[
        "governance_action",
        "external_event",
        "synthetic_stress",
    ]
    phases: list[PhaseInput] = Field(min_length=4, max_length=4)
    analysis_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description=(
            "Registered analysis artifact whose parent source snapshot is "
            "attached to this project."
        ),
    )
    evidence_snapshot_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
        description=(
            "Deprecated v1 compatibility alias for analysis_id; this field "
            "does not identify a source snapshot."
        ),
        json_schema_extra={"deprecated": True},
    )
    claim_boundary: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def require_consistent_analysis_reference(self):
        analysis_id = self.analysis_id
        legacy_id = self.evidence_snapshot_id
        if analysis_id is None and legacy_id is None:
            raise ValueError("analysis_id is required")
        if (
            analysis_id is not None
            and legacy_id is not None
            and analysis_id != legacy_id
        ):
            raise ValueError(
                "analysis_id and evidence_snapshot_id must match"
            )
        return self

    def to_service_payload(self) -> dict:
        """Map the explicit API name to the v1 internal service contract."""

        payload = self.model_dump(
            exclude={"analysis_id", "evidence_snapshot_id"},
        )
        payload["evidence_snapshot_id"] = (
            self.analysis_id or self.evidence_snapshot_id
        )
        return payload


class EvidenceAttach(StrictModel):
    make_primary: bool = True


class SensingActivation(StrictModel):
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_state_version: int = Field(ge=0)


class PolicyActionInput(StrictModel):
    action_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,39}$")
    label: str = Field(min_length=1, max_length=80)
    commitment: str = Field(min_length=1, max_length=500)


class PolicyCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    is_baseline: bool = False
    actions: list[PolicyActionInput] = Field(default_factory=list, max_length=12)
    evaluation_level: Literal[
        "model_conditional",
        "empirical_candidate",
    ] = "model_conditional"


class RunPlanCreate(StrictModel):
    scenario_id: str
    execution_mode: Literal[
        "audited_replay",
        "live_llm",
        "budgeted_population",
        "adaptive_particle_population",
        "budgeted_llm_agent_population",
        "llm_forum_twin",
        "llm_forum_twin_v2",
    ] = "audited_replay"
    execution_provenance: Literal[
        "emulator_only_development",
        "reviewed_trace_replay",
        "authorized_live_llm",
    ] | None = None
    forum_activation_mode: Literal[
        "budgeted_pps",
        "full_population_keyframes",
        "full_population_every_tick",
    ] | None = None
    forum_keyframe_ticks: list[int] = Field(
        default_factory=list,
        max_length=24,
    )
    forum_public_anonymity_enabled: bool | None = None
    forum_relationship_attention_enabled: bool | None = None
    forum_friend_chat_enabled: bool | None = None
    forum_dynamic_group_enabled: bool | None = None
    forum_service_desk_enabled: bool | None = None
    population_release_id: str = "persona-release-v1-reviewed-17x3"
    policy_ids: list[str] = Field(min_length=1, max_length=8)
    agent_count: int = Field(default=48, ge=1, le=1000)
    seeds: list[int] = Field(
        default_factory=lambda: [20260722, 20261731],
        min_length=1,
        max_length=8,
    )
    model_name: str = Field(
        default="gpt-5.6-sol",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{1,79}$",
    )
    provider_profile_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{1,95}$",
    )
    provider_call_limit: int | None = Field(
        default=None,
        ge=1,
        le=10_000,
    )
    max_workers: int = Field(default=32, ge=1, le=48)
    token_budget: int = Field(default=2_000_000, ge=1_000, le=80_000_000)

    @model_validator(mode="after")
    def enforce_execution_mode_token_contract(self):
        # The service and frozen runtime contracts already define these
        # mode-specific ceilings. Keep the HTTP boundary aligned without
        # changing simulation or provider-budget semantics.
        if self.execution_mode == "llm_forum_twin_v2":
            mode = self.forum_activation_mode or "budgeted_pps"
            ticks = self.forum_keyframe_ticks
            if mode == "full_population_keyframes":
                if not ticks or len(ticks) != len(set(ticks)) or any(
                    isinstance(tick, bool)
                    or not isinstance(tick, int)
                    or not 0 <= tick <= 23
                    for tick in ticks
                ):
                    raise ValueError(
                        "full_population_keyframes requires unique ticks in 0..23"
                    )
            elif ticks:
                raise ValueError(
                    "forum_keyframe_ticks are only valid in keyframe mode"
                )
            friend_chat = (
                True
                if self.forum_friend_chat_enabled is None
                else self.forum_friend_chat_enabled
            )
            dynamic_group = (
                True
                if self.forum_dynamic_group_enabled is None
                else self.forum_dynamic_group_enabled
            )
            if dynamic_group and not friend_chat:
                raise ValueError(
                    "forum_dynamic_group_enabled requires friend chat"
                )
        elif (
            self.forum_activation_mode is not None
            or self.forum_keyframe_ticks
            or any(
                value is not None
                for value in (
                    self.forum_public_anonymity_enabled,
                    self.forum_relationship_attention_enabled,
                    self.forum_friend_chat_enabled,
                    self.forum_dynamic_group_enabled,
                    self.forum_service_desk_enabled,
                )
            )
        ):
            raise ValueError(
                "ForumTwin v2 world fields require llm_forum_twin_v2"
            )
        if "token_budget" not in self.model_fields_set:
            return self
        required_budget = {
            "budgeted_llm_agent_population": 40_000_000,
            "llm_forum_twin": 80_000_000,
        }.get(self.execution_mode)
        if required_budget is not None and self.token_budget != required_budget:
            raise ValueError(
                f"token_budget must be {required_budget} for "
                f"execution_mode {self.execution_mode}"
            )
        if (
            required_budget is None
            and self.execution_mode != "llm_forum_twin_v2"
            and self.token_budget > 5_000_000
        ):
            raise ValueError(
                "token_budget must not exceed 5000000 for this execution_mode"
            )
        return self


class RunEnqueue(StrictModel):
    expected_state_version: int = Field(ge=0, le=0)
    turn_budget: int = Field(ge=1, le=3_000_000)
    max_attempts: int = Field(ge=1, le=5)


class RunCancel(StrictModel):
    expected_state_version: int = Field(ge=0)
    reason_code: Literal[
        "operator_requested",
        "superseded_run",
        "budget_revision",
    ]


class TranslationRequest(StrictModel):
    text: str = Field(min_length=1, max_length=1500)
    target_language: Literal["en"] = "en"


class ProviderConfigUpdate(StrictModel):
    """OpenAI-compatible defaults stored only on the current machine."""

    base_url: str = Field(min_length=8, max_length=500)
    model_name: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$",
    )
    api_key: SecretStr | None = Field(
        default=None,
        min_length=8,
        max_length=4096,
        json_schema_extra={"writeOnly": True},
    )
    reasoning_effort: Literal["none", "low", "medium", "high"] = "none"
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=512, ge=16, le=8192)
    timeout_seconds: int = Field(default=180, ge=5, le=600)
    max_concurrency: int = Field(default=8, ge=1, le=64)


class ForumLiveVignettePrepare(StrictModel):
    scenario_id: Literal[
        "century_gym_ghost_booking_dispute",
        "governance_legitimacy_dispute",
        "lecture_external_incident_shock",
    ]
    run_scope: Literal[
        "quick_closed_loop",
        "resource_public_private_closed_loop",
    ] = "quick_closed_loop"


class ForumLiveVignetteStart(StrictModel):
    preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    authorization_statement_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_acknowledged: Literal[True]


class ForumLiveVignetteUnknownResolution(StrictModel):
    preflight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolution_statement_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    late_result_rejected: Literal[True]
    replacement_limit_acknowledged: Literal[1]


router = APIRouter(
    prefix="/campus-pulse/v1",
    tags=["campus-pulse-workbench"],
)

_service: WorkbenchService | None = None
_service_path: Path | None = None
_service_runtime_configuration: tuple[str, int, bool] | None = None
_service_lock = threading.Lock()


def _database_path() -> Path:
    configured = os.environ.get(
        "CAMPUS_PULSE_WORKBENCH_DB",
        "data/campus_pulse_workspace/workbench.sqlite3",
    )
    return Path(configured).expanduser().resolve()


def get_service() -> WorkbenchService:
    global _service, _service_path, _service_runtime_configuration
    path = _database_path()
    adapter_mode = os.environ.get(
        "CAMPUS_PULSE_RUNTIME_ADAPTER",
        "disabled",
    ).strip()
    configured_cap = os.environ.get(
        "CAMPUS_PULSE_RUNTIME_CONCURRENCY_CAP",
        "32",
    ).strip()
    try:
        concurrency_cap = int(configured_cap)
    except ValueError as exc:
        raise RuntimeError(
            "CAMPUS_PULSE_RUNTIME_CONCURRENCY_CAP must be an integer"
        ) from exc
    live_gate_text = os.environ.get(
        "CAMPUS_PULSE_LIVE_LLM_ENABLED",
        "false",
    ).strip().lower()
    if live_gate_text not in {"true", "false"}:
        raise RuntimeError(
            "CAMPUS_PULSE_LIVE_LLM_ENABLED must be true or false"
        )
    live_llm_enabled = live_gate_text == "true"
    runtime_configuration = (
        adapter_mode,
        concurrency_cap,
        live_llm_enabled,
    )
    if (
        _service is None
        or _service_path != path
        or _service_runtime_configuration != runtime_configuration
    ):
        # FastAPI runs sync handlers in a thread pool. The workbench home page
        # intentionally loads health, overview and projects concurrently, so
        # first-use service construction must be serialized.
        with _service_lock:
            if (
                _service is None
                or _service_path != path
                or _service_runtime_configuration
                != runtime_configuration
            ):
                _service = WorkbenchService(
                    WorkbenchStore(path),
                    runtime_adapter_mode=adapter_mode,
                    runtime_concurrency_cap=concurrency_cap,
                    live_llm_enabled=live_llm_enabled,
                )
                _service_path = path
                _service_runtime_configuration = runtime_configuration
    return _service


def reset_service_for_tests() -> None:
    global _service, _service_path, _service_runtime_configuration
    with _service_lock:
        _service = None
        _service_path = None
        _service_runtime_configuration = None


def _data(value):
    return {
        "data": value,
        "meta": {
            "schema_version": "campus-pulse-workbench-api-v1",
        },
        "error": None,
    }


def _invoke(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except WorkbenchError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "code": exc.__class__.__name__,
                "message": str(exc),
            },
        ) from exc


def _require_loopback(request: Request) -> None:
    """Keep secret-bearing endpoints local even if the API is network-bound."""

    host = request.client.host if request.client else ""
    if host == "testclient":
        return
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host.lower() == "localhost"
    if not is_loopback:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "LocalProviderConfigurationOnly",
                "message": "Provider configuration is available only from loopback",
            },
        )


def _provider_config_error(exc: ProviderConfigError, status_code: int = 422):
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": "ProviderConfigurationError",
            "message": str(exc),
        },
    ) from exc


@router.get("/provider-config")
def get_local_provider_config(request: Request):
    """Read public metadata; the API key is never returned."""

    _require_loopback(request)
    try:
        return _data(provider_config_status())
    except ProviderConfigError as exc:
        _provider_config_error(exc, status_code=500)


@router.put("/provider-config")
def put_local_provider_config(payload: ProviderConfigUpdate, request: Request):
    """Atomically save public defaults and an optional replacement key."""

    _require_loopback(request)
    try:
        values = payload.model_dump()
        values["api_key"] = (
            payload.api_key.get_secret_value()
            if payload.api_key is not None
            else None
        )
        return _data(save_provider_config(**values))
    except ProviderConfigError as exc:
        _provider_config_error(exc)


@router.delete("/provider-config")
def clear_local_provider_config(request: Request):
    _require_loopback(request)
    try:
        return _data(delete_provider_config())
    except ProviderConfigError as exc:
        _provider_config_error(exc, status_code=500)


@router.post("/provider-config/test")
async def test_local_provider_config(request: Request):
    """Send one minimal, project-data-free connectivity request with no retry."""

    _require_loopback(request)
    try:
        config, api_key = load_provider_credentials()
        endpoint = provider_chat_completions_url(config["base_url"])
    except ProviderConfigError as exc:
        _provider_config_error(exc)

    body = {
        "model": config["model_name"],
        "messages": [
            {
                "role": "user",
                "content": "Return exactly: {\"status\":\"ok\"}",
            }
        ],
        "reasoning_effort": config["reasoning_effort"],
        "max_tokens": min(32, config["max_output_tokens"]),
    }
    if config["temperature"] is not None:
        body["temperature"] = config["temperature"]

    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(float(config["timeout_seconds"])),
        ) as client:
            response = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail={
                "code": "ProviderConnectivityTimeout",
                "message": "Provider connectivity test timed out; no automatic retry was made",
            },
        ) from exc
    except httpx.TransportError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "ProviderConnectivityFailed",
                "message": "Provider connectivity test failed before a valid response was received",
            },
        ) from exc

    latency_ms = round((time.perf_counter() - started) * 1000)
    if not response.is_success:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "ProviderRejectedTestRequest",
                "message": f"Provider returned HTTP {response.status_code}; response body was not retained",
            },
        )
    try:
        response_value = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "ProviderTestResponseInvalid",
                "message": "Provider returned a non-JSON response; response body was not retained",
            },
        ) from exc
    if not isinstance(response_value, dict) or not isinstance(
        response_value.get("choices"), list
    ):
        raise HTTPException(
            status_code=502,
            detail={
                "code": "ProviderTestResponseInvalid",
                "message": "Provider response did not match the chat-completions contract",
            },
        )
    usage = response_value.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    return _data(
        {
            "connected": True,
            "diagnostic_code": "provider_chat_completions_ok",
            "provider_http_status": response.status_code,
            "latency_ms": latency_ms,
            "model_name": config["model_name"],
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "automatic_retries": 0,
            "project_data_sent": False,
            "response_body_retained": False,
        }
    )


@router.get("/live-demos/century-gym/status")
def get_century_gym_demo_status(request: Request):
    """Return non-secret local status for the fixed competition vignette."""

    _require_loopback(request)
    return _data(century_gym_demo_status())


@router.get("/live-demos/century-gym/social-world")
def get_century_gym_social_world(request: Request):
    """Return the reviewed aggregate social-world overlay for the demo."""

    _require_loopback(request)
    try:
        return _data(century_gym_social_world())
    except CenturyGymDemoError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CenturyGymSocialWorldUnavailable",
                "message": str(exc),
            },
        ) from exc


@router.get("/cases/resource-allocation/private-channel-summary")
def get_resource_private_channel_summary(request: Request):
    """Return the hash-bound aggregate private-channel supplement."""

    _require_loopback(request)
    try:
        return _data(resource_private_channel_summary())
    except ResourcePrivateChannelError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ResourcePrivateChannelSummaryUnavailable",
                "message": str(exc),
            },
        ) from exc


@router.post("/live-demos/century-gym/start", status_code=202)
def start_century_gym_demo_run(request: Request):
    """Start exactly the reviewed 16-slot audit; no caller-defined scope."""

    _require_loopback(request)
    try:
        return _data(start_century_gym_demo())
    except CenturyGymDemoError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CenturyGymDemoUnavailable",
                "message": str(exc),
            },
        ) from exc


@router.post("/live-vignettes/prepare", status_code=201)
def prepare_quick_forum_live_vignette(
    payload: ForumLiveVignettePrepare,
    request: Request,
):
    """Freeze one body-free ForumTwin preflight; this sends no prompt."""

    _require_loopback(request)
    try:
        return _data(
            prepare_forum_live_vignette(
                payload.scenario_id,
                run_scope=payload.run_scope,
            )
        )
    except ForumLiveVignetteError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ForumLiveVignetteUnavailable",
                "message": str(exc),
            },
        ) from exc


@router.get("/live-vignettes/{session_id}/status")
def get_quick_forum_live_vignette_status(
    session_id: Annotated[
        str,
        ApiPath(pattern=r"^forum-vignette-[a-z0-9]{12}$"),
    ],
    request: Request,
):
    """Return only public progress and immutable budget metadata."""

    _require_loopback(request)
    try:
        return _data(forum_live_vignette_status(session_id))
    except ForumLiveVignetteError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "ForumLiveVignetteNotFound",
                "message": str(exc),
            },
        ) from exc


@router.post("/live-vignettes/{session_id}/start", status_code=202)
def start_quick_forum_live_vignette(
    session_id: Annotated[
        str,
        ApiPath(pattern=r"^forum-vignette-[a-z0-9]{12}$"),
    ],
    payload: ForumLiveVignetteStart,
    request: Request,
):
    """Record the displayed one-time acknowledgement and launch the run."""

    _require_loopback(request)
    try:
        return _data(
            start_forum_live_vignette(
                session_id,
                preflight_sha256=payload.preflight_sha256,
                authorization_statement_sha256=(
                    payload.authorization_statement_sha256
                ),
            )
        )
    except ForumLiveVignetteError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ForumLiveVignetteUnavailable",
                "message": str(exc),
            },
        ) from exc


@router.post(
    "/live-vignettes/{session_id}/resolve-unknown",
    status_code=200,
)
def resolve_quick_forum_live_vignette_unknown(
    session_id: Annotated[
        str,
        ApiPath(pattern=r"^forum-vignette-[a-z0-9]{12}$"),
    ],
    payload: ForumLiveVignetteUnknownResolution,
    request: Request,
):
    """Abandon one unknown request and authorize exactly one replacement."""

    _require_loopback(request)
    try:
        return _data(
            resolve_forum_live_vignette_unknown(
                session_id,
                preflight_sha256=payload.preflight_sha256,
                resolution_statement_sha256=(
                    payload.resolution_statement_sha256
                ),
            )
        )
    except ForumLiveVignetteError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ForumLiveVignetteUnknownResolutionUnavailable",
                "message": str(exc),
            },
        ) from exc


@router.get("/health")
def health():
    return _data({**get_service().health(), "workspace": replay_workspace()})


class ReplayCursor(StrictModel):
    tick: int = Field(ge=0, le=10)
    branch: Literal["Natural", "D"]
    status: Literal["playing", "paused", "completed"]
    revision: int = Field(ge=0)


def _project_replay(project_id: str, **kwargs):
    _invoke(get_service().get_project, project_id)
    scenarios = _invoke(get_service().list_scenarios, project_id)
    if not any(s.get("template_key") in (
        "project_setup_century_gym_v1", "century_gym_ghost_booking_v1"
    ) for s in scenarios):
        raise HTTPException(status_code=409, detail="Select a Century Gym scenario to replay this saved run")
    try:
        return _data(replay_session(project_id, **kwargs))
    except CenturyGymDemoError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/projects/{project_id}/replay")
def get_project_replay(project_id: str):
    return _project_replay(project_id)


@router.post("/projects/{project_id}/replay", status_code=201)
def start_project_replay(project_id: str):
    return _project_replay(project_id, start=True)


@router.put("/projects/{project_id}/replay")
def update_project_replay(project_id: str, payload: ReplayCursor):
    return _project_replay(project_id, progress=payload.model_dump())


@router.post("/translate")
async def translate_public_text(payload: TranslationRequest):
    """Translate only already-public synthetic text; never persist the body."""

    try:
        translated = await asyncio.to_thread(
            translate_public_synthetic_text,
            payload.text,
            payload.target_language,
        )
    except TranslationUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "TranslationUnavailable", "message": str(exc)},
        ) from exc
    return _data(translated)


@router.get("/readiness")
def readiness():
    return _data(get_service().readiness())


@router.get("/overview")
def overview():
    return _data(get_service().overview())


@router.get("/forum-twin-v2/agent-world")
def forum_twin_v2_agent_world():
    """Public aggregate projection of the fixed ForumTwin Agent world."""

    return _data(_invoke(get_service().get_forum_twin_v2_agent_world))


@router.get("/forum-twin-v2/agent-world/agents/{display_id}")
def forum_twin_v2_agent_dossier(
    display_id: str = ApiPath(min_length=3, max_length=96),
    project_id: str | None = Query(default=None),
):
    """Inspect one synthetic Agent without exposing its private lineage."""

    return _data(
        _invoke(
            get_service().get_forum_twin_v2_agent_dossier,
            display_id,
            project_id=project_id,
        )
    )


@router.put(
    "/projects/{project_id}/forum-twin-v2/agents/{display_id}/profile-override"
)
def save_forum_twin_v2_agent_profile_override(
    payload: AgentProfileOverridePut,
    project_id: str = ApiPath(min_length=3, max_length=96),
    display_id: str = ApiPath(min_length=3, max_length=96),
):
    """Append a project-scoped, hash-bound Agent profile release."""

    return _data(
        _invoke(
            get_service().save_forum_twin_v2_agent_profile_override,
            project_id,
            display_id,
            payload.model_dump(),
        )
    )


@router.delete(
    "/projects/{project_id}/forum-twin-v2/agents/{display_id}/profile-override"
)
def restore_forum_twin_v2_agent_profile(
    project_id: str = ApiPath(min_length=3, max_length=96),
    display_id: str = ApiPath(min_length=3, max_length=96),
):
    """Restore the reviewed profile by appending a new immutable release."""

    return _data(
        _invoke(
            get_service().restore_forum_twin_v2_agent_profile,
            project_id,
            display_id,
        )
    )


@router.get("/live-runtime/capability")
def live_runtime_capability(
    run_id: str | None = Query(default=None),
):
    return _data(
        _invoke(get_service().live_runtime_capability, run_id)
    )


@router.get("/evidence/releases")
def list_installed_evidence_releases():
    return _data(get_service().list_installed_evidence_releases())


@router.post(
    "/evidence/releases/{release_key}/register",
    status_code=201,
)
async def register_reviewed_evidence_release(
    release_key: str,
    request: Request,
):
    if await request.body():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "UnexpectedRequestBody",
                "message": (
                    "Evidence release registration accepts no request body"
                ),
            },
        )
    return _data(
        _invoke(
            get_service().register_reviewed_evidence_release,
            release_key,
        )
    )


@router.get("/evidence-snapshots")
def list_evidence_snapshots():
    return _data(get_service().list_evidence_snapshots())


@router.get("/evidence-snapshots/{snapshot_id}")
def get_evidence_snapshot(snapshot_id: str):
    return _data(
        _invoke(get_service().get_evidence_snapshot, snapshot_id)
    )


@router.get("/sensing/releases")
def list_installed_sensing_releases():
    return _data(get_service().list_installed_sensing_releases())


@router.post(
    "/sensing/releases/{release_key}/register",
    status_code=201,
)
async def register_reviewed_sensing_release(
    release_key: str,
    request: Request,
):
    if await request.body():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "UnexpectedRequestBody",
                "message": (
                    "Sensing release registration accepts no request body"
                ),
            },
        )
    return _data(
        _invoke(
            get_service().register_reviewed_sensing_release,
            release_key,
        )
    )


@router.get("/sensing-snapshots")
def list_sensing_snapshots():
    return _data(get_service().list_sensing_snapshots())


@router.get("/sensing-snapshots/{snapshot_id}")
def get_sensing_snapshot(snapshot_id: str):
    return _data(
        _invoke(get_service().get_sensing_snapshot, snapshot_id)
    )


@router.post("/projects", status_code=201)
def create_project(payload: ProjectCreate):
    return _data(_invoke(get_service().create_project, payload.model_dump()))


@router.get("/projects")
def list_projects():
    return _data(get_service().list_projects())


@router.get("/projects/{project_id}")
def get_project(project_id: str):
    return _data(_invoke(get_service().get_project, project_id))


@router.patch("/projects/{project_id}")
def update_project(project_id: str, payload: ProjectUpdate):
    return _data(
        _invoke(
            get_service().update_project,
            project_id,
            payload.model_dump(exclude_unset=True),
        )
    )


@router.delete("/projects/{project_id}", status_code=204)
def archive_project(project_id: str):
    _invoke(get_service().archive_project, project_id)
    return Response(status_code=204)


@router.post("/projects/{project_id}/bootstrap", status_code=201)
def bootstrap_project(project_id: str, payload: ProjectSetup | None = None):
    return _data(
        _invoke(get_service().bootstrap_reviewed_templates, project_id,
                payload.model_dump() if payload else None)
    )


@router.get("/projects/{project_id}/evidence-snapshots")
def list_project_evidence(project_id: str):
    return _data(
        _invoke(get_service().list_project_evidence, project_id)
    )


@router.post(
    "/projects/{project_id}/evidence-snapshots/{snapshot_id}/attach"
)
def attach_project_evidence(
    project_id: str,
    snapshot_id: str,
    payload: EvidenceAttach,
):
    return _data(
        _invoke(
            get_service().attach_project_evidence,
            project_id,
            snapshot_id,
            payload.make_primary,
        )
    )


@router.get("/projects/{project_id}/sensing-snapshots")
def list_project_sensing(project_id: str):
    return _data(
        _invoke(get_service().list_project_sensing, project_id)
    )


@router.post(
    "/projects/{project_id}/sensing-snapshots/{snapshot_id}/attach"
)
async def attach_project_sensing(
    project_id: str,
    snapshot_id: str,
    request: Request,
):
    if await request.body():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "UnexpectedRequestBody",
                "message": (
                    "Sensing snapshot attachment accepts no request body"
                ),
            },
        )
    return _data(
        _invoke(
            get_service().attach_project_sensing,
            project_id,
            snapshot_id,
        )
    )


@router.get("/projects/{project_id}/sensing-state")
def get_project_sensing_state(project_id: str):
    return _data(
        _invoke(get_service().get_project_sensing_state, project_id)
    )


@router.put("/projects/{project_id}/sensing-state/active")
def activate_project_sensing(
    project_id: str,
    payload: SensingActivation,
):
    return _data(
        _invoke(
            get_service().activate_project_sensing,
            project_id,
            payload.snapshot_id,
            payload.expected_state_version,
        )
    )


@router.post("/projects/{project_id}/scenarios", status_code=201)
def create_scenario(project_id: str, payload: ScenarioCreate):
    return _data(
        _invoke(
            get_service().create_scenario,
            project_id,
            payload.to_service_payload(),
        )
    )


@router.get("/projects/{project_id}/scenarios")
def list_scenarios(project_id: str):
    return _data(_invoke(get_service().list_scenarios, project_id))


@router.post("/projects/{project_id}/policies", status_code=201)
def create_policy(project_id: str, payload: PolicyCreate):
    return _data(
        _invoke(
            get_service().create_policy,
            project_id,
            payload.model_dump(),
        )
    )


@router.get("/projects/{project_id}/policies")
def list_policies(project_id: str):
    return _data(_invoke(get_service().list_policies, project_id))


@router.post("/projects/{project_id}/runs", status_code=201)
def plan_run(
    project_id: str,
    payload: RunPlanCreate,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key"),
    ] = None,
):
    return _data(
        _invoke(
            get_service().plan_run,
            project_id,
            payload.model_dump(exclude_unset=True),
            idempotency_key,
        )
    )


@router.get("/projects/{project_id}/runs")
def list_runs(project_id: str):
    return _data(_invoke(get_service().list_runs, project_id))


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    return _data(_invoke(get_service().get_run, run_id))


@router.post("/runs/{run_id}/enqueue", status_code=202)
def enqueue_run_runtime(
    run_id: str,
    payload: RunEnqueue,
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
        ),
    ],
):
    return _data(
        _invoke(
            get_service().enqueue_run_runtime,
            run_id,
            idempotency_key=idempotency_key,
            **payload.model_dump(),
        )
    )


@router.get("/runs/{run_id}/runtime")
def get_run_runtime(run_id: str):
    return _data(_invoke(get_service().get_run_runtime, run_id))


@router.get("/runs/{run_id}/events")
def list_run_runtime_events(
    run_id: str,
    after_sequence: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    return _data(
        _invoke(
            get_service().list_run_runtime_events,
            run_id,
            after_sequence=after_sequence,
            limit=limit,
        )
    )


@router.get("/runs/{run_id}/result")
def get_run_runtime_result(run_id: str):
    return _data(
        _invoke(get_service().get_run_runtime_result, run_id)
    )


@router.get("/runs/{run_id}/ticks")
def list_run_ticks(run_id: str):
    return _data(_invoke(get_service().list_run_ticks, run_id))


@router.get("/runs/{run_id}/ticks/{tick}/world")
def get_run_tick_world(run_id: str, tick: Annotated[int, ApiPath(ge=0, le=23)]):
    return _data(_invoke(get_service().get_run_tick_world, run_id, tick))


@router.get("/runs/{run_id}/ticks/{tick}/forum")
def get_run_tick_forum(run_id: str, tick: Annotated[int, ApiPath(ge=0, le=23)]):
    return _data(_invoke(get_service().get_run_tick_forum, run_id, tick))


@router.get("/runs/{run_id}/ticks/{tick}/private-conversations")
def get_run_tick_private_conversations(
    run_id: str, tick: Annotated[int, ApiPath(ge=0, le=23)]
):
    return _data(
        _invoke(get_service().get_run_tick_private_conversations, run_id, tick)
    )


@router.get("/runs/{run_id}/private-conversations/{conversation_id}")
def get_run_private_conversation(run_id: str, conversation_id: str):
    return _data(
        _invoke(get_service().get_run_private_conversation, run_id, conversation_id)
    )


@router.get("/runs/{run_id}/agents/{display_id}")
def get_run_agent(run_id: str, display_id: str):
    return _data(
        _invoke(get_service().get_forum_twin_display_profile, run_id, display_id)
    )


@router.get("/runs/{run_id}/governance")
def get_run_governance(run_id: str):
    return _data(_invoke(get_service().get_run_governance, run_id))


@router.get("/runs/{run_id}/risks")
def get_run_risks(run_id: str):
    return _data(_invoke(get_service().get_run_risks, run_id))


@router.get(
    "/runs/{run_id}/visualization/manifest",
    response_class=Response,
)
def get_run_visualization_manifest(run_id: str):
    asset = _invoke(
        get_service().get_run_visualization,
        run_id,
    )
    return Response(
        content=asset["manifest_bytes"],
        media_type="application/json; charset=utf-8",
        headers={
            "ETag": f'"{asset["manifest_sha256"]}"',
            "X-Content-SHA256": asset["manifest_sha256"],
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get(
    "/runs/{run_id}/visualization/frames",
    response_class=Response,
)
def get_run_visualization_frames(run_id: str):
    asset = _invoke(
        get_service().get_run_visualization,
        run_id,
    )
    return Response(
        content=asset["frames"],
        media_type="application/octet-stream",
        headers={
            "ETag": f'"{asset["binary_sha256"]}"',
            "X-Content-SHA256": asset["binary_sha256"],
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get(
    "/runs/{run_id}/forum-twin/manifest",
    response_class=Response,
)
def get_run_forum_twin_manifest(run_id: str):
    asset = _invoke(get_service().get_forum_twin_manifest, run_id)
    return Response(
        content=asset["manifest_bytes"],
        media_type="application/json; charset=utf-8",
        headers={
            "ETag": f'"{asset["manifest_sha256"]}"',
            "X-Content-SHA256": asset["manifest_sha256"],
            "X-Blob-SHA256": asset["content_sha256"],
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/runs/{run_id}/forum-twin/threads")
def list_run_forum_twin_threads(
    run_id: str,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    scenario_id: str | None = None,
    branch: str | None = None,
):
    return _data(
        _invoke(
            get_service().list_forum_twin_threads,
            run_id,
            offset=offset,
            limit=limit,
            scenario_id=scenario_id,
            branch=branch,
        )
    )


@router.get("/runs/{run_id}/forum-twin/threads/{thread_id}")
def get_run_forum_twin_thread(run_id: str, thread_id: str):
    return _data(
        _invoke(get_service().get_forum_twin_thread, run_id, thread_id)
    )


@router.get("/runs/{run_id}/forum-twin/claims/{claim_id}")
def get_run_forum_twin_claim(run_id: str, claim_id: str):
    return _data(
        _invoke(get_service().get_forum_twin_claim, run_id, claim_id)
    )


@router.get("/runs/{run_id}/forum-twin/agents/{display_id}")
def get_run_forum_twin_display_profile(run_id: str, display_id: str):
    return _data(
        _invoke(
            get_service().get_forum_twin_display_profile,
            run_id,
            display_id,
        )
    )


def _forum_twin_tick_asset_response(
    run_id: str,
    asset_kind: Literal["particle_frame", "propagation_matrix"],
    scenario_id: str,
    branch: str,
    tick: int,
) -> Response:
    asset = _invoke(
        get_service().get_forum_twin_tick_asset,
        run_id,
        asset_kind=asset_kind,
        scenario_id=scenario_id,
        branch=branch,
        tick=tick,
    )
    return Response(
        content=asset["content"],
        media_type=asset["mime_type"],
        headers={
            "ETag": f'"{asset["content_sha256"]}"',
            "X-Content-SHA256": asset["content_sha256"],
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/runs/{run_id}/forum-twin/particle-frame", response_class=Response)
def get_run_forum_twin_particle_frame(
    run_id: str,
    scenario_id: str,
    branch: str,
    tick: Annotated[int, Query(ge=0, le=23)],
):
    return _forum_twin_tick_asset_response(
        run_id, "particle_frame", scenario_id, branch, tick
    )


@router.get(
    "/runs/{run_id}/forum-twin/propagation-matrix",
    response_class=Response,
)
def get_run_forum_twin_propagation_matrix(
    run_id: str,
    scenario_id: str,
    branch: str,
    tick: Annotated[int, Query(ge=0, le=23)],
):
    return _forum_twin_tick_asset_response(
        run_id, "propagation_matrix", scenario_id, branch, tick
    )


def _aggregate_report_response(
    run_id: str,
    report_format: Literal["json", "html"],
) -> Response:
    artifacts = _invoke(
        get_service().get_live_run_report_artifacts,
        run_id,
        external_download=True,
    )
    if report_format == "json":
        content = artifacts.json_bytes
        digest = artifacts.json_sha256
        media_type = "application/json; charset=utf-8"
    else:
        content = artifacts.html_bytes
        digest = artifacts.html_sha256
        media_type = "text/html; charset=utf-8"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="campus-pulse-{run_id}.'
                f'{report_format}"'
            ),
            "X-Content-SHA256": digest,
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/runs/{run_id}/reports/aggregate.json",
    response_class=Response,
)
def download_run_aggregate_json_report(run_id: str):
    return _aggregate_report_response(run_id, "json")


@router.get(
    "/runs/{run_id}/reports/aggregate.html",
    response_class=Response,
)
def download_run_aggregate_html_report(run_id: str):
    return _aggregate_report_response(run_id, "html")


@router.post("/runs/{run_id}/cancel")
def cancel_run_runtime(run_id: str, payload: RunCancel):
    return _data(
        _invoke(
            get_service().cancel_run_runtime,
            run_id,
            **payload.model_dump(),
        )
    )
