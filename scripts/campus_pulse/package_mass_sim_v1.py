"""Build an isolated, deterministic CampusPulse-MassSim-v1 offline ZIP."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import zipfile


RESULT_SCHEMA = "campus-pulse-live-aggregate-result-v3"
PACKAGE_SCHEMA = "campus-pulse-mass-sim-offline-package-v1"
FORBIDDEN_RESULT_KEYS = {
    "agents",
    "states",
    "edges",
    "selected",
    "_selected",
    "input_fingerprint",
    "source_key",
    "source_id",
    "post_code",
    "raw_text",
    "redacted_text",
    "provider_body",
    "api_key",
    "password",
    "credential",
    "local_path",
    "file_path",
}

SERVER_SOURCE = '''#!/usr/bin/env python3
"""Loopback-only static server for the CampusPulse MassSim offline package."""
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
    address = ("127.0.0.1", 4174)
    print("Open http://127.0.0.1:4174/campus-pulse/mass-sim")
    ThreadingHTTPServer(address, SPAHandler).serve_forever()
'''

README = """# CampusPulse-MassSim-v1

This is an offline, zero-Provider demonstration package.

1. Run `python run_server.py`.
2. Open `http://127.0.0.1:4174/campus-pulse/mass-sim`.

The package contains aggregate MassSim output and reviewed synthetic demo
utterances only. It does not contain raw forum text, source identifiers,
micro-agent state, sample identities, record-level graph edges, credentials,
an API server, or a database.

The 1,000 micro-agents are reviewed forum behavior episodes, not identified
students. The 10,000 particles are a visual uncertainty layer and are not
independent evidence subjects. The exposure graph is fully synthetic.
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _walk_keys(value, path=()):
    if isinstance(value, dict):
        for key, child in value.items():
            yield path + (str(key),)
            yield from _walk_keys(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_keys(child, path + (str(index),))


def _validate_result(result: dict) -> None:
    if result.get("schema_version") != RESULT_SCHEMA:
        raise ValueError("offline result is not aggregate-result-v3")
    body = dict(result)
    stored_hash = body.pop("result_sha256", None)
    if stored_hash != _canonical_hash(body):
        raise ValueError("offline aggregate-result-v3 hash does not match")
    forbidden = sorted(
        ".".join(path)
        for path in _walk_keys(result)
        if path[-1].lower() in FORBIDDEN_RESULT_KEYS
    )
    if forbidden:
        raise ValueError(
            "offline result contains forbidden record-level fields: "
            + ", ".join(forbidden[:8])
        )
    privacy = result.get("privacy", {})
    if not privacy or any(bool(value) for value in privacy.values()):
        raise ValueError("offline result privacy contract is not aggregate-only")
    if result.get("usage", {}).get("provider_calls") != 0:
        raise ValueError("offline package must record zero Provider calls")


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
                f"CampusPulse-MassSim-v1/{relative}",
                date_time=(2026, 7, 30, 0, 0, 0),
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (
                (stat.S_IFREG | (0o755 if relative == "run_server.py" else 0o644))
                << 16
            )
            archive.writestr(info, path.read_bytes())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dist",
        default="src/frontend/dist",
    )
    parser.add_argument(
        "--result",
        default="src/frontend/public/campus-pulse-data/mass-sim-v1.json",
    )
    parser.add_argument(
        "--scale-gap",
        default="_run/mass-sim-scale-gap-v1.json",
    )
    parser.add_argument(
        "--run-plan",
        default="_run/mass-sim-v1/campus-pulse-run-plan-v5.json",
    )
    parser.add_argument(
        "--output-dir",
        default="_run/CampusPulse-MassSim-v1",
    )
    parser.add_argument(
        "--zip",
        dest="zip_path",
        default="_run/CampusPulse-MassSim-v1.zip",
    )
    parser.add_argument(
        "--preserve",
        action="append",
        default=[],
        help="Existing audited artifact whose checksum must stay unchanged.",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    dist = Path(args.dist)
    result_path = Path(args.result)
    scale_gap_path = Path(args.scale_gap)
    plan_path = Path(args.run_plan)
    if not (dist / "index.html").is_file():
        raise ValueError("frontend production build is missing")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    scale_gap = json.loads(scale_gap_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    _validate_result(result)
    if not scale_gap.get("gate", {}).get("passed"):
        raise ValueError("Scale-Gap gate is not passed")
    if (
        plan.get("schema_version") != "campus-pulse-run-plan-v5"
        or plan.get("plan_sha256") != result.get("plan_sha256")
    ):
        raise ValueError("run-plan-v5 and aggregate-result-v3 are not bound")
    preserved_before = {
        str(Path(path)): _sha256(Path(path)) for path in args.preserve
    }

    package = Path(args.output_dir)
    if package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True)
    shutil.copytree(dist, package / "web")
    (package / "run_server.py").write_text(
        SERVER_SOURCE, encoding="utf-8"
    )
    (package / "README.md").write_text(README, encoding="utf-8")
    audit = {
        "schema_version": PACKAGE_SCHEMA,
        "package_name": "CampusPulse-MassSim-v1",
        "execution_mode": "budgeted_population",
        "population_size": result["population_identity"]["population_size"],
        "particle_count": result["population_identity"]["particle_count"],
        "result_sha256": result["result_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "scale_gap_sha256": _sha256(scale_gap_path),
        "scale_gap_passed": True,
        "formal_writeback_mode": result["writeback_evaluation"]["formal_mode"],
        "provider_calls": result["usage"]["provider_calls"],
        "engine_elapsed_seconds": result["performance"][
            "engine_elapsed_seconds"
        ],
        "peak_additional_memory_bytes": result["performance"][
            "peak_additional_memory_bytes"
        ],
        "privacy": result["privacy"],
        "preserved_artifacts": preserved_before,
    }
    audit["audit_sha256"] = _canonical_hash(audit)
    (package / "AUDIT.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
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
        raise RuntimeError("an existing audited artifact changed during packaging")
    checksum_path = zip_path.with_suffix(zip_path.suffix + ".sha256")
    checksum_path.write_text(
        f"{_sha256(zip_path)}  {zip_path.name}\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "package": str(zip_path),
                "package_sha256": _sha256(zip_path),
                "package_bytes": zip_path.stat().st_size,
                "audit_sha256": audit["audit_sha256"],
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
