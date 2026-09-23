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

## 7. Deploy

### Option A: Railway (recommended — no server/TLS setup needed)

1. In the [Railway dashboard](https://railway.app), create a project and connect it to this
   GitHub repo. Railway detects the `Dockerfile` and `railway.json` automatically (builder,
   healthcheck path).
2. Add a **Postgres** plugin to the project. Railway injects `DATABASE_URL` (as a plain
   `postgres://...` URL) into the app service automatically — the app normalizes that to the
   `psycopg` driver itself, no manual edit needed.
3. In the app service's **Variables**, set everything from `.env.example` *except*
   `DATABASE_URL` (Railway's Postgres plugin already provides it) and `WEBHOOK_BASE_URL` (set it
   to your public URL — see step 5). Railway also injects `$PORT`; `python -m app serve` already
   reads it.
4. Under **Settings → Networking**, add your custom domain and point its CNAME at Railway per
   their instructions. Railway provisions TLS automatically. (A generated `*.up.railway.app`
   domain works too if you don't need a custom one yet.)
5. Set `WEBHOOK_BASE_URL` to that domain (e.g. `https://digest.yourdomain.com`, no trailing
   slash) and redeploy.
6. **Important if this Whapi channel is shared with another app:** don't set the webhook URL by
   hand in the Whapi dashboard — that overwrites the *entire* webhooks list, which would delete
   any other app's webhook on the same channel. Instead run:
   ```bash
   ./.venv/Scripts/python -m app register-webhook
   ```
   This only ever appends/updates *this app's* entry (matched by URL) in Whapi's `webhooks` array
   and leaves every other entry untouched. Safe to re-run any time the URL changes.

### Option B: Your own VPS (Docker Compose)

```bash
git clone <this repo> && cd whatsapp-digest-bot
cp .env.example .env   # fill in real values; DATABASE_URL is overridden by docker-compose.yml
docker compose up -d --build
```

This runs the app behind Postgres (`docker-compose.yml`), keeping message history and daily
summaries in `db_data`. Put a reverse proxy with TLS (Whapi requires HTTPS in production) in front
pointing at port 8000, set `WEBHOOK_BASE_URL` in `.env` to that public URL, then run
`python -m app register-webhook` the same way as above. `groups.yaml` is mounted read-only, so you
can edit it on the host and restart the `app` container to pick up changes:

```bash
docker compose restart app
```

**Note:** the Postgres path (via `psycopg`) has been checked for import/packaging correctness but
not exercised against a live Postgres instance in this environment — worth a smoke test
(`docker compose up` or a Railway deploy, then `python -m app groups`) before relying on it.

## Daily operation

- **Scheduled digest:** every day at `DAILY_DIGEST_HOUR` (default 8) in `TIMEZONE` (default
  `Asia/Dubai`), *yesterday* (the last complete calendar day) is summarized and sent to your own
  chat. Each message lands in exactly one scheduled digest, up to about a day after it was sent.
- **On demand:** from your own WhatsApp chat, send `/digest 1d`, `/digest 3d`, `/digest 7d <group
  name>`, or `/groups`. `Nd` means the last N full days **plus today so far**, so `/digest 1d`
  at 8am shows yesterday and the morning's messages.
- **Caching:** only completed days are cached (so a digest never freezes a half-finished day);
  today is always summarized fresh.
- **Retention:** raw messages older than `RETENTION_DAYS` (default 30) are deleted daily at 03:00
  local time; daily summaries are kept indefinitely so history stays queryable even after raw
  messages are purged. Run it manually with `python -m app retention`.
- **Cost:** every digest run logs input/output token counts and an estimated USD cost
  (`ANTHROPIC_PRICE_INPUT_PER_MTOK` / `ANTHROPIC_PRICE_OUTPUT_PER_MTOK` in `.env` — update these to
  match current Anthropic pricing, they're not fetched live).

## Read-only by design

The bot never posts into a WhatsApp group. It only ever sends messages to your own chat
(`SELF_NUMBER`) — for the scheduled digest and for replies to `/digest`/`/groups` commands.
