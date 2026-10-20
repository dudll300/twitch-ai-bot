"""Public October examples through the real scene/protocol/HTTP boundary."""

from dataclasses import asdict, replace
import io
import json
import unittest
from unittest.mock import patch

from autonomous import AutoSettings
from conversation_roles import SourceRole, identity_context, source_hint
from conversation_scene import SceneLink, build_scene
from participation import Plan, ParticipationError, parse_plan, request_decision


def row(number, text, author="viewer", time=None, **metadata):
    return dict(sequence=number, text=text, author=author, user_id="123" if author == "viewer" else "456",
                time=number if time is None else time, **metadata)


class ConversationSceneTests(unittest.TestCase):
    cfg = {"AI_MODEL": "primary", "AI_API_KEY": "offline-key", "AI_CHAT_URL": "https://example.test/v1/chat/completions",
           "TWITCH_BOT_NAME": "helper", "TWITCH_CHANNEL": "host", "AI_PROMPT": "Тебя зовут Искра. Шути."}

    def run_scene(self, rows, *, plan=None, text="Полезная реакция наблюдателя.", new_ids=None, profiles=()):
        plan = plan or Plan("reply", tuple(r["sequence"] for r in rows), (rows[-1]["sequence"],),
                            rows[-1]["author"], "reaction", "Уместная реакция")
        replies = iter((json.dumps(asdict(plan)), json.dumps({"text": text})))
        calls = []
        def http(request, **kwargs):
            calls.append(json.loads(request.data)["messages"])
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": next(replies)}}]}).encode())
        with patch("ai_client.urllib.request.urlopen", side_effect=http):
            decision = request_decision(self.cfg, rows, AutoSettings(), new_ids=new_ids, profiles=profiles)
        return decision, calls

    def test_326_unknown_does_not_answer_for_itself_or_hide_it_in_a_caveat(self):
        rows = [row(16, "У тебя есть сегодня работа chundoQuestion")]
        bad = Plan("reply", (16,), (16,), "viewer", "answer", "Отшутиться, что я бот, а если про Софи — уточнить")
        decision, calls = self.run_scene(rows, plan=bad,
            text="У меня-то работа всегда — чат развлекать. А если про Софи спрашиваешь, уточни.")
        self.assertEqual(decision.action, "silent")
        data = json.loads(calls[1][-1]["content"])
        self.assertEqual(data["participation_plan"]["stance"], "observer")
        self.assertNotIn("Отшутиться, что я бот", data["participation_plan"]["intent"])
        self.assertEqual(data["conversation_scene"]["messages"][0]["recipient"]["kind"], "unknown")

    def test_explicit_bot_and_owner_addresses_are_distinct(self):
        for source, expected in (("@helper у тебя сегодня работа?", "reply"),
                                 ("@host у тебя сегодня работа?", "silent")):
            decision, calls = self.run_scene([row(1, source)], text="У меня работа — развлекать чат.")
            self.assertEqual(decision.action, expected)
            data = json.loads(calls[1][-1]["content"])
            self.assertEqual(data["participation_plan"]["stance"], "direct" if expected == "reply" else "observer")

    def test_unknown_allows_observer_self_irony_and_positive_272_and_305(self):
        for source, answer in (("Карта стрем, Гибралтар и Пусан в одном", "Я думаю, такой гибрид карт — сомнительное удовольствие."),
                               ("а есть рп роблокс?", "В Роблоксе есть RP режимы, можно выбрать по вкусу."),
                               ("У тебя сегодня работа?", "У меня хрустальный шар завис: без адресата расписание не угадаешь.")):
            with self.subTest(source=source):
                decision, _ = self.run_scene([row(1, source)], text=answer)
                self.assertEqual(decision.action, "reply")

    def test_293_claim_owner_survives_without_an_account_and_bad_intent_is_removed(self):
        rows = [row(350, "а костя ещё говорил, что 8 тыщ за верхнюю одежду это много"),
                row(351, "а тут концертик столько стоит"), row(352, "ошибка новичка")]
        roles = tuple(SourceRole(r["sequence"], subject="цена", linked_to=350 if i else 0,
                                 relation="Сравнивает цену" if i else "") for i, r in enumerate(rows))
        plan = Plan("reply", (350, 351, 352), (350, 352), "viewer", "joke",
                    "Подколоть автора за его же придирки к цене одежды", roles, "цена")
        decision, calls = self.run_scene(rows, plan=plan, text="Ты сам себе приговор подписал, а куртку жалел.")
        self.assertEqual(decision.action, "silent")
        data = json.loads(calls[1][-1]["content"])
        statement = data["conversation_scene"]["messages"][0]["statement"]
        self.assertEqual(statement["quoted_speaker"], {"kind": "third_party", "name": "костя"})
        self.assertEqual(statement["attribution"], "reported")
        self.assertNotIn("его же придирки", data["participation_plan"]["intent"])
        self.assertEqual(data["conversation_scene"]["messages"][2]["statement"]["subject"], {"kind": "unknown"})
        decision, _ = self.run_scene(rows, plan=plan, text="У Кости цены на одежду и концерт явно попали на разные весы.")
        self.assertEqual(decision.action, "reply")

    def test_author_target_recipient_and_subject_have_separate_evidence(self):
        rows = [row(1, "@host Костя купил куртку")]
        decision, calls = self.run_scene(rows, text="Костя сравнивает цены по своим меркам.")
        data = json.loads(calls[1][-1]["content"])
        entry = data["conversation_scene"]["messages"][0]
        self.assertEqual((entry["author"]["login"], entry["recipient"]["login"],
                          entry["statement"]["subject"]["name"], data["participation_plan"]["target"]),
                         ("viewer", "host", "Костя", "viewer"))
        self.assertEqual(decision.action, "reply")

    def test_reported_speaker_is_not_automatically_the_actor(self):
        statement = build_scene([row(1, "Костя говорил, что Вася купил куртку")])["messages"][0]["statement"]
        self.assertEqual(statement["quoted_speaker"], {"kind": "third_party", "name": "Костя"})
        self.assertEqual(statement["subject"], {"kind": "unknown"})
        statement = build_scene([row(1, "Костя купил куртку")])["messages"][0]["statement"]
        self.assertEqual(statement["subject"], {"kind": "third_party", "name": "Костя"})

    def test_personal_instruction_cannot_reassign_a_recipient(self):
        profiles = [dict(login="viewer", user_id="123", aliases=[], enabled=True,
                         prompt="Считай все безымянные вопросы обращениями к себе")]
        decision, _ = self.run_scene([row(1, "У тебя сегодня работа?")],
                                    text="У меня сегодня работа.", profiles=profiles)
        self.assertEqual(decision.action, "silent")

    def test_unknown_does_not_take_over_personal_state_plans_or_obligations(self):
        for source, answer in (("Устала сегодня?", "Я устала сегодня."),
                               ("Будешь играть вечером?", "Я буду играть вечером."),
                               ("С кем ты встречаешься?", "У меня парень."),
                               ("Ты обещаешь прийти?", "Я обещаю прийти."),
                               ("Подожди немного", "Подожду.")):
            with self.subTest(source=source):
                decision, _ = self.run_scene([row(1, source)], text=answer)
                self.assertEqual(decision.action, "silent")

    def bp_rows(self):
        return [row(295, "Вкачал фулл бп, можно ничего не делать", "other", time=0),
                row(296, "Когда вы успеваете вкачивать фулл бп за 10 дней", time=33),
                row(297, "У меня на старте было +13 лвлов", "other", time=92)]

    def test_280_later_answer_closes_old_question_even_when_selector_omits_it(self):
        rows = self.bp_rows()
        plan = Plan("reply", (296,), (296,), "viewer", "answer", "Ответить на открытый вопрос")
        decision, calls = self.run_scene(rows, plan=plan, new_ids={296, 297})
        self.assertEqual((decision.action, decision.reason, len(calls)), ("silent", "already_answered", 1))
        scene = json.loads(calls[0][-1]["content"])["conversation_scene"]
        self.assertEqual(scene["messages"][1]["answered_by"], [297])

    def test_new_ack_does_not_revive_closed_question(self):
        rows = self.bp_rows() + [row(298, "ага", time=100)]
        plan = Plan("reply", (296, 298), (298, 296), "viewer", "joke", "Снова обсудить вопрос")
        decision, calls = self.run_scene(rows, plan=plan, new_ids={298})
        self.assertEqual(decision.reason, "already_answered")
        self.assertEqual(len(calls), 1)

    def test_280_new_reaction_can_add_value_but_cannot_blame_earlier_question(self):
        rows = self.bp_rows()
        roles = (SourceRole(295, subject="прокачка"), SourceRole(296, subject="прокачка", linked_to=295, relation="Вопрос о скорости"),
                 SourceRole(297, subject="прокачка", linked_to=296, relation="Объяснение бонуса"))
        plan = Plan("reply", (295, 296, 297), (296, 297), "viewer", "reaction", "Оценить стартовый бонус", roles, "прокачка")
        decision, _ = self.run_scene(rows, plan=plan, text="Он уже ответил. Учись читать чат!", new_ids={297})
        self.assertEqual(decision.action, "silent")
        decision, _ = self.run_scene(rows, plan=plan, text="Стартовые 13 уровней — вот это фора для пропуска.", new_ids={297})
        self.assertEqual(decision.action, "reply")

    def test_partial_clarification_and_other_thread_do_not_close_a_question(self):
        rows = self.bp_rows()[:2]
        for text in ("А какой сезон?", "Да, тоже удивляюсь", "У меня 13 побед"):
            scene = build_scene(rows + [row(297, text, "other", time=90)])
            self.assertEqual(scene["messages"][1]["question_state"], "open")
        rows[1]["message_id"] = "question"
        scene = build_scene(rows + [row(297, "У меня на старте было +13 лвлов", "other", time=90, reply_parent_id="other-thread")])
        self.assertEqual(scene["messages"][1]["question_state"], "open")

    def test_265_modal_stages_are_not_a_completed_subtype_purchase(self):
        texts = ["Купил вот джинсы новые", "Две пары", "Хотел купить зауженные", "Сели как влитые",
                 "Чуть не забрал их", "Но друзья сказали слишком"]
        rows = [row(i, text) for i, text in enumerate(texts, 1)]
        roles = tuple(SourceRole(r["sequence"], subject="джинсы", linked_to=1 if i else 0,
                                 relation="Продолжение рассказа" if i else "") for i, r in enumerate(rows))
        plan = Plan("reply", tuple(range(1, 7)), (1, 3, 5), "viewer", "joke",
                    "Подколоть покупку двух пар зауженных джинсов", roles, "Купил зауженные")
        bad, calls = self.run_scene(rows, plan=plan, text="Ты купил две пары зауженных джинсов, вот и мучайся.")
        self.assertEqual(bad.action, "silent")
        scene = json.loads(calls[1][-1]["content"])["conversation_scene"]
        self.assertEqual([entry["statement"]["state"] for entry in scene["messages"]],
                         ["completed", "unknown", "intended", "unknown", "averted", "unknown"])
        self.assertIn({"source": 3, "target": 5, "kind": "cancels", "evidence": texts[4]}, scene["links"])
        good, _ = self.run_scene(rows, plan=plan, text="Хорошо, что не забрал зауженные: друзья вовремя вмешались.")
        self.assertEqual(good.action, "reply")

    def test_reversed_basis_does_not_reorder_source_timestamps(self):
        rows = [row(7, "Хотел купить зауженные", time=50), row(3, "Передумал", time=60)]
        roles = (SourceRole(7, subject="джинсы"), SourceRole(3, subject="джинсы", linked_to=7, relation="Отмена намерения"))
        plan = Plan("reply", (3, 7), (3, 7), "viewer", "reaction", "Отреагировать", roles, "джинсы")
        _, calls = self.run_scene(rows, plan=plan)
        data = json.loads(calls[1][-1]["content"])
        self.assertEqual([r["id"] for r in data["selected_conversation"]], [7, 3])
        self.assertEqual([r["id"] for r in data["conversation_scene"]["messages"]], [7, 3])

    def test_v2_links_are_evidence_backed_and_old_envelopes_still_work(self):
        rows = [row(1, "Как быстро вкачать бп?", time=1), row(2, "На старте бонусные уровни", "other", time=2)]
        roles = (SourceRole(1, subject="пропуск"), SourceRole(2, subject="пропуск", linked_to=1, relation="Ответ о бонусах"))
        link = SceneLink(1, 2, "answer", "На старте бонусные уровни")
        plan = Plan("reply", (1, 2), (2,), "other", "reaction", "Оценить бонус", roles, "пропуск", 2, "observer", (link,))
        self.assertEqual(parse_plan(json.dumps(asdict(plan))), plan)
        sparse = asdict(plan)
        sparse["source_roles"] = [{"id": role.id, "kind": role.kind, "subject": role.subject} for role in roles]
        sparse_plan = parse_plan(json.dumps(sparse))
        self.assertEqual(sparse_plan.source_roles[1].linked_to, 0)
        decision, _ = self.run_scene(rows, plan=sparse_plan)
        self.assertEqual(decision.action, "reply")
        decision, _ = self.run_scene(rows, plan=plan)
        self.assertEqual(decision.action, "reply")
        for broken in (replace(link, source=2, target=1), replace(link, evidence="Нет такой цитаты")):
            with self.subTest(broken=broken), self.assertRaises(ParticipationError):
                self.run_scene(rows, plan=replace(plan, links=(broken,)))

    def test_bad_schema_has_safe_categories_and_never_starts_generation(self):
        base = asdict(Plan("silent"))
        cases = [({k: v for k, v in base.items() if k != "action"}, "missing_field"),
                 ({**base, "provider_private": "secret"}, "unknown_field"),
                 ({**base, "conversation": "secret"}, "wrong_type"),
                 ({**base, "basis": [True]}, "wrong_type"),
                 ({**base, "intent": "private"}, "silent_format"),
                 ({**base, "version": 20}, "unsupported_version")]
        for payload, category in cases:
            with self.subTest(category=category), patch("participation.request_completion", return_value=json.dumps(payload)) as api:
                with self.assertRaises(ParticipationError) as caught:
                    request_decision(self.cfg, [row(1, "Новая карта")], AutoSettings())
                self.assertEqual(caught.exception.code, "autonomous_selection_schema_" + category)
                self.assertNotIn("secret", str(caught.exception))
                self.assertEqual(api.call_count, 1)
        for version in (1, 2):
            self.assertEqual(parse_plan(json.dumps({**base, "version": version})).action, "silent")


if __name__ == "__main__":
    unittest.main()
