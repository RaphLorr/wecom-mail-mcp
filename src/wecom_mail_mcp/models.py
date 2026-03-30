from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CONTENT_TYPE_ALIASES = {
    "text": "text",
    "plain": "text",
    "text/plain": "text",
    "html": "html",
    "text/html": "html",
}



def normalize_content_type(value: str | None) -> str:
    """Map friendly MIME types to the official WeCom values."""

    if value is None:
        return "text"

    normalized = value.strip().lower()
    if not normalized:
        return "text"

    mapped = CONTENT_TYPE_ALIASES.get(normalized)
    if mapped is None:
        supported = ", ".join(sorted(CONTENT_TYPE_ALIASES))
        raise ValueError(f"Unsupported content_type '{value}'. Supported values: {supported}")
    return mapped


def validate_email_address(value: str) -> str:
    """Apply lightweight email validation before calling the API."""

    normalized = value.strip()
    if not normalized:
        raise ValueError("Email address cannot be empty")
    if not EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError(f"Invalid email address: {value}")
    return normalized


def validate_time_range(start_time: int, end_time: int) -> None:
    """Validate that start_time and end_time form a valid range."""

    if start_time <= 0:
        raise ValueError("start_time must be a positive Unix timestamp")
    if end_time <= 0:
        raise ValueError("end_time must be a positive Unix timestamp")
    if end_time <= start_time:
        raise ValueError("end_time must be after start_time")


# ---------------------------------------------------------------------------
# Plain email models (existing)
# ---------------------------------------------------------------------------


class SendEmailRequest(BaseModel):
    """Validated input for a single outbound email."""

    to_email: str = Field(description="Recipient email address.")
    subject: str = Field(description="Email subject.")
    content: str = Field(description="Email body.")
    content_type: str = Field(default="text", description="Body type: text or html.")

    @field_validator("to_email")
    @classmethod
    def _validate_to_email(cls, value: str) -> str:
        return validate_email_address(value)

    @field_validator("subject")
    @classmethod
    def _validate_subject(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Subject cannot be empty")
        return value

    @field_validator("content")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Content cannot be empty")
        return value

    @field_validator("content_type")
    @classmethod
    def _normalize_content_type(cls, value: str) -> str:
        return normalize_content_type(value)


class MailboxInfo(BaseModel):
    """The configured WeCom app mailbox."""

    email: str
    alias_list: list[str] = Field(default_factory=list)


class SendEmailResult(BaseModel):
    """Structured MCP response for successful sends."""

    ok: bool = True
    provider: str = "wecom"
    sender_email: str
    to_email: str
    subject: str
    content_type: str
    message: str = "邮件已通过企业微信官方邮件 API 发送。"


class MailboxInfoResult(BaseModel):
    """Structured MCP response for the mailbox lookup tool."""

    ok: bool = True
    provider: str = "wecom"
    sender_email: str
    alias_list: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Schedule / meeting email models
# ---------------------------------------------------------------------------


class SendScheduleEmailRequest(BaseModel):
    """Validated input for a schedule (calendar) invitation email."""

    to_emails: list[str] = Field(min_length=1, description="Recipient email addresses.")
    subject: str = Field(description="Email and schedule title.")
    content: str = Field(description="Email body / schedule description.")
    content_type: str = Field(default="text", description="Body type: text or html.")
    location: str = Field(default="", description="Meeting location.")
    start_time: int = Field(description="Start time as Unix timestamp.")
    end_time: int = Field(description="End time as Unix timestamp.")
    remind_before_mins: int = Field(default=15, ge=0, description="Remind N minutes before.")

    @field_validator("to_emails")
    @classmethod
    def _validate_emails(cls, value: list[str]) -> list[str]:
        return [validate_email_address(e) for e in value]

    @field_validator("subject")
    @classmethod
    def _validate_subject(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Subject cannot be empty")
        return value

    @field_validator("content")
    @classmethod
    def _validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Content cannot be empty")
        return value

    @field_validator("content_type")
    @classmethod
    def _normalize_content_type(cls, value: str) -> str:
        return normalize_content_type(value)

    @model_validator(mode="after")
    def _validate_time_range(self) -> SendScheduleEmailRequest:
        validate_time_range(self.start_time, self.end_time)
        return self


class SendMeetingEmailRequest(SendScheduleEmailRequest):
    """Validated input for a meeting invitation email with 腾讯会议."""

    meeting_admin_userid: str = Field(description="Meeting admin userid (required by WeCom).")
    enable_waiting_room: bool = Field(default=True, description="Enable waiting room.")
    allow_enter_before_host: bool = Field(default=True, description="Allow joining before host.")

    @field_validator("meeting_admin_userid")
    @classmethod
    def _validate_admin(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("meeting_admin_userid cannot be empty")
        return value.strip()


class SendScheduleEmailResult(BaseModel):
    """Structured MCP response for schedule email sends."""

    ok: bool = True
    provider: str = "wecom"
    sender_email: str
    to_emails: list[str]
    subject: str
    start_time: int
    end_time: int
    message: str = "日程邮件已发送。"


class SendMeetingEmailResult(SendScheduleEmailResult):
    """Structured MCP response for meeting email sends."""

    message: str = "会议邮件已发送（含腾讯会议链接）。"


# ---------------------------------------------------------------------------
# Meeting room models
# ---------------------------------------------------------------------------


class MeetingRoom(BaseModel):
    """A single meeting room from WeCom."""

    meetingroom_id: int
    name: str
    capacity: int
    equipment: list[int] = Field(default_factory=list)
    need_approval: int = 0


class ListMeetingRoomsResult(BaseModel):
    """Structured MCP response for meeting room listing."""

    ok: bool = True
    rooms: list[MeetingRoom]


class RoomBookingInfo(BaseModel):
    """Booking info for a single room in a time window."""

    meetingroom_id: int
    schedule: list[dict] = Field(default_factory=list)


class QueryRoomAvailabilityResult(BaseModel):
    """Structured MCP response for room availability queries."""

    ok: bool = True
    booking_list: list[RoomBookingInfo]


class BookMeetingRoomRequest(BaseModel):
    """Validated input for booking a meeting room."""

    meetingroom_id: int = Field(description="Meeting room ID.")
    subject: str = Field(description="Booking subject.")
    booker_userid: str = Field(description="Booker's WeCom userid.")
    start_time: int = Field(description="Start time as Unix timestamp.")
    end_time: int = Field(description="End time as Unix timestamp.")

    @field_validator("subject")
    @classmethod
    def _validate_subject(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Subject cannot be empty")
        return value

    @field_validator("booker_userid")
    @classmethod
    def _validate_booker(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("booker_userid cannot be empty")
        return value.strip()

    @model_validator(mode="after")
    def _validate_time_range(self) -> BookMeetingRoomRequest:
        validate_time_range(self.start_time, self.end_time)
        return self


class BookMeetingRoomResult(BaseModel):
    """Structured MCP response for room booking."""

    ok: bool = True
    booking_id: str = Field(description="取消预定时需要此 ID。")
    message: str = "会议室已预定。取消时需要 booking_id。"


class CancelRoomBookingResult(BaseModel):
    """Structured MCP response for booking cancellation."""

    ok: bool = True
    message: str = "会议室预定已取消。"


# ---------------------------------------------------------------------------
# Employee directory models
# ---------------------------------------------------------------------------


class Employee(BaseModel):
    """An employee from WeCom directory."""

    userid: str
    name: str
    english_name: str = ""
    alias: str = ""
    department: list[int] = Field(default_factory=list)
    main_department: int = 0
    position: str = ""
    status: int = 1
    is_leader_in_dept: list[int] = Field(default_factory=list)
    direct_leader: list[str] = Field(default_factory=list)


class Department(BaseModel):
    """A department from WeCom directory."""

    id: int
    name: str
    parentid: int = 0


class ListEmployeesResult(BaseModel):
    """Structured MCP response for employee listing."""

    ok: bool = True
    departments: list[Department] = Field(default_factory=list)
    employees: list[Employee] = Field(default_factory=list)
    total: int = 0
    message: str = (
        "注意：由于企业微信隐私策略限制，员工邮箱（email/biz_mail）无法通过 API 获取。"
        "企业邮箱格式通常为 {userid}@企业域名，但可能存在错误，具体以邮箱管理员分配的邮箱为准。"
    )
