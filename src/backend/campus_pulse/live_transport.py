"""Network-free deterministic transport fixture for the P3.2b runtime.

The fixture implements the callable shape consumed by the existing
OpenAI-compatible LLM adapter, but it has no URL, credential, environment,
file, clock or real-network configuration. A frozen script and a canonical
JSON request are the only inputs capable of changing an outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import threading
from typing import Any, Mapping, Sequence


DETERMINISTIC_TRANSPORT_ID = "campus-pulse-deterministic-transport-v1"
DETERMINISTIC_TRANSPORT_SCHEMA_VERSION = "1.0.0"
REAL_TRANSPORT_AVAILABLE = False

_REQUEST_FIELDS = {
    "model",
    "reasoning_effort",
    "messages",
    "max_tokens",
    "stream",
}
_MESSAGE_FIELDS = {"role", "content"}
_MESSAGE_ROLES = ("system", "user")
_REASONING_EFFORTS = {"none", "low", "medium", "high"}
_OUTCOMES = {
    "success",
    "format_error",
    "timeout_unknown",
    "connection_unknown",
}
_SCRIPT_MODES = {"keyed", "sequential"}
_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_FIELD_CODES = {
    "apikey",
    "authorization",
    "baseurl",
    "credential",
    "endpoint",
    "headers",
    "password",
    "secret",
    "tokenurl",
    "transport",
    "url",
}
_MAX_PROMPT_BYTES = 65_536
_MAX_RESPONSE_CONTENT_BYTES = 65_536
_MAX_SCRIPT_STEPS = 100_000
_MAX_USAGE_VALUE = 2_147_483_647


class DeterministicTransportError(RuntimeError):
    """Base class for controlled fixture failures."""

    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


class DeterministicTransportContractError(DeterministicTransportError):
    """Raised before invocation when request or script data is invalid."""


class DeterministicTransportUnknown(DeterministicTransportError):
    """Raised after invocation when the fixture outcome is unknown."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        outcome: str,
        invocation_index: int,
        script_step_index: int,
        request_sha256: str,
        request_occurrence: int,
    ):
        super().__init__(message, code=code)
        self.outcome = outcome
        self.invocation_index = invocation_index
        self.script_step_index = script_step_index
        self.request_sha256 = request_sha256
        self.request_occurrence = request_occurrence


class DeterministicTimeoutUnknown(DeterministicTransportUnknown):
    """Represents a sent request whose timeout outcome cannot be known."""


class DeterministicConnectionUnknown(DeterministicTransportUnknown):
    """Represents a sent request lost with an unknown remote outcome."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise DeterministicTransportContractError(
            "value is not strict canonical JSON",
            code="deterministic_transport_json_invalid",
        ) from exc


def canonical_transport_sha256(value: Any) -> str:
    """Hash strict canonical JSON with non-finite values rejected."""

    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalized_field_code(value: Any) -> str:
    if not isinstance(value, str):
        raise DeterministicTransportContractError(
            "request field names must be strings",
            code="deterministic_transport_field_invalid",
        )
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _assert_no_real_transport_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if _normalized_field_code(key) in _FORBIDDEN_FIELD_CODES:
                raise DeterministicTransportContractError(
                    "URL, credential and transport fields are forbidden",
                    code="deterministic_transport_real_configuration_forbidden",
                )
            _assert_no_real_transport_fields(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _assert_no_real_transport_fields(nested)


def _controlled_integer(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise DeterministicTransportContractError(
            f"{field} is outside the controlled integer range",
            code="deterministic_transport_integer_invalid",
        )
    return value


def _controlled_model(value: Any, field: str) -> str:
    if not isinstance(value, str) or _MODEL_PATTERN.fullmatch(value) is None:
        raise DeterministicTransportContractError(
            f"{field} is not a controlled model identifier",
            code="deterministic_transport_model_invalid",
        )
    return value


def _optional_sha256(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise DeterministicTransportContractError(
            f"{field} is not a SHA-256 identity",
            code="deterministic_transport_sha_invalid",
        )
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DeterministicTransportContractError(
                "response content contains duplicate JSON keys",
                code="deterministic_transport_response_json_invalid",
            )
        value[key] = item
    return value


def _parse_canonical_object(content: str) -> dict[str, Any]:
    try:
        value = json.loads(
            content,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                DeterministicTransportContractError(
                    "response content contains a non-finite value",
                    code="deterministic_transport_response_json_invalid",
                )
            ),
        )
    except DeterministicTransportContractError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DeterministicTransportContractError(
            "success content must be one canonical JSON object",
            code="deterministic_transport_response_json_invalid",
        ) from exc
    if not isinstance(value, dict) or _canonical_json(value) != content:
        raise DeterministicTransportContractError(
            "success content must be one canonical JSON object",
            code="deterministic_transport_response_json_invalid",
        )
    return value


@dataclass(frozen=True)
class CanonicalTransportRequest:
    """Immutable canonical representation of one accepted fixture request."""

    canonical_json: str
    request_sha256: str
    model: str
    reasoning_effort: str
    max_tokens: int
    prompt_bytes: int


def canonicalize_transport_request(
    request: dict[str, Any],
) -> CanonicalTransportRequest:
    """Validate and canonicalize the exact supported JSON request shape."""

    if type(request) is not dict:
        raise DeterministicTransportContractError(
            "transport request must be a JSON object",
            code="deterministic_transport_request_invalid",
        )
    _assert_no_real_transport_fields(request)
    if set(request) != _REQUEST_FIELDS:
        raise DeterministicTransportContractError(
            "transport request fields do not match the fixture contract",
            code="deterministic_transport_request_fields_invalid",
        )
    model = _controlled_model(request["model"], "model")
    reasoning_effort = request["reasoning_effort"]
    if reasoning_effort not in _REASONING_EFFORTS:
        raise DeterministicTransportContractError(
            "reasoning_effort is unsupported",
            code="deterministic_transport_reasoning_invalid",
        )
    max_tokens = _controlled_integer(
        request["max_tokens"],
        "max_tokens",
        minimum=32,
        maximum=1_000,
    )
    if request["stream"] is not False:
        raise DeterministicTransportContractError(
            "deterministic transport requires stream=false",
            code="deterministic_transport_stream_invalid",
        )
    messages = request["messages"]
    if type(messages) is not list or len(messages) != len(_MESSAGE_ROLES):
        raise DeterministicTransportContractError(
            "messages must contain exact system and user entries",
            code="deterministic_transport_messages_invalid",
        )
    prompt_bytes = 0
    for index, (message, expected_role) in enumerate(
        zip(messages, _MESSAGE_ROLES)
    ):
        if type(message) is not dict or set(message) != _MESSAGE_FIELDS:
            raise DeterministicTransportContractError(
                f"messages[{index}] fields are invalid",
                code="deterministic_transport_messages_invalid",
            )
        if message["role"] != expected_role:
            raise DeterministicTransportContractError(
                f"messages[{index}].role is invalid",
                code="deterministic_transport_messages_invalid",
            )
        content = message["content"]
        if not isinstance(content, str) or not content:
            raise DeterministicTransportContractError(
                f"messages[{index}].content must be non-empty text",
                code="deterministic_transport_messages_invalid",
            )
        prompt_bytes += len(content.encode("utf-8"))
    if prompt_bytes > _MAX_PROMPT_BYTES:
        raise DeterministicTransportContractError(
            "request exceeds the bounded prompt byte contract",
            code="deterministic_transport_prompt_too_large",
        )
    canonical_json = _canonical_json(request)
    return CanonicalTransportRequest(
        canonical_json=canonical_json,
        request_sha256=hashlib.sha256(
            canonical_json.encode("utf-8")
        ).hexdigest(),
        model=model,
        reasoning_effort=reasoning_effort,
        max_tokens=max_tokens,
        prompt_bytes=prompt_bytes,
    )


@dataclass(frozen=True)
class DeterministicTransportStep:
    """One immutable scripted provider outcome."""

    outcome: str
    response_content: str = ""
    returned_model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    expected_request_sha256: str | None = None
    expected_request_occurrence: int | None = None

    def __post_init__(self) -> None:
        if self.outcome not in _OUTCOMES:
            raise DeterministicTransportContractError(
                "script outcome is unsupported",
                code="deterministic_transport_outcome_invalid",
            )
        _optional_sha256(
            self.expected_request_sha256,
            "expected_request_sha256",
        )
        if self.expected_request_occurrence is not None:
            _controlled_integer(
                self.expected_request_occurrence,
                "expected_request_occurrence",
                minimum=1,
                maximum=_MAX_SCRIPT_STEPS,
            )
        response_outcome = self.outcome in {"success", "format_error"}
        if response_outcome:
            _controlled_model(self.returned_model, "returned_model")
            _controlled_integer(
                self.prompt_tokens,
                "prompt_tokens",
                minimum=0,
                maximum=_MAX_USAGE_VALUE,
            )
            _controlled_integer(
                self.completion_tokens,
                "completion_tokens",
                minimum=0,
                maximum=_MAX_USAGE_VALUE,
            )
            if self.prompt_tokens + self.completion_tokens > _MAX_USAGE_VALUE:
                raise DeterministicTransportContractError(
                    "script usage total is outside the controlled range",
                    code="deterministic_transport_integer_invalid",
                )
            if (
                not isinstance(self.response_content, str)
                or not self.response_content
                or len(self.response_content.encode("utf-8"))
                > _MAX_RESPONSE_CONTENT_BYTES
            ):
                raise DeterministicTransportContractError(
                    "response content is outside the fixture contract",
                    code="deterministic_transport_response_invalid",
                )
            if self.outcome == "success":
                _parse_canonical_object(self.response_content)
        elif (
            self.response_content
            or self.returned_model
            or self.prompt_tokens != 0
            or self.completion_tokens != 0
        ):
            raise DeterministicTransportContractError(
                "unknown outcomes cannot contain a response or usage",
                code="deterministic_transport_unknown_payload_invalid",
            )

    @classmethod
    def success(
        cls,
        decision: Mapping[str, Any],
        *,
        returned_model: str = "gpt-5.6-sol",
        prompt_tokens: int = 100,
        completion_tokens: int = 20,
        expected_request_sha256: str | None = None,
        expected_request_occurrence: int | None = None,
    ) -> "DeterministicTransportStep":
        if not isinstance(decision, Mapping):
            raise DeterministicTransportContractError(
                "success decision must be a JSON object",
                code="deterministic_transport_response_json_invalid",
            )
        return cls(
            outcome="success",
            response_content=_canonical_json(dict(decision)),
            returned_model=returned_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            expected_request_sha256=expected_request_sha256,
            expected_request_occurrence=expected_request_occurrence,
        )

    @classmethod
    def format_error(
        cls,
        *,
        response_content: str = "not-json",
        returned_model: str = "gpt-5.6-sol",
        prompt_tokens: int = 100,
        completion_tokens: int = 5,
        expected_request_sha256: str | None = None,
        expected_request_occurrence: int | None = None,
    ) -> "DeterministicTransportStep":
        return cls(
            outcome="format_error",
            response_content=response_content,
            returned_model=returned_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            expected_request_sha256=expected_request_sha256,
            expected_request_occurrence=expected_request_occurrence,
        )

    @classmethod
    def timeout_unknown(
        cls,
        *,
        expected_request_sha256: str | None = None,
        expected_request_occurrence: int | None = None,
    ) -> "DeterministicTransportStep":
        return cls(
            outcome="timeout_unknown",
            expected_request_sha256=expected_request_sha256,
            expected_request_occurrence=expected_request_occurrence,
        )

    @classmethod
    def connection_unknown(
        cls,
        *,
        expected_request_sha256: str | None = None,
        expected_request_occurrence: int | None = None,
    ) -> "DeterministicTransportStep":
        return cls(
            outcome="connection_unknown",
            expected_request_sha256=expected_request_sha256,
            expected_request_occurrence=expected_request_occurrence,
        )


@dataclass(frozen=True)
class DeterministicTransportCall:
    """Safe call evidence containing identities rather than request bodies."""

    invocation_index: int
    script_step_index: int
    request_sha256: str
    request_occurrence: int
    outcome: str
    response_sha256: str | None


def _step_fingerprint_body(step: DeterministicTransportStep) -> dict[str, Any]:
    return {
        "outcome": step.outcome,
        "response_content": step.response_content,
        "returned_model": step.returned_model,
        "prompt_tokens": step.prompt_tokens,
        "completion_tokens": step.completion_tokens,
        "expected_request_sha256": step.expected_request_sha256,
        "expected_request_occurrence": step.expected_request_occurrence,
    }


class DeterministicTransport:
    """Scripted transport whose default keyed mode is schedule-independent."""

    def __init__(
        self,
        steps: Sequence[DeterministicTransportStep],
        *,
        mode: str = "keyed",
        start_step_index: int = 0,
        consumed_keys: Sequence[tuple[str, int]] = (),
    ) -> None:
        if mode not in _SCRIPT_MODES:
            raise DeterministicTransportContractError(
                "transport script mode is unsupported",
                code="deterministic_transport_mode_invalid",
            )
        if isinstance(steps, (str, bytes)) or not isinstance(steps, Sequence):
            raise DeterministicTransportContractError(
                "transport script must be a sequence of frozen steps",
                code="deterministic_transport_script_invalid",
            )
        provided_steps = tuple(steps)
        if not 1 <= len(provided_steps) <= _MAX_SCRIPT_STEPS or any(
            not isinstance(step, DeterministicTransportStep)
            for step in provided_steps
        ):
            raise DeterministicTransportContractError(
                "transport script is empty, oversized or malformed",
                code="deterministic_transport_script_invalid",
            )
        if mode == "keyed":
            if start_step_index != 0:
                raise DeterministicTransportContractError(
                    "keyed scripts resume by consumed request keys",
                    code="deterministic_transport_resume_invalid",
                )
            if any(
                step.expected_request_sha256 is None
                or step.expected_request_occurrence is None
                for step in provided_steps
            ):
                raise DeterministicTransportContractError(
                    "keyed steps require request SHA and occurrence",
                    code="deterministic_transport_key_missing",
                )
            frozen_steps = tuple(
                sorted(
                    provided_steps,
                    key=lambda step: (
                        step.expected_request_sha256,
                        step.expected_request_occurrence,
                    ),
                )
            )
        else:
            if consumed_keys:
                raise DeterministicTransportContractError(
                    "sequential scripts resume by step index",
                    code="deterministic_transport_resume_invalid",
                )
            if any(
                step.expected_request_occurrence is not None
                for step in provided_steps
            ):
                raise DeterministicTransportContractError(
                    "sequential steps cannot bind request occurrence",
                    code="deterministic_transport_key_invalid",
                )
            frozen_steps = provided_steps
        self._mode = mode
        self._steps = frozen_steps
        self._step_by_key: dict[
            tuple[str, int], tuple[int, DeterministicTransportStep]
        ] = {}
        if mode == "keyed":
            for index, step in enumerate(frozen_steps):
                key = (
                    str(step.expected_request_sha256),
                    int(step.expected_request_occurrence),
                )
                if key in self._step_by_key:
                    raise DeterministicTransportContractError(
                        "keyed script contains a duplicate request occurrence",
                        code="deterministic_transport_key_duplicate",
                    )
                self._step_by_key[key] = (index, step)
        self._next_step_index = _controlled_integer(
            start_step_index,
            "start_step_index",
            minimum=0,
            maximum=len(frozen_steps),
        )
        self._script_fingerprint = canonical_transport_sha256(
            {
                "transport_id": DETERMINISTIC_TRANSPORT_ID,
                "schema_version": DETERMINISTIC_TRANSPORT_SCHEMA_VERSION,
                "mode": mode,
                "steps": [
                    _step_fingerprint_body(step) for step in frozen_steps
                ],
            }
        )
        self._claimed_keys: set[tuple[str, int]] = set()
        if mode == "keyed":
            if isinstance(consumed_keys, (str, bytes)) or not isinstance(
                consumed_keys, Sequence
            ):
                raise DeterministicTransportContractError(
                    "consumed keys must be a controlled sequence",
                    code="deterministic_transport_resume_invalid",
                )
            for raw_key in consumed_keys:
                if (
                    type(raw_key) is not tuple
                    or len(raw_key) != 2
                    or not isinstance(raw_key[0], str)
                ):
                    raise DeterministicTransportContractError(
                        "consumed request key is malformed",
                        code="deterministic_transport_resume_invalid",
                    )
                occurrence = _controlled_integer(
                    raw_key[1],
                    "consumed request occurrence",
                    minimum=1,
                    maximum=_MAX_SCRIPT_STEPS,
                )
                key = (raw_key[0], occurrence)
                _optional_sha256(key[0], "consumed request SHA")
                if key not in self._step_by_key or key in self._claimed_keys:
                    raise DeterministicTransportContractError(
                        "consumed request key is absent or duplicated",
                        code="deterministic_transport_resume_invalid",
                    )
                self._claimed_keys.add(key)
        self._calls: list[DeterministicTransportCall] = []
        self._lock = threading.Lock()

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def script_fingerprint(self) -> str:
        return self._script_fingerprint

    @property
    def calls(self) -> tuple[DeterministicTransportCall, ...]:
        with self._lock:
            if self._mode == "keyed":
                return tuple(
                    sorted(
                        self._calls,
                        key=lambda call: (
                            call.request_sha256,
                            call.request_occurrence,
                        ),
                    )
                )
            return tuple(self._calls)

    @property
    def claimed_keys(self) -> tuple[tuple[str, int], ...]:
        with self._lock:
            if self._mode != "keyed":
                return ()
            return tuple(sorted(self._claimed_keys))

    @property
    def next_step_index(self) -> int:
        with self._lock:
            if self._mode != "sequential":
                raise DeterministicTransportContractError(
                    "keyed scripts do not have a global next step",
                    code="deterministic_transport_mode_invalid",
                )
            return self._next_step_index

    def resumed_from(self, start_step_index: int) -> "DeterministicTransport":
        """Resume the explicitly sequential test mode from a durable offset."""

        if self._mode != "sequential":
            raise DeterministicTransportContractError(
                "keyed scripts resume by consumed request keys",
                code="deterministic_transport_resume_invalid",
            )
        return DeterministicTransport(
            self._steps,
            mode="sequential",
            start_step_index=start_step_index,
        )

    def resumed_with_consumed(
        self,
        consumed_keys: Sequence[tuple[str, int]],
    ) -> "DeterministicTransport":
        """Resume keyed mode from durable request-hash/occurrence identities."""

        if self._mode != "keyed":
            raise DeterministicTransportContractError(
                "sequential scripts resume by step index",
                code="deterministic_transport_resume_invalid",
            )
        return DeterministicTransport(
            self._steps,
            mode="keyed",
            consumed_keys=consumed_keys,
        )

    def _consumed_step_rows(self) -> list[dict[str, Any]]:
        if self._mode == "keyed":
            rows = []
            for request_sha256, occurrence in sorted(self._claimed_keys):
                index, step = self._step_by_key[
                    (request_sha256, occurrence)
                ]
                rows.append(
                    {
                        "script_step_index": index,
                        "request_sha256": request_sha256,
                        "request_occurrence": occurrence,
                        "outcome": step.outcome,
                    }
                )
            return rows
        return [
            {
                "script_step_index": index,
                "request_sha256": (
                    step.expected_request_sha256 or "unbound"
                ),
                "request_occurrence": None,
                "outcome": step.outcome,
            }
            for index, step in enumerate(
                self._steps[: self._next_step_index]
            )
        ]

    def safe_snapshot(self) -> dict[str, Any]:
        """Return schedule-stable provenance without prompts or responses."""

        with self._lock:
            consumed_rows = self._consumed_step_rows()
            outcome_counts = {
                outcome: sum(
                    row["outcome"] == outcome for row in consumed_rows
                )
                for outcome in sorted(_OUTCOMES)
            }
            return {
                "transport_id": DETERMINISTIC_TRANSPORT_ID,
                "schema_version": DETERMINISTIC_TRANSPORT_SCHEMA_VERSION,
                "mode": self._mode,
                "script_fingerprint": self._script_fingerprint,
                "configured_step_count": len(self._steps),
                "consumed_step_count": len(consumed_rows),
                "remaining_step_count": len(self._steps)
                - len(consumed_rows),
                "consumed_evidence_fingerprint": (
                    canonical_transport_sha256(consumed_rows)
                ),
                "outcome_counts": outcome_counts,
                "real_transport_available": REAL_TRANSPORT_AVAILABLE,
                "request_or_response_bodies_recorded": False,
                "credential_fields_accepted": False,
            }

    def _response(
        self,
        *,
        step: DeterministicTransportStep,
        script_step_index: int,
        canonical: CanonicalTransportRequest,
        request_occurrence: int,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if step.outcome not in {"success", "format_error"}:
            return None, None
        total_tokens = step.prompt_tokens + step.completion_tokens
        response = {
            "id": (
                f"fixture-{self._script_fingerprint[:12]}-"
                f"{script_step_index + 1:06d}-"
                f"{canonical.request_sha256[:12]}-"
                f"{request_occurrence:06d}"
            ),
            "model": step.returned_model,
            "choices": [
                {"message": {"content": step.response_content}}
            ],
            "usage": {
                "prompt_tokens": step.prompt_tokens,
                "completion_tokens": step.completion_tokens,
                "total_tokens": total_tokens,
            },
        }
        return response, canonical_transport_sha256(response)

    def _finish(
        self,
        *,
        canonical: CanonicalTransportRequest,
        request_occurrence: int,
        script_step_index: int,
        step: DeterministicTransportStep,
        invocation_index: int,
    ) -> dict[str, Any]:
        response, response_sha256 = self._response(
            step=step,
            script_step_index=script_step_index,
            canonical=canonical,
            request_occurrence=request_occurrence,
        )
        self._calls.append(
            DeterministicTransportCall(
                invocation_index=invocation_index,
                script_step_index=script_step_index,
                request_sha256=canonical.request_sha256,
                request_occurrence=request_occurrence,
                outcome=step.outcome,
                response_sha256=response_sha256,
            )
        )
        if step.outcome == "timeout_unknown":
            raise DeterministicTimeoutUnknown(
                "deterministic timeout has an unknown provider outcome",
                code="deterministic_transport_timeout_unknown",
                outcome=step.outcome,
                invocation_index=invocation_index,
                script_step_index=script_step_index,
                request_sha256=canonical.request_sha256,
                request_occurrence=request_occurrence,
            )
        if step.outcome == "connection_unknown":
            raise DeterministicConnectionUnknown(
                "deterministic connection loss has an unknown provider outcome",
                code="deterministic_transport_connection_unknown",
                outcome=step.outcome,
                invocation_index=invocation_index,
                script_step_index=script_step_index,
                request_sha256=canonical.request_sha256,
                request_occurrence=request_occurrence,
            )
        if response is None:  # pragma: no cover - guarded by frozen outcomes
            raise DeterministicTransportContractError(
                "script outcome did not produce a controlled result",
                code="deterministic_transport_outcome_invalid",
            )
        return response

    def invoke(
        self,
        request: dict[str, Any],
        *,
        request_occurrence: int,
    ) -> dict[str, Any]:
        """Claim the exact keyed request occurrence independent of scheduling."""

        if self._mode != "keyed":
            raise DeterministicTransportContractError(
                "explicit occurrence is available only in keyed mode",
                code="deterministic_transport_mode_invalid",
            )
        occurrence = _controlled_integer(
            request_occurrence,
            "request_occurrence",
            minimum=1,
            maximum=_MAX_SCRIPT_STEPS,
        )
        canonical = canonicalize_transport_request(request)
        key = (canonical.request_sha256, occurrence)
        with self._lock:
            selected = self._step_by_key.get(key)
            if selected is None:
                raise DeterministicTransportContractError(
                    "canonical request occurrence is absent from the script",
                    code="deterministic_transport_request_mismatch",
                )
            if key in self._claimed_keys:
                raise DeterministicTransportContractError(
                    "canonical request occurrence was already consumed",
                    code="deterministic_transport_request_already_consumed",
                )
            script_step_index, step = selected
            self._claimed_keys.add(key)
            invocation_index = script_step_index + 1
            return self._finish(
                canonical=canonical,
                request_occurrence=occurrence,
                script_step_index=script_step_index,
                step=step,
                invocation_index=invocation_index,
            )

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        """Execute only the explicitly sequential small-test mode."""

        if self._mode != "sequential":
            raise DeterministicTransportContractError(
                "keyed transport requires invoke(request, request_occurrence=...)",
                code="deterministic_transport_occurrence_required",
            )
        canonical = canonicalize_transport_request(request)
        with self._lock:
            if self._next_step_index >= len(self._steps):
                raise DeterministicTransportContractError(
                    "deterministic transport script is exhausted",
                    code="deterministic_transport_script_exhausted",
                )
            script_step_index = self._next_step_index
            step = self._steps[script_step_index]
            if (
                step.expected_request_sha256 is not None
                and step.expected_request_sha256
                != canonical.request_sha256
            ):
                raise DeterministicTransportContractError(
                    "canonical request does not match the scripted step",
                    code="deterministic_transport_request_mismatch",
                )
            self._next_step_index += 1
            occurrence = (
                sum(
                    call.request_sha256 == canonical.request_sha256
                    for call in self._calls
                )
                + 1
            )
            return self._finish(
                canonical=canonical,
                request_occurrence=occurrence,
                script_step_index=script_step_index,
                step=step,
                invocation_index=script_step_index + 1,
            )
