import os
import unittest
from unittest.mock import patch

from wecom_mail_mcp.config import load_settings
from wecom_mail_mcp.server import create_server


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_managed_tools_are_registered_with_partial_success_and_delivery_guidance(self):
        with patch.dict(os.environ, {'CORPID': 'test', 'CORPSECRET': 'test'}, clear=True):
            settings=load_settings(_env_file=None)
        mcp=create_server(settings)
        tools={t.name:t for t in await mcp.list_tools()}
        for name in ['create_managed_meeting','get_managed_meeting','list_managed_meetings','cancel_managed_meeting','retry_managed_room_booking']:
            self.assertIn(name,tools)
        self.assertIn('request_id',str(tools['create_managed_meeting'].inputSchema))
        self.assertIn('location 仅为文字',tools['send_schedule_email'].description)
