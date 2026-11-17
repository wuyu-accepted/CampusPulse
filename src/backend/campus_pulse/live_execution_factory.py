"""Snapshot-qualified live execution context construction for P3.2b.

The worker hands this module an immutable lease identity.  Context
qualification then reads exactly one SQLite snapshot containing the frozen
run-plan-v4, schema-v7 fixture runtime/usage rows, reviewed policy bindings and
the four requalified asset BLOBs.  Population construction happens only from
those copied, reviewed documents.

This module deliberately has no environment, path, clock, credential or
network integration.  The authorized path reads only the already-consumed
grant identity and caps.  A keyed deterministic transport must be injected by
the operator-side fixture harness and its complete script fingerprint must
already be bound to the materialized runtime.
"""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Callable, Mapping

from .executors.live_llm import (
    DeterministicLiveRuntimeExecutor,
    LiveExecutionContext,
)
from .live_asset_catalog import (
    PERSONA_EVIDENCE_ROLE,
    PERSONA_RELEASE_ROLE,
    SHOCK_BENCHMARK_ROLE,
    SHOCK_DEFINITIONS_ROLE,
    LiveAssetCatalogError,
    QualifiedLiveAssetBundle,
)
from .live_authorized_guard import AuthorizedLiveClaimIdentity
from .live_runtime import (
    LIVE_AUTHORIZED_ADAPTER_MODE,
    LIVE_AUTHORIZED_EXECUTION_PROVENANCE,
    LIVE_FIXTURE_ADAPTER_MODE,
    LIVE_FIXTURE_EXECUTION_PROVENANCE,
    LIVE_PHASE_ORDER,
    LIVE_USAGE_SCHEMA_VERSION,
    SHARED_BASELINE_BRANCH_ID,
)
from .live_runtime_contract import get_reviewed_provider_profile
from .live_runtime_worker import LiveFixtureClaimIdentity
from .live_sqlite_repository import (
    ClaimedAuthorizedLiveExecutionIdentity,
    ClaimedLiveExecutionIdentity,
    SQLiteLiveExecutionRepository,
)
from .live_transport import DeterministicTransport
from .store import StoreConflictError, WorkbenchStore
from src.envs.campus_pulse_governance.llm_agents import (
    LLMAgentError,
    construct_reviewed_population,
)
from src.envs.campus_pulse_governance.policies import POLICIES, Policy


_ACTIVE_CLAIM_STATUSES = ("leased", "running", "cancel_requested")
_FIXTURE_AUTHORIZATION_FIELDS = (
    "authorization_id",
    "authorization_fingerprint",
    "authorization_provider_call_cap",
    "authorization_token_cap",
)


class LiveExecutionFactoryError(ValueError):
    """Controlled failure before a fixture executor can be returned."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "live_execution_context_invalid",
    ) -> None:
        super().__init__(message)
        self.code = code


def _decode_qualified_document(
    bundle: QualifiedLiveAssetBundle,
    role: str,
) -> dict[str, Any]:
    """Decode one already-byte-qualified BLOB without any fallback source."""

    try:
        value = json.loads(
            bundle.bytes_for_role(role),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (
        LiveAssetCatalogError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as exc:
        raise LiveExecutionFactoryError(
            "reviewed live asset document could not be reconstructed",
            code="live_execution_asset_document_invalid",
        ) from exc
    if not isinstance(value, dict):
        raise LiveExecutionFactoryError(
            "reviewed live asset document is not an object",
            code="live_execution_asset_document_invalid",
        )
    return value


def _reject_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate reviewed asset key")
        value[key] = item
    return value


def _claim_repository_identity(
    identity: LiveFixtureClaimIdentity,
) -> ClaimedLiveExecutionIdentity:
    if not isinstance(identity, LiveFixtureClaimIdentity):
        raise LiveExecutionFactoryError(
            "fixture claim identity has the wrong contract",
            code="live_execution_claim_identity_invalid",
        )
    return ClaimedLiveExecutionIdentity(
        tenant_id=identity.tenant_id,
        project_id=identity.project_id,
        run_id=identity.run_id,
        launch_fingerprint=identity.launch_fingerprint,
        transport_config_fingerprint=(
            identity.transport_config_fingerprint
        ),
        adapter_mode=identity.adapter_mode,
        lease_id=identity.lease_id,
        lease_generation=identity.lease_generation,
    )


def _claim_authorized_repository_identity(
    identity: AuthorizedLiveClaimIdentity,
) -> ClaimedAuthorizedLiveExecutionIdentity:
    if not isinstance(identity, AuthorizedLiveClaimIdentity):
        raise LiveExecutionFactoryError(
            "authorized claim identity has the wrong contract",
            code="live_execution_claim_identity_invalid",
        )
    return ClaimedAuthorizedLiveExecutionIdentity(
        tenant_id=identity.tenant_id,
        project_id=identity.project_id,
        run_id=identity.run_id,
        launch_fingerprint=identity.launch_fingerprint,
        transport_config_fingerprint=(
            identity.transport_config_fingerprint
        ),
        adapter_mode=identity.adapter_mode,
        lease_id=identity.lease_id,
        lease_generation=identity.lease_generation,
        authorization_id=identity.authorization_id,
        authorization_fingerprint=identity.authorization_fingerprint,
    )


def _require_claimed_fixture_row(
    store: WorkbenchStore,
    connection: Any,
    identity: LiveFixtureClaimIdentity,
    run: Mapping[str, Any],
    extension: Mapping[str, Any],
) -> None:
    """Bind plan, runtime and usage without consulting authorization state."""

    row = connection.execute(
        """
        SELECT
            rr.status, rr.launch_contract_json, rr.launch_fingerprint,
            rr.adapter_mode, rr.input_fingerprint,
            rr.token_limit AS runtime_token_limit,
            rr.effective_concurrency_limit AS runtime_concurrency_limit,
            rr.turn_limit, rr.lease_id, rr.lease_generation,
            rr.lease_expires_at,
            u.launch_fingerprint AS usage_launch_fingerprint,
            u.input_fingerprint AS usage_input_fingerprint,
            u.extension_fingerprint, u.usage_schema_version,
            u.execution_provenance, u.transport_config_fingerprint,
            u.expected_agent_turns,
            u.effective_concurrency_limit AS usage_concurrency_limit,
            u.plan_provider_call_limit, u.effective_provider_call_limit,
            u.plan_token_limit, u.effective_token_limit,
            u.authorization_id, u.authorization_fingerprint,
            u.authorization_provider_call_cap, u.authorization_token_cap,
            strftime('%Y-%m-%dT%H:%M:%fZ', 'now') AS database_now
        FROM run_runtime_states rr
        JOIN run_live_usage u
          ON u.tenant_id = rr.tenant_id
         AND u.project_id = rr.project_id
         AND u.run_id = rr.run_id
         AND u.launch_fingerprint = rr.launch_fingerprint
        WHERE rr.tenant_id = ?
          AND rr.project_id = ?
          AND rr.run_id = ?
          AND rr.launch_fingerprint = ?
          AND rr.adapter_mode = ?
          AND rr.lease_id = ?
          AND rr.lease_generation = ?
          AND u.transport_config_fingerprint = ?
        """,
        (
            identity.tenant_id,
            identity.project_id,
            identity.run_id,
            identity.launch_fingerprint,
            identity.adapter_mode,
            identity.lease_id,
            identity.lease_generation,
            identity.transport_config_fingerprint,
        ),
    ).fetchone()
    if (
        row is None
        or row["status"] not in _ACTIVE_CLAIM_STATUSES
        or row["lease_expires_at"] is None
        or row["lease_expires_at"] <= row["database_now"]
    ):
        raise LiveExecutionFactoryError(
            "fixture claim is absent, stale or outside its active lease",
            code="live_execution_claim_stale",
        )

    expected_launch = store._build_live_fixture_launch(extension)
    expected_launch_json = store._canonical_json(expected_launch)
    expected_turns = extension["expected_agent_turns"]
    expected_workers = extension["effective_max_workers"]
    expected_input = extension["input_fingerprint"]
    if (
        identity.adapter_mode != LIVE_FIXTURE_ADAPTER_MODE
        or row["launch_contract_json"] != expected_launch_json
        or row["launch_fingerprint"] != expected_launch["launch_fingerprint"]
        or row["usage_launch_fingerprint"]
        != expected_launch["launch_fingerprint"]
        or row["input_fingerprint"] != expected_input
        or row["usage_input_fingerprint"] != expected_input
        or row["extension_fingerprint"]
        != extension["extension_fingerprint"]
        or row["usage_schema_version"] != LIVE_USAGE_SCHEMA_VERSION
        or row["execution_provenance"]
        != LIVE_FIXTURE_EXECUTION_PROVENANCE
        or row["runtime_token_limit"] != extension["token_limit"]
        or row["runtime_concurrency_limit"] != expected_workers
        or row["usage_concurrency_limit"] != expected_workers
        or row["turn_limit"] != expected_turns
        or row["expected_agent_turns"] != expected_turns
        or row["plan_provider_call_limit"]
        != extension["provider_call_limit"]
        or row["effective_provider_call_limit"]
        != extension["provider_call_limit"]
        or row["plan_token_limit"] != extension["token_limit"]
        or row["effective_token_limit"] != extension["token_limit"]
        or any(row[field] is not None for field in _FIXTURE_AUTHORIZATION_FIELDS)
        or run.get("run_id") != identity.run_id
    ):
        raise LiveExecutionFactoryError(
            "schema-v7 fixture runtime conflicts with its frozen run plan",
            code="live_execution_runtime_identity_mismatch",
        )


def _require_claimed_authorized_row(
    store: WorkbenchStore,
    connection: Any,
    identity: AuthorizedLiveClaimIdentity,
    run: Mapping[str, Any],
    extension: Mapping[str, Any],
) -> None:
    """Bind one current lease to its exact already-consumed grant."""

    row = connection.execute(
        """
        SELECT
            rr.status, rr.launch_contract_json, rr.launch_fingerprint,
            rr.adapter_mode, rr.input_fingerprint,
            rr.token_limit AS runtime_token_limit,
            rr.effective_concurrency_limit AS runtime_concurrency_limit,
            rr.turn_limit, rr.lease_id, rr.lease_generation,
            rr.lease_expires_at,
            u.launch_fingerprint AS usage_launch_fingerprint,
            u.input_fingerprint AS usage_input_fingerprint,
            u.extension_fingerprint, u.usage_schema_version,
            u.execution_provenance, u.transport_config_fingerprint,
            u.expected_agent_turns,
            u.effective_concurrency_limit AS usage_concurrency_limit,
            u.plan_provider_call_limit, u.effective_provider_call_limit,
            u.plan_token_limit, u.effective_token_limit,
            u.authorization_id, u.authorization_fingerprint,
            u.authorization_provider_call_cap, u.authorization_token_cap,
            a.provider_call_cap, a.token_cap, a.consumed_at,
            a.consumed_runtime_fingerprint,
            a.consumed_plan_fingerprint,
            strftime('%Y-%m-%dT%H:%M:%fZ', 'now') AS database_now
        FROM run_runtime_states rr
        JOIN run_live_usage u
          ON u.tenant_id = rr.tenant_id
         AND u.project_id = rr.project_id
         AND u.run_id = rr.run_id
         AND u.launch_fingerprint = rr.launch_fingerprint
        JOIN run_live_authorizations a
          ON a.tenant_id = u.tenant_id
         AND a.project_id = u.project_id
         AND a.run_id = u.run_id
         AND a.authorization_id = u.authorization_id
         AND a.authorization_fingerprint = u.authorization_fingerprint
        WHERE rr.tenant_id = ?
          AND rr.project_id = ?
          AND rr.run_id = ?
          AND rr.launch_fingerprint = ?
          AND rr.adapter_mode = ?
          AND rr.lease_id = ?
          AND rr.lease_generation = ?
          AND u.transport_config_fingerprint = ?
        """,
        (
            identity.tenant_id,
            identity.project_id,
            identity.run_id,
            identity.launch_fingerprint,
            identity.adapter_mode,
            identity.lease_id,
            identity.lease_generation,
            identity.transport_config_fingerprint,
        ),
    ).fetchone()
    if (
        row is None
        or row["status"] not in _ACTIVE_CLAIM_STATUSES
        or row["lease_expires_at"] is None
        or row["lease_expires_at"] <= row["database_now"]
    ):
        raise LiveExecutionFactoryError(
            "authorized claim is absent, stale or outside its active lease",
            code="live_execution_claim_stale",
        )

    expected_launch = store._build_live_authorized_launch(extension)
    expected_turns = extension["expected_agent_turns"]
    expected_workers = extension["effective_max_workers"]
    if (
        identity.adapter_mode != LIVE_AUTHORIZED_ADAPTER_MODE
        or row["launch_contract_json"]
        != store._canonical_json(expected_launch)
        or row["launch_fingerprint"]
        != expected_launch["launch_fingerprint"]
        or row["usage_launch_fingerprint"]
        != expected_launch["launch_fingerprint"]
        or row["input_fingerprint"] != extension["input_fingerprint"]
        or row["usage_input_fingerprint"]
        != extension["input_fingerprint"]
        or row["extension_fingerprint"]
        != extension["extension_fingerprint"]
        or row["usage_schema_version"] != LIVE_USAGE_SCHEMA_VERSION
        or row["execution_provenance"]
        != LIVE_AUTHORIZED_EXECUTION_PROVENANCE
        or row["runtime_token_limit"] != extension["token_limit"]
        or row["runtime_concurrency_limit"] != expected_workers
        or row["usage_concurrency_limit"] != expected_workers
        or row["turn_limit"] != expected_turns
        or row["expected_agent_turns"] != expected_turns
        or row["plan_provider_call_limit"]
        != extension["provider_call_limit"]
        or row["plan_token_limit"] != extension["token_limit"]
        or row["authorization_id"] != identity.authorization_id
        or row["authorization_fingerprint"]
        != identity.authorization_fingerprint
        or row["authorization_provider_call_cap"]
        != row["provider_call_cap"]
        or row["authorization_token_cap"] != row["token_cap"]
        or row["effective_provider_call_limit"]
        != min(extension["provider_call_limit"], row["provider_call_cap"])
        or row["effective_token_limit"]
        != min(extension["token_limit"], row["token_cap"])
        or row["consumed_at"] is None
        or row["consumed_runtime_fingerprint"]
        != identity.launch_fingerprint
        or row["consumed_plan_fingerprint"]
        != extension["input_fingerprint"]
        or run.get("run_id") != identity.run_id
    ):
        raise LiveExecutionFactoryError(
            "schema-v7 authorized runtime conflicts with its consumed grant",
            code="live_execution_runtime_identity_mismatch",
        )


def _require_bundle_identity(
    bundle: QualifiedLiveAssetBundle,
    extension: Mapping[str, Any],
) -> None:
    manifest = bundle.manifest
    population = manifest.get("population")
    if (
        manifest.get("bundle_id") != extension["asset_bundle_id"]
        or manifest.get("bundle_fingerprint")
        != extension["asset_bundle_fingerprint"]
        or not isinstance(population, Mapping)
        or population.get("population_release_fingerprint")
        != extension["population_release_fingerprint"]
        or population.get("population_evidence_fingerprint")
        != extension["population_evidence_fingerprint"]
    ):
        raise LiveExecutionFactoryError(
            "requalified asset BLOBs conflict with the frozen run plan",
            code="live_execution_asset_identity_mismatch",
        )


def _select_unique_scenario(
    document: Mapping[str, Any],
    scenario_id: str,
    *,
    document_name: str,
) -> dict[str, Any]:
    events = document.get("events")
    if not isinstance(events, list):
        raise LiveExecutionFactoryError(
            f"{document_name} does not contain reviewed events",
            code="live_execution_scenario_invalid",
        )
    matches = [
        item
        for item in events
        if isinstance(item, Mapping)
        and item.get("scenario_id") == scenario_id
    ]
    if len(matches) != 1:
        raise LiveExecutionFactoryError(
            f"{document_name} does not uniquely bind the reviewed scenario",
            code="live_execution_scenario_invalid",
        )
    return deepcopy(dict(matches[0]))


def _build_policies(
    policy_bindings: list[dict[str, str]],
) -> dict[str, Policy]:
    policies: dict[str, Policy] = {
        SHARED_BASELINE_BRANCH_ID: POLICIES["baseline"],
    }
    for binding in policy_bindings:
        engine_policy_id = binding["engine_policy_id"]
        policy = POLICIES.get(engine_policy_id)
        if (
            policy is None
            or policy.policy_id != engine_policy_id
            or engine_policy_id in policies
        ):
            raise LiveExecutionFactoryError(
                "reviewed policy binding is unsupported by the engine",
                code="live_execution_policy_invalid",
            )
        policies[engine_policy_id] = policy
    return policies


def _build_context_from_snapshot(
    *,
    identity: LiveFixtureClaimIdentity | AuthorizedLiveClaimIdentity,
    run: Mapping[str, Any],
    extension: Mapping[str, Any],
    policy_bindings: list[dict[str, str]],
    bundle: QualifiedLiveAssetBundle,
    cache_namespace: str = LIVE_FIXTURE_EXECUTION_PROVENANCE,
) -> LiveExecutionContext:
    snapshot = run.get("input_snapshot")
    live_runtime = (
        snapshot.get("live_runtime")
        if isinstance(snapshot, Mapping)
        else None
    )
    mapping = (
        live_runtime.get("template_mapping")
        if isinstance(live_runtime, Mapping)
        else None
    )
    provider_profile = (
        live_runtime.get("provider_profile")
        if isinstance(live_runtime, Mapping)
        else None
    )
    if (
        not isinstance(mapping, Mapping)
        or not isinstance(provider_profile, Mapping)
        or dict(provider_profile)
        != get_reviewed_provider_profile(internal=True)
    ):
        raise LiveExecutionFactoryError(
            "frozen engine mapping or provider profile is invalid",
            code="live_execution_plan_projection_invalid",
        )
    engine_scenario_id = mapping.get("engine_scenario_id")
    if not isinstance(engine_scenario_id, str):
        raise LiveExecutionFactoryError(
            "frozen engine scenario is unavailable",
            code="live_execution_scenario_invalid",
        )

    persona_release = _decode_qualified_document(
        bundle,
        PERSONA_RELEASE_ROLE,
    )
    persona_evidence = _decode_qualified_document(
        bundle,
        PERSONA_EVIDENCE_ROLE,
    )
    benchmark = _decode_qualified_document(
        bundle,
        SHOCK_BENCHMARK_ROLE,
    )
    definitions = _decode_qualified_document(
        bundle,
        SHOCK_DEFINITIONS_ROLE,
    )
    event = _select_unique_scenario(
        benchmark,
        engine_scenario_id,
        document_name="reviewed shock benchmark",
    )
    definition = _select_unique_scenario(
        definitions,
        engine_scenario_id,
        document_name="reviewed shock definitions",
    )
    event_phases = event.get("phases")
    if (
        not isinstance(event_phases, list)
        or tuple(
            phase.get("phase_id")
            for phase in event_phases
            if isinstance(phase, Mapping)
        )
        != LIVE_PHASE_ORDER
    ):
        raise LiveExecutionFactoryError(
            "reviewed event phase order is invalid",
            code="live_execution_phase_invalid",
        )
    phase_map = {
        str(phase["phase_id"]): deepcopy(dict(phase))
        for phase in event_phases
    }

    agent_count = extension["agent_count"]
    seeds = run.get("seeds")
    if (
        not isinstance(seeds, list)
        or len(seeds) != extension["seed_count"]
        or any(isinstance(seed, bool) or not isinstance(seed, int) for seed in seeds)
        or len(set(seeds)) != len(seeds)
    ):
        raise LiveExecutionFactoryError(
            "frozen population seed projection is invalid",
            code="live_execution_population_invalid",
        )
    populations = {}
    try:
        for seed in seeds:
            agents, metadata = construct_reviewed_population(
                definition,
                persona_release,
                persona_evidence,
                agent_count=agent_count,
                seed=seed,
            )
            if (
                len(agents) != agent_count
                or len({agent.agent_id for agent in agents}) != agent_count
                or sum(metadata["archetype_agent_counts"].values())
                != agent_count
                or any(
                    not agent.archetype_id
                    or not agent.persona_variant_id
                    or not isinstance(agent.persona, dict)
                    or not agent.persona
                    for agent in agents
                )
            ):
                raise LiveExecutionFactoryError(
                    "reviewed population construction did not conserve agents",
                    code="live_execution_population_invalid",
                )
            populations[seed] = tuple(agents)
    except LiveExecutionFactoryError:
        raise
    except (KeyError, TypeError, ValueError, LLMAgentError) as exc:
        raise LiveExecutionFactoryError(
            "reviewed population construction failed",
            code="live_execution_population_invalid",
        ) from exc

    policies = _build_policies(policy_bindings)
    if (
        len(policy_bindings) != extension["policy_count"]
        or len(policies) != extension["policy_count"] + 1
    ):
        raise LiveExecutionFactoryError(
            "reviewed policy cardinality conflicts with the frozen plan",
            code="live_execution_policy_invalid",
        )
    internal_profile = dict(provider_profile)
    return LiveExecutionContext(
        run_id=identity.run_id,
        launch_id=identity.launch_fingerprint,
        event=event,
        definition=definition,
        phases=phase_map,
        policies=policies,
        initial_populations=populations,
        model=internal_profile["requested_model"],
        reasoning_effort=internal_profile["reasoning_effort"],
        max_output_tokens=internal_profile["maximum_output_tokens"],
        max_workers=extension["effective_max_workers"],
        transport_config_fingerprint=(
            identity.transport_config_fingerprint
        ),
        network_retry_limit=internal_profile["network_retry_limit"],
        format_repair_limit=internal_profile["format_repair_limit"],
        cache_namespace=cache_namespace,
    )


def build_live_fixture_execution_context(
    store: WorkbenchStore,
    identity: LiveFixtureClaimIdentity,
) -> LiveExecutionContext:
    """Construct one lease-bound context from one SQLite read snapshot."""

    if not isinstance(store, WorkbenchStore):
        raise LiveExecutionFactoryError(
            "execution context requires a WorkbenchStore",
            code="live_execution_store_invalid",
        )
    _claim_repository_identity(identity)
    try:
        with store._connection() as connection:
            connection.execute("BEGIN")
            try:
                run, extension = (
                    store._load_live_fixture_plan_from_connection(
                        connection,
                        identity.tenant_id,
                        identity.project_id,
                        identity.run_id,
                    )
                )
                bundle = (
                    store._load_qualified_live_asset_bundle_from_connection(
                        connection
                    )
                )
                policy_bindings = (
                    store._resolve_live_fixture_policy_bindings(
                        connection,
                        run,
                    )
                )
                _require_claimed_fixture_row(
                    store,
                    connection,
                    identity,
                    run,
                    extension,
                )
                _require_bundle_identity(bundle, extension)
            finally:
                connection.rollback()
    except LiveExecutionFactoryError:
        raise
    except (StoreConflictError, TypeError, ValueError) as exc:
        raise LiveExecutionFactoryError(
            "frozen fixture snapshot failed qualification",
            code="live_execution_snapshot_invalid",
        ) from exc

    return _build_context_from_snapshot(
        identity=identity,
        run=deepcopy(run),
        extension=deepcopy(extension),
        policy_bindings=deepcopy(policy_bindings),
        bundle=bundle,
        cache_namespace=LIVE_FIXTURE_EXECUTION_PROVENANCE,
    )


def build_live_authorized_execution_context(
    store: WorkbenchStore,
    identity: AuthorizedLiveClaimIdentity,
) -> LiveExecutionContext:
    """Reconstruct a consumed-grant context without provider capability."""

    if not isinstance(store, WorkbenchStore):
        raise LiveExecutionFactoryError(
            "execution context requires a WorkbenchStore",
            code="live_execution_store_invalid",
        )
    _claim_authorized_repository_identity(identity)
    try:
        with store._connection() as connection:
            connection.execute("BEGIN")
            try:
                run, extension = (
                    store._load_live_fixture_plan_from_connection(
                        connection,
                        identity.tenant_id,
                        identity.project_id,
                        identity.run_id,
                    )
                )
                bundle = (
                    store._load_qualified_live_asset_bundle_from_connection(
                        connection
                    )
                )
                policy_bindings = (
                    store._resolve_live_fixture_policy_bindings(
                        connection,
                        run,
                    )
                )
                _require_claimed_authorized_row(
                    store,
                    connection,
                    identity,
                    run,
                    extension,
                )
                _require_bundle_identity(bundle, extension)
            finally:
                connection.rollback()
    except LiveExecutionFactoryError:
        raise
    except (StoreConflictError, TypeError, ValueError) as exc:
        raise LiveExecutionFactoryError(
            "frozen authorized snapshot failed qualification",
            code="live_execution_snapshot_invalid",
        ) from exc

    return _build_context_from_snapshot(
        identity=identity,
        run=deepcopy(run),
        extension=deepcopy(extension),
        policy_bindings=deepcopy(policy_bindings),
        bundle=bundle,
        cache_namespace=LIVE_AUTHORIZED_EXECUTION_PROVENANCE,
    )


class LiveFixtureExecutorFactory:
    """Callable worker factory with an injected, fingerprint-bound script."""

    def __init__(
        self,
        store: WorkbenchStore,
        transport_factory: Callable[
            [LiveFixtureClaimIdentity],
            DeterministicTransport,
        ],
    ) -> None:
        if not isinstance(store, WorkbenchStore):
            raise LiveExecutionFactoryError(
                "executor factory requires a WorkbenchStore",
                code="live_execution_store_invalid",
            )
        if not callable(transport_factory):
            raise LiveExecutionFactoryError(
                "deterministic transport factory must be callable",
                code="live_execution_transport_invalid",
            )
        self._store = store
        self._transport_factory = transport_factory

    def __call__(
        self,
        identity: LiveFixtureClaimIdentity,
    ) -> DeterministicLiveRuntimeExecutor:
        repository_identity = _claim_repository_identity(identity)
        context = build_live_fixture_execution_context(
            self._store,
            identity,
        )
        transport = self._transport_factory(identity)
        if (
            not isinstance(transport, DeterministicTransport)
            or transport.mode != "keyed"
            or transport.script_fingerprint
            != identity.transport_config_fingerprint
        ):
            raise LiveExecutionFactoryError(
                "injected keyed script differs from the materialized runtime",
                code="live_execution_transport_identity_mismatch",
            )
        repository = SQLiteLiveExecutionRepository(
            self._store,
            repository_identity,
            context,
        )
        return DeterministicLiveRuntimeExecutor(repository, transport)
