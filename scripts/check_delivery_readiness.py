from __future__ import annotations

import re
import subprocess
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_PATHS = (
    "README.md",
    "EXPERIMENT_REPORT.md",
    "docs/USER_GUIDE.md",
    "docs/ARCHITECTURE_AND_CODE.md",
    "docs/DATA_PIPELINE.md",
    "docs/UPSTREAM_COMPARISON.md",
    "docs/EVALUATION_REPRODUCTION.md",
    "data/campus_pulse_workspace/project-catalog.json",
    "evidence/final-projects/index.json",
    "START_FULL_SYSTEM.cmd",
    "src/backend/campus_pulse_app.py",
    "src/frontend/src/campus-pulse/live/CenturyGymLivePage.vue",
    "src/frontend/src/campus-pulse/live/centuryGymLive.ts",
    "evidence/century-gym-live-v1/public-progress/latest.json",
    "evidence/century-gym-live-v1/public-progress/shared_baseline/T03.json",
    "evidence/century-gym-live-v1/public-progress/shared_baseline/T04.json",
    "evidence/century-gym-live-v1/public-progress/Natural/T10.json",
    "evidence/century-gym-live-v1/public-progress/D/T10.json",
    "evidence/century-gym-live-v1/public-agent-trace-FT-0084.json",
    "evidence/four-dimension-v5-formal-v1/public-summary-v1.json",
    "evidence/four-dimension-v5-formal-v1/fidelity-evaluation-v3.json",
    "evidence/four-dimension-v5-formal-v1/persona-consistency-v3.svg",
    "evidence/four-dimension-v5-formal-v1/population-heterogeneity-v2.svg",
    "evidence/four-dimension-v5-formal-v1/particle-distributions-v2.svg",
    "evidence/four-dimension-v5-formal-v1/anonymous-particle-panel-v1.npz",
    "evidence/four-dimension-v5-formal-v1/anonymous-particle-panel-v1.manifest.json",
    "evidence/four-dimension-v5-formal-v1/reply-count-detail-v2.json",
    "evidence/metric-provenance-v2.json",
    "scripts/evaluate_forum_fidelity_v2.py",
    "scripts/recompute_public_metric_summary.py",
    "scripts/build_public_agent_decision_trace.py",
    "src/envs/campus_pulse_governance/forum_fidelity_v2.py",
    "scripts/compile_resource_flagship_product_validation.py",
    "scripts/analyze_resource_flagship_result.py",
    "scripts/publish_resource_flagship_evidence.py",
    "src/frontend/public/campus-pulse-data/resource-policy-live-r1.json",
    "src/frontend/public/campus-pulse-data/resource-policy-live-r1.sha256",
    "src/frontend/public/campus-pulse-data/lecture-open-choice-r4.json",
    "src/frontend/public/campus-pulse-data/lecture-open-choice-r4.sha256",
    "src/frontend/public/campus-pulse-data/resource-flagship-summary-v1.json",
    "src/frontend/public/campus-pulse-data/resource-flagship-summary-v1.sha256",
    "src/frontend/public/campus-pulse-data/resource-v5-formal-summary-v1.json",
    "src/frontend/public/campus-pulse-data/resource-v5-formal-summary-v1.sha256",
)

FORBIDDEN_DELIVERY_NAMES = {
    "api.txt",
    "provider-key",
    "authorized-live-ledger.sqlite3",
}
FORBIDDEN_DELIVERY_SUFFIXES = {
    ".sqlite",
    ".sqlite3",
    ".sqlite3-shm",
    ".sqlite3-wal",
}
SECRET_PATTERN = re.compile(rb"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_-])")


def delivery_files() -> list[Path]:
    """Return the candidate delivery tree from a checkout or source bundle."""
    command = [
        "git",
        "-c",
        f"safe.directory={ROOT.as_posix()}",
        "-C",
        str(ROOT),
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
    ]
    completed = subprocess.run(command, check=False, capture_output=True)
    if completed.returncode == 0:
        return [ROOT / entry.decode("utf-8") for entry in completed.stdout.split(b"\0") if entry]
    excluded = {
        ".git", ".idea", ".pytest_cache", ".venv", ".vscode", "__pycache__",
        "_run", "build", "dist", "node_modules", "playwright-report", "test-results",
    }
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file() and not any(part in excluded for part in path.relative_to(ROOT).parts)
    ]


def contains_secret(path: Path) -> bool:
    """Scan the whole file without loading large evidence or release assets into memory."""
    overlap = b""
    with path.open("rb") as stream:
        while chunk := stream.read(1_048_576):
            payload = overlap + chunk
            if SECRET_PATTERN.search(payload):
                return True
            overlap = payload[-128:]
    return False


def inspect_zip(path: Path) -> list[str]:
    """Inspect archive entry names and uncompressed payloads before delivery."""
    errors: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                archive_name = Path(info.filename.replace("\\", "/")).name.lower()
                if (
                    archive_name in FORBIDDEN_DELIVERY_NAMES
                    or any(archive_name.endswith(suffix) for suffix in FORBIDDEN_DELIVERY_SUFFIXES)
                ):
                    errors.append(f"forbidden file inside archive {path.relative_to(ROOT)}: {info.filename}")
                    continue
                overlap = b""
                with archive.open(info) as stream:
                    while chunk := stream.read(1_048_576):
                        payload = overlap + chunk
                        if SECRET_PATTERN.search(payload):
                            errors.append(
                                f"possible API key inside archive {path.relative_to(ROOT)}: {info.filename}"
                            )
                            break
                        overlap = payload[-128:]
    except (OSError, zipfile.BadZipFile) as exc:
        errors.append(f"cannot inspect archive {path.relative_to(ROOT)}: {exc}")
    return errors


def main() -> int:
    errors: list[str] = []

    for relative in REQUIRED_PATHS:
        if not (ROOT / relative).is_file():
            errors.append(f"missing required delivery file: {relative}")

    try:
        delivery = delivery_files()
    except OSError as exc:
        errors.append(f"unable to enumerate delivery files: {exc}")
        delivery = []

    for path in delivery:
        if not path.is_file():
            # A tracked path scheduled for deletion is already absent from the
            # delivery tree and should not be treated as an unreadable asset.
            continue
        lower_name = path.name.lower()
        if (
            lower_name in FORBIDDEN_DELIVERY_NAMES
            or any(lower_name.endswith(suffix) for suffix in FORBIDDEN_DELIVERY_SUFFIXES)
        ):
            errors.append(f"forbidden delivery file: {path.relative_to(ROOT)}")
            continue
        try:
            if path.suffix.lower() == ".zip":
                errors.extend(inspect_zip(path))
            elif contains_secret(path):
                errors.append(f"possible API key in delivery file: {path.relative_to(ROOT)}")
        except OSError as exc:
            errors.append(f"cannot inspect delivery file {path.relative_to(ROOT)}: {exc}")

    if errors:
        print("DELIVERY_READINESS_FAILED")
        for error in errors:
            print(error)
        return 1

    print(
        "DELIVERY_READINESS_OK "
        f"required={len(REQUIRED_PATHS)} delivery_scanned={len(delivery)} secrets=0"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
