"""Dedicated, allow-listed FastAPI application for CampusPulse.

This application intentionally does not mount the upstream OneSim code
generation, generic simulation, model configuration, private-agent or
filesystem routers.
"""

from __future__ import annotations

import ipaddress
import os

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend.routers.campus_pulse import get_service, router


_VALIDATION_LOCATION_ROOTS = {"body", "header", "path", "query"}
_PUBLIC_REQUEST_FIELDS = {
    "action_id",
    "actions",
    "after_sequence",
    "agent_count",
    "analysis_id",
    "claim_boundary",
    "commitment",
    "description",
    "evaluation_level",
    "evaluation_mode",
    "evidence_snapshot_id",
    "execution_mode",
    "expected_state_version",
    "governance_domain",
    "idempotency-key",
    "is_baseline",
    "label",
    "limit",
    "make_primary",
    "max_attempts",
    "max_workers",
    "model_name",
    "base_url",
    "max_concurrency",
    "max_output_tokens",
    "name",
    "objective",
    "phase_id",
    "phases",
    "policy_ids",
    "population_release_id",
    "provider_call_limit",
    "provider_profile_id",
    "project_id",
    "reason_code",
    "reasoning_effort",
    "release_key",
    "run_id",
    "scenario_id",
    "seeds",
    "shock_origin",
    "snapshot_id",
    "token_budget",
    "text",
    "target_language",
    "temperature",
    "timeout_seconds",
    "turn_budget",
    "window",
}


def _controlled_validation_location(error: dict) -> list[str | int]:
    """Project a Pydantic error location without echoing user-controlled data."""

    location = error.get("loc")
    if not isinstance(location, (tuple, list)) or not location:
        return ["request"]
    root = str(location[0]).lower()
    controlled: list[str | int] = [
        root if root in _VALIDATION_LOCATION_ROOTS else "request"
    ]
    for item in location[1:]:
        if isinstance(item, int) and item >= 0:
            controlled.append(item)
            continue
        normalized = str(item).lower()
        if normalized in _PUBLIC_REQUEST_FIELDS:
            controlled.append(normalized)
    return controlled


def _bind_host() -> str:
    host = os.environ.get("CAMPUS_PULSE_BIND_HOST", "127.0.0.1").strip()
    if not host:
        raise RuntimeError("CAMPUS_PULSE_BIND_HOST must not be empty")

    is_loopback = host.lower() == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_loopback = False

    network_opt_in = (
        os.environ.get(
            "CAMPUS_PULSE_ALLOW_UNAUTHENTICATED_NETWORK",
            "",
        )
        .strip()
        .lower()
        == "true"
    )
    if not is_loopback and not network_opt_in:
        raise RuntimeError(
            "Unauthenticated CampusPulse may bind only to loopback; "
            "set CAMPUS_PULSE_ALLOW_UNAUTHENTICATED_NETWORK=true only "
            "inside an explicitly protected network boundary"
        )
    return host


def _allowed_origins() -> list[str]:
    configured = os.environ.get(
        "CAMPUS_PULSE_ALLOWED_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173",
    )
    origins = [origin.strip() for origin in configured.split(",") if origin.strip()]
    if not origins:
        raise RuntimeError("CAMPUS_PULSE_ALLOWED_ORIGINS must not be empty")
    if "*" in origins:
        raise RuntimeError("Wildcard CORS origins are forbidden")
    return origins


def create_app() -> FastAPI:
    app = FastAPI(
        title="CampusPulse Governance Workbench API",
        description=(
            "Internal single-node API for evidence-bound public-opinion "
            "simulation workbench records."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Idempotency-Key"],
    )
    app.include_router(router)

    @app.exception_handler(RequestValidationError)
    async def controlled_request_validation_error(
        request: Request,
        exc: RequestValidationError,
    ):
        del request
        issues = []
        seen_locations = set()
        for error in exc.errors():
            location = _controlled_validation_location(error)
            location_key = tuple(location)
            if location_key in seen_locations:
                continue
            seen_locations.add(location_key)
            issues.append(
                {
                    "code": "invalid_request_field",
                    "location": location,
                }
            )
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "RequestValidationError",
                    "message": "Request validation failed",
                    "issues": issues,
                }
            },
        )

    @app.get("/health")
    def health():
        return {
            "status": "healthy",
            "service": "campus-pulse-workbench",
            "internet_ready": False,
        }

    @app.get("/ready")
    def readiness():
        try:
            report = get_service().readiness()
        except Exception:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "service": "campus-pulse-workbench",
                    "checks": {
                        "database": "not_ready",
                        "schema": "unknown",
                        "runtime_adapter": "unknown",
                        "worker_heartbeat": "invalid",
                    },
                    "runtime_enqueue_ready": False,
                    "runtime_inline_execution": False,
                },
            )
        return JSONResponse(
            status_code=200 if report["status"] == "ready" else 503,
            content=report,
        )

    return app


app = create_app()


def start_server() -> None:
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host=_bind_host(), port=port)


if __name__ == "__main__":
    start_server()
