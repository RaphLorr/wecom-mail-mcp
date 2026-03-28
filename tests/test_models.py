from __future__ import annotations

import unittest

from wecom_mail_mcp.models import (
    BookMeetingRoomRequest,
    SendEmailRequest,
    SendMeetingEmailRequest,
    SendScheduleEmailRequest,
    normalize_content_type,
)


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


class SendScheduleEmailRequestTests(unittest.TestCase):
    def _valid_kwargs(self, **overrides) -> dict:
        defaults = {
            "to_emails": ["user@example.com"],
            "subject": "Team sync",
            "content": "Weekly sync meeting",
            "start_time": 1700000000,
            "end_time": 1700001800,
        }
        defaults.update(overrides)
        return defaults

    def test_builds_valid_request(self) -> None:
        request = SendScheduleEmailRequest(**self._valid_kwargs())
        self.assertEqual(request.to_emails, ["user@example.com"])
        self.assertEqual(request.remind_before_mins, 15)
        self.assertEqual(request.content_type, "text")

    def test_multiple_recipients(self) -> None:
        request = SendScheduleEmailRequest(
            **self._valid_kwargs(to_emails=["a@b.com", "c@d.com"])
        )
        self.assertEqual(len(request.to_emails), 2)

    def test_rejects_empty_email_list(self) -> None:
        with self.assertRaises(ValueError):
            SendScheduleEmailRequest(**self._valid_kwargs(to_emails=[]))

    def test_rejects_invalid_email_in_list(self) -> None:
        with self.assertRaisesRegex(ValueError, "Invalid email address"):
            SendScheduleEmailRequest(
                **self._valid_kwargs(to_emails=["good@example.com", "bad-email"])
            )

    def test_rejects_empty_subject(self) -> None:
        with self.assertRaisesRegex(ValueError, "Subject cannot be empty"):
            SendScheduleEmailRequest(**self._valid_kwargs(subject="   "))

    def test_rejects_empty_content(self) -> None:
        with self.assertRaisesRegex(ValueError, "Content cannot be empty"):
            SendScheduleEmailRequest(**self._valid_kwargs(content=""))

    def test_rejects_end_before_start(self) -> None:
        with self.assertRaisesRegex(ValueError, "end_time must be after start_time"):
            SendScheduleEmailRequest(
                **self._valid_kwargs(start_time=1700001800, end_time=1700000000)
            )

    def test_rejects_negative_start_time(self) -> None:
        with self.assertRaisesRegex(ValueError, "start_time must be a positive"):
            SendScheduleEmailRequest(**self._valid_kwargs(start_time=-1))

    def test_defaults_location_to_empty(self) -> None:
        request = SendScheduleEmailRequest(**self._valid_kwargs())
        self.assertEqual(request.location, "")


class SendMeetingEmailRequestTests(unittest.TestCase):
    def _valid_kwargs(self, **overrides) -> dict:
        defaults = {
            "to_emails": ["user@example.com"],
            "subject": "Product review",
            "content": "Let's review the product",
            "start_time": 1700000000,
            "end_time": 1700001800,
            "meeting_admin_userid": "admin.user",
        }
        defaults.update(overrides)
        return defaults

    def test_builds_valid_request(self) -> None:
        request = SendMeetingEmailRequest(**self._valid_kwargs())
        self.assertEqual(request.meeting_admin_userid, "admin.user")
        self.assertTrue(request.enable_waiting_room)
        self.assertTrue(request.allow_enter_before_host)

    def test_inherits_schedule_validation(self) -> None:
        with self.assertRaisesRegex(ValueError, "end_time must be after start_time"):
            SendMeetingEmailRequest(
                **self._valid_kwargs(start_time=1700001800, end_time=1700000000)
            )

    def test_rejects_empty_admin_userid(self) -> None:
        with self.assertRaisesRegex(ValueError, "meeting_admin_userid cannot be empty"):
            SendMeetingEmailRequest(**self._valid_kwargs(meeting_admin_userid="  "))

    def test_custom_meeting_options(self) -> None:
        request = SendMeetingEmailRequest(
            **self._valid_kwargs(enable_waiting_room=False, allow_enter_before_host=False)
        )
        self.assertFalse(request.enable_waiting_room)
        self.assertFalse(request.allow_enter_before_host)


class BookMeetingRoomRequestTests(unittest.TestCase):
    def _valid_kwargs(self, **overrides) -> dict:
        defaults = {
            "meetingroom_id": 5,
            "subject": "Team standup",
            "booker_userid": "raphael.huang",
            "start_time": 1700000000,
            "end_time": 1700001800,
        }
        defaults.update(overrides)
        return defaults

    def test_builds_valid_request(self) -> None:
        request = BookMeetingRoomRequest(**self._valid_kwargs())
        self.assertEqual(request.meetingroom_id, 5)
        self.assertEqual(request.booker_userid, "raphael.huang")

    def test_rejects_empty_subject(self) -> None:
        with self.assertRaisesRegex(ValueError, "Subject cannot be empty"):
            BookMeetingRoomRequest(**self._valid_kwargs(subject=""))

    def test_rejects_empty_booker(self) -> None:
        with self.assertRaisesRegex(ValueError, "booker_userid cannot be empty"):
            BookMeetingRoomRequest(**self._valid_kwargs(booker_userid="  "))

    def test_rejects_invalid_time_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "end_time must be after start_time"):
            BookMeetingRoomRequest(
                **self._valid_kwargs(start_time=1700001800, end_time=1700000000)
            )


if __name__ == "__main__":
    unittest.main()
