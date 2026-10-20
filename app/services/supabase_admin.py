"""Supabase service-role client for narrowly scoped admin operations."""

import logging

from app.config import get_settings
from supabase import AsyncClient, create_async_client

logger = logging.getLogger(__name__)

supabase_admin: AsyncClient | None = None


async def get_supabase_admin() -> AsyncClient:
    """Return a singleton Supabase client that can call Auth Admin APIs."""
    global supabase_admin
    if supabase_admin is None:
        settings = get_settings()
        supabase_admin = await create_async_client(
            settings.supabase_url,
            settings.supabase_service_key,
        )
        logger.info("Supabase admin client initialised")
    return supabase_admin
