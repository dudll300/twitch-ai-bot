"""Provider profiles, immutable operation credentials and one HTTPS boundary."""
from dataclasses import dataclass, field
import hashlib
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

PROVIDERS = ('compatible', 'openai')
OPENAI_BASE_URL = 'https://api.openai.com/v1'
OPENAI_MODEL = 'gpt-4.1-mini'
PROFILE_FIELDS = ('BASE_URL', 'API_KEY', 'MODEL', 'FALLBACK_MODELS', 'PROMPT_MODEL')


def provider_id(value='compatible'):
    if value not in PROVIDERS:
        raise ValueError('Выберите подключение: OpenAI или Совместимый API.')
    return value


def profile_key(provider, name):
    return 'AI_' + provider_id(provider).upper() + '_' + name


def profile_defaults(provider):
    from configuration import AI_MODEL, DEFAULTS
    model = OPENAI_MODEL if provider == 'openai' else AI_MODEL
    return dict(BASE_URL=OPENAI_BASE_URL if provider == 'openai' else DEFAULTS['AI_BASE_URL'],
                API_KEY='', MODEL=model, FALLBACK_MODELS='', PROMPT_MODEL=model)


def profiles(values):
    """Pure, repeatable legacy migration. Never infers a provider from a URL/key."""
    result = {}
    for provider in PROVIDERS:
        defaults = profile_defaults(provider)
        result[provider] = {name: values.get(profile_key(provider, name),
            values.get('AI_' + name, defaults[name]) if provider == 'compatible' else defaults[name])
            for name in PROFILE_FIELDS}
        result[provider]['KEY_ORIGIN'] = values.get(profile_key(provider, 'KEY_ORIGIN'), '')
        if provider == 'openai':
            result[provider]['BASE_URL'] = OPENAI_BASE_URL
    return result


def migrate(values):
    result = dict(values)
    provider = provider_id(values.get('AI_PROVIDER', 'compatible'))
    result['AI_PROVIDER'] = provider
    for name, row in profiles(values).items():
        result.update({profile_key(name, key): value for key, value in row.items()})
    # Legacy aliases always describe the selected connection, never a mixture.
    for name, value in profiles(result)[provider].items():
        if name in PROFILE_FIELDS:
            result['AI_' + name] = value
    return result


def origin(base_url):
    from configuration import normalize
    parsed = urlsplit(normalize('AI_BASE_URL', base_url))
    try:
        port = parsed.port or 443
    except ValueError:
        raise ValueError('Некорректный порт в адресе AI API.') from None
    return f'https://{parsed.hostname.lower()}:{port}'


def selected_values(values, previous=None, *, allow_incomplete=False):
    """Merge only provided edits; a blank key is scoped to its saved origin."""
    from configuration import normalize
    previous = previous or {}
    saved = profiles(previous)
    provider = provider_id(values.get('AI_PROVIDER', previous.get('AI_PROVIDER', 'compatible')))
    combined = migrate(previous)
    for name in PROVIDERS:
        row = dict(saved[name])
        for key in PROFILE_FIELDS:
            explicit = profile_key(name, key)
            # Existing callers edit these aliases after load_settings().
            if name == provider and 'AI_' + key in values:
                row[key] = values['AI_' + key]
            elif explicit in values:
                row[key] = values[explicit]
        if name == 'openai':
            row['BASE_URL'] = OPENAI_BASE_URL
        edited = name == provider or any(profile_key(name, k) in values
            and values[profile_key(name, k)] != saved[name][k]
            and (k != 'API_KEY' or values[profile_key(name, k)].strip()) for k in PROFILE_FIELDS)
        if not edited:
            continue
        for key in PROFILE_FIELDS:
            value = row[key]
            if key in ('MODEL', 'PROMPT_MODEL'):
                value = normalize('AI_MODEL', value) if value.strip() else ''
            elif key == 'FALLBACK_MODELS':
                value = normalize('AI_FALLBACK_MODELS', value)
            elif key == 'BASE_URL':
                value = normalize('AI_BASE_URL', value) if value.strip() else ''
            elif value.strip():
                value = normalize('AI_API_KEY', value)
            row[key] = value
        old = saved[name]
        entered = row['API_KEY'].strip()
        bound = old['KEY_ORIGIN'] or (origin(old['BASE_URL']) if old['API_KEY'] and old['BASE_URL'] else '')
        current_origin = origin(row['BASE_URL']) if row['BASE_URL'] else ''
        if not entered and old['API_KEY'] and bound == current_origin:
            row['API_KEY'] = old['API_KEY']
        # A file whose endpoint was edited outside the app cannot move its key.
        elif (entered and entered == old['API_KEY'] and bound and bound != current_origin
              and values.get(profile_key(name, 'CONFIRMED_ORIGIN')) != current_origin):
            row['API_KEY'] = ''
        row['KEY_ORIGIN'] = current_origin if row['API_KEY'] else ''
        combined.update({profile_key(name, key): value for key, value in row.items()})
    combined['AI_PROVIDER'] = provider
    result = migrate(combined)
    secrets = tuple(row['API_KEY'] for row in profiles(result).values() if row['API_KEY'])
    for key in ('AI_BASE_URL', 'AI_MODEL', 'AI_FALLBACK_MODELS', 'AI_PROMPT_MODEL'):
        if any(secret in result[key] for secret in secrets):
            raise ValueError('Уберите API-ключ из адреса и полей моделей.')
    if not allow_incomplete:
        if not result['AI_API_KEY'] and saved[provider]['API_KEY']:
            raise ValueError('Адрес AI API изменился. Введите ключ для этого подключения заново; сохранённый ключ на другой сервер не отправляется.')
        for key in ('BASE_URL', 'API_KEY', 'MODEL'):
            normalize('AI_' + key, result['AI_' + key])
    return result


@dataclass(frozen=True)
class Credentials:
    base_url: str = field(repr=False)
    api_key: str = field(repr=False)
    provider: str = 'compatible'
    revision: int = field(default=0, repr=False)
    model: str = field(default='', compare=False, repr=False)
    fallback_models: str = field(default='', compare=False, repr=False)

    def __post_init__(self):
        from configuration import normalize
        provider_id(self.provider)
        object.__setattr__(self, 'base_url', OPENAI_BASE_URL if self.provider == 'openai'
                           else normalize('AI_BASE_URL', self.base_url))
        object.__setattr__(self, 'api_key', normalize('AI_API_KEY', self.api_key))
        if self.api_key in self.base_url:
            raise ValueError('Уберите API-ключ из адреса AI API.')

    def config(self):
        return dict(AI_PROVIDER=self.provider, AI_BASE_URL=self.base_url, AI_API_KEY=self.api_key,
                    AI_CHAT_URL=self.base_url + '/chat/completions', AI_MODEL=self.model,
                    AI_FALLBACK_MODELS=self.fallback_models)

    @property
    def description(self):
        return ('OpenAI' if self.provider == 'openai' else 'Совместимый API') + ' · ' + urlsplit(self.base_url).netloc

    @property
    def identity(self):
        # Queue results contain this opaque identity, never credentials.
        value = '\0'.join((self.provider, self.base_url, self.api_key, str(self.revision)))
        return hashlib.sha256(value.encode('utf-8')).hexdigest()


def credentials(base_url, entered_key, saved_key='', *, provider='compatible', revision=0,
                model='', fallback_models=''):
    return Credentials(base_url, entered_key.strip() or saved_key, provider, revision, model, fallback_models)


def from_config(cfg):
    provider = provider_id(cfg.get('AI_PROVIDER', 'compatible'))
    base = cfg.get('AI_BASE_URL') or cfg.get('AI_CHAT_URL', '').removesuffix('/chat/completions')
    return Credentials(base, cfg.get('AI_API_KEY', ''), provider,
                       model=cfg.get('AI_MODEL', ''), fallback_models=cfg.get('AI_FALLBACK_MODELS', ''))


def chat_compatibility(provider, model):
    """Known exclusions only. Catalog membership never promises chat support."""
    if provider != 'openai':
        return ''
    if model.startswith(('text-embedding-', 'tts-', 'whisper-', 'dall-e-', 'gpt-image-',
                         'omni-moderation-', 'sora-', 'o1-mini', 'o1-preview')):
        return 'Эта модель не поддерживает текстовый Chat Completions с системными инструкциями. Выберите другую модель.'
    return ''


def completion_payload(auth, model, messages, max_tokens):
    error = chat_compatibility(auth.provider, model)
    if error:
        raise ValueError(error)
    payload = dict(model=model, messages=messages, stream=False)
    if auth.provider == 'compatible':
        payload['max_tokens'] = max_tokens
        return payload
    # Only documented reasoning families get extra reasoning budget/parameters.
    family = model.split(':')[1] if model.startswith('ft:') and ':' in model else model
    modern = bool(re.match(r'^(?:o[134](?:-|$)|gpt-[56](?:[.-]|$))', family))
    reasoning = modern and '-chat-' not in family
    payload['max_completion_tokens'] = min(8192, max(4096, max_tokens * 2)) if reasoning else max_tokens
    payload['store'] = False
    if modern:
        payload['messages'] = [{**row, 'role': 'developer' if row['role'] == 'system' else row['role']} for row in messages]
    if reasoning:
        if '-pro' not in family:
            payload['reasoning_effort'] = 'low'
    return payload


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # No second HTTP attempt outside the quota gate, and no forwarded key.
        return None


def urlopen(request, *, timeout):
    # Build only at the actual network boundary; no CA-store work at import/start.
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)


def authenticated_request(url, api_key, *, data=None):
    request = urllib.request.Request(url, data=data, headers={'Accept': 'application/json'},
                                     method='GET' if data is None else 'POST')
    request.add_unredirected_header('Authorization', 'Bearer ' + api_key)
    if data is not None:
        request.add_header('Content-Type', 'application/json')
    return request
