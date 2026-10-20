"""Startup diagnostics never need a repository, network or installed Git."""

import io
import unittest
from unittest.mock import Mock, patch

import app_version


class AppVersionTests(unittest.TestCase):
    def test_source_without_git_and_packaged_without_metadata_have_fallbacks(self):
        with patch("app_version.Path.read_text", side_effect=FileNotFoundError), \
                patch("app_version.subprocess.run", side_effect=OSError):
            self.assertEqual(app_version.build_id(), "source-unknown")
        with patch("app_version.Path.read_text", side_effect=FileNotFoundError), \
                patch("app_version.sys.frozen", True, create=True), patch("app_version.subprocess.run") as git:
            self.assertEqual(app_version.build_id(), "packaged-unknown")
            git.assert_not_called()

    def test_build_metadata_and_git_are_validated_without_echoing_invalid_values(self):
        with patch("app_version.Path.read_text", return_value='{"commit":"abcdef0123456789"}'):
            self.assertEqual(app_version.build_id(), "abcdef0123456789")
        with patch("app_version.Path.read_text", return_value='{"commit":"SECRET_TOKEN"}'), \
                patch("app_version.subprocess.run", return_value=Mock(returncode=0, stdout="f" * 40)):
            self.assertEqual(app_version.build_id(), "f" * 40)

    def test_header_has_version_commit_date_and_explicit_utc(self):
        stream = io.StringIO()
        with patch("app_version.build_id", return_value="abcdef0"), patch("sys.stdout", stream):
            app_version.log_startup()
        self.assertIn("version=2026.10.20 commit=abcdef0", stream.getvalue())
        self.assertRegex(stream.getvalue(), r"started_at=\d{4}-\d\d-\d\dT.*\+00:00 log_timezone=UTC")
