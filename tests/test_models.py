from __future__ import annotations

import unittest

from wecom_mail_mcp.models import SendEmailRequest, normalize_content_type


class NormalizeContentTypeTests(unittest.TestCase):
    def test_normalizes_plain_text_aliases(self) -> None:
        self.assertEqual(normalize_content_type("text"), "text")
        self.assertEqual(normalize_content_type("text/plain"), "text")
        self.assertEqual(normalize_content_type("plain"), "text")

    def test_normalizes_html_aliases(self) -> None:
        self.assertEqual(normalize_content_type("html"), "html")
        self.assertEqual(normalize_content_type("text/html"), "html")

    def test_rejects_unknown_content_type(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported content_type"):
            normalize_content_type("markdown")


class SendEmailRequestTests(unittest.TestCase):
    def test_builds_valid_request(self) -> None:
        request = SendEmailRequest(
            to_email="user@example.com",
            subject="Test subject",
            content="Hello from WeCom",
            content_type="text/plain",
        )

        self.assertEqual(request.to_email, "user@example.com")
        self.assertEqual(request.content_type, "text")

    def test_rejects_empty_subject(self) -> None:
        with self.assertRaisesRegex(ValueError, "Subject cannot be empty"):
            SendEmailRequest(
                to_email="user@example.com",
                subject="   ",
                content="hello",
                content_type="text",
            )

    def test_rejects_invalid_email(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid email address"):
            SendEmailRequest(
                to_email="not-an-email",
                subject="Hello",
                content="hello",
                content_type="text",
            )


if __name__ == "__main__":
    unittest.main()
