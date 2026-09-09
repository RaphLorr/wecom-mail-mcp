"""The wiring test: attachments must actually reach the compose_send payload."""

from __future__ import annotations

import base64
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wecom_mail_mcp.attachments import AttachmentError
from wecom_mail_mcp.config import load_settings
from wecom_mail_mcp.models import MailboxInfo, SendEmailRequest
from wecom_mail_mcp.wecom import WeComMailClient


class SendEmailPayloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._original_cwd = Path.cwd()
        self._temp_dir = Path(tempfile.mkdtemp())
        os.chdir(self._temp_dir)
        self.addCleanup(shutil.rmtree, self._temp_dir)
        self.addCleanup(os.chdir, self._original_cwd)

        self.allowed = self._temp_dir / "allowed"
        self.allowed.mkdir()
        self.outside = self._temp_dir / "outside"
        self.outside.mkdir()

    def _client(self, roots: str | None) -> WeComMailClient:
        env = {"CORPID": "corp", "CORPSECRET": "secret"}
        if roots is not None:
            env["WECOM_ATTACHMENT_ROOTS"] = roots
        with patch.dict(os.environ, env, clear=True):
            settings = load_settings()
        client = WeComMailClient(settings)
        self.addAsyncCleanup(client.aclose)
        return client

    async def _capture(self, client: WeComMailClient, request: SendEmailRequest) -> dict:
        captured: dict = {}

        async def fake_request(method, path, *, endpoint_name, json_body=None, params=None):
            captured["method"] = method
            captured["path"] = path
            captured["body"] = json_body
            return {}

        async def fake_mailbox(*_args, **_kwargs):
            return MailboxInfo(email="bot@example.com")

        with patch.object(client, "_request_authed", fake_request), patch.object(
            client, "get_mailbox_info", fake_mailbox
        ):
            await client.send_email(request)
        return captured

    async def test_omits_attachment_list_when_there_are_none(self) -> None:
        client = self._client(str(self.allowed))
        captured = await self._capture(
            client,
            SendEmailRequest(to_email="a@b.com", subject="s", content="hello"),
        )

        self.assertEqual(captured["path"], "/cgi-bin/exmail/app/compose_send")
        self.assertNotIn("attachment_list", captured["body"])

    async def test_puts_base64_attachments_in_the_payload(self) -> None:
        target = self.allowed / "report.csv"
        target.write_bytes(b"a,b\n1,2\n")
        client = self._client(str(self.allowed))

        captured = await self._capture(
            client,
            SendEmailRequest(
                to_email="a@b.com",
                subject="s",
                content="hello",
                attachments=[str(target)],
            ),
        )

        attachment_list = captured["body"]["attachment_list"]
        self.assertEqual(len(attachment_list), 1)
        self.assertEqual(attachment_list[0]["file_name"], "report.csv")
        self.assertEqual(base64.b64decode(attachment_list[0]["content"]), b"a,b\n1,2\n")

    async def test_refuses_a_file_outside_the_allowed_roots(self) -> None:
        secret = self.outside / "secret.json"
        secret.write_bytes(b"token")
        client = self._client(str(self.allowed))

        with self.assertRaisesRegex(AttachmentError, "outside the allowed roots"):
            await self._capture(
                client,
                SendEmailRequest(
                    to_email="a@b.com",
                    subject="s",
                    content="hello",
                    attachments=[str(secret)],
                ),
            )

    async def test_refuses_attachments_when_no_roots_are_configured(self) -> None:
        target = self.allowed / "report.csv"
        target.write_bytes(b"x")
        client = self._client(None)

        with self.assertRaisesRegex(AttachmentError, "Attachments are disabled"):
            await self._capture(
                client,
                SendEmailRequest(
                    to_email="a@b.com",
                    subject="s",
                    content="hello",
                    attachments=[str(target)],
                ),
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
