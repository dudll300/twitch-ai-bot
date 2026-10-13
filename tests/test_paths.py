"""Frozen applications must keep writable data outside the installed bundle."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import paths


class DataPathsTests(unittest.TestCase):
    def test_native_frozen_locations(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            for platform, env, relative in (
                ("win32", {"APPDATA": str(home / "roaming")}, "roaming/TwitchAIBot"),
                ("win32", {}, "AppData/Roaming/TwitchAIBot"),
                ("darwin", {}, "Library/Application Support/TwitchAIBot"),
                ("linux", {}, ".config/TwitchAIBot"),
                ("linux", {"XDG_CONFIG_HOME": str(home / "custom")}, "custom/TwitchAIBot"),
                ("linux", {"XDG_CONFIG_HOME": "relative"}, ".config/TwitchAIBot"),
            ):
                with self.subTest(platform=platform, env=env), \
                        patch.object(paths.sys, "frozen", True, create=True), \
                        patch.object(paths.sys, "platform", platform), \
                        patch.object(Path, "home", return_value=home), \
                        patch.dict(os.environ, env, clear=True):
                    result = paths.data_dir()
                    self.assertEqual(result, home / relative)
                    self.assertTrue(result.is_dir())

    def test_source_location_is_unchanged(self):
        with patch.object(paths.sys, "frozen", False, create=True):
            self.assertEqual(paths.data_dir(), Path(paths.__file__).resolve().parent)
