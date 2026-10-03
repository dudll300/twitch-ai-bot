"""Name recognition, identity boundaries and identical AI snapshots."""

import asyncio
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ai_client
import autonomous
import bot
import testing
from profiles import ProfileError, load_profiles, save_profiles, validate_profiles
from viewer_recognition import recognize_mentions, related_context, russian_name


def profile(login="lopotik", user_id="123", prompt="Шути про пельмени", enabled=True, aliases=()):
    return {"login": login, "user_id": user_id, "prompt": prompt, "enabled": enabled, "aliases": list(aliases)}


def memory_card(login, user_id, fact):
    return {"login": login, "user_id": user_id, "facts": [fact], "jokes": [], "avoid": []}


CFG = {"AI_MODEL": "primary", "AI_FALLBACK_MODELS": "backup", "AI_PROMPT": "Общий стиль",
       "AI_API_KEY": "private-key", "AI_CHAT_URL": "https://example.com/chat/completions"}


class RecognitionTests(unittest.TestCase):
    def setUp(self):
        self.rows = validate_profiles([profile(aliases=("Лёха",)), profile("other", "456", "Другой промпт")])
        self.memory = {"streamer": {"facts": [], "jokes": []}, "viewers": [
            memory_card("old_name", "123", "Любит хорроры"),
            memory_card("lopotik", "", "Чужая карточка без ID")]}

    def test_latin_russian_alias_cases_and_minor_typos(self):
        for name in ("lopotik", "@LOPOTIK", "лопотик", "ЛОПОТИК", "лопотика", "лопотику",
                     "лоптик", "лоптоик", "лёха", "ЛЕХА"):
            with self.subTest(name=name):
                result = recognize_mentions(self.rows, "Что скажешь про " + name + "?")
                self.assertEqual([match.index for match in result.matches], [0])
                self.assertFalse(result.ambiguous)
        self.assertEqual(russian_name("lopotik"), "лопотик")

    def test_substrings_and_strong_distortions_are_not_guessed(self):
        for text in ("ultralopotik", "lopotik_extra", "лопатек", "лопотик123", "Привет всем"):
            with self.subTest(text=text):
                self.assertEqual(recognize_mentions(self.rows, text).matches, ())

    def test_short_names_are_exact_only_and_numeric_suffixes_stay_distinct(self):
        rows = validate_profiles([profile("max", "1", aliases=("макс",)), profile("lopotik1", "2")])
        self.assertEqual(len(recognize_mentions(rows, "макс").matches), 1)
        for word in ("мак", "мкас", "loxotik2"):
            self.assertFalse(recognize_mentions(rows, word).matches)
        rows = validate_profiles([profile("alex", "1", aliases=("леха",))])
        self.assertFalse(recognize_mentions(rows, "лехож").matches)

    def test_shared_alias_and_equal_typos_are_ambiguous_including_disabled_profiles(self):
        rows = validate_profiles([profile("lopotik", "1", aliases=("Лёха",)),
                                  profile("lopotak", "2", enabled=False, aliases=("Леха",))])
        for word in ("леха", "lopotek"):
            result = recognize_mentions(rows, word)
            self.assertFalse(result.matches)
            self.assertEqual(result.ambiguous, (word,))
            context = related_context(rows, word)
            self.assertEqual(context.prompt, "")
            self.assertIn("неоднозначное", str(context.diagnostics))

    def test_exact_match_wins_over_another_similar_nickname(self):
        rows = validate_profiles([profile("lopotik", "1"), profile("lopotak", "2")])
        self.assertEqual([match.index for match in recognize_mentions(rows, "lopotik").matches], [0])

    def test_candidate_pruning_keeps_edits_at_every_position_including_prefix(self):
        rows = validate_profiles([profile()])
        name = "lopotik"
        variants = [name[:i] + name[i + 1:] for i in range(len(name))]
        variants += [name[:i] + "z" + name[i:] for i in range(len(name) + 1)]
        variants += [name[:i] + "z" + name[i + 1:] for i in range(len(name))]
        variants += [name[:i] + name[i + 1] + name[i] + name[i + 2:] for i in range(len(name) - 1)]
        for word in variants:
            with self.subTest(word=word):
                self.assertEqual([match.index for match in recognize_mentions(rows, word).matches], [0])
        rows = validate_profiles([profile("supernickname")])
        for word in ("xxpernickname", "xxsupernickname", "pernickname", "useprnickname"):
            with self.subTest(word=word):
                self.assertEqual([match.index for match in recognize_mentions(rows, word).matches], [0])

    def test_multiple_mentions_have_scoped_prompts_and_memory_id_priority(self):
        context = related_context(self.rows, "Лоптик и @other", self.memory)
        self.assertIn("Шути про пельмени", context.prompt)
        self.assertIn("Другой промпт", context.prompt)
        self.assertIn("Любит хорроры", context.prompt)
        self.assertNotIn("Чужая карточка", context.prompt)
        self.assertIn("НЕ подтверждает личность отправителя", context.prompt)
        self.assertIn("а не к остальным зрителям", context.prompt)

    def test_disabled_instruction_never_enters_context_but_memory_is_independent(self):
        self.rows[0]["enabled"] = False
        context = related_context(self.rows, "лопотик", self.memory)
        self.assertNotIn("Шути про пельмени", context.prompt)
        self.assertIn("Любит хорроры", context.prompt)
        self.assertIn("отключена", str(context.diagnostics))
        self.assertEqual(related_context(self.rows, "лопотик").prompt, "")

    def test_sender_is_not_duplicated_or_replaced_by_textual_mentions(self):
        context = related_context(self.rows, "лопотик и other", sender_profile=self.rows[0])
        self.assertNotIn("Шути про пельмени", context.prompt)
        self.assertIn("Другой промпт", context.prompt)
        self.assertIn("уже учтён", str(context.diagnostics))

    def test_id_only_profile_can_be_mentioned_through_alias(self):
        rows = validate_profiles([profile("", "123", aliases=("лопотик",))])
        context = related_context(rows, "лоптика", self.memory)
        self.assertIn("ID 123", context.prompt)
        self.assertIn("Любит хорроры", context.prompt)

    def test_old_profiles_load_and_new_aliases_round_trip_without_version_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            path.write_text(json.dumps({"version": 1, "profiles": [{key: value for key, value in profile().items()
                                                                    if key != "aliases"}]}), encoding="utf-8")
            self.assertEqual(load_profiles(path)[0]["aliases"], [])
            save_profiles(path, [profile(aliases=(" @Лопотик ", "лопотик", "Лёха"))])
            self.assertEqual(load_profiles(path)[0]["aliases"], ["Лопотик", "Лёха"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], 1)

    def test_invalid_aliases_preserve_last_valid_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profiles.json"
            save_profiles(path, self.rows)
            before = path.read_bytes()
            for aliases in ("not-a-list", [123], [""], ["name\ncommand"], ["has spaces"], ["x" * 65]):
                with self.subTest(aliases=aliases), self.assertRaises(ProfileError):
                    save_profiles(path, [{**profile(), "aliases": aliases}])
                self.assertEqual(path.read_bytes(), before)

    def test_reward_fallbacks_get_same_related_context_and_preserve_sender_id(self):
        router = bot.AIModelRouter(self.rows)
        with patch.object(bot, "call_ai", side_effect=[bot.TemporaryAIError("failed"), "OK"]) as call:
            self.assertEqual(router.ask(CFG, "someone", "Расскажи про лоптика", self.memory, "999"), "OK")
        self.assertEqual([args.kwargs["model"] for args in call.call_args_list], ["primary", "backup"])
        contexts = [args.kwargs["viewer_context"] for args in call.call_args_list]
        self.assertEqual(contexts[0], contexts[1])
        self.assertIn("Шути про пельмени", contexts[0])
        self.assertTrue(all(args.args[4] == "999" for args in call.call_args_list))
        self.assertTrue(all("personal_prompt" not in args.kwargs for args in call.call_args_list))

    def test_mentions_outside_question_limit_are_not_sent(self):
        with patch.object(bot, "call_ai", return_value="OK") as call:
            bot.AIModelRouter(self.rows).ask(CFG, "someone", "x" * 400 + " lopotik")
        self.assertNotIn("viewer_context", call.call_args.kwargs)

    def test_testing_uses_all_unsaved_profiles_and_fixes_same_snapshot_for_models(self):
        auth = testing.credentials("https://example.com/v1", "private-key")
        drafts = [profile(aliases=("хомячок",), prompt="Новая несохранённая инструкция")]
        snapshot = testing.make_snapshot(auth, ["one", "two"], "Расскажи про хомячк", "Новый общий",
                                         "viewer", "someone", memory_data=self.memory,
                                         profiles=drafts)
        self.assertIn("Новая несохранённая инструкция", str(snapshot.messages))
        self.assertIn("Любит хорроры", snapshot.context)
        self.assertIn("опечатка", snapshot.context)
        self.assertIn("lopotik", snapshot.context)
        drafts[0]["prompt"] = "Позднее изменение"
        drafts[0]["aliases"] = ["Другой ник"]
        payloads = []
        def request(req, timeout):
            payloads.append(json.loads(req.data))
            return io.BytesIO(b'{"choices":[{"message":{"content":"OK"}}]}')
        with patch.object(ai_client.urllib.request, "urlopen", side_effect=request):
            for model in snapshot.models:
                testing.test_model(snapshot, model)
        self.assertEqual(payloads[0]["messages"], payloads[1]["messages"])
        self.assertNotIn("Позднее изменение", str(payloads))
        self.assertEqual(payloads[0]["messages"][-1]["content"], "Зритель someone спрашивает: Расскажи про хомячк")

    def test_testing_reports_ambiguity_disabled_and_redacts_context_secret(self):
        auth = testing.credentials("https://example.com/v1", "private-key")
        rows = [profile(aliases=("леха",)), profile("other", "456", aliases=("Леха",))]
        snapshot = testing.make_snapshot(auth, ["one"], "леха", "Общий", "owner", profiles=rows)
        self.assertIn("неоднозначное", snapshot.context)
        self.assertNotIn("Шути про пельмени", str(snapshot.messages))
        rows[0].update(enabled=False, prompt="private-key")
        snapshot = testing.make_snapshot(auth, ["one"], "лопотик", "Общий", "viewer", profiles=rows)
        self.assertIn("отключена", snapshot.context)
        self.assertNotIn("private-key", snapshot.context)

    def test_long_instruction_does_not_hide_related_memory_preview_or_escaped_key(self):
        auth = testing.credentials("https://example.com/v1", 'sk-"private"')
        rows = [profile(prompt="Личная инструкция " * 500)]
        memory = {"streamer": {"facts": [], "jokes": []}, "viewers": [
            memory_card("old", "123", "Любит хорроры " + auth.api_key)]}
        snapshot = testing.make_snapshot(auth, ["one"], "лопотик", "Общий", "viewer", profiles=rows,
                                         memory_data=memory)
        self.assertIn("Любит хорроры", snapshot.context)
        self.assertNotIn(auth.api_key, snapshot.context)
        self.assertNotIn(json.dumps(auth.api_key)[1:-1], snapshot.context)
        self.assertIn("[ключ скрыт]", snapshot.context)

    def test_autonomous_authors_and_mentions_match_profiles_by_id_and_alias(self):
        participants = [{"author": "renamed", "user_id": "123", "text": "Хочу шутку", "time": 1, "sequence": 1},
                        {"author": "someone", "user_id": "999", "text": "Что думаешь про лоптика?", "time": 2, "sequence": 2}]
        context = related_context(self.rows, "\n".join(row["text"] for row in participants), self.memory,
                                  participants=participants)
        self.assertIn("Шути про пельмени", context.prompt)
        self.assertIn("renamed", context.prompt)
        self.assertNotIn("Другой промпт", context.prompt)
        cfg = {**CFG, "TWITCH_CHANNEL": "channel", "TWITCH_BOT_NAME": "helper"}
        captured = []
        def request(req, timeout):
            captured.append(json.loads(req.data))
            value = ({"action": "reply", "conversation": [1, 2], "basis": [2], "target": "someone",
                      "reason": "answer", "intent": "Ответить про знакомого зрителя"}
                     if len(captured) == 1 else {"text": "Знакомый любитель пельменей!"})
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": json.dumps(value)}}]}).encode())
        with patch.object(ai_client.urllib.request, "urlopen", side_effect=request):
            autonomous.request_decision(cfg, participants, autonomous.AutoSettings(), profiles=self.rows, memory_data=self.memory)
        self.assertEqual(captured[0]["model"], "primary")
        self.assertEqual(len(captured), 2)
        self.assertIn("Шути про пельмени", str(captured[1]["messages"]))
        self.assertIn("Общий стиль", captured[1]["messages"][0]["content"])

    def test_autonomous_author_id_does_not_fall_back_to_recycled_login(self):
        self.assertEqual(related_context(self.rows, "Привет", participants=[{
            "author": "lopotik", "user_id": "999"}]).prompt, "")
        rows = validate_profiles([profile("lopotik", "123", "По ID", enabled=False),
                                  profile("renamed", "", "По логину")])
        self.assertEqual(related_context(rows, "Привет", participants=[{
            "author": "renamed", "user_id": "123"}]).prompt, "")

    def test_observed_id_identity_wins_over_another_profiles_stale_login(self):
        rows = validate_profiles([profile("lopotik", "123", "Старый владелец ника"),
                                  profile("", "999", "Настоящий участник")])
        context = related_context(rows, "Как тебе lopotik?", participants=[{"author": "lopotik", "user_id": "999"}])
        self.assertIn("Настоящий участник", context.prompt)
        self.assertNotIn("Старый владелец ника", context.prompt)

    def test_chat_buffer_keeps_server_identity_for_autonomous_matching(self):
        buffer = autonomous.ChatBuffer()
        self.assertTrue(buffer.add_irc("@user-id=123 :renamed!renamed PRIVMSG #channel :Новый вопрос",
                                      "channel", "helper", 10, autonomous.AutoSettings()))
        self.assertEqual(buffer.messages[0]["user_id"], "123")
