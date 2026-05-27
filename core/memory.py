"""Memory framework: extract counseling memory from conversation logs.

Two extraction modes:
  - "consolidated" (Method A): one LLM call covering all 29 items
  - "per_item" (Method B): one LLM call per item, optionally in parallel

State is persisted to `memory_state.json` at the project root, keyed by item ID.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml

from core.llm import LLMError, Message, ModelConfig, chat

ROOT = Path(__file__).parent.parent
SCHEMA_PATH = ROOT / "config" / "memory_items.yaml"
PROMPTS_DIR = ROOT / "config" / "prompts" / "memory_items"


def state_path() -> Path:
    """Path to the memory state file for the currently logged-in user.

    Per-user storage prevents one team member's extraction from overwriting
    another's. The file is created on demand by save_state().
    """
    # Local import to avoid a circular dependency at module load time.
    from core.auth import current_user_data_dir
    return current_user_data_dir() / "memory_state.json"

Mode = Literal["consolidated", "per_item"]


# ---------- Schema ----------

@dataclass
class MemoryItem:
    id: str
    title: str
    category: str
    output_fields: list[str]

    def _default_path(self) -> Path:
        """Shipped default prompt — same for everyone."""
        return PROMPTS_DIR / f"{self.id}.md"

    def _user_path(self) -> Path:
        """Current user's customized copy (created on save)."""
        # Local import avoids a circular dependency at module load time.
        from core.auth import current_user_data_dir
        d = current_user_data_dir() / "prompts" / "memory_items"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{self.id}.md"

    def prompt_path(self) -> Path:
        """Path of the prompt that's currently active for this user."""
        up = self._user_path()
        return up if up.exists() else self._default_path()

    def load_prompt(self) -> str:
        """User's copy wins; fall back to shipped default."""
        up = self._user_path()
        if up.exists():
            return up.read_text(encoding="utf-8")
        return self._default_path().read_text(encoding="utf-8")

    def save_prompt(self, content: str) -> None:
        """Always saves to the user's directory."""
        self._user_path().write_text(content, encoding="utf-8")

    def is_customized(self) -> bool:
        return self._user_path().exists()

    def reset_to_default(self) -> None:
        """Delete the user's copy so the shipped default takes over again."""
        up = self._user_path()
        if up.exists():
            up.unlink()


def load_schema() -> list[MemoryItem]:
    with SCHEMA_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return [MemoryItem(**entry) for entry in data["items"]]


# ---------- State persistence ----------

def load_state() -> dict[str, dict]:
    path = state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(state: dict[str, dict]) -> None:
    state_path().write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def clear_state() -> None:
    path = state_path()
    if path.exists():
        path.unlink()


# ---------- Inject memory into a chat system prompt ----------

_MEMORY_INJECTION_FOOTER = """

---
위 [내담자에 대해 지금까지 파악된 정보]는 이전 상담 세션들에서 누적된 메모리입니다. 응답 시 \
자연스럽게 활용하되, 다음 원칙을 지키십시오:

- 내담자가 이번 세션에서 직접 말하지 않은 내용을 "전에 ~라고 하셨죠?"처럼 단정적으로 언급하지 마십시오.
- 메모리 항목 중 [추측] 표시가 있는 정보는 사실로 단정하지 마십시오.
- 위 정보가 현재 발화와 모순될 경우, 최신 발화를 우선합니다.
- 메모리에 [Crisis · 위기 관리] 항목이 있다면 안전을 최우선으로 응대하십시오.
"""


def format_memory_for_prompt(state: dict[str, dict] | None = None) -> str:
    """Render memory state as a Markdown block suitable for system-prompt injection.

    Returns empty string if there's no usable memory yet, so callers can
    safely concatenate without producing dangling headers.
    """
    if state is None:
        state = load_state()
    if not state:
        return ""

    items = load_schema()
    by_category: dict[str, list[tuple[MemoryItem, dict]]] = {}

    for item in items:
        entry = state.get(item.id)
        if not entry:
            continue
        # Skip pure NO_UPDATE entries with no actual content.
        meaningful = [
            (k, v) for k, v in entry.items()
            if not k.startswith("_") and k != "Status" and v and v not in ("None", "null")
        ]
        if not meaningful:
            continue
        by_category.setdefault(item.category, []).append((item, entry))

    if not by_category:
        return ""

    lines: list[str] = ["## 내담자에 대해 지금까지 파악된 정보 (누적 메모리)\n"]
    for category, pairs in by_category.items():
        lines.append(f"### {category}")
        for item, entry in pairs:
            lines.append(f"**{item.title}**")
            for key, value in entry.items():
                if key.startswith("_") or key == "Status":
                    continue
                if not value or value in ("None", "null"):
                    continue
                lines.append(f"- {key}: {value}")
            lines.append("")

    return "\n".join(lines) + _MEMORY_INJECTION_FOOTER


def count_filled_memory_items(state: dict[str, dict] | None = None) -> int:
    """How many items have at least one non-empty field besides Status."""
    if state is None:
        state = load_state()
    count = 0
    for entry in state.values():
        for k, v in entry.items():
            if k.startswith("_") or k == "Status":
                continue
            if v and v not in ("None", "null"):
                count += 1
                break
    return count


# ---------- Helpers ----------

def format_session_log(messages: list[dict]) -> str:
    """Turn the chat history into a single string for the extractor.

    Only role/content are kept; latency etc. is irrelevant to extraction.
    """
    lines: list[str] = []
    for m in messages:
        role = "내담자" if m["role"] == "user" else "상담사"
        lines.append(f"[{role}]: {m['content']}")
    return "\n".join(lines)


def _previous_assessment_str(state: dict[str, dict], item_id: str) -> str:
    prev = state.get(item_id)
    if not prev:
        return "(이전 기록 없음 — 첫 세션)"
    # Strip internal metadata when feeding back to the LLM.
    visible = {k: v for k, v in prev.items() if not k.startswith("_")}
    return json.dumps(visible, ensure_ascii=False, indent=2)


_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def _extract_json(text: str) -> dict | list | None:
    """Best-effort JSON extraction from model output (handles fenced blocks)."""
    text = text.strip()
    # 1) raw JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 2) fenced JSON
    m = _JSON_FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 3) first {...} or [...] greedy slice
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


# ---------- Method B: per-item extraction ----------

def _extract_one_item(
    item: MemoryItem,
    state: dict[str, dict],
    session_log: str,
    model_config: ModelConfig,
    api_key: str | None,
) -> tuple[str, dict | None, str | None]:
    """Returns (item_id, parsed_json_or_None, error_message_or_None)."""
    prompt_template = item.load_prompt()
    prev = _previous_assessment_str(state, item.id)
    system_prompt = prompt_template.replace("{PREVIOUS_ASSESSMENT}", prev)
    user_msg = f"# Current Session Log\n\n{session_log}"

    try:
        raw = chat(
            model_config=model_config,
            system_prompt=system_prompt,
            messages=[Message(role="user", content=user_msg)],
            api_key=api_key,
        )
    except LLMError as e:
        return item.id, None, str(e)

    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        return item.id, None, f"JSON 파싱 실패: {raw[:200]}…"

    return item.id, parsed, None


def extract_per_item(
    messages: list[dict],
    model_config: ModelConfig,
    api_key: str | None,
    max_workers: int = 5,
    progress_cb=None,
    debug: dict | None = None,
) -> tuple[dict[str, dict], list[str]]:
    """Run all 29 items in parallel. Returns (updated_state, errors).

    If `debug` is provided, fills in per-item raw responses for inspection.
    """
    items = load_schema()
    state = load_state()
    session_log = format_session_log(messages)

    errors: list[str] = []
    done = 0
    total = len(items)

    if debug is not None:
        debug.setdefault("mode", "per_item")
        debug.setdefault("session_log", session_log)
        debug.setdefault("per_item", {})

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [
            ex.submit(_extract_one_item_with_debug, item, state, session_log, model_config, api_key)
            for item in items
        ]
        for fut in futures:
            item_id, parsed, err, raw_response = fut.result()
            done += 1
            if progress_cb:
                progress_cb(done, total, item_id)
            if debug is not None:
                debug["per_item"][item_id] = {"raw": raw_response, "parsed": parsed, "error": err}
            if err:
                errors.append(f"{item_id}: {err}")
                continue
            if parsed.get("Status") == "Updated":
                parsed["_last_updated"] = datetime.now().isoformat(timespec="seconds")
                state[item_id] = parsed

    save_state(state)
    return state, errors


def _extract_one_item_with_debug(
    item: MemoryItem,
    state: dict[str, dict],
    session_log: str,
    model_config: ModelConfig,
    api_key: str | None,
) -> tuple[str, dict | None, str | None, str | None]:
    """Same as _extract_one_item but also returns the raw response for debug."""
    prompt_template = item.load_prompt()
    prev = _previous_assessment_str(state, item.id)
    system_prompt = prompt_template.replace("{PREVIOUS_ASSESSMENT}", prev)
    user_msg = f"# Current Session Log\n\n{session_log}"

    try:
        raw = chat(
            model_config=model_config,
            system_prompt=system_prompt,
            messages=[Message(role="user", content=user_msg)],
            api_key=api_key,
        )
    except LLMError as e:
        return item.id, None, str(e), None

    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        return item.id, None, f"JSON 파싱 실패: {raw[:200]}…", raw

    return item.id, parsed, None, raw


# ---------- Method A: consolidated extraction ----------

_CONSOLIDATED_HEADER = """당신은 상담 메모리 추출 시스템입니다. 아래 정의된 29개 항목 각각에 대해 독립적으로 추출 작업을 수행하고, 모든 결과를 하나의 JSON 객체로 반환하십시오.

# ⚠️ 매우 중요한 핵심 원칙 (이걸 어기면 결과가 무용지물입니다)

## 1. 발화 주체 식별
"내담자"는 상담을 받는 본인입니다. 내담자가 자기 자신에 대해 말한 내용과, 내담자가 제3자(아내, 남편, 부모, 자녀, 친구, 동료 등)에 대해 말한 내용을 엄격히 구분하십시오.

내담자가 "제 아내가 ~~" 라고 말했다면 → 그 정보는 **아내**의 정보이지 내담자 본인의 정보가 아닙니다. "제 아들이 ~~" 도 마찬가지로 자녀의 정보입니다.

## 2. 항목별 주체와 Scope 매핑

각 항목은 정해진 주체(subject)만 다룹니다:

- 항목 01 직업, 02 경제, 17 자아상, 11~20 심리/신체 항목 → **모두 내담자 본인**
- 항목 03 부모 → 내담자의 부모와의 관계만
- 항목 04 형제 → 내담자의 형제·방계 가족만
- 항목 05 배우자/연인 → 내담자의 배우자/연인만 (배우자의 직업/심리 등도 여기에)
- 항목 06 친구 → 내담자의 친구만 (가족/직장 제외)
- 항목 07 자녀/양육 → 내담자의 자녀 양육 관련만
- 항목 08 직장 동료 → 내담자의 직장 인간관계만

## 3. 분류 예시

✅ 올바른 분류:
- 내담자 "제 아내가 회사에서 스트레스 받아요" → 항목 05(배우자) RomanticSnapshot에 "배우자가 직장 스트레스 호소" 기록. 항목 01·11·20 모두 NO_UPDATE.
- 내담자 "아들이 초등학교 2학년인데 게임만 해요" → 항목 07(자녀) ParentingSnapshot에 "초2 아들, 게임 과몰입 양육 고민" 기록. 항목 12(보상시스템)는 NO_UPDATE.

❌ 절대 하지 말아야 할 잘못된 분류:
- 위 "아내가 스트레스" → ❌ 항목 01(직업)이나 항목 11(감정조절)에 내담자 본인 정보처럼 기록
- 위 "아들이 게임만" → ❌ 항목 12(보상)에 내담자 본인의 중독으로 기록

## 4. 변경 없음 표기
세션 로그에 해당 항목과 관련된 새로운 정보가 없으면 반드시 "Status": "NO_UPDATE"로 표기하고 나머지 필드는 빈 문자열로 두십시오. 억지로 추측해서 채우지 마십시오.

---

# 출력 형식

다음 형식의 단일 JSON 객체만 반환하십시오. 마크다운 코드 블록(```) 없이 순수 JSON만:

{
  "01_occupation": { "Status": "Updated 또는 NO_UPDATE", ... },
  "02_economic":   { "Status": "Updated 또는 NO_UPDATE", ... },
  ...
  "29_future":     { "Status": "Updated 또는 NO_UPDATE", ... }
}

각 항목의 정확한 필드 스키마는 아래 [항목 N] 섹션의 Output Format을 따르십시오.

---

# 각 항목의 역할 정의 및 이전 평가 기록

"""

_CONSOLIDATED_FOOTER = """

---

# Current Session Log (상담 대화 전체)

아래는 추출 대상이 되는 실제 대화입니다. "내담자"의 발화에서 본인 정보와 제3자(가족/친구 등) 정보를 구분해서 위 29개 항목에 정확히 분류하십시오.

{SESSION_LOG}

---

위 Session Log를 기반으로, 위에 정의된 29개 항목 각각에 대해 추출을 수행하고 하나의 JSON 객체로 반환하십시오. ⚠️ 발화 주체를 혼동하지 마십시오. 아내·자녀·부모 등 제3자에 대한 정보를 내담자 본인 정보로 잘못 분류하지 마십시오.
"""


def _build_consolidated_prompt(items: list[MemoryItem], state: dict[str, dict]) -> str:
    sections: list[str] = []
    for item in items:
        prev = _previous_assessment_str(state, item.id)
        body = item.load_prompt().replace("{PREVIOUS_ASSESSMENT}", prev)
        sections.append(f"[항목 {item.id}] {item.title}\n\n{body}")
    return _CONSOLIDATED_HEADER + "\n\n---\n\n".join(sections) + _CONSOLIDATED_FOOTER


def extract_consolidated(
    messages: list[dict],
    model_config: ModelConfig,
    api_key: str | None,
    debug: dict | None = None,
) -> tuple[dict[str, dict], list[str]]:
    """Run one LLM call with all 29 items. Returns (updated_state, errors)."""
    items = load_schema()
    state = load_state()
    session_log = format_session_log(messages)

    system_prompt = _build_consolidated_prompt(items, state).replace(
        "{SESSION_LOG}", session_log
    )

    if debug is not None:
        debug["mode"] = "consolidated"
        debug["system_prompt"] = system_prompt
        debug["session_log"] = session_log

    try:
        raw = chat(
            model_config=model_config,
            system_prompt=system_prompt,
            messages=[Message(role="user", content="위 지침에 따라 JSON을 출력하세요.")],
            api_key=api_key,
        )
    except LLMError as e:
        if debug is not None:
            debug["error"] = str(e)
        return state, [str(e)]

    if debug is not None:
        debug["raw_response"] = raw

    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        return state, [f"JSON 파싱 실패. 모델 응답 일부: {raw[:300]}…"]

    if debug is not None:
        debug["parsed_response"] = parsed

    errors: list[str] = []
    now = datetime.now().isoformat(timespec="seconds")
    for item in items:
        entry = parsed.get(item.id)
        if not isinstance(entry, dict):
            errors.append(f"{item.id}: 응답에 없음")
            continue
        if entry.get("Status") == "Updated":
            entry["_last_updated"] = now
            state[item.id] = entry

    save_state(state)
    return state, errors


# ---------- Public extraction entry point ----------

def extract_memory(
    mode: Mode,
    messages: list[dict],
    model_config: ModelConfig,
    api_key: str | None,
    progress_cb=None,
    debug: dict | None = None,
) -> tuple[dict[str, dict], list[str]]:
    if mode == "consolidated":
        return extract_consolidated(messages, model_config, api_key, debug=debug)
    return extract_per_item(messages, model_config, api_key, progress_cb=progress_cb, debug=debug)
