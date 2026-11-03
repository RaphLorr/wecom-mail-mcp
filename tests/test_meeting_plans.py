import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from wecom_mail_mcp.meeting_plans import (
    MeetingPlans,
    PlanStore,
    WeeklyMeetingRequest,
    weekly_occurrences,
)
from wecom_mail_mcp.errors import WeComAPIError


def ts(day, hour=10):
    return int(
        datetime.fromisoformat(day)
        .replace(hour=hour, tzinfo=ZoneInfo("Asia/Shanghai"))
        .timestamp()
    )


class FakeClient:
    def __init__(self):
        self.booked = []
        self.invites = []
        self.notices = []
        self.cancelled = []
        self.conflict = False
        self.book_error = None
        self.invite_error = None
        self.read_error = None
        self.cancel_error = None

    async def query_room_availability(self, *args, **kwargs):
        if self.read_error:
            raise self.read_error
        return [
            {
                "meetingroom_id": kwargs["meetingroom_id"],
                "schedule": [{"start_time": args[0], "end_time": args[1]}]
                if self.conflict
                else [],
            }
        ]

    async def book_meeting_room(self, request):
        self.booked.append(request)
        if self.book_error:
            raise self.book_error
        return "booking-" + str(request.start_time)

    async def send_schedule_email(self, request):
        self.invites.append(request)
        if self.invite_error:
            raise self.invite_error

    async def send_email(self, request):
        self.notices.append(request)

    async def cancel_room_booking(self, booking):
        self.cancelled.append(booking)
        if self.cancel_error:
            raise self.cancel_error


class MeetingPlansTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_worker_bootstrap_performs_no_external_actions(self):
        await self.manager.tick()
        self.assertEqual(self.client.invites, [])
        self.assertEqual(self.client.booked, [])
        self.assertEqual(self.client.notices, [])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "plans.sqlite")
        self.store = PlanStore(self.path, "corp-a")
        self.addCleanup(self.store.close)
        self.now = ts("2026-11-03")
        self.store.heartbeat(self.now)
        self.client = FakeClient()
        self.manager = MeetingPlans(self.store, self.client, clock=lambda: self.now)

    def request(self, **kw):
        data = dict(
            request_id="message-1",
            to_emails=["person@example.com"],
            subject="Weekly",
            content="Agenda",
            location="PORTAL",
            start_time=ts("2026-11-09"),
            end_time=ts("2026-11-09", 11),
            until_date="2026-12-28",
            meetingroom_id=8,
            booker_userid="organizer",
            notify_email="organizer@example.com",
        )
        data.update(kw)
        return WeeklyMeetingRequest(**data)

    async def test_only_open_window_is_booked_and_one_native_repeat_invite_is_sent(
        self,
    ):
        result = await self.manager.create(self.request())
        self.assertEqual(len(self.client.booked), 4)
        self.assertEqual(len(self.client.invites), 1)
        self.assertEqual(result["counts"]["room_pending_window"], 4)
        self.assertFalse(result["complete"])
        self.assertTrue(
            all("不代表所有日期均已预订" in r.content for r in self.client.invites)
        )
        self.assertTrue(all(r.meetingroom_id == 8 for r in self.client.booked))

    async def test_restart_rolls_forward_without_resending_or_rebooking(self):
        result = await self.manager.create(self.request())
        other = PlanStore(self.path, "corp-a")
        self.addCleanup(other.close)
        self.now = ts("2026-11-10")
        await MeetingPlans(other, self.client, clock=lambda: self.now).tick()
        self.assertEqual(len(self.client.invites), 1)
        self.assertEqual(len(self.client.booked), 5)
        self.assertEqual(len({r.start_time for r in self.client.booked}), 5)
        self.assertEqual(
            self.manager.status(result["id"])["counts"]["room_pending_window"], 3
        )

    async def test_idempotent_request_and_changed_payload_are_not_new_sends(self):
        a = await self.manager.create(self.request())
        b = await self.manager.create(self.request())
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(len(self.client.invites), 1)
        with self.assertRaises(ValueError):
            await self.manager.create(self.request(subject="Changed"))
        with self.assertRaises(ValueError):
            await self.manager.create(self.request(request_id="another-message"))
        self.assertEqual(len(self.client.invites), 1)

    async def test_worker_missing_or_stale_fails_before_side_effects(self):
        self.now += 7201
        with self.assertRaises(ValueError):
            await self.manager.create(self.request())
        self.assertEqual(self.store.active(), [])
        self.assertEqual(self.client.invites, [])

    async def test_booking_rejection_is_partial_and_bounded_retry_does_not_resend_invite(
        self,
    ):
        self.client.book_error = WeComAPIError("meetingroom_book", 42, "rejected")
        result = await self.manager.create(self.request(until_date="2026-11-09"))
        self.assertEqual(result["counts"]["room_retry"], 1)
        for _ in range(3):
            self.now += 3601
            await self.manager.tick()
        self.assertEqual(len(self.client.booked), 3)
        self.assertEqual(len(self.client.invites), 1)
        self.assertEqual(self.manager.status(result["id"])["counts"]["room_failed"], 1)

    async def test_booking_timeout_is_unknown_never_automatically_retried(self):
        self.client.book_error = TimeoutError()
        result = await self.manager.create(self.request(until_date="2026-11-09"))
        self.assertEqual(result["counts"]["room_unknown"], 1)
        self.now += 3601
        await self.manager.tick()
        self.assertEqual(len(self.client.booked), 1)
        with self.assertRaises(ValueError):
            await self.manager.retry_room(result["id"], ts("2026-11-09"))

    async def test_invite_timeout_does_not_undo_booking_or_resend(self):
        self.client.invite_error = TimeoutError()
        result = await self.manager.create(self.request(until_date="2026-11-09"))
        self.assertEqual(result["counts"]["invite_unknown"], 1)
        self.assertEqual(result["counts"]["room_booked"], 1)
        self.now += 3601
        await self.manager.tick()
        self.assertEqual(len(self.client.invites), 1)
        self.assertEqual(len(self.client.booked), 1)

    async def test_conflict_is_explicit_and_manual_retry_only_books_original_room(self):
        self.client.conflict = True
        r = await self.manager.create(self.request(until_date="2026-11-09"))
        self.assertEqual(r["counts"]["room_conflict"], 1)
        self.assertEqual(self.client.booked, [])
        self.client.conflict = False
        r = await self.manager.retry_room(r["id"], ts("2026-11-09"))
        self.assertTrue(r["complete"])
        self.assertEqual(len(self.client.invites), 1)

    async def test_cancel_stops_future_bookings_and_keeps_calendar_cancellation_limit_visible(
        self,
    ):
        r = await self.manager.create(self.request())
        result = await self.manager.cancel(r["id"])
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(len(self.client.cancelled), 4)
        self.now = ts("2026-12-01")
        await self.manager.tick()
        self.assertEqual(len(self.client.booked), 4)
        self.assertEqual(len(self.client.invites), 1)
        self.assertIn("不取消已发送", result["warning"])

    async def test_cancel_error_retains_booking_id_and_is_not_reported_successful(self):
        r = await self.manager.create(self.request(until_date="2026-11-09"))
        self.client.cancel_error = TimeoutError()
        result = await self.manager.cancel(r["id"])
        row = result["occurrences"][0]
        self.assertEqual(row["room"], "booked")
        self.assertIn("cancel_error", row)
        self.assertIn("booking_id", row)

    async def test_process_lock_prevents_duplicate_writer(self):
        other = PlanStore(self.path, "corp-a")
        self.addCleanup(other.close)
        with other.exclusive() as acquired:
            self.assertTrue(acquired)
            r = await self.manager.create(self.request())
            self.assertEqual(r["status"], "busy")
        self.assertEqual(self.client.invites, [])

    async def test_unfinished_writes_from_crash_become_unknown(self):
        r = await self.manager.create(self.request(until_date="2026-11-09"))
        plan = self.store.get(r["id"])
        plan["occurrences"][0].update(room="booking")
        plan["invite"]["state"] = "sending"
        self.store.save(plan)
        await self.manager.tick()
        result = self.manager.status(r["id"])
        self.assertEqual(result["counts"]["room_unknown"], 1)
        self.assertEqual(result["counts"]["invite_unknown"], 1)
        self.assertEqual(len(self.client.booked), 1)
        self.assertEqual(len(self.client.invites), 1)

    async def test_other_corporation_cannot_query_plan(self):
        r = await self.manager.create(self.request(until_date="2026-11-09"))
        other = PlanStore(self.path, "different-corp")
        self.addCleanup(other.close)
        with self.assertRaises(ValueError):
            other.get(r["id"])

    def test_weekly_calendar_inclusive_end_and_validation(self):
        rows = weekly_occurrences(self.request())
        self.assertEqual(rows[-1]["start_time"], ts("2026-12-28"))
        self.assertTrue(
            all(
                datetime.fromtimestamp(
                    r["start_time"], ZoneInfo("Asia/Shanghai")
                ).weekday()
                == 0
                for r in rows
            )
        )
        for kw in [
            dict(until_date="2026-11-01"),
            dict(start_time=ts("2026-11-09") + 900),
            dict(notify_email="not-email"),
            dict(location=""),
        ]:
            with self.assertRaises(ValueError):
                self.request(**kw)

    async def test_availability_failure_is_not_treated_as_empty_room(self):
        self.client.read_error = TimeoutError()
        r = await self.manager.create(self.request(until_date="2026-11-09"))
        self.assertEqual(self.client.booked, [])
        self.assertFalse(r["complete"])
