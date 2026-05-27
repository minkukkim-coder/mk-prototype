"""Per-user prompt storage: list / load / save / delete with default fallback.

Prompts come in two flavors:
  - Shipped defaults under `config/prompts/<kind>/`  — same for everyone.
  - User customizations under `user_data/{uid}/prompts/<kind>/` — per-account.

Resolution order: user copy first, then fall back to the shipped default.
This way each team member can experiment with their own variants without
overwriting anyone else's.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

Kind = Literal["chat", "memory"]

ROOT = Path(__file__).parent.parent
DEFAULT_PROMPTS_DIR = ROOT / "config" / "prompts"

DEFAULT_NAME = "default"

# Allow Korean (Hangul) plus ASCII alphanumerics, dash, underscore.
_VALID_NAME = re.compile(r"[^\w\-가-힣]+")


def _default_dir(kind: Kind) -> Path:
    d = DEFAULT_PROMPTS_DIR / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def _user_dir(kind: Kind) -> Path:
    # Local import avoids a circular dependency at module load.
    from core.auth import current_user_data_dir
    d = current_user_data_dir() / "prompts" / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def sanitize_name(name: str) -> str:
    cleaned = _VALID_NAME.sub("_", name.strip())
    return cleaned[:64] or "untitled"


def list_prompts(kind: Kind) -> list[str]:
    """Union of user prompts and defaults. 'default' first if present."""
    user_names = {p.stem for p in _user_dir(kind).glob("*.md")}
    default_names = {p.stem for p in _default_dir(kind).glob("*.md")}
    names = sorted(user_names | default_names)
    if DEFAULT_NAME in names:
        names.remove(DEFAULT_NAME)
        names.insert(0, DEFAULT_NAME)
    return names


def load_prompt(kind: Kind, name: str) -> str:
    """User's copy takes precedence; fall back to the shipped default."""
    user_path = _user_dir(kind) / f"{name}.md"
    if user_path.exists():
        return user_path.read_text(encoding="utf-8")
    return (_default_dir(kind) / f"{name}.md").read_text(encoding="utf-8")


def save_prompt(kind: Kind, name: str, content: str) -> str:
    """Always saves to the user's directory, leaving the shared default intact."""
    safe = sanitize_name(name)
    (_user_dir(kind) / f"{safe}.md").write_text(content, encoding="utf-8")
    return safe


def delete_prompt(kind: Kind, name: str) -> None:
    """Delete the user's copy. If a shipped default exists, it remains visible
    and becomes the active version (i.e. this acts as 'reset to default')."""
    user_path = _user_dir(kind) / f"{name}.md"
    if user_path.exists():
        user_path.unlink()


def is_customized(kind: Kind, name: str) -> bool:
    """True iff the current user has their own version of this prompt."""
    return (_user_dir(kind) / f"{name}.md").exists()


def has_default(kind: Kind, name: str) -> bool:
    """True iff the shipped default exists for this name."""
    return (_default_dir(kind) / f"{name}.md").exists()
