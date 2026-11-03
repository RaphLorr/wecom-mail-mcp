"""Query full Beijing days, then detect overlaps locally.

WeCom's narrow-window query can omit bookings that cross a query boundary.
An empty narrow response therefore does not establish availability.
"""

from datetime import datetime, time, timedelta, timezone

from .errors import WeComResponseError
from .models import validate_time_range

BEIJING = timezone(timedelta(hours=8))


def daily_booking_window(start_time: int, end_time: int) -> tuple[int, int]:
    validate_time_range(start_time, end_time)
    day = datetime.fromtimestamp(start_time, BEIJING).date()
    if datetime.fromtimestamp(end_time, BEIJING).date() != day:
        raise ValueError("查询时间范围不能跨天（北京时间，企业微信 API 限制）")
    start = int(datetime.combine(day, time.min, BEIJING).timestamp())
    return start, start + 86400 - 1


def overlapping_bookings(
    raw_list: object, start_time: int, end_time: int, meetingroom_id: int | None
) -> list[dict]:
    if not isinstance(raw_list, list):
        raise WeComResponseError("Room availability response has no booking_list array")
    result = []
    seen = set()
    for room in raw_list:
        if (
            not isinstance(room, dict)
            or type(room.get("meetingroom_id")) is not int
            or room["meetingroom_id"] <= 0
            or room["meetingroom_id"] in seen
            or not isinstance(room.get("schedule"), list)
        ):
            raise WeComResponseError(
                "Room availability response contains an invalid room"
            )
        seen.add(room["meetingroom_id"])
        schedule = []
        for booking in room["schedule"]:
            if (
                not isinstance(booking, dict)
                or type(booking.get("start_time")) is not int
                or type(booking.get("end_time")) is not int
                or booking["end_time"] <= booking["start_time"]
            ):
                raise WeComResponseError(
                    "Room availability response contains invalid booking times"
                )
            # [start, end): adjacent meetings do not overlap. Preserve provider
            # IDs/status and other metadata for every actual overlap.
            if booking["start_time"] < end_time and booking["end_time"] > start_time:
                schedule.append(booking)
        if meetingroom_id is None or room["meetingroom_id"] == meetingroom_id:
            result.append({**room, "schedule": schedule})
    if meetingroom_id is not None and meetingroom_id not in seen:
        raise WeComResponseError("Room availability response omits the requested room")
    return result
