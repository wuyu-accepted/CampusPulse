"""Create a reproducible code-only comparison with official YuLan-OneSim.

Example:
    python scripts/compare_upstream.py \
      --upstream ../YuLan-OneSim-official \
      --project . \
      --output docs/UPSTREAM_DIFF_MANIFEST.csv

Generated data, dependencies, evidence, archives and runtime outputs are
excluded so the manifest answers "which maintained code differs?" rather than
"which bytes exist in the working directory?".
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path


INCLUDED_ROOT_FILES = {
    ".dockerignore",
    ".gitignore",
    "Dockerfile",
    "Dockerfile.campus-pulse",
    "Makefile",
    "README.md",
    "setup.py",
    "START_FULL_SYSTEM.cmd",
    "requirements-campus-pulse-online.lock",
    "requirements-campus-pulse-online.txt",
}
INCLUDED_ROOTS = {"config", "scripts", "src"}
EXCLUDED_PARTS = {
    ".git",
    ".idea",
    ".pytest_cache",
    ".venv",
    ".vscode",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
EXCLUDED_SUFFIXES = {".bin", ".db", ".pyc", ".sqlite", ".sqlite3"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def include(relative: Path) -> bool:
    if any(part in EXCLUDED_PARTS for part in relative.parts):
        return False
    if relative.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    return (
        len(relative.parts) == 1 and relative.name in INCLUDED_ROOT_FILES
    ) or (relative.parts and relative.parts[0] in INCLUDED_ROOTS)


def files(root: Path) -> dict[str, str]:
    values = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if include(relative):
            values[relative.as_posix()] = sha256(path)
    return values


def category(path: str) -> str:
    if path.startswith("src/envs/campus_pulse_governance/"):
        return "CampusPulse simulation kernel"
    if path.startswith("src/backend/campus_pulse") or path == "src/backend/campus_pulse_app.py" or path == "src/backend/routers/campus_pulse.py":
        return "CampusPulse API and runtime"
    if path.startswith("src/backend/tests/"):
        return "Backend tests"
    if path.startswith("src/frontend/"):
        return "CampusPulse product frontend"
    if path.startswith("scripts/campus_pulse/"):
        return "Data preparation and release tools"
    if path.startswith("scripts/"):
        return "Experiment and operations tools"
    if path.startswith("config/"):
        return "Configuration"
    if path.startswith("src/envs/rumor_spiral_governance/"):
        return "Governance scenario extension"
    if path.startswith("src/"):
        return "Upstream integration"
    return "Build and launch"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--project", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--include-absent",
        action="store_true",
        help=(
            "also list official-main files absent from this project; "
            "the default manifest answers only added/modified code"
        ),
    )
    args = parser.parse_args()

    upstream = args.upstream.expanduser().resolve()
    project = args.project.expanduser().resolve()
    if not (upstream / "src").is_dir() or not (project / "src").is_dir():
        parser.error("both --upstream and --project must be YuLan-OneSim roots")

    upstream_files = files(upstream)
    project_files = files(project)
    rows = []
    comparison_paths = set(project_files)
    if args.include_absent:
        comparison_paths |= set(upstream_files)
    for path in sorted(comparison_paths):
        if path not in upstream_files:
            status = "added"
        elif path not in project_files:
            status = "absent_from_project"
        elif upstream_files[path] != project_files[path]:
            status = "modified"
        else:
            continue
        rows.append(
            {
                "status": status,
                "category": category(path),
                "path": path,
            }
        )

    output = args.output
    if not output.is_absolute():
        output = project / output
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "status",
                "category",
                "path",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    counts = {
        status: sum(row["status"] == status for row in rows)
        for status in ("added", "modified", "absent_from_project")
    }
    print(
        f"wrote {len(rows)} rows to {output} "
        f"(added={counts['added']}, modified={counts['modified']}, "
        f"absent_from_project={counts['absent_from_project']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
