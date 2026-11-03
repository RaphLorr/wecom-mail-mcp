import copy
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from wecom_mail_mcp.config import load_settings
from wecom_mail_mcp.errors import WeComResponseError
from wecom_mail_mcp.meeting_plans import MeetingPlans, PlanStore, WeeklyMeetingRequest
from wecom_mail_mcp.room_availability import daily_booking_window, overlapping_bookings
from wecom_mail_mcp.wecom import WeComMailClient


def ts(hour, minute=0, day="2026-11-04"):
    return int(
        datetime.fromisoformat(day)
        .replace(hour=hour, minute=minute, tzinfo=ZoneInfo("Asia/Shanghai"))
        .timestamp()
    )


def booking(start, end, booking_id="existing", status=0):
    return {
        "start_time": start,
        "end_time": end,
        "booking_id": booking_id,
        "status": status,
    }


class AvailabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with patch.dict(
            os.environ, {"CORPID": "test", "CORPSECRET": "test"}, clear=True
        ):
            self.client = WeComMailClient(load_settings(_env_file=None))
        self.addAsyncCleanup(self.client.aclose)
        self.target_start, self.target_end = ts(15, 30), ts(16, 30)
        self.bookings = [
            booking(ts(14), ts(16), "earlier"),
            booking(ts(16), ts(18), "later"),
        ]
        self.calls = []

    async def provider(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs["json_body"]))
        body = kwargs["json_body"]
        # Reproduce the observed provider behavior: boundary-crossing bookings
        # disappear unless the query window fully contains them.
        schedules = [
            b
            for b in self.bookings
            if body["start_time"] <= b["start_time"]
            and b["end_time"] <= body["end_time"]
        ]
        return {"booking_list": [{"meetingroom_id": 6, "schedule": schedules}]}

    async def test_daily_query_finds_both_boundary_crossing_conflicts(self):
        with patch.object(self.client, "_request_authed", self.provider):
            result = await self.client.query_room_availability(
                self.target_start,
                self.target_end,
                meetingroom_id=6,
                city="city",
                building="building",
                floor="floor",
            )
        self.assertEqual(result[0]["schedule"], self.bookings)
        self.assertEqual(len(self.calls), 1)
        method, path, body = self.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/cgi-bin/oa/meetingroom/get_booking_info")
        self.assertEqual(
            body,
            {
                "start_time": ts(0),
                "end_time": ts(0) + 86399,
                "meetingroom_id": 6,
                "city": "city",
                "building": "building",
                "floor": "floor",
            },
        )

    def test_overlap_boundaries_and_metadata_are_preserved_without_mutating_response(
        self,
    ):
        cases = [
            (ts(14), ts(15, 30), False),  # Ends exactly at requested start.
            (ts(16, 30), ts(18), False),  # Starts exactly at requested end.
            (ts(14), ts(16), True),
            (ts(16), ts(18), True),
            (ts(14), ts(18), True),  # Contains the entire target window.
            (ts(15, 45), ts(16), True),  # Inside target.
            (ts(15, 30), ts(16, 30), True),
        ]
        for start, end, expected in cases:
            with self.subTest(start=start, end=end):
                raw = [
                    {"meetingroom_id": 6, "schedule": [booking(start, end, status=2)]}
                ]
                original = copy.deepcopy(raw)
                result = overlapping_bookings(
                    raw, self.target_start, self.target_end, 6
                )
                self.assertEqual(bool(result[0]["schedule"]), expected)
                self.assertEqual(raw, original)
                if expected:
                    self.assertEqual(result[0]["schedule"][0], raw[0]["schedule"][0])

    async def test_all_room_query_keeps_empty_rooms_and_only_relevant_bookings(self):
        raw = [
            {"meetingroom_id": 5, "schedule": [booking(ts(10), ts(11))]},
            {"meetingroom_id": 6, "schedule": self.bookings},
        ]
        with patch.object(
            self.client,
            "_request_authed",
            AsyncMock(return_value={"booking_list": raw}),
        ) as request:
            result = await self.client.query_room_availability(
                self.target_start, self.target_end
            )
        self.assertNotIn("meetingroom_id", request.call_args.kwargs["json_body"])
        self.assertEqual(result[0], {"meetingroom_id": 5, "schedule": []})
        self.assertEqual(result[1]["schedule"], self.bookings)

    async def test_invalid_time_ranges_fail_before_provider_call(self):
        for start, end in [
            (ts(16), ts(15)),
            (ts(16), ts(16)),
            (ts(23), ts(0, day="2026-11-05")),
        ]:
            with (
                self.subTest(start=start, end=end),
                patch.object(self.client, "_request_authed", AsyncMock()) as request,
            ):
                with self.assertRaises(ValueError):
                    await self.client.query_room_availability(
                        start, end, meetingroom_id=6
                    )
                request.assert_not_awaited()
        self.assertEqual(daily_booking_window(ts(1), ts(2)), (ts(0), ts(0) + 86399))

    async def test_missing_or_malformed_provider_data_is_not_reported_as_free(self):
        for raw in [
            None,
            [],
            [{"meetingroom_id": 5, "schedule": []}],
            [{"meetingroom_id": 6}],
            [{"meetingroom_id": 6, "schedule": [{}]}],
            [{"meetingroom_id": 6, "schedule": [booking(ts(16), ts(15))]}],
            [
                {"meetingroom_id": 6, "schedule": []},
                {"meetingroom_id": 6, "schedule": []},
            ],
        ]:
            with (
                self.subTest(raw=raw),
                patch.object(
                    self.client,
                    "_request_authed",
                    AsyncMock(return_value={"booking_list": raw}),
                ),
            ):
                with self.assertRaises(WeComResponseError):
                    await self.client.query_room_availability(
                        self.target_start, self.target_end, meetingroom_id=6
                    )

    async def test_managed_plan_does_not_attempt_to_book_an_overlapping_room(self):
        with tempfile.TemporaryDirectory() as directory:
            store = PlanStore(str(Path(directory) / "plans.sqlite"), "corp")
            self.addCleanup(store.close)
            now = ts(10, day="2026-11-03")
            store.heartbeat(now)
            manager = MeetingPlans(store, self.client, clock=lambda: now)
            request = WeeklyMeetingRequest(
                request_id="boundary-test",
                to_emails=["user@example.com"],
                subject="Meeting",
                content="Agenda",
                location="Room",
                start_time=self.target_start,
                end_time=self.target_end,
                until_date="2026-11-04",
                meetingroom_id=6,
                booker_userid="booker",
                notify_email="user@example.com",
            )
            with (
                patch.object(self.client, "_request_authed", self.provider),
                patch.object(self.client, "book_meeting_room", AsyncMock()) as book,
                patch.object(self.client, "send_schedule_email", AsyncMock()),
                patch.object(self.client, "send_email", AsyncMock()),
            ):
                result = await manager.create(request)
            self.assertEqual(result["occurrences"][0]["room"], "conflict")
            self.assertFalse(result["complete"])
            book.assert_not_awaited()
