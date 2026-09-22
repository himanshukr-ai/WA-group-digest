from __future__ import annotations

from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def render_prompt(filename: str, **kwargs: str) -> str:
    text = (PROMPTS_DIR / filename).read_text(encoding="utf-8")
    return Template(text).safe_substitute(**kwargs)
