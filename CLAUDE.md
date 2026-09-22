# WhatsApp Group Digest Bot

Personal bot that ingests WhatsApp groups via Whapi.cloud and sends AI digests
(topics, hot takes, decisions, open questions, action items, links, mentions)
to Himanshu's own WhatsApp chat, on a schedule and on demand.

Built clean enough to become a multi-tenant product later, but v1 is single-user.

## Stack
- Python 3.12+, FastAPI (webhook receiver + admin endpoints)
- SQLAlchemy 2.0, SQLite locally / Postgres in prod (same models, `DATABASE_URL` env var)
- Anthropic SDK for summarization (model set via `ANTHROPIC_MODEL`, default current Sonnet)
- APScheduler for the daily digest cron and retention job
- Whapi.cloud (`https://gate.whapi.cloud`) as the only WhatsApp integration — read-only on
  groups, only ever sends messages to the configured self chat

## Commands
```bash
# from whatsapp-digest-bot/
python -m venv .venv && ./.venv/Scripts/pip install -e ".[dev]"   # setup (Windows)
./.venv/Scripts/python -m pytest tests/                            # run tests
./.venv/Scripts/python -m app init-db                               # create tables
./.venv/Scripts/python -m app serve                                  # run the webhook server
./.venv/Scripts/python -m app groups                                  # list Whapi groups (find IDs for groups.yaml)
./.venv/Scripts/python -m app backfill --days 7 [--group <id-or-name>]  # pull message history
./.venv/Scripts/python -m app digest --window 3d [--group <id-or-name>]  # print a merged digest
```

## Architecture
- `app/config.py` — env-based `Settings` (pydantic-settings) + `groups.yaml` watchlist loader.
- `app/db/` — SQLAlchemy models (`Message`, `DailySummary`, `Group`) and session management.
  Tables are created with `Base.metadata.create_all` (`init_db()`), no migration framework yet —
  fine for v1's single-user scale; revisit with Alembic before multi-tenant.
- `app/whapi/` — `client.py` (HTTP client with retry/backoff on 429/5xx) and `schemas.py`
  (pydantic models mirroring Whapi's message/group/webhook payloads).
- `app/ingest/` — `persist.py` (shared message persistence + `@lid` sender-name fallback used by
  both the webhook and backfill paths) and `webhook.py` (FastAPI router for incoming events).
- `app/summarize/` — two-pass summarizer:
  - `pass1.py` formats one group/day of messages, calls Claude with a forced tool call
    (`record_summary`) against a strict JSON schema, and chunks+merges when a day is too long
    for one call (`CHUNK_CHAR_BUDGET`).
  - `cache.py` caches pass-1 output in `daily_summaries` keyed by `(group_id, summary_date)` —
    re-running a digest for a date that's already been summarized costs nothing.
  - `pass2.py` (`build_digest`) pulls the cached per-day JSON for a window, asks Claude to merge
    it into one readable digest (mentions-of-user and open questions to them first, then
    per-group sections), and returns `(digest_text, usage)`.
  - `usage.py` estimates cost from `Settings.anthropic_price_*_per_mtok` (approximate — update to
    match current Anthropic pricing) and logs it per run.
  - Prompts live in `prompts/*.md` (`$name`-style placeholders via `string.Template`), not inline
    in code, so they're tunable without touching Python.
- `app/delivery/` —
  - `scheduler.py` (`run_daily_digest`, `create_scheduler`): an APScheduler cron job at
    `DAILY_DIGEST_HOUR` in `Settings.timezone` that builds the last-24h digest and sends it to
    `self_chat_id`. Started/stopped in `app/api.py`'s FastAPI lifespan, gated by
    `ENABLE_SCHEDULER` (tests always disable it).
  - `commands.py` (`is_self_command`, `parse_command`, `handle_command`): recognizes `/digest
    <1d|3d|7d> [group]` and `/groups` sent from the user's own number to their own chat
    (`chat_id == self_chat_id and from_me`) and returns the reply text. Wired into
    `app/ingest/webhook.py`, which sends the reply back through Whapi — the only other place a
    `send_text` call is ever made.
  - `POST /admin/run-digest` (in `app/api.py`) manually triggers `run_daily_digest`; guarded to
    localhost/TestClient via `is_local_client`.
- `groups.yaml` — the group watchlist (id, name, enabled, notes). Source of truth; only enabled
  groups are ingested or backfilled.

## Conventions
- Never send messages into a WhatsApp group — the bot is read-only there. Sends only ever target
  the configured self chat (`Settings.self_chat_id`).
- `@lid` sender IDs (WhatsApp's anonymized participant format for groups with hidden numbers)
  aren't phone numbers — always fall back to `from_name` for display. See
  `app/ingest/persist.py::sender_display_name`.
- Webhook ingest and backfill share one persistence function (`persist_message`) so dedupe-by-
  message-id behaves identically regardless of ingestion path.
- Timestamps are stored in UTC (`timestamp_utc`); render in `Settings.timezone` (Asia/Dubai) only
  at the display/prompt-formatting layer.
- Keep prompts in `prompts/*.md`, not inline strings, so they're tunable without touching code.
- Log token usage and estimated cost per digest run.

## Status
Phase 1 (skeleton, config, DB models, Whapi client, webhook ingest), Phase 2 (backfill CLI,
`groups` listing CLI, groups.yaml -> DB sync), Phase 3 (two-pass summarizer, `digest` CLI), and
Phase 4 (scheduled + on-demand delivery) are done and tested against a synthetic UAE
construction-project group fixture (`tests/fixtures/construction_group_day.json`), a stub
Anthropic client (`tests/fake_anthropic.py`), and mocked Whapi HTTP calls (`respx`) — no live
Anthropic or Whapi call has been made yet. Phase 5 (Docker Compose, README, retention job) is
tracked in the original project plan.
