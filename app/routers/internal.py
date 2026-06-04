from fastapi import APIRouter, Header, HTTPException, status

from app.config import get_settings
from app.models.schemas import AccountDeletionPurgeResponse, NotificationRunResponse
from app.services.account_deletion import purge_due_deleted_accounts
from app.services.push_notifications import send_due_deadline_notifications

router = APIRouter(prefix="/internal", tags=["internal"])


def _verify_cron_secret(value: str | None) -> None:
    settings = get_settings()
    expected = settings.internal_cron_secret
    if expected:
        if value != expected:
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


@router.post("/accounts/purge-deleted", response_model=AccountDeletionPurgeResponse)
async def run_account_deletion_purge_job(
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> AccountDeletionPurgeResponse:
    _verify_cron_secret(x_cron_secret)
    stats = await purge_due_deleted_accounts()
    return AccountDeletionPurgeResponse(**stats)
