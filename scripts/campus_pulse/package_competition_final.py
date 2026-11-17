#!/usr/bin/env python3
"""Build and verify the immutable CampusPulse competition Final handoff."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.campus_pulse.run_competition_demo import (
    BuildAudit,
    CompetitionDemoError,
    audit_build,
)


PACKAGE_SCHEMA_VERSION = "campus-pulse-competition-final-package-v1"
PACKAGE_ROOT = "CampusPulse-Competition-Final"
PACKAGE_MANIFEST_NAME = "PACKAGE-MANIFEST.json"
FIXED_ZIP_TIME = (2026, 7, 27, 0, 0, 0)
FINAL_AUDIT_PATH = Path(
    "specs/014-competition-final-results/competition-final-audit-v1.json"
)
FORBIDDEN_PACKAGE_SUFFIXES = frozenset(
    {
        ".csv",
        ".db",
        ".env",
        ".key",
        ".pem",
        ".sqlite",
        ".sqlite3",
        ".tsv",
        ".xls",
        ".xlsx",
    }
)
STATIC_MEMBERS = (
    Path("START_COMPETITION_DEMO.cmd"),
    Path("start-competition-demo.sh"),
    Path("scripts/campus_pulse/run_competition_demo.py"),
    Path("scripts/campus_pulse/package_competition_final.py"),
    Path("COMPETITION_DEMO.md"),
    Path("README_CAMPUS_PULSE.md"),
    Path("RELEASE_NOTES_CAMPUS_PULSE_V1.md"),
    Path("PRODUCT_ROADMAP.md"),
    Path(
        "specs/004-data-grounded-representative-population/"
        "release-audit-v1.json"
    ),
    Path("specs/013-governance-orchestration/spec.md"),
    Path("specs/013-governance-orchestration/phase-a-audit-v1.json"),
    Path("specs/013-governance-orchestration/phase-b-audit-v1.json"),
    Path(
        "specs/013-governance-orchestration/"
        "phase-c-comparison-audit-v1.json"
    ),
    Path("specs/014-competition-final-results/browser-qa-20260727.md"),
    Path("specs/014-competition-final-results/spec.md"),
    FINAL_AUDIT_PATH,
)


class CompetitionFinalPackageError(RuntimeError):
    """Raised when the Final handoff cannot be proven safe."""


@dataclass(frozen=True)
class FinalPackageResult:
    output_path: Path
    sha256_path: Path
    package_sha256: str
    member_count: int
    total_uncompressed_bytes: int
    build_fingerprint: str
    final_audit_sha256: str

    def public_dict(self) -> dict[str, object]:
        return {
            "status": "packaged",
            "schema_version": PACKAGE_SCHEMA_VERSION,
            "release_tier": "competition_final",
            "output": self.output_path.name,
            "sha256_file": self.sha256_path.name,
            "package_sha256": self.package_sha256,
            "member_count": self.member_count,
            "total_uncompressed_bytes": self.total_uncompressed_bytes,
            "build_fingerprint": self.build_fingerprint,
            "final_audit_sha256": self.final_audit_sha256,
            "provider_calls": 0,
            "frozen_rc_mutated": False,
        }


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_json(value: object) -> bytes:
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


def _safe_member(root: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        raise CompetitionFinalPackageError(
            f"unsafe package member: {relative}"
        )
    if relative.suffix.lower() in FORBIDDEN_PACKAGE_SUFFIXES:
        raise CompetitionFinalPackageError(
            f"private or mutable file type is forbidden: {relative}"
        )
    path = root / relative
    if path.is_symlink():
        raise CompetitionFinalPackageError(
            f"symbolic links are forbidden: {relative.as_posix()}"
        )
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise CompetitionFinalPackageError(
            f"package member escapes repository: {relative.as_posix()}"
        ) from exc
    if not resolved.is_file():
        raise CompetitionFinalPackageError(
            f"required package member is missing: {relative.as_posix()}"
        )
    return resolved


def _load_final_audit(root: Path) -> tuple[dict[str, object], str]:
    path = _safe_member(root, FINAL_AUDIT_PATH)
    content = path.read_bytes()
    try:
        audit = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompetitionFinalPackageError(
            "Final audit is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(audit, dict):
        raise CompetitionFinalPackageError("Final audit must be an object")
    if (
        audit.get("schema_version")
        != "campus-pulse-competition-final-audit-v1"
    ):
        raise CompetitionFinalPackageError(
            "Final audit schema is not approved"
        )
    if audit.get("release_status") != "competition_final_ready":
        raise CompetitionFinalPackageError(
            "Final audit is not release-ready"
        )
    frozen_rc = audit.get("frozen_rc")
    if (
        not isinstance(frozen_rc, dict)
        or frozen_rc.get("mutated") is not False
    ):
        raise CompetitionFinalPackageError(
            "Final audit does not preserve the frozen RC"
        )
    automated_gates = audit.get("automated_gates")
    if (
        not isinstance(automated_gates, dict)
        or automated_gates.get("provider_calls") != 0
    ):
        raise CompetitionFinalPackageError(
            "Final audit Provider boundary is invalid"
        )
    audited_files = audit.get("audited_files")
    if not isinstance(audited_files, dict) or not audited_files:
        raise CompetitionFinalPackageError(
            "Final audit has no file identity set"
        )
    for raw_path, expected_sha256 in audited_files.items():
        if not isinstance(raw_path, str) or not isinstance(
            expected_sha256, str
        ):
            raise CompetitionFinalPackageError(
                "Final audit file identity is malformed"
            )
        relative = Path(PurePosixPath(raw_path))
        actual = _sha256_file(_safe_member(root, relative))
        if actual != expected_sha256:
            raise CompetitionFinalPackageError(
                f"Final audited file hash mismatch: {raw_path}"
            )
    return audit, _sha256_bytes(content)


def _validate_build_identity(
    audit: dict[str, object],
    build_audit: BuildAudit,
) -> None:
    automated = audit.get("automated_gates")
    if not isinstance(automated, dict):
        raise CompetitionFinalPackageError(
            "Final audit automated gates are invalid"
        )
    production = automated.get(
        "frontend_production_build_authorized_server"
    )
    if not isinstance(production, dict):
        raise CompetitionFinalPackageError(
            "Final audit has no production build identity"
        )
    expected = {
        "build_fingerprint": build_audit.build_fingerprint,
        "file_count": build_audit.file_count,
        "total_bytes": build_audit.total_bytes,
    }
    for field, actual in expected.items():
        if production.get(field) != actual:
            raise CompetitionFinalPackageError(
                f"production build {field} mismatch"
            )


def _package_members(root: Path) -> list[tuple[Path, Path]]:
    members: dict[str, tuple[Path, Path]] = {}
    for relative in STATIC_MEMBERS:
        source = _safe_member(root, relative)
        members[relative.as_posix()] = (relative, source)

    dist_root = root / "src/frontend/dist"
    for source in sorted(
        (path for path in dist_root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix(),
    ):
        if source.is_symlink():
            raise CompetitionFinalPackageError(
                "symbolic links are forbidden in frontend build"
            )
        relative = source.relative_to(root)
        if relative.suffix.lower() in FORBIDDEN_PACKAGE_SUFFIXES:
            raise CompetitionFinalPackageError(
                f"forbidden frontend package member: {relative.as_posix()}"
            )
        members[relative.as_posix()] = (relative, source)
    return [members[key] for key in sorted(members)]


def _zip_info(relative: PurePosixPath, *, executable: bool) -> zipfile.ZipInfo:
    name = f"{PACKAGE_ROOT}/{relative.as_posix()}"
    info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    mode = 0o755 if executable else 0o644
    info.external_attr = mode << 16
    return info


def _manifest(
    *,
    files: dict[str, dict[str, object]],
    build_audit: BuildAudit,
    final_audit_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "release_tier": "competition_final",
        "package_root": PACKAGE_ROOT,
        "entrypoint_windows": "START_COMPETITION_DEMO.cmd",
        "entrypoint_posix": "start-competition-demo.sh",
        "offline_api_required": False,
        "provider_calls": 0,
        "frozen_rc_mutated": False,
        "build_fingerprint": build_audit.build_fingerprint,
        "final_audit_sha256": final_audit_sha256,
        "files": files,
    }


def build_package(root: Path, output: Path) -> FinalPackageResult:
    resolved_root = root.expanduser().resolve()
    if not resolved_root.is_dir():
        raise CompetitionFinalPackageError(
            "repository root does not exist"
        )
    try:
        build_audit = audit_build(
            resolved_root / "src/frontend/dist"
        )
    except CompetitionDemoError as exc:
        raise CompetitionFinalPackageError(str(exc)) from exc
    audit, final_audit_sha256 = _load_final_audit(resolved_root)
    _validate_build_identity(audit, build_audit)
    members = _package_members(resolved_root)

    file_manifest: dict[str, dict[str, object]] = {}
    payloads: list[tuple[Path, bytes]] = []
    for relative, source in members:
        content = source.read_bytes()
        key = relative.as_posix()
        file_manifest[key] = {
            "bytes": len(content),
            "sha256": _sha256_bytes(content),
        }
        payloads.append((relative, content))

    manifest_bytes = _canonical_json(
        _manifest(
            files=file_manifest,
            build_audit=build_audit,
            final_audit_sha256=final_audit_sha256,
        )
    )
    resolved_output = output.expanduser().resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved_output.with_name(
        f".{resolved_output.name}.tmp"
    )
    if temporary.exists():
        temporary.unlink()
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            for relative, content in payloads:
                executable = relative.suffix in {".py", ".sh"}
                archive.writestr(
                    _zip_info(
                        PurePosixPath(relative.as_posix()),
                        executable=executable,
                    ),
                    content,
                )
            archive.writestr(
                _zip_info(
                    PurePosixPath(PACKAGE_MANIFEST_NAME),
                    executable=False,
                ),
                manifest_bytes,
            )
        os.replace(temporary, resolved_output)
    finally:
        if temporary.exists():
            temporary.unlink()

    package_sha256 = _sha256_file(resolved_output)
    sha256_path = resolved_output.with_suffix(
        f"{resolved_output.suffix}.sha256"
    )
    sha256_path.write_text(
        f"{package_sha256}  {resolved_output.name}\n",
        encoding="ascii",
        newline="\n",
    )
    result = FinalPackageResult(
        output_path=resolved_output,
        sha256_path=sha256_path,
        package_sha256=package_sha256,
        member_count=len(payloads) + 1,
        total_uncompressed_bytes=(
            sum(len(content) for _, content in payloads)
            + len(manifest_bytes)
        ),
        build_fingerprint=build_audit.build_fingerprint,
        final_audit_sha256=final_audit_sha256,
    )
    verify_package(resolved_output)
    return result


def verify_package(package_path: Path) -> dict[str, object]:
    resolved = package_path.expanduser().resolve()
    if not resolved.is_file():
        raise CompetitionFinalPackageError(
            "package ZIP does not exist"
        )
    manifest_member = f"{PACKAGE_ROOT}/{PACKAGE_MANIFEST_NAME}"
    with zipfile.ZipFile(resolved, "r") as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise CompetitionFinalPackageError(
                "package contains duplicate members"
            )
        if manifest_member not in names:
            raise CompetitionFinalPackageError(
                "package manifest is missing"
            )
        if not all(
            name.startswith(f"{PACKAGE_ROOT}/") for name in names
        ):
            raise CompetitionFinalPackageError(
                "package contains an unexpected root"
            )
        try:
            manifest = json.loads(
                archive.read(manifest_member).decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CompetitionFinalPackageError(
                "package manifest is invalid"
            ) from exc
        if manifest.get("schema_version") != PACKAGE_SCHEMA_VERSION:
            raise CompetitionFinalPackageError(
                "package manifest schema is invalid"
            )
        if manifest.get("release_tier") != "competition_final":
            raise CompetitionFinalPackageError(
                "package release tier is invalid"
            )
        if (
            manifest.get("provider_calls") != 0
            or manifest.get("offline_api_required") is not False
            or manifest.get("frozen_rc_mutated") is not False
        ):
            raise CompetitionFinalPackageError(
                "package safety boundary is invalid"
            )
        files = manifest.get("files")
        if not isinstance(files, dict):
            raise CompetitionFinalPackageError(
                "package file manifest is invalid"
            )
        expected_names = {
            f"{PACKAGE_ROOT}/{relative}" for relative in files
        } | {manifest_member}
        if set(names) != expected_names:
            raise CompetitionFinalPackageError(
                "ZIP member set differs from package manifest"
            )
        for relative, identity in files.items():
            if not isinstance(identity, dict):
                raise CompetitionFinalPackageError(
                    "package identity is malformed"
                )
            if (
                Path(relative).suffix.lower()
                in FORBIDDEN_PACKAGE_SUFFIXES
            ):
                raise CompetitionFinalPackageError(
                    f"forbidden package member: {relative}"
                )
            content = archive.read(f"{PACKAGE_ROOT}/{relative}")
            if (
                identity.get("bytes") != len(content)
                or identity.get("sha256")
                != _sha256_bytes(content)
            ):
                raise CompetitionFinalPackageError(
                    f"package member identity mismatch: {relative}"
                )
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build or verify the privacy-gated CampusPulse Final ZIP."
        )
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "_release/campus-pulse-competition-final.zip"
        ),
    )
    parser.add_argument(
        "--verify",
        type=Path,
        help="verify an existing Final package instead of creating one",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.verify is not None:
        manifest = verify_package(args.verify)
        print(
            json.dumps(
                {
                    "status": "verified",
                    "schema_version": manifest["schema_version"],
                    "release_tier": manifest["release_tier"],
                    "file_count": len(manifest["files"]),
                    "provider_calls": manifest["provider_calls"],
                    "frozen_rc_mutated": manifest[
                        "frozen_rc_mutated"
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    result = build_package(args.root, args.output)
    print(
        json.dumps(
            result.public_dict(),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
