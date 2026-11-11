"""Organizer alerts for actual exceptions, using business-facing wording."""

from datetime import datetime
from zoneinfo import ZoneInfo

from .errors import redact_diagnostic

ROOM_ALERT_STATES = frozenset({"conflict", "failed", "retry", "unknown", "expired"})
INVITE_ALERT_STATES = frozenset({"failed", "unknown", "not_sent", "expired"})


def render_meeting_alert(plan: dict, events: list) -> str:
    zone = ZoneInfo(plan["request"]["timezone"])
    lines = [
        f"会议：{plan['request']['subject']}",
        f"会议室：{plan['request']['location']}",
        f"时区：{plan['request']['timezone']}",
    ]
    for _, row, kind in events:
        start = datetime.fromtimestamp(row["start_time"], zone)
        if kind == "invite":
            message = {
                "failed": "日历邀请发送失败，未自动重发。",
                "unknown": "日历邀请发送结果尚未确认。请核对已发送邮件或日历，核实前不会自动重发。",
                "not_sent": "日历邀请尚未发送，后续自动订房已暂停。",
                "expired": "会议已到开始时间，日历邀请未发送，不再尝试发送。",
            }[row["invite"]]
            lines.extend(["", f"{start:%Y-%m-%d %H:%M}：{message}"])
            continue
        end = datetime.fromtimestamp(row["end_time"], zone)
        detail = row.get("room_error_detail", {})
        if row["room"] == "retry":
            next_attempt = row.get("next_attempt", 0)
            when = (
                datetime.fromtimestamp(next_attempt, zone).strftime("%Y-%m-%d %H:%M")
                if next_attempt
                else "下一次后台检查时"
            )
            action = "空闲查询" if detail.get("phase") == "availability" else "预订"
            message = f"本次{action}暂未成功，系统将在{when}自动重试，会议室尚未确认。"
        else:
            message = {
                "conflict": "该时段已有预订，会议室未订成功。请调整时间或选择其他会议室。",
                "failed": (
                    "空闲查询失败，未发起本次预订；已停止自动重试。"
                    if detail.get("phase") == "availability"
                    else "会议室预订失败，已停止自动重试。请检查原因后处理。"
                ),
                "unknown": "预订请求结果尚未确认。请先在企业微信核对实际预订，核实前不会自动重订。",
                "expired": "会议已到开始时间，预订未完成，不再自动尝试。",
            }[row["room"]]
        lines.extend(["", f"{start:%Y-%m-%d %H:%M}–{end:%H:%M}：{message}"])
        if detail.get("errcode") is not None:
            lines.append(f"错误码：{detail['errcode']}")
        if detail.get("errmsg"):
            lines.append("接口说明：" + redact_diagnostic(detail["errmsg"]))
    return "\n".join(lines)
