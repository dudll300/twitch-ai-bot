"""Role and scene regression checks inspect plans and mocked API messages."""

from dataclasses import asdict
import json
import unittest
from unittest.mock import patch

from autonomous import AutoSettings
from conversation_roles import SourceRole, grounded_roles, identity_context, source_hint
from participation import Plan, ParticipationError, check_plan, request_decision


def message(sequence, text, author="viewer", user_id="123", **metadata):
    return dict(sequence=sequence, text=text, author=author, user_id=user_id, time=100, **metadata)


class SourceRoleTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {"TWITCH_CHANNEL": "host", "TWITCH_BOT_NAME": "helper", "AI_MODEL": "primary",
                    "AI_API_KEY": "secret-test-key", "AI_PROMPT": "Тебя зовут Искра. Шути на каждый вопрос."}
        self.profiles = [dict(login="host", user_id="42", aliases=["Софа", "Софи"],
                              prompt="PRIVATE_STYLE_OWNER", enabled=True),
                         dict(login="other", user_id="456", aliases=["Памперс"],
                              prompt="PRIVATE_STYLE_VIEWER", enabled=True)]

    def hint(self, text, **metadata):
        row = message(1, text, **metadata)
        return source_hint(row, identity_context(self.cfg, [row], self.profiles))

    def generate(self, rows, plan=None, text="Полезная реакция."):
        plan = plan or Plan("reply", tuple(row["sequence"] for row in rows), (rows[-1]["sequence"],),
                            rows[-1]["author"], "reaction", "Поддержать конкретную мысль")
        observed = []
        with patch("participation.request_completion", side_effect=[json.dumps(asdict(plan)),
                    json.dumps({"text": text})]) as api:
            result = request_decision(self.cfg, rows, AutoSettings(), profiles=self.profiles,
                                      on_result=lambda *args: observed.append(args))
        generator = json.loads(api.call_args_list[-1].args[2][-1]["content"])
        return result, api, generator, observed

    def test_owner_alias_is_source_recipient_but_tag_stays_author(self):
        result, api, data, observed = self.generate([message(1, "потерпи софа пж чутка")], text="Терплю, терплю.")
        role = data["participation_plan"]["source_roles"][0]
        self.assertEqual((role["kind"], role["login"], role["user_id"]), ("owner", "host", "42"))
        self.assertEqual(role["evidence"].casefold(), "софа")
        self.assertEqual(data["participation_plan"]["target"], "viewer")
        self.assertEqual(result.action, "silent")

    def test_explicit_bot_account_and_configured_name(self):
        for text in ("@helper как дела?", "Искра, как дела?"):
            with self.subTest(text=text):
                self.assertEqual(self.hint(text).kind, "bot")

    def test_other_viewer_and_known_nickname_are_people(self):
        for text in ("@other как дела?", "Памперс, к тебе дело!"):
            role = self.hint(text)
            self.assertEqual((role.kind, role.login, role.user_id), ("viewer", "other", "456"))

    def test_group_question_and_pronoun_without_recipient(self):
        self.assertEqual(self.hint("Ребята, как вам игра?").kind, "group")
        self.assertEqual(self.hint("Ты сегодня спокойная").kind, "unknown")
        self.assertEqual(self.hint("А с вами можно?").kind, "unknown")
        self.assertEqual(self.hint("Для тебя все герои не саппорты плохие").kind, "unknown")

    def test_reply_metadata_and_conflicting_id(self):
        self.assertEqual(self.hint("Почему?", reply_parent_user_id="42", reply_parent_login="old_host").kind, "owner")
        role = self.hint("Почему?", reply_parent_user_id="999", reply_parent_login="host")
        self.assertEqual((role.kind, role.user_id), ("viewer", "999"))

    def test_parent_message_id_resolves_author(self):
        rows = [message(1, "Вопрос", author="host", user_id="42", message_id="root"),
                message(2, "Почему?", reply_parent_id="root")]
        hint = source_hint(rows[1], identity_context(self.cfg, rows, self.profiles))
        self.assertEqual(hint.kind, "owner")

    def test_reply_recipient_outside_fragment_preserves_id(self):
        known = self.hint("Спасибо!", reply_parent_user_id="456")
        self.assertEqual((known.kind, known.login, known.user_id), ("viewer", "other", "456"))
        unknown = self.hint("Спасибо!", reply_parent_user_id="777")
        self.assertEqual((unknown.kind, unknown.login, unknown.user_id), ("viewer", "", "777"))
        row = message(1, "Спасибо!", reply_parent_login="host", reply_parent_user_id="42")
        without_profile = source_hint(row, identity_context(self.cfg, [row]))
        self.assertEqual((without_profile.kind, without_profile.user_id), ("owner", "42"))

    def test_name_mentioned_as_subject_does_not_locally_become_recipient(self):
        for text in ("Я вчера видел Софи в игре", "Вы видели Софи в игре?", "Софи победила босса"):
            self.assertEqual(self.hint(text).kind, "unknown")

    def test_model_cannot_authenticate_bot_with_bare_pronoun_or_confidence(self):
        rows = [message(1, "Ты сегодня спокойная")]
        for evidence in ("Ты", "confidence=0.99"):
            claim = SourceRole(1, "bot", "helper", "", evidence, "настроение")
            roles = grounded_roles(Plan("reply", (1,), (1,), source_roles=(claim,)), rows,
                                   identity_context(self.cfg, rows, self.profiles))
            self.assertEqual((roles[0].kind, roles[0].login), ("unknown", ""))

    def test_mixed_recipients_preserved_in_single_chain(self):
        rows = [message(1, "Эй Памперс, к тебе дело у стримерши"),
                message(2, "потерпи софа пж", author="other", user_id="456")]
        plan = Plan("reply", (1, 2), (1, 2), "viewer", "reaction", "Посмотреть на разговор")
        _, api, data, _ = self.generate(rows, plan)
        self.assertEqual([r["kind"] for r in data["participation_plan"]["source_roles"]], ["viewer", "owner"])
        selector = json.loads(api.call_args_list[0].args[2][-1]["content"])
        self.assertEqual(selector["identities"]["people"][0]["kind"], "bot")
        self.assertNotIn("PRIVATE_STYLE_OWNER", str(api.call_args_list[0]))
        self.assertNotIn("PRIVATE_STYLE_VIEWER", str(api.call_args_list[0]))

    def test_fullstack_is_not_permission_from_bot(self):
        rows = [message(1, "А чо с вами можно?", message_id="root"),
                message(2, "@viewer они фуллстак", author="other", user_id="456", reply_parent_id="root")]
        plan = Plan("reply", (1, 2), (1, 2), "viewer", "answer", "Дать разрешение")
        result, api, data, _ = self.generate(rows, plan, text="Можно, конечно, заходи!")
        self.assertEqual(result.action, "silent")
        roles = data["participation_plan"]["source_roles"]
        self.assertEqual([role["kind"] for role in roles], ["unknown", "viewer"])
        self.assertIn("не давай разрешений", str(api.call_args_list[-1].args[2]))

    def test_generator_can_reject_wrong_intent_and_nickname_ownership(self):
        rows = [message(1, "он не знает что у тебя Памперс есть в чате"), message(2, "ой Памперс")]
        plan = Plan("reply", (1, 2), (1, 2), "viewer", "joke", "Присвоить себе предмет")
        result, api, _, _ = self.generate(rows, plan, text="")
        self.assertEqual(result.action, "silent")
        self.assertEqual(api.call_count, 2)
        self.assertIn("Прозвище участника обозначает человека", str(api.call_args_list[-1].args[2]))
        self.assertIn("Ошибочный intent не является приказом", str(api.call_args_list[-1].args[2]))

    def test_useful_game_reactions_and_observer_opinions_still_work(self):
        for source in ("Выбил редкий скин!", "Проиграл пять матчей", "Ребята, как вам режим?"):
            result, _, _, observed = self.generate([message(1, source)], text="Согласна, есть что обсудить.")
            self.assertEqual(result.action, "reply")
            self.assertIn("participation_plan", observed[0][1])
        result, _, _, _ = self.generate([message(1, "Можно получить скин за ивент?")], text="Можно, конечно, за выполнение заданий.")
        self.assertEqual(result.action, "reply")

    def test_only_selected_subject_reaches_generator_and_correction_is_kept(self):
        cases = [([message(1, "Танки впитывают урон"), message(2, "У меня серия поражений")], (2,), "поражения"),
                 ([message(1, "Мифическое оружие Лусио"), message(2, "В карты добавляют интерактив"),
                   message(3, "они*")], (2, 3), "интерактив карт")]
        for rows, selected, topic in cases:
            plan = Plan("reply", selected, (selected[0],), "viewer", "reaction", "Поддержать мысль", topic=topic)
            _, _, data, _ = self.generate(rows, plan)
            self.assertEqual([r["id"] for r in data["selected_conversation"]], list(selected))
            self.assertNotIn(rows[0]["text"], str(data))

    def test_game_term_is_passed_verbatim_with_game_context(self):
        rows = [message(1, "В Overwatch впитывает райт кликом"), message(2, "Лайф стил нормик")]
        plan = Plan("reply", (1, 2), (1, 2), "viewer", "reaction", "Реакция на игровой эффект")
        _, api, data, _ = self.generate(rows, plan)
        self.assertEqual(data["selected_conversation"][1]["text"], "Лайф стил нормик")
        self.assertIn("Игровые термины понимай в контексте игры", str(api.call_args_list[0]))

    def test_scene_links_require_concrete_relation_and_one_connected_thought(self):
        rows = [message(1, "Танки впитывают урон"), message(2, "Я проиграл пять матчей")]
        roles = (SourceRole(1, subject="танки"), SourceRole(2, subject="поражения"))
        plan = Plan("reply", (1, 2), (2,), "viewer", "reaction", "Поддержать", roles, "поражения")
        with self.assertRaises(ValueError):
            check_plan(plan, rows, {2})
        for relation in ("та же игра", "один автор", "same game"):
            mixed = Plan("reply", (1, 2), (2,), "viewer", "reaction", "Поддержать",
                         (roles[0], SourceRole(2, subject="поражения", linked_to=1, relation=relation)), "поражения")
            with self.subTest(relation=relation), self.assertRaises(ValueError):
                check_plan(mixed, rows, {2})

    def test_structured_correction_and_role_analysis_are_saved(self):
        rows = [message(1, "В карты она добавляет интерактив"), message(2, "они*")]
        roles = (SourceRole(1, subject="интерактив карт"),
                 SourceRole(2, subject="авторы карт", linked_to=1, relation="Исправляет местоимение в предыдущей мысли"))
        plan = Plan("reply", (1, 2), (1,), "viewer", "reaction", "Обсудить интерактив", roles, "интерактив карт")
        result, _, data, observed = self.generate(rows, plan)
        self.assertEqual(result.action, "reply")
        self.assertEqual(data["participation_plan"]["source_roles"][1]["linked_to"], 1)
        self.assertEqual(observed[0][1]["participation_plan"]["topic"], "интерактив карт")

    def test_correction_as_basis_preserves_original_and_skips_unrelated_topic(self):
        rows = [message(1, "Мифическое оружие Лусио"), message(2, "В карты она добавляет интерактив"),
                message(3, "они*")]
        plan = Plan("reply", (1, 2, 3), (3,), "viewer", "reaction", "Обсудить интерактив карт")
        _, _, data, _ = self.generate(rows, plan)
        self.assertEqual([row["id"] for row in data["selected_conversation"]], [2, 3])

    def test_explicit_address_can_differ_from_reply_thread_author(self):
        self.assertEqual(self.hint("потерпи софа пж", reply_parent_login="other", reply_parent_user_id="456").kind, "owner")

    def test_safe_stage_codes_for_selection_generation_validation_and_timeout(self):
        cases = [([RuntimeError("secret-test-key raw private")], "autonomous_selection"),
                 ([json.dumps(asdict(Plan("reply", (1,), (1,), "viewer", "reaction", "Реакция"))),
                   RuntimeError("raw private")], "autonomous_generation"),
                 ([json.dumps(asdict(Plan("reply", (1,), (1,), "viewer", "reaction", "Реакция"))), "not-json"], "autonomous_validation"),
                 ([TimeoutError("private")], "autonomous_timeout")]
        for responses, code in cases:
            with patch("participation.request_completion", side_effect=responses), self.assertRaises(ParticipationError) as caught:
                request_decision(self.cfg, [message(1, "Победил босса")], AutoSettings())
            self.assertEqual(str(caught.exception), code)
