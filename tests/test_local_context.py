import copy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from local_context import (CATALOG_BUDGET, CONTEXT_BUDGET, DOCUMENT_NAME, USAGE_NAME,
                           LocalContextManager, LocalReply, LocalSettings, direct_bundle,
                           direct_only, document_raw, load_document, normalize,
                           prepare_context, save_document, validate_document)


def card(card_id="loss", name="Сасун", aliases=None, *, situational=True, **changes):
    value = {"id": card_id, "name": name, "aliases": aliases or ["sasun"],
             "meaning": "Местный подкол по поводу серии проигрышей.",
             "situations": "Несколько поражений подряд, дружеский разговор.",
             "avoid": "Не использовать при расстройстве или серьёзной теме.",
             "example": "Опять сасун", "enabled": True, "allow_situational": situational}
    value.update(changes)
    return value


def document(cards=None, **settings):
    values = {"enabled": True, "global_pause_seconds": 300, "card_pause_seconds": 1800,
              "hourly_limit": 4, "max_per_reply": 1}
    values.update(settings)
    return {"version": 1, "settings": values, "cards": [card()] if cards is None else cards}


class LocalDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / DOCUMENT_NAME

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_document_is_empty_disabled_and_does_not_create_files(self):
        snapshot = load_document(self.path)
        self.assertFalse(snapshot.settings.enabled)
        self.assertEqual(snapshot.cards, ())
        self.assertEqual(snapshot.error, "")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_snapshots_and_cards_are_immutable_and_ids_survive_rename(self):
        first = save_document(self.path, document())
        with self.assertRaises(FrozenInstanceError):
            first.cards[0].name = "renamed"
        changed = document_raw(first)
        changed["cards"][0]["name"] = "Новое название"
        second = save_document(self.path, changed)
        self.assertEqual(first.cards[0].id, second.cards[0].id)
        self.assertNotEqual(first.revision, second.revision)
        self.assertEqual(load_document(self.path), second)
        self.assertEqual(first.cards[0].aliases, ("sasun",))

    def test_json_spacing_key_order_do_not_change_revision(self):
        initial = save_document(self.path, document())
        self.path.write_text(json.dumps(document(), ensure_ascii=False), encoding="utf-8")
        self.assertEqual(initial.revision, load_document(self.path).revision)

    def test_invalid_schema_types_lengths_duplicate_ids_and_aliases(self):
        invalid = []
        unknown_root = document(); unknown_root["secret"] = "not allowed"; invalid.append(unknown_root)
        unknown_settings = document(); unknown_settings["settings"]["bad"] = 1; invalid.append(unknown_settings)
        unknown_card = document(); unknown_card["cards"][0]["bad"] = True; invalid.append(unknown_card)
        invalid.extend([document(enabled=1), document(hourly_limit=True), document(global_pause_seconds=-1),
                        document(max_per_reply=2), document(max_per_reply=1.0),
                        document([card(name="x" * 81)]), document([card(meaning="")]),
                        document([card(meaning="x" * 601)]), document([card(avoid="x" * 601)]),
                        document([card(example="x" * 301)]), document([card(situations="")]),
                        document([card(aliases=["one"] * 21)]), document([card(aliases=["Еж", "ёж"])]),
                        document([card(aliases=["line\nbreak"])]), document([card(aliases="sasun")]),
                        document([card(card_id="bad id")]), document([card(enabled="yes")]),
                        document([card(), card(name="other", aliases=["other"])]),
                        document([card(), card("other", "Other", ["SASUN"])]),
                        document([card(allow_situational=False), card("other", "sasun", ["unique"])])])
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_document(raw)

    def test_card_count_and_catalog_budget_are_rejected_without_dropping_cards(self):
        rows = [card(str(index), f"Name{index}", [f"alias{index}"], situational=False) for index in range(41)]
        with self.assertRaisesRegex(ValueError, "40"):
            validate_document(document(rows))
        rows = [card(str(index), f"Name{index}", [f"alias{index}"], meaning="m" * 600,
                     situations="s" * 600, avoid="a" * 600, example="e" * 300) for index in range(6)]
        with self.assertRaisesRegex(ValueError, str(CATALOG_BUDGET)):
            validate_document(document(rows))
        for row in rows:
            row["allow_situational"] = False
        self.assertEqual(len(validate_document(document(rows)).cards), 6)

    def test_corrupt_load_does_not_overwrite_and_explicit_repair_creates_backup(self):
        broken = b'{"version": 1, "cards": broken'
        self.path.write_bytes(broken)
        result = load_document(self.path)
        self.assertTrue(result.error)
        self.assertFalse(result.settings.enabled)
        self.assertEqual(self.path.read_bytes(), broken)
        with self.assertRaises(ValueError):
            save_document(self.path, result)
        saved = save_document(self.path, document())
        self.assertFalse(saved.error)
        backups = list(self.root.glob("local-context.corrupt-*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), broken)

    def test_duplicate_json_fields_are_corruption(self):
        self.path.write_text('{"version":1,"version":1,"settings":{},"cards":[]}', encoding="utf-8")
        self.assertTrue(load_document(self.path).error)

    def test_atomic_replace_failure_keeps_original_and_cleans_temporary_file(self):
        save_document(self.path, document())
        old = self.path.read_bytes()
        with patch("local_context.os.replace", side_effect=OSError("denied")):
            with self.assertRaises(OSError):
                save_document(self.path, document(enabled=False))
        self.assertEqual(self.path.read_bytes(), old)
        self.assertEqual(list(self.root.glob("*.tmp")), [])


class LocalMatchingTests(unittest.TestCase):
    def test_normalization_unicode_case_yo_and_spaces(self):
        self.assertEqual(normalize("  ЁЖ\t ＳＡＳＵＮ\n"), "еж sasun")
        snapshot = validate_document(document([card(name="Ёж", aliases=["Синий  ёж", "ＳＡＳＵＮ"], situational=False)]))
        for text in ("Про СИНИЙ\nеж.", "Что значит sasun?", "ёж!"):
            self.assertEqual([row.id for row in direct_bundle(snapshot, text).direct], ["loss"])

    def test_no_substring_fuzzy_or_alias_fragment_matches(self):
        snapshot = validate_document(document([card(name="Сасун", aliases=["sasun", "синий ёж"], situational=False)]))
        for text in ("sasuna", "presasun", "sasun_123", "sasum", "ёж", "синий"):
            self.assertFalse(direct_bundle(snapshot, text).has_context, text)

    def test_longest_overlapping_phrase_wins_and_repeated_card_is_deduplicated(self):
        snapshot = validate_document(document([
            card("short", "Ёж", ["еж"], situational=False),
            card("long", "Синий ёж", ["blue hedgehog"], situational=False)]))
        result = direct_bundle(snapshot, "Синий ёж, снова синий ёж!")
        self.assertEqual([row.id for row in result.direct], ["long"])
        result = direct_bundle(snapshot, "Синий ёж, а отдельно ёж.")
        self.assertEqual({row.id for row in result.direct}, {"short", "long"})

    def test_disabled_system_and_card_do_not_add_context(self):
        for raw in (document(enabled=False), document([card(enabled=False)]), document([])):
            self.assertFalse(direct_bundle(validate_document(raw), "sasun").has_context)

    def test_ambiguous_in_memory_alias_is_not_assigned_to_an_arbitrary_card(self):
        valid = validate_document(document([
            card("first", "Первый", ["синий еж"], situational=False),
            card("second", "Второй", ["другая фраза"], situational=False),
            card("short", "Еж", ["ежик"], situational=False)]))
        ambiguous = replace(valid, cards=(valid.cards[0],
                            replace(valid.cards[1], aliases=("синий ёж",)), valid.cards[2]))
        self.assertFalse(direct_bundle(ambiguous, "Синий еж").has_context)
        # A separate unambiguous mention still works.
        self.assertEqual([row.id for row in direct_bundle(ambiguous, "Синий еж, а отдельно ежик").direct],
                         ["short"])

    def test_runtime_budget_disables_entire_bundle_without_partial_definitions(self):
        rows = [card(str(index), f"Name{index}", [f"alias{index}"], situational=False,
                     meaning="m" * 600, situations="s" * 600, avoid="a" * 600,
                     example="e" * 300) for index in range(8)]
        snapshot = validate_document(document(rows))
        result = direct_bundle(snapshot, " ".join(row["name"] for row in rows))
        self.assertTrue(result.error)
        self.assertIn(str(CONTEXT_BUDGET), result.error)
        self.assertFalse(result.has_context)
        self.assertEqual(result.prompt, "")
        self.assertEqual(result.direct, ())

    def test_direct_only_and_private_reply_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            save_document(root / DOCUMENT_NAME, document())
            manager = LocalContextManager(root)
            bundle = manager.bundle(manager.snapshot(), "sasun")
            plain = direct_only(bundle)
            self.assertEqual(plain.direct, bundle.direct)
            self.assertEqual(plain.creative, ())
            self.assertIn("direct_understanding_only", plain.prompt)
            self.assertIn("avoid", plain.prompt)
            reply = LocalReply("В чате только текст.", bundle, "loss")
            self.assertEqual(str(reply), "В чате только текст.")
            self.assertIs(reply.local_bundle, bundle)
            self.assertEqual(reply.creative_card_id, "loss")
            self.assertNotIn("creative_card_id", str(reply))


class LocalManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = 10000
        self.events = []
        self.path = self.root / DOCUMENT_NAME
        self.raw = document([card(), card("win", "Победа", ["victory"], meaning="Поздравление.")])
        save_document(self.path, self.raw)
        self.manager = LocalContextManager(self.root, clock=lambda: self.now, emit=self.events.append)

    def tearDown(self):
        self.temp.cleanup()

    def bundle(self, text=""):
        return self.manager.bundle(self.manager.snapshot(), text)

    def publish(self, card_id="loss"):
        lease = self.manager.reserve_publish(self.bundle(), card_id)
        self.assertIsNotNone(lease)
        self.assertTrue(self.manager.complete_publish(lease, success=True))

    def test_empty_off_directonly_and_zero_settings_preserve_ordinary_path(self):
        for changes in ({"enabled": False}, {"max_per_reply": 0}, {"hourly_limit": 0}):
            raw = copy.deepcopy(self.raw)
            raw["settings"].update(changes)
            save_document(self.path, raw)
            result = self.bundle("sasun")
            self.assertFalse(result.creative)
            self.assertEqual(bool(result.direct), changes.get("enabled") is not False)
        save_document(self.path, self.raw)
        result = self.manager.bundle(self.manager.snapshot(), "sasun", creative=False)
        self.assertTrue(result.direct)
        self.assertFalse(result.creative)

    def test_success_counts_once_and_global_and_per_card_pause_are_shared(self):
        original = self.bundle()
        self.publish()
        self.assertFalse(self.manager.is_allowed(original, "loss"))
        self.assertEqual(self.bundle().creative, ())
        self.now += 300
        self.assertEqual(self.bundle().candidate_ids, {"win"})
        self.assertTrue(self.bundle("sasun").direct)
        self.now += 1500
        self.assertIn("loss", self.bundle().candidate_ids)
        usage = json.loads((self.root / USAGE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(set(usage), {"version", "uses"})
        self.assertEqual(usage["uses"], [{"card_id": "loss", "time": 10000}])

    def test_cancel_error_preview_and_plain_answer_do_not_consume_quota(self):
        bundle = self.bundle()
        self.assertTrue(self.manager.is_allowed(bundle, None))
        self.assertIsNone(self.manager.reserve_publish(bundle, None))
        self.assertTrue(self.manager.is_allowed(bundle, "loss"))  # Preview: no reservation/publication.
        self.assertFalse((self.root / USAGE_NAME).exists())
        lease = self.manager.reserve_publish(bundle, "loss")
        self.assertFalse(self.manager.complete_publish(lease, success=False))
        self.assertFalse((self.root / USAGE_NAME).exists())
        self.assertIn("loss", self.bundle().candidate_ids)
        self.assertFalse(self.manager.complete_publish(lease, success=True))

    def test_reservation_prevents_parallel_reward_and_autonomous_choice(self):
        bundle = self.bundle()
        start = threading.Barrier(3)
        leases = []
        def reserve(card_id):
            start.wait()
            leases.append(self.manager.reserve_publish(bundle, card_id))
        threads = [threading.Thread(target=reserve, args=(card_id,)) for card_id in ("loss", "win")]
        for thread in threads:
            thread.start()
        start.wait()
        for thread in threads:
            thread.join(timeout=2)
        self.assertEqual(sum(lease is not None for lease in leases), 1)
        self.assertEqual(self.bundle().creative, ())
        self.assertFalse(self.manager.is_allowed(bundle, "loss"))
        self.manager.complete_publish(next(lease for lease in leases if lease), success=False)
        self.assertEqual(self.bundle().candidate_ids, {"loss", "win"})

    def test_hour_limit_slides_and_restart_and_toggle_preserve_limits(self):
        raw = document([card()], global_pause_seconds=0, card_pause_seconds=0, hourly_limit=2)
        save_document(self.path, raw)
        self.publish()
        self.now += 10
        self.publish()
        self.assertEqual(self.bundle().creative, ())
        restarted = LocalContextManager(self.root, clock=lambda: self.now)
        self.assertEqual(restarted.bundle(restarted.snapshot(), "").creative, ())
        raw["settings"]["enabled"] = False
        save_document(self.path, raw)
        self.assertFalse(self.bundle().has_context)
        raw["settings"]["enabled"] = True
        save_document(self.path, raw)
        self.assertEqual(self.bundle().creative, ())
        self.now = 13600
        self.assertIn("loss", self.bundle().candidate_ids)

    def test_manual_edit_and_disable_invalidate_inflight_bundle(self):
        for change in ("meaning", "enabled", "system"):
            save_document(self.path, self.raw)
            bundle = self.bundle()
            raw = copy.deepcopy(self.raw)
            if change == "system":
                raw["settings"]["enabled"] = False
            elif change == "enabled":
                raw["cards"][0]["enabled"] = False
            else:
                raw["cards"][0]["meaning"] = "Изменённое значение."
            save_document(self.path, raw)
            self.assertFalse(self.manager.is_current(bundle.snapshot))
            self.assertFalse(self.manager.is_allowed(bundle, "loss"))
            self.assertIsNone(self.manager.reserve_publish(bundle, "loss"))

    def test_increasing_cooldown_keeps_old_latest_card_use_after_other_publications(self):
        raw = copy.deepcopy(self.raw)
        raw["settings"].update(global_pause_seconds=0, card_pause_seconds=0, hourly_limit=10)
        save_document(self.path, raw)
        self.publish("loss")
        self.now += 3700
        self.publish("win")
        raw["settings"]["card_pause_seconds"] = 7200
        save_document(self.path, raw)
        restarted = LocalContextManager(self.root, clock=lambda: self.now)
        self.assertFalse(restarted.bundle(restarted.snapshot(), "").creative)
        usage = json.loads((self.root / USAGE_NAME).read_text(encoding="utf-8"))
        self.assertEqual({entry["card_id"] for entry in usage["uses"]}, {"loss", "win"})
        self.now = 17200
        self.assertIn("loss", restarted.bundle(restarted.snapshot(), "").candidate_ids)

    def test_unknown_or_not_passed_card_is_never_allowed(self):
        bundle = self.bundle()
        self.assertFalse(self.manager.is_allowed(bundle, "unknown"))
        self.assertFalse(self.manager.is_allowed(direct_only(bundle), "loss"))
        self.assertFalse(self.manager.is_allowed(bundle, ["loss"]))

    def test_corrupt_dictionary_does_not_stop_bot_or_emit_contents(self):
        self.path.write_text("api-key-secret malformed", encoding="utf-8")
        snapshot = self.manager.snapshot()
        self.assertTrue(snapshot.error)
        self.assertTrue(self.manager.is_current(snapshot))
        self.assertFalse(self.manager.bundle(snapshot, "private chat").has_context)
        self.assertTrue(any(event["action"] == "error" for event in self.events))
        self.assertNotIn("api-key-secret", repr(self.events))
        self.assertNotIn("private chat", repr(self.events))
        self.assertEqual(self.path.read_text(encoding="utf-8"), "api-key-secret malformed")
        save_document(self.path, self.raw)
        self.assertFalse(self.manager.is_current(snapshot))

    def test_unexpected_loader_failure_is_disabled_without_overwriting_dictionary(self):
        original = self.path.read_bytes()
        with patch("local_context.load_document", side_effect=RuntimeError("private-loader-data")):
            snapshot = self.manager.snapshot()
            self.assertTrue(snapshot.error)
            self.assertFalse(snapshot.settings.enabled)
            self.assertTrue(self.manager.is_current(snapshot))
            self.assertFalse(prepare_context(self.manager, "sasun").has_context)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertNotIn("private-loader-data", repr(self.events))

    def test_unexpected_preparation_failure_uses_the_shared_ordinary_request_boundary(self):
        for method in ("snapshot", "bundle"):
            with self.subTest(method=method), patch.object(
                    self.manager, method, side_effect=RuntimeError("private-preparation-data")):
                result = prepare_context(self.manager, "private chat says sasun")
                self.assertFalse(result.has_context)
                self.assertTrue(result.error)
        self.assertNotIn("private-preparation-data", repr(self.events))
        self.assertNotIn("private chat", repr(self.events))
        self.assertFalse(self.manager.usage_path.exists())

    def test_direct_logs_only_card_ids_and_error_logs_are_not_repeated(self):
        result = self.bundle("private chat says sasun")
        self.assertTrue(result.direct)
        direct_events = [event for event in self.events if event["action"] == "direct"]
        self.assertEqual(direct_events[-1]["card_ids"], ["loss"])
        self.assertNotIn("private chat", repr(self.events))
        self.path.write_text("invalid JSON", encoding="utf-8")
        for _ in range(3):
            self.manager.snapshot()
        self.assertEqual(sum(event["action"] == "error" for event in self.events), 1)

    def test_corrupt_usage_disables_creative_but_keeps_direct_understanding(self):
        path = self.root / USAGE_NAME
        broken = '{"version":1,"uses":[{"card_id":"loss","time":NaN}]}'
        path.write_text(broken, encoding="utf-8")
        manager = LocalContextManager(self.root, clock=lambda: self.now)
        result = manager.bundle(manager.snapshot(), "Что такое sasun?")
        self.assertTrue(result.direct)
        self.assertFalse(result.creative)
        self.assertEqual(path.read_text(encoding="utf-8"), broken)

    def test_persistence_failure_fails_closed_and_releases_reservation(self):
        lease = self.manager.reserve_publish(self.bundle(), "loss")
        with patch("local_context.os.replace", side_effect=OSError("secret-file-content")):
            self.assertFalse(self.manager.complete_publish(lease, success=True))
        self.assertEqual(self.bundle().creative, ())
        self.assertTrue(self.bundle("sasun").direct)
        self.assertNotIn("secret-file-content", repr(self.events))


if __name__ == "__main__":
    unittest.main()
