import unittest
from unittest.mock import AsyncMock, patch

import test_meeting_plans as plan_tests
from wecom_mail_mcp.errors import WeComAPIError
from wecom_mail_mcp.meeting_notices import render_meeting_alert


class MeetingNoticeTests(unittest.IsolatedAsyncioTestCase):
    setUp = plan_tests.MeetingPlansTests.setUp
    request = plan_tests.MeetingPlansTests.request

    async def test_success_and_future_window_do_not_send_progress_mail(self):
        result = await self.manager.create(self.request())
        self.assertEqual(len(self.client.invites), 1)
        self.assertEqual(result["counts"]["room_booked"], 4)
        self.assertEqual(result["counts"]["room_pending_window"], 4)
        self.assertEqual(self.client.notices, [])
        self.now += 7 * 86400
        await self.manager.tick()
        self.assertEqual(len(self.client.booked), 5)
        self.assertEqual(self.client.notices, [])

    async def test_conflict_alert_is_specific_and_deduplicated(self):
        self.client.conflict = True
        result = await self.manager.create(self.request(until_date="2026-11-09"))
        self.assertEqual(len(self.client.notices), 1)
        notice = self.client.notices[0]
        self.assertIn("异常", notice.subject)
        self.assertIn("已有预订", notice.content)
        self.assertIn("PORTAL", notice.content)
        self.assertIn("2026-11-09 10:00–11:00", notice.content)
        self.assertNotIn(result["id"], notice.content)
        self.assertNotIn("unknown", notice.content)
        self.assertNotIn("待开放", notice.content)
        await self.manager.tick()
        self.assertEqual(len(self.client.notices), 1)
        self.client.conflict = False
        await self.manager.retry_room(result["id"], plan_tests.ts("2026-11-09"))
        self.assertEqual(len(self.client.notices), 1)  # Recovery success stays quiet.

    async def test_rejection_shows_provider_reason_without_unrelated_warnings(self):
        self.client.book_error = WeComAPIError(
            "meetingroom_book", 40058, "has been booked"
        )
        await self.manager.create(self.request(until_date="2026-11-09"))
        self.assertEqual(len(self.client.notices), 1)
        text = self.client.notices[0].content
        self.assertIn("40058", text)
        self.assertIn("has been booked", text)
        self.assertIn("已停止自动重试", text)
        self.assertNotIn("待开放", text)
        self.assertNotIn("unknown", text)
        self.assertNotIn("room=", text)

    async def test_ambiguous_booking_alert_requests_verification_without_rebooking(
        self,
    ):
        self.client.book_error = TimeoutError()
        await self.manager.create(self.request(until_date="2026-11-09"))
        self.assertEqual(len(self.client.notices), 1)
        self.assertIn("结果尚未确认", self.client.notices[0].content)
        self.assertIn("核实前不会自动重订", self.client.notices[0].content)
        await self.manager.tick()
        self.assertEqual(len(self.client.booked), 1)
        self.assertEqual(len(self.client.notices), 1)

    async def test_invite_failure_still_alerts_when_room_booking_succeeded(self):
        self.client.invite_error = WeComAPIError("compose_send", 40058, "rejected")
        await self.manager.create(self.request(until_date="2026-11-09"))
        self.assertEqual(len(self.client.booked), 1)
        self.assertEqual(len(self.client.notices), 1)
        self.assertIn("日历邀请发送失败", self.client.notices[0].content)
        self.assertNotIn("会议室预订失败", self.client.notices[0].content)

    async def test_transient_read_failure_explains_scheduled_retry_without_claiming_write(
        self,
    ):
        self.client.read_error = TimeoutError()
        await self.manager.create(self.request(until_date="2026-11-09"))
        self.assertEqual(len(self.client.notices), 1)
        self.assertIn("空闲查询", self.client.notices[0].content)
        self.assertIn("自动重试", self.client.notices[0].content)
        self.assertEqual(self.client.booked, [])

    async def test_notification_timeout_is_not_replayed(self):
        self.client.conflict = True
        with patch.object(
            self.client, "send_email", AsyncMock(side_effect=TimeoutError())
        ) as send:
            result = await self.manager.create(self.request(until_date="2026-11-09"))
            await self.manager.tick()
        self.assertEqual(send.await_count, 1)
        self.assertIn("unknown", self.store.get(result["id"])["notices"].values())

    def test_pending_or_expired_invitation_has_its_own_explanation(self):
        plan = {
            "request": {
                "subject": "Review",
                "location": "PORTAL",
                "timezone": "Asia/Shanghai",
            }
        }
        for state, expected in [
            ("not_sent", "后续自动订房已暂停"),
            ("expired", "不再尝试发送"),
        ]:
            text = render_meeting_alert(
                plan,
                [
                    (
                        "event",
                        {"start_time": plan_tests.ts("2026-11-09"), "invite": state},
                        "invite",
                    )
                ],
            )
            self.assertIn(expected, text)
            self.assertNotIn("待开放", text)
