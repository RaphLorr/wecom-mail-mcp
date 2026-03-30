from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field, ValidationError

from .config import Settings
from .errors import summarize_validation_error
from .models import (
    BookMeetingRoomRequest,
    BookMeetingRoomResult,
    CancelRoomBookingResult,
    Department,
    Employee,
    ListEmployeesResult,
    ListMeetingRoomsResult,
    MailboxInfoResult,
    MeetingRoom,
    QueryRoomAvailabilityResult,
    RoomBookingInfo,
    SendEmailRequest,
    SendEmailResult,
    SendMeetingEmailRequest,
    SendMeetingEmailResult,
    SendScheduleEmailRequest,
    SendScheduleEmailResult,
)
from .wecom import WeComMailClient

# ---------------------------------------------------------------------------
# Annotated type aliases for tool parameters
# ---------------------------------------------------------------------------

RecipientEmail = Annotated[str, Field(description="收件人邮箱地址，例如 user@example.com")]
RecipientEmails = Annotated[list[str], Field(description="收件人邮箱地址列表，例如 ['user1@example.com', 'user2@example.com']")]
EmailSubject = Annotated[str, Field(description="邮件主题")]
MeetingLocation = Annotated[str, Field(description="会议地点，例如 '3楼 FRONTIER 会议室'")]
StartTimestamp = Annotated[int, Field(description="开始时间，Unix 时间戳（秒）。注意时间以北京时间（UTC+8）为准。")]
EndTimestamp = Annotated[int, Field(description="结束时间，Unix 时间戳（秒）。注意时间以北京时间（UTC+8）为准。")]
RemindBeforeMins = Annotated[int, Field(description="提前提醒分钟数，默认 15 分钟")]
MeetingAdminUserid = Annotated[str, Field(description="会议管理员的企业微信 userid，严禁使用其他猜测 ID")]
MeetingRoomId = Annotated[int, Field(description="会议室 ID，可通过 list_meeting_rooms 获取")]
BookerUserid = Annotated[str, Field(description="预定人的企业微信 userid，严禁使用其他猜测 ID")]
OptionalCity = Annotated[str, Field(description="城市筛选（可选）")]
OptionalBuilding = Annotated[str, Field(description="楼宇筛选（可选）")]
OptionalFloor = Annotated[str, Field(description="楼层筛选（可选）")]

HTML_EMAIL_GUIDANCE = (
    "如果发送 HTML 邮件，请显式传 content_type=html，并只使用邮件兼容写法："
    "优先用 table/tbody/tr/td 做布局，样式尽量写 inline style。"
    "稳定标签：table、tbody、tr、td、p、br、span、strong、b、em、i、h1-h4、a、img。"
    "避免 script、iframe、form、video、audio、canvas、svg、外链 CSS、flex、grid、position、"
    "web font、相对路径和网页式复杂模板。图片请使用公网 https 绝对地址。"
)
HTML_EMAIL_TEMPLATE_HINT = (
    'HTML 骨架建议：'
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
    '<tr><td>'
    '<h2 style="margin:0 0 16px;">标题</h2>'
    '<p style="margin:0 0 12px;">正文</p>'
    '<a href="https://example.com">链接</a>'
    '<img src="https://example.com/demo.png" alt="" style="display:block;width:100%;height:auto;border:0;">'
    "</td></tr></table>"
)
EmailContent = Annotated[
    str,
    Field(description="邮件正文。默认纯文本。如需 HTML 请同时设置 content_type=html，使用 table 布局和 inline style。"),
]
EmailContentType = Annotated[
    str,
    Field(
        description=(
            "正文类型。支持 text、html、text/plain、text/html。默认 text。"
            "如果正文是 HTML，请显式传 html 或 text/html，并遵守邮件兼容 HTML 限制。"
        )
    ),
]


@dataclass(slots=True)
class AppState:
    settings: Settings
    client: WeComMailClient


def create_server(settings: Settings) -> FastMCP[AppState]:
    """Create the FastMCP server instance."""

    @asynccontextmanager
    async def lifespan(_: FastMCP[AppState]):
        client = WeComMailClient(settings)
        try:
            yield AppState(settings=settings, client=client)
        finally:
            await client.aclose()

    mcp = FastMCP(
        name="WeCom Mail MCP",
        instructions=(
            "通过企业微信官方 API 发送邮件、日程邀请、会议邀请，以及管理会议室预定。"
            "发件人固定为当前应用邮箱账号。"
            f"{HTML_EMAIL_GUIDANCE}"
        ),
        lifespan=lifespan,
        host=settings.wecom_mcp_host,
        port=settings.wecom_mcp_port,
        log_level=settings.wecom_log_level,
    )

    # ------------------------------------------------------------------
    # Tool: send_email
    # ------------------------------------------------------------------

    @mcp.tool(
        name="send_email",
        title="Send Email",
        description=(
            "通过企业微信官方邮件 API 发送普通邮件。发件人固定为当前应用邮箱账号。"
            f"{HTML_EMAIL_GUIDANCE}"
        ),
    )
    async def send_email(
        to_email: RecipientEmail,
        subject: EmailSubject,
        content: EmailContent,
        ctx: Context,
        content_type: EmailContentType = "text",
    ) -> SendEmailResult:
        try:
            request = SendEmailRequest(
                to_email=to_email,
                subject=subject,
                content=content,
                content_type=content_type,
            )
        except ValidationError as exc:
            raise ValueError(summarize_validation_error(exc)) from exc

        state = _require_state(ctx)
        await ctx.info(f"Sending WeCom email to {request.to_email}")
        mailbox = await state.client.send_email(request)
        return SendEmailResult(
            sender_email=mailbox.email,
            to_email=request.to_email,
            subject=request.subject,
            content_type=request.content_type,
        )

    # ------------------------------------------------------------------
    # Tool: get_mailbox_info
    # ------------------------------------------------------------------

    @mcp.tool(
        name="get_mailbox_info",
        title="Get Mailbox Info",
        description="查询当前应用邮箱账号与别名邮箱，用于确认实际发件人地址。",
    )
    async def get_mailbox_info(ctx: Context) -> MailboxInfoResult:
        state = _require_state(ctx)
        mailbox = await state.client.get_mailbox_info(force_refresh=True)
        return MailboxInfoResult(
            sender_email=mailbox.email,
            alias_list=mailbox.alias_list,
        )

    # ------------------------------------------------------------------
    # Tool: send_schedule_email
    # ------------------------------------------------------------------

    @mcp.tool(
        name="send_schedule_email",
        title="Send Schedule Email",
        description=(
            "【推荐】发送日程邀请邮件。收件人会在企业微信日历中收到日程邀请。"
            "适用于大多数会议邀请场景。如需创建腾讯会议（含会议链接），请使用 send_meeting_email。"
            "注意：此接口不支持取消已发送的日程邀请。"
        ),
    )
    async def send_schedule_email(
        to_emails: RecipientEmails,
        subject: EmailSubject,
        content: EmailContent,
        start_time: StartTimestamp,
        end_time: EndTimestamp,
        ctx: Context,
        location: MeetingLocation = "",
        remind_before_mins: RemindBeforeMins = 15,
        content_type: EmailContentType = "text",
    ) -> SendScheduleEmailResult:
        try:
            request = SendScheduleEmailRequest(
                to_emails=to_emails,
                subject=subject,
                content=content,
                content_type=content_type,
                location=location,
                start_time=start_time,
                end_time=end_time,
                remind_before_mins=remind_before_mins,
            )
        except ValidationError as exc:
            raise ValueError(summarize_validation_error(exc)) from exc

        state = _require_state(ctx)
        await ctx.info(f"Sending schedule email to {len(request.to_emails)} recipients")
        mailbox = await state.client.send_schedule_email(request)
        return SendScheduleEmailResult(
            sender_email=mailbox.email,
            to_emails=request.to_emails,
            subject=request.subject,
            start_time=request.start_time,
            end_time=request.end_time,
        )

    # ------------------------------------------------------------------
    # Tool: send_meeting_email
    # ------------------------------------------------------------------

    @mcp.tool(
        name="send_meeting_email",
        title="Send Meeting Email (Tencent Meeting)",
        description=(
            "发送腾讯会议邀请邮件。收件人会收到含腾讯会议链接的日程邀请，可直接点击入会。"
            "仅在需要线上视频会议（腾讯会议）时使用此接口。"
            "一般的日程邀请请优先使用 send_schedule_email。"
            "需要指定会议管理员 userid。注意：此接口不支持取消已发送的会议邀请。"
        ),
    )
    async def send_meeting_email(
        to_emails: RecipientEmails,
        subject: EmailSubject,
        content: EmailContent,
        start_time: StartTimestamp,
        end_time: EndTimestamp,
        meeting_admin_userid: MeetingAdminUserid,
        ctx: Context,
        location: MeetingLocation = "",
        remind_before_mins: RemindBeforeMins = 15,
        content_type: EmailContentType = "text",
        enable_waiting_room: Annotated[bool, Field(description="是否开启等候室")] = True,
        allow_enter_before_host: Annotated[bool, Field(description="是否允许在主持人之前入会")] = True,
    ) -> SendMeetingEmailResult:
        try:
            request = SendMeetingEmailRequest(
                to_emails=to_emails,
                subject=subject,
                content=content,
                content_type=content_type,
                location=location,
                start_time=start_time,
                end_time=end_time,
                remind_before_mins=remind_before_mins,
                meeting_admin_userid=meeting_admin_userid,
                enable_waiting_room=enable_waiting_room,
                allow_enter_before_host=allow_enter_before_host,
            )
        except ValidationError as exc:
            raise ValueError(summarize_validation_error(exc)) from exc

        state = _require_state(ctx)
        await ctx.info(f"Sending meeting email to {len(request.to_emails)} recipients")
        mailbox = await state.client.send_meeting_email(request)
        return SendMeetingEmailResult(
            sender_email=mailbox.email,
            to_emails=request.to_emails,
            subject=request.subject,
            start_time=request.start_time,
            end_time=request.end_time,
        )

    # ------------------------------------------------------------------
    # Tool: list_meeting_rooms
    # ------------------------------------------------------------------

    @mcp.tool(
        name="list_meeting_rooms",
        title="List Meeting Rooms",
        description="查询企业微信中的会议室列表。可按城市、楼宇、楼层筛选。",
    )
    async def list_meeting_rooms(
        ctx: Context,
        city: OptionalCity = "",
        building: OptionalBuilding = "",
        floor: OptionalFloor = "",
    ) -> ListMeetingRoomsResult:
        state = _require_state(ctx)
        await ctx.info("Listing meeting rooms")
        raw_rooms = await state.client.list_meeting_rooms(
            city=city, building=building, floor=floor,
        )
        rooms = [
            MeetingRoom(
                meetingroom_id=r.get("meetingroom_id", 0),
                name=r.get("name", ""),
                capacity=r.get("capacity", 0),
                equipment=r.get("equipment", []),
                need_approval=r.get("need_approval", 0),
            )
            for r in raw_rooms
            if isinstance(r, dict)
        ]
        return ListMeetingRoomsResult(rooms=rooms)

    # ------------------------------------------------------------------
    # Tool: query_room_availability
    # ------------------------------------------------------------------

    @mcp.tool(
        name="query_room_availability",
        title="Query Room Availability",
        description=(
            "查询会议室在指定时间段内的预定情况。"
            "时间范围不能跨天（API 限制）。可按会议室 ID 或位置筛选。"
            "返回的 schedule 为空数组表示该时段空闲。"
        ),
    )
    async def query_room_availability(
        start_time: StartTimestamp,
        end_time: EndTimestamp,
        ctx: Context,
        meetingroom_id: Annotated[int | None, Field(description="会议室 ID（可选，不传则查询所有）")] = None,
        city: OptionalCity = "",
        building: OptionalBuilding = "",
        floor: OptionalFloor = "",
    ) -> QueryRoomAvailabilityResult:
        import datetime
        beijing_tz = datetime.timezone(datetime.timedelta(hours=8))
        start_date = datetime.datetime.fromtimestamp(start_time, tz=beijing_tz).date()
        end_date = datetime.datetime.fromtimestamp(end_time, tz=beijing_tz).date()
        if start_date != end_date:
            raise ValueError("查询时间范围不能跨天（北京时间，企业微信 API 限制）")

        state = _require_state(ctx)
        await ctx.info("Querying room availability")
        raw_list = await state.client.query_room_availability(
            start_time=start_time,
            end_time=end_time,
            meetingroom_id=meetingroom_id,
            city=city,
            building=building,
            floor=floor,
        )
        booking_list = [
            RoomBookingInfo(
                meetingroom_id=item.get("meetingroom_id", 0),
                schedule=item.get("schedule", []),
            )
            for item in raw_list
            if isinstance(item, dict)
        ]
        return QueryRoomAvailabilityResult(booking_list=booking_list)

    # ------------------------------------------------------------------
    # Tool: book_meeting_room
    # ------------------------------------------------------------------

    @mcp.tool(
        name="book_meeting_room",
        title="Book Meeting Room",
        description=(
            "预定会议室。成功后返回 booking_id，取消预定时需要此 ID。"
            "最小预定时长 30 分钟，时间会自动按 30 分钟取整。"
        ),
    )
    async def book_meeting_room(
        meetingroom_id: MeetingRoomId,
        subject: EmailSubject,
        booker_userid: BookerUserid,
        start_time: StartTimestamp,
        end_time: EndTimestamp,
        ctx: Context,
    ) -> BookMeetingRoomResult:
        try:
            request = BookMeetingRoomRequest(
                meetingroom_id=meetingroom_id,
                subject=subject,
                booker_userid=booker_userid,
                start_time=start_time,
                end_time=end_time,
            )
        except ValidationError as exc:
            raise ValueError(summarize_validation_error(exc)) from exc

        state = _require_state(ctx)
        await ctx.info(f"Booking room {meetingroom_id}")
        booking_id = await state.client.book_meeting_room(request)
        return BookMeetingRoomResult(
            booking_id=booking_id,
        )

    # ------------------------------------------------------------------
    # Tool: cancel_room_booking
    # ------------------------------------------------------------------

    @mcp.tool(
        name="cancel_room_booking",
        title="Cancel Room Booking",
        description="取消会议室预定。需要传入预定时返回的 booking_id。",
    )
    async def cancel_room_booking(
        booking_id: Annotated[str, Field(description="预定时 book_meeting_room 返回的 booking_id")],
        ctx: Context,
    ) -> CancelRoomBookingResult:
        if not booking_id.strip():
            raise ValueError("booking_id cannot be empty")

        state = _require_state(ctx)
        await ctx.info(f"Cancelling booking {booking_id}")
        await state.client.cancel_room_booking(booking_id=booking_id.strip())
        return CancelRoomBookingResult()

    # ------------------------------------------------------------------
    # Tool: list_employees
    # ------------------------------------------------------------------

    @mcp.tool(
        name="list_employees",
        title="List Employees",
        description=(
            "获取应用可见范围内的所有员工信息，包括 userid、姓名、英文名、部门、职位、直属上级等。"
            "注意：由于企业微信隐私策略限制，员工邮箱（email/biz_mail）无法通过此接口获取。"
        ),
    )
    async def list_employees(ctx: Context) -> ListEmployeesResult:
        state = _require_state(ctx)
        await ctx.info("Fetching departments and employees")

        raw_depts = await state.client.list_departments()
        departments = [
            Department(
                id=d.get("id", 0),
                name=d.get("name", ""),
                parentid=d.get("parentid", 0),
            )
            for d in raw_depts
            if isinstance(d, dict)
        ]

        seen_userids: set[str] = set()
        employees: list[Employee] = []
        for dept in departments:
            raw_members = await state.client.list_department_members(dept.id)
            for m in raw_members:
                if not isinstance(m, dict):
                    continue
                uid = m.get("userid", "")
                if uid in seen_userids:
                    continue
                seen_userids.add(uid)
                employees.append(Employee(
                    userid=uid,
                    name=m.get("name", ""),
                    english_name=m.get("english_name", ""),
                    alias=m.get("alias", ""),
                    department=m.get("department", []),
                    main_department=m.get("main_department", 0),
                    position=m.get("position", ""),
                    status=m.get("status", 1),
                    is_leader_in_dept=m.get("is_leader_in_dept", []),
                    direct_leader=m.get("direct_leader", []),
                ))

        return ListEmployeesResult(
            departments=departments,
            employees=employees,
            total=len(employees),
        )

    return mcp


def _require_state(ctx: Context) -> AppState:
    state = ctx.request_context.lifespan_context
    if not isinstance(state, AppState):
        raise RuntimeError("WeCom Mail MCP server state is unavailable.")
    return state
