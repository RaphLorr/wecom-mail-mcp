"""Durable weekly plans; invites and room reservations have independent outcomes.

No distributed transaction is possible. Persist intent before writes, never retry
an ambiguous write, and hold a process-shared lock across every processing pass.
"""

from __future__ import annotations

try:
    import fcntl
except ImportError:  # Keep the original mail tools importable on Windows.
    fcntl = None
import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, time as calendar_time
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from .errors import WeComAPIError
from .models import (
    BookMeetingRoomRequest,
    SendScheduleEmailRequest,
    SendEmailRequest,
    WeeklyRepeat,
)


class WeeklyMeetingRequest(SendScheduleEmailRequest):
    repeat: None = (
        None  # Derived from until_date/first occurrence; cannot contradict room dates.
    )
    request_id: str = Field(min_length=1, max_length=200)
    until_date: date
    timezone: str = "Asia/Shanghai"
    meetingroom_id: int = Field(gt=0)
    booker_userid: str = Field(min_length=1)
    notify_email: str

    @model_validator(mode="after")
    def validate_plan(self):
        ZoneInfo(self.timezone)
        # Reuse the existing email validation without inventing a second validator.
        SendEmailRequest(
            to_email=self.notify_email, subject="validation", content="validation"
        )
        if not self.location.strip() or not self.booker_userid.strip():
            raise ValueError(
                "A room name/location and confirmed booker userid are required"
            )
        start = datetime.fromtimestamp(self.start_time, ZoneInfo(self.timezone))
        end = datetime.fromtimestamp(self.end_time, ZoneInfo(self.timezone))
        if (
            start.date() != end.date()
            or start.hour < 8
            or end.hour > 23
            or (end.hour == 23 and end.minute)
        ):
            raise ValueError("Room bookings must be same-day between 08:00 and 23:00")
        if self.start_time % 1800 or self.end_time % 1800:
            raise ValueError(
                "Use 30-minute boundaries; WeCom otherwise rounds the room times"
            )
        if (
            self.until_date < start.date()
            or (self.until_date - start.date()).days > 365
        ):
            raise ValueError(
                "until_date must be within one year of the first occurrence"
            )
        offset = start.utcoffset().total_seconds() / 3600
        if not offset.is_integer() or not -12 <= offset <= 12:
            raise ValueError("邮件 API repeat 仅支持 -12 至 +12 的整数小时 UTC 偏移")
        day = start.date()
        while day <= self.until_date:
            if (
                datetime.combine(
                    day, start.timetz().replace(tzinfo=None), ZoneInfo(self.timezone)
                ).utcoffset()
                != start.utcoffset()
            ):
                raise ValueError(
                    "计划跨越夏令时偏移变化，当前邮件 repeat 无法等价表达；请用客户端处理，不拆成多封邀请"
                )
            day += timedelta(days=7)
        return self


def weekly_occurrences(request: WeeklyMeetingRequest) -> list[dict]:
    zone = ZoneInfo(request.timezone)
    first = datetime.fromtimestamp(request.start_time, zone)
    last = datetime.fromtimestamp(request.end_time, zone)
    rows = []
    day = first.date()
    while day <= request.until_date:
        start = datetime.combine(day, first.timetz().replace(tzinfo=None), zone)
        end = datetime.combine(day, last.timetz().replace(tzinfo=None), zone)
        # Refuse nonexistent/ambiguous local times instead of shifting meetings.
        for dt in (start, end):
            if (
                datetime.fromtimestamp(dt.timestamp(), zone).replace(tzinfo=None)
                != dt.replace(tzinfo=None)
                or dt.replace(fold=0).utcoffset() != dt.replace(fold=1).utcoffset()
            ):
                raise ValueError(
                    "An occurrence falls in a timezone clock transition; specify dates individually"
                )
        rows.append(
            {
                "start_time": int(start.timestamp()),
                "end_time": int(end.timestamp()),
                "room": "pending",
                "attempts": 0,
                "next_attempt": 0,
            }
        )
        day += timedelta(days=7)
    if len(rows) > 52:
        raise ValueError("At most 52 weekly occurrences are supported")
    return rows


class PlanStore:
    def __init__(self, filename: str, scope: str):
        self.path = Path(filename).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.scope = scope
        self.db = sqlite3.connect(self.path, timeout=10)
        os.chmod(self.path, 0o600)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS meeting_plans (id TEXT PRIMARY KEY, scope TEXT NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(scope,request_id))"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS meeting_worker (scope TEXT PRIMARY KEY, checked_at REAL NOT NULL)"
        )
        self.db.commit()

    def close(self):
        self.db.close()

    @contextmanager
    def exclusive(self):
        if fcntl is None:
            raise RuntimeError(
                "Managed meeting plans require POSIX file locking; existing mail tools remain available"
            )
        with open(str(self.path) + ".lock", "a") as lock:
            os.chmod(str(self.path) + ".lock", 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def save(self, plan):
        with self.db:
            self.db.execute(
                "UPDATE meeting_plans SET payload=? WHERE id=? AND scope=?",
                (json.dumps(plan), plan["id"], self.scope),
            )

    def get(self, plan_id):
        row = self.db.execute(
            "SELECT payload FROM meeting_plans WHERE id=? AND scope=?",
            (plan_id, self.scope),
        ).fetchone()
        if not row:
            raise ValueError("Meeting plan not found")
        return json.loads(row[0])

    def active(self):
        return [
            json.loads(row[0])
            for row in self.db.execute(
                "SELECT payload FROM meeting_plans WHERE scope=?", (self.scope,)
            )
        ]

    def heartbeat(self, now):
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO meeting_worker VALUES (?,?)", (self.scope, now)
            )

    def worker_ready(self, now):
        row = self.db.execute(
            "SELECT checked_at FROM meeting_worker WHERE scope=?", (self.scope,)
        ).fetchone()
        return bool(row and 0 <= now - row[0] <= 7200)


class MeetingPlans:
    def __init__(self, store: PlanStore, client, advance_days=30, clock=time.time):
        self.store, self.client, self.advance_days, self.clock = (
            store,
            client,
            advance_days,
            clock,
        )

    def status(self, plan_id):
        plan = self.store.get(plan_id)
        if plan.get("schema_version") != 2:
            return {
                **plan,
                "complete": False,
                "calendar_mode": "legacy_requires_review",
                "worker_healthy": self.store.worker_ready(self.clock()),
                "warning": "旧版逐场邀请计划已隔离，禁止自动重发或转换。请先核对已有日历邀请，再明确迁移。",
            }
        counts = {}
        for row in plan["occurrences"]:
            for kind in ["room"]:
                key = f"{kind}_{row[kind]}"
                counts[key] = counts.get(key, 0) + 1
        counts["invite_" + plan["invite"]["state"]] = 1
        return {
            **plan,
            "counts": counts,
            "worker_healthy": self.store.worker_ready(self.clock()),
            "complete": plan["state"] == "active"
            and plan["invite"]["state"] == "sent"
            and all(r["room"] == "booked" for r in plan["occurrences"]),
            "calendar_mode": "native_repeat"
            if len(plan["occurrences"]) > 1
            else "single_invitation",
            "warning": "地点文字不代表会议室已预订。pending_window 是尚未开放，不是预订成功；unknown 必须人工核对后处理。取消计划不取消已发送的日历邀请。",
        }

    async def create(self, request: WeeklyMeetingRequest):
        encoded = request.model_dump_json()
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        with self.store.exclusive() as acquired:
            if not acquired:
                return {
                    "status": "busy",
                    "message": "另一个会议操作进行中；稍后使用同一 request_id 重试。",
                }
            old = self.store.db.execute(
                "SELECT id,fingerprint FROM meeting_plans WHERE scope=? AND request_id=?",
                (self.store.scope, request.request_id),
            ).fetchone()
            if old:
                if old[1] != fingerprint:
                    raise ValueError("request_id 已用于不同的会议计划；不能静默覆盖")
                return self.status(old[0])
            if not self.store.worker_ready(self.clock()):
                raise ValueError(
                    "滚动预约 worker 未运行或超过两小时未报到；未创建计划、未发邀请"
                )
            rows = weekly_occurrences(request)
            if any(r["start_time"] <= self.clock() for r in rows):
                raise ValueError("首场会议必须在未来")
            # Another request id must not duplicate the same room/time automatically.
            occupied = {
                (r["start_time"], r["end_time"])
                for p in self.store.active()
                if p["state"] == "active"
                and p["request"]["meetingroom_id"] == request.meetingroom_id
                for r in p["occurrences"]
            }
            if any((r["start_time"], r["end_time"]) in occupied for r in rows):
                raise ValueError(
                    "已有托管计划使用这些会议室时段，请先查询确认，不要重复创建"
                )
            plan = {
                "schema_version": 2,
                "invite": {"state": "pending"},
                "id": str(uuid.uuid4()),
                "state": "active",
                "request": request.model_dump(mode="json"),
                "occurrences": rows,
                "notices": {},
                "created_at": self.clock(),
            }
            with self.store.db:
                self.store.db.execute(
                    "INSERT INTO meeting_plans VALUES (?,?,?,?,?)",
                    (
                        plan["id"],
                        self.store.scope,
                        request.request_id,
                        fingerprint,
                        json.dumps(plan),
                    ),
                )
            await self._process(plan)
            await self._send_series_invitation(plan)
            await self._notify(plan)
            return self.status(plan["id"])

    async def tick(self):
        with self.store.exclusive() as acquired:
            if not acquired:
                return {"status": "busy"}
            self.store.heartbeat(self.clock())
            count = 0
            for plan in self.store.active():
                if plan["state"] != "active":
                    continue
                for row in plan["occurrences"]:
                    if row["room"] == "booking":
                        row["room"] = "unknown"
                if (
                    plan.get("schema_version") == 2
                    and plan["invite"]["state"] != "sent"
                ):
                    if plan["invite"]["state"] == "sending":
                        plan["invite"]["state"] = "unknown"
                    elif plan["invite"]["state"] == "pending":
                        plan["invite"]["state"] = "not_sent"
                    self.store.save(plan)
                    await self._notify(plan)
                    count += 1
                    continue
                await self._process(plan)
                count += 1
            return {"status": "ok", "plans_checked": count}

    async def _process(self, plan):
        if plan.get("schema_version") != 2:
            plan["state"] = "requires_review"
            self.store.save(plan)
            return
        request = WeeklyMeetingRequest.model_validate(plan["request"])
        now = self.clock()
        for key, value in plan["notices"].items():
            if value == "sending":
                plan["notices"][key] = "unknown"
        for row in plan["occurrences"]:
            # Lock ownership proves no other live writer can still be making these writes.
            if row["room"] == "booking":
                row["room"] = "unknown"
            self.store.save(plan)
            if row["start_time"] <= now:
                if row["room"] not in ["booked", "unknown", "cancelled"]:
                    row["room"] = "expired"
                self.store.save(plan)
                continue
            due = row["start_time"] <= now + self.advance_days * 86400
            if not due and row["room"] == "pending":
                row["room"] = "pending_window"
                row["eligible_at"] = row["start_time"] - self.advance_days * 86400
                self.store.save(plan)
            if (
                due
                and row["room"] in ["pending", "pending_window", "retry"]
                and row["next_attempt"] <= now
            ):
                try:
                    bookings = await self.client.query_room_availability(
                        row["start_time"],
                        row["end_time"],
                        meetingroom_id=request.meetingroom_id,
                    )
                    # Unexpected response is not proof of an available room.
                    found = [
                        b
                        for b in bookings
                        if b.get("meetingroom_id") == request.meetingroom_id
                    ]
                    if len(found) != 1 or not isinstance(
                        found[0].get("schedule"), list
                    ):
                        raise ValueError(
                            "availability response does not identify the room"
                        )
                    schedule = found[0]["schedule"]
                    for b in schedule:
                        if (
                            not isinstance(b, dict)
                            or "start_time" not in b
                            or "end_time" not in b
                        ):
                            raise ValueError("unrecognized booking entry")
                    if any(
                        int(b["start_time"]) < row["end_time"]
                        and int(b["end_time"]) > row["start_time"]
                        for b in schedule
                    ):
                        row["room"] = "conflict"
                        self.store.save(plan)
                    else:
                        row["room"] = "booking"
                        row["attempts"] += 1
                        self.store.save(plan)
                        booking_id = await self.client.book_meeting_room(
                            BookMeetingRoomRequest(
                                meetingroom_id=request.meetingroom_id,
                                subject=request.subject,
                                booker_userid=request.booker_userid,
                                start_time=row["start_time"],
                                end_time=row["end_time"],
                            )
                        )
                        row["booking_id"] = booking_id
                        row["room"] = "booked"
                        self.store.save(plan)
                except Exception as exc:
                    # Only explicit business rejections are definite write failures.
                    if row["room"] == "booking" and not isinstance(exc, WeComAPIError):
                        row["room"] = "unknown"
                    else:
                        row["room"] = "retry" if row["attempts"] < 3 else "failed"
                        row["next_attempt"] = now + 3600
                    row["room_error"] = f"{type(exc).__name__}" + (
                        f":{exc.errcode}" if isinstance(exc, WeComAPIError) else ""
                    )
                    self.store.save(plan)
        await self._notify(plan)

    async def _send_series_invitation(self, plan):
        # Only an explicit creation/retry tool calls this. The worker NEVER sends invites.
        if plan["invite"]["state"] != "pending":
            return
        request = WeeklyMeetingRequest.model_validate(plan["request"])
        if request.start_time <= self.clock():
            plan["invite"]["state"] = "expired"
            self.store.save(plan)
            return
        zone = ZoneInfo(request.timezone)
        first = datetime.fromtimestamp(request.start_time, zone)
        repeat = None
        if len(plan["occurrences"]) > 1:
            until = (
                int(
                    datetime.combine(
                        request.until_date + timedelta(days=1), calendar_time.min, zone
                    ).timestamp()
                )
                - 1
            )
            repeat = WeeklyRepeat(
                repeat_until=until,
                repeat_day_of_week=[first.isoweekday()],
                timezone=int(first.utcoffset().total_seconds() / 3600),
            )
        invite = SendScheduleEmailRequest(
            to_emails=request.to_emails,
            subject=request.subject,
            content=request.content
            + "\n\n会议室按预约窗口滚动补订；邀请中的地点不代表所有日期均已预订，异常会通知组织者。",
            content_type=request.content_type,
            location=request.location,
            start_time=request.start_time,
            end_time=request.end_time,
            remind_before_mins=request.remind_before_mins,
            repeat=repeat,
        )
        plan["invite"]["state"] = "sending"
        self.store.save(plan)
        try:
            await self.client.send_schedule_email(invite)
            plan["invite"].update(
                state="sent", repeat=repeat.model_dump() if repeat else None
            )
        except Exception as exc:
            plan["invite"].update(
                state="failed" if isinstance(exc, WeComAPIError) else "unknown",
                error=type(exc).__name__,
            )
        self.store.save(plan)

    async def retry_invitation(self, plan_id):
        with self.store.exclusive() as acquired:
            if not acquired:
                return {"status": "busy"}
            plan = self.store.get(plan_id)
            if (
                plan.get("schema_version") != 2
                or plan["state"] != "active"
                or plan["invite"]["state"] not in {"failed", "not_sent"}
            ):
                raise ValueError(
                    "只能明确重试未发送/业务拒绝的系列邀请；已发送或结果不明不能重发"
                )
            plan["invite"]["state"] = "pending"
            self.store.save(plan)
            await self._send_series_invitation(plan)
            await self._notify(plan)
            return self.status(plan_id)

    async def _notify(self, plan):
        events = []
        for row in plan["occurrences"]:
            for kind, states in [
                (
                    "room",
                    {"booked", "conflict", "failed", "retry", "unknown", "expired"},
                ),
            ]:
                if row[kind] not in states:
                    continue
                # One notification for a retry state, not one per attempt.
                key = f"{row['start_time']}:{kind}:{row[kind]}"
                if key not in plan["notices"]:
                    events.append((key, row, kind))
        invite = plan.get("invite", {})
        if invite.get("state") in {"failed", "unknown", "not_sent", "expired"}:
            key = "series:invite:" + invite["state"]
            if key not in plan["notices"]:
                events.append(
                    (
                        key,
                        {
                            "start_time": plan["request"]["start_time"],
                            "invite": invite["state"],
                        },
                        "invite",
                    )
                )
        if not events:
            return
        for key, _, _ in events:
            plan["notices"][key] = "sending"
        self.store.save(plan)
        zone = ZoneInfo(plan["request"]["timezone"])
        lines = [
            f"{datetime.fromtimestamp(row['start_time'], zone).isoformat()} {kind}={row[kind]}"
            for _, row, kind in events
        ]
        try:
            await self.client.send_email(
                SendEmailRequest(
                    to_email=plan["request"]["notify_email"],
                    subject="会议室预约进度：" + plan["request"]["subject"],
                    content="计划 "
                    + plan["id"]
                    + "\n"
                    + "\n".join(lines)
                    + "\n待开放日期尚未预订。unknown 请核对实际记录，不要重复发送/预订。",
                )
            )
            state = "sent"
        except Exception:
            state = "unknown"
        for key, _, _ in events:
            plan["notices"][key] = state
        self.store.save(plan)

    async def cancel(self, plan_id):
        with self.store.exclusive() as acquired:
            if not acquired:
                return {"status": "busy"}
            plan = self.store.get(plan_id)
            plan["state"] = "cancelled"
            self.store.save(plan)
            for row in plan["occurrences"]:
                if row["start_time"] <= self.clock():
                    continue
                if row["room"] == "booking":
                    row["room"] = "unknown"
                if row["room"] == "booked":
                    try:
                        await self.client.cancel_room_booking(row["booking_id"])
                        row["room"] = "cancelled"
                    except Exception:
                        row["cancel_error"] = "取消未确认成功；保留 booking_id 供核对"
                elif row["room"] != "unknown":
                    row["room"] = "cancelled"
                if (
                    "invite" in row
                ):  # Legacy records remain explicit; never resend them.
                    if row["invite"] == "pending":
                        row["invite"] = "cancelled"
                    elif row["invite"] == "sending":
                        row["invite"] = "unknown"
                self.store.save(plan)
            if plan.get("schema_version") == 2:
                if plan["invite"]["state"] == "pending":
                    plan["invite"]["state"] = "cancelled"
                elif plan["invite"]["state"] == "sending":
                    plan["invite"]["state"] = "unknown"
                self.store.save(plan)
            return self.status(plan_id)

    async def retry_room(self, plan_id, start_time):
        with self.store.exclusive() as acquired:
            if not acquired:
                return {"status": "busy"}
            plan = self.store.get(plan_id)
            if plan["state"] != "active":
                raise ValueError("计划已停止")
            row = next(
                (r for r in plan["occurrences"] if r["start_time"] == start_time), None
            )
            if not row or row["room"] not in ["conflict", "failed", "retry"]:
                raise ValueError(
                    "仅允许重试明确失败/冲突的场次；unknown 必须先人工核实，不能盲目重订"
                )
            row.update(room="pending", attempts=0, next_attempt=0)
            self.store.save(plan)
            await self._process(plan)
            return self.status(plan_id)
