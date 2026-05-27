"""Supabase-backed persistent storage.

This module is the single boundary between the app and the database. All
functions degrade gracefully when Supabase isn't configured:

  - If `SUPABASE_URL` and `SUPABASE_KEY` env vars are set → use Supabase.
  - Otherwise → return defaults / no-op, so callers can fall back to local
    filesystem storage (useful for offline local dev).

The `service_role` key is expected — we do all access from the trusted
server, so we bypass row-level security and handle authorization in app code.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from supabase import Client


_client: "Client | None" = None
_init_attempted = False


def get_client() -> "Client | None":
    """Lazy-init the Supabase client. Returns None if not configured."""
    global _client, _init_attempted
    if _init_attempted:
        return _client
    _init_attempted = True

    url = (os.environ.get("SUPABASE_URL") or "").strip()
    key = (os.environ.get("SUPABASE_KEY") or "").strip()
    if not url or not key:
        return None

    try:
        from supabase import create_client
        _client = create_client(url, key)
    except Exception:
        # SDK install issue or auth failure — fall back silently.
        _client = None
    return _client


def is_enabled() -> bool:
    return get_client() is not None


# ---------- Memory state ----------

def load_memory(user_id: str) -> dict:
    client = get_client()
    if client is None:
        return {}
    try:
        resp = client.table("memory_state").select("state").eq("user_id", user_id).execute()
        rows = getattr(resp, "data", None) or []
        if rows:
            return rows[0].get("state") or {}
    except Exception:
        pass
    return {}


def save_memory(user_id: str, state: dict) -> bool:
    client = get_client()
    if client is None:
        return False
    try:
        client.table("memory_state").upsert(
            {"user_id": user_id, "state": state}, on_conflict="user_id"
        ).execute()
        return True
    except Exception:
        return False


def clear_memory(user_id: str) -> bool:
    client = get_client()
    if client is None:
        return False
    try:
        client.table("memory_state").delete().eq("user_id", user_id).execute()
        return True
    except Exception:
        return False


# ---------- Chat history ----------

def load_chat(user_id: str) -> list:
    client = get_client()
    if client is None:
        return []
    try:
        resp = client.table("chat_history").select("messages").eq("user_id", user_id).execute()
        rows = getattr(resp, "data", None) or []
        if rows:
            return rows[0].get("messages") or []
    except Exception:
        pass
    return []


def save_chat(user_id: str, messages: list) -> bool:
    client = get_client()
    if client is None:
        return False
    try:
        client.table("chat_history").upsert(
            {"user_id": user_id, "messages": messages}, on_conflict="user_id"
        ).execute()
        return True
    except Exception:
        return False


def clear_chat(user_id: str) -> bool:
    client = get_client()
    if client is None:
        return False
    try:
        client.table("chat_history").delete().eq("user_id", user_id).execute()
        return True
    except Exception:
        return False


# ---------- Custom prompts (chat + memory_item) ----------

def list_custom_prompt_names(user_id: str, kind: str) -> set[str]:
    client = get_client()
    if client is None:
        return set()
    try:
        resp = (
            client.table("custom_prompts")
            .select("name")
            .eq("user_id", user_id)
            .eq("kind", kind)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        return {r["name"] for r in rows if "name" in r}
    except Exception:
        return set()


def load_custom_prompt(user_id: str, kind: str, name: str) -> str | None:
    client = get_client()
    if client is None:
        return None
    try:
        resp = (
            client.table("custom_prompts")
            .select("content")
            .eq("user_id", user_id)
            .eq("kind", kind)
            .eq("name", name)
            .execute()
        )
        rows = getattr(resp, "data", None) or []
        if rows:
            return rows[0].get("content")
    except Exception:
        pass
    return None


def save_custom_prompt(user_id: str, kind: str, name: str, content: str) -> bool:
    client = get_client()
    if client is None:
        return False
    try:
        client.table("custom_prompts").upsert(
            {"user_id": user_id, "kind": kind, "name": name, "content": content},
            on_conflict="user_id,kind,name",
        ).execute()
        return True
    except Exception:
        return False


def delete_custom_prompt(user_id: str, kind: str, name: str) -> bool:
    client = get_client()
    if client is None:
        return False
    try:
        (
            client.table("custom_prompts")
            .delete()
            .eq("user_id", user_id)
            .eq("kind", kind)
            .eq("name", name)
            .execute()
        )
        return True
    except Exception:
        return False
