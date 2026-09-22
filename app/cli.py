from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.db.session import init_db, session_scope
from app.db.sync_groups import sync_groups


def cmd_init_db(args: argparse.Namespace) -> None:
    init_db()
    print("Database tables created.")


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run("app.api:app", host="0.0.0.0", port=8000, reload=args.reload)


def cmd_groups(args: argparse.Namespace) -> None:
    """List the WhatsApp groups visible to this Whapi channel, to help populate groups.yaml."""
    from app.whapi.client import WhapiClient

    settings = get_settings()
    watchlist_ids = {g.id for g in settings.load_groups()}

    with WhapiClient(settings) as client:
        offset = 0
        count = 100
        while True:
            page = client.get_groups(count=count, offset=offset)
            if not page.groups:
                break
            for g in page.groups:
                marker = "*" if g.id in watchlist_ids else " "
                print(f"[{marker}] {g.id}  {g.name!r}  ({g.participants_count} participants)")
            if len(page.groups) < count:
                break
            offset += count

    print("\n* = already in groups.yaml watchlist")


def cmd_backfill(args: argparse.Namespace) -> None:
    from app.ingest.backfill import backfill_groups
    from app.whapi.client import WhapiClient

    settings = get_settings()
    groups = settings.load_groups()
    if args.group:
        groups = [g for g in groups if g.id == args.group or g.name == args.group]
        if not groups:
            print(f"No group in groups.yaml matches {args.group!r}")
            return

    init_db()
    with session_scope() as session:
        sync_groups(session, settings.load_groups())

    with WhapiClient(settings) as client, session_scope() as session:
        results = backfill_groups(client, session, groups, days=args.days)

    if not results:
        print("No enabled groups to backfill. Check groups.yaml.")
        return

    for group_id, inserted in results.items():
        print(f"{group_id}: {inserted} new messages")


def cmd_digest(args: argparse.Namespace) -> None:
    import sys

    import anthropic

    from app.summarize.pass2 import build_digest, parse_window

    settings = get_settings()
    if not settings.anthropic_api_key:
        print("ANTHROPIC_API_KEY is not set.", file=sys.stderr)
        raise SystemExit(1)

    window_days = parse_window(args.window)
    groups = settings.load_groups()

    init_db()
    with session_scope() as session:
        sync_groups(session, groups)

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    with session_scope() as session:
        digest_text, usage = build_digest(session, client, settings, groups, window_days, focus_group=args.group)

    print(digest_text)
    print(
        f"\n---\ntokens: {usage['input_tokens']} in / {usage['output_tokens']} out  ~${usage['cost']:.4f}",
        file=sys.stderr,
    )


def cmd_retention(args: argparse.Namespace) -> None:
    from app.db.retention import purge_old_messages

    settings = get_settings()
    init_db()
    with session_scope() as session:
        deleted = purge_old_messages(session, settings)
    print(f"Deleted {deleted} messages older than {settings.retention_days} days.")


def main() -> None:
    # Group/message text routinely contains emoji; Windows consoles default to a legacy
    # codepage (cp1252) that can't encode it, so force UTF-8 on stdout/stderr up front.
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="app")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the FastAPI app with uvicorn")
    serve_parser.add_argument("--reload", action="store_true", default=False, help="Auto-reload on code changes (dev only)")
    subparsers.add_parser("init-db", help="Create database tables")
    subparsers.add_parser("groups", help="List WhatsApp groups visible to this Whapi channel")

    backfill_parser = subparsers.add_parser("backfill", help="Pull message history for watchlisted groups")
    backfill_parser.add_argument("--days", type=int, default=7, help="How many days of history to pull")
    backfill_parser.add_argument("--group", type=str, default=None, help="Limit to one group (id or name)")

    digest_parser = subparsers.add_parser("digest", help="Print a merged digest for a time window")
    digest_parser.add_argument("--window", type=str, default="1d", help="1d, 3d, 7d, or a number of days")
    digest_parser.add_argument("--group", type=str, default=None, help="Limit to one group (id or name)")

    subparsers.add_parser("retention", help="Delete raw messages past RETENTION_DAYS (summaries are kept)")

    args = parser.parse_args()

    commands = {
        "init-db": cmd_init_db,
        "serve": cmd_serve,
        "groups": cmd_groups,
        "backfill": cmd_backfill,
        "digest": cmd_digest,
        "retention": cmd_retention,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
