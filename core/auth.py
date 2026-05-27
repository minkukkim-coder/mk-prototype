"""Auth helpers: login gate, allowlist check, and per-user data directories.

We rely on Streamlit's native OIDC support (st.login / st.user / st.logout),
configured via `.streamlit/secrets.toml`. This module also exposes the
per-user data directory so memory and chat history stay isolated between
team members.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import streamlit as st
import yaml

ROOT = Path(__file__).parent.parent
ALLOWED_USERS_YAML = ROOT / "config" / "allowed_users.yaml"
USER_DATA_ROOT = ROOT / "user_data"


def load_allowed_emails() -> set[str]:
    if not ALLOWED_USERS_YAML.exists():
        return set()
    with ALLOWED_USERS_YAML.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {email.strip().lower() for email in (data.get("emails") or []) if email}


def _auth_configured() -> bool:
    """True if [auth] block exists in secrets.toml with required keys."""
    try:
        auth = st.secrets.get("auth", {})
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return False
    return bool(auth.get("client_id") and auth.get("client_secret"))


def _is_logged_in() -> bool:
    """Safe accessor for st.user.is_logged_in (returns False on any error)."""
    try:
        return bool(st.user.is_logged_in)
    except (AttributeError, KeyError, Exception):  # noqa: BLE001
        return False


def require_login() -> None:
    """Render login/no-permission screens and stop execution if not allowed.

    Returns silently when the current user is logged in AND on the allowlist.
    """
    if not _auth_configured():
        st.error("🔐 인증이 설정되지 않았습니다.")
        st.markdown(
            "`.streamlit/secrets.toml` 파일에 Google OAuth 정보를 채워야 합니다. "
            "`.streamlit/secrets.toml.example`을 참고하세요."
        )
        st.stop()

    if not _is_logged_in():
        _render_login_screen()
        st.stop()

    email = (getattr(st.user, "email", "") or "").strip().lower()
    allowed = load_allowed_emails()

    if email not in allowed:
        _render_forbidden_screen(email)
        st.stop()


def _render_login_screen() -> None:
    st.title("💬 상담 챗봇 프로토타입")
    st.markdown("이 프로토타입은 승인된 구글 계정 사용자만 이용할 수 있습니다.")
    st.markdown("")
    st.button("🔐 Google 계정으로 로그인", type="primary", on_click=st.login)


def _render_forbidden_screen(email: str) -> None:
    st.title("🚫 접근 권한 없음")
    st.error(
        f"`{email}` 계정은 이 프로토타입의 허용 목록에 없습니다.\n\n"
        "관리자에게 계정 등록을 요청하세요."
    )
    st.button("다른 계정으로 로그인", on_click=st.logout)


def render_user_chip() -> None:
    """Show 'logged in as X' + logout button in the sidebar."""
    if not _is_logged_in():
        return
    email = getattr(st.user, "email", "?")
    name = getattr(st.user, "name", "") or email
    st.sidebar.divider()
    st.sidebar.caption(f"👤 {name}  ·  `{email}`")
    if st.sidebar.button("로그아웃", width="stretch"):
        st.logout()


# ---------- Per-user data isolation ----------

def current_user_id() -> str:
    """A stable, filesystem-safe ID derived from the user's email.

    Returns "anonymous" if not logged in — useful for local dev when auth
    is bypassed, though in production require_login() ensures this never fires.
    """
    if not _is_logged_in():
        return "anonymous"
    email = (getattr(st.user, "email", "") or "").strip().lower()
    if not email:
        return "anonymous"
    return hashlib.sha256(email.encode("utf-8")).hexdigest()[:16]


def current_user_data_dir() -> Path:
    """Path to the current user's data folder, created on demand."""
    d = USER_DATA_ROOT / current_user_id()
    d.mkdir(parents=True, exist_ok=True)
    return d
