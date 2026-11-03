import os
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from wecom_mail_mcp.config import load_settings
from wecom_mail_mcp.models import SendScheduleEmailRequest, WeeklyRepeat, MailboxInfo
from wecom_mail_mcp.wecom import WeComMailClient
import test_meeting_plans as plan_tests


class RepeatPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_repeat_is_one_compose_send_without_room_booking(self):
        with patch.dict(
            os.environ, {"CORPID": "test", "CORPSECRET": "test"}, clear=True
        ):
            client = WeComMailClient(load_settings(_env_file=None))
        self.addAsyncCleanup(client.aclose)
        calls = []

        async def mailbox():
            return MailboxInfo(email="bot@example.com")

        async def request(method, path, **kw):
            calls.append((path, kw["json_body"]))
            return {}

        req = SendScheduleEmailRequest(
            to_emails=["person@example.com"],
            subject="Series",
            content="Agenda",
            start_time=1794189600,
            end_time=1794193200,
            repeat=WeeklyRepeat(
                repeat_until=1798502399, repeat_day_of_week=[1], timezone=8
            ),
        )
        with (
            patch.object(client, "get_mailbox_info", mailbox),
            patch.object(client, "_request_authed", request),
        ):
            await client.send_schedule_email(req)
        self.assertEqual(len(calls), 1)
        path, payload = calls[0]
        self.assertEqual(path, "/cgi-bin/exmail/app/compose_send")
        rule = payload["schedule"]["reminders"]
        self.assertEqual(rule["is_repeat"], 1)
        self.assertEqual(rule["is_custom_repeat"], 1)
        self.assertEqual(rule["repeat_type"], 1)
        self.assertEqual(rule["repeat_day_of_week"], [1])
        self.assertEqual(rule["repeat_interval"], 1)
        self.assertEqual(rule["repeat_until"], 1798502399)
        self.assertEqual(rule["timezone"], 8)

    def test_bad_recurrence_is_rejected_before_sending(self):
        base = dict(
            to_emails=["x@example.com"],
            subject="Series",
            content="Agenda",
            start_time=1794189600,
            end_time=1794193200,
        )
        for repeat in [
            dict(repeat_until=1),
            dict(repeat_until=1798502399, repeat_day_of_week=[8]),
            dict(repeat_until=1798502399, repeat_interval=0),
            dict(repeat_until=1798502399, repeat_day_of_week=[2]),
        ]:
            with self.subTest(repeat=repeat), self.assertRaises(ValueError):
                SendScheduleEmailRequest(**base, repeat=repeat)


class NativeManagedTests(unittest.IsolatedAsyncioTestCase):
    setUp = plan_tests.MeetingPlansTests.setUp
    request = plan_tests.MeetingPlansTests.request

    async def test_entire_series_is_one_invite_then_worker_only_adds_rooms(self):
        r = await self.manager.create(self.request())
        self.assertEqual(len(self.client.invites), 1)
        req = self.client.invites[0]
        self.assertEqual(req.repeat.repeat_day_of_week, [1])
        self.assertEqual(req.repeat.repeat_type, 1)
        self.assertEqual(req.repeat.timezone, 8)
        self.assertEqual(
            datetime.fromtimestamp(req.repeat.repeat_until, timezone.utc).isoformat(),
            "2026-12-28T15:59:59+00:00",
        )
        self.assertEqual(r["calendar_mode"], "native_repeat")
        self.assertNotIn("invite", r["occurrences"][0])
        self.now += 7 * 86400
        await self.manager.tick()
        self.assertEqual(len(self.client.invites), 1)
        self.assertGreater(len(self.client.booked), 4)

    async def test_worker_does_not_send_pending_or_unknown_series(self):
        r = await self.manager.create(self.request())
        plan = self.store.get(r["id"])
        plan["invite"]["state"] = "pending"
        self.store.save(plan)
        self.now += 7 * 86400
        await self.manager.tick()
        self.assertEqual(self.manager.status(r["id"])["invite"]["state"], "not_sent")
        self.assertEqual(len(self.client.invites), 1)
        self.assertEqual(len(self.client.booked), 4)

    async def test_legacy_per_occurrence_plan_is_quarantined_without_resend(self):
        r = await self.manager.create(self.request())
        plan = self.store.get(r["id"])
        plan.pop("schema_version")
        plan.pop("invite")
        for row in plan["occurrences"]:
            row["invite"] = "pending"
        self.store.save(plan)
        await self.manager.tick()
        result = self.manager.status(r["id"])
        self.assertEqual(result["calendar_mode"], "legacy_requires_review")
        self.assertEqual(result["state"], "requires_review")
        self.assertEqual(len(self.client.invites), 1)

    async def test_explicit_retry_of_definite_failure_uses_one_series_and_unknown_is_refused(
        self,
    ):
        from wecom_mail_mcp.errors import WeComAPIError

        self.client.invite_error = WeComAPIError("send", 42, "reject")
        r = await self.manager.create(self.request())
        self.client.invite_error = None
        await self.manager.retry_invitation(r["id"])
        self.assertEqual(len(self.client.invites), 2)
        self.assertTrue(all(req.repeat is not None for req in self.client.invites))
        with self.assertRaises(ValueError):
            await self.manager.retry_invitation(r["id"])

    async def test_fixed_timezone_required_for_dst_crossing(self):
        with self.assertRaisesRegex(ValueError, "夏令时"):
            first = datetime(2026, 11, 9, 10, tzinfo=ZoneInfo("America/New_York"))
            self.request(
                timezone="America/New_York",
                start_time=int(first.timestamp()),
                end_time=int(first.timestamp()) + 3600,
                until_date="2027-04-01",
            )
