"""Small Twitch IRC bot for an OpenAI-compatible chat API (Python 3.10+)."""

import asyncio
import json
import os
import ssl
import time
# Kept as a shared module reference for existing network test hooks.
import urllib.request
from collections import deque

from configuration import AI_MODEL, AI_FALLBACK_MODELS, SYSTEM_PROMPT, DEFAULTS, FIELDS, normalize, read_config

from ai_client import (AI_REQUEST_TIMEOUT_SECONDS, TemporaryAIError, call_ai, clean_question,
                       clean_text, is_local_service_output, redact_secret)

from autonomous import Autonomous
from local_context import LocalContextManager, LocalReply, LocalResultError, prepare_context
from reply_rules import CHAT_MAX_CHARS
from memory import ensure_local_memory, load_memory
from message_history import MessageHistory
from profiles import REWARD_BLOCKED_REFUSAL, load_profiles, profile_for, prompt_for, reward_is_blocked
from privacy import PRIVACY_REFUSAL, PrivacyViolation, check_question, safe_history_text, unsafe_question
from viewer_recognition import related_context
from paths import data_dir
from rewards import RewardListener
from twitch_auth import get_access_token


ROOT = data_dir()
AI_PRIMARY_FAILURE_LIMIT = 3
AI_PRIMARY_COOLDOWN_SECONDS = 5 * 60
MAX_QUEUED_QUESTIONS = 10
MAX_HISTORY_VIEWERS = 1000


def config() -> dict[str, str]:
    # Saved form values take precedence over inherited shell variables.
    values = {**DEFAULTS, **os.environ, **read_config(ROOT / ".env")}
    result = {name: normalize(name, values.get(name, "")) for name, _ in FIELDS}
    prompt_path = ROOT / "prompt.txt"
    result["AI_PROMPT"] = prompt_path.read_text(encoding="utf-8-sig").strip() if prompt_path.exists() else SYSTEM_PROMPT
    if len(result["AI_PROMPT"]) > 20000:
        raise ValueError("Промпт должен содержать не более 20000 символов.")
    result["AI_CHAT_URL"] = result["AI_BASE_URL"] + "/chat/completions"
    return result


def irc_command(line: str) -> str:
    parts = line.split()
    if parts and parts[0].startswith("@"):
        parts.pop(0)
    if parts and parts[0].startswith(":"):
        parts.pop(0)
    return parts[0] if parts else ""


class AIModelRouter:
    def __init__(self, profiles: list[dict] | None = None, local_context=None):
        self.profiles = profiles or []
        self.local_context = local_context
        self.primary_failures = 0
        self.primary_disabled_until = 0.0
        self.last_model = ""

    def ask(self, cfg: dict[str, str], user: str, question: str,
            memory_data: dict | None = None, user_id: str = "",
            history: tuple[tuple[str, str], ...] = (), *, allow_creative=True, on_answer=None) -> str:
        check_question(question)
        self.last_model = ""
        bundle = None
        if self.local_context is not None:
            text = "\n".join(part for pair in history[-10:] for part in pair) + "\n" + clean_question(question)
            bundle = prepare_context(self.local_context, text, creative=allow_creative)
        try:
            answer = self._ask_models(cfg, user, question, memory_data, user_id, history, bundle,
                                      reject_local_service=not allow_creative)
            if not allow_creative and is_local_service_output(answer):
                raise LocalResultError("Некорректный обычный ответ после отмены локальной отсылки.")
            if on_answer is not None:
                on_answer(answer, self.last_model)
            return answer
        except LocalResultError:
            if not allow_creative or bundle is None or not bundle.has_context:
                raise
            print("Локальный контекст: некорректный результат; повторяю ответ без творческой отсылки.", flush=True)
            return self.ask(cfg, user, question, memory_data, user_id, history,
                            allow_creative=False, on_answer=on_answer)

    def _ask_models(self, cfg, user, question, memory_data, user_id, history, local_bundle,
                    *, reject_local_service=False):
        primary = cfg["AI_MODEL"]
        backups = tuple(dict.fromkeys(name.strip() for name in cfg.get("AI_FALLBACK_MODELS", "").split(",")
                                      if name.strip() and name.strip() != primary))
        models = backups
        if not models or time.monotonic() >= self.primary_disabled_until:
            models = (primary,) + models
        personal_prompt = prompt_for(self.profiles, user, user_id)
        context = related_context(self.profiles, clean_question(question), memory_data,
                                  sender_profile=profile_for(self.profiles, user, user_id))
        last_error = None
        for model in models:
            try:
                kwargs = {"model": model, "history": history}
                if personal_prompt:
                    kwargs["personal_prompt"] = personal_prompt
                if context.prompt:
                    kwargs["viewer_context"] = context.prompt
                if local_bundle is not None and local_bundle.has_context:
                    kwargs["local_bundle"] = local_bundle
                if reject_local_service:
                    kwargs["reject_local_service"] = True
                answer = call_ai(cfg, user, question, memory_data, user_id, **kwargs)
            except TemporaryAIError as exc:
                last_error = exc
                if model == primary:
                    self.primary_failures += 1
                    if self.primary_failures >= AI_PRIMARY_FAILURE_LIMIT and backups:
                        self.primary_disabled_until = time.monotonic() + AI_PRIMARY_COOLDOWN_SECONDS
                        print(f"Основная модель {primary} отключена на 5 минут: {exc}", flush=True)
                print(f"Модель {model} недоступна: {exc}", flush=True)
                continue
            except Exception:
                if model == primary:
                    self.primary_failures = 0
                raise
            if model == primary:
                self.primary_failures = 0
                self.primary_disabled_until = 0.0
            self.last_model = model
            return answer
        if last_error is not None:
            raise last_error
        raise RuntimeError("Нет доступных AI-моделей")


class Bot:
    def __init__(self, cfg: dict[str, str]):
        self.cfg = cfg
        self.memory = load_memory(ensure_local_memory(ROOT / "memory.json"))
        self.last_sent = 0.0
        self._say_lock = asyncio.Lock()
        self.local_context = LocalContextManager(ROOT, emit=self.local_event)
        self.ai_router = AIModelRouter(load_profiles(ROOT / "profiles.json"), self.local_context)
        self.histories: dict[str, deque[tuple[str, str]]] = {}
        self.history = MessageHistory(ROOT, secrets=(cfg.get("AI_API_KEY", ""),))
        self._paid_busy = False
        self.autonomous = Autonomous(cfg, ROOT, profiles=self.ai_router.profiles, memory_data=self.memory,
                                     local_manager=self.local_context, history_store=self.history)
        self.seen_redemptions: set[str] = set()
        self.recent_redemptions: deque[str] = deque()

    async def send(self, writer: asyncio.StreamWriter, line: str) -> None:
        writer.write((line + "\r\n").encode("utf-8"))
        await writer.drain()

    def local_event(self, event):
        text = event.get("message", "")
        if event.get("card_ids"):
            text += " Карточки: " + ", ".join(event["card_ids"])
        print("Локальный контекст: " + clean_text(redact_secret(text, self.cfg.get("AI_API_KEY", "")), 300), flush=True)

    async def say(self, writer: asyncio.StreamWriter, message: str, *,
                  local_bundle=None, creative_card_id=None) -> None:
        async with self._say_lock:
            wait = 1.6 - (time.monotonic() - self.last_sent)
            if wait > 0:
                await asyncio.sleep(wait)
            lease = None
            if creative_card_id is not None:
                lease = self.local_context.reserve_publish(local_bundle, creative_card_id)
                if lease is None:
                    raise LocalResultError("Локальная отсылка отменена: карточки или лимиты изменились.")
            try:
                await self.send(writer, f"PRIVMSG #{self.cfg['TWITCH_CHANNEL']} :{clean_text(message, CHAT_MAX_CHARS)}")
            except BaseException:
                if lease is not None:
                    self.local_context.complete_publish(lease, success=False)
                raise
            if lease is not None:
                self.local_context.complete_publish(lease, success=True)
            self.last_sent = time.monotonic()

    async def say_autonomous(self, writer, message, valid, reserve, *,
                             local_bundle=None, creative_card_id=None):
        # Never hold the paid-send lock or wait for a paid request / rate-limit slot.
        # No await between the last guard and write: a redemption cannot interleave.
        if not valid() or self._say_lock.locked() or time.monotonic() - self.last_sent < 1.6:
            return False
        lease = None
        if creative_card_id is not None:
            lease = self.local_context.reserve_publish(local_bundle, creative_card_id)
            if lease is None:
                return False
        try:
            reserve()
            writer.write((f"PRIVMSG #{self.cfg['TWITCH_CHANNEL']} :{message}\r\n").encode("utf-8"))
            self.last_sent = time.monotonic()
            await writer.drain()
        except BaseException:
            if lease is not None:
                self.local_context.complete_publish(lease, success=False)
            raise
        if lease is not None:
            self.local_context.complete_publish(lease, success=True)
        return True

    async def history_add(self, status, **values):
        journal = getattr(self, "history", None)
        if journal is None:
            return None
        return await asyncio.to_thread(journal.add, "reward", status,
                                       channel=self.cfg.get("TWITCH_CHANNEL", ""), **values)

    async def history_update(self, record_id, status, **values):
        journal = getattr(self, "history", None)
        if journal is not None:
            await asyncio.to_thread(journal.update, record_id, status, **values)

    def refusal_for(self, user, user_id, question):
        profiles = getattr(self.ai_router, "profiles", [])
        if not isinstance(profiles, (list, tuple)):
            profiles = []
        if reward_is_blocked(profiles, user, user_id):
            return REWARD_BLOCKED_REFUSAL, "Запросы через награду запрещены для зрителя."
        if unsafe_question(question):
            return PRIVACY_REFUSAL, "Защита личных данных."
        return None

    async def refuse(self, writer, user, user_id, question, notice, reason, record_id=None):
        if record_id is None:
            record_id = await self.history_add("rejected", viewer=user, viewer_id=user_id,
                question=safe_history_text(question), answer=notice, action="refusal", reason=reason)
        else:
            await self.history_update(record_id, "rejected", answer=notice, action="refusal", reason=reason)
        text = clean_text(f"@{user} {notice}", CHAT_MAX_CHARS)
        try:
            await self.say(writer, text)
            await self.history_update(record_id, "rejected", sent_text=text)
        except asyncio.CancelledError:
            await self.history_update(record_id, "cancelled", reason="Отправка отказа отменена.")
            raise
        except Exception:
            await self.history_update(record_id, "send_error", reason="Не удалось передать отказ Twitch.")
        return record_id

    async def worker(self, writer: asyncio.StreamWriter, queue: asyncio.Queue) -> None:
        while True:
            user, user_id, question, redemption_id = await queue.get()
            self._paid_busy = True
            record_id = None
            try:
                refusal = self.refusal_for(user, user_id, question)
                if refusal:
                    await self.refuse(writer, user, user_id, question, *refusal)
                    continue
                record_id = await self.history_add("generating", viewer=user, viewer_id=user_id,
                                                   question=question)
                observed = False
                def generated(answer, model):
                    nonlocal observed
                    self.history.update(record_id, "generated", answer=str(answer), model=model)
                    observed = True
                async def ask(*, allow_creative=True):
                    nonlocal observed
                    observed = False
                    kwargs = {} if allow_creative else {"allow_creative": False}
                    if getattr(self, "history", None) is not None and isinstance(self.ai_router, AIModelRouter):
                        kwargs["on_answer"] = generated
                    result = await asyncio.to_thread(
                        self.ai_router.ask, self.cfg, user, question, self.memory, user_id, history, **kwargs)
                    if not observed:
                        await self.history_update(record_id, "generated", answer=str(result),
                            model=getattr(self.ai_router, "last_model", "") or self.cfg.get("AI_MODEL", ""))
                    return result
                key = user_id or user.casefold()
                history = tuple(self.histories.get(key, ()))
                answer = await ask()
                try:
                    if isinstance(answer, LocalReply) and answer.creative_card_id is not None:
                        await self.say(writer, f"@{user} {str(answer)}", local_bundle=answer.local_bundle,
                                       creative_card_id=answer.creative_card_id)
                    else:
                        await self.say(writer, f"@{user} {str(answer)}")
                except LocalResultError:
                    print("Локальный контекст: отсылка устарела; готовлю обычный ответ на награду.", flush=True)
                    await self.history_update(record_id, "skipped", reason="Локальная отсылка отменена перед отправкой.")
                    record_id = await self.history_add("generating", viewer=user, viewer_id=user_id, question=question)
                    answer = await ask(allow_creative=False)
                    if is_local_service_output(answer):
                        raise LocalResultError("Некорректный обычный ответ после отмены локальной отсылки.")
                    await self.say(writer, f"@{user} {str(answer)}")
                answer = str(answer)
                await self.history_update(record_id, "sent", sent_text=clean_text(f"@{user} {answer}", CHAT_MAX_CHARS))
                self.autonomous.remember_reply(answer, target=user, source="reward", question=question)
                # Refresh insertion order only after a successful publication.
                pairs = self.histories.pop(key, deque(maxlen=10))
                pairs.append((question, answer))
                self.histories[key] = pairs
                if len(self.histories) > MAX_HISTORY_VIEWERS:
                    del self.histories[next(iter(self.histories))]
                print(f"Ответ отправлен для {user}", flush=True)
            except asyncio.CancelledError:
                await self.history_update(record_id, "cancelled", reason="Ответ прерван при остановке или разрыве соединения.")
                print(f"Награда {redemption_id} от {user}: ответ прерван; проверьте возврат баллов вручную.", flush=True)
                raise
            except PrivacyViolation:
                await self.refuse(writer, user, user_id, question, PRIVACY_REFUSAL,
                                  "Запрос или ответ отклонён защитой личных данных.", record_id)
            except Exception as exc:
                await self.history_update(record_id, "error", reason=str(exc))
                safe_error = redact_secret(str(exc), self.cfg.get("AI_API_KEY", ""))
                print(f"Ошибка ответа для {user} (награда {redemption_id}): {safe_error}; проверьте возврат баллов вручную.", flush=True)
                try:
                    notice = f"@{user} сейчас не получается ответить, попробуй позже."
                    await self.say(writer, notice)
                    await self.history_update(record_id, "error", sent_text=notice)
                except Exception as send_error:
                    await self.history_update(record_id, "send_error", reason=str(send_error))
            finally:
                self._paid_busy = False
                queue.task_done()

    async def connection(self) -> None:
        access_token = await asyncio.to_thread(
            get_access_token, self.cfg["TWITCH_CLIENT_ID"], self.cfg["TWITCH_BOT_NAME"],
            ROOT / ".twitch_token.json",
        )
        queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUED_QUESTIONS)
        async def on_question(user: str, user_id: str, question: str, redemption_id: str) -> None:
            refusal = self.refusal_for(user, user_id, question)
            if refusal:
                await self.refuse(writer, user, user_id, question, *refusal)
                return
            self.autonomous.interrupt()
            try:
                queue.put_nowait((user, user_id, question, redemption_id))
            except asyncio.QueueFull:
                print(f"Награда {redemption_id} от {user}: очередь заполнена; проверьте возврат баллов вручную.", flush=True)
                record_id = await self.history_add("error", viewer=user, viewer_id=user_id, question=question,
                                                   reason="Очередь вопросов заполнена.")
                notice = f"@{user} очередь переполнена, сообщи владельцу канала о возврате баллов."
                try:
                    await self.say(writer, notice)
                except BaseException:
                    await self.history_update(record_id, "send_error", reason="Не удалось передать уведомление Twitch.")
                    raise
                await self.history_update(record_id, "error", sent_text=notice)
                return
            print(f"Вопрос от {user} принят (в очереди: {queue.qsize()})", flush=True)

        listener = RewardListener(
            self.cfg["TWITCH_CLIENT_ID"], self.cfg["TWITCH_CHANNEL"],
            ROOT / ".twitch_broadcaster_token.json", on_question,
            reward_title=self.cfg["TWITCH_REWARD_TITLE"],
        )
        listener.seen_ids = self.seen_redemptions
        listener.recent_ids = self.recent_redemptions
        await listener.prepare()
        reader, writer = await asyncio.open_connection(
            "irc.chat.twitch.tv", 6697, ssl=ssl.create_default_context()
        )
        worker = asyncio.create_task(self.worker(writer, queue))
        rewards_task = None
        reader_task = None
        autonomous_task = None
        try:
            await self.send(writer, "PASS oauth:" + access_token)
            await self.send(writer, "NICK " + self.cfg["TWITCH_BOT_NAME"])
            await self.send(writer, "CAP REQ :twitch.tv/tags twitch.tv/commands")
            await self.send(writer, "JOIN #" + self.cfg["TWITCH_CHANNEL"])
            rewards_task = asyncio.create_task(listener.run())
            self.autonomous.connect(
                lambda text, valid, reserve, **kwargs: self.say_autonomous(writer, text, valid, reserve, **kwargs),
                lambda: self._paid_busy or not queue.empty(),
            )
            autonomous_task = asyncio.create_task(self.autonomous.run())
            async def read_chat() -> None:
                while raw := await reader.readline():
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    command = irc_command(line)
                    if command == "PING":
                        await self.send(writer, "PONG " + line[5:])
                    elif command == "RECONNECT":
                        return
                    elif command == "NOTICE" and "Login authentication failed" in line:
                        raise RuntimeError("Twitch отклонил OAuth-токен")
                    elif command == "PRIVMSG":
                        self.autonomous.receive(line)
                    elif command == "NOTICE":
                        print("Twitch: " + line.split(" :", 1)[-1], flush=True)

            reader_task = asyncio.create_task(read_chat())
            done, _ = await asyncio.wait((reader_task, rewards_task, autonomous_task), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            self.autonomous.disconnect()
            if autonomous_task is not None:
                autonomous_task.cancel()
            worker.cancel()
            if reader_task is not None:
                reader_task.cancel()
            if rewards_task is not None:
                rewards_task.cancel()
            await asyncio.gather(*(task for task in (worker, reader_task, rewards_task, autonomous_task) if task), return_exceptions=True)
            while not queue.empty():
                user, user_id, question, redemption_id = queue.get_nowait()
                await self.history_add("cancelled", viewer=user, viewer_id=user_id, question=question,
                                       reason="Соединение закрыто до обработки вопроса.")
                print(f"Награда {redemption_id} от {user}: соединение закрыто до ответа; проверьте возврат баллов вручную.", flush=True)
                queue.task_done()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def run(self) -> None:
        try:
            while True:
                try:
                    print(f"Подключаюсь к чату #{self.cfg['TWITCH_CHANNEL']}...", flush=True)
                    await self.connection()
                    print("Соединение закрыто; повтор через 5 секунд", flush=True)
                except (OSError, asyncio.IncompleteReadError) as exc:
                    print(f"Ошибка соединения: {exc}", flush=True)
                await asyncio.sleep(5)
        finally:
            self.autonomous.close()


if __name__ == "__main__":
    try:
        asyncio.run(Bot(config()).run())
    except (ValueError, RuntimeError) as exc:
        print(exc, flush=True)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("Бот остановлен", flush=True)
