# AI Taro Runbook

## 1. Pre-deploy checks

- Backend syntax:
  - `python3 -m py_compile /Users/Kristina/Desktop/ГОТОВО/backend/main.py`
  - `python3 -m py_compile /Users/Kristina/Desktop/ГОТОВО/backend/telegram_bot.py`
- Frontend build:
  - `cd /Users/Kristina/Desktop/ГОТОВО/telegram-miniapp && npm run build`

## 2. Backend deploy (VDS)

1. Pull latest code on API server.
2. Restart backend service.
3. Restart telegram bot worker.
4. Verify health endpoint and `/me` auth flow.

## 3. Frontend deploy (cPanel)

1. Build miniapp locally (`npm run build`).
2. Upload fresh `dist` artifacts to cPanel miniapp root.
3. Hard refresh miniapp in Telegram (close/open).

## 4. Smoke checklist

- Auth in miniapp works.
- Card of day works with daily guard.
- 3-card readings produce interpretation.
- Payment confirmation updates profile access.
- Support message from user reaches support bot.
- Operator reply returns to user thread.
- `/open` `/pending` `/closed` in support bot work.

## 5. Incident notes

- If support replies fail: verify `SUPPORT_INBOX_BOT_TOKEN` and `SUPPORT_INBOX_CHAT_ID`.
- If memory hints disappear: check `FEATURE_MEMORY_V1` and `/memory/summary` response.
- If nudges do not send: check `FEATURE_NUDGES_V1`, user opt-in flags, and server timezone.
