#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any, Dict

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


COUNT_SQL = """
WITH active_users AS (
  SELECT
    u.id AS user_id,
    u.telegram_id AS telegram_id,
    GREATEST(
      COALESCE(bo.last_opened_at, u.created_at),
      COALESCE((SELECT MAX(r.created_at) FROM readings r WHERE r.user_id = u.id), u.created_at),
      COALESCE((SELECT MAX(c.created_at) FROM card_of_day c WHERE c.user_id = u.id), u.created_at),
      COALESCE((SELECT MAX(p.created_at) FROM payment_transactions p WHERE p.user_id = u.id), u.created_at)
    ) AS sent_at
  FROM users u
  LEFT JOIN bot_open_users bo ON bo.telegram_id = u.telegram_id
  WHERE u.telegram_id > 0
    AND (
         bo.telegram_id IS NOT NULL
     OR EXISTS (SELECT 1 FROM readings r WHERE r.user_id = u.id)
     OR EXISTS (SELECT 1 FROM card_of_day c WHERE c.user_id = u.id)
     OR EXISTS (SELECT 1 FROM payment_transactions p WHERE p.user_id = u.id)
    )
)
SELECT COUNT(*)::bigint AS missing_count
FROM active_users a
LEFT JOIN bot_pm_bootstrap b ON b.user_id = a.user_id
WHERE b.user_id IS NULL;
"""


SYNC_SQL = """
WITH active_users AS (
  SELECT
    u.id AS user_id,
    u.telegram_id AS telegram_id,
    GREATEST(
      COALESCE(bo.last_opened_at, u.created_at),
      COALESCE((SELECT MAX(r.created_at) FROM readings r WHERE r.user_id = u.id), u.created_at),
      COALESCE((SELECT MAX(c.created_at) FROM card_of_day c WHERE c.user_id = u.id), u.created_at),
      COALESCE((SELECT MAX(p.created_at) FROM payment_transactions p WHERE p.user_id = u.id), u.created_at)
    ) AS sent_at
  FROM users u
  LEFT JOIN bot_open_users bo ON bo.telegram_id = u.telegram_id
  WHERE u.telegram_id > 0
    AND (
         bo.telegram_id IS NOT NULL
     OR EXISTS (SELECT 1 FROM readings r WHERE r.user_id = u.id)
     OR EXISTS (SELECT 1 FROM card_of_day c WHERE c.user_id = u.id)
     OR EXISTS (SELECT 1 FROM payment_transactions p WHERE p.user_id = u.id)
    )
),
latest_success_audit AS (
  SELECT DISTINCT ON (a.user_id)
    a.user_id,
    a.message_id
  FROM bot_pm_bootstrap_audit a
  WHERE a.status IN ('success', 'already_sent')
  ORDER BY a.user_id, a.created_at DESC
),
inserted AS (
  INSERT INTO bot_pm_bootstrap (user_id, telegram_id, message_id, sent_at)
  SELECT
    au.user_id,
    au.telegram_id,
    lsa.message_id,
    au.sent_at
  FROM active_users au
  LEFT JOIN latest_success_audit lsa ON lsa.user_id = au.user_id
  LEFT JOIN bot_pm_bootstrap b ON b.user_id = au.user_id
  WHERE b.user_id IS NULL
  ON CONFLICT (user_id) DO NOTHING
  RETURNING user_id
)
SELECT COUNT(*)::bigint AS inserted_count FROM inserted;
"""


SAMPLE_SQL = """
WITH active_users AS (
  SELECT
    u.id AS user_id,
    u.telegram_id AS telegram_id,
    u.username AS username,
    GREATEST(
      COALESCE(bo.last_opened_at, u.created_at),
      COALESCE((SELECT MAX(r.created_at) FROM readings r WHERE r.user_id = u.id), u.created_at),
      COALESCE((SELECT MAX(c.created_at) FROM card_of_day c WHERE c.user_id = u.id), u.created_at),
      COALESCE((SELECT MAX(p.created_at) FROM payment_transactions p WHERE p.user_id = u.id), u.created_at)
    ) AS sent_at
  FROM users u
  LEFT JOIN bot_open_users bo ON bo.telegram_id = u.telegram_id
  WHERE u.telegram_id > 0
    AND (
         bo.telegram_id IS NOT NULL
     OR EXISTS (SELECT 1 FROM readings r WHERE r.user_id = u.id)
     OR EXISTS (SELECT 1 FROM card_of_day c WHERE c.user_id = u.id)
     OR EXISTS (SELECT 1 FROM payment_transactions p WHERE p.user_id = u.id)
    )
)
SELECT
  au.user_id,
  au.telegram_id,
  au.username,
  au.sent_at
FROM active_users au
LEFT JOIN bot_pm_bootstrap b ON b.user_id = au.user_id
WHERE b.user_id IS NULL
ORDER BY au.sent_at DESC
LIMIT :sample_limit;
"""


def _db_url() -> str:
    load_dotenv()
    raw = str(os.getenv("DATABASE_URL") or "").strip()
    if not raw:
        raise RuntimeError("DATABASE_URL is empty")
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw


async def _run(*, dry_run: bool, sample_limit: int) -> Dict[str, Any]:
    engine = create_async_engine(_db_url(), future=True)
    try:
        async with engine.begin() as conn:
            before_missing = int((await conn.execute(text(COUNT_SQL))).scalar_one() or 0)
            sample_rows = (
                await conn.execute(text(SAMPLE_SQL), {"sample_limit": int(max(1, sample_limit))})
            ).mappings().all()
            inserted = 0
            if not dry_run and before_missing > 0:
                inserted = int((await conn.execute(text(SYNC_SQL))).scalar_one() or 0)
            after_missing = int((await conn.execute(text(COUNT_SQL))).scalar_one() or 0)

        return {
            "dry_run": bool(dry_run),
            "missing_before": before_missing,
            "inserted": inserted,
            "missing_after": after_missing,
            "sample": [
                {
                    "user_id": int(row["user_id"]),
                    "telegram_id": int(row["telegram_id"]),
                    "username": row.get("username"),
                    "sent_at": str(row.get("sent_at") or ""),
                }
                for row in sample_rows
            ],
        }
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill/sync bot_pm_bootstrap for already active users.")
    parser.add_argument("--dry-run", action="store_true", help="Only show counters, do not write rows.")
    parser.add_argument("--sample-limit", type=int, default=20, help="How many missing users to print in sample.")
    args = parser.parse_args()

    result = asyncio.run(_run(dry_run=bool(args.dry_run), sample_limit=int(args.sample_limit)))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
