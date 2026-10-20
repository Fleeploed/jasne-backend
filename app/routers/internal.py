import hmac

from fastapi import APIRouter, Header, HTTPException, status

from app.config import get_settings
from app.models.schemas import (
    AccountDeletionPurgeResponse,
    NotificationReceiptRunResponse,
    NotificationRunResponse,
    TrackingCronResponse,
)
from app.services.account_deletion import purge_due_deleted_accounts
from app.services.push_notifications import check_expo_push_receipts, send_due_deadline_notifications
from app.workers.tracking_cron import run_tracking_cron

router = APIRouter(prefix="/internal", tags=["internal"])


def _verify_cron_secret(value: str | None) -> None:
    settings = get_settings()
    expected = settings.internal_cron_secret
    if expected:
        # Constant-time comparison so the secret isn't recoverable via timing.
        if not hmac.compare_digest(value or "", expected):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid cron secret")
        return

    if settings.environment.lower() not in {"development", "test"}:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="INTERNAL_CRON_SECRET is not configured",
        )


@router.post("/notifications/due", response_model=NotificationRunResponse)
async def run_due_notification_job(
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> NotificationRunResponse:
    _verify_cron_secret(x_cron_secret)
    stats = await send_due_deadline_notifications()
    return NotificationRunResponse(**stats)


@router.post("/notifications/receipts", response_model=NotificationReceiptRunResponse)
async def run_receipt_check_job(
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> NotificationReceiptRunResponse:
    """Fetch Expo push receipts for sent notifications and disable dead tokens.

    Runs 20+ minutes after the send job — DeviceNotRegistered often appears
    only in the receipt, not the ticket.
    """
    _verify_cron_secret(x_cron_secret)
    stats = await check_expo_push_receipts()
    return NotificationReceiptRunResponse(**stats)


@router.post("/accounts/purge-deleted", response_model=AccountDeletionPurgeResponse)
async def run_account_deletion_purge_job(
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> AccountDeletionPurgeResponse:
    _verify_cron_secret(x_cron_secret)
    stats = await purge_due_deleted_accounts()
    return AccountDeletionPurgeResponse(**stats)


@router.post("/trackings/run-cron", response_model=TrackingCronResponse)
async def run_tracking_cron_job(
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> TrackingCronResponse:
    """Re-check every background tracking whose `next_check_at` is due.

    The periodic path is the Railway `jasne-backend` service (see
    `/railway.toml`), which runs `python -m app.workers.tracking_cron`
    directly on an hourly native cron — no HTTP hop, no secret needed. This
    HTTP entrypoint is kept only as a manual/ops trigger (e.g. `curl` with
    `X-Cron-Secret` to force an immediate re-check while debugging); it is no
    longer driven by a scheduled GitHub Action.
    """
    _verify_cron_secret(x_cron_secret)
    stats = await run_tracking_cron()
    return TrackingCronResponse(**stats)
