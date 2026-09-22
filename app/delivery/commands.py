from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.config import Settings
from app.summarize.pass1 import AnthropicLike
from app.summarize.pass2 import build_digest, parse_window
from app.whapi.schemas import WhapiMessage

HELP_TEXT = "Try /digest <1d|3d|7d> [group name] or /groups."


@dataclass
class ParsedCommand:
    name: str
    args: list[str] = field(default_factory=list)


def parse_command(text: str) -> ParsedCommand | None:
    text = text.strip()
    if not text.startswith("/"):
        return None
    parts = text.split()
    name = parts[0][1:].lower()
    if not name:
        return None
    return ParsedCommand(name=name, args=parts[1:])


def is_self_command(message: WhapiMessage, settings: Settings) -> bool:
    """Only messages the user sent themselves, to their own chat, are treated as commands."""
    if not settings.self_chat_id:
        return False
    return message.chat_id == settings.self_chat_id and message.from_me and bool(message.text and message.text.body.strip())


def _format_groups_list(settings: Settings) -> str:
    groups = settings.load_groups()
    if not groups:
        return "No groups configured in groups.yaml."
    lines = [f"{'✓' if g.enabled else '✗'} {g.name}" for g in groups]
    return "Watchlist:\n" + "\n".join(lines)


def handle_command(
    session: Session | None,
    anthropic_client: AnthropicLike | None,
    settings: Settings,
    command: ParsedCommand,
) -> str | None:
    """Return the reply text for a parsed self-chat command, or None if nothing should be sent."""
    if command.name == "groups":
        return _format_groups_list(settings)

    if command.name == "digest":
        if not command.args:
            return "Usage: /digest <1d|3d|7d> [group name]"
        try:
            window_days = parse_window(command.args[0])
        except ValueError as exc:
            return str(exc)
        if anthropic_client is None:
            return "ANTHROPIC_API_KEY is not configured."
        focus_group = " ".join(command.args[1:]) or None
        digest_text, _usage = build_digest(
            session, anthropic_client, settings, settings.load_groups(), window_days, focus_group=focus_group
        )
        return digest_text

    return f"Unknown command /{command.name}. {HELP_TEXT}"
