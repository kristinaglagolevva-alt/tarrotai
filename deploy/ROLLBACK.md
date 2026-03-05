# AI Taro Rollback Guide

## Fast rollback (no code revert)

1. Disable flags on backend env:
   - `FEATURE_MEMORY_V1=0`
   - `FEATURE_TICKETS_V2=0`
   - `FEATURE_NUDGES_V1=0`
2. Restart backend service.
3. Confirm baseline flows (`/me`, readings, payments) are healthy.

## Frontend rollback

1. Re-upload previous known-good `dist` bundle on cPanel.
2. Keep backend running with flags as needed.

## Backend rollback

1. Checkout previous stable commit on VDS.
2. Restart API and bot services.
3. Keep DB schema (forward-compatible columns/tables are additive).

## Data safety notes

- New tables are additive and do not block old code.
- User preference fields have safe defaults (`false`/`null`).
- Support ticket history remains in DB after rollback.
