import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

import openai
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.models.schemas import DeepHealthResponse, DepStatus, HealthResponse, VersionResponse
from app.routers import (
    admin,
    chat,
    deadlines,
    internal,
    limits,
    profile,
    push,
    scan,
    status,
    tracking,
    webhooks,
)
from app.routers import help as help_router
from app.services.embeddings import close_openai_client, get_openai_client
from app.services.help_loader import load_help_content
from app.services.rag import close_supabase, get_supabase, init_supabase

settings = get_settings()


def _load_git_sha() -> str:
    """Resolve the deployed commit SHA from CI-injected env vars.

    GIT_SHA is set explicitly by our workflow; Render and Railway populate
    their own values when the deploy came from a connected repo.
    Falls back to "unknown" so the endpoint never 500s in local/dev.
    """
    sha = (
        os.getenv("GIT_SHA")
        or os.getenv("RENDER_GIT_COMMIT")
        or os.getenv("RAILWAY_GIT_COMMIT_SHA")
        or "unknown"
    )
    return sha.strip() or "unknown"


GIT_SHA = _load_git_sha()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup
    logger.info("Starting Jasně API env=%s", settings.environment)
    # Help content is loaded before Supabase init so a bad YAML aborts the
    # deploy with a clear error before any DB connections are opened.
    load_help_content()
    await init_supabase()
    yield
    # --- shutdown
    logger.info("Shutting down")
    await close_openai_client()
    await close_supabase()


app = FastAPI(
    title="Jasně API",
    version="0.1.0",
    docs_url="/docs" if not settings.is_production else None,
    redoc_url=None,
    lifespan=lifespan,
)

app.state.git_sha = GIT_SHA

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_id_and_timing(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "%s %s -> %d in %.0fms req_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
        request_id,
    )
    return response


# --- Routers
app.include_router(scan.router)
app.include_router(chat.router)  # /scans/{scan_id}/chat (Pro Q&A)
app.include_router(deadlines.router)
app.include_router(profile.router)
app.include_router(limits.router)  # /limits — usage vs plan caps
app.include_router(status.router)  # /status/oamp — OAMP proceedings status
app.include_router(tracking.router)  # /trackings — generic Pro background tracking
app.include_router(webhooks.router)
app.include_router(push.router)  # /push-tokens — Expo push token registration
app.include_router(internal.router)  # /internal — cron-only jobs
app.include_router(help_router.router)  # /help — static content from YAML
app.include_router(admin.router)  # /admin — proxies for the static Help admin UI


# ---------------------------------------------------------------------------
# Global exception handlers — always return JSON
# ---------------------------------------------------------------------------


def _request_id_for(request: Request) -> str | None:
    return getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID")


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": jsonable_encoder(exc.errors()), "request_id": _request_id_for(request)},
    )


@app.exception_handler(openai.RateLimitError)
async def openai_rate_limit_handler(request: Request, exc: openai.RateLimitError):
    logger.error("OpenAI rate limit: %s", exc)
    return JSONResponse(
        status_code=429,
        content={
            "detail": "OpenAI quota exceeded — check billing at platform.openai.com",
            "request_id": _request_id_for(request),
        },
    )


@app.exception_handler(openai.APIStatusError)
async def openai_api_error_handler(request: Request, exc: openai.APIStatusError):
    logger.error("OpenAI API error %s: %s", exc.status_code, exc.message)
    return JSONResponse(
        status_code=502,
        content={"detail": f"OpenAI error: {exc.message}", "request_id": _request_id_for(request)},
    )


@app.exception_handler(openai.APIConnectionError)
async def openai_connection_handler(request: Request, exc: openai.APIConnectionError):
    logger.error("OpenAI connection error: %s", exc)
    return JSONResponse(
        status_code=503,
        content={
            "detail": "Could not reach OpenAI — check internet connection",
            "request_id": _request_id_for(request),
        },
    )


@app.exception_handler(openai.APITimeoutError)
async def openai_timeout_handler(request: Request, exc: openai.APITimeoutError):
    logger.error("OpenAI timeout: %s", exc)
    return JSONResponse(
        status_code=504,
        content={"detail": "OpenAI request timed out", "request_id": _request_id_for(request)},
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    logger.warning("ValueError: %s", exc)
    return JSONResponse(
        status_code=422,
        content={"detail": str(exc), "request_id": _request_id_for(request)},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "request_id": _request_id_for(request),
        },
    )


@app.get("/version", response_model=VersionResponse, tags=["system"])
async def version() -> VersionResponse:
    """Reports the deployed commit SHA so CI can verify a rollout actually landed.

    Hosted deploys can briefly serve the previous container while the new one
    is still building. Polling /version until the SHA flips proves the new build
    is live.
    """
    sha = app.state.git_sha
    return VersionResponse(
        git_sha=sha,
        git_sha_short=sha[:7] if sha != "unknown" else "unknown",
        environment=settings.environment,
    )


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Liveness probe — cheap, used by platform healthchecks.

    Intentionally does NOT touch external dependencies: a failing dep
    should not trigger a container restart (rebooting won't fix Supabase).
    For dependency status, use /health/deep.
    """
    return HealthResponse(environment=settings.environment)


# Per-dep timeout for the deep probe. Long enough to forgive a slow API call,
# short enough that the cron job and any caller don't hang.
_DEEP_DEP_TIMEOUT_SECONDS = 5.0


async def _check_supabase() -> DepStatus:
    """Round-trip a trivial query so we know the DB is actually reachable."""
    started = time.perf_counter()
    try:
        supabase = await get_supabase()
        await asyncio.wait_for(
            supabase.table("profiles").select("id").limit(1).execute(),
            timeout=_DEEP_DEP_TIMEOUT_SECONDS,
        )
        return DepStatus(
            status="ok",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    except TimeoutError:
        return DepStatus(status="error", error=f"timeout after {_DEEP_DEP_TIMEOUT_SECONDS}s")
    except Exception as exc:
        # Truncate to avoid leaking long stack traces / credentials in the response body
        return DepStatus(status="error", error=f"{type(exc).__name__}: {exc}"[:200])


async def _check_openai() -> DepStatus:
    """models.list() is free (no tokens consumed) and verifies auth + reachability."""
    started = time.perf_counter()
    try:
        client = get_openai_client()
        await asyncio.wait_for(client.models.list(), timeout=_DEEP_DEP_TIMEOUT_SECONDS)
        return DepStatus(
            status="ok",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    except TimeoutError:
        return DepStatus(status="error", error=f"timeout after {_DEEP_DEP_TIMEOUT_SECONDS}s")
    except Exception as exc:
        return DepStatus(status="error", error=f"{type(exc).__name__}: {exc}"[:200])


@app.get(
    "/health/deep",
    response_model=DeepHealthResponse,
    tags=["system"],
    responses={503: {"model": DeepHealthResponse, "description": "One or more deps are degraded"}},
)
async def health_deep() -> JSONResponse:
    """Readiness probe — pings every external dependency in parallel.

    Returns 200 when every dep is healthy, 503 if any dep is down. Intended
    for external uptime monitors (GitHub cron, UptimeRobot, etc.) — NOT for
    platform healthchecks (would cause crash-loops on Supabase/OpenAI outages).
    """
    supabase_status, openai_status = await asyncio.gather(_check_supabase(), _check_openai())
    deps = {"supabase": supabase_status, "openai": openai_status}
    overall_ok = all(d.status == "ok" for d in deps.values())

    if not overall_ok:
        for name, dep in deps.items():
            if dep.status != "ok":
                logger.warning("Deep health: %s is degraded — %s", name, dep.error)

    body = DeepHealthResponse(
        status="ok" if overall_ok else "degraded",
        environment=settings.environment,
        deps=deps,
    )
    return JSONResponse(
        status_code=200 if overall_ok else 503,
        content=body.model_dump(),
    )
