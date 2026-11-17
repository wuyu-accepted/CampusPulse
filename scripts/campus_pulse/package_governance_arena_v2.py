#!/usr/bin/env python3
"""Build a deterministic, privacy-bounded Governance Arena v2 offline ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import zipfile
from typing import Any, Mapping

from backend.campus_pulse.governance_v2_contract import (
    GOVERNANCE_V2_FIXTURE_SLOTS,
    GOVERNANCE_V2_REQUIRED_UNITS,
    GOVERNANCE_V2_VISUALIZATION_SCHEMA_VERSION,
    bytes_sha256,
    canonical_json_bytes,
)
from backend.campus_pulse.runtime_contract import (
    build_launch_contract,
    canonical_sha256,
    validate_aggregate_result,
    validate_frozen_run_plan,
)


PACKAGE_NAME = "CampusPulse-Governance-Arena-v2"
PACKAGE_SCHEMA = "campus-pulse-governance-arena-offline-package-v2"
RESULT_NAME = "governance-arena-v2.json"
MANIFEST_NAME = "governance-arena-v2.visualization.json"
MANIFEST_HASH_NAME = "governance-arena-v2.visualization.sha256"
BINARY_NAME = "governance-arena-v2.frames.bin"
RUN_PLAN_NAME = "campus-pulse-run-plan-v6.json"
PUBLIC_ASSET_NAMES = (
    RESULT_NAME,
    MANIFEST_NAME,
    MANIFEST_HASH_NAME,
    BINARY_NAME,
)
FORBIDDEN_PACKAGE_PATH_PARTS = {
    "checkpoint",
    "private",
    "sample-identities",
    "sample_identities",
    "semantic-population",
    "exposure-graph",
    "sqlite",
}

SERVER_SOURCE = '''#!/usr/bin/env python3
"""Loopback-only static server for CampusPulse Governance Arena v2."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent / "web"
os.chdir(ROOT)

class SPAHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        requested = urlparse(self.path).path.lstrip("/")
        if requested and not (ROOT / requested).is_file() and "." not in Path(requested).name:
            self.path = "/index.html"
        super().do_GET()

if __name__ == "__main__":
    address = ("127.0.0.1", 4175)
    print("Open http://127.0.0.1:4175/campus-pulse/governance-arena-v2")
    ThreadingHTTPServer(address, SPAHandler).serve_forever()
'''

README = """# CampusPulse Governance Arena v2

This is the independent offline, zero-Provider Governance Arena v2 release.

1. Run `python run_server.py`.
2. Open `http://127.0.0.1:4175/campus-pulse/governance-arena-v2`.

The frozen run contains two synthetic scenarios, five paired branches,
eight seeds, 1,728 low-cost population branch-ticks, 8,192 reviewed fixture
semantic slots, and no Provider call. The animation uses the canonical display
seed; statistical intervals use all eight paired seeds.

The 1,000 parents are reviewed forum behavior episodes, not identified
students. The 10,000 particles are state-uncertainty hypotheses, not people or
independent evidence subjects. Exposure, diffusion, actor observations, game
decisions, and policy effects are model-conditional synthetic mechanisms.
Nothing in this package is a real-world causal estimate or governance advice.

The package contains only the static SPA, public aggregate result-v4, public
visualization manifest, anonymous quantized frames, the public run-plan,
README, and audit records. It contains no database, checkpoint, private
message batch, sample identity, parent-particle mapping, record-level graph,
credential, or unreviewed source text.
"""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the deterministic Governance Arena v2 offline ZIP."
    )
    parser.add_argument("--dist", default="src/frontend/dist")
    parser.add_argument(
        "--asset-dir",
        default="src/frontend/public/campus-pulse-data",
    )
    parser.add_argument(
        "--run-plan",
        default="_run/governance-arena-v2/campus-pulse-run-plan-v6.json",
    )
    parser.add_argument(
        "--audit",
        default="specs/015-governance-arena-v2/AUDIT.md",
    )
    parser.add_argument(
        "--output-dir",
        default="_run/CampusPulse-Governance-Arena-v2",
    )
    parser.add_argument(
        "--zip",
        dest="zip_path",
        default="_release/CampusPulse-Governance-Arena-v2.zip",
    )
    parser.add_argument(
        "--preserve",
        action="append",
        default=[],
        help="Existing audited ZIP whose SHA-256 must remain unchanged.",
    )
    return parser


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path} is not a readable JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _safe_rebuild_directory(path: Path) -> None:
    resolved = path.resolve()
    if resolved == Path.cwd().resolve() or resolved == Path(resolved.anchor):
        raise ValueError("refusing to rebuild an unsafe package directory")
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def _validate_release_inputs(
    *,
    asset_dir: Path,
    run_plan_path: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    bytes,
    str,
]:
    paths = {name: asset_dir / name for name in PUBLIC_ASSET_NAMES}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise ValueError("Governance Arena v2 public assets are missing: " + ", ".join(missing))
    run_document = _load_object(run_plan_path)
    plan = validate_frozen_run_plan(run_document)
    launch = build_launch_contract(
        plan,
        turn_limit=GOVERNANCE_V2_REQUIRED_UNITS,
        max_attempts=2,
        server_concurrency_cap=32,
    )
    result = validate_aggregate_result(
        _load_object(paths[RESULT_NAME]),
        run_plan=plan,
        launch_contract=launch,
    )
    manifest_bytes = paths[MANIFEST_NAME].read_bytes()
    manifest = _load_object(paths[MANIFEST_NAME])
    expected_manifest_sha = paths[MANIFEST_HASH_NAME].read_text(
        encoding="ascii"
    ).strip()
    binary = paths[BINARY_NAME].read_bytes()
    if (
        manifest.get("schema_version")
        != GOVERNANCE_V2_VISUALIZATION_SCHEMA_VERSION
        or manifest_bytes != canonical_json_bytes(manifest)
        or bytes_sha256(manifest_bytes) != expected_manifest_sha
        or manifest.get("outer_result_sha256") != result["result_sha256"]
        or manifest.get("domain_result_sha256")
        != result["visualization_asset"]["domain_result_sha256"]
        or manifest.get("binary_sha256") != bytes_sha256(binary)
        or manifest.get("byte_length") != len(binary)
        or manifest.get("frame_count") != 240
        or result["usage"]["fixture_semantic_slots"]
        != GOVERNANCE_V2_FIXTURE_SLOTS
        or result["usage"]["provider_calls"] != 0
        or result["usage"]["provider_tokens"] != 0
        or result["usage"]["real_governance_actions"] != 0
    ):
        raise ValueError("Governance Arena v2 public asset binding is invalid")
    return run_document, result, manifest, binary, expected_manifest_sha


def _assert_package_paths_are_public(package: Path) -> None:
    forbidden = []
    for path in package.rglob("*"):
        if not path.is_file():
            continue
        lowered = path.relative_to(package).as_posix().lower()
        if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"} or any(
            token in lowered for token in FORBIDDEN_PACKAGE_PATH_PARTS
        ):
            forbidden.append(lowered)
    if forbidden:
        raise ValueError(
            "offline package contains forbidden private paths: "
            + ", ".join(sorted(forbidden)[:8])
        )


def _deterministic_zip(source: Path, output: Path) -> None:
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(
                f"{PACKAGE_NAME}/{relative}",
                date_time=(2026, 7, 31, 0, 0, 0),
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            executable = relative in {"run_server.py"}
            info.external_attr = (
                (stat.S_IFREG | (0o755 if executable else 0o644)) << 16
            )
            archive.writestr(info, path.read_bytes())


def _stage_file_hashes(package: Path) -> dict[str, str]:
    return {
        path.relative_to(package).as_posix(): _sha256(path)
        for path in sorted(package.rglob("*"))
        if path.is_file()
    }


def main() -> None:
    args = _parser().parse_args()
    dist = Path(args.dist)
    asset_dir = Path(args.asset_dir)
    run_plan_path = Path(args.run_plan)
    audit_path = Path(args.audit)
    if not (dist / "index.html").is_file():
        raise ValueError("frontend production build is missing index.html")
    if not audit_path.is_file():
        raise ValueError("Governance Arena v2 AUDIT.md is missing")
    run_document, result, manifest, binary, manifest_sha = (
        _validate_release_inputs(
            asset_dir=asset_dir,
            run_plan_path=run_plan_path,
        )
    )
    preserved_before = {
        str(Path(item)): _sha256(Path(item)) for item in args.preserve
    }

    package = Path(args.output_dir)
    _safe_rebuild_directory(package)
    shutil.copytree(dist, package / "web")
    public_target = package / "web" / "campus-pulse-data"
    public_target.mkdir(parents=True, exist_ok=True)
    for name in PUBLIC_ASSET_NAMES:
        shutil.copyfile(asset_dir / name, public_target / name)
    shutil.copyfile(run_plan_path, package / RUN_PLAN_NAME)
    shutil.copyfile(audit_path, package / "AUDIT.md")
    (package / "README.md").write_text(README, encoding="utf-8")
    (package / "run_server.py").write_text(
        SERVER_SOURCE, encoding="utf-8"
    )

    _assert_package_paths_are_public(package)
    staged_hashes = _stage_file_hashes(package)
    package_audit = {
        "schema_version": PACKAGE_SCHEMA,
        "package_name": PACKAGE_NAME,
        "execution_mode": "adaptive_particle_population",
        "result_sha256": result["result_sha256"],
        "plan_sha256": result["plan_sha256"],
        "input_fingerprint": run_document["input_fingerprint"],
        "manifest_sha256": manifest_sha,
        "binary_sha256": manifest["binary_sha256"],
        "binary_bytes": len(binary),
        "frame_count": manifest["frame_count"],
        "work_units": result["completeness"]["completed_turns"],
        "fixture_semantic_slots": result["usage"][
            "fixture_semantic_slots"
        ],
        "provider_calls": result["usage"]["provider_calls"],
        "provider_tokens": result["usage"]["provider_tokens"],
        "real_governance_actions": result["usage"][
            "real_governance_actions"
        ],
        "privacy": result["privacy"],
        "included_file_sha256": staged_hashes,
        "preserved_artifact_sha256": preserved_before,
    }
    package_audit["audit_sha256"] = canonical_sha256(package_audit)
    (package / "AUDIT.json").write_text(
        json.dumps(
            package_audit,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    _assert_package_paths_are_public(package)

    zip_path = Path(args.zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_zip = zip_path.with_suffix(zip_path.suffix + ".tmp")
    if temporary_zip.exists():
        temporary_zip.unlink()
    _deterministic_zip(package, temporary_zip)
    temporary_zip.replace(zip_path)
    preserved_after = {
        path: _sha256(Path(path)) for path in preserved_before
    }
    if preserved_after != preserved_before:
        raise RuntimeError("an existing audited ZIP changed during v2 packaging")
    checksum_path = zip_path.with_suffix(zip_path.suffix + ".sha256")
    checksum_path.write_text(
        f"{_sha256(zip_path)}  {zip_path.name}\n",
        encoding="ascii",
    )
    print(
        json.dumps(
            {
                "package": str(zip_path),
                "package_sha256": _sha256(zip_path),
                "package_bytes": zip_path.stat().st_size,
                "audit_sha256": package_audit["audit_sha256"],
                "result_sha256": result["result_sha256"],
                "manifest_sha256": manifest_sha,
                "binary_sha256": manifest["binary_sha256"],
                "preserved_artifacts": preserved_after,
                "provider_calls": 0,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
