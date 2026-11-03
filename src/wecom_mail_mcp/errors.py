from __future__ import annotations

import logging
import re

from pydantic import ValidationError


def redact_diagnostic(value: str) -> str:
    value = re.sub(r"https?://[^\s<>]+", "[URL redacted]", value, flags=re.IGNORECASE)
    value = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [redacted]", value)
    value = re.sub(
        r"""(?ix)\b(access[_-]?token|corp[_-]?secret|api[_-]?key|secret|token|password|authorization)\b
        ["']?\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;&}]+)""",
        r"\1=[redacted]",
        value,
    )
    value = re.sub(
        r"\b(?:sk-|eyk_|gh[pousr]_)[A-Za-z0-9_-]{16,}\b", "[redacted]", value
    )
    return " ".join(value.split())[:1000]


class CredentialLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_diagnostic(record.getMessage())
        record.args = ()
        return True


class WeComMailError(RuntimeError):
    """Base class for WeCom mail errors."""


class WeComConfigurationError(WeComMailError):
    """Raised when the service is misconfigured."""


class WeComClientError(WeComMailError):
    """Raised when an HTTP request fails before the API responds normally."""


class WeComResponseError(WeComMailError):
    """Raised when the API response is malformed."""


class WeComAPIError(WeComMailError):
    """Raised when the WeCom API returns a non-zero error code."""

    def __init__(self, endpoint: str, errcode: int, errmsg: str):
        self.endpoint = endpoint
        self.errcode = errcode
        self.errmsg = errmsg
        super().__init__(f"WeCom API error at {endpoint}: [{errcode}] {errmsg}")


def summarize_validation_error(exc: ValidationError) -> str:
    """Return a compact one-line validation summary."""

    first_error = exc.errors()[0]
    location = ".".join(str(part) for part in first_error.get("loc", ())) or "input"
    message = first_error.get("msg", "invalid value")
    return f"Invalid {location}: {message}"
