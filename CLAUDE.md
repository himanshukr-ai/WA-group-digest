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
./.venv/Scripts/python -m app retention                                    # delete messages past RETENTION_DAYS
./.venv/Scripts/python -m app register-webhook                               # register this app's webhook with Whapi

docker compose up -d --build   # run app + Postgres on a VPS (see README.md)
# or deploy to Railway -- see README.md "Deploy" section
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
- `app/db/retention.py` (`purge_old_messages`) deletes raw `messages` rows older than
  `RETENTION_DAYS`; `daily_summaries` are never deleted. Runs daily at 03:00 local time (a job in
  the same scheduler as the digest, added in `app/delivery/scheduler.py::create_scheduler`) and
  via `python -m app retention`.
- `groups.yaml` — the group watchlist (id, name, enabled, notes). Source of truth; only enabled
  groups are ingested or backfilled.
- `app/whapi/webhook_setup.py` (`register_webhook`) — appends/updates *only this app's* entry
  (matched by URL) in the Whapi channel's `webhooks` array via `PATCH /settings`. **Never replaces
  the whole array** — this Whapi channel is shared with at least one other app (a "Zendox"
  service, also on Railway), so blindly overwriting `webhooks` would silently break it. Always go
  through this function (or `python -m app register-webhook`), never set the webhook URL by hand
  in the Whapi dashboard.
- `Dockerfile` / `docker-compose.yml` / `railway.json` — deploy configs; see `README.md`. Deployed
  target is Railway (Postgres plugin, custom domain); `app/db/session.py::_normalize_database_url`
  rewrites the plain `postgres://`/`postgresql://` URL Railway's Postgres plugin provides to use
  the installed `psycopg` driver. `cmd_serve` reads `$PORT` (Railway assigns it dynamically),
  falling back to 8000 locally.

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
All 5 phases from the original plan are built and tested, and the app has been verified live
against the real Whapi API, a real linked WhatsApp number, and real Claude calls: `python -m app
groups` lists all real groups, `backfill`/`digest` work against real message history for the
watchlisted "SM - GenAI" group, and a real digest was sent to and received on WhatsApp via
`run_daily_digest`.

Two live-testing bugs were found and fixed (see git log): `GroupParticipant.id`/`rank` needed to
be optional (Whapi omits `id` for privacy-hidden participants), and pass1/pass2 needed
`output_config.effort` set explicitly + a larger `max_tokens` -- Claude Sonnet 5 runs adaptive
thinking on by default, billed out of the same `max_tokens` budget, and was silently consuming the
entire response before any digest text got written.

**Still not yet verified:**
- Real webhook delivery (`register-webhook` + a live deploy) — see README.md step 7 (Railway).
- The Postgres path (`psycopg`) is packaging-checked but hasn't run against a live Postgres.
- Retention has only been unit-tested, not run against real aged data.
