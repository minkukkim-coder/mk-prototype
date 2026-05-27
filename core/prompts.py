"""Per-user prompt storage: list / load / save / delete with default fallback.

Prompts come in two flavors:
  - Shipped defaults under `config/prompts/<kind>/`  — same for everyone, on disk.
  - User customizations stored in Supabase (`custom_prompts` table) when DB is
    enabled; otherwise on the local filesystem at
    `user_data/{uid}/prompts/<kind>/`.

Resolution order: user copy (DB or fs) first, then fall back to the shipped
default. This way each team member can experiment with their own variants
without overwriting anyone else's, AND data survives container restarts on
Render when Supabase is configured.

Note: This module handles only the 'chat' kind. Memory item prompts use the
same DB schema (kind='memory_item') but are managed via `MemoryItem` in
`core/memory.py` because they have fixed names tied to the schema.
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
    """Union of user prompts (DB or filesystem) and defaults. 'default' first."""
    user_names: set[str] = set()
    from core import db
    if db.is_enabled():
        from core.auth import current_user_id
        user_names = db.list_custom_prompt_names(current_user_id(), kind)
    else:
        user_names = {p.stem for p in _user_dir(kind).glob("*.md")}

    default_names = {p.stem for p in _default_dir(kind).glob("*.md")}
    names = sorted(user_names | default_names)
    if DEFAULT_NAME in names:
        names.remove(DEFAULT_NAME)
        names.insert(0, DEFAULT_NAME)
    return names


def load_prompt(kind: Kind, name: str) -> str:
    """User's copy takes precedence; fall back to the shipped default."""
    from core import db
    if db.is_enabled():
        from core.auth import current_user_id
        content = db.load_custom_prompt(current_user_id(), kind, name)
        if content is not None:
            return content
    else:
        user_path = _user_dir(kind) / f"{name}.md"
        if user_path.exists():
            return user_path.read_text(encoding="utf-8")
    return (_default_dir(kind) / f"{name}.md").read_text(encoding="utf-8")


def save_prompt(kind: Kind, name: str, content: str) -> str:
    """Save under user's namespace, leaving the shared default intact."""
    safe = sanitize_name(name)
    from core import db
    if db.is_enabled():
        from core.auth import current_user_id
        if db.save_custom_prompt(current_user_id(), kind, safe, content):
            return safe
    (_user_dir(kind) / f"{safe}.md").write_text(content, encoding="utf-8")
    return safe


def delete_prompt(kind: Kind, name: str) -> None:
    """Delete the user's copy. If a shipped default exists, it remains visible
    and becomes the active version (i.e. this acts as 'reset to default')."""
    from core import db
    if db.is_enabled():
        from core.auth import current_user_id
        db.delete_custom_prompt(current_user_id(), kind, name)
        return
    user_path = _user_dir(kind) / f"{name}.md"
    if user_path.exists():
        user_path.unlink()


def is_customized(kind: Kind, name: str) -> bool:
    """True iff the current user has their own version of this prompt."""
    from core import db
    if db.is_enabled():
        from core.auth import current_user_id
        return name in db.list_custom_prompt_names(current_user_id(), kind)
    return (_user_dir(kind) / f"{name}.md").exists()


def has_default(kind: Kind, name: str) -> bool:
    """True iff the shipped default exists for this name."""
    return (_default_dir(kind) / f"{name}.md").exists()
