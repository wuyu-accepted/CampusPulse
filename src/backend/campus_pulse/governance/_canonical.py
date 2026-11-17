"""Small canonicalization primitives shared by governance contracts.

The governance layer intentionally does not reuse runtime exceptions.  A
program is qualified before it becomes a run-plan binding, so callers need a
stable, domain-specific error code without importing worker or transport code.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


_CONTROLLED_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_URI = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_ABSOLUTE_PATH = re.compile(
    r"(?:^|[\s:=])(?:[A-Za-z]:[\\/]|/(?!/)\S|\\\\)"
)
_SECRET_ASSIGNMENT = re.compile(
    r"\b(?:api[_-]?key|password|credential|secret|token)"
    r"\s*[:=]\s*\S+",
    re.I,
)
_EXECUTABLE_TEXT = re.compile(
    r"(?:<script\b|\b(?:eval|exec)\s*\(|"
    r"^\s*(?:from\s+\S+\s+import|import\s+\S+|def\s+\w+\s*\())",
    re.I | re.M,
)


class GovernanceContractError(ValueError):
    """A controlled, privacy-safe governance contract failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "governance_contract_invalid",
    ):
        super().__init__(message)
        self.code = code


def canonical_json(value: Any) -> str:
    """Return the one canonical JSON representation used for identities."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise GovernanceContractError(
            "value is not canonical JSON",
            code="canonical_json_invalid",
        ) from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GovernanceContractError(
            f"{field} must be an object",
            code="governance_schema_invalid",
        )
    return value


def require_sequence(value: Any, field: str) -> Sequence[Any]:
    if (
        isinstance(value, (str, bytes, bytearray))
        or not isinstance(value, Sequence)
    ):
        raise GovernanceContractError(
            f"{field} must be an array",
            code="governance_schema_invalid",
        )
    return value


def require_exact_keys(
    value: Mapping[str, Any],
    expected: set[str] | frozenset[str],
    field: str,
) -> None:
    if set(value) != set(expected):
        raise GovernanceContractError(
            f"{field} keys do not match the frozen schema",
            code="governance_unknown_field",
        )


def controlled_id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _CONTROLLED_ID.fullmatch(value):
        raise GovernanceContractError(
            f"{field} must be a controlled identifier",
            code="governance_identifier_invalid",
        )
    return value


def strict_int(
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
        raise GovernanceContractError(
            f"{field} must be an integer between {minimum} and {maximum}",
            code="governance_integer_invalid",
        )
    return value


def bounded_text(
    value: Any,
    field: str,
    *,
    maximum_length: int,
    permit_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise GovernanceContractError(
            f"{field} must be text",
            code="governance_text_invalid",
        )
    normalized = " ".join(value.split())
    if (not normalized and not permit_empty) or len(normalized) > maximum_length:
        raise GovernanceContractError(
            f"{field} is outside its text bound",
            code="governance_text_invalid",
        )
    if (
        _CONTROL_CHARACTER.search(value)
        or _URI.search(value)
        or _ABSOLUTE_PATH.search(value)
        or _SECRET_ASSIGNMENT.search(value)
        or _EXECUTABLE_TEXT.search(value)
    ):
        raise GovernanceContractError(
            f"{field} contains forbidden executable or private material",
            code="governance_unsafe_text",
        )
    return normalized
