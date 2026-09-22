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
- `app/summarize/` — two-pass summarizer (per-group/per-day JSON, then a merged digest for the
  requested window). Prompts live in `prompts/*.md`, not inline in code.
- `app/delivery/` — the 08:00 Asia/Dubai scheduled digest and the self-chat `/digest`, `/groups`
  command handlers.
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
Phase 1 (skeleton, config, DB models, Whapi client, webhook ingest) is done and tested against
fixtures derived from the current Whapi docs. Phases 2-5 (backfill, summarizer, delivery,
packaging) are tracked in the original project plan.
