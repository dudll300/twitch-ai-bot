"""Shared chat request construction and transport; no Twitch connection or writes."""

import json
import urllib.error
import urllib.request

from configuration import SYSTEM_PROMPT
from memory import context_for

AI_REQUEST_TIMEOUT_SECONDS = 20


def redact_secret(text: str, api_key: str) -> str:
    return text.replace(api_key, "[ключ скрыт]") if api_key else text


def clean_text(value: str, limit: int) -> str:
    value = " ".join(value.replace("\x00", " ").replace("\r", " ").replace("\n", " ").split())
    return value[:limit].rstrip()


def clean_question(value: str) -> str:
    return " ".join(value.split())[:400].rstrip()


class TemporaryAIError(RuntimeError):
    """A model request may work again or through another model."""


def http_error_detail(exc: urllib.error.HTTPError, api_key: str) -> str:
    try:
        raw = exc.read(4096)
    except (AttributeError, OSError, ValueError):
        return ""
    if not raw:
        return ""
    detail = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(detail)
        if isinstance(payload, dict):
            error = payload.get("error", payload)
            if isinstance(error, dict):
                detail = error.get("message") or error.get("detail") or error.get("code") or ""
            elif isinstance(error, str):
                detail = error
    except ValueError:
        pass
    if not isinstance(detail, str):
        detail = str(detail)
    if api_key:
        detail = detail.replace(api_key, "[ключ скрыт]")
    detail = clean_text(detail, 240)
    return f": {detail}" if detail else ""


def build_messages(cfg: dict[str, str], user: str, question: str,
            memory_data: dict | None = None, user_id: str = "",
            history: tuple[tuple[str, str], ...] = (),
            personal_prompt: str = "", sender_role: str | None = None) -> list[dict]:
    messages = []
    prompt = cfg.get("AI_PROMPT", SYSTEM_PROMPT).strip()
    if prompt:
        messages.append({"role": "system", "content": prompt})
    if personal_prompt:
        messages.append({"role": "system", "content": personal_prompt})
    if sender_role not in (None, "viewer", "owner"):
        raise ValueError("Неизвестная роль отправителя")
    is_streamer = (sender_role == "owner" if sender_role is not None else
                   bool(user) and user.casefold() == cfg.get("TWITCH_CHANNEL", "").casefold())
    if memory_data is not None:
        context = context_for(memory_data, user, user_id)
        if context:
            messages.append({"role": "system", "content": context})
    author = "Владелец канала" if is_streamer else "Зритель"
    for previous_question, previous_answer in history[-10:]:
        messages.append({"role": "user", "content": f"{author} {user} спрашивает: {previous_question}"})
        messages.append({"role": "assistant", "content": previous_answer})
    messages.append({"role": "user", "content": f"{author} {user} спрашивает: {clean_question(question)}"})
    return messages


def call_ai(cfg: dict[str, str], user: str, question: str,
            memory_data: dict | None = None, user_id: str = "",
            model: str | None = None,
            history: tuple[tuple[str, str], ...] = (),
            personal_prompt: str = "", sender_role: str | None = None) -> str:
    messages = build_messages(cfg, user, question, memory_data, user_id,
                              history=history, personal_prompt=personal_prompt,
                              sender_role=sender_role)
    return send_messages(cfg, model or cfg["AI_MODEL"], messages)


def send_messages(cfg: dict[str, str], model: str, messages: list[dict]) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 512,
        "stream": False,
    }
    request = urllib.request.Request(
        cfg["AI_CHAT_URL"],
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + cfg["AI_API_KEY"],
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=AI_REQUEST_TIMEOUT_SECONDS) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        message = f"AI API вернул HTTP {exc.code}{http_error_detail(exc, cfg['AI_API_KEY'])}"
        if exc.code in (400, 408, 429, 500, 502, 503, 504):
            raise TemporaryAIError(message) from None
        raise RuntimeError(message) from None
    except TimeoutError:
        raise TemporaryAIError("Превышено время ожидания AI API (20 с).") from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise TemporaryAIError("Превышено время ожидания AI API (20 с).") from None
        raise TemporaryAIError(f"AI API недоступен: {type(exc).__name__}") from None
    except ValueError:
        raise TemporaryAIError("AI API вернул некорректный JSON") from None
    try:
        answer = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise TemporaryAIError("AI API вернул некорректный ответ") from None
    if not isinstance(answer, str) or not answer.strip():
        raise TemporaryAIError("AI API вернул пустой ответ")
    answer = clean_text(redact_secret(answer, cfg["AI_API_KEY"]), 300)
    if not answer:
        raise TemporaryAIError("AI API вернул пустой ответ")
    return answer


