"""Read-only AI comparisons with immutable drafts and exact model IDs."""

import http.client
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from ai_client import (AI_REQUEST_TIMEOUT_SECONDS, build_messages, clean_question, clean_text,
                       http_error_detail, redact_secret, send_messages)
from configuration import normalize
from memory import context_for, load_memory
from paths import resource_path
from profiles import REWARD_BLOCKED_REFUSAL, prompt_for, reward_is_blocked, validate_profiles
from privacy import check_question
from viewer_recognition import related_context
from reply_rules import ANSWER_MAX_CHARS, QUESTION_MAX_CHARS, upgrade_generated_prompt


@dataclass(frozen=True)
class Credentials:
    base_url: str
    api_key: str = field(repr=False)

    def config(self):
        return {"AI_CHAT_URL": self.base_url + "/chat/completions", "AI_API_KEY": self.api_key}


def credentials(base_url: str, entered_key: str, saved_key: str = "") -> Credentials:
    return Credentials(normalize("AI_BASE_URL", base_url),
                       normalize("AI_API_KEY", entered_key.strip() or saved_key))


@dataclass(frozen=True)
class Model:
    id: str
    available: bool | None = None


def fetch_models(auth: Credentials) -> tuple[Model, ...]:
    """OpenAI-compatible GET /models, authenticated with the current key.

    Do not infer key access from a public website or a configured fallback list.
    Standard responses only carry IDs; optional access flags are respected.
    """
    request = urllib.request.Request(auth.base_url + "/models", headers={
        "Authorization": "Bearer " + auth.api_key, "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=AI_REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Каталог: HTTP {exc.code}{http_error_detail(exc, auth.api_key)}") from None
    except (OSError, http.client.HTTPException):
        raise RuntimeError("Каталог недоступен: ошибка сети или таймаут (20 с).") from None
    except ValueError:
        raise RuntimeError("Каталог вернул некорректный JSON.") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("Каталог: ожидается JSON со списком data и ID моделей.")
    models = {}
    for row in payload["data"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError("Каталог содержит модель без строкового ID.")
        model_id = normalize("AI_MODEL", row["id"])
        if model_id != row["id"] or auth.api_key in model_id:
            raise ValueError("Каталог содержит некорректный ID модели.")
        flags = [row.get(name) for name in ("available", "accessible", "enabled", "is_available", "allowed")]
        available = False if any(flag is False for flag in flags) else (
            True if any(flag is True for flag in flags) else None)
        if row.get("disabled") is True or row.get("status") in ("unavailable", "disabled", "inaccessible"):
            available = False
        permissions = row.get("permission")
        if isinstance(permissions, list) and permissions and all(
                isinstance(p, dict) and p.get("allow_view") is False for p in permissions):
            available = False
        models[model_id] = Model(model_id, available)
    return tuple(sorted(models.values(), key=lambda model: model.id.casefold()))


def read_test_memory(root: Path) -> dict:
    # Same template as the bot, but testing must never create or update memory.
    path = root / "memory.json"
    if not path.exists():
        path = root / "memory.example.json"
        if not path.exists():
            path = resource_path("memory.example.json")
    return load_memory(path)


@dataclass(frozen=True)
class TestSnapshot:
    auth: Credentials = field(repr=False)
    models: tuple[str, ...]
    messages: tuple[tuple[str, str], ...] = field(repr=False)
    context: str


def make_snapshot(auth: Credentials, models: list[str], question: str, prompt: str,
                  sender: str, login: str = "", profile: dict | None = None,
                  memory_data: dict | None = None, profiles: list[dict] | None = None) -> TestSnapshot:
    model_ids = tuple(dict.fromkeys(normalize("AI_MODEL", model) for model in models))
    if not model_ids:
        raise ValueError("Выберите модель или добавьте её ID вручную.")
    if any(auth.api_key in model for model in model_ids):
        raise ValueError("В поле ID модели указан API-ключ.")
    check_question(question)
    question = clean_question(question)
    if not question:
        raise ValueError("Введите пробный вопрос.")
    prompt = upgrade_generated_prompt(prompt.strip())
    if len(prompt) > 20000:
        raise ValueError("Общий промпт должен содержать не более 20 000 символов.")
    personal, user_id, sender_profile = "", "", None
    rows = validate_profiles(profiles or [], allow_empty_prompt=True)
    if sender == "profile":
        if profile is None:
            raise ValueError("Выберите зрителя из вкладки «Зрители».")
        # A draft can have an empty instruction; testing then applies none.
        # Saving profiles continues to use the strict validation by default.
        selected = validate_profiles([profile], allow_empty_prompt=True)[0]
        login, user_id = selected["login"], selected["user_id"]
        index = next((i for i, row in enumerate(rows) if
                      (user_id and row["user_id"] == user_id) or
                      (not user_id and not row["user_id"] and row["login"] == login)), None)
        if index is None:
            index = len(rows)
            rows.append(selected)
        else:
            rows[index] = selected
        rows = validate_profiles(rows, allow_empty_prompt=True)
        sender_profile = rows[index]
        personal = prompt_for(rows, login, user_id)
    elif sender == "viewer":
        login = normalize("TWITCH_CHANNEL", login) if login.strip() else "test_viewer"
    elif sender == "owner":
        # This is an explicit role, including when no channel/login is configured.
        login = normalize("TWITCH_CHANNEL", login) if login.strip() else ""
    else:
        raise ValueError("Неизвестный отправитель теста.")
    if sender != "owner" and reward_is_blocked(rows, login, user_id):
        raise ValueError(REWARD_BLOCKED_REFUSAL)
    related = related_context(rows, question, memory_data, sender_profile=sender_profile)
    messages = build_messages({"AI_PROMPT": prompt}, login, question, memory_data,
                              user_id, personal_prompt=personal,
                              sender_role="owner" if sender == "owner" else "viewer",
                              viewer_context=related.prompt)
    # Derive the displayed context from exactly the messages sent to every model.
    note_text = context_for(memory_data, login, user_id) if memory_data is not None else ""
    channel_notes, viewer_notes = "не применены", "не применены"
    if note_text:
        content = json.loads(note_text.split(": ", 1)[1])
        def describe(value):
            return clean_text(redact_secret(json.dumps(value, ensure_ascii=False), auth.api_key), 350)
        if any(content["Стример и канал"].values()):
            channel_notes = describe(content["Стример и канал"])
        if "зритель" in content:
            viewer_notes = describe(content["зритель"])
    context = "\n".join((
        "Общий промпт: " + (clean_text(redact_secret(prompt, auth.api_key), 220) if prompt else "пустой"),
        "Личная инструкция: " + (clean_text(redact_secret(personal, auth.api_key), 220) if personal else "не применяется"),
        "Заметки о канале: " + channel_notes,
        "Заметки о зрителе: " + viewer_notes,
        "Распознанные зрители и применённые профили:\n" + ("\n".join(
            clean_text(redact_secret(line, auth.api_key), 600) for line in related.diagnostics)
            if related.diagnostics else "Упоминаний зрителей с профилями не найдено."),
        "История реальных разговоров недоступна и не учитывалась.",
        "Защита личных данных: обязательна для всех моделей.",
        f"Вопрос ограничен {QUESTION_MAX_CHARS} символами, ответ — {ANSWER_MAX_CHARS}, как у бота.",
    ))
    return TestSnapshot(auth, model_ids, tuple((m["role"], m["content"]) for m in messages),
                        redact_secret(context, auth.api_key))


@dataclass(frozen=True)
class Result:
    model: str
    seconds: float
    answer: str = ""
    error: str = ""


def test_model(snapshot: TestSnapshot, model: str) -> Result:
    started = time.monotonic()
    try:
        answer = send_messages(snapshot.auth.config(), model,
                               [{"role": role, "content": content} for role, content in snapshot.messages])
        return Result(model, time.monotonic() - started,
                      answer=redact_secret(answer, snapshot.auth.api_key))
    except Exception as exc:
        error = redact_secret(str(exc), snapshot.auth.api_key)
        return Result(model, time.monotonic() - started, error=clean_text(error, 500) or "Ошибка AI API")
