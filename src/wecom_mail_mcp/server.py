from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field, ValidationError

from .config import Settings
from .errors import summarize_validation_error
from .models import MailboxInfoResult, SendEmailRequest, SendEmailResult
from .wecom import WeComMailClient

RecipientEmail = Annotated[str, Field(description="收件人邮箱地址，例如 user@example.com")]
EmailSubject = Annotated[str, Field(description="邮件主题")]
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
    Field(description=f"邮件正文。{HTML_EMAIL_GUIDANCE}{HTML_EMAIL_TEMPLATE_HINT}"),
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
            "Use send_email to send outbound email through the official WeCom mail API. "
            "The sender address is always the configured WeCom app mailbox. "
            f"{HTML_EMAIL_GUIDANCE}"
        ),
        lifespan=lifespan,
        host=settings.wecom_mcp_host,
        port=settings.wecom_mcp_port,
        log_level=settings.wecom_log_level,
    )

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

    return mcp


def _require_state(ctx: Context) -> AppState:
    state = ctx.request_context.lifespan_context
    if not isinstance(state, AppState):
        raise RuntimeError("WeCom Mail MCP server state is unavailable.")
    return state
