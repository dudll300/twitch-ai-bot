"""Immutable, bounded public evidence captured before reward generation."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import re

from privacy import application_content, application_json, protocol_id, safe_history_text
from recent_context import PERSONAL_REPLY_LIMIT, BASIS_LIMIT, BASIS_TEXT_LIMIT

PUBLIC_CONTEXT_LIMIT = 5000
REWARD_PAIR_LIMIT = 3


@dataclass(frozen=True)
class RewardPublicContext:
    question: str
    wire: str
    parts: tuple[str, ...]
    metadata: tuple[tuple[str, int | bool], ...]

    def request_content(self, candidate, kind, target):
        payload = dict(candidate=candidate, kind=kind, target=target,
                       current_question=self.question, public_context=json.loads(self.wire))
        # Keep the trusted metadata exemptions established by the builder.
        return application_content(json.dumps(payload, ensure_ascii=False),
                                  (candidate, kind, target, self.question, *self.parts))


def capture_reward_context(cfg, login, user_id, question, history=(), history_times=(),
                           recent=(), history_recipients=()):
    # Runtime import avoids the ai_client -> safety -> reward_context cycle.
    from ai_client import redact_secret
    def clean(value, limit):
        return safe_history_text(redact_secret(str(value), cfg.get('AI_API_KEY', '')))[:limit]
    def actor(name):
        return dict(login=clean(name, 25), user_id=protocol_id(user_id))
    def stamp(value):
        if type(value) not in (int, float) or not math.isfinite(value):
            return None
        try:
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            return None
    pairs = history[-10:]
    times = history_times[-len(pairs):] if pairs and len(history_times) >= len(pairs) else ()
    recipients = history_recipients[-len(pairs):] if pairs and len(history_recipients) >= len(pairs) else ()
    groups, seen = [], set()
    for index in range(len(pairs)-1, max(-1, len(pairs)-REWARD_PAIR_LIMIT-1), -1):
        q, a = pairs[index]
        if (q, a) in seen:
            continue
        seen.add((q, a))
        name = recipients[index] if recipients else ''  # legacy origin is unknown
        when = times[index] if times else None
        groups.append((when if stamp(when) else float('-inf'), index, dict(
            source='sent_reward', sent_at=stamp(when),
            question=dict(role='viewer', author=actor(name), text=clean(q, 400)),
            answer=dict(role='bot', author=clean(cfg.get('TWITCH_BOT_NAME', ''), 25),
                        recipient=actor(name), text=clean(a, 450)))))
    previous = {answer.strip() for _, answer in pairs}
    for index, row in enumerate(recent[:PERSONAL_REPLY_LIMIT]):
        text = row['text']
        body = re.sub(r'^@[a-zA-Z0-9_]{1,25}\s+', '', text)
        if body.strip() in previous or text.strip() in previous:
            continue
        recipient = row['recipient']
        groups.append((row['time'], len(pairs)+index, dict(
            source='sent_autonomous', sent_at=row['sent_at'],
            answer=dict(role='bot', author=clean(cfg.get('TWITCH_BOT_NAME', ''), 25),
                        recipient=dict(login=clean(recipient['login'], 25),
                                       user_id=protocol_id(recipient['user_id'])), text=clean(text, 450)),
            basis_messages=[dict(role='viewer', author=clean(item['author'], 25),
                                 text=clean(item['text'], BASIS_TEXT_LIMIT))
                            for item in row['basis_messages'][:BASIS_LIMIT]])))
    groups.sort(key=lambda item: (item[0], item[1]))
    truncated = len(pairs) > REWARD_PAIR_LIMIT
    while True:
        content = application_json([group[2] for group in groups])
        if len(content) <= PUBLIC_CONTEXT_LIMIT:
            break
        groups.pop(0)
        truncated = True
    metadata = dict(reward_pairs=sum(g[2]['source']=='sent_reward' for g in groups),
                    autonomous_replies=sum(g[2]['source']=='sent_autonomous' for g in groups),
                    truncated=truncated)
    return RewardPublicContext(clean(question, 400), str(content), content.parts, tuple(metadata.items()))
