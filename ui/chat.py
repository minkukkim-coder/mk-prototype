"""Chat interface: render history, accept input, call the model, time it.

`st.chat_input` only auto-pins to the page bottom when placed at script root.
Inside a tab it renders inline, which means new messages appear *after* the
input box and the visual order breaks. The fix is to put all chat messages
inside a scrollable container of fixed height, with the input rendered
*after* that container — the input then always sits at the bottom of the tab.
"""

from __future__ import annotations

import streamlit as st

import json

from core.auth import current_user_data_dir, current_user_id
from core.llm import LLMError, Message, ModelConfig, chat
from core.memory import count_filled_memory_items, format_memory_for_prompt, load_state
from core.metrics import measure_seconds
from ui.sidebar import current_api_key


def _chat_history_path():
    return current_user_data_dir() / "chat_history.json"


def _load_chat_history() -> list[dict]:
    path = _chat_history_path()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _save_chat_history(messages: list[dict]) -> None:
    try:
        _chat_history_path().write_text(
            json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        # Disk full or permission issue — don't crash chat; chat still works
        # in-memory via session_state, just won't persist across sessions.
        pass


def _ensure_chat_history_loaded() -> None:
    """Load chat history from disk on first render OR when the user changes.

    Streamlit's session_state survives login/logout transitions, so without
    this check, user B could briefly see user A's messages after a switch.
    """
    current_uid = current_user_id()
    cached_uid = st.session_state.get("_chat_history_user_id")
    if cached_uid != current_uid:
        st.session_state.messages = _load_chat_history()
        st.session_state["_chat_history_user_id"] = current_uid


def _render_message(msg: dict) -> None:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and "latency_s" in msg:
            label = msg.get("model_label", "?")
            st.caption(f"⏱ {msg['latency_s']:.2f}s  ·  {label}")


def _build_system_prompt(base_prompt: str, memory_enabled: bool) -> tuple[str, str]:
    """Combine base prompt + memory block. Returns (final_prompt, memory_block)."""
    if not memory_enabled:
        return base_prompt, ""
    memory_block = format_memory_for_prompt()
    if not memory_block:
        return base_prompt, ""
    return f"{base_prompt}\n\n{memory_block}", memory_block


def _render_memory_status_bar(memory_enabled: bool) -> str:
    """Render a small status row above the chat. Returns the memory block used."""
    state = load_state()
    filled = count_filled_memory_items(state)

    col_status, col_inspect = st.columns([4, 1])
    with col_status:
        if not memory_enabled:
            st.caption("🧠 메모리 참조: **OFF**  ·  사이드바에서 켤 수 있습니다")
        elif filled == 0:
            st.caption("🧠 메모리 참조: ON  ·  아직 누적된 메모리 없음 (🧠 메모리 탭에서 추출 실행)")
        else:
            st.caption(f"🧠 메모리 참조: **ON**  ·  {filled}개 항목이 시스템 프롬프트에 주입됨")

    memory_block = format_memory_for_prompt(state) if memory_enabled else ""

    with col_inspect:
        if memory_block and st.button("🔍 주입 내용", key="chat_inspect_memory_btn", width="stretch"):
            st.session_state["_show_memory_inspector"] = not st.session_state.get(
                "_show_memory_inspector", False
            )

    if memory_block and st.session_state.get("_show_memory_inspector"):
        with st.expander("📋 현재 시스템 프롬프트에 주입되는 메모리 블록", expanded=True):
            st.code(memory_block, language="markdown")

    return memory_block


def render_chat(model_config: ModelConfig, system_prompt: str) -> None:
    _ensure_chat_history_loaded()

    memory_enabled = st.session_state.get("memory_reference_enabled", True)
    _render_memory_status_bar(memory_enabled)

    # Scrollable history area. The input goes BELOW this container so it
    # always shows at the bottom of the tab, with history scrolling above.
    # Height is configurable from the sidebar so users can match their viewport.
    history_height = st.session_state.get("chat_history_height", 420)
    history_box = st.container(height=history_height)

    user_text = st.chat_input("메시지를 입력하세요…")

    # Append the user's new turn BEFORE rendering so the loop sees it.
    if user_text:
        st.session_state.messages.append({"role": "user", "content": user_text})
        _save_chat_history(st.session_state.messages)

    with history_box:
        for msg in st.session_state.messages:
            _render_message(msg)

        if not user_text:
            return

        # Build the final system prompt with optional memory injection.
        final_system_prompt, _ = _build_system_prompt(system_prompt, memory_enabled)

        history = [
            Message(role=m["role"], content=m["content"])
            for m in st.session_state.messages
        ]

        with st.chat_message("assistant"):
            placeholder = st.empty()
            latency_caption = st.empty()
            placeholder.markdown("_응답 생성 중…_")

            try:
                with measure_seconds() as t:
                    reply = chat(
                        model_config=model_config,
                        system_prompt=final_system_prompt,
                        messages=history,
                        api_key=current_api_key(model_config.provider),
                    )
            except LLMError as e:
                placeholder.error(str(e))
                st.session_state.messages.pop()  # don't poison next turn
                _save_chat_history(st.session_state.messages)
                return

            placeholder.markdown(reply)
            latency_caption.caption(f"⏱ {t.seconds:.2f}s  ·  {model_config.label}")

            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": reply,
                    "latency_s": t.seconds,
                    "model_label": model_config.label,
                }
            )
            _save_chat_history(st.session_state.messages)
