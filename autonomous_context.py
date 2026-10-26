"""Bounded immutable public evidence for the existing autonomous review call."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math

from conversation_roles import grounded_roles, identity_context, source_hint
from conversation_scene import build_scene
from privacy import application_content, application_json, protocol_id, safe_history_text


CONTEXT_LIMIT = 24000
MESSAGE_LIMIT = 8


@dataclass(frozen=True)
class AutonomousReviewContext:
    wire: str
    parts: tuple[str, ...]

    def request_content(self, candidate, kind, target):
        return application_content(json.dumps(dict(candidate=candidate, kind=kind, target=target,
            public_context=json.loads(self.wire)), ensure_ascii=False),
            (candidate, kind, target, *self.parts))


def capture_autonomous_context(cfg, decision, messages, generated=None):
    """Use the selected IDs, but take all message facts from the frozen batch.

    Never send request_messages, personal prompts, or unrelated neighboring chat.
    Injected/legacy generators without scene metadata retain their explicit basis.
    """
    from ai_client import redact_secret
    from participation import parse_plan

    generated = generated if isinstance(generated, dict) else {}
    selected_ids = set(decision.basis)
    if "conversation" in generated:
        recorded = generated["conversation"]
        if not isinstance(recorded, (list, tuple)) or not 0 < len(recorded) <= MESSAGE_LIMIT:
            raise ValueError("autonomous_review_context")
        ids = [row.get("sequence") for row in recorded if isinstance(row, dict)]
        if (len(ids) != len(recorded) or any(type(value) is not int for value in ids)
                or len(set(ids)) != len(ids) or not set(decision.basis) <= set(ids)):
            raise ValueError("autonomous_review_context")
        selected_ids = set(ids)
    selected = [dict(row) for row in messages if row["sequence"] in selected_ids]
    if not 0 < len(selected) <= MESSAGE_LIMIT or {row["sequence"] for row in selected} != selected_ids:
        raise ValueError("autonomous_review_context")

    def clean(value, limit):
        return safe_history_text(redact_secret(str(value), cfg.get("AI_API_KEY", "")))[:limit]

    def stamp(value):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("autonomous_review_context")
        return datetime.fromtimestamp(value, timezone.utc).isoformat()

    # Text redaction precedes bounding. Protocol metadata gets only typed ID
    # exemptions; arbitrary numeric content is still subject to privacy checks.
    public = []
    for row in selected:
        row["author"] = clean(row["author"], 25)
        row["text"] = clean(row["text"], 500)
        public.append(dict(id=row["sequence"], time=stamp(row["time"]),
            author=dict(login=row["author"], user_id=protocol_id(row.get("user_id", ""))),
            text=row["text"], reply_parent_id=protocol_id(row.get("reply_parent_id", ""), message=True),
            reply_parent_login=clean(row.get("reply_parent_login", ""), 25),
            thread_id=protocol_id(row.get("thread_id", ""), message=True)))
    identities = identity_context(cfg, selected)
    roles = tuple(source_hint(row, identities) for row in selected)
    links = ()
    if isinstance(generated.get("participation_plan"), dict):
        plan = parse_plan(json.dumps(generated["participation_plan"], ensure_ascii=False))
        if (set(plan.conversation) != selected_ids or tuple(plan.basis) != tuple(decision.basis)
                or plan.target != decision.target or plan.action != "reply"):
            raise ValueError("autonomous_review_context")
        roles = grounded_roles(plan, selected, identities)
        links = plan.links
    scene = build_scene(selected, roles, links)
    wire = application_json(dict(bot_login=clean(cfg.get("TWITCH_BOT_NAME", ""), 25),
        owner_login=clean(cfg.get("TWITCH_CHANNEL", ""), 25),
        basis=list(decision.basis), purpose=decision.reason, messages=public, conversation_scene=scene))
    if len(wire) > CONTEXT_LIMIT:
        # Never cut an attributed JSON scene in the middle or approve a fragment.
        raise ValueError("autonomous_review_context")
    return AutonomousReviewContext(str(wire), wire.parts)
