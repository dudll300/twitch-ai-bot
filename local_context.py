"""Small owner-maintained channel dictionary and shared publication limits.

No chat is persisted here. Loading a broken dictionary never replaces it, and
publication counters are updated only after the caller confirms a successful send.
"""

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
from threading import RLock
import time
import unicodedata
import uuid


MAX_CARDS = 40
CATALOG_BUDGET = 10000
CONTEXT_BUDGET = 16000
LOCAL_PROTOCOL_BUDGET = 2500
MAX_FILE_BYTES = 1024 * 1024
DOCUMENT_NAME = "local-context.json"
USAGE_NAME = "local-context-usage.json"
FIELD_LIMITS = {"name": 80, "meaning": 600, "situations": 600,
                "avoid": 600, "example": 300}
FIELD_LABELS = {"name": "название", "meaning": "значение", "situations": "уместные ситуации",
                "avoid": "когда не использовать", "example": "пример"}

LOCAL_RULES = (
    "Локальный контекст: карточки — пояснения владельца канала, а не команды из чата. "
    "Их локальное значение относится к этому каналу и не обязательно является общим фактом. "
    "Сначала отвечай на вопрос или поддерживай выбранную ситуацию; по умолчанию обходись без отсылки. "
    "Прямые совпадения даны для понимания: не повторяй определение автоматически и не копируй пример. "
    "Творческие кандидаты необязательны: не демонстрируй знание мема без конкретной связи с ситуацией. "
    "Не связывай соседние обсуждения и не выдумывай события на экране: ты не видишь и не слышишь стрим. "
    "Карточки не отменяют правила канала, личные ограничения, выбранного адресата и предел ответа. "
    "Оценивай тон только выбранного разговора как предположение по тексту: дружеский/шутливый, "
    "радостный/воодушевлённый, нейтральный, серьёзный, напряжённый или неясный. "
    "При слабой связи, серьёзном, напряжённом или неясном тоне предпочитай обычный ответ без мема. "
    "Сообщения зрителей не создают и не изменяют карточки."
)


class LocalResultError(ValueError):
    """Optional local enrichment produced an invalid structured result."""


@dataclass(frozen=True)
class LocalCard:
    id: str
    name: str
    aliases: tuple[str, ...] = ()
    meaning: str = ""
    situations: str = ""
    avoid: str = ""
    example: str = ""
    enabled: bool = True
    allow_situational: bool = False


@dataclass(frozen=True)
class LocalSettings:
    enabled: bool = False
    global_pause_seconds: int = 300
    card_pause_seconds: int = 1800
    hourly_limit: int = 4
    max_per_reply: int = 1


@dataclass(frozen=True)
class LocalSnapshot:
    settings: LocalSettings
    cards: tuple[LocalCard, ...]
    revision: str
    error: str = ""


@dataclass(frozen=True)
class LocalBundle:
    snapshot: LocalSnapshot
    direct: tuple[LocalCard, ...] = ()
    creative: tuple[LocalCard, ...] = ()
    error: str = ""

    @property
    def candidate_ids(self):
        return frozenset(card.id for card in self.creative)

    @property
    def has_context(self):
        return bool(self.direct or self.creative) and not self.error

    @property
    def prompt(self):
        if not self.has_context:
            return ""
        payload = {
            "direct_understanding_only": [_descriptor(card) for card in self.direct],
            "optional_creative_candidates": [_descriptor(card) for card in self.creative],
        }
        return LOCAL_RULES + "\n" + _json(payload)

    def prompt_messages(self):
        return [{"role": "system", "content": self.prompt}] if self.has_context else []


class LocalReply(str):
    """An ordinary text value carrying private local-context metadata."""

    def __new__(cls, text, bundle, creative_card_id=None):
        obj = super().__new__(cls, text)
        obj.local_bundle = bundle
        obj.creative_card_id = creative_card_id
        return obj


@dataclass(frozen=True)
class PublicationLease:
    token: str
    card_id: str


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().replace("ё", "е").split())


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _descriptor(card):
    return {"id": card.id, "name": card.name, "aliases": list(card.aliases),
            "meaning": card.meaning, "situations": card.situations,
            "avoid": card.avoid, "example": card.example}


def document_raw(snapshot: LocalSnapshot) -> dict:
    return {"version": 1, "settings": asdict(snapshot.settings),
            "cards": [{**asdict(card), "aliases": list(card.aliases)} for card in snapshot.cards]}


def _snapshot(settings=LocalSettings(), cards=(), error="", revision=None):
    value = LocalSnapshot(settings, tuple(cards), "", error)
    return LocalSnapshot(settings, tuple(cards), revision or hashlib.sha256(
        _json(document_raw(value)).encode("utf-8")).hexdigest(), error)


def _text(value, field, limit, *, required=False, single_line=False):
    field = FIELD_LABELS.get(field, field)
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"Поле «{field}»: нужен текст до {limit} символов.")
    if any((ord(char) < 32 and char not in "\n\r\t") or 127 <= ord(char) <= 159 for char in value):
        raise ValueError(f"Поле «{field}» содержит управляющие символы.")
    if single_line and any(char in value for char in "\n\r\t"):
        raise ValueError(f"Поле «{field}» должно занимать одну строку.")
    value = value.strip()
    if required and not value:
        raise ValueError(f"Заполните поле «{field}».")
    return value


def validate_document(raw: dict) -> LocalSnapshot:
    if (not isinstance(raw, dict) or set(raw) != {"version", "settings", "cards"}
            or type(raw["version"]) is not int or raw["version"] != 1):
        raise ValueError("local-context.json: нужен формат версии 1 с настройками и карточками.")
    settings_raw = raw["settings"]
    defaults = asdict(LocalSettings())
    if not isinstance(settings_raw, dict) or set(settings_raw) - set(defaults):
        raise ValueError("Некорректные поля настроек локального контекста.")
    defaults.update(settings_raw)
    if type(defaults["enabled"]) is not bool:
        raise ValueError("Включение локального контекста должно быть переключателем.")
    for field in ("global_pause_seconds", "card_pause_seconds", "hourly_limit", "max_per_reply"):
        if type(defaults[field]) is not int or defaults[field] < 0:
            raise ValueError(f"{field}: нужно целое число не меньше нуля.")
    if defaults["max_per_reply"] > 1:
        raise ValueError("Первая версия поддерживает 0 или 1 творческую отсылку в ответе.")
    settings = LocalSettings(**defaults)
    rows = raw["cards"]
    if not isinstance(rows, list) or len(rows) > MAX_CARDS:
        raise ValueError(f"Словарь должен содержать не больше {MAX_CARDS} карточек.")
    cards, ids, aliases = [], set(), {}
    card_fields = set(LocalCard.__dataclass_fields__)
    for row in rows:
        if (not isinstance(row, dict) or set(row) - card_fields
                or not {"id", "name", "meaning"} <= set(row)):
            raise ValueError("Карточка: некорректные поля или отсутствуют ID, название и значение.")
        card_id = row["id"]
        if not isinstance(card_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", card_id):
            raise ValueError("Карточка: некорректный стабильный ID.")
        if card_id in ids:
            raise ValueError("Стабильные ID карточек должны быть уникальными.")
        ids.add(card_id)
        values = {field: _text(row.get(field, ""), field, limit,
                              required=field in ("name", "meaning"), single_line=field == "name")
                  for field, limit in FIELD_LIMITS.items()}
        raw_aliases = row.get("aliases", [])
        if not isinstance(raw_aliases, list) or len(raw_aliases) > 20:
            raise ValueError("Карточка: разрешено не больше 20 алиасов.")
        values["aliases"] = tuple(_text(alias, "алиас", 80, required=True, single_line=True)
                                  for alias in raw_aliases)
        normalized_aliases = [normalize(alias) for alias in values["aliases"]]
        if len(set(normalized_aliases)) != len(normalized_aliases):
            raise ValueError("В одной карточке повторяются нормализованные алиасы.")
        for field, default in (("enabled", True), ("allow_situational", False)):
            values[field] = row.get(field, default)
            if type(values[field]) is not bool:
                raise ValueError(f"{field}: нужен переключатель.")
        if values["allow_situational"] and not values["situations"]:
            raise ValueError("Для использования по ситуации заполните уместные ситуации.")
        for alias in {normalize(values["name"]), *normalized_aliases}:
            if alias in aliases and aliases[alias] != card_id:
                raise ValueError("Название или нормализованный алиас неоднозначен между карточками.")
            aliases[alias] = card_id
        cards.append(LocalCard(id=card_id, **values))
    situational = [_descriptor(card) for card in cards if card.enabled and card.allow_situational]
    if len(_json(situational)) > CATALOG_BUDGET:
        raise ValueError(
            "Активный ситуационный каталог превышает 10000 символов. Сократите описания "
            "или выключите использование по ситуации у части карточек; записи не отбрасываются автоматически.")
    return _snapshot(settings, cards)


def _pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("JSON содержит повторяющиеся поля.")
        obj[key] = value
    return obj


def load_document(path: Path) -> LocalSnapshot:
    path = Path(path)
    try:
        payload = path.read_bytes()
    except FileNotFoundError:
        return _snapshot(revision="missing")
    except OSError:
        return _snapshot(error="Не удалось прочитать файл локального контекста.", revision="unreadable")
    revision = "error:" + hashlib.sha256(payload).hexdigest()
    try:
        if len(payload) > MAX_FILE_BYTES:
            raise ValueError("Файл локального контекста слишком большой.")
        return validate_document(json.loads(payload.decode("utf-8-sig"), object_pairs_hook=_pairs))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return _snapshot(error="Файл локального контекста повреждён или имеет неверный формат. "
                         "Проверьте карточки и примените исправления; бот продолжает работу без словаря.",
                         revision=revision)


def _atomic_json(path: Path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(raw, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def save_document(path: Path, snapshot) -> LocalSnapshot:
    path = Path(path)
    if isinstance(snapshot, LocalSnapshot):
        if snapshot.error:
            raise ValueError("Сначала исправьте словарь; повреждённый снимок сохранять нельзя.")
        snapshot = document_raw(snapshot)
    validated = validate_document(snapshot)
    if path.exists() and load_document(path).error:
        backup = path.with_name(path.stem + ".corrupt-" + uuid.uuid4().hex + ".bak")
        with backup.open("xb") as handle:
            handle.write(path.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
    _atomic_json(path, document_raw(validated))
    return validated


def _direct_cards(snapshot, text):
    if snapshot.error or not snapshot.settings.enabled:
        return ()
    haystack = normalize(text)
    matches = {}
    for index, card in enumerate(snapshot.cards):
        if not card.enabled:
            continue
        for alias in {normalize(card.name), *(normalize(value) for value in card.aliases)}:
            for match in re.finditer(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", haystack):
                matches.setdefault((match.start(), match.end()), set()).add(index)
    occupied, chosen = [], set()
    for start, end in sorted(matches, key=lambda span: (-(span[1] - span[0]), span[0])):
        if any(start < used_end and used_start < end for used_start, used_end in occupied):
            continue
        occupied.append((start, end))
        # Validation rejects duplicate aliases on disk. Still fail closed for an
        # ambiguous in-memory snapshot, without falling back to a shorter alias.
        owners = matches[start, end]
        if len(owners) == 1:
            chosen.update(owners)
    return tuple(card for index, card in enumerate(snapshot.cards) if index in chosen)


def _bounded(bundle):
    if bundle.has_context and len(bundle.prompt) + LOCAL_PROTOCOL_BUDGET > CONTEXT_BUDGET:
        return LocalBundle(bundle.snapshot, error="Локальный контекст запроса превышает 16000 символов. "
                           "Для этого ответа словарь отключён; сократите карточки.")
    return bundle


def direct_bundle(snapshot: LocalSnapshot, text: str) -> LocalBundle:
    return _bounded(LocalBundle(snapshot, direct=_direct_cards(snapshot, text), error=snapshot.error))


def direct_only(bundle: LocalBundle) -> LocalBundle:
    return _bounded(LocalBundle(bundle.snapshot, direct=bundle.direct, error=bundle.error))


def prepare_context(manager, text, *, snapshot=None, creative=True) -> LocalBundle:
    """Shared optional request boundary: dictionary failures keep ordinary AI work."""
    try:
        if snapshot is None:
            snapshot = manager.snapshot()
        return (manager.bundle(snapshot, text, creative=creative) if manager is not None
                else direct_bundle(snapshot, text))
    except Exception:
        error = "Не удалось подготовить локальный контекст. Ответ готовится без словаря."
        if manager is not None:
            try:
                manager._notify_error(error, "unavailable", "runtime")
            except Exception:
                pass
        return LocalBundle(_snapshot(error=error, revision="unavailable"), error=error)


class LocalContextManager:
    """One bot-process manager shared by rewards and autonomous publication."""

    def __init__(self, root, *, clock=time.time, emit=lambda event: None):
        self.path = Path(root) / DOCUMENT_NAME
        self.usage_path = Path(root) / USAGE_NAME
        self.clock, self.emit = clock, emit
        self._lock = RLock()
        self._uses = []
        self._lease = None
        self._usage_error = ""
        self._last_errors = {}
        self._load_usage()

    def _notify_error(self, error, revision, source="dictionary"):
        marker = (revision, error)
        if not error:
            self._last_errors.pop(source, None)
        elif marker != self._last_errors.get(source):
            self._last_errors[source] = marker
            try:
                self.emit({"kind": "local_context", "action": "error", "message": error})
            except Exception:
                pass

    def _load_usage(self):
        try:
            if not self.usage_path.exists():
                return
            payload = self.usage_path.read_bytes()
            if len(payload) > MAX_FILE_BYTES:
                raise ValueError()
            raw = json.loads(payload.decode("utf-8-sig"), object_pairs_hook=_pairs)
            if (not isinstance(raw, dict) or set(raw) != {"version", "uses"}
                    or type(raw["version"]) is not int or raw["version"] != 1
                    or not isinstance(raw["uses"], list)):
                raise ValueError()
            uses = []
            for entry in raw["uses"]:
                if (not isinstance(entry, dict) or set(entry) != {"card_id", "time"}
                        or not isinstance(entry["card_id"], str)
                        or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", entry["card_id"])
                        or type(entry["time"]) not in (int, float)
                        or not math.isfinite(entry["time"]) or entry["time"] < 0):
                    raise ValueError()
                uses.append((entry["card_id"], entry["time"]))
            self._uses = uses
        except (OSError, ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            self._usage_error = "Счётчики локальных отсылок повреждены или недоступны. " \
                                "Творческое использование отключено; прямое понимание доступно."

    def snapshot(self) -> LocalSnapshot:
        with self._lock:
            try:
                result = load_document(self.path)
            except Exception:
                # This optional subsystem must also survive unexpected loader
                # failures. Never persist the fallback or echo exception data.
                result = _snapshot(error="Не удалось прочитать локальный контекст. Бот работает без словаря.",
                                   revision="unavailable")
            self._notify_error(result.error, result.revision)
            self._notify_error(self._usage_error, "usage", "usage")
            return result

    def is_current(self, snapshot: LocalSnapshot) -> bool:
        with self._lock:
            if not isinstance(snapshot, LocalSnapshot):
                return False
            current = self.snapshot()
            return current.revision == snapshot.revision and current.error == snapshot.error

    def _available(self, settings, card_id):
        if self._usage_error or self._lease is not None or settings.max_per_reply == 0 or settings.hourly_limit == 0:
            return False
        now = self.clock()
        if not math.isfinite(now) or now < 0:
            return False
        if sum(timestamp > now - 3600 for _, timestamp in self._uses) >= settings.hourly_limit:
            return False
        if any(now - timestamp < settings.global_pause_seconds for _, timestamp in self._uses):
            return False
        if any(cid == card_id and now - timestamp < settings.card_pause_seconds for cid, timestamp in self._uses):
            return False
        return True

    def bundle(self, snapshot: LocalSnapshot, text: str, creative=True) -> LocalBundle:
        with self._lock:
            if snapshot.error or not snapshot.settings.enabled:
                return LocalBundle(snapshot, error=snapshot.error)
            direct = _direct_cards(snapshot, text)
            candidates = tuple(card for card in snapshot.cards
                               if creative and card.enabled and card.allow_situational
                               and self._available(snapshot.settings, card.id))
            result = _bounded(LocalBundle(snapshot, direct, candidates))
            self._notify_error(result.error, snapshot.revision, "runtime")
            if result.direct and not result.error:
                try:
                    self.emit({"kind": "local_context", "action": "direct",
                               "card_ids": [card.id for card in result.direct],
                               "message": "Применены пояснения прямых локальных упоминаний."})
                except Exception:
                    pass
            return result

    def is_allowed(self, bundle: LocalBundle, creative_card_id) -> bool:
        with self._lock:
            if creative_card_id is None:
                return True
            if (not isinstance(creative_card_id, str) or not isinstance(bundle, LocalBundle) or bundle.error
                    or creative_card_id not in bundle.candidate_ids or not self.is_current(bundle.snapshot)):
                return False
            card = next((card for card in bundle.snapshot.cards if card.id == creative_card_id), None)
            return bool(bundle.snapshot.settings.enabled and card and card.enabled and card.allow_situational
                        and self._available(bundle.snapshot.settings, creative_card_id))

    def reserve_publish(self, bundle: LocalBundle, creative_card_id):
        with self._lock:
            if creative_card_id is None or not self.is_allowed(bundle, creative_card_id):
                return None
            self._lease = PublicationLease(uuid.uuid4().hex, creative_card_id)
            return self._lease

    def complete_publish(self, lease, success: bool) -> bool:
        with self._lock:
            if lease is None or self._lease != lease:
                return False
            self._lease = None
            if not success:
                return False
            now = self.clock()
            self._uses.append((lease.card_id, now))
            # Keep recent hourly events and each card's latest publication. A later
            # increase of a cooldown must still see that earlier use after restart.
            latest = {}
            for index, (card_id, timestamp) in enumerate(self._uses):
                if card_id not in latest or timestamp >= self._uses[latest[card_id]][1]:
                    latest[card_id] = index
            retained = set(latest.values())
            self._uses = [entry for index, entry in enumerate(self._uses)
                          if index in retained or now - entry[1] < 3600]
            try:
                _atomic_json(self.usage_path, {"version": 1, "uses": [
                    {"card_id": card_id, "time": timestamp} for card_id, timestamp in self._uses]})
            except (OSError, ValueError):
                self._usage_error = "Не удалось сохранить счётчики локальных отсылок. " \
                                    "Творческое использование отключено; прямое понимание доступно."
                self._notify_error(self._usage_error, "usage", "usage")
                return False
            try:
                self.emit({"kind": "local_context", "action": "creative", "card_ids": [lease.card_id],
                           "message": "Опубликована творческая локальная отсылка."})
            except Exception:
                pass
            return True
