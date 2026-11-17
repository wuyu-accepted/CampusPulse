#!/usr/bin/env python3
"""Launch the CampusPulse product UI and its dedicated local API.

This starts the real workbench control plane and the production frontend
preview, waits for both services, and shuts both child processes down together.
The local public-progress bridge starts with the product. Provider-backed
execution still requires an explicit click in the Century Gym run console.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import hashlib
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser


DEFAULT_API_PORT = 8000
DEFAULT_UI_PORT = 4173
DEFAULT_PROGRESS_PORT = 8766
CENTURY_GYM_SESSION = "century-gym-full"
CENTURY_GYM_REPLAY_SESSION = "century-gym-replay"
CENTURY_GYM_DEMO_SESSION = "century-gym-demo"


class FullSystemError(RuntimeError):
    """Raised when a local product service cannot be started safely."""


def _run_setup(command: list[str], cwd: Path, label: str) -> None:
    """Run a deterministic first-launch setup step with a useful failure."""

    print(f"CampusPulse first launch: {label}...", flush=True)
    completed = subprocess.run(command, cwd=cwd, check=False)
    if completed.returncode != 0:
        raise FullSystemError(
            f"{label} failed (exit={completed.returncode}); see the output above"
        )


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _npm_command() -> str:
    executable = "npm.cmd" if os.name == "nt" else "npm"
    resolved = shutil.which(executable)
    if resolved is None:
        raise FullSystemError("npm is required to start the frontend preview")
    return resolved


def _ensure_backend_runtime(root: Path) -> None:
    """Install the frozen local API runtime only when it is absent."""

    if importlib.util.find_spec("uvicorn") and importlib.util.find_spec("fastapi"):
        return
    lockfile = root / "requirements-campus-pulse-online.lock"
    if not lockfile.is_file():
        raise FullSystemError("backend dependency lockfile is missing")
    _run_setup(
        [sys.executable, "-m", "pip", "install", "-r", str(lockfile)],
        root,
        "installing the frozen backend runtime",
    )


def _ensure_frontend_build(frontend: Path) -> None:
    """Build the checked-out frontend so the launcher always serves its source."""

    output = frontend / "dist" / "index.html"
    npm = _npm_command()
    if not (frontend / "node_modules").is_dir():
        install = "ci" if (frontend / "package-lock.json").is_file() else "install"
        _run_setup([npm, install], frontend, "installing frontend dependencies")
    _run_setup(
        [npm, "run", "build", "--", "--configLoader", "runner"],
        frontend,
        "building the checked-out frontend",
    )
    if not output.is_file():
        raise FullSystemError("frontend build completed without dist/index.html")


def _seed_public_progress(
    root: Path,
    *,
    evidence_release: str,
    session_id: str,
) -> None:
    """Install or refresh one tracked public-progress projection."""

    source = root / "evidence" / evidence_release / "public-progress"
    target = (
        root
        / "_run"
        / "forum-twin-live-sessions"
        / session_id
        / "public-progress"
    )
    source_manifest = source / "latest.json"
    if not source_manifest.is_file():
        return
    target_manifest = target / "latest.json"
    if target_manifest.is_file():
        source_digest = hashlib.sha256(source_manifest.read_bytes()).digest()
        target_digest = hashlib.sha256(target_manifest.read_bytes()).digest()
        if source_digest == target_digest:
            return
    if target.is_dir():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def _seed_century_gym_public_replays(root: Path) -> None:
    """Seed the immutable audit replay and the full Tick 0--10 demo view."""

    _seed_public_progress(
        root,
        evidence_release="century-gym-live-v1",
        session_id=CENTURY_GYM_REPLAY_SESSION,
    )
    _seed_public_progress(
        root,
        evidence_release="century-gym-demo-v2",
        session_id=CENTURY_GYM_DEMO_SESSION,
    )


def _wait_for(
    url: str,
    process: subprocess.Popen,
    label: str,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise FullSystemError(
                f"{label} stopped before becoming ready (exit={process.returncode})"
            )
        try:
            with urlopen(url, timeout=1.5) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            pass
        time.sleep(0.25)
    raise FullSystemError(f"{label} did not become ready within {timeout:g}s")


def _stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start the CampusPulse frontend and dedicated local API."
    )
    parser.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    parser.add_argument("--ui-port", type=int, default=DEFAULT_UI_PORT)
    parser.add_argument("--progress-port", type=int, default=DEFAULT_PROGRESS_PORT)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--demo", action="store_true", help="Use an isolated project database for a recorded-run walkthrough")
    parser.add_argument("--startup-timeout", type=float, default=30.0)
    return parser


def main() -> int:
    args = _parser().parse_args()
    root = _project_root()
    frontend = root / "src" / "frontend"
    if not all(1 <= port <= 65535 for port in (args.api_port, args.ui_port, args.progress_port)):
        raise FullSystemError("ports must be between 1 and 65535")

    _ensure_backend_runtime(root)
    _ensure_frontend_build(frontend)
    _seed_century_gym_public_replays(root)

    environment = os.environ.copy()
    python_path = os.pathsep.join((str(root), str(root / "src")))
    existing_python_path = environment.get("PYTHONPATH", "").strip()
    environment["PYTHONPATH"] = (
        os.pathsep.join((python_path, existing_python_path))
        if existing_python_path
        else python_path
    )
    environment.setdefault(
        "CAMPUS_PULSE_WORKBENCH_DB",
        str(root / "data" / "campus_pulse_workspace" / "workbench.sqlite3"),
    )
    environment["CAMPUS_PULSE_RUNTIME_ADAPTER"] = "disabled"
    if args.demo:
        environment["CAMPUS_PULSE_DEMO_WORKSPACE"] = "1"
        environment["CAMPUS_PULSE_WORKBENCH_DB"] = str(root / "_run" / "demo-workspace" / "workbench.sqlite3")
    environment["CAMPUS_PULSE_LIVE_LLM_ENABLED"] = "false"
    environment["CAMPUS_PULSE_BIND_HOST"] = "127.0.0.1"
    environment["PORT"] = str(args.api_port)
    environment["CAMPUS_PULSE_API_TARGET"] = (
        f"http://127.0.0.1:{args.api_port}"
    )

    api_process: subprocess.Popen | None = None
    ui_process: subprocess.Popen | None = None
    progress_process: subprocess.Popen | None = None
    try:
        progress_process = subprocess.Popen(
            [
                sys.executable,
                str(root / "scripts" / "serve_forum_live_progress.py"),
                "--root",
                str(root / "_run" / "forum-twin-live-sessions"),
                "--host",
                "127.0.0.1",
                "--port",
                str(args.progress_port),
            ],
            cwd=root,
            env=environment,
        )
        _wait_for(
            f"http://127.0.0.1:{args.progress_port}/health",
            progress_process,
            "CampusPulse public progress bridge",
            args.startup_timeout,
        )
        api_process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "backend.campus_pulse_app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.api_port),
            ],
            cwd=root,
            env=environment,
        )
        _wait_for(
            f"http://127.0.0.1:{args.api_port}/health",
            api_process,
            "CampusPulse API",
            args.startup_timeout,
        )

        ui_process = subprocess.Popen(
            [
                _npm_command(),
                "run",
                "preview",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.ui_port),
                "--strictPort",
                "--configLoader",
                "runner",
            ],
            cwd=frontend,
            env=environment,
        )
        product_url = f"http://127.0.0.1:{args.ui_port}/campus-pulse"
        _wait_for(
            product_url,
            ui_process,
            "CampusPulse frontend",
            args.startup_timeout,
        )
        _wait_for(
            (
                f"http://127.0.0.1:{args.ui_port}"
                "/api/campus-pulse/v1/health"
            ),
            ui_process,
            "CampusPulse frontend-to-API proxy",
            args.startup_timeout,
        )
        print(
            json.dumps(
                {
                    "status": "ready",
                    "url": product_url,
                    "api": f"http://127.0.0.1:{args.api_port}",
                    "workbench_api": True,
                    "live_llm_enabled": False,
                    "public_progress": f"http://127.0.0.1:{args.progress_port}",
                    "provider_calls": 0,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        if args.check:
            return 0
        if not args.no_open:
            webbrowser.open(product_url, new=2)
        while (
            api_process.poll() is None
            and ui_process.poll() is None
            and progress_process.poll() is None
        ):
            time.sleep(0.5)
        raise FullSystemError("a CampusPulse child service stopped unexpectedly")
    except KeyboardInterrupt:
        return 0
    finally:
        _stop(ui_process)
        _stop(api_process)
        _stop(progress_process)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FullSystemError as exc:
        print(f"CampusPulse full-system error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
