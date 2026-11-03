"""Safe diagnostics and conservative retry decisions for room operations."""

from .errors import WeComAPIError, WeComClientError, redact_diagnostic

# Official global codes: system busy and API rate limit. Unknown business
# rejections require intervention rather than speculative automatic retries.
TRANSIENT_ROOM_CODES = frozenset({-1, 45009})
MAX_ROOM_FAILURES = 3


def room_error_detail(exc: Exception, phase: str, request, row: dict) -> dict:
    if isinstance(exc, WeComAPIError):
        if exc.errcode in TRANSIENT_ROOM_CODES:
            category = "transient"
        elif exc.errcode == 40058:
            category = "invalid_parameter"
        else:
            category = "api_rejected"
    elif phase == "booking":
        category = "unknown_write"
    elif isinstance(exc, (WeComClientError, TimeoutError, ConnectionError)):
        category = "transient_read"
    else:
        category = "invalid_response"
    detail = {
        "type": type(exc).__name__,
        "phase": phase,
        "category": category,
        "retryable": category in {"transient", "transient_read"},
        "request": {
            "meetingroom_id": request.meetingroom_id,
            "start_time": row["start_time"],
            "end_time": row["end_time"],
        },
    }
    if isinstance(exc, WeComAPIError):
        detail.update(
            endpoint=redact_diagnostic(exc.endpoint),
            errcode=exc.errcode,
            errmsg=redact_diagnostic(exc.errmsg),
        )
    # Other exceptions can contain credential-bearing URLs in their message or
    # chain. Retain only their type, never str(exc) or a traceback.
    return detail
