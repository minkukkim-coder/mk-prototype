"""Sidebar: model selectors (chat + extraction), API keys, controls, stats."""

from __future__ import annotations

import streamlit as st

from core.llm import ENV_KEY_NAMES, ModelConfig, get_api_key
from core.metrics import per_model_summary, summarize


def _to_model_config(entry: dict) -> ModelConfig:
    return ModelConfig(
        id=entry["id"],
        label=entry["label"],
        provider=entry["provider"],
        model=entry["model"],
    )


def render_sidebar(models: list[dict], default_id: str) -> tuple[ModelConfig, ModelConfig]:
    """Render sidebar; return (chat_model, extraction_model)."""
    st.sidebar.title("⚙️ 설정")

    ids = [m["id"] for m in models]
    labels = [m["label"] for m in models]

    # ---- Chat model ----
    st.sidebar.subheader("💬 대화용 모델")
    chat_default_idx = ids.index(default_id) if default_id in ids else 0
    chat_idx = st.sidebar.selectbox(
        "챗봇 응답에 사용",
        options=range(len(models)),
        format_func=lambda i: labels[i],
        index=chat_default_idx,
        key="selected_chat_model_idx",
    )
    chat_selected = models[chat_idx]
    st.sidebar.caption(f"`{chat_selected['provider']}` · `{chat_selected['model']}`")

    # ---- Extraction model ----
    st.sidebar.subheader("🧠 메모리 추출용 모델")
    # Default extraction model = same as chat default (typically Flash Lite — cheapest)
    extraction_idx = st.sidebar.selectbox(
        "메모리 추출에 사용 (29회 호출에 권장: 가볍고 저렴한 모델)",
        options=range(len(models)),
        format_func=lambda i: labels[i],
        index=chat_default_idx,
        key="selected_extraction_model_idx",
    )
    extraction_selected = models[extraction_idx]
    st.sidebar.caption(f"`{extraction_selected['provider']}` · `{extraction_selected['model']}`")

    # ---- API keys for both providers (deduped) ----
    needed_providers = {chat_selected["provider"], extraction_selected["provider"]}
    for provider in sorted(needed_providers):
        _render_api_key_input(provider)

    # ---- Memory reference toggle ----
    st.sidebar.divider()
    st.sidebar.subheader("🧠 메모리 참조")
    st.session_state["memory_reference_enabled"] = st.sidebar.checkbox(
        "응답 시 누적 메모리 자동 참조",
        value=st.session_state.get("memory_reference_enabled", True),
        help="체크하면 매 응답 호출 시 memory_state.json의 내용을 시스템 프롬프트에 자동 주입합니다. "
        "해제하면 메모리 없이 응답합니다 (A/B 테스트용).",
    )

    # ---- Session controls ----
    st.sidebar.divider()
    if st.sidebar.button("🗑 대화 초기화", width="stretch"):
        st.session_state.messages = []
        # Also delete the persisted history file so it doesn't get reloaded
        # on the next rerun.
        from core.auth import current_user_data_dir
        history_path = current_user_data_dir() / "chat_history.json"
        if history_path.exists():
            history_path.unlink()
        st.rerun()

    # ---- Layout: chat history height ----
    st.session_state["chat_history_height"] = st.sidebar.slider(
        "채팅 영역 높이 (px)",
        min_value=240,
        max_value=800,
        value=st.session_state.get("chat_history_height", 420),
        step=20,
        help="모니터에 맞춰 채팅 메시지 영역의 높이를 조절합니다. 입력창이 화면 아래로 잘리면 줄이세요.",
    )

    # ---- Latency stats ----
    _render_latency_stats()

    return _to_model_config(chat_selected), _to_model_config(extraction_selected)


def _render_api_key_input(provider: str) -> None:
    env_var = ENV_KEY_NAMES[provider]
    env_key_present = bool(get_api_key(provider, None))

    with st.sidebar.expander(f"🔑 {provider} API 키", expanded=not env_key_present):
        if env_key_present:
            st.success(f"{env_var}가 .env에서 로드됨")
        else:
            st.warning(f"{env_var}가 .env에 없습니다. 아래에 입력하거나 .env에 추가하세요.")

        manual_key = st.text_input(
            "수동 입력",
            type="password",
            value=st.session_state.get(f"api_key_{provider}", ""),
            key=f"api_key_input_{provider}",
            help="입력값은 이 세션 동안만 메모리에 보관됩니다.",
        )
        st.session_state[f"api_key_{provider}"] = manual_key


def _render_latency_stats() -> None:
    st.sidebar.divider()
    st.sidebar.subheader("⏱ 응답속도")

    messages = st.session_state.get("messages", [])
    latencies = [m["latency_s"] for m in messages if m.get("role") == "assistant" and "latency_s" in m]

    if not latencies:
        st.sidebar.caption("아직 응답 기록이 없습니다.")
        return

    s = summarize(latencies)
    col1, col2 = st.sidebar.columns(2)
    col1.metric("마지막", f"{s['last']:.2f}s")
    col2.metric("평균", f"{s['avg']:.2f}s")
    col3, col4 = st.sidebar.columns(2)
    col3.metric("최소", f"{s['min']:.2f}s")
    col4.metric("최대", f"{s['max']:.2f}s")
    st.sidebar.caption(f"총 응답 수: {s['count']}")

    per_model = per_model_summary(messages)
    if len(per_model) > 1:
        with st.sidebar.expander("모델별 통계", expanded=False):
            for label, stats in per_model.items():
                st.write(
                    f"**{label}** · n={stats['count']} · "
                    f"avg {stats['avg']:.2f}s · "
                    f"min {stats['min']:.2f}s · "
                    f"max {stats['max']:.2f}s"
                )

    if len(latencies) >= 2:
        st.sidebar.line_chart(latencies[-20:], height=120)


def current_api_key(provider: str) -> str | None:
    return st.session_state.get(f"api_key_{provider}") or None
