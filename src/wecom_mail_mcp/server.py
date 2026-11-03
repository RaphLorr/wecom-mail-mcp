from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
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
    AttachmentSpec,
    SendEmailRequest,
    SendEmailResult,
    SendMeetingEmailRequest,
    SendMeetingEmailResult,
    SendScheduleEmailRequest,
    SendScheduleEmailResult,
    WeeklyRepeat,
)
from .wecom import WeComMailClient
from .meeting_plans import MeetingPlans, PlanStore, WeeklyMeetingRequest

# ---------------------------------------------------------------------------
# Annotated type aliases for tool parameters
# ---------------------------------------------------------------------------

RecipientEmail = Annotated[
    str, Field(description="收件人邮箱地址，例如 user@example.com")
]
RecipientEmails = Annotated[
    list[str],
    Field(
        description="收件人邮箱地址列表，例如 ['user1@example.com', 'user2@example.com']"
    ),
]
EmailSubject = Annotated[str, Field(description="邮件主题")]
MeetingLocation = Annotated[
    str, Field(description="会议地点，例如 '3楼 FRONTIER 会议室'")
]
StartTimestamp = Annotated[
    int,
    Field(description="开始时间，Unix 时间戳（秒）。注意时间以北京时间（UTC+8）为准。"),
]
EndTimestamp = Annotated[
    int,
    Field(description="结束时间，Unix 时间戳（秒）。注意时间以北京时间（UTC+8）为准。"),
]
RemindBeforeMins = Annotated[int, Field(description="提前提醒分钟数，默认 15 分钟")]
MeetingAdminUserid = Annotated[
    str, Field(description="会议管理员的企业微信 userid，严禁使用其他猜测 ID")
]
MeetingRoomId = Annotated[
    int, Field(description="会议室 ID，可通过 list_meeting_rooms 获取")
]
BookerUserid = Annotated[
    str, Field(description="预定人的企业微信 userid，严禁使用其他猜测 ID")
]
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
    "HTML 骨架建议："
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
    "<tr><td>"
    '<h2 style="margin:0 0 16px;">标题</h2>'
    '<p style="margin:0 0 12px;">正文</p>'
    '<a href="https://example.com">链接</a>'
    '<img src="https://example.com/demo.png" alt="" style="display:block;width:100%;height:auto;border:0;">'
    "</td></tr></table>"
)
EmailContent = Annotated[
    str,
    Field(
        description="邮件正文。默认纯文本。如需 HTML 请同时设置 content_type=html，使用 table 布局和 inline style。"
    ),
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
EmailAttachments = Annotated[
    list[str | AttachmentSpec] | None,
    Field(
        description=(
            "附件列表。每项可以是本机绝对路径，也可以是 {path, file_name}。"
            "【收件人看到的文件名就是 file_name】不传 file_name 就用磁盘上的文件名，"
            "而聊天工具收到的文件在磁盘上通常是一串 UUID（如 34ea5294-….xlsx），"
            "直接发出去收件人看到的就是那串 UUID。所以只要你知道这个文件本来的名字，"
            "就一定要传 file_name——比如先用文件检索工具拿到原始文件名，再连同路径一起传："
            "[{'path': '/root/.openclaw/media/inbound/34ea5294-….xlsx', "
            "'file_name': '2026企业微信假期余额模板.xlsx'}]。"
            "服务端自己读取文件并 base64 编码，调用方不要自己编码、也不需要外部图床。"
            "只允许读取管理员配置的目录（WECOM_ATTACHMENT_ROOTS），其它路径一律拒绝。"
            "附件最多 200 个，所有附件加正文不超过 50M。"
            "任何一个附件读不到或不被允许都会直接报错，不会静默跳过——"
            "报错时如实转达，不要改用其它方式发送。"
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
            "周期邀请用 send_schedule_email 的 repeat，一次发送整个系列；只有需要会议室滚动预约时才使用 create_managed_meeting。"
            "不要把邮件成功、location 地点文字或部分订房成功当成全部完成。"
            f"{HTML_EMAIL_GUIDANCE}"
        ),
        lifespan=lifespan,
        host=settings.wecom_mcp_host,
        port=settings.wecom_mcp_port,
        log_level=settings.wecom_log_level,
    )

    @mcp.tool(
        description=(
            "创建托管会议计划：按首场会议的当地星期/时间每周重复到 until_date（含）。"
            "也可把 until_date 设为首场日期创建单场。只发送一次带原生 repeat 的周期邀请，会议室才逐场处理。"
            "会议室只预约允许窗口内的日期，窗口外持久化待补订，由独立 worker 滚动处理。"
            "request_id 使用原用户消息 ID，重试必须复用；不同参数不能复用同一 ID。"
            "需明确用户已授权会议、收件人、会议室、周期结束日期及滚动补订。notify_email 必须是已确认的组织者邮箱。"
            "不要再按每场发送邮件；没有会议室需求时直接调用 send_schedule_email(repeat=...)，无需后台任务。"
            "只有返回 complete=true 才能声称全部订房和邀请成功；否则逐项报告 pending_window/conflict/failed/unknown。"
            "订房错误详情见 room_error_detail；参数/权限等业务拒绝不自动重试，只有明确临时错误有限重试。"
            "failed 需检查并修正原因；unknown 是写入结果不明，必须核对，不能盲目重订。"
            "同名地点 location 仅为文字，绝不能当成订房成功。worker 不健康时拒绝创建，避免承诺未来不会执行的补订。"
        )
    )
    async def create_managed_meeting(plan: WeeklyMeetingRequest, ctx: Context) -> dict:
        state = _require_state(ctx)
        store = PlanStore(settings.wecom_meeting_store, settings.wecom_corp_id)
        try:
            return await MeetingPlans(
                store, state.client, settings.wecom_room_advance_days
            ).create(plan)
        finally:
            store.close()

    @mcp.tool(
        description="查询托管会议计划的每场邀请、订房、booking_id、待补订/冲突/失败状态及 worker 健康。只查询，不重新发送。"
    )
    async def get_managed_meeting(plan_id: str, ctx: Context) -> dict:
        state = _require_state(ctx)
        store = PlanStore(settings.wecom_meeting_store, settings.wecom_corp_id)
        try:
            return MeetingPlans(
                store, state.client, settings.wecom_room_advance_days
            ).status(plan_id)
        finally:
            store.close()

    @mcp.tool(
        description="列出当前企业应用存储的托管会议计划摘要，用于找回 plan_id；不发送邮件或预订。"
    )
    async def list_managed_meetings(ctx: Context) -> list[dict]:
        _require_state(ctx)
        store = PlanStore(settings.wecom_meeting_store, settings.wecom_corp_id)
        try:
            return [
                {
                    "plan_id": p["id"],
                    "subject": p["request"]["subject"],
                    "state": p["state"],
                    "location": p["request"]["location"],
                    "occurrences": len(p["occurrences"]),
                }
                for p in store.active()
            ]
        finally:
            store.close()

    @mcp.tool(
        description=(
            "取消托管计划及未来已知 booking_id 的会议室预订，停止所有未来补订。"
            "需要用户明确要求取消。不会取消已发送的日历邀请，必须明确告知这一限制。"
            "unknown 预订和取消失败项仍需核对，不能声称已全部取消。"
        )
    )
    async def cancel_managed_meeting(plan_id: str, ctx: Context) -> dict:
        state = _require_state(ctx)
        store = PlanStore(settings.wecom_meeting_store, settings.wecom_corp_id)
        try:
            return await MeetingPlans(
                store, state.client, settings.wecom_room_advance_days
            ).cancel(plan_id)
        finally:
            store.close()

    @mcp.tool(
        description="用户要求重试时，重试托管计划中一个明确失败/冲突的会议室场次；不重复发送邀请。unknown 结果不允许盲目重试。"
    )
    async def retry_managed_room_booking(
        plan_id: str, start_time: int, ctx: Context
    ) -> dict:
        state = _require_state(ctx)
        store = PlanStore(settings.wecom_meeting_store, settings.wecom_corp_id)
        try:
            return await MeetingPlans(
                store, state.client, settings.wecom_room_advance_days
            ).retry_room(plan_id, start_time)
        finally:
            store.close()

    @mcp.tool(
        description="用户明确要求重试时，只补发未发送或被业务接口拒绝的原生周期邀请。已发送/结果不明的邀请禁止重发；后台补订任务从不发送邀请。"
    )
    async def retry_managed_invitation(plan_id: str, ctx: Context) -> dict:
        state = _require_state(ctx)
        store = PlanStore(settings.wecom_meeting_store, settings.wecom_corp_id)
        try:
            return await MeetingPlans(
                store, state.client, settings.wecom_room_advance_days
            ).retry_invitation(plan_id)
        finally:
            store.close()

    # ------------------------------------------------------------------
    # Tool: send_email
    # ------------------------------------------------------------------

    @mcp.tool(
        name="send_email",
        title="Send Email",
        description=(
            "通过企业微信官方邮件 API 发送普通邮件，支持附件"
            "（attachments 传本机绝对路径；文件本来的名字用 file_name 一起传，否则收件人看到的是磁盘上的 UUID 名）。"
            "发件人固定为当前应用邮箱账号。"
            f"{HTML_EMAIL_GUIDANCE}"
        ),
    )
    async def send_email(
        to_email: RecipientEmail,
        subject: EmailSubject,
        content: EmailContent,
        ctx: Context,
        content_type: EmailContentType = "text",
        attachments: EmailAttachments = None,
    ) -> SendEmailResult:
        try:
            request = SendEmailRequest(
                to_email=to_email,
                subject=subject,
                content=content,
                content_type=content_type,
                attachments=attachments or [],
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
            attachments=[
                item.file_name or Path(item.path).name for item in request.attachments
            ],
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
            "发送日程邀请邮件；repeat 可设置原生每周重复（repeat_type=1，repeat_day_of_week 周一=1至周日=7，repeat_until 为结束时间戳）。"
            "一次发送整个周期邀请，不按每场发邮件。不需要会议室时直接用本工具，无需托管计划或后台任务。"
            "本工具不预订会议室；location 仅为文字。需要周期订房时使用 create_managed_meeting，避免再调用本工具重复发邀请。"
            "适用于大多数会议邀请场景。如需创建腾讯会议（含会议链接），请使用 send_meeting_email。"
            "当前工具不暴露取消已发送邀请的操作；API 取消需要已知 schedule_id，发送响应未可靠返回该 ID。"
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
        repeat: WeeklyRepeat | None = None,
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
                repeat=repeat,
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
            repeat=request.repeat,
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
        enable_waiting_room: Annotated[
            bool, Field(description="是否开启等候室")
        ] = True,
        allow_enter_before_host: Annotated[
            bool, Field(description="是否允许在主持人之前入会")
        ] = True,
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
            city=city,
            building=building,
            floor=floor,
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
        meetingroom_id: Annotated[
            int | None, Field(description="会议室 ID（可选，不传则查询所有）")
        ] = None,
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
        booking_id: Annotated[
            str, Field(description="预定时 book_meeting_room 返回的 booking_id")
        ],
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
                employees.append(
                    Employee(
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
                    )
                )

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
