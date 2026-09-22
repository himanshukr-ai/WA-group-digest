from __future__ import annotations

import datetime as dt
import json
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import GroupConfig, Settings
from app.summarize.cache import get_or_build_daily_summary
from app.summarize.pass1 import AnthropicLike
from app.summarize.prompts import render_prompt
from app.summarize.usage import log_usage

WINDOW_ALIASES = {"1d": 1, "3d": 3, "7d": 7}


def parse_window(window: str) -> int:
    """Accepts '1d'/'3d'/'7d' or a bare integer number of days."""
    if window in WINDOW_ALIASES:
        return WINDOW_ALIASES[window]
    try:
        days = int(window.rstrip("d"))
    except ValueError as exc:
        raise ValueError(f"Invalid window {window!r}; use 1d, 3d, 7d, or a number of days") from exc
    if days < 1:
        raise ValueError(f"Window must be at least 1 day, got {window!r}")
    return days


def build_digest(
    session: Session,
    client: AnthropicLike,
    settings: Settings,
    groups: list[GroupConfig],
    window_days: int,
    focus_group: str | None = None,
    end_date: dt.date | None = None,
) -> tuple[str, dict]:
    """Merge cached per-day summaries for the window ending on `end_date` (default: today) into
    one digest. Returns (digest_text, usage)."""
    tz = ZoneInfo(settings.timezone)
    end_date = end_date or dt.datetime.now(tz).date()
    dates = sorted(end_date - dt.timedelta(days=i) for i in range(window_days))

    target_groups = [g for g in groups if g.enabled]
    if focus_group:
        target_groups = [g for g in target_groups if g.id == focus_group or g.name == focus_group]

    total_input_tokens = 0
    total_output_tokens = 0

    per_group_days: dict[str, dict[str, dict]] = {}
    for group in target_groups:
        day_entries: dict[str, dict] = {}
        for date in dates:
            row, was_cached = get_or_build_daily_summary(session, client, settings, group, date)
            if not was_cached:
                total_input_tokens += row.input_tokens
                total_output_tokens += row.output_tokens
            if any(row.summary_json.get(key) for key in row.summary_json):
                day_entries[date.isoformat()] = row.summary_json
        if day_entries:
            per_group_days[group.name] = day_entries

    if not per_group_days:
        usage = {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "cost": log_usage("digest (pass1 only, nothing to merge)", total_input_tokens, total_output_tokens, settings),
        }
        return "Nothing to report for this window.", usage

    window_description = f"last {window_days} day{'s' if window_days != 1 else ''} ({dates[0].isoformat()} to {dates[-1].isoformat()})"

    system = render_prompt("pass2_system.md")
    user = render_prompt(
        "pass2_user.md",
        window_description=window_description,
        user_name=settings.user_display_name or "the user",
        groups_json=json.dumps(per_group_days, indent=2, ensure_ascii=False),
    )

    response = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=2048,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    digest_text = "".join(block.text for block in response.content if block.type == "text")
    total_input_tokens += response.usage.input_tokens
    total_output_tokens += response.usage.output_tokens

    cost = log_usage("digest", total_input_tokens, total_output_tokens, settings)
    usage = {"input_tokens": total_input_tokens, "output_tokens": total_output_tokens, "cost": cost}
    return digest_text, usage
