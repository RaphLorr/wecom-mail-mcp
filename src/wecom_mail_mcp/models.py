from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

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
