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
EmailContent = Annotated[str, Field(description="邮件正文")]
EmailContentType = Annotated[
    str,
    Field(description="正文类型。支持 text、html、text/plain、text/html。默认 text。"),
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
            "The sender address is always the configured WeCom app mailbox."
        ),
        lifespan=lifespan,
        host=settings.wecom_mcp_host,
        port=settings.wecom_mcp_port,
        log_level=settings.wecom_log_level,
    )

    @mcp.tool(
        name="send_email",
        title="Send Email",
        description="通过企业微信官方邮件 API 发送普通邮件。发件人固定为当前应用邮箱账号。",
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
