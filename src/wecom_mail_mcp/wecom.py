from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings
from .errors import WeComAPIError, WeComClientError, WeComResponseError
from .models import (
    BookMeetingRoomRequest,
    MailboxInfo,
    SendEmailRequest,
    SendMeetingEmailRequest,
    SendScheduleEmailRequest,
)

TOKEN_ERRCODES = {40001, 40014, 42001}


@dataclass(slots=True)
class TokenState:
    access_token: str | None = None
    expires_at: float = 0.0

    def is_valid(self) -> bool:
        return self.access_token is not None and time.monotonic() < self.expires_at


class WeComMailClient:
    """Thin async client for the official WeCom mail API."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.wecom_api_base,
            timeout=settings.wecom_request_timeout,
            headers={"User-Agent": "wecom-mail-mcp/0.1.0"},
        )
        self._token_state = TokenState()
        self._token_lock = asyncio.Lock()
        self._mailbox_info: MailboxInfo | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Mailbox
    # ------------------------------------------------------------------

    async def get_mailbox_info(self, *, force_refresh: bool = False) -> MailboxInfo:
        if self._mailbox_info is not None and not force_refresh:
            return self._mailbox_info

        data = await self._request_authed(
            "POST",
            "/cgi-bin/exmail/app/get_email_alias",
            endpoint_name="get_email_alias",
        )
        mailbox = MailboxInfo(
            email=self._require_string(data, "email", "Current sender mailbox is missing from the API response."),
            alias_list=self._coerce_string_list(data.get("alias_list")),
        )
        self._mailbox_info = mailbox
        return mailbox

    # ------------------------------------------------------------------
    # Plain email
    # ------------------------------------------------------------------

    async def send_email(self, request: SendEmailRequest) -> MailboxInfo:
        mailbox = await self.get_mailbox_info()
        payload = {
            "to": {"emails": [request.to_email]},
            "subject": request.subject,
            "content": request.content,
            "content_type": request.content_type,
        }
        await self._request_authed(
            "POST",
            "/cgi-bin/exmail/app/compose_send",
            endpoint_name="compose_send",
            json_body=payload,
        )
        return mailbox

    # ------------------------------------------------------------------
    # Schedule email (日程邮件)
    # ------------------------------------------------------------------

    async def send_schedule_email(self, request: SendScheduleEmailRequest) -> MailboxInfo:
        mailbox = await self.get_mailbox_info()
        payload = {
            "to": {"emails": request.to_emails},
            "subject": request.subject,
            "content": request.content,
            "content_type": request.content_type,
            "schedule": {
                "method": "request",
                "location": request.location,
                "start_time": request.start_time,
                "end_time": request.end_time,
                "reminders": {
                    "is_remind": 1,
                    "remind_before_event_mins": request.remind_before_mins,
                },
            },
        }
        await self._request_authed(
            "POST",
            "/cgi-bin/exmail/app/compose_send",
            endpoint_name="compose_send_schedule",
            json_body=payload,
        )
        return mailbox

    # ------------------------------------------------------------------
    # Meeting email (会议邮件 with 腾讯会议)
    # ------------------------------------------------------------------

    async def send_meeting_email(self, request: SendMeetingEmailRequest) -> MailboxInfo:
        mailbox = await self.get_mailbox_info()
        payload = {
            "to": {"emails": request.to_emails},
            "subject": request.subject,
            "content": request.content,
            "content_type": request.content_type,
            "schedule": {
                "method": "request",
                "location": request.location,
                "start_time": request.start_time,
                "end_time": request.end_time,
                "reminders": {
                    "is_remind": 1,
                    "remind_before_event_mins": request.remind_before_mins,
                },
            },
            "meeting": {
                "option": {
                    "enable_waiting_room": request.enable_waiting_room,
                    "allow_enter_before_host": request.allow_enter_before_host,
                },
                "meeting_admins": {
                    "userids": [request.meeting_admin_userid],
                },
            },
        }
        await self._request_authed(
            "POST",
            "/cgi-bin/exmail/app/compose_send",
            endpoint_name="compose_send_meeting",
            json_body=payload,
        )
        return mailbox

    # ------------------------------------------------------------------
    # Meeting rooms
    # ------------------------------------------------------------------

    async def list_meeting_rooms(
        self,
        city: str = "",
        building: str = "",
        floor: str = "",
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {}
        if city:
            body["city"] = city
        if building:
            body["building"] = building
        if floor:
            body["floor"] = floor

        data = await self._request_authed(
            "POST",
            "/cgi-bin/oa/meetingroom/list",
            endpoint_name="meetingroom_list",
            json_body=body,
        )
        raw_list = data.get("meetingroom_list", [])
        return raw_list if isinstance(raw_list, list) else []

    async def query_room_availability(
        self,
        start_time: int,
        end_time: int,
        meetingroom_id: int | None = None,
        city: str = "",
        building: str = "",
        floor: str = "",
    ) -> list[dict[str, Any]]:
        body: dict[str, Any] = {
            "start_time": start_time,
            "end_time": end_time,
        }
        if meetingroom_id is not None:
            body["meetingroom_id"] = meetingroom_id
        if city:
            body["city"] = city
        if building:
            body["building"] = building
        if floor:
            body["floor"] = floor

        data = await self._request_authed(
            "POST",
            "/cgi-bin/oa/meetingroom/get_booking_info",
            endpoint_name="meetingroom_get_booking_info",
            json_body=body,
        )
        raw_list = data.get("booking_list", [])
        return raw_list if isinstance(raw_list, list) else []

    async def book_meeting_room(self, request: BookMeetingRoomRequest) -> str:
        """Book a meeting room. Returns booking_id."""

        body = {
            "meetingroom_id": request.meetingroom_id,
            "subject": request.subject,
            "booker": request.booker_userid,
            "start_time": request.start_time,
            "end_time": request.end_time,
        }
        data = await self._request_authed(
            "POST",
            "/cgi-bin/oa/meetingroom/book",
            endpoint_name="meetingroom_book",
            json_body=body,
        )
        booking_id = self._require_string(
            data, "booking_id", "WeCom did not return booking_id after booking."
        )
        return booking_id

    async def cancel_room_booking(self, booking_id: str) -> None:
        """Cancel a meeting room booking."""

        await self._request_authed(
            "POST",
            "/cgi-bin/oa/meetingroom/cancel_book",
            endpoint_name="meetingroom_cancel_book",
            json_body={
                "booking_id": booking_id,
            },
        )

    # ------------------------------------------------------------------
    # Token management (unchanged)
    # ------------------------------------------------------------------

    async def _request_authed(
        self,
        method: str,
        path: str,
        *,
        endpoint_name: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = await self._get_access_token()
        data = await self._request_json(
            method,
            path,
            endpoint_name=endpoint_name,
            params={"access_token": token},
            json_body=json_body,
        )

        errcode = self._coerce_errcode(data)
        if errcode in TOKEN_ERRCODES:
            token = await self._get_access_token(force_refresh=True)
            data = await self._request_json(
                method,
                path,
                endpoint_name=endpoint_name,
                params={"access_token": token},
                json_body=json_body,
            )

        self._ensure_success(data, endpoint_name)
        return data

    async def _get_access_token(self, *, force_refresh: bool = False) -> str:
        async with self._token_lock:
            if self._token_state.is_valid() and not force_refresh:
                return self._token_state.access_token or ""

            data = await self._request_json(
                "GET",
                "/cgi-bin/gettoken",
                endpoint_name="gettoken",
                params={
                    "corpid": self._settings.wecom_corp_id,
                    "corpsecret": self._settings.wecom_corp_secret.get_secret_value(),
                },
            )
            self._ensure_success(data, "gettoken")

            access_token = self._require_string(
                data,
                "access_token",
                "WeCom returned a successful token response without access_token.",
            )
            expires_in = self._coerce_positive_int(data.get("expires_in"), default=7200)
            safety_margin = 300 if expires_in > 600 else max(expires_in // 10, 30)
            self._token_state = TokenState(
                access_token=access_token,
                expires_at=time.monotonic() + max(expires_in - safety_margin, 30),
            )
            return access_token

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        endpoint_name: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, params=params, json=json_body)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise WeComClientError(f"Request to WeCom API timed out at {endpoint_name}.") from exc
        except httpx.HTTPStatusError as exc:
            raise WeComClientError(
                f"WeCom API returned HTTP {exc.response.status_code} at {endpoint_name}."
            ) from exc
        except httpx.RequestError as exc:
            raise WeComClientError(f"Unable to reach WeCom API at {endpoint_name}: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise WeComResponseError(f"WeCom API returned non-JSON content at {endpoint_name}.") from exc

        if not isinstance(data, dict):
            raise WeComResponseError(f"WeCom API returned an unexpected payload at {endpoint_name}.")

        return data

    @staticmethod
    def _coerce_errcode(data: dict[str, Any]) -> int:
        errcode = data.get("errcode", 0)
        try:
            return int(errcode)
        except (TypeError, ValueError):
            return -1

    @staticmethod
    def _coerce_positive_int(value: Any, *, default: int) -> int:
        try:
            candidate = int(value)
        except (TypeError, ValueError):
            return default
        return candidate if candidate > 0 else default

    @staticmethod
    def _require_string(data: dict[str, Any], key: str, error_message: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise WeComResponseError(error_message)
        return value.strip()

    @staticmethod
    def _coerce_string_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]

    @staticmethod
    def _ensure_success(data: dict[str, Any], endpoint_name: str) -> None:
        errcode = WeComMailClient._coerce_errcode(data)
        if errcode == 0:
            return

        errmsg = data.get("errmsg")
        if not isinstance(errmsg, str) or not errmsg.strip():
            errmsg = "unknown error"
        raise WeComAPIError(endpoint_name, errcode, errmsg.strip())
