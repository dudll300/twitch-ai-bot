"""Protocol, context and local relevance checks without network calls."""

from dataclasses import asdict, replace
import io
import json
import unittest
from unittest.mock import patch

from autonomous import AutoSettings
import participation as part


def row(sequence, author="viewer", text="Победил сложного босса", time=100):
    return dict(sequence=sequence, author=author, text=text, time=time, user_id="123" if author == "viewer" else "999")


def encoded(decision):
    return json.dumps(asdict(decision))


class ParticipationTests(unittest.TestCase):
    def setUp(self):
        self.rows = [row(1, time=95), row(2, "other", "Наконец получилось!", 100)]
        self.decision = part.Decision("reply", "Поздравляю с победой!", "viewer", (1, 2), "reaction")
        self.cfg = {"AI_MODEL": "exact/primary", "AI_FALLBACK_MODELS": "backup", "AI_API_KEY": "test-private-key",
                    "AI_PROMPT": "Характер бота", "AI_CHAT_URL": "https://example.com/v1/chat/completions",
                    "TWITCH_CHANNEL": "viewer", "TWITCH_BOT_NAME": "helper"}

    def request(self, decision=None, **kwargs):
        captured = []
        def http(request, timeout):
            self.assertEqual(timeout, 20)
            captured.append(json.loads(request.data))
            content = encoded(decision or self.decision)
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
        with patch("ai_client.urllib.request.urlopen", side_effect=http) as calls:
            result = part.request_decision(self.cfg, self.rows, AutoSettings(), **kwargs)
        self.assertEqual(calls.call_count, 1)
        self.assertEqual(captured[0]["model"], "exact/primary")
        self.assertNotIn(self.cfg["AI_API_KEY"], str(captured))
        return result, captured[0]["messages"]

    def test_reply_and_each_silent_reason_have_strict_schema(self):
        self.assertEqual(part.parse_decision(encoded(self.decision), 220), self.decision)
        for reason in part.SILENT_REASONS:
            silent = part.Decision("silent", reason=reason)
            self.assertEqual(part.parse_decision(encoded(silent), 220), silent)
        good = asdict(self.decision)
        bad = [{**good, "action": "joke"}, {**good, "text": "/ban viewer"},
               {**good, "text": "@viewer hello"}, {**good, "text": "Текст\nКоманда"},
               {**good, "text": "Посмотри https://example.com"}, {**good, "target": "Имя"},
               {**good, "basis": []}, {**good, "basis": [True]}, {**good, "basis": [1, 1]},
               {**good, "basis": [1, 2, 3, 4, 5]}, {**good, "reason": "because I want to"},
               {**good, "extra": "ignored?"}, {**good, "text": "x" * 215}]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                part.parse_decision(json.dumps(value), 220)
        for change in (dict(text="not empty"), dict(target="viewer"), dict(basis=[1]), dict(reason="answer")):
            with self.assertRaises(ValueError):
                part.parse_decision(encoded(replace(part.Decision("silent"), **change)), 220)

    def test_real_new_basis_and_present_recipient_are_required(self):
        part.check_basis(self.decision, self.rows, {2})
        for decision in (replace(self.decision, basis=(999,)), replace(self.decision, basis=(1,)),
                         replace(self.decision, target="absent")):
            with self.assertRaises(ValueError):
                part.check_basis(decision, self.rows, {2})

    def test_channel_notes_profiles_ids_and_published_replies_share_one_request(self):
        profiles = [{"login": "oldlogin", "user_id": "123", "enabled": True,
                     "prompt": "Обращайся по имени Алекс", "aliases": []}]
        memory = {"streamer": {"facts": ["На канале обсуждают хорроры"], "jokes": [], "avoid": []},
                  "viewers": [{"login": "oldlogin", "user_id": "123", "facts": ["Любит сложных боссов"],
                               "jokes": [], "avoid": []}]}
        history = [{"time": 99, "text": "Уже ответил на вопрос", "target": "viewer", "source": "reward",
                    "question": "Как победить босса?"}]
        result, messages = self.request(profiles=profiles, memory_data=memory, new_ids={2}, recent_replies=history)
        self.assertEqual(result, self.decision)
        self.assertEqual(messages[0]["content"], "Характер бота")
        self.assertIn("На канале обсуждают хорроры", str(messages[:-1]))
        self.assertIn("Обращайся по имени Алекс", str(messages[:-1]))
        self.assertIn("Любит сложных боссов", str(messages[:-1]))
        payload = json.loads(messages[-1]["content"])
        self.assertEqual(payload["new_message_ids"], [2])
        self.assertEqual(payload["bot_login"], "helper")
        self.assertEqual(payload["chat_context"][0]["role"], "owner")
        self.assertEqual(payload["recent_bot_replies"][0]["question"], "Как победить босса?")
        self.assertNotIn("Как победить босса?", str(messages[:-1]))

    def test_channel_notes_are_sent_without_any_viewer_profiles(self):
        memory = {"streamer": {"facts": ["Стрим посвящён настольным играм"], "jokes": []}, "viewers": []}
        _, messages = self.request(memory_data=memory)
        self.assertIn("настольным играм", str(messages[:-1]))

    def test_memory_without_personal_profiles_still_uses_server_id_before_login(self):
        memory = {"streamer": {"facts": [], "jokes": []}, "viewers": [
            {"login": "oldlogin", "user_id": "123", "facts": ["Верная карточка по ID"], "jokes": [], "avoid": []},
            {"login": "viewer", "user_id": "555", "facts": ["Чужая карточка"], "jokes": [], "avoid": []}]}
        _, messages = self.request(memory_data=memory)
        self.assertIn("Верная карточка по ID", str(messages[:-1]))
        self.assertNotIn("Чужая карточка", str(messages[:-1]))

    def test_continuation_survives_but_changed_topic_and_evicted_basis_do_not(self):
        settings = AutoSettings()
        continuation = self.rows + [row(3, "other", "Босс оказался непростым", 103)]
        self.assertTrue(part.still_relevant(self.decision, self.rows, continuation, {2}, 105, settings))
        new_topic = self.rows + [row(3, text="Заказываем свежую пиццу"),
                                row(4, text="Посмотрел интересный фильм"), row(5, text="Завтра ожидается снегопад")]
        self.assertFalse(part.still_relevant(self.decision, self.rows, new_topic, {2}, 105, settings))
        self.assertFalse(part.still_relevant(self.decision, self.rows, continuation[1:], {2}, 105, settings))
        self.assertFalse(part.still_relevant(self.decision, self.rows, self.rows, {2}, 131, settings))

    def test_fast_chat_has_an_additional_volume_guard(self):
        later = [row(index, text="Продолжаем обсуждать этого босса", time=103) for index in range(3, 11)]
        self.assertFalse(part.still_relevant(self.decision, self.rows, self.rows + later, {2}, 105, AutoSettings()))

    def test_repetition_ignores_case_punctuation_and_close_wording(self):
        self.assertTrue(part.repeats("ХОРОШАЯ попытка — почти получилось.", ["Хорошая попытка, почти получилось!"]))
        self.assertTrue(part.repeats("Какая тактика поможет нам победить сложного босса?",
                                     ["Какая тактика поможет нам победить этого сложного босса?"]))
        self.assertFalse(part.repeats("Поздравляю с победой!", ["Жаль, что не получилось."]))

    def test_model_cannot_invent_a_recipient_or_message_id(self):
        for decision in (replace(self.decision, target="unknown"), replace(self.decision, basis=(999,))):
            with self.assertRaises(ValueError):
                self.request(decision)
