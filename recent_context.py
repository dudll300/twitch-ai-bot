"""Bounded, recipient-scoped conversation data shared by live reward requests."""

from datetime import datetime, timezone
import math
import re

from privacy import safe_history_text
from viewer_recognition import recognize_mentions

PERSONAL_REPLY_SECONDS = 30 * 60
PERSONAL_REPLY_LIMIT = 1
BASIS_LIMIT = 4
BASIS_TEXT_LIMIT = 240
PUBLIC_REPLY_LIMIT = 2
PUBLIC_CANDIDATE_LIMIT = 100


def references_public_reply(question):
    return bool(re.search(r"извин\w*|наех\w*|(?:ты|бот)\s+.*(?:сказал|ответ|написал)|тво[йяию]\w*\s+(?:ответ|реплик)|цитат", question, re.I))


def same_recipient(saved_login, saved_id, login, user_id):
    if saved_id and user_id:
        return saved_id == user_id
    # A saved stable identity cannot be authenticated by a login-only request.
    if saved_id:
        return False
    return bool(saved_login and saved_login.casefold() == login.casefold())


def public_reply(row, now, *, channel=""):
    if not isinstance(row, dict) or not isinstance(row.get("source"), str):
        return None
    if (row.get("source") not in {"autonomous", "reward"} or row.get("status") != "sent"
            or row.get("mode") == "preview" or not row.get("target")
            or not isinstance(row.get("target"), str) or not isinstance(row.get("user_id", ""), str)
            or not isinstance(row.get("channel", ""), str)
            or (channel and row.get("channel", "").casefold() != channel.casefold())):
        return None
    sent_at = row.get("time")
    if (type(sent_at) not in (int, float) or not math.isfinite(sent_at)
            or not 0 <= now - sent_at <= PERSONAL_REPLY_SECONDS):
        return None
    text = row.get("text", "")
    if not isinstance(text, str):
        return None
    tag = re.match(r"^@([a-zA-Z0-9_]{1,25})\s+", text)
    if not tag or tag[1].casefold() != row["target"].casefold():
        return None
    from privacy import PRIVACY_REFUSAL
    from profiles import REWARD_BLOCKED_REFUSAL
    from safety import SAFETY_REFUSAL
    if text[tag.end():].strip().casefold() in {
            value.casefold() for value in (PRIVACY_REFUSAL, REWARD_BLOCKED_REFUSAL, SAFETY_REFUSAL,
            "сейчас не получается ответить, попробуй позже.",
            "очередь переполнена, сообщи владельцу канала о возврате баллов.")}:
        return None
    bases = []
    basis = row.get("basis_messages", ()) if row["source"] == "autonomous" else ()
    basis = basis if isinstance(basis, (list, tuple)) else ()
    for item in basis[:BASIS_LIMIT]:
        if isinstance(item, dict):
            bases.append({"author": safe_history_text(str(item.get("author", ""))),
                          "text": safe_history_text(str(item.get("text", "")))[:BASIS_TEXT_LIMIT]})
    return {"time": sent_at, "sent_at": datetime.fromtimestamp(sent_at, timezone.utc).isoformat(),
            "recipient": {"login": row["target"], "user_id": row.get("user_id", "")},
            "text": safe_history_text(text), "basis_messages": bases}


def personal_reply(row, login, user_id, now, *, channel=""):
    if not isinstance(row, dict) or row.get("source") != "autonomous":
        return None
    value = public_reply(row, now, channel=channel)
    if value is None or not same_recipient(row.get("target", ""), row.get("user_id", ""), login, user_id):
        return None
    return value


def recent_personal_replies(rows, login, user_id, now, *, channel=""):
    matches = [value for row in rows if (value := personal_reply(row, login, user_id, now, channel=channel))]
    return tuple(sorted(matches, key=lambda value: value["time"], reverse=True)[:PERSONAL_REPLY_LIMIT])


def _reference(question, candidates, profiles=(), reply_id=""):
    """Resolve only explicit public references; ambiguity yields no cross-viewer data."""
    if reply_id:
        return [row for row in candidates if row.get("message_id") == reply_id]
    if not references_public_reply(question):
        return []
    people = [{"login": p["login"], "user_id": p.get("user_id", ""), "aliases": p.get("aliases", ())}
              for p in profiles]
    for row in candidates:
        person = {"login": row["target"], "user_id": row.get("user_id", ""), "aliases": ()}
        if not any(p["login"] == person["login"] and p["user_id"] == person["user_id"] for p in people):
            people.append(person)
    mentions = recognize_mentions(people, question)
    if mentions.ambiguous:
        return []
    matched = [people[m.index] for m in mentions.matches if not m.method.startswith("опечатка")]
    identities = {(p["user_id"] or "login:" + p["login"].casefold()) for p in matched}
    if not identities and re.search(r'[«"][^»"]{8,240}[»"]', question):
        return candidates
    if len(identities) != 1:
        return []
    person = matched[0] if matched else None
    return [row for row in candidates if person and same_recipient(row["target"], row.get("user_id", ""),
                                                                  person["login"], person["user_id"])]


def for_reward(store, rows, channel, login, user_id, now, *, question="", profiles=(), reply_id=""):
    """Called only by the Twitch reward worker, never offline testing or the GUI."""
    candidates = list(rows)
    if store is not None:
        try:
            candidates.extend(store.recent_personal_replies(channel, login, user_id, now))
        except Exception:
            # Optional history is best effort; never expose a database error/body.
            pass
    personal = recent_personal_replies(candidates, login, user_id, now, channel=channel)
    if not question and not reply_id:
        return personal
    if store is not None:
        try:
            candidates.extend(store.recent_public_replies(channel, now))
        except Exception:
            pass
    # First whitelist public fields and remove copies from memory/SQLite.
    public, seen = [], []
    for row in candidates:
        value = public_reply(row, now, channel=channel)
        if value is None:
            continue
        recipient = value["recipient"]
        identity = recipient["user_id"] or recipient["login"].casefold()
        if any(saved_identity == identity and saved_text == value["text"] and abs(saved_time - value["time"]) <= 2
               for saved_time, saved_identity, saved_text in seen):
            continue
        seen.append((value["time"], identity, value["text"]))
        public.append({"source": row["source"], "status": "sent", "channel": channel,
                       "target": recipient["login"], "user_id": recipient["user_id"], "time": value["time"],
                       "text": value["text"], "basis_messages": value["basis_messages"],
                       "message_id": row.get("message_id", "")})
    referenced = _reference(question, public, profiles, reply_id)
    # A literal excerpt can disambiguate several replies to the same person.
    quotes = re.findall(r'[«"]([^»"]{8,240})[»"]', question)
    if quotes:
        referenced = [row for row in referenced if any(quote.casefold() in row["text"].casefold() for quote in quotes)]
    if len(referenced) == 1:
        value = public_reply(referenced[0], now, channel=channel)
        value["scope"] = "public_reference"
        combined = [*personal, value]
    else:
        combined = list(personal)
    unique = {}
    for value in combined:
        unique[(value["time"], value["text"])] = value
    return tuple(sorted(unique.values(), key=lambda row: row["time"]))[:PUBLIC_REPLY_LIMIT]
