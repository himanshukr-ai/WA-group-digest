from __future__ import annotations

import argparse

from app.db.session import init_db


def main() -> None:
    parser = argparse.ArgumentParser(prog="app")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("serve", help="Run the FastAPI app with uvicorn")
    subparsers.add_parser("init-db", help="Create database tables")

    args = parser.parse_args()

    if args.command == "init-db":
        init_db()
        print("Database tables created.")
    elif args.command == "serve":
        import uvicorn

        uvicorn.run("app.api:app", host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
