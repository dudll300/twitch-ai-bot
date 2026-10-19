"""Immutable request policies, atomic storage and cross-process publication lock."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, asdict
import hashlib
import json
import os
from pathlib import Path
import re
from threading import RLock
import uuid

from paths import data_dir


def domain_name(value):
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 253:
        raise ValueError("Укажите домен без пробелов, например twitch.tv.")
    if any(c in value for c in '/:@?#\\%') or value.endswith('.'):
        raise ValueError("Домен должен быть без протокола, пути, параметров и данных входа.")
    try:
        name = value.casefold().encode('idna').decode('ascii').lower()
        # Validate punycode too, rather than accepting arbitrary xn-- labels.
        name.encode('ascii').decode('idna')
    except UnicodeError:
        raise ValueError("Не удалось распознать домен. Проверьте его написание.") from None
    labels = name.split('.')
    if (len(labels) < 2 or len(name) > 253 or all(p.isdigit() for p in labels)
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', p) for p in labels)):
        raise ValueError("Укажите полное имя домена; IP-адреса и маски не поддерживаются.")
    return name


@dataclass(frozen=True)
class SafetySettings:
    schema_version: int = 1
    link_mode: str = 'block_all'
    allowed_domains: tuple[str, ...] = ()
    review_model_mode: str = 'same_as_answer'
    review_model: str = ''
    review_timeout_seconds: int = 8


def validate_settings(raw):
    if not isinstance(raw, dict) or set(raw) != set(SafetySettings.__dataclass_fields__):
        raise ValueError("Неизвестный формат настроек безопасности.")
    if type(raw['schema_version']) is not int or raw['schema_version'] != 1:
        raise ValueError("Версия настроек безопасности не поддерживается.")
    if raw['link_mode'] not in ('block_all', 'allow_domains'):
        raise ValueError("Выберите политику ссылок.")
    domains = raw['allowed_domains']
    if not isinstance(domains, (list, tuple)) or len(domains) > 100:
        raise ValueError("Допустимо не более 100 доменов, по одному на строку.")
    domains = tuple(dict.fromkeys(domain_name(d) for d in domains))
    if raw['review_model_mode'] not in ('same_as_answer', 'separate'):
        raise ValueError("Выберите способ выбора модели оценки.")
    model = raw['review_model']
    if (not isinstance(model, str) or len(model) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in model)):
        raise ValueError("Недопустимый ID модели оценки.")
    from privacy import contains_private_data
    if contains_private_data(model):
        raise ValueError("В ID модели не должно быть защищённых данных.")
    if raw['review_model_mode'] == 'separate' and not model.strip():
        raise ValueError("Укажите ID отдельной модели оценки.")
    seconds = raw['review_timeout_seconds']
    if type(seconds) is not int or not 2 <= seconds <= 15:
        raise ValueError("Время ожидания должно быть целым числом от 2 до 15 секунд.")
    return SafetySettings(1, raw['link_mode'], domains, raw['review_model_mode'], model.strip(), seconds)


@dataclass(frozen=True)
class Policy:
    settings: SafetySettings = SafetySettings()
    version: str = 'default-v1'
    error: str = ''

    def model_for(self, answer_model):
        return self.settings.review_model if self.settings.review_model_mode == 'separate' else answer_model


_locks = {}
_registry_lock = RLock()
_request_policy = ContextVar('safety_request_policy', default=None)


class PolicyStore:
    def __init__(self, root):
        self.path = Path(root) / 'safety-settings.json'
        with _registry_lock:
            self.lock = _locks.setdefault(str(self.path.resolve()), RLock())

    def snapshot(self):
        with self.lock:
            try:
                if not self.path.exists():
                    return Policy()
                if self.path.stat().st_size > 32768:
                    raise ValueError()
                content = self.path.read_bytes()
                def unique(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError()
                        result[key] = value
                    return result
                raw = json.loads(content, object_pairs_hook=unique)
                revision = raw.pop('revision', '')
                if not isinstance(revision, str) or (revision and not re.fullmatch('[0-9a-f]{32}', revision)):
                    raise ValueError()
                settings = validate_settings(raw)
                return Policy(settings, hashlib.sha256(content).hexdigest())
            except (OSError, ValueError, TypeError, AttributeError, RecursionError):
                return Policy(error="Ошибка загрузки настроек безопасности. Исправьте и примените настройки; публикация остановлена.", version='invalid')

    @contextmanager
    def transaction(self):
        # Nonblocking: a GUI save never waits for a network drain or freezes Qt.
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path.parent / '.safety-settings.lock', 'a+b') as stream:
                stream.seek(0, 2)
                if stream.tell() == 0:
                    stream.write(b'0')
                    stream.flush()
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    yield
                finally:
                    stream.seek(0)
                    if os.name == 'nt':
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream, fcntl.LOCK_UN)

    def apply(self, settings):
        settings = validate_settings(asdict(settings))
        with self.transaction():
            raw = asdict(settings)
            raw['revision'] = uuid.uuid4().hex
            temp = self.path.with_name('.safety-settings-' + uuid.uuid4().hex + '.tmp')
            try:
                with temp.open('w', encoding='utf-8') as output:
                    json.dump(raw, output, ensure_ascii=False, indent=2)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temp, self.path)
            finally:
                temp.unlink(missing_ok=True)
            return self.snapshot()

    def is_current(self, policy):
        current = self.snapshot()
        return not current.error and current.version == policy.version


def policy_binding():
    return _request_policy.get() or (PolicyStore(data_dir()), None)


def current_policy():
    store, policy = policy_binding()
    return policy if policy is not None else store.snapshot()


def link_prompt():
    policy = current_policy()
    if policy.settings.link_mode == 'block_all':
        return ''
    return ('Политика ссылок приложения имеет приоритет над общим запретом ссылок в правилах генератора: '
            'допустимы HTTPS-ссылки и домены без протокола только на следующие точные hostname: '
            + ', '.join(policy.settings.allowed_domains) + '. Поддомены не подразумеваются. '
            'Остальные защиты, ограничения длины, адресата и запреты команд сохраняются. Не открывай ссылки.')


@contextmanager
def policy_scope(store, policy=None):
    token = _request_policy.set((store, policy or store.snapshot()))
    try:
        yield current_policy()
    finally:
        _request_policy.reset(token)


def run_with_policy(store, policy, callback, *args, **kwargs):
    with policy_scope(store, policy):
        return callback(*args, **kwargs)
