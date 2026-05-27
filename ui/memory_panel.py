"""Memory panel: view memory table, edit per-item prompts, run extraction."""

from __future__ import annotations

import json
from datetime import datetime

import streamlit as st

from core.llm import ModelConfig
from core.memory import (
    MemoryItem,
    clear_state,
    extract_memory,
    load_schema,
    load_state,
)
from core.metrics import measure_seconds


def render_memory_panel(extraction_model: ModelConfig, api_key: str | None) -> None:
    items = load_schema()
    tab_view, tab_edit, tab_run = st.tabs(["📋 메모리 테이블", "✏️ 항목 프롬프트 편집", "🔄 추출 실행"])

    with tab_view:
        _render_table(items)
    with tab_edit:
        _render_item_editor(items)
    with tab_run:
        _render_extraction(items, extraction_model, api_key)


# ---------- View ----------

def _render_table(items: list[MemoryItem]) -> None:
    state = load_state()
    if not state:
        st.info("아직 추출된 메모리가 없습니다. '🔄 추출 실행' 탭에서 추출을 실행해보세요.")
        return

    by_category: dict[str, list[MemoryItem]] = {}
    for item in items:
        by_category.setdefault(item.category, []).append(item)

    col_top1, col_top2 = st.columns([3, 1])
    with col_top1:
        st.caption(f"전체 {len(items)}개 항목 중 {len(state)}개 채워짐")
    with col_top2:
        st.download_button(
            "💾 JSON 다운로드",
            data=json.dumps(state, ensure_ascii=False, indent=2),
            file_name="memory_state.json",
            mime="application/json",
            width="stretch",
        )

    for category, cat_items in by_category.items():
        with st.expander(f"📂 {category}  ({sum(1 for i in cat_items if i.id in state)}/{len(cat_items)})", expanded=True):
            for item in cat_items:
                entry = state.get(item.id)
                if not entry:
                    st.markdown(f"**{item.title}** — _아직 비어 있음_")
                    continue
                _render_entry_card(item, entry)


def _render_entry_card(item: MemoryItem, entry: dict) -> None:
    last_updated = entry.get("_last_updated", "?")
    st.markdown(f"**{item.title}**  ·  _{last_updated}_")
    visible = {k: v for k, v in entry.items() if not k.startswith("_") and k != "Status"}
    for key, value in visible.items():
        if value in (None, "None", "", "null"):
            continue
        st.markdown(f"- **{key}**: {value}")
    st.divider()


# ---------- Item prompt editor ----------

def _render_item_editor(items: list[MemoryItem]) -> None:
    st.caption(
        "각 항목의 추출 프롬프트를 개별 편집할 수 있습니다. "
        "편집한 내용은 **본인 계정에만** 저장되며 다른 팀원에게는 영향을 주지 않습니다."
    )

    labels = [
        f"{'✨ ' if item.is_customized() else ''}{item.id}  ·  {item.title}"
        for item in items
    ]
    idx = st.selectbox(
        "편집할 항목 (✨ = 본인 커스텀 버전 있음)",
        options=range(len(items)),
        format_func=lambda i: labels[i],
        key="memory_edit_selected_idx",
    )
    item = items[idx]

    if item.is_customized():
        st.info(f"✨ 현재 **본인 커스텀 버전**을 보고 있습니다. 기본값으로 되돌리려면 아래 '기본값으로 복원' 버튼을 누르세요.")
    else:
        st.caption("📦 기본값(모든 사용자 공통)을 보고 있습니다. 편집하고 저장하면 본인 전용 커스텀 버전이 생성됩니다.")

    current = item.load_prompt()
    edited = st.text_area(
        "프롬프트",
        value=current,
        height=500,
        key=f"memory_edit_text_{item.id}",
    )

    col_save, col_reset, col_reload = st.columns([1, 1, 1])
    with col_save:
        if st.button("💾 내 버전으로 저장", key=f"memory_save_{item.id}", width="stretch", type="primary"):
            item.save_prompt(edited)
            st.success(f"{item.title} — 본인 계정에 저장됨")
            st.rerun()
    with col_reset:
        disabled = not item.is_customized()
        if st.button(
            "↩️ 기본값으로 복원",
            key=f"memory_reset_default_{item.id}",
            width="stretch",
            disabled=disabled,
            help="본인 커스텀 버전을 삭제하고 기본값으로 되돌립니다." if not disabled else "이미 기본값을 보고 있습니다.",
        ):
            item.reset_to_default()
            st.success(f"{item.title} — 기본값으로 복원됨")
            st.rerun()
    with col_reload:
        if st.button("🔄 다시 불러오기", key=f"memory_reload_{item.id}", width="stretch"):
            st.rerun()


# ---------- Extraction runner ----------

def _render_extraction(items: list[MemoryItem], model_config: ModelConfig, api_key: str | None) -> None:
    if not st.session_state.get("messages"):
        st.info("아직 추출할 대화 내용이 없습니다. 먼저 '💬 채팅' 탭에서 대화를 진행하세요.")
        return

    msg_count = len(st.session_state.messages)
    st.caption(f"현재 대화 메시지 수: **{msg_count}** · 추출 모델: **{model_config.label}**")

    # Show result of the previous extraction (persisted via session state).
    # st.tabs() doesn't trigger reruns on tab switch, so we always rerun after
    # extraction and surface the summary here on the next render.
    last = st.session_state.get("_last_extraction_summary")
    if last:
        st.success(last["msg"])
        if last.get("errors"):
            with st.expander(f"⚠️ {len(last['errors'])}개 항목 실패 / 경고", expanded=False):
                for e in last["errors"]:
                    st.write(f"- {e}")
        st.caption("📋 메모리 테이블 탭에서 갱신된 내용을 확인하세요.")

    mode_label = st.radio(
        "추출 방식",
        options=[
            "항목별 29회 병렬 호출 (방식 B · 정확도 권장)",
            "통합 1회 호출 (방식 A · 빠르고 저렴하지만 항목 혼동 위험)",
        ],
        index=0,
        horizontal=False,
        key="memory_mode_radio",
        help="원본 PDF 프롬프트는 항목별 개별 호출용으로 설계되어 있습니다. "
        "방식 A는 29개 역할을 동시에 수행해야 해서 발화 주체를 혼동하기 쉬워요 "
        "(예: '제 아내가...'를 내담자 본인 정보로 잘못 분류). "
        "정확도가 중요하면 B, 빠른 반복 테스트엔 A를 쓰세요.",
    )
    mode = "per_item" if "항목별" in mode_label else "consolidated"
    debug_enabled = st.checkbox(
        "🔍 디버그 모드 (모델의 원시 응답 표시 — 분류 오류 진단용)",
        value=False,
        key="memory_debug_checkbox",
    )

    col_run, col_clear = st.columns([3, 1])
    with col_run:
        run_clicked = st.button("🔄 메모리 업데이트 실행", type="primary", width="stretch", key="memory_run_btn")
    with col_clear:
        if st.button("🗑 전체 메모리 초기화", width="stretch", key="memory_clear_btn"):
            clear_state()
            st.session_state.pop("_extraction_debug", None)
            st.session_state.pop("_last_extraction_summary", None)
            st.success("memory_state.json 삭제됨")
            st.rerun()

    # Render last debug if toggle is on (survives reruns)
    if debug_enabled and st.session_state.get("_extraction_debug"):
        _render_debug(st.session_state["_extraction_debug"])

    if not run_clicked:
        return

    status = st.empty()
    progress = st.progress(0.0)

    def cb(done: int, total: int, item_id: str) -> None:
        progress.progress(done / total)
        status.caption(f"⏳ {done}/{total} · 최근 완료: {item_id}")

    debug: dict = {} if debug_enabled else None
    status.caption("⏳ 호출 중…")
    with measure_seconds() as t:
        state, errors = extract_memory(
            mode=mode,
            messages=st.session_state.messages,
            model_config=model_config,
            api_key=api_key,
            progress_cb=cb if mode == "per_item" else None,
            debug=debug,
        )
    progress.progress(1.0)

    updated_count = sum(1 for v in state.values() if v.get("_last_updated"))
    st.session_state["_last_extraction_summary"] = {
        "msg": f"메모리 상태 저장 완료 — {t.seconds:.1f}s · 총 {updated_count}개 항목이 갱신된 상태",
        "errors": errors,
    }
    if debug:
        st.session_state["_extraction_debug"] = debug

    # Force rerun so the "📋 메모리 테이블" subtab reflects the new state.
    # Without this, st.tabs holds the pre-extraction render of the table tab
    # and the user sees stale data until they trigger another rerun.
    st.rerun()


def _render_debug(debug: dict) -> None:
    st.divider()
    st.subheader("🔍 디버그 정보")
    st.caption(f"추출 방식: `{debug.get('mode', '?')}`")

    with st.expander("📜 세션 로그 (모델에 전달된 형태)", expanded=False):
        st.code(debug.get("session_log", "(없음)"), language="text")

    if debug.get("mode") == "consolidated":
        with st.expander("📨 모델 원시 응답", expanded=True):
            st.code(debug.get("raw_response", "(없음)"), language="json")
        if debug.get("system_prompt"):
            with st.expander("📋 전달된 시스템 프롬프트 (전체)", expanded=False):
                st.code(debug["system_prompt"], language="markdown")
    else:
        # per_item — show each item's raw response
        per = debug.get("per_item", {})
        st.caption(f"항목별 응답 ({len(per)}개)")
        # Only show items that returned Updated (often the most interesting)
        updated_items = {
            iid: info for iid, info in per.items()
            if isinstance(info.get("parsed"), dict) and info["parsed"].get("Status") == "Updated"
        }
        if updated_items:
            with st.expander(f"✅ Updated 응답만 보기 ({len(updated_items)}개)", expanded=True):
                for item_id, info in updated_items.items():
                    st.markdown(f"**{item_id}**")
                    st.code(info.get("raw", "(없음)"), language="json")
        with st.expander(f"📨 모든 29개 항목 원시 응답", expanded=False):
            for item_id, info in per.items():
                status_tag = (info.get("parsed") or {}).get("Status", "?")
                err = info.get("error")
                st.markdown(f"**{item_id}** · `Status: {status_tag}`" + (f" · ⚠️ {err}" if err else ""))
                st.code(info.get("raw") or "(응답 없음)", language="json")
