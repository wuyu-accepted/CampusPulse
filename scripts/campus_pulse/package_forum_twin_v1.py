#!/usr/bin/env python3
"""Build the formal CampusPulse YuLan ForumTwin v1 offline archive.

The packager is intentionally a final, read-only publication boundary.  It
does not run a simulation, fetch Provider responses, repair results or create
missing forum assets.  Every input must already be publication eligible and
content-addressed by the frozen result-v6 / visualization-v4 contracts.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
from typing import Any, Mapping
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.campus_pulse.forum_twin_contract import (  # noqa: E402
    FORUM_TWIN_DOMAIN_RESULT_SCHEMA_VERSION,
    FORUM_TWIN_MAX_RESIDENT_TURNS,
    FORUM_TWIN_MAX_TURNS_PER_TICK,
    FORUM_TWIN_PRIMARY_SLOTS,
    FORUM_TWIN_PUBLIC_MESSAGE_PROVENANCE,
    FORUM_TWIN_REQUEST_LIMIT,
    FORUM_TWIN_RESULT_SCHEMA_VERSION,
    FORUM_TWIN_TOKEN_LIMIT,
    FORUM_TWIN_VISUALIZATION_SCHEMA_VERSION,
    validate_forum_twin_public_payload,
    validate_forum_twin_visualization_manifest,
)
from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402


PACKAGE_SCHEMA_VERSION = "campus-pulse-yulan-forum-twin-offline-package-v1"
AUDIT_SCHEMA_VERSION = "campus-pulse-yulan-forum-twin-offline-audit-v1"
FORMAL_EXECUTION_PROVENANCE = frozenset(
    {"reviewed_trace_replay", "authorized_live_llm"}
)
ASSET_GROUPS = (
    "thread_batches",
    "claim_batches",
    "particle_frames",
    "propagation_matrices",
)
JSON_ASSET_GROUPS = frozenset(
    {"thread_batches", "claim_batches", "propagation_matrices"}
)
FIXED_ZIP_TIME = (2026, 8, 3, 0, 0, 0)
MAX_PUBLIC_ASSET_BYTES = 100_000_000
MAX_EXPANDED_JSON_BYTES = 100_000_000
DATA_DIR = PurePosixPath("campus-pulse-data")
RESULT_MEMBER = DATA_DIR / "forum-twin-v1.json"
RESULT_FILE_SHA_MEMBER = DATA_DIR / "forum-twin-v1.file.sha256"
VISUALIZATION_MEMBER = DATA_DIR / "forum-twin-v1.visualization.json"
VISUALIZATION_SHA_MEMBER = DATA_DIR / "forum-twin-v1.visualization.sha256"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID = re.compile(r"^run_[0-9a-f]{24}$")
_PRIVATE_NETWORK = re.compile(
    rb"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
    rb"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    rb"(?ix)[\"']?(?:api[_-]?key|password|secret|access[_-]?token)[\"']?"
    rb"\s*[:=]\s*[\"'][^\"']{8,}[\"']"
)
_FORBIDDEN_BYTES = (
    b"authorization: bearer",
    b"-----begin private key-----",
    b"c:\\users\\",
    b"d:\\code\\",
    b"/home/",
)


def _read_json(path: Path, field: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{field} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{field} must be a JSON object")
    return value


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise RuntimeError(f"{field} must be a lowercase SHA-256")
    return value


def _reject_nonformal_markers(value: Any, path: str = "$") -> None:
    """Reject fixture/development evidence without rejecting zero audit flags."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if "fixture" in lowered and child not in (False, None, "", 0, [], {}):
                raise RuntimeError(f"fixture evidence is forbidden at {path}.{key}")
            _reject_nonformal_markers(child, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_nonformal_markers(child, f"{path}[{index}]")
        return
    if value in {"deterministic_fixture", "emulator_only_development", "dynamics"}:
        raise RuntimeError(f"development provenance is forbidden at {path}")


def _validate_formal_result(result: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version",
        "run_id",
        "launch_fingerprint",
        "result_kind",
        "plan_sha256",
        "execution_provenance",
        "publication_eligible",
        "domain_result",
        "visualization_asset",
        "completeness",
        "budget_usage",
        "usage",
        "privacy",
        "non_claims",
        "result_sha256",
    }
    if set(result) != required:
        raise RuntimeError("result-v6 keys do not match the frozen contract")
    if (
        result.get("schema_version") != FORUM_TWIN_RESULT_SCHEMA_VERSION
        or not isinstance(result.get("run_id"), str)
        or _RUN_ID.fullmatch(result["run_id"]) is None
        or result.get("result_kind") != "llm_forum_twin_simulation"
        or result.get("publication_eligible") is not True
        or result.get("execution_provenance") not in FORMAL_EXECUTION_PROVENANCE
    ):
        raise RuntimeError("ForumTwin result is not publication eligible")
    for field in ("launch_fingerprint", "plan_sha256", "result_sha256"):
        _require_sha(result.get(field), field)
    expected_result_sha = canonical_sha256(
        {key: value for key, value in result.items() if key != "result_sha256"}
    )
    if result["result_sha256"] != expected_result_sha:
        raise RuntimeError("ForumTwin result-v6 content hash is invalid")

    completeness = result.get("completeness")
    if not isinstance(completeness, Mapping) or (
        completeness.get("required_primary_slots") != FORUM_TWIN_PRIMARY_SLOTS
        or completeness.get("completed_primary_slots") != FORUM_TWIN_PRIMARY_SLOTS
        or completeness.get("status") != "complete"
    ):
        raise RuntimeError("ForumTwin formal trace coverage is incomplete")
    privacy = result.get("privacy")
    if not isinstance(privacy, Mapping) or privacy.get("scan_passed") is not True:
        raise RuntimeError("ForumTwin privacy scan did not pass")
    budget = result.get("budget_usage")
    if not isinstance(budget, Mapping) or (
        budget.get("primary_slot_limit") != FORUM_TWIN_PRIMARY_SLOTS
        or budget.get("provider_request_limit") != FORUM_TWIN_REQUEST_LIMIT
        or budget.get("provider_token_limit") != FORUM_TWIN_TOKEN_LIMIT
        or isinstance(budget.get("peak_resident_turns_per_tick"), bool)
        or not isinstance(budget.get("peak_resident_turns_per_tick"), int)
        or budget["peak_resident_turns_per_tick"] < 0
        or budget["peak_resident_turns_per_tick"] > FORUM_TWIN_MAX_RESIDENT_TURNS
        or isinstance(budget.get("peak_total_turns_per_tick"), bool)
        or not isinstance(budget.get("peak_total_turns_per_tick"), int)
        or budget["peak_total_turns_per_tick"] < 0
        or budget["peak_total_turns_per_tick"] > FORUM_TWIN_MAX_TURNS_PER_TICK
    ):
        raise RuntimeError("ForumTwin frozen Provider/activation budget is invalid")

    domain = result.get("domain_result")
    if not isinstance(domain, Mapping) or domain.get("schema_version") != FORUM_TWIN_DOMAIN_RESULT_SCHEMA_VERSION:
        raise RuntimeError("ForumTwin domain result is invalid")
    audit = domain.get("audit")
    if not isinstance(audit, Mapping) or (
        audit.get("emulator_public_messages") != 0
        or audit.get("real_governance_actions") != 0
    ):
        raise RuntimeError("ForumTwin public-content boundary is invalid")
    messages = domain.get("public_messages")
    if not isinstance(messages, list):
        raise RuntimeError("ForumTwin public messages are missing")
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            raise RuntimeError(f"ForumTwin public message {index} is invalid")
        provenance = message.get("provenance")
        provenance_kind = (
            provenance.get("kind") if isinstance(provenance, Mapping) else provenance
        )
        if provenance_kind not in FORUM_TWIN_PUBLIC_MESSAGE_PROVENANCE:
            raise RuntimeError(
                f"ForumTwin public message {index} lacks formal LLM provenance"
            )
        if isinstance(provenance, Mapping):
            _require_sha(
                provenance.get("prompt_sha256"),
                f"public_messages[{index}].provenance.prompt_sha256",
            )
            _require_sha(
                provenance.get("response_sha256"),
                f"public_messages[{index}].provenance.response_sha256",
            )
            if provenance_kind == "reviewed_trace_replay":
                _require_sha(
                    provenance.get("trace_release_sha256"),
                    f"public_messages[{index}].provenance.trace_release_sha256",
                )
            elif provenance.get("trace_release_sha256") is not None:
                raise RuntimeError(
                    f"ForumTwin public message {index} has an invalid live trace binding"
                )

    llm_usage = domain.get("llm_usage")
    if not isinstance(llm_usage, Mapping):
        raise RuntimeError("ForumTwin LLM usage evidence is missing")
    resident_turns = llm_usage.get("resident_turns")
    governance_turns = llm_usage.get("governance_turns")
    live_turns = llm_usage.get("live_provider_turns")
    trace_turns = llm_usage.get("trace_replay_turns")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in (resident_turns, governance_turns, live_turns, trace_turns)
    ) or (
        resident_turns + governance_turns != FORUM_TWIN_PRIMARY_SLOTS
        or live_turns + trace_turns != FORUM_TWIN_PRIMARY_SLOTS
    ):
        raise RuntimeError("ForumTwin formal LLM turn provenance is incomplete")
    aggregate_usage = result.get("usage")
    if not isinstance(aggregate_usage, Mapping):
        raise RuntimeError("ForumTwin aggregate usage is missing")
    for aggregate_key, domain_key in (
        ("provider_calls", "provider_calls"),
        ("provider_tokens", "provider_tokens"),
    ):
        if aggregate_key in aggregate_usage and aggregate_usage[aggregate_key] != llm_usage.get(domain_key):
            raise RuntimeError("ForumTwin Provider usage counters do not balance")

    visual = result.get("visualization_asset")
    if not isinstance(visual, Mapping) or set(visual) != {
        "schema_version",
        "manifest_sha256",
        "domain_result_sha256",
        "public_forum_sha256",
    } or visual.get("schema_version") != FORUM_TWIN_VISUALIZATION_SCHEMA_VERSION:
        raise RuntimeError("ForumTwin visualization descriptor is invalid")
    for field in ("manifest_sha256", "domain_result_sha256", "public_forum_sha256"):
        _require_sha(visual.get(field), f"visualization_asset.{field}")
    if visual["domain_result_sha256"] != canonical_sha256(domain):
        raise RuntimeError("ForumTwin domain result hash is invalid")
    forum_projection = {
        "public_threads": domain.get("public_threads"),
        "public_messages": domain.get("public_messages"),
        "claims": domain.get("claims"),
    }
    if not all(isinstance(forum_projection[key], list) for key in forum_projection):
        raise RuntimeError("ForumTwin public forum projection is incomplete")
    if visual["public_forum_sha256"] != canonical_sha256(forum_projection):
        raise RuntimeError("ForumTwin public forum hash is invalid")

    validate_forum_twin_public_payload(result)
    _reject_nonformal_markers(result)
    return result


def _validate_manifest_binding(result: Mapping[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    validated = validate_forum_twin_visualization_manifest(manifest)
    descriptor = result["visualization_asset"]
    expected = {
        "run_id": result["run_id"],
        "result_sha256": result["result_sha256"],
        "domain_result_sha256": descriptor["domain_result_sha256"],
        "public_forum_sha256": descriptor["public_forum_sha256"],
        "manifest_sha256": descriptor["manifest_sha256"],
    }
    actual = {field: validated.get(field) for field in expected}
    if actual != expected:
        raise RuntimeError("ForumTwin result-to-manifest hash chain is invalid")
    if any(not validated[group] for group in ASSET_GROUPS):
        raise RuntimeError("ForumTwin formal visualization asset groups are incomplete")
    return validated


def _expanded_public_json(raw: bytes, name: str) -> Any:
    if raw.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as stream:
                value = stream.read(MAX_EXPANDED_JSON_BYTES + 1)
        except (OSError, EOFError) as exc:
            raise RuntimeError(f"Public asset {name} has invalid gzip content") from exc
    else:
        value = raw
    if len(value) > MAX_EXPANDED_JSON_BYTES:
        raise RuntimeError(f"Public asset {name} exceeds the expanded JSON limit")
    _privacy_scan_bytes(value, name)
    try:
        payload = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Public asset {name} is not valid JSON") from exc
    if not isinstance(payload, (dict, list)):
        raise RuntimeError(f"Public asset {name} must contain an object or array")
    validate_forum_twin_public_payload(payload)
    _reject_nonformal_markers(payload, f"asset:{name}")
    return payload


def _privacy_scan_bytes(raw: bytes, name: str) -> None:
    lowered = raw.lower()
    if (
        any(token in lowered for token in _FORBIDDEN_BYTES)
        or _PRIVATE_NETWORK.search(lowered) is not None
        or _CREDENTIAL_ASSIGNMENT.search(lowered) is not None
    ):
        raise RuntimeError(f"Privacy scan rejected public asset {name}")


def _load_public_assets(
    assets_dir: Path,
    manifest: Mapping[str, Any],
) -> dict[str, list[tuple[str, bytes]]]:
    if not assets_dir.is_dir() or assets_dir.is_symlink():
        raise RuntimeError("ForumTwin public asset directory is unavailable")
    expected_ids: set[str] = set()
    loaded: dict[str, list[tuple[str, bytes]]] = {group: [] for group in ASSET_GROUPS}
    declared_total = 0
    for group in ASSET_GROUPS:
        for descriptor in manifest[group]:
            asset_id = descriptor["asset_id"]
            if asset_id in expected_ids:
                raise RuntimeError(f"Duplicate ForumTwin public asset id: {asset_id}")
            expected_ids.add(asset_id)
            source = assets_dir / asset_id
            if (
                source.parent != assets_dir
                or not source.is_file()
                or source.is_symlink()
            ):
                raise RuntimeError(f"ForumTwin public asset is missing: {asset_id}")
            raw = source.read_bytes()
            if (
                len(raw) != descriptor["compressed_bytes"]
                or _sha256_bytes(raw) != descriptor["sha256"]
            ):
                raise RuntimeError(f"ForumTwin public asset hash/size mismatch: {asset_id}")
            _privacy_scan_bytes(raw, asset_id)
            if group in JSON_ASSET_GROUPS:
                _expanded_public_json(raw, asset_id)
            elif not raw:
                raise RuntimeError(f"ForumTwin particle frame is empty: {asset_id}")
            declared_total += len(raw)
            loaded[group].append((asset_id, raw))
    actual_files = {
        path.name for path in assets_dir.iterdir() if path.is_file()
    }
    if actual_files != expected_ids or any(path.is_dir() for path in assets_dir.iterdir()):
        raise RuntimeError("ForumTwin public asset directory contains unmanifested members")
    if declared_total != manifest["total_compressed_bytes"] or declared_total > MAX_PUBLIC_ASSET_BYTES:
        raise RuntimeError("ForumTwin public asset byte total is invalid")
    return loaded


def _verify_public_asset_projection(
    result: Mapping[str, Any],
    public_assets: Mapping[str, list[tuple[str, bytes]]],
) -> None:
    """Prove that public batch assets are the result-v6 forum projection."""

    threads: list[Any] = []
    messages: list[Any] = []
    claims: list[Any] = []
    for asset_id, raw in public_assets["thread_batches"]:
        value = _expanded_public_json(raw, asset_id)
        if (
            not isinstance(value, Mapping)
            or not isinstance(value.get("threads"), list)
            or not isinstance(value.get("messages"), list)
        ):
            raise RuntimeError("ForumTwin thread batch projection is invalid")
        threads.extend(value["threads"])
        messages.extend(value["messages"])
    for asset_id, raw in public_assets["claim_batches"]:
        value = _expanded_public_json(raw, asset_id)
        if not isinstance(value, Mapping) or not isinstance(value.get("claims"), list):
            raise RuntimeError("ForumTwin claim batch projection is invalid")
        claims.extend(value["claims"])
    domain = result["domain_result"]
    projected = {
        "public_threads": threads,
        "public_messages": messages,
        "claims": claims,
    }
    expected = {
        "public_threads": domain["public_threads"],
        "public_messages": domain["public_messages"],
        "claims": domain["claims"],
    }
    if projected != expected or canonical_sha256(projected) != result[
        "visualization_asset"
    ]["public_forum_sha256"]:
        raise RuntimeError("ForumTwin public asset batches disagree with result-v6")


def _copy_frontend(frontend_dist: Path, staging: Path) -> None:
    index = frontend_dist / "index.html"
    assets = frontend_dist / "assets"
    if (
        not index.is_file()
        or index.is_symlink()
        or not assets.is_dir()
        or assets.is_symlink()
        or any(path.is_symlink() for path in assets.rglob("*"))
    ):
        raise RuntimeError("ForumTwin production SPA build is unavailable")
    staging.mkdir(parents=True, exist_ok=False)
    shutil.copy2(index, staging / "index.html")
    shutil.copytree(assets, staging / "assets")


def _write_bytes(staging: Path, relative: PurePosixPath, value: bytes) -> None:
    destination = staging.joinpath(*relative.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(value)


def _file_hashes(staging: Path, *, exclude: set[str] | None = None) -> dict[str, str]:
    excluded = exclude or set()
    return {
        path.relative_to(staging).as_posix(): _sha256_bytes(path.read_bytes())
        for path in sorted(staging.rglob("*"), key=lambda item: item.as_posix())
        if path.is_file() and path.relative_to(staging).as_posix() not in excluded
    }


def _zip_tree(staging: Path, output: Path) -> None:
    if output.exists():
        raise RuntimeError("Refusing to overwrite an existing ForumTwin release archive")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(staging.rglob("*"), key=lambda item: item.as_posix()):
            if not path.is_file():
                continue
            relative = path.relative_to(staging).as_posix()
            info = zipfile.ZipInfo(relative, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes(), compresslevel=9)


def _scan_staging(staging: Path) -> None:
    for path in staging.rglob("*"):
        if not path.is_file():
            continue
        raw = path.read_bytes()
        _privacy_scan_bytes(raw, path.name)
        if path.suffix.lower() == ".json":
            _expanded_public_json(raw, path.name)


def package(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists():
        raise RuntimeError("Refusing to overwrite an existing ForumTwin release archive")
    result = _validate_formal_result(_read_json(args.result, "result-v6"))
    manifest = _validate_manifest_binding(
        result,
        _read_json(args.manifest, "visualization-v4 manifest"),
    )
    public_assets = _load_public_assets(args.assets_dir, manifest)
    _verify_public_asset_projection(result, public_assets)

    with tempfile.TemporaryDirectory() as raw_staging:
        staging = Path(raw_staging) / "CampusPulse-YuLan-ForumTwin-v1"
        _copy_frontend(args.frontend_dist, staging)
        result_bytes = _canonical_json_bytes(result)
        manifest_bytes = _canonical_json_bytes(manifest)
        _write_bytes(staging, RESULT_MEMBER, result_bytes)
        _write_bytes(
            staging,
            RESULT_FILE_SHA_MEMBER,
            (_sha256_bytes(result_bytes) + "\n").encode("ascii"),
        )
        _write_bytes(staging, VISUALIZATION_MEMBER, manifest_bytes)
        _write_bytes(
            staging,
            VISUALIZATION_SHA_MEMBER,
            (manifest["manifest_sha256"] + "\n").encode("ascii"),
        )
        for group, members in public_assets.items():
            for asset_id, raw in members:
                _write_bytes(
                    staging,
                    DATA_DIR / "forum-twin-assets" / group / asset_id,
                    raw,
                )

        readme = """# CampusPulse YuLan ForumTwin v1

This is the formal offline competition package for the LLM-driven synthetic
campus parallel forum. All visible forum messages come from authorized live
LLM turns or exact reviewed-trace replay. Dynamics never creates public text.

The 1,000 agents are synthetic forum agents grounded in reviewed episodes,
not real students. The 10,000 particles express state uncertainty and are not
users or independent evidence. The synthetic graph, forum propagation and
governance branches are not observed relationships, human opinion estimates,
causal policy effects or real governance advice.

The archive excludes raw corpus text, source identifiers, private feeds,
sample identities, prompts, Provider bodies, credentials, SQLite databases
and checkpoints. `AUDIT.json` and `SHA256SUMS.json` bind every public member.
"""
        (staging / "README.md").write_text(readme, encoding="utf-8")
        audit_body = {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "package_schema_version": PACKAGE_SCHEMA_VERSION,
            "run_id": result["run_id"],
            "execution_provenance": result["execution_provenance"],
            "publication_eligible": True,
            "completed_primary_slots": FORUM_TWIN_PRIMARY_SLOTS,
            "result_sha256": result["result_sha256"],
            "domain_result_sha256": manifest["domain_result_sha256"],
            "public_forum_sha256": manifest["public_forum_sha256"],
            "visualization_manifest_sha256": manifest["manifest_sha256"],
            "public_asset_count": sum(len(value) for value in public_assets.values()),
            "public_asset_bytes": manifest["total_compressed_bytes"],
            "privacy_scan_passed": True,
            "fixture_evidence_included": False,
            "development_evidence_included": False,
            "private_assets_included": False,
            "raw_prompts_or_provider_bodies_included": False,
            "existing_release_overwritten": False,
        }
        audit = {**audit_body, "audit_sha256": canonical_sha256(audit_body)}
        (staging / "AUDIT.json").write_bytes(_canonical_json_bytes(audit))

        file_hashes = _file_hashes(staging)
        package_manifest_body = {
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "archive_name": "CampusPulse-YuLan-ForumTwin-v1.zip",
            "result_sha256": result["result_sha256"],
            "visualization_manifest_sha256": manifest["manifest_sha256"],
            "members": file_hashes,
        }
        package_manifest = {
            **package_manifest_body,
            "package_manifest_sha256": canonical_sha256(package_manifest_body),
        }
        (staging / "SHA256SUMS.json").write_bytes(
            _canonical_json_bytes(package_manifest)
        )
        _scan_staging(staging)
        _zip_tree(staging, args.output)

    return {
        "output": str(args.output),
        "zip_sha256": _sha256_bytes(args.output.read_bytes()),
        "result_sha256": result["result_sha256"],
        "manifest_sha256": manifest["manifest_sha256"],
        "asset_count": sum(len(value) for value in public_assets.values()),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a fail-closed formal ForumTwin v1 offline ZIP."
    )
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--assets-dir", type=Path, required=True)
    parser.add_argument("--frontend-dist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    print(json.dumps(package(parse_args()), ensure_ascii=False, sort_keys=True))
