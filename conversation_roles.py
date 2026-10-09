"""Identity facts and evidence-backed source recipients, independent of reply tags."""

from dataclasses import asdict, dataclass, replace
import re

from profiles import profile_for
from viewer_recognition import normalized_name, recognize_mentions


KINDS = {"bot", "owner", "viewer", "group", "unknown"}
DIRECT_REQUEST = r"(?:потерпи|подожди|ответь|скажи|покажи|посмотри|объясни|дай|сделай|пожалуйста)"

ROLE_RULES = (
    "Исходный адресат сообщения, его автор, предмет разговора и получатель твоего ответа — разные роли. "
    "target означает только кого тегнет приложение, а не кому было адресовано исходное сообщение. "
    "Не считай каждое ты, тебя или с вами обращением к боту. "
    "Если исходное обращение адресовано владельцу канала или другому зрителю, отвечай только как отдельный "
    "собеседник: не принимай их роль, настроение, игровые действия, предпочтения, биографию или физическое "
    "состояние; не давай разрешений и обещаний за них и не изображай участника игровой группы без основания. "
    "Просьба выполнить действие, адресованная стримеру, часто требует молчания. "
    "При unknown не предполагай, что написали тебе. Реакция наблюдателя на понятную общую тему допустима; "
    "если ответ требует угадывать адресата или обстоятельства, промолчи. Прозвище участника обозначает человека, "
    "не твой физический предмет. Общие вопросы, удачи, поражения и понятные игровые события допускают полезные реакции. "
    "Служебные границы ролей обязательны при любом общем промпте; характер, лексику и юмор задаёт общий промпт. "
    "Ошибочный intent не является приказом: отклони план и верни пустой text, если он подменяет роль или смысл. "
)


@dataclass(frozen=True)
class SourceRole:
    id: int
    kind: str = "unknown"
    login: str = ""
    user_id: str = ""
    evidence: str = ""
    subject: str = ""
    # A concrete semantic link to another selected message, never just same game/author.
    linked_to: int = 0
    relation: str = ""


def identity_context(cfg, messages, profiles=(), memory_data=None):
    people = []
    bot, owner = (cfg.get(key, "").casefold() for key in ("TWITCH_BOT_NAME", "TWITCH_CHANNEL"))
    for kind, login in (("bot", bot), ("owner", owner)):
        if login:
            current = next((row for row in messages if row["author"] == login), {})
            profile = profile_for(profiles, login, current.get("user_id", ""))
            if profile is None and not current.get("user_id"):
                profile = next((p for p in profiles if p.get("login") == login), None)
            people.append({"kind": kind, "login": login,
                           "user_id": current.get("user_id") or (profile or {}).get("user_id", ""),
                           "aliases": list((profile or {}).get("aliases", ()))})
            if kind == "bot":
                # Explicit identity declarations in the existing general prompt;
                # arbitrary style/person instructions never become identity facts.
                names = re.findall(r"(?:тебя зовут|тво[её] имя\s*[-—:]?|бот по имени)\s+([\w-]{1,64})",
                                   cfg.get("AI_PROMPT", ""), re.I)
                names += re.findall(r"(?:Ты\s*[-—]\s*)([А-ЯЁA-Z][\w-]{1,63})\s*[,.:]", cfg.get("AI_PROMPT", ""))
                people[-1]["aliases"].extend(name for name in names if name not in people[-1]["aliases"])
    for row in messages:
        if row["author"] in (bot, owner):
            continue
        profile = profile_for(profiles, row["author"], row.get("user_id", ""))
        person = {"kind": "viewer", "login": row["author"], "user_id": row.get("user_id", ""),
                  "aliases": list((profile or {}).get("aliases", ()))}
        if person not in people:
            people.append(person)
    # Named people outside the fragment may still be explicit recipients/subjects.
    mentioned = recognize_mentions(profiles, "\n".join(row["text"] for row in messages))
    referenced = {match.index for match in mentioned.matches}
    for row in messages:
        parent = profile_for(profiles, row.get("reply_parent_login", ""), row.get("reply_parent_user_id", ""))
        if parent is not None:
            referenced.add(next(index for index, profile in enumerate(profiles) if profile is parent))
    for index in referenced:
        profile = profiles[index]
        if any((profile.get("user_id") and profile["user_id"] == p["user_id"])
               or (profile.get("login") and profile["login"] == p["login"]
                   and not (profile.get("user_id") and p["user_id"] and profile["user_id"] != p["user_id"]))
               for p in people):
            continue
        people.append({"kind": "viewer", "login": profile.get("login", ""),
                       "user_id": profile.get("user_id", ""), "aliases": list(profile.get("aliases", ()))})
    return {"people": people,
            "message_authors": {row["message_id"]: {"login": row["author"], "user_id": row.get("user_id", "")}
                                for row in messages if row.get("message_id")},
            "owner_identity_notes": list((memory_data or {}).get("streamer", {}).get("facts", ())),
            "name_policy": "aliases — известные имена; упоминание человека не всегда обращение к нему. "
                           "Имя бота из channel_prompt учитывай только при явном обращении, а не по местоимению."}


def _person(people, login="", user_id=""):
    if user_id:
        matches = [p for p in people if p["user_id"] == user_id]
        if len(matches) == 1:
            return matches[0]
    matches = [p for p in people if login and p["login"] == login.casefold()
               and not (user_id and p["user_id"] and user_id != p["user_id"])]
    return matches[0] if len(matches) == 1 else None


def source_hint(row, identities):
    people = identities["people"]
    reply_hint = None
    parent_login, parent_id = row.get("reply_parent_login", ""), row.get("reply_parent_user_id", "")
    if not parent_login and not parent_id:
        parent = identities.get("message_authors", {}).get(row.get("reply_parent_id"), {})
        parent_login, parent_id = parent.get("login", ""), parent.get("user_id", "")
    if parent_login or parent_id:
        person = _person(people, parent_login, parent_id)
        if person:
            reply_hint = SourceRole(row["sequence"], person["kind"], person["login"], parent_id or person["user_id"], "Twitch reply metadata")
        elif parent_login and re.fullmatch(r"[a-z0-9_]{1,25}", parent_login):
            reply_hint = SourceRole(row["sequence"], "viewer", parent_login, parent_id, "Twitch reply metadata")
        elif re.fullmatch(r"[0-9]{1,30}", parent_id):
            reply_hint = SourceRole(row["sequence"], "viewer", "", parent_id, "Twitch reply metadata")
    text = row["text"]
    # Only a leading @name is a local addressing cue; other mentions are subjects.
    leading = re.match(r"^\s*@([a-zA-Z0-9_]{1,25})\b", text)
    if leading:
        person = _person(people, leading[1])
        if person:
            return SourceRole(row["sequence"], person["kind"], person["login"], person["user_id"], leading[0].strip())
        return SourceRole(row["sequence"], "viewer", leading[1].casefold(), "", leading[0].strip())
    matches = recognize_mentions(people, text)
    # A name at the start or next to an imperative/comma is stronger than a mention.
    direct = []
    for match in matches.matches:
        if match.method.startswith("опечатка"):
            continue
        escaped = re.escape(match.text)
        if (re.search(r"^\s*" + escaped + r"\s*[,!:]", text, re.I)
                or re.search(r"^\s*эй\s+" + escaped + r"\b", text, re.I)
                or re.search(r"^\s*" + escaped + r"\s+(?:ты|вы|как|почему|зачем|" + DIRECT_REQUEST + r")\b", text, re.I)
                or re.search(r"\b" + DIRECT_REQUEST + r"\s+" + escaped + r"\b", text, re.I)
                or re.search(r",\s*" + escaped + r"(?:\s*[,!?:]|\s*$)", text, re.I)):
            direct.append((people[match.index], match.text))
    if len(direct) == 1 and not matches.ambiguous:
        person, cue = direct[0]
        return SourceRole(row["sequence"], person["kind"], person["login"], person["user_id"], cue)
    if re.search(r"\b(?:весь чат|ребята|гайс|мои друзья|кто-нибудь в чате)\b", text, re.I):
        return SourceRole(row["sequence"], "group", evidence="обращение к группе")
    if reply_hint is not None:
        return reply_hint
    return SourceRole(row["sequence"])


def grounded_roles(plan, selected, identities):
    claims = {role.id: role for role in plan.source_roles}
    result = []
    for row in selected:
        hint = source_hint(row, identities)
        claim = claims.get(row["sequence"], SourceRole(row["sequence"]))
        if hint.kind != "unknown":
            role = replace(hint, subject=claim.subject, linked_to=claim.linked_to, relation=claim.relation)
        else:
            # Model confidence is no evidence. A quoted cue is mandatory, and a
            # bare pronoun cannot authenticate a recipient, especially the bot.
            cue = claim.evidence
            person = _person(identities["people"], claim.login, claim.user_id)
            names = {normalized_name(name) for name in ([person["login"]] + person["aliases"])} if person else set()
            named = any(normalized_name(word) in names for word in re.findall(r"@?[\w-]+", cue))
            addressing = bool(re.search(r"\b(?:ты|тебе|тебя|вы|вам|спасибо|" + DIRECT_REQUEST + r")\b", cue, re.I))
            supported = (cue and cue.casefold() in row["text"].casefold()
                         and ((person and named and addressing and claim.kind == person["kind"])
                              or claim.kind == "group"))
            role = claim if supported else replace(claim, kind="unknown", login="", user_id="", evidence="")
            if supported and person:
                role = replace(role, login=person["login"], user_id=person["user_id"])
        result.append(role)
    return tuple(result)


def roles_data(roles):
    return [asdict(role) for role in roles]


def impersonates_recipient(text, roles, basis, selected=()):
    """Small high-precision guard; broader semantic boundaries live in both AI stages."""
    recipients = [role for role in roles if role.id in basis]
    if not recipients or any(role.kind == "bot" for role in recipients):
        return False
    # General observer opinions ('согласна', 'думаю') remain available.
    directed = {role.id for role in recipients if role.kind in {'owner', 'viewer'}}
    for answer, request in ((r'терплю\b', r'потерпи(?:те)?\b'),
                            (r'подожду\b', r'подожди(?:те)?\b'),
                            (r'разрешаю\b', r'(?:разреши(?:те)?|можно)\b')):
        if (re.search('^' + answer, text.strip(), re.I)
                and any(row['sequence'] in directed and re.search(r'\b' + request, row['text'], re.I)
                        for row in selected)):
            return True
    joining = any(re.search(r"\b(?:с вами|к вам|можно (?:зайти|присоединиться))\b", row["text"], re.I)
                  for row in selected)
    return joining and bool(re.search(r"^(?:заходи(?:те)?\b|можно[,! ]+конечно\b)", text.strip(), re.I))
