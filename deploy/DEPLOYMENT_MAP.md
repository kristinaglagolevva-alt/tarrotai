# AI Taro Deployment Map

## Current topology

- `VDS (api.tarrotai.ru)`
  - FastAPI backend (`/Users/Kristina/Desktop/ГОТОВО/backend/main.py`)
  - Telegram bot runtime (`/Users/Kristina/Desktop/ГОТОВО/backend/telegram_bot.py`)
  - Postgres (source of truth)
  - Feature workers (support retries, retention cycle)

- `cPanel (tarrotai.ru)`
  - Telegram miniapp static bundle (`/Users/Kristina/Desktop/ГОТОВО/telegram-miniapp/dist`)
  - OpenAI relay (`/Users/Kristina/Desktop/ГОТОВО/deploy/cpanel-relay/openai.php`)

## Request flow

1. User opens miniapp from `@Ttaarrroobot`.
2. Frontend calls API at `https://api.tarrotai.ru`.
3. API validates Telegram JWT and serves readings/billing/history/support.
4. Bot handles payment UI/support commands and operator workflow via `@Sup_taro_bot`.

## Feature flags (kill switches)

- `FEATURE_MEMORY_V1`
- `FEATURE_TICKETS_V2`
- `FEATURE_NUDGES_V1`

All flags are enabled by default and can be disabled at backend env-level without frontend rollback.
