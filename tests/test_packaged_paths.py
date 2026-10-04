import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import paths


class PackagedPathsTests(unittest.TestCase):
    def test_nuitka_keeps_user_settings_in_appdata(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"APPDATA": folder}), patch.dict(paths.__dict__, {"__compiled__": object()}):
            data = paths.data_dir()
            self.assertEqual(data, Path(folder) / "TwitchAIBot")
            self.assertTrue(data.is_dir())
            self.assertNotEqual(data, paths.resource_path(""))

    def test_compiled_worker_uses_application_cli_with_unicode_path(self):
        log = Path("Папка с пробелами") / "bot.log"
        with patch.dict(paths.__dict__, {"__compiled__": object()}):
            program, arguments = paths.worker_command(log, self_test=True)
        self.assertEqual(program, sys.executable)
        self.assertEqual(arguments, ["--bot", str(log), "--self-test"])

    def test_pyinstaller_worker_remains_supported(self):
        with patch.object(sys, "frozen", True, create=True):
            self.assertEqual(paths.worker_command(Path("bot.log")), (sys.executable, ["--bot", "bot.log"]))

    def test_source_worker_uses_python_and_script(self):
        program, arguments = paths.worker_command(Path("bot.log"))
        self.assertEqual(program, sys.executable)
        self.assertEqual(arguments, ["-u", str(paths.resource_path("app.py")), "--bot", "bot.log"])


if __name__ == "__main__":
    unittest.main()
