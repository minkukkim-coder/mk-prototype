"""Chat prompt editor: load / edit / save / delete chat system prompts.

All edits are scoped to the currently logged-in user — saving creates a copy
in `user_data/{uid}/prompts/chat/` without touching the shared defaults.

The text area is the source of truth for what's active right now — the chat
pulls its system prompt directly from session state, so unsaved edits take
effect immediately when you send the next message. Saving just persists.
"""

from __future__ import annotations

import streamlit as st

from core.auth import current_user_id
from core.prompts import (
    DEFAULT_NAME,
    delete_prompt,
    has_default,
    is_customized,
    list_prompts,
    load_prompt,
    save_prompt,
)


def _k(suffix: str) -> str:
    return f"prompt_chat_{suffix}"


def _ensure_loaded() -> None:
    """Initialize on first render OR when the user changes (login switch)."""
    current_uid = current_user_id()
    if st.session_state.get(_k("user_id")) != current_uid:
        st.session_state[_k("user_id")] = current_uid
        st.session_state[_k("name")] = DEFAULT_NAME
        st.session_state[_k("text")] = load_prompt("chat", DEFAULT_NAME)


def get_active_chat_prompt() -> str:
    _ensure_loaded()
    return st.session_state[_k("text")]


def get_active_chat_prompt_name() -> str:
    _ensure_loaded()
    return st.session_state[_k("name")]


def render_chat_prompt_editor() -> None:
    _ensure_loaded()
    st.subheader("💬 대화용 시스템 프롬프트")
    st.caption(
        "챗봇 응답에 사용되는 프롬프트입니다. 편집한 내용은 즉시 다음 호출에 반영되며, "
        "저장은 별도입니다. **저장은 본인 계정에만 적용되어** 다른 팀원에게 영향을 주지 않습니다."
    )

    names = list_prompts("chat")
    current_name = st.session_state[_k("name")]

    col_sel, col_load = st.columns([4, 1])
    with col_sel:
        labels = [f"{'✨ ' if is_customized('chat', n) else ''}{n}" for n in names]
        chosen_idx = st.selectbox(
            "저장된 프롬프트 (✨ = 본인 커스텀 버전)",
            options=range(len(names)),
            format_func=lambda i: labels[i],
            index=names.index(current_name) if current_name in names else 0,
            key=_k("selector"),
        )
        chosen = names[chosen_idx]
    with col_load:
        st.write("")
        if st.button("불러오기", key=_k("load_btn"), width="stretch"):
            st.session_state[_k("name")] = chosen
            st.session_state[_k("text")] = load_prompt("chat", chosen)
            st.rerun()

    if is_customized("chat", current_name):
        st.info(f"✨ 현재 **본인 커스텀 버전**을 보고 있습니다.")
    elif has_default("chat", current_name):
        st.caption("📦 기본값을 보고 있습니다. 편집 후 저장하면 본인 전용 커스텀 버전이 생성됩니다.")

    edited = st.text_area(
        "프롬프트 내용",
        value=st.session_state[_k("text")],
        height=300,
        key=_k("textarea"),
    )
    st.session_state[_k("text")] = edited

    col_save, col_saveas, col_del = st.columns([1, 2, 1])
    with col_save:
        if st.button(
            f"💾 '{current_name}'에 내 버전으로 저장",
            key=_k("save_btn"),
            width="stretch",
            type="primary",
        ):
            save_prompt("chat", current_name, edited)
            st.success(f"'{current_name}' — 본인 계정에 저장됨")
            st.rerun()

    with col_saveas:
        new_name = st.text_input(
            "다른 이름으로 저장",
            key=_k("saveas_input"),
            placeholder="새 프롬프트 이름 (예: v2-공감강조)",
            label_visibility="collapsed",
        )
        if st.button("💾 다른 이름으로 저장", key=_k("saveas_btn"), width="stretch"):
            if not new_name.strip():
                st.warning("이름을 입력하세요.")
            else:
                actual = save_prompt("chat", new_name, edited)
                st.session_state[_k("name")] = actual
                st.success(f"'{actual}' — 본인 계정에 저장됨")
                st.rerun()

    with col_del:
        st.write("")
        # If this is a customization of a shipped default, deleting actually
        # "resets to default" (the default file is untouched). For user-only
        # prompts it deletes outright. Disable button when there's nothing to delete.
        is_custom = is_customized("chat", current_name)
        has_def = has_default("chat", current_name)
        if is_custom and has_def:
            label, help_text = "↩️ 기본값으로 복원", "본인 커스텀 버전 삭제 후 기본값으로 복원"
        elif is_custom:
            label, help_text = "🗑 내 프롬프트 삭제", "본인 커스텀 프롬프트 삭제"
        else:
            label, help_text = "🗑 삭제", "삭제할 본인 버전이 없습니다."
        if st.button(label, key=_k("del_btn"), disabled=not is_custom, help=help_text, width="stretch"):
            delete_prompt("chat", current_name)
            # Fall back to default; if the deleted item was user-only, switch to DEFAULT_NAME.
            fallback = current_name if has_def else DEFAULT_NAME
            st.session_state[_k("name")] = fallback
            st.session_state[_k("text")] = load_prompt("chat", fallback)
            st.success(f"'{current_name}' 처리 완료")
            st.rerun()
