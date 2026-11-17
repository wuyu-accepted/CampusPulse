"""Body-free control probe for a claimed authorized-live execution.

This module is deliberately not a provider adapter.  It reads only sealed
runtime, usage, authorization and provider-profile control rows.  It never
reads a credential, prompt, persona, cache value or provider body and it has
no transport construction or network capability.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .live_runtime import (
    LIVE_AUTHORIZED_ADAPTER_MODE,
    LIVE_AUTHORIZED_EXECUTION_PROVENANCE,
)
from .store import WorkbenchStore


AUTHORIZED_LIVE_CACHE_NAMESPACE = LIVE_AUTHORIZED_EXECUTION_PROVENANCE

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AuthorizedLiveGuardError(RuntimeError):
    """Controlled rejection before an authorized provider attempt."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AuthorizedLiveClaimIdentity:
    """Exact control-plane identity bound to one claimed live launch."""

    tenant_id: str
    project_id: str
    run_id: str
    launch_fingerprint: str
    transport_config_fingerprint: str
    adapter_mode: str
    lease_id: str
    lease_generation: int
    authorization_id: str
    authorization_fingerprint: str


@dataclass(frozen=True)
class AuthorizedLiveControlState:
    """Safe result of one current-lease authorization probe."""

    cancellation_requested: bool
    reason_code: str | None
    runtime_status: str
    usage_version: int
    authorization_lifecycle_version: int
    effective_provider_call_limit: int
    effective_token_limit: int


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise AuthorizedLiveGuardError(
            f"{field} is not a controlled identifier",
            code="authorized_live_identity_invalid",
        )
    return value


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise AuthorizedLiveGuardError(
            f"{field} is not a SHA-256 identity",
            code="authorized_live_identity_invalid",
        )
    return value


def _integer(value: Any, field: str, *, minimum: int = 0) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < minimum
    ):
        raise AuthorizedLiveGuardError(
            f"{field} is not a controlled integer",
            code="authorized_live_identity_invalid",
        )
    return value


class AuthorizedLiveExecutionGuard:
    """Revalidate lease, authorization and caps between bounded live slots."""

    cache_namespace = AUTHORIZED_LIVE_CACHE_NAMESPACE
    network_enabled = False
    provider_enabled = False

    def __init__(
        self,
        store: WorkbenchStore,
        identity: AuthorizedLiveClaimIdentity,
    ) -> None:
        if not isinstance(store, WorkbenchStore):
            raise TypeError("store must be a WorkbenchStore")
        if not isinstance(identity, AuthorizedLiveClaimIdentity):
            raise TypeError(
                "identity must be an AuthorizedLiveClaimIdentity"
            )
        for field in (
            "tenant_id",
            "project_id",
            "run_id",
            "lease_id",
            "authorization_id",
        ):
            _identifier(getattr(identity, field), field)
        _sha256(identity.launch_fingerprint, "launch_fingerprint")
        _sha256(
            identity.transport_config_fingerprint,
            "transport_config_fingerprint",
        )
        _sha256(
            identity.authorization_fingerprint,
            "authorization_fingerprint",
        )
        _integer(
            identity.lease_generation,
            "lease_generation",
            minimum=1,
        )
        if identity.adapter_mode != LIVE_AUTHORIZED_ADAPTER_MODE:
            raise AuthorizedLiveGuardError(
                "claim is not bound to the authorized-live adapter",
                code="authorized_live_identity_invalid",
            )
        self.store = store
        self.identity = identity

    def _row(self, connection) -> Mapping[str, Any]:
        identity = self.identity
        row = connection.execute(
            """
            SELECT
                rr.status AS runtime_status,
                rr.input_fingerprint,
                rr.lease_expires_at,
                u.usage_version,
                u.execution_provenance,
                u.transport_config_fingerprint,
                u.authorization_id AS usage_authorization_id,
                u.authorization_fingerprint
                    AS usage_authorization_fingerprint,
                u.authorization_provider_call_cap,
                u.authorization_token_cap,
                u.effective_provider_call_limit,
                u.effective_token_limit,
                u.provider_calls_reserved,
                u.provider_calls_completed,
                u.tokens_in_flight,
                u.provider_reported_tokens,
                u.risk_charged_tokens,
                a.authorization_fingerprint
                    AS row_authorization_fingerprint,
                a.provider_call_cap,
                a.token_cap,
                a.lifecycle_version
                    AS authorization_lifecycle_version,
                a.consumed_at,
                a.consumed_runtime_fingerprint,
                a.consumed_plan_fingerprint,
                a.revoked_at,
                ps.enabled AS provider_profile_enabled
            FROM run_runtime_states AS rr
            JOIN run_live_usage AS u
              ON u.tenant_id = rr.tenant_id
             AND u.project_id = rr.project_id
             AND u.run_id = rr.run_id
             AND u.launch_fingerprint = rr.launch_fingerprint
            JOIN run_live_authorizations AS a
              ON a.tenant_id = u.tenant_id
             AND a.project_id = u.project_id
             AND a.run_id = u.run_id
             AND a.authorization_id = u.authorization_id
             AND a.authorization_fingerprint
                    = u.authorization_fingerprint
            JOIN run_live_plan_extensions AS e
              ON e.tenant_id = rr.tenant_id
             AND e.project_id = rr.project_id
             AND e.run_id = rr.run_id
             AND e.input_fingerprint = rr.input_fingerprint
             AND e.extension_fingerprint = u.extension_fingerprint
            JOIN live_provider_profile_states AS ps
              ON ps.provider_profile_id = e.provider_profile_id
            WHERE rr.tenant_id = ? AND rr.project_id = ?
              AND rr.run_id = ? AND rr.launch_fingerprint = ?
              AND rr.adapter_mode = ?
              AND rr.lease_id = ? AND rr.lease_generation = ?
            """,
            (
                identity.tenant_id,
                identity.project_id,
                identity.run_id,
                identity.launch_fingerprint,
                identity.adapter_mode,
                identity.lease_id,
                identity.lease_generation,
            ),
        ).fetchone()
        if row is None:
            raise AuthorizedLiveGuardError(
                "authorized-live lease is absent or stale",
                code="authorized_live_stale_lease",
            )
        return row

    def probe(self) -> AuthorizedLiveControlState:
        """Return a safe cancellation decision from one database snapshot."""

        with self.store._connection() as connection:
            row = self._row(connection)
            database_now, _unused = self.store._database_clock(connection)
            identity = self.identity
            integers = {
                field: _integer(row[field], field)
                for field in (
                    "usage_version",
                    "authorization_lifecycle_version",
                    "authorization_provider_call_cap",
                    "authorization_token_cap",
                    "effective_provider_call_limit",
                    "effective_token_limit",
                    "provider_calls_reserved",
                    "provider_calls_completed",
                    "tokens_in_flight",
                    "provider_reported_tokens",
                    "risk_charged_tokens",
                    "provider_call_cap",
                    "token_cap",
                    "provider_profile_enabled",
                )
            }
            if (
                row["runtime_status"]
                not in {"running", "cancel_requested"}
                or row["lease_expires_at"] is None
                or row["lease_expires_at"] <= database_now
            ):
                raise AuthorizedLiveGuardError(
                    "authorized-live lease is outside its active window",
                    code="authorized_live_stale_lease",
                )
            if (
                row["execution_provenance"]
                != LIVE_AUTHORIZED_EXECUTION_PROVENANCE
                or row["transport_config_fingerprint"]
                != identity.transport_config_fingerprint
                or row["usage_authorization_id"]
                != identity.authorization_id
                or row["usage_authorization_fingerprint"]
                != identity.authorization_fingerprint
                or row["row_authorization_fingerprint"]
                != identity.authorization_fingerprint
                or row["consumed_at"] is None
                or row["consumed_runtime_fingerprint"]
                != identity.launch_fingerprint
                or row["consumed_plan_fingerprint"]
                != row["input_fingerprint"]
                or integers["authorization_provider_call_cap"]
                != integers["provider_call_cap"]
                or integers["authorization_token_cap"]
                != integers["token_cap"]
                or integers["effective_provider_call_limit"]
                > integers["authorization_provider_call_cap"]
                or integers["effective_token_limit"]
                > integers["authorization_token_cap"]
                or integers["provider_calls_reserved"]
                + integers["provider_calls_completed"]
                > integers["effective_provider_call_limit"]
                or integers["tokens_in_flight"]
                + integers["provider_reported_tokens"]
                + integers["risk_charged_tokens"]
                > integers["effective_token_limit"]
            ):
                raise AuthorizedLiveGuardError(
                    "authorized-live control identities are inconsistent",
                    code="authorized_live_identity_invalid",
                )
            reason_code = None
            if row["runtime_status"] == "cancel_requested":
                reason_code = "runtime_cancel_requested"
            elif row["revoked_at"] is not None:
                reason_code = "authorization_revoked"
            elif integers["provider_profile_enabled"] != 1:
                reason_code = "provider_profile_disabled"
            return AuthorizedLiveControlState(
                cancellation_requested=reason_code is not None,
                reason_code=reason_code,
                runtime_status=row["runtime_status"],
                usage_version=integers["usage_version"],
                authorization_lifecycle_version=integers[
                    "authorization_lifecycle_version"
                ],
                effective_provider_call_limit=integers[
                    "effective_provider_call_limit"
                ],
                effective_token_limit=integers[
                    "effective_token_limit"
                ],
            )

    def cancellation_requested(self) -> bool:
        return self.probe().cancellation_requested

    def assert_request_allowed(self) -> AuthorizedLiveControlState:
        """Fail closed immediately before a future authorized reservation."""

        state = self.probe()
        if state.cancellation_requested:
            raise AuthorizedLiveGuardError(
                "authorized-live request is cancelled by control state",
                code=f"authorized_live_{state.reason_code}",
            )
        return state
