import os
import traceback
import unittest
from unittest.mock import patch

import httpx

from wecom_mail_mcp.config import load_settings
from wecom_mail_mcp.errors import WeComClientError, redact_diagnostic
from wecom_mail_mcp.wecom import WeComMailClient


class ErrorSafetyTests(unittest.IsolatedAsyncioTestCase):
    def test_redacts_credentials_before_truncation(self):
        message = (
            'invalid parameter; {"access_token":"token-value", "corpsecret":"corp-value"} '
            "API_KEY='key-value' Authorization: Bearer bearer-value "
            "https://api.example.test/?access_token=url-value\nnext line"
        )
        safe = redact_diagnostic(message)
        self.assertIn("invalid parameter", safe)
        for secret in (
            "token-value",
            "corp-value",
            "key-value",
            "bearer-value",
            "url-value",
        ):
            self.assertNotIn(secret, safe)
        self.assertNotIn("https://", safe)
        self.assertNotIn("\n", safe)
        self.assertLessEqual(len(redact_diagnostic(message * 200)), 1000)

    async def test_transport_tracebacks_do_not_expose_request_urls(self):
        for kind in ("http", "timeout", "connect"):
            with self.subTest(kind=kind):
                with patch.dict(
                    os.environ, {"CORPID": "test", "CORPSECRET": "test"}, clear=True
                ):
                    client = WeComMailClient(load_settings(_env_file=None))
                original = client._client

                async def handler(request, kind=kind):
                    if kind == "timeout":
                        raise httpx.ReadTimeout("url-token-value", request=request)
                    if kind == "connect":
                        raise httpx.ConnectError(str(request.url), request=request)
                    return httpx.Response(503, request=request)

                client._client = httpx.AsyncClient(
                    base_url="https://api.example.test",
                    transport=httpx.MockTransport(handler),
                )
                try:
                    try:
                        if kind == "http":
                            with self.assertLogs("httpx", level="INFO") as logs:
                                await client._request_json(
                                    "POST",
                                    "/booking",
                                    endpoint_name="meetingroom_book",
                                    params={"access_token": "url-token-value"},
                                )
                        else:
                            await client._request_json(
                                "POST",
                                "/booking",
                                endpoint_name="meetingroom_book",
                                params={"access_token": "url-token-value"},
                            )
                    except WeComClientError:
                        rendered = traceback.format_exc()
                    else:
                        self.fail("Expected safe transport error")
                    self.assertIn("meetingroom_book", rendered)
                    self.assertNotIn("url-token-value", rendered)
                    self.assertNotIn("https://api.example.test", rendered)
                    if kind == "http":
                        self.assertNotIn("url-token-value", str(logs.output))
                finally:
                    await client.aclose()
                    await original.aclose()
