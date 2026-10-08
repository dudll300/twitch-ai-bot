"""Optional dictionary enrichment with fake completions, no Twitch or paid calls."""

from dataclasses import asdict, replace
import json
import io
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import ai_client as ai
from local_context import (DOCUMENT_NAME, LocalBundle, LocalCard, LocalContextManager,
                           LOCAL_PROTOCOL_BUDGET, LocalReply, LocalResultError, LocalSettings, LocalSnapshot,
                           direct_bundle, save_document)
import participation as part


def encoded(value):
    return json.dumps(value, ensure_ascii=False)


def row(sequence, author, text):
    return dict(sequence=sequence, author=author, text=text, user_id=str(sequence), time=100 + sequence)


class LocalContextAITests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.card = LocalCard("loss-chain", "Сасун", ("sasun", "полоса невезения"),
                              "Локальный добродушный подкол после серии игровых поражений.",
                              "Человек шутит над чередой собственных игровых проигрышей.",
                              "Не использовать при настоящем расстройстве и личных проблемах.",
                              "Опять полоса невезения!", True, True)
        self.dictionary = LocalSnapshot(LocalSettings(enabled=True), (self.card,), "initial")
        save_document(self.root / DOCUMENT_NAME, self.dictionary)
        self.manager = LocalContextManager(self.root, clock=lambda: 10000)
        self.snapshot = self.manager.snapshot()
        self.bundle = self.manager.bundle(self.snapshot, "Никак не могу выиграть, уже сам смеюсь")
        self.cfg = {"AI_MODEL": "exact-primary", "AI_FALLBACK_MODELS": "unused-backup",
                    "AI_API_KEY": "key-that-must-stay-private", "AI_PROMPT": "Лаконичный собеседник",
                    "AI_CHAT_URL": "https://example.invalid/v1/chat/completions"}
        self.settings = SimpleNamespace(participation="cautious", max_chars=220,
                                        autonomous_prompt=part.DEFAULT_AUTONOMOUS_PROMPT)

    def request(self, content, *, bundle=None, **options):
        with patch("ai_client.request_completion", return_value=content) as completion:
            reply = ai.call_ai(self.cfg, "viewer", "Как пройти игрового босса?",
                               local_bundle=self.bundle if bundle is None else bundle, **options)
        return reply, completion.call_args

    def pipeline(self, rows, plan, answer, **options):
        with patch("participation.request_completion",
                   side_effect=[encoded(asdict(plan)), encoded(answer)]) as completion:
            reply = part.request_decision(self.cfg, rows, self.settings,
                                          local_manager=self.manager, local_snapshot=self.snapshot, **options)
        return reply, [call.args[2] for call in completion.call_args_list]

    def test_empty_disabled_damaged_and_no_applicable_dictionary_keep_old_path(self):
        plain_messages = ai.build_messages(self.cfg, "viewer", "Вопрос")
        empty = LocalBundle(replace(self.snapshot, cards=()))
        disabled = LocalBundle(replace(self.snapshot, settings=LocalSettings()), creative=(self.card,))
        damaged = LocalBundle(replace(self.snapshot, error="Файл повреждён"), direct=(self.card,))
        no_match = direct_bundle(self.snapshot, "Играем дальше")
        for bundle in (empty, disabled, damaged, no_match):
            with self.subTest(bundle=bundle):
                self.assertEqual(ai.build_messages(self.cfg, "viewer", "Вопрос", local_bundle=bundle), plain_messages)
                reply, call = self.request("Обычный ответ", bundle=bundle)
                self.assertEqual(type(reply), str)
                self.assertNotIn("creative_card_id", str(call.args[2]))
                self.assertNotIn(self.card.meaning, str(call.args[2]))

    def test_direct_understanding_uses_plain_text_and_carries_private_snapshot(self):
        direct = direct_bundle(self.snapshot, "Что значит SASUN?")
        reply, call = self.request("Это местный подкол после полосы проигрышей.", bundle=direct)
        self.assertIsInstance(reply, LocalReply)
        self.assertIs(reply.local_bundle, direct)
        self.assertIsNone(reply.creative_card_id)
        self.assertIn(self.card.meaning, str(call.args[2]))
        self.assertIn("только для понимания", str(call.args[2]))
        self.assertEqual(str(reply), "Это местный подкол после полосы проигрышей.")
        self.assertFalse(self.manager.usage_path.exists())

    def test_creative_selection_is_optional_and_exact_model_is_used(self):
        reply, call = self.request(encoded({"text": "Сначала изучи окно после его третьего удара.",
                                           "creative_card_id": None}), model="explicit-id")
        self.assertEqual(reply, "Сначала изучи окно после его третьего удара.")
        self.assertIsNone(reply.creative_card_id)
        self.assertEqual(call.args[1], "explicit-id")
        self.assertTrue(call.kwargs["reject_truncated"])
        self.assertNotIn("response_format", call.kwargs)
        self.assertNotIn("tools", call.kwargs)
        self.assertFalse(self.manager.usage_path.exists())

    def test_valid_metadata_is_separate_and_text_limit_is_applied_after_json(self):
        reply, _ = self.request(encoded({"text": "x" * 410 + " Ещё текст",
                                        "creative_card_id": self.card.id}))
        self.assertEqual(str(reply), "x" * 400)
        self.assertEqual(reply.creative_card_id, self.card.id)
        self.assertIs(reply.local_bundle, self.bundle)
        self.assertNotIn("creative_card_id", str(reply))
        self.assertFalse(self.manager.usage_path.exists())

    def test_truncated_structured_completion_requests_ordinary_recovery(self):
        response = {"choices": [{"message": {"content": '{"text":"Неоконченный ответ'}, "finish_reason": "length"}]}
        with patch("urllib.request.urlopen", return_value=io.BytesIO(encoded(response).encode("utf-8"))):
            with self.assertRaises(LocalResultError):
                ai.call_ai(self.cfg, "viewer", "Вопрос", local_bundle=self.bundle)
        # Existing prompt generation/autonomous callers still catch the common
        # temporary-error base while preserving the precise truncation reason.
        with patch("urllib.request.urlopen", return_value=io.BytesIO(encoded(response).encode("utf-8"))):
            with self.assertRaises(ai.TemporaryAIError) as error:
                ai.request_completion(self.cfg, "exact-primary", [], reject_truncated=True)
        self.assertIsInstance(error.exception, ai.TruncatedAIError)

    def test_unknown_inactive_or_understanding_only_id_is_rejected(self):
        for bundle, card_id in ((self.bundle, "unknown-id"),
                                (direct_bundle(self.snapshot, "sasun"), self.card.id),
                                (LocalBundle(self.snapshot, creative=(replace(self.card, enabled=False),)), self.card.id),
                                (LocalBundle(self.snapshot, creative=(replace(self.card, allow_situational=False),)), self.card.id),
                                (LocalBundle(self.snapshot, creative=(replace(self.card, id="outside-snapshot"),)), "outside-snapshot")):
            with self.subTest(card_id=card_id), self.assertRaises(LocalResultError):
                ai.parse_local_reply(encoded({"text": "Реплика", "creative_card_id": card_id}), bundle)

    def test_structured_result_rejects_wrong_schema_types_and_duplicate_fields(self):
        bad = ["Обычная строка", '```json\n{"text":"Ответ","creative_card_id":null}\n```',
               encoded({"text": "Ответ"}), encoded({"text": "Ответ", "creative_card_id": None, "target": "other"}),
               encoded({"text": None, "creative_card_id": None}), encoded({"text": "Ответ", "creative_card_id": True}),
               encoded({"text": "Ответ", "creative_card_id": ""}), encoded({"text": "", "creative_card_id": None}),
               encoded({"text": "   ", "creative_card_id": None}),
               encoded({"text": encoded({"text": "Ответ", "creative_card_id": "loss-chain"}), "creative_card_id": None}),
               '{"text":"Ответ","creative_card_id":"loss-chain","creative_card_id":null}',
               "[" * 1500 + "0" + "]" * 1500]
        for content in bad:
            with self.subTest(content=content), self.assertRaises(LocalResultError) as error:
                ai.parse_local_reply(content, self.bundle)
            self.assertEqual(str(error.exception), "Некорректный ответ с локальным контекстом; результат отклонён.")

    def test_secret_is_rejected_without_echo_even_when_escaped_in_json(self):
        for key in (self.cfg["AI_API_KEY"], 'private"key\\сюрприз'):
            content = json.dumps({"text": "Секрет " + key, "creative_card_id": None})
            with self.assertRaises(LocalResultError) as error:
                ai.parse_local_reply(content, self.bundle, api_key=key)
            self.assertNotIn(key, str(error.exception))
            self.assertNotIn(content, str(error.exception))

    def test_recovery_plain_text_cannot_expose_service_json_even_after_limit(self):
        direct = direct_bundle(self.snapshot, "sasun")
        bad = [encoded({"text": "Обычный ответ", "creative_card_id": None}),
               "x" * 500 + ' {"creative_card_id": null}', self.cfg["AI_API_KEY"]]
        for content in bad:
            with self.subTest(content=content), self.assertRaises(LocalResultError):
                self.request(content, bundle=direct)
        self.assertTrue(ai.is_local_service_output(bad[0]))
        self.assertFalse(ai.is_local_service_output("Обычный ответ с пояснением местного слова."))

    def test_nested_escaped_and_fenced_service_envelopes_never_become_published_text(self):
        envelopes = [encoded({"text": "Ответ"}),
                     '{"text":"Ответ","\\u0063reative_card_id":null}',
                     '```json\n{"text":"Ответ"}\n```',
                     encoded(encoded({"text": "Ответ", "creative_card_id": None}))]
        for envelope in envelopes:
            with self.subTest(envelope=envelope):
                self.assertTrue(ai.is_local_service_output(envelope))
                with self.assertRaises(LocalResultError):
                    ai.parse_local_reply(encoded({"text": envelope, "creative_card_id": None}), self.bundle)
                with self.assertRaises(LocalResultError):
                    self.request(envelope, bundle=direct_bundle(self.snapshot, "sasun"))
        self.assertFalse(ai.is_local_service_output('Можно выбрать ключ "text" в настройках.'))

    def test_preparation_failure_keeps_both_autonomous_stages_and_plain_output_protocol(self):
        rows = [row(1, "viewer", "Наконец победил босса")]
        plan = part.Plan("reply", (1,), (1,), "viewer", "reaction", "Поздравить с победой")
        events = []
        self.manager.emit = events.append
        with patch.object(self.manager, "bundle", side_effect=RuntimeError("private-dictionary-data")), \
             patch("participation.request_completion", side_effect=[encoded(asdict(plan)),
                   encoded({"text": "Поздравляю с победой!"})]) as completion:
            reply = part.request_decision(self.cfg, rows, self.settings,
                                          local_manager=self.manager, local_snapshot=self.snapshot)
        self.assertEqual(reply.reply, "@viewer Поздравляю с победой!")
        self.assertEqual(completion.call_count, 2)
        self.assertNotIn("optional_creative_candidates", str(completion.call_args_list))
        self.assertTrue(any(event["action"] == "error" for event in events))
        self.assertNotIn("private-dictionary-data", repr(events))
        self.assertFalse(self.manager.usage_path.exists())

    def test_recovery_rejects_metadata_before_truncation_even_when_system_was_disabled(self):
        disabled = LocalBundle(replace(self.snapshot, settings=LocalSettings()))
        long_json = encoded({"text": "x" * 500, "creative_card_id": self.card.id})
        with patch("ai_client.request_completion", return_value=long_json):
            with self.assertRaises(LocalResultError):
                ai.call_ai(self.cfg, "viewer", "Вопрос", local_bundle=disabled, reject_local_service=True)
        # The special recovery gate does not change normal disabled requests.
        with patch("ai_client.request_completion", return_value="Обычный ответ"):
            normal = ai.call_ai(self.cfg, "viewer", "Вопрос", local_bundle=disabled)
            recovery = ai.call_ai(self.cfg, "viewer", "Вопрос", local_bundle=disabled, reject_local_service=True)
        self.assertEqual(type(normal), str)
        self.assertEqual(type(recovery), str)
        self.assertEqual(normal, recovery)

    def test_rules_prioritise_content_personal_boundaries_and_scene_tone(self):
        _, call = self.request(encoded({"text": "Полезный ответ без мема.", "creative_card_id": None}))
        prompt = str(call.args[2])
        for guard in ("Сначала ответь по существу", "дружеский или шутливый", "радостный или воодушевлённый",
                      "нейтральный", "серьёзный", "напряжённый", "неясный", "предположение по тексту",
                      "личные инструкции", "avoid", "Не выдумывай", "максимум одна карточка"):
            self.assertIn(guard, prompt)
        self.assertIn(self.card.situations, prompt)
        self.assertIn(self.card.avoid, prompt)
        for bundle in (self.bundle, direct_bundle(self.snapshot, "sasun")):
            for autonomous in (False, True):
                self.assertLessEqual(len(ai.local_context_rules(bundle, autonomous=autonomous)), LOCAL_PROTOCOL_BUDGET)

    def test_silent_selector_has_only_direct_explanations_and_no_creative_pick(self):
        rows = [row(1, "viewer", "Опять sasun"), row(2, "other", "Просто болтаем")]
        plan = part.Plan("silent", reason="no_reason")
        with patch("participation.request_completion", return_value=encoded(asdict(plan))) as completion:
            result = part.request_decision(self.cfg, rows, self.settings,
                                           local_manager=self.manager, local_snapshot=self.snapshot)
        self.assertEqual(result, part.Decision("silent", reason="no_reason"))
        self.assertEqual(completion.call_count, 1)
        prompt = completion.call_args.args[2]
        self.assertIn(self.card.meaning, str(prompt))
        local_payload = next(json.loads(message["content"].split("\n", 1)[1]) for message in prompt
                             if '"direct_understanding_only"' in message["content"])
        self.assertEqual(local_payload["optional_creative_candidates"], [])
        self.assertIn("не являются поводом вмешиваться", str(prompt))

    def test_selector_reports_direct_context_budget_error_without_changing_plain_protocol(self):
        cards = tuple(LocalCard(f"term-{index}", f"Термин{index}", meaning="м" * 600,
                                situations="с" * 600, avoid="а" * 600, example="п" * 300)
                      for index in range(8))
        snapshot = save_document(self.root / DOCUMENT_NAME, replace(self.snapshot, cards=cards))
        events = []
        manager = LocalContextManager(self.root, clock=lambda: 10000, emit=events.append)
        rows = [row(1, "viewer", " ".join(card.name for card in cards))]
        with patch("participation.request_completion", return_value=encoded(asdict(part.Plan("silent")))) as completion:
            reply = part.request_decision(self.cfg, rows, self.settings,
                                          local_manager=manager, local_snapshot=snapshot)
        self.assertEqual(reply, part.Decision("silent"))
        self.assertEqual(completion.call_count, 1)
        self.assertTrue(any(event["action"] == "error" and "16000" in event["message"] for event in events))
        self.assertNotIn("direct_understanding_only", str(completion.call_args.args[2]))
        self.assertFalse(manager.usage_path.exists())

    def test_adjacent_chain_direct_mem_does_not_enter_selected_generation(self):
        adjacent_card = replace(self.card, allow_situational=False)
        snapshot = save_document(self.root / DOCUMENT_NAME, replace(self.snapshot, cards=(adjacent_card,)))
        rows = [row(1, "viewer", "Наконец прошёл сложного босса"),
                row(2, "neighbor", "Опять sasun, третий проигрыш"),
                row(3, "friend", "@viewer Поздравляю с победой!")]
        rows[0]["message_id"] = "selected-root"
        rows[1]["message_id"] = "adjacent-root"
        rows[2].update(reply_parent_id="selected-root", thread_id="selected-root")
        plan = part.Plan("reply", (1, 3), (1, 3), "viewer", "reaction", "Поздравить с победой")
        with patch("participation.request_completion", side_effect=[encoded(asdict(plan)),
                   encoded({"text": "Поздравляю с победой!"})]) as completion:
            reply = part.request_decision(self.cfg, rows, self.settings,
                                           local_manager=self.manager, local_snapshot=snapshot, new_ids={3})
        selector, generator = [call.args[2] for call in completion.call_args_list]
        self.assertIn(self.card.meaning, str(selector))
        for neighbor in (self.card.meaning, "третий проигрыш", "adjacent-root", "neighbor"):
            self.assertNotIn(neighbor, str(generator))
        self.assertEqual(type(reply), part.Decision)
        self.assertEqual(reply.target, "viewer")

    def test_situational_catalog_is_separate_from_neighbor_direct_understanding(self):
        rows = [row(1, "viewer", "Наконец победил босса"), row(2, "neighbor", "Опять sasun, третий проигрыш")]
        plan = part.Plan("reply", (1,), (1,), "viewer", "reaction", "Поздравить с конкретной победой")
        reply, calls = self.pipeline(rows, plan, {"text": "Поздравляю с победой!", "creative_card_id": None}, new_ids={1})
        local_payload = next(json.loads(message["content"].split("\n", 1)[1]) for message in calls[1]
                             if '"direct_understanding_only"' in message["content"])
        self.assertEqual(local_payload["direct_understanding_only"], [])
        # The compact owner-approved catalog is still available for semantic
        # selection; it does not inherit the neighbor's situation or direct hit.
        self.assertEqual([card["id"] for card in local_payload["optional_creative_candidates"]], [self.card.id])
        selected = json.loads(calls[1][-1]["content"])["selected_conversation"]
        self.assertEqual([message["id"] for message in selected], [1])
        self.assertNotIn("третий проигрыш", str(calls[1]))
        self.assertIsNone(reply.creative_card_id)

    def test_both_stages_use_one_immutable_dictionary_snapshot(self):
        rows = [row(1, "viewer", "Как понять sasun?"), row(2, "friend", "Тоже слышал это слово")]
        plan = part.Plan("reply", (1, 2), (1, 2), "viewer", "answer", "Пояснить значение")
        captured = []
        def complete(cfg, model, messages, **options):
            captured.append(messages)
            if len(captured) == 1:
                changed = replace(self.card, meaning="НОВОЕ_ОПИСАНИЕ_ПОСЛЕ_НАЧАЛА", allow_situational=False)
                save_document(self.root / DOCUMENT_NAME, replace(self.snapshot, cards=(changed,)))
                return encoded(asdict(plan))
            return encoded({"text": "Это местный подкол после игровых проигрышей.", "creative_card_id": None})
        with patch("participation.request_completion", side_effect=complete):
            reply = part.request_decision(self.cfg, rows, self.settings,
                                          local_manager=self.manager, local_snapshot=self.snapshot)
        self.assertIn(self.card.meaning, str(captured[1]))
        self.assertNotIn("НОВОЕ_ОПИСАНИЕ_ПОСЛЕ_НАЧАЛА", str(captured[1]))
        self.assertIs(reply.local_bundle.snapshot, self.snapshot)
        self.assertFalse(self.manager.is_current(reply.local_bundle.snapshot))

    def test_generator_can_choose_creative_card_only_after_scene_selection(self):
        rows = [row(1, "viewer", "Ха, снова проиграл, пятая попытка подряд"),
                row(2, "friend", "@viewer Не сдавайся, зато уже смешно")]
        plan = part.Plan("reply", (1, 2), (1, 2), "viewer", "joke", "Поддержать добродушной реакцией")
        reply, calls = self.pipeline(rows, plan, {"text": "Полоса невезения тоже когда-нибудь заканчивается!",
                                               "creative_card_id": self.card.id})
        self.assertIsInstance(reply, part.ContextDecision)
        self.assertEqual(reply.creative_card_id, self.card.id)
        self.assertEqual(reply.target, "viewer")
        self.assertEqual(reply.basis, (1, 2))
        self.assertNotIn(self.card.meaning, str(calls[0]))
        self.assertIn(self.card.meaning, str(calls[1]))
        self.assertNotIn("creative_card_id", reply.reply)
        self.assertFalse(self.manager.usage_path.exists())

    def test_generator_can_choose_no_card_or_silence_without_third_call(self):
        rows = [row(1, "viewer", "Снова не прошёл босса"), row(2, "friend", "Давай ещё попытку")]
        plan = part.Plan("reply", (1, 2), (1, 2), "viewer", "reaction", "Поддержать игрока")
        for text in ("Попробуй переждать его длинную атаку.", ""):
            with self.subTest(text=text):
                reply, calls = self.pipeline(rows, plan, {"text": text, "creative_card_id": None})
                self.assertEqual(len(calls), 2)
                self.assertIsNone(reply.creative_card_id)
                self.assertEqual(reply.action, "reply" if text else "silent")
        with self.assertRaises(part.ParticipationError) as failure:
            self.pipeline(rows, plan, {"text": "", "creative_card_id": self.card.id})
        self.assertEqual(failure.exception.code, "autonomous_validation")

    def test_autonomous_generator_keeps_strict_controls_length_and_plan(self):
        rows = [row(1, "viewer", "Снова проиграл босса"), row(2, "friend", "Получится в следующий раз")]
        plan = part.Plan("reply", (1, 2), (1, 2), "viewer", "reaction", "Поддержать игрока")
        invalid = [{"text": "a" * 220, "creative_card_id": None},
                   {"text": "Реплика\nВторая строка", "creative_card_id": None},
                   {"text": "@other Другой адресат", "creative_card_id": None},
                   {"text": "/ban viewer", "creative_card_id": None},
                   {"text": "Ответ", "creative_card_id": "unknown-id"},
                   {"text": "Ответ", "creative_card_id": None, "target": "other"}]
        for result in invalid:
            with self.subTest(result=result), self.assertRaises(ValueError):
                self.pipeline(rows, plan, result)
        self.assertFalse(self.manager.usage_path.exists())

    def test_scoped_personal_avoid_is_present_when_optional_creative_choice_is_available(self):
        rows = [row(1, "viewer", "Пятый проигрыш, уже смешно"), row(2, "friend", "Скоро выиграешь")]
        plan = part.Plan("reply", (1, 2), (1, 2), "viewer", "reaction", "Поддержать игрока")
        profiles = [{"login": "viewer", "user_id": "1", "enabled": True,
                     "prompt": "Говори доброжелательно и не подкалывай проигрыши", "aliases": []}]
        memory = {"streamer": {"facts": [], "jokes": [], "avoid": []}, "viewers": [
            {"login": "viewer", "user_id": "1", "facts": [], "jokes": [],
             "avoid": ["Не подкалывай мои игровые поражения"]}]}
        reply, calls = self.pipeline(rows, plan, {"text": "Ещё одна попытка, ты уже лучше знаешь его движения.",
                                               "creative_card_id": None}, profiles=profiles, memory_data=memory)
        self.assertIsNone(reply.creative_card_id)
        self.assertIn("Не подкалывай мои игровые поражения", str(calls[1]))
        self.assertIn("Говори доброжелательно", str(calls[1]))

    def test_direct_explanation_survives_shared_creative_cooldown(self):
        lease = self.manager.reserve_publish(self.bundle, self.card.id)
        self.assertIsNotNone(lease)
        self.assertTrue(self.manager.complete_publish(lease, True))
        snapshot = self.manager.snapshot()
        paused = self.manager.bundle(snapshot, "Что значит sasun?")
        self.assertEqual(paused.candidate_ids, frozenset())
        self.assertEqual([card.id for card in paused.direct], [self.card.id])
        saved_usage = self.manager.usage_path.read_bytes()
        reply, _ = self.request("Это местный подкол после череды проигрышей.", bundle=paused)
        self.assertIsNone(reply.creative_card_id)
        rows = [row(1, "viewer", "Что значит sasun?"), row(2, "friend", "Тоже не знаю")]
        plan = part.Plan("reply", (1, 2), (1, 2), "viewer", "answer", "Пояснить локальное слово")
        with patch("participation.request_completion", side_effect=[encoded(asdict(plan)),
                   encoded({"text": "Это местный подкол после череды игровых проигрышей."})]):
            decision = part.request_decision(self.cfg, rows, self.settings,
                                            local_manager=self.manager, local_snapshot=snapshot)
        self.assertIsInstance(decision, part.ContextDecision)
        self.assertIsNone(decision.creative_card_id)
        self.assertEqual(self.manager.usage_path.read_bytes(), saved_usage)

    def test_damaged_dictionary_keeps_plain_autonomous_protocol(self):
        snapshot = replace(self.snapshot, error="Файл повреждён")
        rows = [row(1, "viewer", "Поздравьте с победой"), row(2, "friend", "Молодец")]
        plan = part.Plan("reply", (1, 2), (1, 2), "viewer", "reaction", "Поздравить игрока")
        with patch("participation.request_completion", side_effect=[encoded(asdict(plan)),
                   encoded({"text": "Поздравляю с победой!"})]) as completion:
            reply = part.request_decision(self.cfg, rows, self.settings,
                                          local_manager=self.manager, local_snapshot=snapshot)
        self.assertEqual(type(reply), part.Decision)
        self.assertNotIn("creative_card_id", str(completion.call_args_list[1].args[2]))


if __name__ == "__main__":
    unittest.main()
