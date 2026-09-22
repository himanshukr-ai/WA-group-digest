from __future__ import annotations

import argparse

import httpx
import respx

from app.cli import cmd_groups
from app.config import get_settings

BASE_URL = "https://gate.whapi.cloud"


@respx.mock
def test_cmd_groups_lists_and_marks_watchlisted(db_env, capsys):
    settings = get_settings()
    respx.get(f"{BASE_URL}/groups").mock(
        return_value=httpx.Response(
            200,
            json={
                "groups": [
                    {
                        "id": "120363000000000001@g.us",
                        "name": "Site A Coordination",
                        "participants_count": 12,
                        "participants": [],
                    },
                    {
                        "id": "999@g.us",
                        "name": "Other Group",
                        "participants_count": 3,
                        "participants": [],
                    },
                ],
                "count": 2,
                "total": 2,
                "offset": 0,
            },
        )
    )

    cmd_groups(argparse.Namespace())

    out = capsys.readouterr().out
    assert "[*] 120363000000000001@g.us" in out
    assert "[ ] 999@g.us" in out
