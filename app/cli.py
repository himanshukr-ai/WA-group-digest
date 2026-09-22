from __future__ import annotations

import argparse

from app.config import get_settings
from app.db.session import init_db, session_scope
from app.db.sync_groups import sync_groups


def cmd_init_db(args: argparse.Namespace) -> None:
    init_db()
    print("Database tables created.")


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run("app.api:app", host="0.0.0.0", port=8000, reload=True)


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


def main() -> None:
    parser = argparse.ArgumentParser(prog="app")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("serve", help="Run the FastAPI app with uvicorn")
    subparsers.add_parser("init-db", help="Create database tables")
    subparsers.add_parser("groups", help="List WhatsApp groups visible to this Whapi channel")

    backfill_parser = subparsers.add_parser("backfill", help="Pull message history for watchlisted groups")
    backfill_parser.add_argument("--days", type=int, default=7, help="How many days of history to pull")
    backfill_parser.add_argument("--group", type=str, default=None, help="Limit to one group (id or name)")

    args = parser.parse_args()

    commands = {
        "init-db": cmd_init_db,
        "serve": cmd_serve,
        "groups": cmd_groups,
        "backfill": cmd_backfill,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
