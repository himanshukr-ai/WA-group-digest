from __future__ import annotations

import argparse

import pytest

from app.cli import cmd_digest


def test_cmd_digest_requires_api_key(db_env):
    # db_env sets WHAPI_TOKEN but not ANTHROPIC_API_KEY
    with pytest.raises(SystemExit):
        cmd_digest(argparse.Namespace(window="1d", group=None))
