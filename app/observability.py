"""Sentry error tracking — env-gated, off unless SENTRY_DSN is set.

Without this, an analyzer/OCR failure in prod is only visible by trawling
Render logs (which roll off). With a DSN configured, unhandled exceptions and
the explicitly-captured SSE pipeline failures land in Sentry, tagged with the
same request_id the middleware stamps on every request/response.
"""

from __future__ import annotations

import logging

from app.config import get_settings

logger = logging.getLogger(__name__)

_enabled = False


def init_sentry(release: str | None = None) -> None:
    """Initialise Sentry when SENTRY_DSN is configured. No-op otherwise.

    Must run before the FastAPI app is created so the Starlette/FastAPI
    integrations can patch the framework.
    """
    global _enabled
    settings = get_settings()
    dsn = settings.sentry_dsn
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed; skipping Sentry init")
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.environment,
        release=release if release and release != "unknown" else None,
        # Crash/error reporting only — no performance tracing or PII.
        traces_sample_rate=0.0,
        send_default_pii=False,
        integrations=[StarletteIntegration(), FastApiIntegration()],
    )
    _enabled = True
    logger.info("Sentry initialised env=%s", settings.environment)


def tag_request(request_id: str | None) -> None:
    """Tag the current request's Sentry scope with our request_id.

    Under the ASGI integration each request has its own contextvar-scoped Sentry
    scope, so this is request-local and safe under concurrency.
    """
    if not _enabled or not request_id:
        return
    import sentry_sdk

    sentry_sdk.set_tag("request_id", request_id)


def capture_exception(exc: BaseException) -> None:
    """Send an explicitly-caught exception to Sentry. No-op when disabled.

    Used in the SSE pipelines, whose errors are swallowed into an error frame
    and would otherwise never reach Sentry's unhandled-exception hook.
    """
    if not _enabled:
        return
    import sentry_sdk

    sentry_sdk.capture_exception(exc)
