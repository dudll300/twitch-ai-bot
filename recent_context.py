"""Bounded, recipient-scoped conversation data shared by live reward requests."""

from datetime import datetime, timezone
import math
import re

from privacy import safe_history_text

PERSONAL_REPLY_SECONDS = 30 * 60
PERSONAL_REPLY_LIMIT = 1
BASIS_LIMIT = 4
BASIS_TEXT_LIMIT = 240


def same_recipient(saved_login, saved_id, login, user_id):
    if saved_id and user_id:
        return saved_id == user_id
    # A saved stable identity cannot be authenticated by a login-only request.
    if saved_id:
        return False
    return bool(saved_login and saved_login.casefold() == login.casefold())


def personal_reply(row, login, user_id, now, *, channel=""):
    if (row.get("source") != "autonomous" or row.get("status") != "sent"
            or row.get("mode") == "preview" or not row.get("target")
            or (channel and row.get("channel", "").casefold() != channel.casefold())):
        return None
    sent_at = row.get("time")
    if (type(sent_at) not in (int, float) or not math.isfinite(sent_at)
            or not 0 <= now - sent_at <= PERSONAL_REPLY_SECONDS
            or not same_recipient(row["target"], row.get("user_id", ""), login, user_id)):
        return None
    text = row.get("text", "")
    tag = re.match(r"^@([a-zA-Z0-9_]{1,25})\s+", text)
    if not tag or tag[1].casefold() != row["target"].casefold():
        return None
    bases = []
    for item in row.get("basis_messages", ())[:BASIS_LIMIT]:
        if isinstance(item, dict):
            bases.append({"author": safe_history_text(str(item.get("author", ""))),
                          "text": safe_history_text(str(item.get("text", "")))[:BASIS_TEXT_LIMIT]})
    return {"time": sent_at, "sent_at": datetime.fromtimestamp(sent_at, timezone.utc).isoformat(),
            "recipient": {"login": row["target"], "user_id": row.get("user_id", "")},
            "text": safe_history_text(text), "basis_messages": bases}


def recent_personal_replies(rows, login, user_id, now, *, channel=""):
    matches = [value for row in rows if (value := personal_reply(row, login, user_id, now, channel=channel))]
    return tuple(sorted(matches, key=lambda value: value["time"], reverse=True)[:PERSONAL_REPLY_LIMIT])


def for_reward(store, rows, channel, login, user_id, now):
    """Called only by the Twitch reward worker, never offline testing or the GUI."""
    candidates = list(rows)
    if store is not None:
        try:
            candidates.extend(store.recent_personal_replies(channel, login, user_id, now))
        except Exception:
            # Optional history is best effort; never expose a database error/body.
            pass
    return recent_personal_replies(candidates, login, user_id, now, channel=channel)
