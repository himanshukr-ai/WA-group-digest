# WhatsApp Group Digest Bot

Reads the WhatsApp groups you're in (via [Whapi.cloud](https://whapi.cloud)) and sends you AI
digests — topics, hot takes, decisions, open questions, action items, links, and mentions of you —
on a daily schedule and on demand. Personal tool first; see [CLAUDE.md](CLAUDE.md) for the
architecture if you're extending it.

## 1. Set up Whapi.cloud

1. Create an account at [whapi.cloud](https://whapi.cloud) and create a channel.
2. Link your WhatsApp number by scanning the channel's QR code with WhatsApp on your phone
   (Settings -> Linked Devices -> Link a Device) — same as WhatsApp Web. This gives Whapi
   read/send access to your account as an extra linked device.
3. Copy the channel's API token from the Whapi dashboard.

## 2. Configure the bot

```bash
cd whatsapp-digest-bot
cp .env.example .env
```

Fill in `.env`:
- `WHAPI_TOKEN` — from step 1.
- `ANTHROPIC_API_KEY` — an Anthropic API key.
- `SELF_NUMBER` — your own WhatsApp number in international format, no `+`, no spaces (e.g.
  `971501234567`). Digests are sent here and `/digest`, `/groups` commands are only accepted from
  here.
- `USER_DISPLAY_NAME` — how you're referred to in your groups, so the summarizer can flag mentions
  of you.
- Leave the rest at their defaults to start.

## 3. Local setup and tests

```bash
python -m venv .venv
./.venv/Scripts/pip install -e ".[dev]"   # Windows; drop the .Scripts for macOS/Linux
./.venv/Scripts/python -m pytest tests/
```

All tests run offline against fixtures and mocked Whapi/Anthropic clients — no real token needed
to develop or run the test suite.

## 4. Find your group IDs and build the watchlist

```bash
./.venv/Scripts/python -m app groups
```

This lists every WhatsApp group visible to your linked number, with its Whapi chat ID. Copy the
ones you want summarized into `groups.yaml`:

```yaml
groups:
  - id: "120363000000000000@g.us"
    name: "Site A Coordination"
    enabled: true
    notes: "project X site group"
```

Only `enabled: true` groups are ever ingested, backfilled, or summarized.

## 5. Backfill history and try a digest locally

```bash
./.venv/Scripts/python -m app backfill --days 7
./.venv/Scripts/python -m app digest --window 1d
```

`digest` prints straight to stdout and logs token usage/estimated cost to stderr. No WhatsApp
message is sent by these commands — this is the fastest way to iterate on the prompts in
`prompts/*.md` without spamming your own chat.

## 6. Live webhook testing (local, via ngrok)

The bot needs Whapi to POST incoming messages to it in real time. To test that locally:

```bash
./.venv/Scripts/python -m app serve --reload
ngrok http 8000
```

In the Whapi dashboard, set the channel's webhook URL to `https://<your-ngrok-subdomain>.ngrok-free.app/webhook/whapi`,
mode "Body", and subscribe to the `messages` event. Send a message in a watchlisted group (or
`/groups` / `/digest 1d` to your own chat) and confirm it's picked up — `python -m app serve`
logs each webhook POST.

## 7. Deploy to a VPS

```bash
git clone <this repo> && cd whatsapp-digest-bot
cp .env.example .env   # fill in real values; DATABASE_URL is overridden by docker-compose.yml
docker compose up -d --build
```

This runs the app behind Postgres (`docker-compose.yml`), keeping message history and daily
summaries in `db_data`. Point the Whapi webhook URL at `https://<your-domain-or-ip>:8000/webhook/whapi`
(put a reverse proxy with TLS in front for a real domain — Whapi requires HTTPS for the
webhook URL in production). `groups.yaml` is mounted read-only, so you can edit it on the host and
restart the `app` container to pick up changes:

```bash
docker compose restart app
```

**Note:** the Postgres path (via `psycopg`) has been checked for import/packaging correctness but
not exercised against a live Postgres instance in this environment — worth a smoke test
(`docker compose up`, then `python -m app groups` from inside the container) before relying on it.

## Daily operation

- **Scheduled digest:** every day at `DAILY_DIGEST_HOUR` (default 8) in `TIMEZONE` (default
  `Asia/Dubai`), the last 24h are summarized and sent to your own chat.
- **On demand:** from your own WhatsApp chat, send `/digest 1d`, `/digest 3d`, `/digest 7d <group
  name>`, or `/groups`.
- **Retention:** raw messages older than `RETENTION_DAYS` (default 30) are deleted daily at 03:00
  local time; daily summaries are kept indefinitely so history stays queryable even after raw
  messages are purged. Run it manually with `python -m app retention`.
- **Cost:** every digest run logs input/output token counts and an estimated USD cost
  (`ANTHROPIC_PRICE_INPUT_PER_MTOK` / `ANTHROPIC_PRICE_OUTPUT_PER_MTOK` in `.env` — update these to
  match current Anthropic pricing, they're not fetched live).

## Read-only by design

The bot never posts into a WhatsApp group. It only ever sends messages to your own chat
(`SELF_NUMBER`) — for the scheduled digest and for replies to `/digest`/`/groups` commands.
