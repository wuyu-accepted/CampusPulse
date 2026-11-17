"""Provenance-bound SQLite durability adapter for the live executor protocol.

The adapter is intentionally run scoped.  It accepts an immutable claim and
an already-qualified execution context, then binds every SQL statement to the
same tenant, project, run, launch, adapter, transport and lease generation.
For authorized-live runs it validates only the already-consumed authorization
row and its integer caps; it never reads a credential, environment variable,
file or network resource, and never stores a rendered prompt or provider
response body.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, fields
import hashlib
import json
import math
import re
import sqlite3
from typing import Any, Iterator, Mapping, Sequence
import unicodedata

from .executors.live_llm import (
    AttemptRequestIdentity,
    AttemptSettlement,
    FORMAT_REPAIR_REASON_CODE,
    LiveExecutionContext,
    LiveExecutionError,
    ReadyLivePhase,
    TurnAcquisition,
    build_attempt_request_identities,
    build_deterministic_transport_request,
)
from .live_runtime import (
    LIVE_AUTHORIZED_ADAPTER_MODE,
    LIVE_AUTHORIZED_EXECUTION_PROVENANCE,
    LIVE_FIXTURE_ADAPTER_MODE,
    LIVE_FIXTURE_EXECUTION_PROVENANCE,
    LIVE_PHASE_ORDER,
)
from .live_runtime_contract import (
    LIVE_CACHE_ALGORITHM_VERSION,
    LIVE_CHECKPOINT_ALGORITHM_VERSION,
    get_reviewed_provider_profile,
)
from .live_transport import (
    DETERMINISTIC_TRANSPORT_ID,
    canonicalize_transport_request,
)
from .store import WorkbenchStore
from src.envs.campus_pulse_governance.llm_agents import (
    AgentDecision,
    AgentState,
    ModelTurn,
    render_agent_turn,
    render_format_repair_turn,
    transition_agent_state,
    validate_rendered_turn,
)


LIVE_ATTEMPT_SCHEMA_VERSION = "campus-pulse-live-attempt-v1"
LIVE_CACHE_SCHEMA_VERSION = LIVE_CACHE_ALGORITHM_VERSION
LIVE_CHECKPOINT_SCHEMA_VERSION = LIVE_CHECKPOINT_ALGORITHM_VERSION

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")
_PHASE_INDEX = {value: index for index, value in enumerate(LIVE_PHASE_ORDER)}
_SLOT_FIELDS = (
    "slot_schema_version",
    "run_id",
    "input_fingerprint",
    "extension_fingerprint",
    "ordinal",
    "seed",
    "branch_id",
    "workbench_policy_id",
    "workbench_policy_template_key",
    "engine_policy_id",
    "phase_id",
    "agent_index",
    "state_input_kind",
    "public_input_kind",
    "state_parent_ordinal",
    "required_checkpoint_ordinal",
    "slot_fingerprint",
)
_AGENT_FIELDS = {item.name for item in fields(AgentState)}
_FORBIDDEN_JSON_KEY_PARTS = {
    "agentprompt",
    "apikey",
    "authorization",
    "baseurl",
    "credential",
    "headers",
    "password",
    "prompt",
    "providerbody",
    "requestbody",
    "responsebody",
    "secret",
    "systemprompt",
    "utterance",
    "rawtext",
    "rawrecord",
    "forumrecord",
    "sourceid",
    "sourceidentifier",
    "traceback",
    "stacktrace",
    "localpath",
    "filepath",
    "accesstoken",
    "refreshtoken",
    "bearertoken",
    "authheader",
}
_ALLOWED_DURABLE_MESSAGE_KEYS = {
    "message",
    "syntheticmessage",
    "messagememory",
    "representativesyntheticmessages",
    "canonicalmessage",
    "parentsyntheticmessage",
    "rootsyntheticmessage",
    "ownpreviouspublicmessages",
    "priormessages",
}
_ALLOWED_DURABLE_PROMPT_KEYS = {"prompttokens"}
_RAW_CONTENT_KEY_PARTS = {
    "comment",
    "content",
    "message",
    "post",
    "record",
    "text",
    "utterance",
}
_MAX_DURABLE_JSON_BYTES = 4_000_000


class LiveSQLiteRepositoryError(LiveExecutionError):
    """Controlled fail-closed error at the SQLite repository boundary."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "live_sqlite_repository_conflict",
    ) -> None:
        super().__init__(message, code=code)


@dataclass(frozen=True)
class ClaimedLiveExecutionIdentity:
    """The complete immutable identity of one claimed live execution."""

    tenant_id: str
    project_id: str
    run_id: str
    launch_fingerprint: str
    transport_config_fingerprint: str
    adapter_mode: str
    lease_id: str
    lease_generation: int


@dataclass(frozen=True)
class ClaimedAuthorizedLiveExecutionIdentity(
    ClaimedLiveExecutionIdentity
):
    """A claimed authorized execution plus its consumed grant identity."""

    authorization_id: str
    authorization_fingerprint: str


@dataclass(frozen=True)
class _RenderedIdentity:
    request_sha256: str
    cache_key: str
    prompt_fingerprint: str
    base_prompt_fingerprint: str
    format_repair_index: int
    agent_state_fingerprint: str
    prior_public_fingerprint: str
    agent: AgentState
    previous_public: Mapping[str, Any] | None
    base_rendered: Any
    rendered: Any


def _canonical_json(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LiveSQLiteRepositoryError(
            "durable execution value is not strict canonical JSON",
            code="live_sqlite_json_invalid",
        ) from exc
    if len(encoded.encode("utf-8")) > _MAX_DURABLE_JSON_BYTES:
        raise LiveSQLiteRepositoryError(
            "durable execution JSON exceeds the repository bound",
            code="live_sqlite_json_too_large",
        )
    return encoded


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise LiveSQLiteRepositoryError(
            f"{field} is not a lowercase SHA-256",
            code="live_sqlite_identity_invalid",
        )
    return value


def _require_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise LiveSQLiteRepositoryError(
            f"{field} is not a controlled identifier",
            code="live_sqlite_identity_invalid",
        )
    return value


def _strict_integer(
    value: Any,
    field: str,
    *,
    minimum: int = 0,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
    ):
        raise LiveSQLiteRepositoryError(
            f"{field} is not a controlled integer",
            code="live_sqlite_integer_invalid",
        )
    return value


def _normalized_json_key(value: str) -> str:
    compatible = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^a-z0-9]", "", compatible)


def _json_key_contains_private_material(normalized: str) -> bool:
    if normalized in _ALLOWED_DURABLE_MESSAGE_KEYS:
        return False
    if normalized in _ALLOWED_DURABLE_PROMPT_KEYS:
        return False
    if any(
        part in normalized for part in _FORBIDDEN_JSON_KEY_PARTS
    ):
        return True
    if "message" in normalized:
        return True
    if "prompt" in normalized:
        return True
    if normalized.startswith("raw") and any(
        part in normalized for part in _RAW_CONTENT_KEY_PARTS
    ):
        return True
    return (
        normalized.startswith("source")
        and (
            "identifier" in normalized
            or normalized.endswith("id")
            or normalized.endswith("ids")
        )
    )


def _scan_safe_json(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise LiveSQLiteRepositoryError(
                    "durable JSON keys must be strings",
                    code="live_sqlite_json_invalid",
                )
            normalized = _normalized_json_key(key)
            if _json_key_contains_private_material(normalized):
                raise LiveSQLiteRepositoryError(
                    "raw source, prompt, provider body or credential "
                    "material is forbidden",
                    code="live_sqlite_private_material_forbidden",
                )
            _scan_safe_json(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _scan_safe_json(nested)
    elif isinstance(value, float) and not math.isfinite(value):
        raise LiveSQLiteRepositoryError(
            "non-finite durable values are forbidden",
            code="live_sqlite_json_invalid",
        )


def _strict_json_loads(value: Any, field: str) -> Any:
    if not isinstance(value, str):
        raise LiveSQLiteRepositoryError(
            f"{field} is not stored JSON",
            code="live_sqlite_durable_state_invalid",
        )
    try:
        decoded = json.loads(
            value,
            parse_constant=lambda _item: (_ for _ in ()).throw(ValueError()),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LiveSQLiteRepositoryError(
            f"{field} is invalid JSON",
            code="live_sqlite_durable_state_invalid",
        ) from exc
    if _canonical_json(decoded) != value:
        raise LiveSQLiteRepositoryError(
            f"{field} is not canonical JSON",
            code="live_sqlite_durable_state_invalid",
        )
    _scan_safe_json(decoded)
    return decoded


def _canonical_json_loads(value: Any, field: str) -> Any:
    """Parse canonical internal metadata without the durable-state key scan."""

    if not isinstance(value, str):
        raise LiveSQLiteRepositoryError(
            f"{field} is not stored JSON",
            code="live_sqlite_durable_state_invalid",
        )
    try:
        decoded = json.loads(
            value,
            parse_constant=lambda _item: (_ for _ in ()).throw(ValueError()),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LiveSQLiteRepositoryError(
            f"{field} is invalid JSON",
            code="live_sqlite_durable_state_invalid",
        ) from exc
    if _canonical_json(decoded) != value:
        raise LiveSQLiteRepositoryError(
            f"{field} is not canonical JSON",
            code="live_sqlite_durable_state_invalid",
        )
    return decoded


def _agent_from_payload(value: Any) -> AgentState:
    if not isinstance(value, dict) or set(value) != _AGENT_FIELDS:
        raise LiveSQLiteRepositoryError(
            "durable agent state fields are invalid",
            code="live_sqlite_agent_state_invalid",
        )
    try:
        agent = AgentState(**deepcopy(value))
    except TypeError as exc:
        raise LiveSQLiteRepositoryError(
            "durable agent state cannot be reconstructed",
            code="live_sqlite_agent_state_invalid",
        ) from exc
    if asdict(agent) != value:
        raise LiveSQLiteRepositoryError(
            "durable agent state changed during reconstruction",
            code="live_sqlite_agent_state_invalid",
        )
    return agent


class SQLiteLiveExecutionRepository:
    """Schema-v7 durable implementation of ``LiveExecutionRepository``."""

    def __init__(
        self,
        store: WorkbenchStore,
        identity: ClaimedLiveExecutionIdentity,
        context: LiveExecutionContext,
    ) -> None:
        if not isinstance(store, WorkbenchStore):
            raise LiveSQLiteRepositoryError(
                "repository requires a WorkbenchStore",
                code="live_sqlite_store_invalid",
            )
        if not isinstance(identity, ClaimedLiveExecutionIdentity):
            raise LiveSQLiteRepositoryError(
                "claimed execution identity is invalid",
                code="live_sqlite_identity_invalid",
            )
        if not isinstance(context, LiveExecutionContext):
            raise LiveSQLiteRepositoryError(
                "qualified execution context is invalid",
                code="live_sqlite_context_invalid",
            )
        self.store = store
        self.identity = identity
        self._context = deepcopy(context)
        self._validate_constructor_identity()
        with self._read_transaction() as connection:
            self._require_bound_runtime(
                connection,
                allowed_statuses=("leased", "running", "cancel_requested"),
                require_unexpired=True,
            )

    @property
    def execution_provenance(self) -> str:
        if self.identity.adapter_mode == LIVE_FIXTURE_ADAPTER_MODE:
            return LIVE_FIXTURE_EXECUTION_PROVENANCE
        return LIVE_AUTHORIZED_EXECUTION_PROVENANCE

    @property
    def transport_id(self) -> str:
        if self.identity.adapter_mode == LIVE_FIXTURE_ADAPTER_MODE:
            return DETERMINISTIC_TRANSPORT_ID
        return LIVE_AUTHORIZED_EXECUTION_PROVENANCE

    @property
    def authorization_id(self) -> str | None:
        if isinstance(
            self.identity,
            ClaimedAuthorizedLiveExecutionIdentity,
        ):
            return self.identity.authorization_id
        return None

    @property
    def authorization_fingerprint(self) -> str | None:
        if isinstance(
            self.identity,
            ClaimedAuthorizedLiveExecutionIdentity,
        ):
            return self.identity.authorization_fingerprint
        return None

    def _validate_constructor_identity(self) -> None:
        identity = self.identity
        for field in (
            "tenant_id",
            "project_id",
            "run_id",
            "lease_id",
        ):
            _require_identifier(getattr(identity, field), field)
        _require_sha256(
            identity.launch_fingerprint,
            "launch_fingerprint",
        )
        _require_sha256(
            identity.transport_config_fingerprint,
            "transport_config_fingerprint",
        )
        if identity.adapter_mode not in {
            LIVE_FIXTURE_ADAPTER_MODE,
            LIVE_AUTHORIZED_ADAPTER_MODE,
        }:
            raise LiveSQLiteRepositoryError(
                "repository adapter is outside the two frozen live modes",
                code="live_sqlite_adapter_forbidden",
            )
        if identity.adapter_mode == LIVE_FIXTURE_ADAPTER_MODE:
            if isinstance(
                identity,
                ClaimedAuthorizedLiveExecutionIdentity,
            ):
                raise LiveSQLiteRepositoryError(
                    "fixture identity cannot carry an authorization",
                    code="live_sqlite_identity_invalid",
                )
        else:
            if not isinstance(
                identity,
                ClaimedAuthorizedLiveExecutionIdentity,
            ):
                raise LiveSQLiteRepositoryError(
                    "authorized adapter requires a consumed grant identity",
                    code="live_sqlite_identity_invalid",
                )
            _require_identifier(
                self.authorization_id,
                "authorization_id",
            )
            _require_sha256(
                self.authorization_fingerprint,
                "authorization_fingerprint",
            )
        _strict_integer(
            identity.lease_generation,
            "lease_generation",
            minimum=1,
        )
        context = self._context
        if (
            context.run_id != identity.run_id
            or context.launch_id != identity.launch_fingerprint
            or context.transport_config_fingerprint
            != identity.transport_config_fingerprint
            or context.cache_namespace
            != self.execution_provenance
            or isinstance(context.max_workers, bool)
            or not isinstance(context.max_workers, int)
            or not 1 <= context.max_workers <= 48
        ):
            raise LiveSQLiteRepositoryError(
                "qualified context disagrees with the claimed identity",
                code="live_sqlite_context_identity_mismatch",
            )

    @contextmanager
    def _read_transaction(self) -> Iterator[sqlite3.Connection]:
        with self.store._connection() as connection:
            connection.execute("BEGIN")
            try:
                yield connection
            finally:
                connection.rollback()

    @staticmethod
    def _database_now(connection: sqlite3.Connection) -> str:
        return str(
            connection.execute(
                "SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            ).fetchone()[0]
        )

    def _require_bound_runtime(
        self,
        connection: sqlite3.Connection,
        *,
        allowed_statuses: Sequence[str],
        require_unexpired: bool,
    ) -> sqlite3.Row:
        identity = self.identity
        if identity.adapter_mode == LIVE_AUTHORIZED_ADAPTER_MODE:
            authorization_projection = """
                , a.authorization_id AS row_authorization_id
                , a.authorization_fingerprint
                    AS row_authorization_fingerprint
                , a.provider_call_cap AS row_provider_call_cap
                , a.token_cap AS row_token_cap
                , a.consumed_at AS authorization_consumed_at
                , a.consumed_runtime_fingerprint
                , a.consumed_plan_fingerprint
                , a.revoked_at AS authorization_revoked_at
            """
            authorization_join = """
            JOIN run_live_authorizations a
              ON a.tenant_id = u.tenant_id
             AND a.project_id = u.project_id
             AND a.run_id = u.run_id
             AND a.authorization_id = u.authorization_id
             AND a.authorization_fingerprint
                    = u.authorization_fingerprint
            """
        else:
            authorization_projection = """
                , NULL AS row_authorization_id
                , NULL AS row_authorization_fingerprint
                , NULL AS row_provider_call_cap
                , NULL AS row_token_cap
                , NULL AS authorization_consumed_at
                , NULL AS consumed_runtime_fingerprint
                , NULL AS consumed_plan_fingerprint
                , NULL AS authorization_revoked_at
            """
            authorization_join = ""
        row = connection.execute(
            f"""
            SELECT
                rr.status, rr.adapter_mode, rr.launch_fingerprint,
                rr.input_fingerprint, rr.effective_concurrency_limit,
                rr.turn_limit, rr.lease_id, rr.lease_generation,
                rr.lease_expires_at,
                u.extension_fingerprint, u.execution_provenance,
                u.transport_config_fingerprint, u.slot_set_fingerprint,
                u.expected_agent_turns, u.effective_concurrency_limit
                    AS usage_concurrency_limit,
                u.authorization_id, u.authorization_fingerprint,
                u.authorization_provider_call_cap,
                u.authorization_token_cap, u.usage_version,
                u.provider_calls_reserved, u.provider_calls_completed,
                u.provider_calls_known, u.provider_calls_unknown,
                u.effective_provider_call_limit, u.tokens_in_flight,
                u.provider_reported_tokens, u.risk_charged_tokens,
                u.effective_token_limit, u.active_attempts,
                u.peak_concurrency, u.turns_completed,
                e.provider_profile_id, e.provider_profile_fingerprint,
                e.prompt_contract_version,
                e.decision_validator_version,
                e.cache_algorithm_version, e.checkpoint_algorithm_version,
                e.reservation_algorithm, e.network_retry_limit,
                e.format_repair_limit,
                p.profile_json,
                ps.enabled AS provider_profile_enabled
                {authorization_projection}
            FROM run_runtime_states rr
            JOIN run_live_usage u
              ON u.tenant_id = rr.tenant_id
             AND u.project_id = rr.project_id
             AND u.run_id = rr.run_id
             AND u.launch_fingerprint = rr.launch_fingerprint
            JOIN run_live_plan_extensions e
              ON e.tenant_id = rr.tenant_id
             AND e.project_id = rr.project_id
             AND e.run_id = rr.run_id
             AND e.input_fingerprint = rr.input_fingerprint
             AND e.extension_fingerprint = u.extension_fingerprint
            JOIN live_provider_profiles p
              ON p.provider_profile_id = e.provider_profile_id
             AND p.provider_profile_fingerprint
                    = e.provider_profile_fingerprint
            JOIN live_provider_profile_states ps
              ON ps.provider_profile_id = e.provider_profile_id
            {authorization_join}
            WHERE rr.tenant_id = ? AND rr.project_id = ? AND rr.run_id = ?
              AND rr.launch_fingerprint = ? AND rr.adapter_mode = ?
              AND rr.lease_id = ? AND rr.lease_generation = ?
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
        if row is None or row["status"] not in allowed_statuses:
            raise LiveSQLiteRepositoryError(
                "claimed execution lease is stale or outside its state",
                code="live_sqlite_stale_lease",
            )
        now = self._database_now(connection)
        if (
            require_unexpired
            and (
                row["lease_expires_at"] is None
                or row["lease_expires_at"] <= now
            )
        ):
            raise LiveSQLiteRepositoryError(
                "claimed execution lease has expired",
                code="live_sqlite_stale_lease",
            )
        fixture_identity_invalid = (
            identity.adapter_mode == LIVE_FIXTURE_ADAPTER_MODE
            and (
                row["authorization_id"] is not None
                or row["authorization_fingerprint"] is not None
                or row["authorization_provider_call_cap"] is not None
                or row["authorization_token_cap"] is not None
                or row["row_authorization_id"] is not None
            )
        )
        authorized_identity_invalid = (
            identity.adapter_mode == LIVE_AUTHORIZED_ADAPTER_MODE
            and (
                row["authorization_id"] != self.authorization_id
                or row["authorization_fingerprint"]
                != self.authorization_fingerprint
                or row["row_authorization_id"]
                != self.authorization_id
                or row["row_authorization_fingerprint"]
                != self.authorization_fingerprint
                or row["authorization_consumed_at"] is None
                or row["consumed_runtime_fingerprint"]
                != identity.launch_fingerprint
                or row["consumed_plan_fingerprint"]
                != row["input_fingerprint"]
                or row["authorization_provider_call_cap"]
                != row["row_provider_call_cap"]
                or row["authorization_token_cap"]
                != row["row_token_cap"]
                or row["effective_provider_call_limit"]
                > row["authorization_provider_call_cap"]
                or row["effective_token_limit"]
                > row["authorization_token_cap"]
            )
        )
        if (
            row["execution_provenance"] != self.execution_provenance
            or fixture_identity_invalid
            or authorized_identity_invalid
            or row["expected_agent_turns"] != row["turn_limit"]
            or row["usage_concurrency_limit"]
            != row["effective_concurrency_limit"]
            or row["cache_algorithm_version"]
            != LIVE_CACHE_SCHEMA_VERSION
            or row["checkpoint_algorithm_version"]
            != LIVE_CHECKPOINT_SCHEMA_VERSION
        ):
            raise LiveSQLiteRepositoryError(
                "claimed runtime is not an isolated schema-v7 live identity",
                code="live_sqlite_execution_identity_invalid",
            )
        profile = _canonical_json_loads(
            row["profile_json"],
            "profile_json",
        )
        reviewed_profile = get_reviewed_provider_profile(internal=True)
        reviewed_profile.pop("provider_profile_fingerprint")
        context = self._context
        if (
            not isinstance(profile, dict)
            or profile != reviewed_profile
            or context.model != profile.get("requested_model")
            or context.reasoning_effort != profile.get("reasoning_effort")
            or context.max_output_tokens
            != profile.get("maximum_output_tokens")
            or context.max_workers != row["effective_concurrency_limit"]
            or context.cache_namespace
            != self.execution_provenance
            or context.network_retry_limit
            != row["network_retry_limit"]
            or context.format_repair_limit
            != row["format_repair_limit"]
            or context.network_retry_limit
            != profile.get("network_retry_limit")
            or context.format_repair_limit
            != profile.get("format_repair_limit")
        ):
            raise LiveSQLiteRepositoryError(
                "qualified context conflicts with the frozen live plan",
                code="live_sqlite_context_identity_mismatch",
            )
        return row

    def _assert_new_attempt_allowed(
        self,
        runtime: Mapping[str, Any],
    ) -> None:
        if runtime["status"] != "running":
            raise LiveSQLiteRepositoryError(
                "runtime cancellation forbids a new provider attempt",
                code="live_sqlite_runtime_cancel_requested",
            )
        if self.identity.adapter_mode != LIVE_AUTHORIZED_ADAPTER_MODE:
            return
        if runtime["authorization_revoked_at"] is not None:
            raise LiveSQLiteRepositoryError(
                "consumed authorization was explicitly revoked",
                code="live_sqlite_authorization_revoked",
            )
        if runtime["provider_profile_enabled"] != 1:
            raise LiveSQLiteRepositoryError(
                "reviewed provider profile is disabled",
                code="live_sqlite_provider_profile_disabled",
            )

    @staticmethod
    def _slot_mapping(row: Mapping[str, Any]) -> dict[str, Any]:
        return {field: row[field] for field in _SLOT_FIELDS}

    def _require_slot(
        self,
        connection: sqlite3.Connection,
        slot: Mapping[str, Any],
    ) -> sqlite3.Row:
        if not isinstance(slot, Mapping) or set(slot) != set(_SLOT_FIELDS):
            raise LiveSQLiteRepositoryError(
                "turn slot does not contain the exact immutable fields",
                code="live_sqlite_slot_identity_invalid",
            )
        ordinal = _strict_integer(
            slot.get("ordinal"),
            "slot.ordinal",
            minimum=1,
        )
        identity = self.identity
        row = connection.execute(
            """
            SELECT *
            FROM run_live_turn_slots
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
              AND launch_fingerprint = ? AND ordinal = ?
            """,
            (
                identity.tenant_id,
                identity.project_id,
                identity.run_id,
                identity.launch_fingerprint,
                ordinal,
            ),
        ).fetchone()
        if row is None or self._slot_mapping(row) != dict(slot):
            raise LiveSQLiteRepositoryError(
                "turn slot differs from its durable immutable identity",
                code="live_sqlite_slot_identity_invalid",
            )
        return row

    def _checkpoint_row(
        self,
        connection: sqlite3.Connection,
        ordinal: int,
    ) -> sqlite3.Row | None:
        identity = self.identity
        row = connection.execute(
            """
            SELECT *
            FROM run_live_checkpoints
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
              AND launch_fingerprint = ? AND checkpoint_ordinal = ?
            """,
            (
                identity.tenant_id,
                identity.project_id,
                identity.run_id,
                identity.launch_fingerprint,
                ordinal,
            ),
        ).fetchone()
        if row is None:
            return None
        population = _strict_json_loads(
            row["population_state_json"],
            "population_state_json",
        )
        public = _strict_json_loads(
            row["public_state_json"],
            "public_state_json",
        )
        if (
            _sha256(population) != row["population_state_sha256"]
            or _sha256(public) != row["public_state_sha256"]
        ):
            raise LiveSQLiteRepositoryError(
                "checkpoint JSON fingerprints are inconsistent",
                code="live_sqlite_checkpoint_invalid",
            )
        body = self._checkpoint_fingerprint_body(row)
        if _sha256(body) != row["checkpoint_sha256"]:
            raise LiveSQLiteRepositoryError(
                "checkpoint seal is invalid",
                code="live_sqlite_checkpoint_invalid",
            )
        return row

    @staticmethod
    def _checkpoint_fingerprint_body(
        row: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "tenant_id": row["tenant_id"],
            "project_id": row["project_id"],
            "run_id": row["run_id"],
            "launch_fingerprint": row["launch_fingerprint"],
            "checkpoint_ordinal": row["checkpoint_ordinal"],
            "checkpoint_schema_version": row[
                "checkpoint_schema_version"
            ],
            "checkpoint_kind": row["checkpoint_kind"],
            "seed": row["seed"],
            "branch_id": row["branch_id"],
            "phase_id": row["phase_id"],
            "last_completed_ordinal": row["last_completed_ordinal"],
            "population_state_sha256": row["population_state_sha256"],
            "public_state_sha256": row["public_state_sha256"],
            "decision_cache_key": row["decision_cache_key"],
            "previous_checkpoint_sha256": row[
                "previous_checkpoint_sha256"
            ],
            "lease_id": row["lease_id"],
            "lease_generation": row["lease_generation"],
        }

    def _initial_checkpoint_payload(
        self,
        connection: sqlite3.Connection,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        identity = self.identity
        slot_rows = connection.execute(
            """
            SELECT seed, agent_index
            FROM run_live_turn_slots
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
              AND launch_fingerprint = ?
            ORDER BY seed, agent_index, ordinal
            """,
            (
                identity.tenant_id,
                identity.project_id,
                identity.run_id,
                identity.launch_fingerprint,
            ),
        ).fetchall()
        indexes_by_seed: dict[int, set[int]] = {}
        for row in slot_rows:
            indexes_by_seed.setdefault(row["seed"], set()).add(
                row["agent_index"]
            )
        populations = self._context.initial_populations
        if (
            not indexes_by_seed
            or set(indexes_by_seed) != set(populations)
        ):
            raise LiveSQLiteRepositoryError(
                "qualified initial-population seeds disagree with slots",
                code="live_sqlite_context_invalid",
            )
        population_entries: list[dict[str, Any]] = []
        for seed in sorted(indexes_by_seed):
            population = populations[seed]
            if (
                not isinstance(population, Sequence)
                or isinstance(population, (str, bytes, bytearray))
                or indexes_by_seed[seed] != set(range(len(population)))
                or not all(isinstance(agent, AgentState) for agent in population)
            ):
                raise LiveSQLiteRepositoryError(
                    "qualified initial population is incomplete",
                    code="live_sqlite_context_invalid",
                )
            population_entries.append(
                {
                    "seed": seed,
                    "agents": [asdict(agent) for agent in population],
                }
            )
        population_payload = {
            "populations": population_entries,
            "initial_population_fingerprint": _sha256(
                population_entries
            ),
        }
        public_payload: dict[str, Any] = {}
        _scan_safe_json(population_payload)
        return population_payload, public_payload

    def _ensure_initial_checkpoint(
        self,
        connection: sqlite3.Connection,
    ) -> sqlite3.Row:
        population_payload, public_payload = (
            self._initial_checkpoint_payload(connection)
        )
        population_json = _canonical_json(population_payload)
        public_json = _canonical_json(public_payload)
        existing = self._checkpoint_row(connection, 0)
        if existing is not None:
            if (
                existing["checkpoint_kind"] != "initial_population"
                or existing["seed"] is not None
                or existing["branch_id"] is not None
                or existing["phase_id"] is not None
                or existing["previous_checkpoint_sha256"] is not None
                or existing["population_state_json"] != population_json
                or existing["public_state_json"] != public_json
            ):
                raise LiveSQLiteRepositoryError(
                    "initial checkpoint conflicts with qualified context",
                    code="live_sqlite_checkpoint_invalid",
                )
            return existing
        population_sha = _sha256(population_payload)
        public_sha = _sha256(public_payload)
        body = {
            "tenant_id": self.identity.tenant_id,
            "project_id": self.identity.project_id,
            "run_id": self.identity.run_id,
            "launch_fingerprint": self.identity.launch_fingerprint,
            "checkpoint_ordinal": 0,
            "checkpoint_schema_version": LIVE_CHECKPOINT_SCHEMA_VERSION,
            "checkpoint_kind": "initial_population",
            "seed": None,
            "branch_id": None,
            "phase_id": None,
            "last_completed_ordinal": 0,
            "population_state_sha256": population_sha,
            "public_state_sha256": public_sha,
            "decision_cache_key": None,
            "previous_checkpoint_sha256": None,
            "lease_id": self.identity.lease_id,
            "lease_generation": self.identity.lease_generation,
        }
        now = self._database_now(connection)
        connection.execute(
            """
            INSERT INTO run_live_checkpoints(
                tenant_id, project_id, run_id,
                launch_fingerprint, checkpoint_ordinal,
                checkpoint_schema_version, checkpoint_kind,
                seed, branch_id, phase_id,
                last_completed_ordinal,
                population_state_json, population_state_sha256,
                public_state_json, public_state_sha256,
                decision_cache_key, previous_checkpoint_sha256,
                checkpoint_sha256, lease_id, lease_generation,
                created_at
            ) VALUES (
                ?, ?, ?, ?, 0, ?, 'initial_population',
                NULL, NULL, NULL, 0, ?, ?, ?, ?,
                NULL, NULL, ?, ?, ?, ?
            )
            """,
            (
                self.identity.tenant_id,
                self.identity.project_id,
                self.identity.run_id,
                self.identity.launch_fingerprint,
                LIVE_CHECKPOINT_SCHEMA_VERSION,
                population_json,
                population_sha,
                public_json,
                public_sha,
                _sha256(body),
                self.identity.lease_id,
                self.identity.lease_generation,
                now,
            ),
        )
        stored = self._checkpoint_row(connection, 0)
        if stored is None:
            raise LiveSQLiteRepositoryError(
                "initial checkpoint was not durably created",
                code="live_sqlite_checkpoint_invalid",
            )
        return stored

    def _validate_checkpoint_chain(
        self,
        connection: sqlite3.Connection,
    ) -> sqlite3.Row:
        identity = self.identity
        rows = connection.execute(
            """
            SELECT *
            FROM run_live_checkpoints
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
              AND launch_fingerprint = ?
            ORDER BY checkpoint_ordinal
            """,
            (
                identity.tenant_id,
                identity.project_id,
                identity.run_id,
                identity.launch_fingerprint,
            ),
        ).fetchall()
        if (
            not rows
            or rows[0]["checkpoint_ordinal"] != 0
            or rows[0]["checkpoint_kind"] != "initial_population"
        ):
            raise LiveSQLiteRepositoryError(
                "checkpoint chain has no initial-population root",
                code="live_sqlite_checkpoint_invalid",
            )
        previous_sha: str | None = None
        for row in rows:
            checked = self._checkpoint_row(
                connection,
                row["checkpoint_ordinal"],
            )
            if (
                checked is None
                or row["previous_checkpoint_sha256"] != previous_sha
            ):
                raise LiveSQLiteRepositoryError(
                    "checkpoint seal chain is discontinuous",
                    code="live_sqlite_checkpoint_invalid",
                )
            previous_sha = row["checkpoint_sha256"]
        return rows[-1]

    def _load_parent_from_connection(
        self,
        connection: sqlite3.Connection,
        slot_row: Mapping[str, Any],
    ) -> AgentState:
        parent = slot_row["state_parent_ordinal"]
        required = slot_row["required_checkpoint_ordinal"]
        if parent is None or required is None:
            raise LiveSQLiteRepositoryError(
                "slot has no durable parent dependency",
                code="live_sqlite_parent_invalid",
            )
        checkpoint = self._checkpoint_row(connection, required)
        if checkpoint is None:
            raise LiveSQLiteRepositoryError(
                "required parent checkpoint is unavailable",
                code="live_sqlite_phase_barrier_missing",
            )
        population = _strict_json_loads(
            checkpoint["population_state_json"],
            "population_state_json",
        )
        if (
            not isinstance(population, dict)
            or set(population) != {"agents", "phase_fingerprint"}
            or not isinstance(population["agents"], list)
        ):
            raise LiveSQLiteRepositoryError(
                "checkpoint population envelope is invalid",
                code="live_sqlite_checkpoint_invalid",
            )
        match = [
            item
            for item in population["agents"]
            if isinstance(item, dict)
            and item.get("slot_ordinal") == parent
        ]
        if (
            len(match) != 1
            or set(match[0]) != {"slot_ordinal", "state"}
        ):
            raise LiveSQLiteRepositoryError(
                "checkpoint does not contain the required parent state",
                code="live_sqlite_parent_invalid",
            )
        return _agent_from_payload(match[0]["state"])

    def _load_public_from_connection(
        self,
        connection: sqlite3.Connection,
        slot_row: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        required = slot_row["required_checkpoint_ordinal"]
        if required is None:
            raise LiveSQLiteRepositoryError(
                "slot has no public-state dependency",
                code="live_sqlite_parent_invalid",
            )
        checkpoint = self._checkpoint_row(connection, required)
        if checkpoint is None:
            raise LiveSQLiteRepositoryError(
                "required public checkpoint is unavailable",
                code="live_sqlite_phase_barrier_missing",
            )
        public = _strict_json_loads(
            checkpoint["public_state_json"],
            "public_state_json",
        )
        if not isinstance(public, dict):
            raise LiveSQLiteRepositoryError(
                "checkpoint public state is not an object",
                code="live_sqlite_checkpoint_invalid",
            )
        return public

    def _rendered_identity(
        self,
        connection: sqlite3.Connection,
        slot_row: Mapping[str, Any],
        *,
        format_repair_index: int = 0,
    ) -> _RenderedIdentity:
        repair_index = _strict_integer(
            format_repair_index,
            "format_repair_index",
        )
        if repair_index > self._context.format_repair_limit:
            raise LiveSQLiteRepositoryError(
                "format-repair index exceeds the frozen plan",
                code="live_sqlite_request_identity_invalid",
            )
        seed = slot_row["seed"]
        agent_index = slot_row["agent_index"]
        population = self._context.initial_populations.get(seed)
        if (
            population is None
            or agent_index < 0
            or agent_index >= len(population)
        ):
            raise LiveSQLiteRepositoryError(
                "qualified initial population is incomplete",
                code="live_sqlite_context_invalid",
            )
        if slot_row["phase_id"] in {"baseline", "burst"}:
            agent = deepcopy(population[agent_index])
            previous_public = None
        else:
            agent = self._load_parent_from_connection(connection, slot_row)
            previous_public = self._load_public_from_connection(
                connection,
                slot_row,
            )
        phase = self._context.phases.get(slot_row["phase_id"])
        policy = self._context.policies.get(slot_row["branch_id"])
        if phase is None or policy is None:
            raise LiveSQLiteRepositoryError(
                "qualified phase or policy is unavailable",
                code="live_sqlite_context_invalid",
            )
        base_rendered = render_agent_turn(
            event=deepcopy(dict(self._context.event)),
            definition=deepcopy(dict(self._context.definition)),
            phase=deepcopy(dict(phase)),
            policy=policy,
            agent=deepcopy(agent),
            previous_public=(
                None
                if previous_public is None
                else deepcopy(dict(previous_public))
            ),
        )
        base_request = build_deterministic_transport_request(
            base_rendered,
            self._context,
        )
        base_request_sha = canonicalize_transport_request(
            base_request
        ).request_sha256
        rendered = (
            base_rendered
            if repair_index == 0
            else render_format_repair_turn(
                base_rendered,
                controlled_reason_code=FORMAT_REPAIR_REASON_CODE,
                repair_index=repair_index,
            )
        )
        request = build_deterministic_transport_request(
            rendered,
            self._context,
        )
        request_sha = canonicalize_transport_request(request).request_sha256
        agent_sha = _sha256(asdict(agent))
        public_sha = (
            None
            if previous_public is None
            else _sha256(previous_public)
        )
        cache_key = _sha256(
            {
                "namespace": self._context.cache_namespace,
                "transport_id": self.transport_id,
                "transport_config_fingerprint": (
                    self._context.transport_config_fingerprint
                ),
                "run_id": self._context.run_id,
                "launch_id": self._context.launch_id,
                "slot_fingerprint": slot_row["slot_fingerprint"],
                "prompt_sha256": base_rendered.prompt_sha256,
                "request_sha256": base_request_sha,
                "agent_state_sha256": agent_sha,
                "prior_public_sha256": public_sha,
            }
        )
        return _RenderedIdentity(
            request_sha256=request_sha,
            cache_key=cache_key,
            prompt_fingerprint=rendered.prompt_sha256,
            base_prompt_fingerprint=base_rendered.prompt_sha256,
            format_repair_index=repair_index,
            agent_state_fingerprint=agent_sha,
            prior_public_fingerprint=(
                public_sha if public_sha is not None else _sha256(None)
            ),
            agent=agent,
            previous_public=previous_public,
            base_rendered=base_rendered,
            rendered=rendered,
        )

    def load_execution_context(self) -> LiveExecutionContext:
        with self._read_transaction() as connection:
            self._require_bound_runtime(
                connection,
                allowed_statuses=("running",),
                require_unexpired=True,
            )
        return deepcopy(self._context)

    def load_parent_agent(self, slot: Mapping[str, Any]) -> AgentState:
        with self._read_transaction() as connection:
            self._require_bound_runtime(
                connection,
                allowed_statuses=("running",),
                require_unexpired=True,
            )
            slot_row = self._require_slot(connection, slot)
            return deepcopy(
                self._load_parent_from_connection(connection, slot_row)
            )

    def load_required_phase_aggregate(
        self,
        slot: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        with self._read_transaction() as connection:
            self._require_bound_runtime(
                connection,
                allowed_statuses=("running",),
                require_unexpired=True,
            )
            slot_row = self._require_slot(connection, slot)
            return deepcopy(
                dict(
                    self._load_public_from_connection(
                        connection,
                        slot_row,
                    )
                )
            )

    def next_ready_phase(self) -> ReadyLivePhase | None:
        with self.store._write_transaction() as connection:
            runtime = self._require_bound_runtime(
                connection,
                allowed_statuses=("running", "cancel_requested"),
                require_unexpired=True,
            )
            if runtime["status"] == "cancel_requested":
                return None
            self._ensure_initial_checkpoint(connection)
            self._validate_checkpoint_chain(connection)
            self._recover_stale_attempts(
                connection,
                runtime,
            )
            return self._next_phase_from_connection(
                connection,
                runtime,
            )

    def _next_phase_from_connection(
        self,
        connection: sqlite3.Connection,
        runtime: Mapping[str, Any],
    ) -> ReadyLivePhase | None:
        identity = self.identity
        rows = connection.execute(
            """
            SELECT *
            FROM run_live_turn_slots
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
              AND launch_fingerprint = ?
            ORDER BY ordinal
            """,
            (
                identity.tenant_id,
                identity.project_id,
                identity.run_id,
                identity.launch_fingerprint,
            ),
        ).fetchall()
        if len(rows) != runtime["expected_agent_turns"]:
            raise LiveSQLiteRepositoryError(
                "durable turn-slot set is incomplete",
                code="live_sqlite_slot_set_invalid",
            )
        groups: list[list[sqlite3.Row]] = []
        for row in rows:
            key = (row["seed"], row["branch_id"], row["phase_id"])
            if not groups or (
                groups[-1][0]["seed"],
                groups[-1][0]["branch_id"],
                groups[-1][0]["phase_id"],
            ) != key:
                groups.append([row])
            else:
                groups[-1].append(row)
        seen_missing = False
        for group in groups:
            barrier = group[-1]["ordinal"]
            checkpoint = self._checkpoint_row(connection, barrier)
            if checkpoint is not None:
                if seen_missing or any(
                    row["status"] != "completed" for row in group
                ):
                    raise LiveSQLiteRepositoryError(
                        "phase checkpoint bypasses incomplete durable slots",
                        code="live_sqlite_phase_barrier_invalid",
                    )
                continue
            seen_missing = True
            if any(
                row["status"] in {"failed", "cancelled"} for row in group
            ):
                raise LiveSQLiteRepositoryError(
                    "the next phase contains terminal turn slots",
                    code="live_sqlite_phase_terminal",
                )
            requirements = {
                row["required_checkpoint_ordinal"] for row in group
            }
            if len(requirements) != 1:
                raise LiveSQLiteRepositoryError(
                    "phase slots disagree on their checkpoint barrier",
                    code="live_sqlite_phase_barrier_invalid",
                )
            required = next(iter(requirements))
            if required is not None and (
                self._checkpoint_row(connection, required) is None
            ):
                raise LiveSQLiteRepositoryError(
                    "the next phase dependency is not durable",
                    code="live_sqlite_phase_barrier_missing",
                )
            first = group[0]
            phase_key = (
                f"{first['seed']}:{first['branch_id']}:"
                f"{first['phase_id']}"
            )
            return ReadyLivePhase(
                phase_key=phase_key,
                seed=first["seed"],
                branch_id=first["branch_id"],
                phase_id=first["phase_id"],
                slots=tuple(self._slot_mapping(row) for row in group),
            )
        if any(row["status"] != "completed" for row in rows):
            raise LiveSQLiteRepositoryError(
                "completed phase chain disagrees with slot statuses",
                code="live_sqlite_phase_barrier_invalid",
            )
        if runtime["turns_completed"] != runtime["expected_agent_turns"]:
            raise LiveSQLiteRepositoryError(
                "slot and usage completion counters disagree",
                code="live_sqlite_usage_invalid",
            )
        return None

    @staticmethod
    def _attempt_final_body(
        attempt: Mapping[str, Any],
        *,
        outcome_code: str,
        returned_model_code: str | None,
        provider_reported_tokens: int,
        risk_charged_tokens: int,
        response_sha256: str | None,
    ) -> dict[str, Any]:
        return {
            "attempt_id": attempt["attempt_id"],
            "reservation_fingerprint": attempt[
                "reservation_fingerprint"
            ],
            "canonical_request_sha256": attempt[
                "canonical_request_sha256"
            ],
            "request_occurrence": attempt["request_occurrence"],
            "outcome_code": outcome_code,
            "returned_model_code": returned_model_code,
            "provider_reported_tokens": provider_reported_tokens,
            "risk_charged_tokens": risk_charged_tokens,
            "response_sha256": response_sha256,
        }

    def _finalize_recovered_attempt(
        self,
        connection: sqlite3.Connection,
        attempt: sqlite3.Row,
        *,
        outcome_code: str,
        risk_charged_tokens: int,
        now: str,
    ) -> None:
        final_body = self._attempt_final_body(
            attempt,
            outcome_code=outcome_code,
            returned_model_code=None,
            provider_reported_tokens=0,
            risk_charged_tokens=risk_charged_tokens,
            response_sha256=None,
        )
        updated = connection.execute(
            """
            UPDATE run_live_provider_attempts
            SET attempt_status = 'finalized',
                outcome_code = ?,
                returned_model_code = NULL,
                provider_reported_tokens = 0,
                risk_charged_tokens = ?,
                final_fingerprint = ?,
                attempt_version = attempt_version + 1,
                finalized_at = ?,
                updated_at = ?
            WHERE attempt_id = ?
              AND tenant_id = ? AND project_id = ? AND run_id = ?
              AND launch_fingerprint = ?
              AND attempt_status = ?
              AND lease_id = ? AND lease_generation = ?
              AND attempt_version = ?
            """,
            (
                outcome_code,
                risk_charged_tokens,
                _sha256(final_body),
                now,
                now,
                attempt["attempt_id"],
                self.identity.tenant_id,
                self.identity.project_id,
                self.identity.run_id,
                self.identity.launch_fingerprint,
                attempt["attempt_status"],
                attempt["lease_id"],
                attempt["lease_generation"],
                attempt["attempt_version"],
            ),
        )
        if updated.rowcount != 1:
            raise LiveSQLiteRepositoryError(
                "stale attempt recovery lost its compare-and-swap",
                code="live_sqlite_recovery_conflict",
            )

    @staticmethod
    def _retry_available(
        runtime: Mapping[str, Any],
        attempt: Mapping[str, Any],
        *,
        outcome_code: str,
    ) -> bool:
        format_index = attempt["format_repair_index"]
        network_index = attempt["network_retry_index"]
        if (
            isinstance(format_index, bool)
            or not isinstance(format_index, int)
            or isinstance(network_index, bool)
            or not isinstance(network_index, int)
            or not 0 <= format_index <= runtime["format_repair_limit"]
            or not 0 <= network_index <= runtime["network_retry_limit"]
        ):
            raise LiveSQLiteRepositoryError(
                "attempt retry coordinates exceed the frozen plan",
                code="live_sqlite_recovery_invalid",
            )
        if outcome_code == "reported_error":
            return format_index < runtime["format_repair_limit"]
        if outcome_code in {
            "reported_success",
            "timeout_unknown",
            "transport_unknown",
            "worker_crash_unknown",
            "pre_send_failed",
        }:
            return network_index < runtime["network_retry_limit"]
        raise LiveSQLiteRepositoryError(
            "attempt outcome has no frozen retry transition",
            code="live_sqlite_recovery_invalid",
        )

    def _recover_stale_attempts(
        self,
        connection: sqlite3.Connection,
        runtime: Mapping[str, Any],
    ) -> None:
        identity = self.identity
        active_attempts = connection.execute(
            """
            SELECT *
            FROM run_live_provider_attempts
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
              AND launch_fingerprint = ?
              AND attempt_status IN ('reserved', 'invoked')
            ORDER BY slot_ordinal, attempt_number
            """,
            (
                identity.tenant_id,
                identity.project_id,
                identity.run_id,
                identity.launch_fingerprint,
            ),
        ).fetchall()
        if (
            runtime["provider_calls_reserved"] != len(active_attempts)
            or runtime["active_attempts"] != len(active_attempts)
            or runtime["tokens_in_flight"]
            != sum(
                item["token_risk_reservation"]
                for item in active_attempts
            )
        ):
            raise LiveSQLiteRepositoryError(
                "active attempts do not conserve live usage reservations",
                code="live_sqlite_recovery_invalid",
            )
        if any(
            item["transport_provenance"] != self.execution_provenance
            or item["transport_config_fingerprint"]
            != identity.transport_config_fingerprint
            for item in active_attempts
        ):
            raise LiveSQLiteRepositoryError(
                "active attempts cross their frozen transport identity",
                code="live_sqlite_recovery_invalid",
            )
        attempts = [
            item
            for item in active_attempts
            if (
                item["lease_id"] != identity.lease_id
                or item["lease_generation"] != identity.lease_generation
            )
        ]
        now = self._database_now(connection)
        unknown_attempts = [
            item for item in attempts if item["attempt_status"] == "invoked"
        ]
        for attempt in attempts:
            invoked = attempt["attempt_status"] == "invoked"
            outcome = (
                "worker_crash_unknown" if invoked else "pre_send_failed"
            )
            risk = attempt["token_risk_reservation"] if invoked else 0
            self._finalize_recovered_attempt(
                connection,
                attempt,
                outcome_code=outcome,
                risk_charged_tokens=risk,
                now=now,
            )
            slot_status = (
                "pending"
                if self._retry_available(
                    runtime,
                    attempt,
                    outcome_code=outcome,
                )
                else "failed"
            )
            updated_slot = connection.execute(
                """
                UPDATE run_live_turn_slots
                SET status = ?,
                    prompt_fingerprint = CASE
                        WHEN ? = 'pending' THEN NULL
                        ELSE prompt_fingerprint
                    END,
                    decision_cache_key = CASE
                        WHEN ? = 'pending' THEN NULL
                        ELSE decision_cache_key
                    END,
                    lease_id = NULL,
                    lease_generation = NULL,
                    slot_version = slot_version + 1,
                    inflight_at = NULL,
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND launch_fingerprint = ? AND ordinal = ?
                  AND status = 'inflight'
                  AND lease_id = ? AND lease_generation = ?
                """,
                (
                    slot_status,
                    slot_status,
                    slot_status,
                    now,
                    identity.tenant_id,
                    identity.project_id,
                    identity.run_id,
                    identity.launch_fingerprint,
                    attempt["slot_ordinal"],
                    attempt["lease_id"],
                    attempt["lease_generation"],
                ),
            )
            if updated_slot.rowcount != 1:
                raise LiveSQLiteRepositoryError(
                    "stale slot recovery lost its compare-and-swap",
                    code="live_sqlite_recovery_conflict",
                )
        if attempts:
            unknown_count = len(unknown_attempts)
            unknown_risk = sum(
                item["token_risk_reservation"]
                for item in unknown_attempts
            )
            stale_risk = sum(
                item["token_risk_reservation"] for item in attempts
            )
            updated_usage = connection.execute(
                """
                UPDATE run_live_usage
                SET provider_calls_reserved
                        = provider_calls_reserved - ?,
                    provider_calls_completed
                        = provider_calls_completed + ?,
                    provider_calls_unknown
                        = provider_calls_unknown + ?,
                    tokens_in_flight = tokens_in_flight - ?,
                    risk_charged_tokens
                        = risk_charged_tokens + ?,
                    active_attempts = active_attempts - ?,
                    usage_version = usage_version + 1,
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND launch_fingerprint = ? AND usage_version = ?
                  AND provider_calls_reserved >= ?
                  AND active_attempts >= ?
                  AND tokens_in_flight >= ?
                  AND execution_provenance = ?
                  AND authorization_id IS ?
                  AND authorization_fingerprint IS ?
                """,
                (
                    len(attempts),
                    unknown_count,
                    unknown_count,
                    stale_risk,
                    unknown_risk,
                    len(attempts),
                    now,
                    identity.tenant_id,
                    identity.project_id,
                    identity.run_id,
                    identity.launch_fingerprint,
                    runtime["usage_version"],
                    len(attempts),
                    len(attempts),
                    stale_risk,
                    self.execution_provenance,
                    self.authorization_id,
                    self.authorization_fingerprint,
                ),
            )
            if updated_usage.rowcount != 1:
                raise LiveSQLiteRepositoryError(
                    "stale usage recovery lost its compare-and-swap",
                    code="live_sqlite_recovery_conflict",
                )

        # A provider-success settlement and the slot/cache commit are separate
        # transactions.  After a lease hand-off, a finalized success with no
        # cache body is therefore an orphan: its cost remains charged, while a
        # fresh network attempt is the only body-free recovery path.
        orphans = connection.execute(
            """
            SELECT a.*
            FROM run_live_provider_attempts AS a
            JOIN run_live_turn_slots AS s
              ON s.tenant_id = a.tenant_id
             AND s.project_id = a.project_id
             AND s.run_id = a.run_id
             AND s.launch_fingerprint = a.launch_fingerprint
             AND s.ordinal = a.slot_ordinal
            LEFT JOIN live_decision_cache AS c
              ON c.tenant_id = a.tenant_id
             AND c.cache_key = a.decision_cache_key
            WHERE a.tenant_id = ? AND a.project_id = ?
              AND a.run_id = ? AND a.launch_fingerprint = ?
              AND a.attempt_status = 'finalized'
              AND a.outcome_code = 'reported_success'
              AND s.status = 'inflight'
              AND s.attempt_number = a.attempt_number
              AND (
                    s.lease_id != ?
                    OR s.lease_generation != ?
              )
              AND c.cache_key IS NULL
            ORDER BY a.slot_ordinal
            """,
            (
                identity.tenant_id,
                identity.project_id,
                identity.run_id,
                identity.launch_fingerprint,
                identity.lease_id,
                identity.lease_generation,
            ),
        ).fetchall()
        for attempt in orphans:
            slot_status = (
                "pending"
                if self._retry_available(
                    runtime,
                    attempt,
                    outcome_code="reported_success",
                )
                else "failed"
            )
            updated_slot = connection.execute(
                """
                UPDATE run_live_turn_slots
                SET status = ?,
                    prompt_fingerprint = CASE
                        WHEN ? = 'pending' THEN NULL
                        ELSE prompt_fingerprint
                    END,
                    decision_cache_key = CASE
                        WHEN ? = 'pending' THEN NULL
                        ELSE decision_cache_key
                    END,
                    lease_id = NULL,
                    lease_generation = NULL,
                    slot_version = slot_version + 1,
                    inflight_at = NULL,
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND launch_fingerprint = ? AND ordinal = ?
                  AND status = 'inflight'
                  AND attempt_number = ?
                  AND lease_id = ? AND lease_generation = ?
                """,
                (
                    slot_status,
                    slot_status,
                    slot_status,
                    now,
                    identity.tenant_id,
                    identity.project_id,
                    identity.run_id,
                    identity.launch_fingerprint,
                    attempt["slot_ordinal"],
                    attempt["attempt_number"],
                    attempt["lease_id"],
                    attempt["lease_generation"],
                ),
            )
            if updated_slot.rowcount != 1:
                raise LiveSQLiteRepositoryError(
                    "orphaned-success recovery lost its compare-and-swap",
                    code="live_sqlite_recovery_conflict",
                )

    def _cache_row(
        self,
        connection: sqlite3.Connection,
        cache_key: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT *
            FROM live_decision_cache
            WHERE tenant_id = ? AND cache_key = ?
            """,
            (self.identity.tenant_id, cache_key),
        ).fetchone()

    def _turn_from_cache(
        self,
        row: sqlite3.Row,
        rendered: _RenderedIdentity,
        runtime: Mapping[str, Any],
        *,
        completed_replay: bool = False,
    ) -> ModelTurn:
        identity = self.identity
        if (
            row["cache_schema_version"] != LIVE_CACHE_SCHEMA_VERSION
            or row["transport_provenance"]
            != self.execution_provenance
            or row["transport_config_fingerprint"]
            != identity.transport_config_fingerprint
            or row["source_project_id"] != identity.project_id
            or row["source_run_id"] != identity.run_id
            or row["source_launch_fingerprint"]
            != identity.launch_fingerprint
            or row["input_fingerprint"] != runtime["input_fingerprint"]
            or row["extension_fingerprint"]
            != runtime["extension_fingerprint"]
            or row["provider_profile_fingerprint"]
            != runtime["provider_profile_fingerprint"]
            or row["requested_model_code"] != self._context.model
            or row["prompt_contract_version"]
            != runtime["prompt_contract_version"]
            or row["decision_validator_version"]
            != runtime["decision_validator_version"]
            or row["prompt_fingerprint"]
            != rendered.base_prompt_fingerprint
            or row["agent_state_fingerprint"]
            != rendered.agent_state_fingerprint
            or row["prior_public_state_fingerprint"]
            != rendered.prior_public_fingerprint
        ):
            raise LiveSQLiteRepositoryError(
                "decision cache entry crosses its frozen execution identity",
                code="live_sqlite_cache_identity_invalid",
            )
        envelope = _strict_json_loads(
            row["decision_json"],
            "decision_json",
        )
        required = {
            "decision",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "format_repairs",
            "contract_adjustments",
        }
        if not isinstance(envelope, dict) or set(envelope) != required:
            raise LiveSQLiteRepositoryError(
                "decision cache envelope fields are invalid",
                code="live_sqlite_cache_invalid",
            )
        if _sha256(envelope) != row["decision_sha256"]:
            raise LiveSQLiteRepositoryError(
                "decision cache fingerprint is invalid",
                code="live_sqlite_cache_invalid",
            )
        integers = {
            key: _strict_integer(envelope[key], key)
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "format_repairs",
            )
        }
        if (
            integers["prompt_tokens"] + integers["completion_tokens"]
            != integers["total_tokens"]
            or integers["total_tokens"]
            != row["provider_reported_tokens"]
            or not isinstance(envelope["contract_adjustments"], list)
            or not all(
                isinstance(item, str)
                and 1 <= len(item) <= 96
                for item in envelope["contract_adjustments"]
            )
            or not isinstance(envelope["decision"], dict)
        ):
            raise LiveSQLiteRepositoryError(
                "decision cache usage is inconsistent",
                code="live_sqlite_cache_invalid",
            )
        decision_json = _canonical_json(envelope["decision"])
        try:
            decision = validate_rendered_turn(
                decision_json,
                rendered.base_rendered,
            )
        except Exception as exc:
            raise LiveSQLiteRepositoryError(
                "cached decision no longer validates against its prompt hash",
                code="live_sqlite_cache_invalid",
            ) from exc
        if asdict(decision) != envelope["decision"]:
            raise LiveSQLiteRepositoryError(
                "cached decision changed during validation",
                code="live_sqlite_cache_invalid",
            )
        entry_body = {
            "tenant_id": row["tenant_id"],
            "cache_key": row["cache_key"],
            "cache_schema_version": row["cache_schema_version"],
            "transport_provenance": row["transport_provenance"],
            "transport_config_fingerprint": row[
                "transport_config_fingerprint"
            ],
            "source_project_id": row["source_project_id"],
            "source_run_id": row["source_run_id"],
            "source_launch_fingerprint": row[
                "source_launch_fingerprint"
            ],
            "source_slot_ordinal": row["source_slot_ordinal"],
            "source_attempt_id": row["source_attempt_id"],
            "input_fingerprint": row["input_fingerprint"],
            "extension_fingerprint": row["extension_fingerprint"],
            "provider_profile_fingerprint": row[
                "provider_profile_fingerprint"
            ],
            "requested_model_code": row["requested_model_code"],
            "prompt_contract_version": row[
                "prompt_contract_version"
            ],
            "decision_validator_version": row[
                "decision_validator_version"
            ],
            "prompt_fingerprint": row["prompt_fingerprint"],
            "agent_state_fingerprint": row[
                "agent_state_fingerprint"
            ],
            "prior_public_state_fingerprint": row[
                "prior_public_state_fingerprint"
            ],
            "decision_sha256": row["decision_sha256"],
            "returned_model_code": row["returned_model_code"],
            "provider_reported_tokens": row[
                "provider_reported_tokens"
            ],
            "risk_charged_tokens": row["risk_charged_tokens"],
        }
        if _sha256(entry_body) != row["entry_fingerprint"]:
            raise LiveSQLiteRepositoryError(
                "decision cache entry seal is invalid",
                code="live_sqlite_cache_invalid",
            )
        return ModelTurn(
            decision=decision,
            returned_model=row["returned_model_code"],
            prompt_tokens=integers["prompt_tokens"],
            completion_tokens=integers["completion_tokens"],
            total_tokens=integers["total_tokens"],
            provider_attempts=(1 if completed_replay else 0),
            format_repairs=integers["format_repairs"],
            contract_adjustments=tuple(
                envelope["contract_adjustments"]
            ),
            cache_hit=not completed_replay,
        )

    @staticmethod
    def _request_kind(
        format_repair_index: int,
        network_retry_index: int,
    ) -> str:
        if format_repair_index == 0 and network_retry_index == 0:
            return "initial"
        if format_repair_index == 0:
            return "network_retry"
        if network_retry_index == 0:
            return "format_repair"
        return "format_repair_network_retry"

    def _validated_request_candidates(
        self,
        connection: sqlite3.Connection,
        slot: sqlite3.Row,
        *,
        cache_key: str,
        request_candidates: tuple[AttemptRequestIdentity, ...],
    ) -> tuple[_RenderedIdentity, ...]:
        if (
            type(request_candidates) is not tuple
            or len(request_candidates)
            != self._context.format_repair_limit + 1
            or any(
                not isinstance(item, AttemptRequestIdentity)
                for item in request_candidates
            )
        ):
            raise LiveSQLiteRepositoryError(
                "caller request candidates are outside the frozen plan",
                code="live_sqlite_request_identity_invalid",
            )
        base = self._rendered_identity(connection, slot)
        if base.cache_key != cache_key:
            raise LiveSQLiteRepositoryError(
                "caller cache identity is invalid",
                code="live_sqlite_request_identity_invalid",
            )
        try:
            expected = build_attempt_request_identities(
                base.base_rendered,
                self._context,
            )
        except LiveExecutionError as exc:
            raise LiveSQLiteRepositoryError(
                "repository could not reconstruct request candidates",
                code="live_sqlite_request_identity_invalid",
            ) from exc
        if request_candidates != expected:
            raise LiveSQLiteRepositoryError(
                "caller request candidates disagree with frozen prompts",
                code="live_sqlite_request_identity_invalid",
            )
        rendered_candidates: list[_RenderedIdentity] = []
        for index, candidate in enumerate(expected):
            rendered = self._rendered_identity(
                connection,
                slot,
                format_repair_index=index,
            )
            if (
                candidate.format_repair_index != index
                or candidate.prompt_fingerprint
                != rendered.prompt_fingerprint
                or candidate.request_sha256 != rendered.request_sha256
                or rendered.cache_key != cache_key
            ):
                raise LiveSQLiteRepositoryError(
                    "repository request reconstruction is inconsistent",
                    code="live_sqlite_request_identity_invalid",
                )
            rendered_candidates.append(rendered)
        return tuple(rendered_candidates)

    def cache_or_reserve(
        self,
        *,
        slot: Mapping[str, Any],
        cache_key: str,
        request_candidates: tuple[AttemptRequestIdentity, ...],
    ) -> TurnAcquisition:
        _require_sha256(cache_key, "cache_key")
        try:
            with self.store._write_transaction() as connection:
                runtime = self._require_bound_runtime(
                    connection,
                    allowed_statuses=("running",),
                    require_unexpired=True,
                )
                self._recover_stale_attempts(connection, runtime)
                slot_row = self._require_slot(connection, slot)
                rendered_candidates = self._validated_request_candidates(
                    connection,
                    slot_row,
                    cache_key=cache_key,
                    request_candidates=request_candidates,
                )
                base_rendered = rendered_candidates[0]
                cache = self._cache_row(connection, cache_key)
                if cache is not None:
                    completed_replay = slot_row["status"] == "completed"
                    if not completed_replay:
                        self._assert_new_attempt_allowed(runtime)
                    return TurnAcquisition(
                        status=(
                            "completed_replay"
                            if completed_replay
                            else "cache_hit"
                        ),
                        cache_key=cache_key,
                        cached_turn=self._turn_from_cache(
                            cache,
                            base_rendered,
                            runtime,
                            completed_replay=completed_replay,
                        ),
                    )
                self._assert_new_attempt_allowed(runtime)
                return self._reserve_attempt(
                    connection,
                    runtime,
                    slot_row,
                    rendered_candidates,
                    request_candidates,
                )
        except sqlite3.IntegrityError as exc:
            raise LiveSQLiteRepositoryError(
                "attempt reservation conflicted atomically",
                code="live_sqlite_reservation_conflict",
            ) from exc

    def _reserve_attempt(
        self,
        connection: sqlite3.Connection,
        runtime: Mapping[str, Any],
        slot: sqlite3.Row,
        rendered_candidates: tuple[_RenderedIdentity, ...],
        request_candidates: tuple[AttemptRequestIdentity, ...],
    ) -> TurnAcquisition:
        identity = self.identity
        base_rendered = rendered_candidates[0]
        if slot["status"] == "completed":
            raise LiveSQLiteRepositoryError(
                "completed slot has no matching durable cache entry",
                code="live_sqlite_cache_missing",
            )
        if slot["status"] in {"failed", "cancelled"}:
            raise LiveSQLiteRepositoryError(
                "terminal slot cannot reserve a provider attempt",
                code="live_sqlite_slot_terminal",
            )
        history = connection.execute(
            """
            SELECT *
            FROM run_live_provider_attempts
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
              AND launch_fingerprint = ? AND slot_ordinal = ?
            ORDER BY attempt_number
            """,
            (
                identity.tenant_id,
                identity.project_id,
                identity.run_id,
                identity.launch_fingerprint,
                slot["ordinal"],
            ),
        ).fetchall()
        active = [
            attempt
            for attempt in history
            if attempt["attempt_status"] in {"reserved", "invoked"}
        ]
        if len(active) > 1:
            raise LiveSQLiteRepositoryError(
                "slot has multiple active provider attempts",
                code="live_sqlite_reservation_invalid",
            )
        next_coordinate = (0, 0)
        for position, attempt in enumerate(history, start=1):
            format_index = attempt["format_repair_index"]
            network_index = attempt["network_retry_index"]
            if (
                attempt["attempt_number"] != position
                or (format_index, network_index) != next_coordinate
                or attempt["transport_provenance"]
                != self.execution_provenance
                or attempt["transport_config_fingerprint"]
                != identity.transport_config_fingerprint
                or not 0 <= format_index < len(rendered_candidates)
                or not 0
                <= network_index
                <= runtime["network_retry_limit"]
            ):
                raise LiveSQLiteRepositoryError(
                    "attempt history violates the frozen retry graph",
                    code="live_sqlite_reservation_invalid",
                )
            rendered = rendered_candidates[format_index]
            candidate = request_candidates[format_index]
            if (
                attempt["canonical_request_sha256"]
                != candidate.request_sha256
                or attempt["prompt_fingerprint"]
                != candidate.prompt_fingerprint
                or attempt["decision_cache_key"]
                != base_rendered.cache_key
                or attempt["token_risk_reservation"]
                != candidate.risk_tokens
                or attempt["request_kind"]
                != self._request_kind(format_index, network_index)
                or rendered.request_sha256 != candidate.request_sha256
            ):
                raise LiveSQLiteRepositoryError(
                    "attempt history conflicts with request identities",
                    code="live_sqlite_reservation_invalid",
                )
            if attempt["attempt_status"] != "finalized":
                if position != len(history):
                    raise LiveSQLiteRepositoryError(
                        "active attempt is not last in its slot history",
                        code="live_sqlite_reservation_invalid",
                    )
                continue
            outcome = attempt["outcome_code"]
            if outcome == "reported_error":
                next_coordinate = (format_index + 1, 0)
            elif outcome in {
                "reported_success",
                "timeout_unknown",
                "transport_unknown",
                "worker_crash_unknown",
                "pre_send_failed",
            }:
                next_coordinate = (format_index, network_index + 1)
            else:
                raise LiveSQLiteRepositoryError(
                    "finalized attempt has no retry transition",
                    code="live_sqlite_reservation_invalid",
                )
        if active:
            attempt = active[0]
            format_index = attempt["format_repair_index"]
            network_index = attempt["network_retry_index"]
            rendered = rendered_candidates[format_index]
            candidate = request_candidates[format_index]
            if (
                attempt["lease_id"] != identity.lease_id
                or attempt["lease_generation"] != identity.lease_generation
                or attempt["attempt_status"] != "reserved"
                or attempt["canonical_request_sha256"]
                != candidate.request_sha256
                or attempt["token_risk_reservation"]
                != candidate.risk_tokens
            ):
                raise LiveSQLiteRepositoryError(
                    "active attempt is not a replayable reservation",
                    code="live_sqlite_reservation_busy",
                )
            return TurnAcquisition(
                status="reserved",
                cache_key=base_rendered.cache_key,
                attempt_id=attempt["attempt_id"],
                request_occurrence=attempt["request_occurrence"],
                risk_tokens=attempt["token_risk_reservation"],
                request_kind=attempt["request_kind"],
                format_repair_index=format_index,
                network_retry_index=network_index,
                request_sha256=rendered.request_sha256,
                prompt_fingerprint=rendered.prompt_fingerprint,
            )
        format_index, network_index = next_coordinate
        if format_index >= len(rendered_candidates):
            raise LiveSQLiteRepositoryError(
                "slot format-repair budget is exhausted",
                code="live_sqlite_format_repair_exhausted",
            )
        if network_index > runtime["network_retry_limit"]:
            raise LiveSQLiteRepositoryError(
                "slot network-retry budget is exhausted",
                code="live_sqlite_retry_exhausted",
            )
        rendered = rendered_candidates[format_index]
        candidate = request_candidates[format_index]
        risk_tokens = candidate.risk_tokens
        competing = connection.execute(
            """
            SELECT slot_ordinal
            FROM run_live_provider_attempts
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
              AND launch_fingerprint = ?
              AND decision_cache_key = ?
              AND attempt_status IN ('reserved', 'invoked')
            LIMIT 1
            """,
            (
                identity.tenant_id,
                identity.project_id,
                identity.run_id,
                identity.launch_fingerprint,
                base_rendered.cache_key,
            ),
        ).fetchone()
        if competing is not None:
            raise LiveSQLiteRepositoryError(
                "another slot owns the in-flight cache identity",
                code="live_sqlite_cache_busy",
            )
        usage = connection.execute(
            """
            SELECT *
            FROM run_live_usage
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
              AND launch_fingerprint = ?
            """,
            (
                identity.tenant_id,
                identity.project_id,
                identity.run_id,
                identity.launch_fingerprint,
            ),
        ).fetchone()
        if usage is None:
            raise LiveSQLiteRepositoryError(
                "live usage row is unavailable",
                code="live_sqlite_usage_invalid",
            )
        if (
            usage["provider_calls_reserved"]
            + usage["provider_calls_completed"]
            >= usage["effective_provider_call_limit"]
            or usage["tokens_in_flight"]
            + usage["provider_reported_tokens"]
            + usage["risk_charged_tokens"]
            + risk_tokens
            > usage["effective_token_limit"]
            or usage["active_attempts"]
            >= usage["effective_concurrency_limit"]
        ):
            raise LiveSQLiteRepositoryError(
                "provider call, token or concurrency budget is exhausted",
                code="live_sqlite_budget_exhausted",
            )
        attempt_number = len(history) + 1
        occurrence_row = connection.execute(
            """
            SELECT MAX(request_occurrence) AS occurrence
            FROM run_live_provider_attempts
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
              AND launch_fingerprint = ?
              AND canonical_request_sha256 = ?
            """,
            (
                identity.tenant_id,
                identity.project_id,
                identity.run_id,
                identity.launch_fingerprint,
                candidate.request_sha256,
            ),
        ).fetchone()
        occurrence = int(occurrence_row["occurrence"] or 0) + 1
        request_kind = self._request_kind(
            format_index,
            network_index,
        )
        seed_body = {
            "tenant_id": identity.tenant_id,
            "project_id": identity.project_id,
            "run_id": identity.run_id,
            "launch_fingerprint": identity.launch_fingerprint,
            "slot_ordinal": slot["ordinal"],
            "attempt_number": attempt_number,
            "canonical_request_sha256": candidate.request_sha256,
            "request_occurrence": occurrence,
            "lease_id": identity.lease_id,
            "lease_generation": identity.lease_generation,
        }
        attempt_id = "attempt_" + _sha256(seed_body)[:32]
        reservation_body = {
            **seed_body,
            "attempt_id": attempt_id,
            "transport_provenance": self.execution_provenance,
            "transport_config_fingerprint": (
                identity.transport_config_fingerprint
            ),
            "provider_profile_id": runtime["provider_profile_id"],
            "provider_profile_fingerprint": runtime[
                "provider_profile_fingerprint"
            ],
            "requested_model_code": self._context.model,
            "prompt_fingerprint": rendered.prompt_fingerprint,
            "decision_cache_key": base_rendered.cache_key,
            "request_kind": request_kind,
            "format_repair_index": format_index,
            "network_retry_index": network_index,
            "reservation_algorithm": runtime["reservation_algorithm"],
            "provider_call_reservation": 1,
            "token_risk_reservation": risk_tokens,
        }
        now = self._database_now(connection)
        connection.execute(
            """
            INSERT INTO run_live_provider_attempts(
                attempt_id, tenant_id, project_id, run_id,
                launch_fingerprint, slot_ordinal, attempt_number,
                attempt_schema_version, transport_provenance,
                transport_config_fingerprint, provider_profile_id,
                provider_profile_fingerprint, requested_model_code,
                prompt_fingerprint, decision_cache_key,
                canonical_request_sha256, request_occurrence,
                request_kind, format_repair_index, network_retry_index,
                reservation_algorithm, provider_call_reservation,
                token_risk_reservation, lease_id, lease_generation,
                attempt_status, transport_invoked, outcome_code,
                returned_model_code, provider_reported_tokens,
                risk_charged_tokens, reservation_fingerprint,
                final_fingerprint, attempt_version, reserved_at,
                transport_invoked_at, finalized_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, 1, ?, ?, ?, 'reserved', 0, NULL, NULL, NULL,
                NULL, ?, NULL, 1, ?, NULL, NULL, ?
            )
            """,
            (
                attempt_id,
                identity.tenant_id,
                identity.project_id,
                identity.run_id,
                identity.launch_fingerprint,
                slot["ordinal"],
                attempt_number,
                LIVE_ATTEMPT_SCHEMA_VERSION,
                self.execution_provenance,
                identity.transport_config_fingerprint,
                runtime["provider_profile_id"],
                runtime["provider_profile_fingerprint"],
                self._context.model,
                rendered.prompt_fingerprint,
                base_rendered.cache_key,
                candidate.request_sha256,
                occurrence,
                request_kind,
                format_index,
                network_index,
                runtime["reservation_algorithm"],
                risk_tokens,
                identity.lease_id,
                identity.lease_generation,
                _sha256(reservation_body),
                now,
                now,
            ),
        )
        updated_slot = connection.execute(
            """
            UPDATE run_live_turn_slots
            SET status = 'inflight',
                attempt_number = ?,
                prompt_fingerprint = ?,
                decision_cache_key = ?,
                lease_id = ?,
                lease_generation = ?,
                slot_version = slot_version + 1,
                inflight_at = ?,
                completed_at = NULL,
                updated_at = ?
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
              AND launch_fingerprint = ? AND ordinal = ?
              AND status IN ('pending', 'inflight')
              AND slot_version = ?
            """,
            (
                attempt_number,
                rendered.prompt_fingerprint,
                base_rendered.cache_key,
                identity.lease_id,
                identity.lease_generation,
                now,
                now,
                identity.tenant_id,
                identity.project_id,
                identity.run_id,
                identity.launch_fingerprint,
                slot["ordinal"],
                slot["slot_version"],
            ),
        )
        updated_usage = connection.execute(
            """
            UPDATE run_live_usage
            SET provider_calls_reserved = provider_calls_reserved + 1,
                tokens_in_flight = tokens_in_flight + ?,
                active_attempts = active_attempts + 1,
                peak_concurrency = max(
                    peak_concurrency,
                    active_attempts + 1
                ),
                usage_version = usage_version + 1,
                updated_at = ?
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
              AND launch_fingerprint = ? AND usage_version = ?
              AND execution_provenance = ?
              AND authorization_id IS ?
              AND authorization_fingerprint IS ?
            """,
            (
                risk_tokens,
                now,
                identity.tenant_id,
                identity.project_id,
                identity.run_id,
                identity.launch_fingerprint,
                usage["usage_version"],
                self.execution_provenance,
                self.authorization_id,
                self.authorization_fingerprint,
            ),
        )
        if updated_slot.rowcount != 1 or updated_usage.rowcount != 1:
            raise LiveSQLiteRepositoryError(
                "reservation lost its slot or usage compare-and-swap",
                code="live_sqlite_reservation_conflict",
            )
        return TurnAcquisition(
            status="reserved",
            cache_key=base_rendered.cache_key,
            attempt_id=attempt_id,
            request_occurrence=occurrence,
            risk_tokens=risk_tokens,
            request_kind=request_kind,
            format_repair_index=format_index,
            network_retry_index=network_index,
            request_sha256=candidate.request_sha256,
            prompt_fingerprint=candidate.prompt_fingerprint,
        )

    def mark_invoked(
        self,
        *,
        slot: Mapping[str, Any],
        attempt_id: str,
        request_sha256: str,
        request_occurrence: int,
    ) -> None:
        _require_identifier(attempt_id, "attempt_id")
        _require_sha256(request_sha256, "request_sha256")
        occurrence = _strict_integer(
            request_occurrence,
            "request_occurrence",
            minimum=1,
        )
        with self.store._write_transaction() as connection:
            runtime = self._require_bound_runtime(
                connection,
                allowed_statuses=("running",),
                require_unexpired=True,
            )
            self._assert_new_attempt_allowed(runtime)
            slot_row = self._require_slot(connection, slot)
            attempt = connection.execute(
                """
                SELECT *
                FROM run_live_provider_attempts
                WHERE attempt_id = ?
                  AND tenant_id = ? AND project_id = ? AND run_id = ?
                  AND launch_fingerprint = ? AND slot_ordinal = ?
                  AND lease_id = ? AND lease_generation = ?
                """,
                (
                    attempt_id,
                    self.identity.tenant_id,
                    self.identity.project_id,
                    self.identity.run_id,
                    self.identity.launch_fingerprint,
                    slot_row["ordinal"],
                    self.identity.lease_id,
                    self.identity.lease_generation,
                ),
            ).fetchone()
            if (
                attempt is None
                or attempt["canonical_request_sha256"] != request_sha256
                or attempt["request_occurrence"] != occurrence
                or attempt["transport_provenance"]
                != self.execution_provenance
                or attempt["transport_config_fingerprint"]
                != self.identity.transport_config_fingerprint
            ):
                raise LiveSQLiteRepositoryError(
                    "provider invocation identity is invalid",
                    code="live_sqlite_attempt_identity_invalid",
                )
            if attempt["attempt_status"] == "invoked":
                return
            if attempt["attempt_status"] != "reserved":
                raise LiveSQLiteRepositoryError(
                    "only a reserved attempt can be invoked",
                    code="live_sqlite_attempt_transition_invalid",
                )
            now = self._database_now(connection)
            updated = connection.execute(
                """
                UPDATE run_live_provider_attempts
                SET attempt_status = 'invoked',
                    transport_invoked = 1,
                    attempt_version = attempt_version + 1,
                    transport_invoked_at = ?,
                    updated_at = ?
                WHERE attempt_id = ?
                  AND attempt_status = 'reserved'
                  AND attempt_version = ?
                  AND tenant_id = ? AND project_id = ? AND run_id = ?
                  AND launch_fingerprint = ? AND slot_ordinal = ?
                  AND lease_id = ? AND lease_generation = ?
                """,
                (
                    now,
                    now,
                    attempt_id,
                    attempt["attempt_version"],
                    self.identity.tenant_id,
                    self.identity.project_id,
                    self.identity.run_id,
                    self.identity.launch_fingerprint,
                    slot_row["ordinal"],
                    self.identity.lease_id,
                    self.identity.lease_generation,
                ),
            )
            if updated.rowcount != 1:
                raise LiveSQLiteRepositoryError(
                    "provider invocation lost its compare-and-swap",
                    code="live_sqlite_attempt_transition_conflict",
                )

    @staticmethod
    def _settlement_values(
        attempt: Mapping[str, Any],
        settlement: AttemptSettlement,
    ) -> tuple[str, str | None, int, int]:
        outcomes = {
            "success": "reported_success",
            "format_or_contract_error": "reported_error",
            "timeout_unknown": "timeout_unknown",
            "connection_unknown": "transport_unknown",
            "pre_send_failed": "pre_send_failed",
        }
        outcome = outcomes.get(settlement.outcome)
        if outcome is None:
            raise LiveSQLiteRepositoryError(
                "attempt settlement outcome is unsupported",
                code="live_sqlite_settlement_invalid",
            )
        integer_values = (
            settlement.prompt_tokens,
            settlement.completion_tokens,
            settlement.total_tokens,
        )
        if (
            any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in integer_values
            )
            or any(value < 0 for value in integer_values)
            or settlement.prompt_tokens + settlement.completion_tokens
            != settlement.total_tokens
        ):
            raise LiveSQLiteRepositoryError(
                "attempt settlement usage is invalid",
                code="live_sqlite_settlement_invalid",
            )
        if settlement.response_sha256 is not None:
            _require_sha256(
                settlement.response_sha256,
                "response_sha256",
            )
        returned_model = settlement.returned_model
        if returned_model is not None:
            _require_identifier(returned_model, "returned_model")
        risk = attempt["token_risk_reservation"]
        if settlement.total_tokens > risk:
            raise LiveSQLiteRepositoryError(
                "reported usage exceeds the conservative reservation",
                code="live_sqlite_settlement_invalid",
            )
        if outcome == "reported_success":
            if (
                returned_model is None
                or settlement.response_sha256 is None
                or settlement.total_tokens < 1
            ):
                raise LiveSQLiteRepositoryError(
                    "successful settlement lacks validated metadata",
                    code="live_sqlite_settlement_invalid",
                )
            return outcome, returned_model, settlement.total_tokens, 0
        if outcome == "reported_error":
            reported = settlement.total_tokens
            if reported > 0 and (
                returned_model is None
                or settlement.response_sha256 is None
            ):
                raise LiveSQLiteRepositoryError(
                    "reported error usage lacks validated metadata",
                    code="live_sqlite_settlement_invalid",
                )
            return (
                outcome,
                returned_model,
                reported,
                0 if reported > 0 else risk,
            )
        if outcome in {
            "timeout_unknown",
            "transport_unknown",
        }:
            if any(integer_values) or returned_model is not None:
                raise LiveSQLiteRepositoryError(
                    "unknown settlement cannot claim provider usage",
                    code="live_sqlite_settlement_invalid",
                )
            return outcome, None, 0, risk
        if (
            any(integer_values)
            or returned_model is not None
            or settlement.response_sha256 is not None
        ):
            raise LiveSQLiteRepositoryError(
                "pre-send failure cannot contain provider output",
                code="live_sqlite_settlement_invalid",
            )
        return outcome, None, 0, 0

    def settle_attempt(
        self,
        *,
        slot: Mapping[str, Any],
        attempt_id: str,
        settlement: AttemptSettlement,
    ) -> None:
        _require_identifier(attempt_id, "attempt_id")
        if not isinstance(settlement, AttemptSettlement):
            raise LiveSQLiteRepositoryError(
                "attempt settlement is invalid",
                code="live_sqlite_settlement_invalid",
            )
        _require_sha256(settlement.request_sha256, "request_sha256")
        with self.store._write_transaction() as connection:
            runtime = self._require_bound_runtime(
                connection,
                allowed_statuses=("running", "cancel_requested"),
                require_unexpired=True,
            )
            slot_row = self._require_slot(connection, slot)
            attempt = connection.execute(
                """
                SELECT *
                FROM run_live_provider_attempts
                WHERE attempt_id = ?
                  AND tenant_id = ? AND project_id = ? AND run_id = ?
                  AND launch_fingerprint = ? AND slot_ordinal = ?
                  AND lease_id = ? AND lease_generation = ?
                """,
                (
                    attempt_id,
                    self.identity.tenant_id,
                    self.identity.project_id,
                    self.identity.run_id,
                    self.identity.launch_fingerprint,
                    slot_row["ordinal"],
                    self.identity.lease_id,
                    self.identity.lease_generation,
                ),
            ).fetchone()
            if (
                attempt is None
                or attempt["canonical_request_sha256"]
                != settlement.request_sha256
                or attempt["transport_provenance"]
                != self.execution_provenance
                or attempt["transport_config_fingerprint"]
                != self.identity.transport_config_fingerprint
            ):
                raise LiveSQLiteRepositoryError(
                    "settlement attempt identity is invalid",
                    code="live_sqlite_attempt_identity_invalid",
                )
            (
                outcome,
                returned_model,
                reported_tokens,
                risk_tokens,
            ) = self._settlement_values(attempt, settlement)
            if returned_model is not None and (
                returned_model != self._context.model
            ):
                raise LiveSQLiteRepositoryError(
                    "settlement returned the wrong model",
                    code="live_sqlite_settlement_invalid",
                )
            final_body = self._attempt_final_body(
                attempt,
                outcome_code=outcome,
                returned_model_code=returned_model,
                provider_reported_tokens=reported_tokens,
                risk_charged_tokens=risk_tokens,
                response_sha256=settlement.response_sha256,
            )
            final_fingerprint = _sha256(final_body)
            if attempt["attempt_status"] == "finalized":
                if attempt["final_fingerprint"] != final_fingerprint:
                    raise LiveSQLiteRepositoryError(
                        "settlement replay conflicts with durable outcome",
                        code="live_sqlite_settlement_conflict",
                    )
                return
            pre_send = outcome == "pre_send_failed"
            expected_status = "reserved" if pre_send else "invoked"
            if attempt["attempt_status"] != expected_status:
                raise LiveSQLiteRepositoryError(
                    "attempt settlement transition is invalid",
                    code="live_sqlite_attempt_transition_invalid",
                )
            now = self._database_now(connection)
            updated_attempt = connection.execute(
                """
                UPDATE run_live_provider_attempts
                SET attempt_status = 'finalized',
                    outcome_code = ?,
                    returned_model_code = ?,
                    provider_reported_tokens = ?,
                    risk_charged_tokens = ?,
                    final_fingerprint = ?,
                    attempt_version = attempt_version + 1,
                    finalized_at = ?,
                    updated_at = ?
                WHERE attempt_id = ? AND attempt_status = ?
                  AND attempt_version = ?
                  AND tenant_id = ? AND project_id = ? AND run_id = ?
                  AND launch_fingerprint = ? AND slot_ordinal = ?
                  AND lease_id = ? AND lease_generation = ?
                """,
                (
                    outcome,
                    returned_model,
                    reported_tokens,
                    risk_tokens,
                    final_fingerprint,
                    now,
                    now,
                    attempt_id,
                    expected_status,
                    attempt["attempt_version"],
                    self.identity.tenant_id,
                    self.identity.project_id,
                    self.identity.run_id,
                    self.identity.launch_fingerprint,
                    slot_row["ordinal"],
                    self.identity.lease_id,
                    self.identity.lease_generation,
                ),
            )
            known = outcome in {"reported_success", "reported_error"}
            unknown = outcome in {
                "timeout_unknown",
                "transport_unknown",
            }
            updated_usage = connection.execute(
                """
                UPDATE run_live_usage
                SET provider_calls_reserved
                        = provider_calls_reserved - 1,
                    provider_calls_completed
                        = provider_calls_completed + ?,
                    provider_calls_known = provider_calls_known + ?,
                    provider_calls_unknown = provider_calls_unknown + ?,
                    tokens_in_flight = tokens_in_flight - ?,
                    provider_reported_tokens
                        = provider_reported_tokens + ?,
                    risk_charged_tokens = risk_charged_tokens + ?,
                    active_attempts = active_attempts - 1,
                    usage_version = usage_version + 1,
                    updated_at = ?
                WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                  AND launch_fingerprint = ? AND usage_version = ?
                  AND provider_calls_reserved > 0
                  AND active_attempts > 0
                  AND tokens_in_flight >= ?
                  AND execution_provenance = ?
                  AND authorization_id IS ?
                  AND authorization_fingerprint IS ?
                """,
                (
                    int(not pre_send),
                    int(known),
                    int(unknown),
                    attempt["token_risk_reservation"],
                    reported_tokens,
                    risk_tokens,
                    now,
                    self.identity.tenant_id,
                    self.identity.project_id,
                    self.identity.run_id,
                    self.identity.launch_fingerprint,
                    runtime["usage_version"],
                    attempt["token_risk_reservation"],
                    self.execution_provenance,
                    self.authorization_id,
                    self.authorization_fingerprint,
                ),
            )
            updated_slot_count = 1
            if pre_send or unknown or outcome == "reported_error":
                status = (
                    "pending"
                    if self._retry_available(
                        runtime,
                        attempt,
                        outcome_code=outcome,
                    )
                    else "failed"
                )
                updated_slot_count = connection.execute(
                    """
                    UPDATE run_live_turn_slots
                    SET status = ?,
                        prompt_fingerprint = CASE
                            WHEN ? = 'pending' THEN NULL
                            ELSE prompt_fingerprint
                        END,
                        decision_cache_key = CASE
                            WHEN ? = 'pending' THEN NULL
                            ELSE decision_cache_key
                        END,
                        lease_id = CASE
                            WHEN ? = 'pending' THEN NULL
                            ELSE lease_id
                        END,
                        lease_generation = CASE
                            WHEN ? = 'pending' THEN NULL
                            ELSE lease_generation
                        END,
                        slot_version = slot_version + 1,
                        inflight_at = CASE
                            WHEN ? = 'pending' THEN NULL
                            ELSE inflight_at
                        END,
                        updated_at = ?
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                      AND launch_fingerprint = ? AND ordinal = ?
                      AND status = 'inflight'
                      AND lease_id = ? AND lease_generation = ?
                    """,
                    (
                        status,
                        status,
                        status,
                        status,
                        status,
                        status,
                        now,
                        self.identity.tenant_id,
                        self.identity.project_id,
                        self.identity.run_id,
                        self.identity.launch_fingerprint,
                        slot_row["ordinal"],
                        self.identity.lease_id,
                        self.identity.lease_generation,
                    ),
                ).rowcount
            if (
                updated_attempt.rowcount != 1
                or updated_usage.rowcount != 1
                or updated_slot_count != 1
            ):
                raise LiveSQLiteRepositoryError(
                    "attempt settlement lost an atomic compare-and-swap",
                    code="live_sqlite_settlement_conflict",
                )

    @staticmethod
    def _turn_envelope(turn: ModelTurn) -> dict[str, Any]:
        if not isinstance(turn, ModelTurn):
            raise LiveSQLiteRepositoryError(
                "committed turn is invalid",
                code="live_sqlite_turn_invalid",
            )
        envelope = {
            "decision": asdict(turn.decision),
            "prompt_tokens": turn.prompt_tokens,
            "completion_tokens": turn.completion_tokens,
            "total_tokens": turn.total_tokens,
            "format_repairs": turn.format_repairs,
            "contract_adjustments": list(turn.contract_adjustments),
        }
        _scan_safe_json(envelope)
        _canonical_json(envelope)
        return envelope

    def _cache_entry_body(
        self,
        *,
        cache_key: str,
        source_slot_ordinal: int,
        source_attempt_id: str,
        runtime: Mapping[str, Any],
        rendered: _RenderedIdentity,
        decision_sha256: str,
        returned_model_code: str,
        provider_reported_tokens: int,
        risk_charged_tokens: int,
    ) -> dict[str, Any]:
        identity = self.identity
        return {
            "tenant_id": identity.tenant_id,
            "cache_key": cache_key,
            "cache_schema_version": LIVE_CACHE_SCHEMA_VERSION,
            "transport_provenance": self.execution_provenance,
            "transport_config_fingerprint": (
                identity.transport_config_fingerprint
            ),
            "source_project_id": identity.project_id,
            "source_run_id": identity.run_id,
            "source_launch_fingerprint": identity.launch_fingerprint,
            "source_slot_ordinal": source_slot_ordinal,
            "source_attempt_id": source_attempt_id,
            "input_fingerprint": runtime["input_fingerprint"],
            "extension_fingerprint": runtime["extension_fingerprint"],
            "provider_profile_fingerprint": runtime[
                "provider_profile_fingerprint"
            ],
            "requested_model_code": self._context.model,
            "prompt_contract_version": runtime[
                "prompt_contract_version"
            ],
            "decision_validator_version": runtime[
                "decision_validator_version"
            ],
            "prompt_fingerprint": rendered.base_prompt_fingerprint,
            "agent_state_fingerprint": (
                rendered.agent_state_fingerprint
            ),
            "prior_public_state_fingerprint": (
                rendered.prior_public_fingerprint
            ),
            "decision_sha256": decision_sha256,
            "returned_model_code": returned_model_code,
            "provider_reported_tokens": provider_reported_tokens,
            "risk_charged_tokens": risk_charged_tokens,
        }

    def _insert_or_validate_cache(
        self,
        connection: sqlite3.Connection,
        *,
        cache_key: str,
        slot_row: Mapping[str, Any],
        attempt: sqlite3.Row,
        runtime: Mapping[str, Any],
        rendered: _RenderedIdentity,
        turn: ModelTurn,
        now: str,
    ) -> sqlite3.Row:
        envelope = self._turn_envelope(turn)
        decision_json = _canonical_json(envelope)
        decision_sha = _sha256(envelope)
        if (
            attempt["attempt_status"] != "finalized"
            or attempt["outcome_code"] != "reported_success"
            or attempt["transport_provenance"]
            != self.execution_provenance
            or attempt["transport_config_fingerprint"]
            != self.identity.transport_config_fingerprint
            or attempt["returned_model_code"] != turn.returned_model
            or attempt["provider_reported_tokens"] != turn.total_tokens
            or attempt["risk_charged_tokens"] != 0
            or attempt["decision_cache_key"] != cache_key
            or attempt["prompt_fingerprint"]
            != rendered.prompt_fingerprint
        ):
            raise LiveSQLiteRepositoryError(
                "successful turn conflicts with its durable settlement",
                code="live_sqlite_turn_settlement_mismatch",
            )
        body = self._cache_entry_body(
            cache_key=cache_key,
            source_slot_ordinal=slot_row["ordinal"],
            source_attempt_id=attempt["attempt_id"],
            runtime=runtime,
            rendered=rendered,
            decision_sha256=decision_sha,
            returned_model_code=turn.returned_model,
            provider_reported_tokens=turn.total_tokens,
            risk_charged_tokens=0,
        )
        existing = self._cache_row(connection, cache_key)
        if existing is None:
            connection.execute(
                """
                INSERT INTO live_decision_cache(
                    tenant_id, cache_key, cache_schema_version,
                    transport_provenance,
                    transport_config_fingerprint,
                    source_project_id, source_run_id,
                    source_launch_fingerprint, source_slot_ordinal,
                    source_attempt_id, input_fingerprint,
                    extension_fingerprint,
                    provider_profile_fingerprint,
                    requested_model_code, prompt_contract_version,
                    decision_validator_version, prompt_fingerprint,
                    agent_state_fingerprint,
                    prior_public_state_fingerprint,
                    decision_json, decision_sha256,
                    returned_model_code, provider_reported_tokens,
                    risk_charged_tokens, entry_fingerprint, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    body["tenant_id"],
                    body["cache_key"],
                    body["cache_schema_version"],
                    body["transport_provenance"],
                    body["transport_config_fingerprint"],
                    body["source_project_id"],
                    body["source_run_id"],
                    body["source_launch_fingerprint"],
                    body["source_slot_ordinal"],
                    body["source_attempt_id"],
                    body["input_fingerprint"],
                    body["extension_fingerprint"],
                    body["provider_profile_fingerprint"],
                    body["requested_model_code"],
                    body["prompt_contract_version"],
                    body["decision_validator_version"],
                    body["prompt_fingerprint"],
                    body["agent_state_fingerprint"],
                    body["prior_public_state_fingerprint"],
                    decision_json,
                    body["decision_sha256"],
                    body["returned_model_code"],
                    body["provider_reported_tokens"],
                    body["risk_charged_tokens"],
                    _sha256(body),
                    now,
                ),
            )
            existing = self._cache_row(connection, cache_key)
        if existing is None:
            raise LiveSQLiteRepositoryError(
                "decision cache insertion was not durable",
                code="live_sqlite_cache_conflict",
            )
        self._turn_from_cache(existing, rendered, runtime)
        if existing["decision_json"] != decision_json:
            raise LiveSQLiteRepositoryError(
                "decision cache replay conflicts with the turn",
                code="live_sqlite_cache_conflict",
            )
        return existing

    def _phase_rows_for_slot(
        self,
        connection: sqlite3.Connection,
        slot_row: Mapping[str, Any],
    ) -> list[sqlite3.Row]:
        identity = self.identity
        rows = connection.execute(
            """
            SELECT *
            FROM run_live_turn_slots
            WHERE tenant_id = ? AND project_id = ? AND run_id = ?
              AND launch_fingerprint = ?
              AND seed = ? AND branch_id = ? AND phase_id = ?
            ORDER BY ordinal
            """,
            (
                identity.tenant_id,
                identity.project_id,
                identity.run_id,
                identity.launch_fingerprint,
                slot_row["seed"],
                slot_row["branch_id"],
                slot_row["phase_id"],
            ),
        ).fetchall()
        if not rows:
            raise LiveSQLiteRepositoryError(
                "turn slot has no durable phase",
                code="live_sqlite_phase_invalid",
            )
        return list(rows)

    def commit_slot(
        self,
        *,
        slot: Mapping[str, Any],
        cache_key: str,
        acquisition: TurnAcquisition,
        agent: AgentState,
        turn: ModelTurn,
        record: Mapping[str, Any],
    ) -> None:
        _require_sha256(cache_key, "cache_key")
        if (
            not isinstance(acquisition, TurnAcquisition)
            or acquisition.cache_key != cache_key
            or acquisition.status
            not in {"reserved", "cache_hit", "completed_replay"}
            or not isinstance(agent, AgentState)
            or not isinstance(record, Mapping)
        ):
            raise LiveSQLiteRepositoryError(
                "slot commit inputs are invalid",
                code="live_sqlite_slot_commit_invalid",
            )
        agent_payload = asdict(agent)
        record_payload = deepcopy(dict(record))
        _scan_safe_json(agent_payload)
        _scan_safe_json(record_payload)
        _canonical_json(agent_payload)
        _canonical_json(record_payload)
        try:
            with self.store._write_transaction() as connection:
                runtime = self._require_bound_runtime(
                    connection,
                    allowed_statuses=("running",),
                    require_unexpired=True,
                )
                slot_row = self._require_slot(connection, slot)
                rendered = self._rendered_identity(
                    connection,
                    slot_row,
                )
                if rendered.cache_key != cache_key:
                    raise LiveSQLiteRepositoryError(
                        "slot commit cache identity is invalid",
                        code="live_sqlite_request_identity_invalid",
                    )
                expected_transition = transition_agent_state(
                    deepcopy(rendered.agent),
                    turn,
                    deepcopy(rendered.rendered.activation),
                )
                if (
                    asdict(expected_transition.agent) != agent_payload
                    or expected_transition.record != record_payload
                    or expected_transition.turn != turn
                ):
                    raise LiveSQLiteRepositoryError(
                        "slot commit is not the qualified state transition",
                        code="live_sqlite_slot_commit_invalid",
                    )
                phase_rows = self._phase_rows_for_slot(
                    connection,
                    slot_row,
                )
                barrier = phase_rows[-1]["ordinal"]
                now = self._database_now(connection)
                if acquisition.status == "reserved":
                    if (
                        not acquisition.attempt_id
                        or acquisition.request_occurrence is None
                    ):
                        raise LiveSQLiteRepositoryError(
                            "reserved acquisition has no attempt identity",
                            code="live_sqlite_slot_commit_invalid",
                        )
                    attempt = connection.execute(
                        """
                        SELECT *
                        FROM run_live_provider_attempts
                        WHERE attempt_id = ?
                          AND tenant_id = ? AND project_id = ?
                          AND run_id = ? AND launch_fingerprint = ?
                          AND slot_ordinal = ?
                          AND lease_id = ? AND lease_generation = ?
                        """,
                        (
                            acquisition.attempt_id,
                            self.identity.tenant_id,
                            self.identity.project_id,
                            self.identity.run_id,
                            self.identity.launch_fingerprint,
                            slot_row["ordinal"],
                            self.identity.lease_id,
                            self.identity.lease_generation,
                        ),
                    ).fetchone()
                    if attempt is None:
                        raise LiveSQLiteRepositoryError(
                            "settled attempt is unavailable",
                            code="live_sqlite_attempt_identity_invalid",
                        )
                    attempt_rendered = self._rendered_identity(
                        connection,
                        slot_row,
                        format_repair_index=attempt[
                            "format_repair_index"
                        ],
                    )
                    if (
                        acquisition.request_kind
                        != attempt["request_kind"]
                        or acquisition.format_repair_index
                        != attempt["format_repair_index"]
                        or acquisition.network_retry_index
                        != attempt["network_retry_index"]
                        or acquisition.request_sha256
                        != attempt["canonical_request_sha256"]
                        or acquisition.prompt_fingerprint
                        != attempt["prompt_fingerprint"]
                        or acquisition.risk_tokens
                        != attempt["token_risk_reservation"]
                        or attempt_rendered.cache_key != cache_key
                    ):
                        raise LiveSQLiteRepositoryError(
                            "slot commit acquisition identity is invalid",
                            code="live_sqlite_attempt_identity_invalid",
                        )
                    self._insert_or_validate_cache(
                        connection,
                        cache_key=cache_key,
                        slot_row=slot_row,
                        attempt=attempt,
                        runtime=runtime,
                        rendered=attempt_rendered,
                        turn=turn,
                        now=now,
                    )
                    cache_hit_increment = 0
                else:
                    cache = self._cache_row(connection, cache_key)
                    if cache is None:
                        raise LiveSQLiteRepositoryError(
                            "cache-hit acquisition is not durable",
                            code="live_sqlite_cache_missing",
                        )
                    cached_turn = self._turn_from_cache(
                        cache,
                        rendered,
                        runtime,
                    )
                    if (
                        asdict(cached_turn.decision)
                        != asdict(turn.decision)
                        or cached_turn.returned_model
                        != turn.returned_model
                        or cached_turn.total_tokens != turn.total_tokens
                    ):
                        raise LiveSQLiteRepositoryError(
                            "cache-hit turn conflicts with durable cache",
                            code="live_sqlite_cache_conflict",
                        )
                    cache_hit_increment = int(
                        acquisition.status == "cache_hit"
                    )
                if slot_row["status"] == "completed":
                    if (
                        slot_row["decision_cache_key"] != cache_key
                        or slot_row["prompt_fingerprint"]
                        != rendered.base_prompt_fingerprint
                        or slot_row["checkpoint_ordinal"] != barrier
                    ):
                        raise LiveSQLiteRepositoryError(
                            "completed slot replay conflicts with durable state",
                            code="live_sqlite_slot_commit_conflict",
                        )
                    return
                expected_statuses = (
                    ("inflight",)
                    if acquisition.status == "reserved"
                    else (
                        ("completed",)
                        if acquisition.status == "completed_replay"
                        else ("pending", "inflight")
                    )
                )
                if slot_row["status"] not in expected_statuses:
                    raise LiveSQLiteRepositoryError(
                        "slot is not commit eligible",
                        code="live_sqlite_slot_commit_conflict",
                    )
                updated_slot = connection.execute(
                    """
                    UPDATE run_live_turn_slots
                    SET status = 'completed',
                        prompt_fingerprint = ?,
                        decision_cache_key = ?,
                        checkpoint_ordinal = ?,
                        lease_id = ?,
                        lease_generation = ?,
                        slot_version = slot_version + 1,
                        completed_at = ?,
                        updated_at = ?
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                      AND launch_fingerprint = ? AND ordinal = ?
                      AND status = ? AND slot_version = ?
                    """,
                    (
                        rendered.base_prompt_fingerprint,
                        cache_key,
                        barrier,
                        self.identity.lease_id,
                        self.identity.lease_generation,
                        now,
                        now,
                        self.identity.tenant_id,
                        self.identity.project_id,
                        self.identity.run_id,
                        self.identity.launch_fingerprint,
                        slot_row["ordinal"],
                        slot_row["status"],
                        slot_row["slot_version"],
                    ),
                )
                usage = connection.execute(
                    """
                    SELECT usage_version
                    FROM run_live_usage
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                      AND launch_fingerprint = ?
                    """,
                    (
                        self.identity.tenant_id,
                        self.identity.project_id,
                        self.identity.run_id,
                        self.identity.launch_fingerprint,
                    ),
                ).fetchone()
                updated_usage = connection.execute(
                    """
                    UPDATE run_live_usage
                    SET turns_completed = turns_completed + 1,
                        cache_hits = cache_hits + ?,
                        usage_version = usage_version + 1,
                        updated_at = ?
                    WHERE tenant_id = ? AND project_id = ? AND run_id = ?
                      AND launch_fingerprint = ? AND usage_version = ?
                      AND turns_completed < expected_agent_turns
                      AND execution_provenance = ?
                      AND authorization_id IS ?
                      AND authorization_fingerprint IS ?
                    """,
                    (
                        cache_hit_increment,
                        now,
                        self.identity.tenant_id,
                        self.identity.project_id,
                        self.identity.run_id,
                        self.identity.launch_fingerprint,
                        usage["usage_version"],
                        self.execution_provenance,
                        self.authorization_id,
                        self.authorization_fingerprint,
                    ),
                )
                if (
                    updated_slot.rowcount != 1
                    or updated_usage.rowcount != 1
                ):
                    raise LiveSQLiteRepositoryError(
                        "slot commit lost its atomic compare-and-swap",
                        code="live_sqlite_slot_commit_conflict",
                    )
        except sqlite3.IntegrityError as exc:
            raise LiveSQLiteRepositoryError(
                "slot and cache commit conflicted atomically",
                code="live_sqlite_slot_commit_conflict",
            ) from exc

    @staticmethod
    def _phase_fingerprint(
        phase: ReadyLivePhase,
        aggregate: Mapping[str, Any],
    ) -> str:
        return _sha256(
            {
                "phase_key": phase.phase_key,
                "seed": phase.seed,
                "branch_id": phase.branch_id,
                "phase_id": phase.phase_id,
                "slot_ordinals": [
                    item["ordinal"] for item in phase.slots
                ],
                "aggregate": aggregate,
            }
        )

    def commit_phase(
        self,
        *,
        phase: ReadyLivePhase,
        agents: Sequence[AgentState],
        records: Sequence[Mapping[str, Any]],
        aggregate: Mapping[str, Any],
        phase_fingerprint: str,
    ) -> None:
        if (
            not isinstance(phase, ReadyLivePhase)
            or not isinstance(agents, Sequence)
            or isinstance(agents, (str, bytes, bytearray))
            or not isinstance(records, Sequence)
            or isinstance(records, (str, bytes, bytearray))
            or not isinstance(aggregate, Mapping)
        ):
            raise LiveSQLiteRepositoryError(
                "phase commit inputs are invalid",
                code="live_sqlite_phase_commit_invalid",
            )
        _require_sha256(phase_fingerprint, "phase_fingerprint")
        agents_list = list(agents)
        records_list = [deepcopy(dict(item)) for item in records]
        aggregate_payload = deepcopy(dict(aggregate))
        if (
            len(agents_list) != len(phase.slots)
            or len(records_list) != len(phase.slots)
            or not all(isinstance(item, AgentState) for item in agents_list)
            or aggregate_payload.get("agent_decisions") != records_list
            or self._phase_fingerprint(phase, aggregate_payload)
            != phase_fingerprint
        ):
            raise LiveSQLiteRepositoryError(
                "phase commit does not match its executor seal",
                code="live_sqlite_phase_commit_invalid",
            )
        population_payload = {
            "agents": [
                {
                    "slot_ordinal": slot["ordinal"],
                    "state": asdict(agent),
                }
                for slot, agent in zip(
                    phase.slots,
                    agents_list,
                    strict=True,
                )
            ],
            "phase_fingerprint": phase_fingerprint,
        }
        _scan_safe_json(population_payload)
        _scan_safe_json(aggregate_payload)
        population_json = _canonical_json(population_payload)
        public_json = _canonical_json(aggregate_payload)
        population_sha = _sha256(population_payload)
        public_sha = _sha256(aggregate_payload)
        try:
            with self.store._write_transaction() as connection:
                self._require_bound_runtime(
                    connection,
                    allowed_statuses=("running",),
                    require_unexpired=True,
                )
                durable_slots = [
                    self._require_slot(connection, item)
                    for item in phase.slots
                ]
                if not durable_slots:
                    raise LiveSQLiteRepositoryError(
                        "phase has no durable slots",
                        code="live_sqlite_phase_commit_invalid",
                    )
                expected_key = (
                    phase.seed,
                    phase.branch_id,
                    phase.phase_id,
                )
                if any(
                    (
                        row["seed"],
                        row["branch_id"],
                        row["phase_id"],
                    )
                    != expected_key
                    for row in durable_slots
                ):
                    raise LiveSQLiteRepositoryError(
                        "phase slots cross a durable phase boundary",
                        code="live_sqlite_phase_commit_invalid",
                    )
                all_phase_rows = self._phase_rows_for_slot(
                    connection,
                    durable_slots[0],
                )
                if [
                    row["ordinal"] for row in all_phase_rows
                ] != [
                    row["ordinal"] for row in durable_slots
                ]:
                    raise LiveSQLiteRepositoryError(
                        "phase commit omits durable slots",
                        code="live_sqlite_phase_commit_invalid",
                    )
                barrier = durable_slots[-1]["ordinal"]
                if any(
                    row["status"] != "completed"
                    or row["checkpoint_ordinal"] != barrier
                    or row["decision_cache_key"] is None
                    for row in durable_slots
                ):
                    raise LiveSQLiteRepositoryError(
                        "phase checkpoint precedes complete durable slots",
                        code="live_sqlite_phase_barrier_invalid",
                    )
                requirements = {
                    row["required_checkpoint_ordinal"]
                    for row in durable_slots
                }
                if len(requirements) != 1:
                    raise LiveSQLiteRepositoryError(
                        "phase dependency barrier is inconsistent",
                        code="live_sqlite_phase_barrier_invalid",
                    )
                required = next(iter(requirements))
                if required is not None and (
                    self._checkpoint_row(connection, required) is None
                ):
                    raise LiveSQLiteRepositoryError(
                        "required phase checkpoint is unavailable",
                        code="live_sqlite_phase_barrier_missing",
                    )
                self._ensure_initial_checkpoint(connection)
                previous = self._validate_checkpoint_chain(connection)
                if previous["checkpoint_ordinal"] >= barrier:
                    raise LiveSQLiteRepositoryError(
                        "phase checkpoint order is not append-only",
                        code="live_sqlite_phase_commit_conflict",
                    )
                previous_sha = previous["checkpoint_sha256"]
                body = {
                    "tenant_id": self.identity.tenant_id,
                    "project_id": self.identity.project_id,
                    "run_id": self.identity.run_id,
                    "launch_fingerprint": (
                        self.identity.launch_fingerprint
                    ),
                    "checkpoint_ordinal": barrier,
                    "checkpoint_schema_version": (
                        LIVE_CHECKPOINT_SCHEMA_VERSION
                    ),
                    "checkpoint_kind": "phase_complete",
                    "seed": phase.seed,
                    "branch_id": phase.branch_id,
                    "phase_id": phase.phase_id,
                    "last_completed_ordinal": barrier,
                    "population_state_sha256": population_sha,
                    "public_state_sha256": public_sha,
                    "decision_cache_key": None,
                    "previous_checkpoint_sha256": previous_sha,
                    "lease_id": self.identity.lease_id,
                    "lease_generation": self.identity.lease_generation,
                }
                checkpoint_sha = _sha256(body)
                existing = self._checkpoint_row(connection, barrier)
                if existing is not None:
                    if (
                        existing["population_state_json"]
                        != population_json
                        or existing["public_state_json"] != public_json
                        or existing["checkpoint_sha256"]
                        != checkpoint_sha
                    ):
                        raise LiveSQLiteRepositoryError(
                            "phase checkpoint replay conflicts",
                            code="live_sqlite_phase_commit_conflict",
                        )
                    return
                now = self._database_now(connection)
                connection.execute(
                    """
                    INSERT INTO run_live_checkpoints(
                        tenant_id, project_id, run_id,
                        launch_fingerprint, checkpoint_ordinal,
                        checkpoint_schema_version, checkpoint_kind,
                        seed, branch_id, phase_id,
                        last_completed_ordinal,
                        population_state_json, population_state_sha256,
                        public_state_json, public_state_sha256,
                        decision_cache_key, previous_checkpoint_sha256,
                        checkpoint_sha256, lease_id, lease_generation,
                        created_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, 'phase_complete', ?, ?, ?, ?,
                        ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        self.identity.tenant_id,
                        self.identity.project_id,
                        self.identity.run_id,
                        self.identity.launch_fingerprint,
                        barrier,
                        LIVE_CHECKPOINT_SCHEMA_VERSION,
                        phase.seed,
                        phase.branch_id,
                        phase.phase_id,
                        barrier,
                        population_json,
                        population_sha,
                        public_json,
                        public_sha,
                        previous_sha,
                        checkpoint_sha,
                        self.identity.lease_id,
                        self.identity.lease_generation,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise LiveSQLiteRepositoryError(
                "phase checkpoint conflicted atomically",
                code="live_sqlite_phase_commit_conflict",
            ) from exc

    def cancellation_requested(self) -> bool:
        with self._read_transaction() as connection:
            runtime = self._require_bound_runtime(
                connection,
                allowed_statuses=("running", "cancel_requested"),
                require_unexpired=True,
            )
            return (
                runtime["status"] == "cancel_requested"
                or (
                    self.identity.adapter_mode
                    == LIVE_AUTHORIZED_ADAPTER_MODE
                    and (
                        runtime["authorization_revoked_at"] is not None
                        or runtime["provider_profile_enabled"] != 1
                    )
                )
            )
