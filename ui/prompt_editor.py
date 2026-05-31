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
            new_content = load_prompt("chat", chosen)
            st.session_state[_k("name")] = chosen
            st.session_state[_k("text")] = new_content
            # Must also update the widget's own session_state key — otherwise
            # st.text_area keeps showing its previous content and ignores `value=`.
            st.session_state[_k("textarea")] = new_content
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

    st.divider()
    st.markdown("##### 저장")

    save_tab, saveas_tab, manage_tab = st.tabs(
        [
            f"💾 '{current_name}'에 덮어쓰기",
            "📋 새 프롬프트로 저장 (이름 입력 후 저장)",
            "🗑 삭제 / 기본값 복원",
        ]
    )

    # ---- Tab 1: overwrite current ----
    with save_tab:
        st.caption(
            f"현재 편집 내용을 **'{current_name}'** 에 저장합니다. "
            f"같은 이름의 이전 내용은 사라집니다."
        )
        if st.button(
            f"💾 '{current_name}'에 저장",
            key=_k("save_btn"),
            width="stretch",
            type="primary",
        ):
            save_prompt("chat", current_name, edited)
            st.success(f"'{current_name}' — 본인 계정에 저장됨")
            st.rerun()

    # ---- Tab 2: save as new (multiple prompts) ----
    with saveas_tab:
        st.caption(
            "현재 편집 내용을 **새 이름으로** 저장합니다. "
            "기존 프롬프트는 그대로 남고, 새 프롬프트가 드롭다운에 추가됩니다."
        )
        new_name = st.text_input(
            "새 프롬프트 이름",
            key=_k("saveas_input"),
            placeholder="예: v2-공감강조  |  단답형  |  코칭톤",
        )
        if st.button(
            "📋 새 프롬프트로 저장",
            key=_k("saveas_btn"),
            width="stretch",
            type="primary",
            disabled=not new_name.strip(),
        ):
            actual = save_prompt("chat", new_name, edited)
            st.session_state[_k("name")] = actual
            st.success(
                f"새 프롬프트 '{actual}' 생성됨. 상단 드롭다운에서 선택해서 사용하세요."
            )
            st.rerun()

    # ---- Tab 3: delete / reset to default ----
    with manage_tab:
        # If this is a customization of a shipped default, deleting actually
        # "resets to default" (the default file is untouched). For user-only
        # prompts it deletes outright. Disable button when there's nothing to delete.
        is_custom = is_customized("chat", current_name)
        has_def = has_default("chat", current_name)
        if is_custom and has_def:
            label = f"↩️ '{current_name}'을 기본값으로 복원"
            help_text = "본인 커스텀 버전을 삭제하고 기본값으로 되돌립니다."
            caption = (
                f"'{current_name}' 프롬프트는 본인이 커스터마이즈한 버전이 있습니다. "
                "기본값으로 복원하면 본인 수정 내용이 사라지고, 다른 팀원과 동일한 기본값이 적용됩니다."
            )
        elif is_custom:
            label = f"🗑 '{current_name}' 프롬프트 삭제"
            help_text = "본인 커스텀 프롬프트를 영구 삭제합니다."
            caption = (
                f"'{current_name}'은 본인이 새로 만든 프롬프트입니다. "
                "삭제하면 영구히 사라집니다 (다른 팀원에게는 영향 없음)."
            )
        else:
            label = "🗑 삭제 불가"
            help_text = "기본값은 삭제할 수 없으며, 본인이 만든 커스텀 버전도 없습니다."
            caption = f"'{current_name}'은 기본값입니다. 삭제할 본인 버전이 없습니다."

        st.caption(caption)
        if st.button(
            label,
            key=_k("del_btn"),
            disabled=not is_custom,
            help=help_text,
            width="stretch",
        ):
            delete_prompt("chat", current_name)
            fallback = current_name if has_def else DEFAULT_NAME
            new_content = load_prompt("chat", fallback)
            st.session_state[_k("name")] = fallback
            st.session_state[_k("text")] = new_content
            # Sync widget key so the text area actually refreshes (same gotcha as load).
            st.session_state[_k("textarea")] = new_content
            st.success(f"'{current_name}' 처리 완료")
            st.rerun()
