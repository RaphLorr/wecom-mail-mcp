from __future__ import annotations

from pydantic import ValidationError


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
