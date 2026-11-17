#!/usr/bin/env python3
"""Serve the frozen CampusPulse competition product without API dependencies.

All CampusPulse product pages are compiled into static assets.
This launcher intentionally binds only to a loopback address, provides SPA
fallback for every product deep link, and never starts a model transport. The
workbench UI remains available in an explicit read-only/offline state. This is
the on-site fallback when the full local API is unavailable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import threading
import webbrowser
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit
from urllib.request import urlopen


HEALTH_PATH = "/_campus_pulse_demo_health"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4173
SPA_ROUTE_PREFIX = "/campus-pulse"
FORBIDDEN_FILE_SUFFIXES = frozenset(
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
FORBIDDEN_TEXT_MARKERS = (
    "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
)
FORBIDDEN_TEXT_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"),
    re.compile(
        r"(?<!\d)(?:"
        r"10(?:\.\d{1,3}){3}|"
        r"192\.168(?:\.\d{1,3}){2}|"
        r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"
        r")(?!\d)"
    ),
    re.compile(
        r"\b[A-Z][A-Z0-9_]{3,}_"
        r"(?:API_KEY|ACCESS_TOKEN|SECRET_KEY|AUTH_TOKEN)\b"
    ),
)
ASSET_REFERENCE_RE = re.compile(
    r"""(?:src|href)=["'](?P<path>/assets/[^"'?#]+)""",
    flags=re.IGNORECASE,
)


class CompetitionDemoError(RuntimeError):
    """Raised when the frozen demo is unsafe or incomplete."""


@dataclass(frozen=True)
class BuildAudit:
    build_dir: Path
    file_count: int
    asset_count: int
    total_bytes: int
    build_fingerprint: str

    def public_dict(self) -> dict[str, object]:
        return {
            "status": "ready",
            "mode": "competition_offline_static",
            "provider_calls": 0,
            "api_required": False,
            "workbench_available": False,
            "workbench_ui_available": True,
            "file_count": self.file_count,
            "asset_count": self.asset_count,
            "total_bytes": self.total_bytes,
            "build_fingerprint": self.build_fingerprint,
        }


def _iter_files(build_dir: Path) -> Iterable[Path]:
    yield from sorted(
        (path for path in build_dir.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(build_dir).as_posix(),
    )


def _hash_build(build_dir: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(build_dir).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def audit_build(build_dir: Path) -> BuildAudit:
    resolved = build_dir.expanduser().resolve()
    if not resolved.is_dir():
        raise CompetitionDemoError(
            f"frontend build directory does not exist: {resolved}"
        )

    index_path = resolved / "index.html"
    if not index_path.is_file():
        raise CompetitionDemoError("frontend build is missing index.html")

    files = list(_iter_files(resolved))
    if not files:
        raise CompetitionDemoError("frontend build is empty")

    for path in files:
        if path.suffix.lower() in FORBIDDEN_FILE_SUFFIXES:
            relative = path.relative_to(resolved).as_posix()
            raise CompetitionDemoError(
                f"private or mutable file type is forbidden in demo build: {relative}"
            )

    index_text = index_path.read_text(encoding="utf-8")
    referenced_assets = {
        match.group("path").removeprefix("/")
        for match in ASSET_REFERENCE_RE.finditer(index_text)
    }
    if not referenced_assets:
        raise CompetitionDemoError(
            "index.html does not reference a compiled /assets/ bundle"
        )
    missing_assets = sorted(
        asset for asset in referenced_assets if not (resolved / asset).is_file()
    )
    if missing_assets:
        raise CompetitionDemoError(
            f"index.html references missing assets: {', '.join(missing_assets)}"
        )

    textual_suffixes = {".css", ".html", ".js", ".json", ".map", ".svg"}
    for path in files:
        if path.suffix.lower() not in textual_suffixes:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for marker in FORBIDDEN_TEXT_MARKERS:
            if marker in text:
                relative = path.relative_to(resolved).as_posix()
                raise CompetitionDemoError(
                    f"forbidden sensitive marker found in {relative}"
                )
        for pattern in FORBIDDEN_TEXT_PATTERNS:
            if pattern.search(text):
                relative = path.relative_to(resolved).as_posix()
                raise CompetitionDemoError(
                    f"forbidden sensitive pattern found in {relative}"
                )

    total_bytes = sum(path.stat().st_size for path in files)
    return BuildAudit(
        build_dir=resolved,
        file_count=len(files),
        asset_count=len(referenced_assets),
        total_bytes=total_bytes,
        build_fingerprint=_hash_build(resolved, files),
    )


class CompetitionDemoHandler(SimpleHTTPRequestHandler):
    """Static handler with explicit health and SPA fallback contracts."""

    server_version = "CampusPulseCompetitionDemo/1.0"

    def __init__(
        self,
        *args,
        directory: str,
        build_audit: BuildAudit,
        quiet: bool = False,
        **kwargs,
    ):
        self.build_audit = build_audit
        self.quiet = quiet
        super().__init__(*args, directory=directory, **kwargs)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args) -> None:
        if not self.quiet:
            super().log_message(format, *args)

    def _send_health(self, *, head_only: bool) -> None:
        content = json.dumps(
            self.build_audit.public_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        if not head_only:
            self.wfile.write(content)

    def _requested_path(self) -> str:
        parsed = urlsplit(self.path)
        return parsed.path

    def _prepare_spa_path(self, path: str) -> None:
        if path == "/" or path == SPA_ROUTE_PREFIX or path.startswith(
            f"{SPA_ROUTE_PREFIX}/"
        ):
            self.path = "/index.html"

    def do_GET(self) -> None:
        path = self._requested_path()
        if path == HEALTH_PATH:
            self._send_health(head_only=False)
            return
        self._prepare_spa_path(path)
        super().do_GET()

    def do_HEAD(self) -> None:
        path = self._requested_path()
        if path == HEALTH_PATH:
            self._send_health(head_only=True)
            return
        self._prepare_spa_path(path)
        super().do_HEAD()


def create_server(
    audit: BuildAudit,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    quiet: bool = False,
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise CompetitionDemoError(
            "competition demo may bind only to a loopback address"
        )
    if not 0 <= port <= 65535:
        raise CompetitionDemoError("port must be between 0 and 65535")
    handler = partial(
        CompetitionDemoHandler,
        directory=str(audit.build_dir),
        build_audit=audit,
        quiet=quiet,
    )
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def smoke_server(audit: BuildAudit) -> dict[str, object]:
    server = create_server(audit, port=0, quiet=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    base_url = f"http://{host}:{port}"
    try:
        for route in (
            "/campus-pulse",
            "/campus-pulse/forum",
            "/campus-pulse/results",
            "/campus-pulse/workbench",
            "/campus-pulse/system",
        ):
            with urlopen(f"{base_url}{route}", timeout=5) as response:
                if response.status != 200 or b"<html" not in response.read().lower():
                    raise CompetitionDemoError(
                        f"SPA smoke request returned invalid HTML for {route}"
                    )
        with urlopen(f"{base_url}{HEALTH_PATH}", timeout=5) as response:
            health = json.loads(response.read().decode("utf-8"))
        if health != audit.public_dict():
            raise CompetitionDemoError("health identity differs from build audit")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return health


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Serve the frozen CampusPulse competition frontend on loopback "
            "without starting an API or model transport."
        )
    )
    parser.add_argument(
        "--build-dir",
        type=Path,
        default=Path("src/frontend/dist"),
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="audit the build and run an ephemeral HTTP smoke test, then exit",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="open the competition home page in the default browser",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    audit = audit_build(args.build_dir)
    if args.check:
        health = smoke_server(audit)
        print(json.dumps(health, ensure_ascii=False, sort_keys=True))
        return 0

    server = create_server(
        audit,
        host=args.host,
        port=args.port,
        quiet=args.quiet,
    )
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/campus-pulse"
    print(
        json.dumps(
            {
                **audit.public_dict(),
                "url": url,
                "binding": "loopback_only",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if args.open:
        webbrowser.open(url, new=2)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
