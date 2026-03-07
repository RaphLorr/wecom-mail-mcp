from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wecom_mail_mcp.config import load_settings


class SettingsLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_cwd = Path.cwd()
        self._temp_dir = Path(tempfile.mkdtemp())
        os.chdir(self._temp_dir)

    def tearDown(self) -> None:
        os.chdir(self._original_cwd)
        shutil.rmtree(self._temp_dir)

    def test_dotenv_overrides_process_environment(self) -> None:
        (self._temp_dir / ".env").write_text(
            "CORPID=corp-from-dotenv\nCORPSECRET=secret-from-dotenv\n",
            encoding="utf-8",
        )

        with patch.dict(
            os.environ,
            {"CORPID": "corp-from-env", "CORPSECRET": "secret-from-env"},
            clear=False,
        ):
            settings = load_settings()

        self.assertEqual(settings.wecom_corp_id, "corp-from-dotenv")
        self.assertEqual(settings.wecom_corp_secret.get_secret_value(), "secret-from-dotenv")

    def test_process_environment_is_used_when_dotenv_is_empty(self) -> None:
        (self._temp_dir / ".env").write_text("", encoding="utf-8")

        with patch.dict(
            os.environ,
            {"CORPID": "corp-from-env", "CORPSECRET": "secret-from-env"},
            clear=False,
        ):
            settings = load_settings()

        self.assertEqual(settings.wecom_corp_id, "corp-from-env")
        self.assertEqual(settings.wecom_corp_secret.get_secret_value(), "secret-from-env")

    def test_process_environment_fills_missing_dotenv_values(self) -> None:
        (self._temp_dir / ".env").write_text("CORPID=corp-from-dotenv\nCORPSECRET=\n", encoding="utf-8")

        with patch.dict(
            os.environ,
            {"CORPID": "corp-from-env", "CORPSECRET": "secret-from-env"},
            clear=False,
        ):
            settings = load_settings()

        self.assertEqual(settings.wecom_corp_id, "corp-from-dotenv")
        self.assertEqual(settings.wecom_corp_secret.get_secret_value(), "secret-from-env")


if __name__ == "__main__":
    unittest.main()
