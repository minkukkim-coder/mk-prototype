"""Counseling chatbot prototype — entry point."""

from __future__ import annotations

from pathlib import Path

import streamlit as st
import yaml
from dotenv import load_dotenv

from core.auth import render_user_chip, require_login
from ui.chat import render_chat
from ui.memory_panel import render_memory_panel
from ui.prompt_editor import get_active_chat_prompt, get_active_chat_prompt_name, render_chat_prompt_editor
from ui.sidebar import current_api_key, render_sidebar

ROOT = Path(__file__).parent
MODELS_YAML = ROOT / "config" / "models.yaml"

load_dotenv(ROOT / ".env")


def load_models_config() -> dict:
    with MODELS_YAML.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> None:
    st.set_page_config(page_title="상담 챗봇 프로토타입", page_icon="💬", layout="wide")

    # Block unauthenticated / unauthorized users before rendering anything else.
    require_login()

    st.title("💬 상담 챗봇 프로토타입")

    cfg = load_models_config()
    chat_model, extraction_model = render_sidebar(cfg["models"], cfg["default"])
    render_user_chip()

    system_prompt = get_active_chat_prompt()
    active_name = get_active_chat_prompt_name()
    st.caption(
        f"대화 모델: **{chat_model.label}**  ·  "
        f"추출 모델: **{extraction_model.label}**  ·  "
        f"대화 프롬프트: **{active_name}**"
    )

    tab_chat, tab_prompts, tab_memory = st.tabs(
        ["💬 채팅", "📝 대화 프롬프트", "🧠 메모리"]
    )

    with tab_chat:
        render_chat(chat_model, system_prompt)
    with tab_prompts:
        render_chat_prompt_editor()
    with tab_memory:
        render_memory_panel(
            extraction_model=extraction_model,
            api_key=current_api_key(extraction_model.provider),
        )


if __name__ == "__main__":
    main()
