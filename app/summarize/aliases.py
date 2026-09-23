from __future__ import annotations

import re
import threading

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import MemberAlias, Message

# A display name that is really just a phone number (or empty / unresolved).
_NUMBER_LIKE_NAME = re.compile(r"^\+?[\d\s\-().]{7,}$")
# A bare or @-mentioned id: 8-15 digits, not part of a longer number or word.
_ID = re.compile(r"(?<!\w)(@?)\+?(\d{8,15})(?!\d)")
# "+971 58 594 9007"-style formatted numbers.
_SPACED = re.compile(r"\+\d[\d ()\-]{7,20}\d")


def local_id(sender_id: str | None) -> str:
    """'971501234567@s.whatsapp.net' / '1524746986546@lid' -> the part before the @."""
    return (sender_id or "").split("@")[0]


def is_number_like(name: str | None) -> bool:
    text = (name or "").strip()
    return not text or text == "Unknown" or bool(_NUMBER_LIKE_NAME.match(text))


def summary_has_raw_numbers(summary: dict) -> bool:
    """True if a cached pass-1 summary names a person by phone number.

    Person fields should only ever hold names or labels now. A number there means the summary
    predates aliasing (or slipped past it), and it can't be scrubbed reliably: the model may have
    garbled the digits when copying them, so an exact match misses it. Such days are rebuilt.
    Numbers in free text (order ids, quotes) are deliberately ignored.
    """
    # Cached model output isn't guaranteed to match the schema (e.g. a plain string where an
    # object belongs), so read every field defensively.
    def items(key: str) -> list[dict]:
        value = summary.get(key)
        return [i for i in value if isinstance(i, dict)] if isinstance(value, list) else []

    people: list = []
    for topic in items("topics"):
        participants = topic.get("key_participants")
        if isinstance(participants, list):
            people.extend(participants)
    people.extend(take.get("person") for take in items("hot_takes"))
    people.extend(item.get("owner") for item in items("action_items"))
    return any(isinstance(p, str) and bool(_NUMBER_LIKE_NAME.match(p.strip())) for p in people)


class Pseudonymizer:
    """Swaps phone numbers for stable labels (SMM1, SMM2, ...) so digests don't expose them.

    Labels are persisted in `member_aliases`, so a number keeps its label across days, groups and
    restarts. Members with a real WhatsApp name keep their name. Raw numbers stay in the
    `messages` table; only what is sent to Claude and shown in digests is scrubbed.

    New labels are created in the caller's session. Two threads building digests at the same
    instant could race for the same number; the unique constraint on `label` makes that fail
    loudly rather than duplicate, and a retry succeeds.
    """

    _lock = threading.Lock()

    def __init__(self, session: Session, prefix: str):
        self.session = session
        self.prefix = (prefix or "").strip()
        self.enabled = bool(self.prefix)
        self._labels: dict[str, str] = {}
        self._names: dict[str, str] = {}  # number -> real name, when we've seen one for them
        self._next = 1
        if not self.enabled:
            return
        # Someone can appear number-only on some messages and named on others. If we know their
        # real name, use it everywhere. Ordered oldest to newest, so the latest name wins.
        for sender_id, sender_name, _last in session.execute(
            select(Message.sender_id, Message.sender_name, func.max(Message.timestamp_utc))
            .group_by(Message.sender_id, Message.sender_name)
            .order_by(func.max(Message.timestamp_utc))
        ):
            number = local_id(sender_id)
            if number.isdigit() and not is_number_like(sender_name):
                self._names[number] = sender_name
        pattern = re.compile(rf"^{re.escape(self.prefix)}(\d+)$")
        for row in session.execute(select(MemberAlias)).scalars():
            self._labels[row.local_id] = row.label
            match = pattern.match(row.label)
            if match:
                self._next = max(self._next, int(match.group(1)) + 1)

    def alias_for(self, number: str) -> str:
        label = self._labels.get(number)
        if label is not None:
            return label
        with self._lock:
            label = f"{self.prefix}{self._next}"
            self._next += 1
            self.session.add(MemberAlias(local_id=number, label=label))
            self.session.flush()
            self._labels[number] = label
        return label

    def _display(self, number: str, create: bool) -> str | None:
        """A real name if we know one, else the stable label (created on demand if asked)."""
        name = self._names.get(number)
        if name:
            return name
        label = self._labels.get(number)
        if label is None and create:
            label = self.alias_for(number)
        return label

    def label_for(self, sender_id: str | None, sender_name: str | None) -> str:
        """What to call a sender: their real name, or a stable label if all we have is a number."""
        if not self.enabled:
            return sender_name or ""
        if not is_number_like(sender_name):
            return self.scrub(sender_name or "")
        number = local_id(sender_id)
        if not (number.isdigit() and 8 <= len(number) <= 15):
            digits = re.sub(r"\D", "", sender_name or "")
            number = digits if 8 <= len(digits) <= 15 else ""
        return self._display(number, create=True) if number else (sender_name or "Unknown")

    def scrub(self, text: str) -> str:
        """Replace known numbers (bare, +prefixed, formatted) and any @mentioned id with labels.

        A bare number that isn't a known member is left alone (it may be an order number, not a
        phone), but an @mention is always a person, so it gets a label too.
        """
        if not self.enabled or not text:
            return text

        def replace_id(match: re.Match) -> str:
            at, digits = match.group(1), match.group(2)
            shown = self._display(digits, create=bool(at))
            return at + shown if shown else match.group(0)

        def replace_spaced(match: re.Match) -> str:
            shown = self._display(re.sub(r"\D", "", match.group(0)), create=False)
            return shown if shown else match.group(0)

        text = _SPACED.sub(replace_spaced, _ID.sub(replace_id, text))

        # Labels handed out before we learned someone's name (e.g. in older cached summaries).
        renamed = {label: self._names[number] for number, label in self._labels.items() if number in self._names}
        if renamed:
            pattern = re.compile(r"(?<!\w)(" + "|".join(re.escape(label) for label in renamed) + r")(?!\w)")
            text = pattern.sub(lambda m: renamed[m.group(1)], text)
        return text

    def ensure_senders(self, group_ids: list[str]) -> None:
        """Give every number-only sender in these groups a label, oldest first, so numbers that
        only appear in previously cached summaries are still scrubbed and numbering follows
        first appearance."""
        if not self.enabled or not group_ids:
            return
        rows = self.session.execute(
            select(Message.sender_id, Message.sender_name, func.min(Message.timestamp_utc))
            .where(Message.group_id.in_(group_ids))
            .group_by(Message.sender_id, Message.sender_name)
            .order_by(func.min(Message.timestamp_utc))
        ).all()
        for sender_id, sender_name, _first_seen in rows:
            self.label_for(sender_id, sender_name)
