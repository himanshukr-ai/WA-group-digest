from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_settings

_basic = HTTPBasic(auto_error=False)

WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}


def _matches(supplied: str, expected: str) -> bool:
    # Compare bytes: compare_digest raises TypeError on non-ASCII str.
    return secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def require_admin(credentials: HTTPBasicCredentials | None = Depends(_basic)) -> None:
    settings = get_settings()
    if not settings.admin_password:
        # Fail closed and don't advertise that an admin page exists.
        raise HTTPException(status_code=404)

    user_ok = credentials is not None and _matches(credentials.username, settings.admin_username)
    pass_ok = credentials is not None and _matches(credentials.password, settings.admin_password)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": 'Basic realm="WhatsApp Digest admin"'},
        )


async def require_json_for_writes(request: Request) -> None:
    """Writes must be JSON. A cross-site HTML form can't send application/json without a CORS
    preflight (which we never allow), so this closes CSRF against Basic auth, which browsers
    attach to cross-site requests automatically."""
    if request.method in WRITE_METHODS:
        content_type = request.headers.get("content-type", "").lower()
        if not content_type.startswith("application/json"):
            raise HTTPException(status_code=415, detail="Content-Type must be application/json")
