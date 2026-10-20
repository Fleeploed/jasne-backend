"""One-shot import of help_content/guides/*.yaml into the help_guides table.

Run once after applying migration 0011_help_admin.sql to preserve the 8
existing guides authored as YAML. Idempotent on (slug, language) — re-runs
upsert into the same rows.

Usage:
    python -m scripts.seed_help_guides_from_yaml

Env: same as the app — needs SUPABASE_URL and SUPABASE_SERVICE_KEY in
.env, because RLS would block anon-key writes.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import yaml

from app.config import get_settings
from supabase import create_async_client

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

GUIDES_ROOT = Path(__file__).resolve().parents[1] / "help_content" / "guides"


async def main() -> None:
    settings = get_settings()
    sb = await create_async_client(settings.supabase_url, settings.supabase_service_key)

    files = sorted(GUIDES_ROOT.rglob("*.yaml"))
    if not files:
        logger.warning("No guide YAML files found under %s", GUIDES_ROOT)
        return

    inserted = 0
    for path in files:
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        row = {
            "slug": raw["slug"],
            "category_slug": raw["category"],
            "language": raw["language"],
            "status": "published" if raw.get("published", True) else "draft",
            "title": raw["title"],
            "subtitle": raw.get("subtitle"),
            "short_summary": raw.get("short_summary"),
            "keywords": raw.get("keywords") or [],
            "official_source_url": raw.get("official_source_url"),
            "sections": raw.get("sections") or [],
        }
        # Upsert on the natural key so re-runs overwrite cleanly.
        res = await sb.table("help_guides").upsert(row, on_conflict="slug,language").execute()
        if res.data:
            inserted += 1
            logger.info("Imported %s.%s", row["slug"], row["language"])
        else:
            logger.warning("No data returned for %s.%s", row["slug"], row["language"])

    logger.info("Done — %d guides upserted.", inserted)


if __name__ == "__main__":
    asyncio.run(main())
