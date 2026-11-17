"""Serve hash-verified public ForumTwin ticks to a local demo UI.

This server exposes no directory listing and never reads the Provider ledger,
prompt cache, private snapshots or credential file.  It is intentionally a
small read-only bridge between the authorized runner's public-progress folder
and the browser used for the competition demo.
"""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.campus_pulse.runtime_contract import canonical_sha256  # noqa: E402


SESSION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
BRANCHES = {"shared_baseline", "Natural", "A", "B", "C", "D"}
MAX_JSON_BYTES = 16 * 1024 * 1024


class PublicProgressError(ValueError):
    """Raised when a requested public artifact is absent or invalid."""


def _load_verified(path: Path) -> tuple[dict[str, Any], bytes, str]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise FileNotFoundError(path.name) from exc
    if not payload or len(payload) > MAX_JSON_BYTES:
        raise PublicProgressError("public progress asset size is invalid")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicProgressError("public progress asset is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise PublicProgressError("public progress root must be an object")
    expected = value.get("content_sha256")
    actual = canonical_sha256(
        {key: child for key, child in value.items() if key != "content_sha256"}
    )
    if expected != actual:
        raise PublicProgressError("public progress content hash mismatch")
    return value, payload, actual


class PublicProgressStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _session_root(self, session_id: str) -> Path:
        if SESSION_PATTERN.fullmatch(session_id) is None:
            raise PublicProgressError("session id is invalid")
        path = (self.root / session_id / "public-progress").resolve()
        if self.root not in path.parents:
            raise PublicProgressError("session path escaped the live root")
        return path

    def manifest(self, session_id: str) -> tuple[dict[str, Any], bytes, str]:
        value, payload, digest = _load_verified(
            self._session_root(session_id) / "latest.json"
        )
        if value.get("schema_version") != "campus-pulse-forum-live-progress-v1":
            raise PublicProgressError("public progress manifest schema is invalid")
        if value.get("contains_private_state") is not False:
            raise PublicProgressError("manifest does not deny private state")
        if value.get("contains_prompt_or_provider_body") is not False:
            raise PublicProgressError("manifest does not deny prompt bodies")
        return value, payload, digest

    def tick(
        self,
        session_id: str,
        branch: str,
        tick: int,
    ) -> tuple[dict[str, Any], bytes, str]:
        if branch not in BRANCHES or not 0 <= tick <= 23:
            raise PublicProgressError("public tick cursor is invalid")
        manifest, _manifest_payload, _manifest_digest = self.manifest(session_id)
        relative = f"{branch}/T{tick:02d}.json"
        committed = {
            (str(row.get("branch")), int(row.get("tick", -1))): row
            for row in manifest.get("committed_ticks", [])
            if isinstance(row, Mapping)
        }
        entry = committed.get((branch, tick))
        if entry is None or entry.get("path") != relative:
            raise FileNotFoundError(relative)
        value, payload, digest = _load_verified(
            self._session_root(session_id) / branch / f"T{tick:02d}.json"
        )
        if (
            value.get("schema_version") != "campus-pulse-forum-public-tick-v1"
            or value.get("branch") != branch
            or int(value.get("tick", -1)) != tick
            or value.get("tick_committed") is not True
            or entry.get("content_sha256") != digest
        ):
            raise PublicProgressError("public tick does not match its manifest")
        return value, payload, digest


def _handler(store: PublicProgressStore) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "CampusPulsePublicProgress/1"

        def _json_response(self, payload: bytes, digest: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-SHA256", digest)
            self.send_header("Access-Control-Expose-Headers", "X-Content-SHA256")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)

        def _problem(self, status: HTTPStatus, detail: str) -> None:
            payload = json.dumps(
                {"error": status.phrase, "detail": detail},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            path = unquote(urlsplit(self.path).path)
            if path == "/health":
                payload = b'{"status":"healthy","read_only":true}'
                self._json_response(payload, canonical_sha256(json.loads(payload)))
                return
            parts = [part for part in path.split("/") if part]
            try:
                if len(parts) == 3 and parts[0] == "sessions" and parts[2] == "manifest":
                    _value, payload, digest = store.manifest(parts[1])
                elif (
                    len(parts) == 5
                    and parts[0] == "sessions"
                    and parts[2] == "ticks"
                ):
                    try:
                        tick = int(parts[4])
                    except ValueError as exc:
                        raise PublicProgressError("tick must be an integer") from exc
                    _value, payload, digest = store.tick(parts[1], parts[3], tick)
                else:
                    self._problem(HTTPStatus.NOT_FOUND, "public endpoint not found")
                    return
            except FileNotFoundError:
                self._problem(HTTPStatus.NOT_FOUND, "committed public artifact not found")
                return
            except PublicProgressError as exc:
                self._problem(HTTPStatus.CONFLICT, str(exc))
                return
            self._json_response(payload, digest)

        def log_message(self, format: str, *args: Any) -> None:
            # Avoid accidentally logging query strings or future user input.
            return

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT / "_run" / "forum-twin-live-sessions",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    arguments = parser.parse_args()
    if arguments.host not in {"127.0.0.1", "localhost"}:
        raise ValueError("public progress server is local-only")
    if not 1024 <= arguments.port <= 65535:
        raise ValueError("public progress port is invalid")
    store = PublicProgressStore(arguments.root)
    server = ThreadingHTTPServer(
        (arguments.host, arguments.port), _handler(store)
    )
    print(
        json.dumps(
            {
                "status": "serving",
                "host": arguments.host,
                "port": arguments.port,
                "root": str(store.root),
                "read_only": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
