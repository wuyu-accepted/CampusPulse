"""Hash-verified public summary for the housing private-channel experiment.

The source runs contain private synthetic messages and record-level relations.
This reader exposes only the aggregate export produced by
``scripts/export_forum_private_shadow_summary.py`` and fails closed if the
summary, its frozen public-timeline binding, or its privacy contract drifts.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from backend.campus_pulse.runtime_contract import canonical_sha256


SCHEMA_VERSION = "campus-pulse-private-channel-summary-public-v1"
SCENARIO_ID = "governance_legitimacy_dispute"
FROZEN_PUBLIC_RESULT_SHA256 = (
    "5712b0af9a757bc38bb1286b362d868f8e49c7eae5a42108569c9818922b5662"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BRANCHES = {"Natural", "D"}
_DENIED_KEYS = {
    "agent_id",
    "conversation_id",
    "message_id",
    "visible_text",
    "prompt",
    "provider_body",
    "api_key",
    "source_id",
    "relationship_edge",
}
_DENIED_SUFFIXES = (
    "_agent_id",
    "_conversation_id",
    "_message_id",
    "_source_id",
)


class ResourcePrivateChannelError(ValueError):
    """Raised when the public private-channel aggregate cannot be trusted."""


def _root() -> Path:
    return Path(__file__).resolve().parents[3]


def _paths() -> tuple[Path, Path]:
    root = _root()
    evidence = root / "evidence" / "forum-v4-resource-live-v1"
    return (
        evidence / "private-channel-summary-public-v1.json",
        evidence / "forum-v4-resource-hero-result.json",
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ResourcePrivateChannelError(
            f"required public artifact is unreadable: {path.name}"
        ) from exc
    return digest.hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResourcePrivateChannelError(
            f"required public artifact is unreadable: {path.name}"
        ) from exc
    if not isinstance(value, dict):
        raise ResourcePrivateChannelError("private-channel summary must be an object")
    return value


def _non_negative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResourcePrivateChannelError(f"{field} must be a non-negative integer")
    return value


def _assert_public_safe(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if lowered in _DENIED_KEYS or lowered.endswith(_DENIED_SUFFIXES):
                raise ResourcePrivateChannelError(
                    f"private field escaped into public summary at {path}.{key}"
                )
            _assert_public_safe(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_public_safe(child, path=f"{path}[{index}]")
    elif isinstance(value, str):
        if "sk-" in value or re.search(r"[A-Za-z]:\\\\", value):
            raise ResourcePrivateChannelError(
                f"credential or local path escaped into public summary at {path}"
            )


def _validate_branch(branch: Mapping[str, Any], *, panel: str, name: str) -> None:
    for field in (
        "conversation_count",
        "direct_conversation_count",
        "group_conversation_count",
        "private_message_count",
        "direct_message_count",
        "group_message_count",
        "unique_active_sender_count",
        "public_to_private_binding_count",
    ):
        _non_negative_integer(branch.get(field), f"{panel}.{name}.{field}")
    rows = branch.get("messages_by_tick")
    if not isinstance(rows, list) or [row.get("tick") for row in rows] != list(
        range(4, 11)
    ):
        raise ResourcePrivateChannelError(
            f"{panel}.{name}.messages_by_tick must cover Tick 4--10"
        )
    for row in rows:
        if not isinstance(row, Mapping):
            raise ResourcePrivateChannelError("message timeline row is invalid")
        _non_negative_integer(row.get("message_count"), "message_count")
    channel_rows = branch.get("channel_messages_by_tick")
    sender_rows = branch.get("unique_active_senders_by_tick")
    if (
        not isinstance(channel_rows, list)
        or not isinstance(sender_rows, list)
        or [row.get("tick") for row in channel_rows] != list(range(4, 11))
        or [row.get("tick") for row in sender_rows] != list(range(4, 11))
    ):
        raise ResourcePrivateChannelError(
            f"{panel}.{name} channel/sender timelines must cover Tick 4--10"
        )
    for index, row in enumerate(channel_rows):
        direct = _non_negative_integer(
            row.get("direct_message_count"), "direct_message_count"
        )
        group = _non_negative_integer(
            row.get("group_message_count"), "group_message_count"
        )
        if direct + group != rows[index]["message_count"]:
            raise ResourcePrivateChannelError(
                f"{panel}.{name} channel counts do not conserve messages"
            )
    for row in sender_rows:
        _non_negative_integer(
            row.get("unique_active_sender_count"),
            "unique_active_sender_count",
        )
    quality = branch.get("quality_screen")
    if not isinstance(quality, Mapping) or quality.get("unsafe_lexical_hit_count") != 0:
        raise ResourcePrivateChannelError(
            f"{panel}.{name} did not pass the public lexical screen"
        )
    if quality.get("exact_duplicate_message_count") != 0:
        raise ResourcePrivateChannelError(
            f"{panel}.{name} contains duplicate private messages"
        )


def private_channel_summary() -> dict[str, Any]:
    """Return the validated aggregate-only housing private-channel summary."""

    summary_path, public_result_path = _paths()
    value = _read_object(summary_path)
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ResourcePrivateChannelError("private-channel summary schema mismatch")
    if value.get("scenario_id") != SCENARIO_ID:
        raise ResourcePrivateChannelError("private-channel scenario mismatch")
    if value.get("frozen_public_timeline_modified") is not False:
        raise ResourcePrivateChannelError("frozen public timeline was modified")
    if value.get("causal_effect_on_frozen_public_timeline_claimed") is not False:
        raise ResourcePrivateChannelError("private supplement overstates causal scope")
    if _file_sha256(public_result_path) != FROZEN_PUBLIC_RESULT_SHA256:
        raise ResourcePrivateChannelError("frozen public result hash changed")
    if value.get("frozen_public_result_sha256") != FROZEN_PUBLIC_RESULT_SHA256:
        raise ResourcePrivateChannelError("private summary public-result binding mismatch")

    expected = value.get("summary_sha256")
    unsigned = {key: child for key, child in value.items() if key != "summary_sha256"}
    if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
        raise ResourcePrivateChannelError("private summary SHA-256 is missing")
    if canonical_sha256(unsigned) != expected:
        raise ResourcePrivateChannelError("private summary content hash mismatch")

    privacy = value.get("privacy")
    if not isinstance(privacy, Mapping) or any(
        privacy.get(key) is not expected_value
        for key, expected_value in {
            "contains_private_text": False,
            "contains_agent_identifiers": False,
            "contains_conversation_or_message_identifiers": False,
            "contains_prompts_or_provider_bodies": False,
            "contains_credentials": False,
            "human_review_required_before_private_excerpt_export": True,
        }.items()
    ):
        raise ResourcePrivateChannelError("private summary privacy contract mismatch")

    panels = value.get("panels")
    if not isinstance(panels, Mapping) or set(panels) != {
        "broad_private_panel",
        "group_lifecycle_panel",
    }:
        raise ResourcePrivateChannelError("private summary panels are incomplete")
    for panel_name, panel in panels.items():
        if not isinstance(panel, Mapping) or set(panel.get("branches") or {}) != _BRANCHES:
            raise ResourcePrivateChannelError(f"{panel_name} branches are incomplete")
        if panel.get("planned_primary_slots") != panel.get("completed_primary_slots"):
            raise ResourcePrivateChannelError(f"{panel_name} is incomplete")
        for branch_name, branch in panel["branches"].items():
            if not isinstance(branch, Mapping):
                raise ResourcePrivateChannelError(f"{panel_name}.{branch_name} is invalid")
            _validate_branch(branch, panel=panel_name, name=branch_name)

    totals = value.get("current_final_run_execution_totals")
    if not isinstance(totals, Mapping):
        raise ResourcePrivateChannelError("private summary execution totals are missing")
    if totals.get("provider_calls_unknown") != 0 or totals.get("unsettled_or_failed_attempts") != 0:
        raise ResourcePrivateChannelError("private summary contains unresolved Provider work")
    if totals.get("tokens_in_flight") != 0:
        raise ResourcePrivateChannelError("private summary has tokens in flight")
    _assert_public_safe(value)
    return value
