# Managed meeting plans

`create_managed_meeting(plan)` persists an authorized weekly plan, handles the
initial room/invitation pass, and returns every occurrence's independent state.
For a single occurrence, set `until_date` to the first meeting's local date.
Weekly occurrences preserve the first meeting's weekday and wall-clock time.
The end date is inclusive. Maximum: 52 occurrences and one year.

The calendar invitation is **one native recurring invitation for the whole series**.
The mail API's `schedule.reminders` carries `is_repeat`, `is_custom_repeat`,
`repeat_type=1`, `repeat_interval`, `repeat_day_of_week` and `repeat_until`.
Only room bookings are expanded into individual occurrences. The worker never
sends calendar invitations. Do not call the old send/book tools as well for the
same series/dates.

If no meeting room is needed, call `send_schedule_email` with `repeat` directly;
no plan database, organizer alert email or worker is required. Example:

```json
{
  "repeat": {
    "repeat_type": 1,
    "repeat_interval": 1,
    "repeat_day_of_week": [1],
    "timezone": 8,
    "repeat_until": 1798473599
  }
}
```

Weekdays are 1 (Monday) through 7 (Sunday). The public tool currently exposes
weekly recurrence; omit `repeat` for a single event. The managed plan derives the
weekly rule from its first local date/time and inclusive `until_date`, without
asking the model to keep room and invitation rules in sync. Because the mail API
uses an integer UTC offset, managed periods crossing DST or using fractional-hour
offsets are rejected instead of silently shifting or splitting the series.

Required plan fields:

```json
{
  "request_id": "stable-original-user-message-id",
  "to_emails": ["attendee@example.com"],
  "subject": "Weekly review",
  "content": "Review agenda",
  "location": "Confirmed room name",
  "start_time": 1794189600,
  "end_time": 1794193200,
  "until_date": "2026-12-28",
  "timezone": "Asia/Shanghai",
  "meetingroom_id": 8,
  "booker_userid": "confirmed-wecom-userid",
  "notify_email": "confirmed-organizer@example.com"
}
```

Timestamps and addresses above are examples, not account defaults. Resolve real
dates and identities before writing. Invitations include a notice that future
room reservations may still be pending. A location string never reserves a room.

## Rolling room reservations

- `WECOM_ROOM_ADVANCE_DAYS` defaults to 30. Configure it to the actual room policy;
  this does not override WeCom's server-side booking restrictions.
- Dates outside this window are `pending_window` with an `eligible_at` timestamp.
  The worker checks availability and books them when eligible. It never books an
  alternative room without an explicit new user decision.
- Only explicit transient API rejections (`-1` system busy, `45009` rate limit)
  and transport failures during availability reads automatically retry. At most
  three failures per occurrence, separated by at least one hour; failed reads
  count toward the same limit. Parameter errors (`40058`), permission errors and
  other business rejections become `failed` immediately, with no scheduled retry.
  Existing `retry` records carrying a non-transient API code stop at the next
  worker pass before another request. Conflicts and corrected definite failures
  require a user-requested `retry_managed_room_booking(plan_id,start_time)`.
- Timeouts, malformed responses after a write, and unfinished writes after a crash
  become `unknown`. They are not automatically retried: compare actual provider
  records before an operator reconciles the stored outcome. Invitation failures
  also do not trigger automatic resends.
- Availability query failures are never treated as proof that a room is empty.
- `room_error_detail` retains the failure phase, error type, API endpoint/code,
  sanitized provider `errmsg`, retry eligibility and room/time parameters.
  Credential assignments and URLs are redacted; generic exception messages and
  request tracebacks are not copied into diagnostics. No full request, invitation
  body, credentials or token-bearing URL is recorded. Details also appear in
  organizer failure alerts. Successful recovery clears stale errors. Older
  code-only records cannot recover provider explanations that were never saved.
- `get_managed_meeting` reports one series-level `invite` outcome, room counts,
  worker health and per-occurrence booking IDs. `complete=true` requires all rooms
  booked and the one invitation confirmed sent. `list_managed_meetings` finds
  existing plans. The room worker pauses further bookings if the invitation is
  not confirmed sent. `retry_managed_invitation` is an explicit user action for
  definite rejections/unattempted sends only; unknown or sent invitations cannot
  be replayed.

The original request ID is idempotent across process restarts. Reusing it with a
different payload is rejected. Overlapping active managed room/time plans are
also rejected. A process-shared file lock serializes API writes by MCP instances
and the worker. SQLite uses durable commits before and after external writes.
It cannot make the provider APIs transactional or guarantee exactly-once delivery
after an ambiguous network result.

Changes to booked/conflicting/failed states generate a batched email to the
confirmed `notify_email`. Notification attempts are durable and ambiguous sends
are not replayed. Status queries remain the authoritative recovery view; alerts
cannot be guaranteed if the email provider itself fails.

## Running the worker

Install an independent timer; a short-lived MCP process is not a scheduler:

```bash
python -m wecom_mail_mcp.meeting_worker
```

It uses the same environment credentials and `WECOM_MEETING_STORE` as the MCP
server. The default store is `~/.local/share/wecom-mail-mcp/meeting-plans.sqlite`,
permission 0600, isolated by corporation ID. Keep it on a local filesystem with
working SQLite/file-lock support. Do not delete the store during deployment.

For this repository's OpenClaw deployment, the worker can reuse the configured
`mcp.servers.wecom-mail.env` without copying secrets into a unit:

```bash
python -m wecom_mail_mcp.meeting_worker --openclaw-config /root/.openclaw/openclaw.json
```

Run every 15 minutes and at boot, with systemd persistent calendar catch-up. The
worker writes a heartbeat even with no plans. Creation is rejected if no heartbeat
exists in the past two hours. An overdue worker is reported in status; externally
monitor the timer so outages do not silently accumulate pending dates.

This server's unit files are in `deploy/systemd/`. Copy them to the operator's
`~/.config/systemd/user/`, run `systemctl --user daemon-reload`, then
`systemctl --user enable --now wecom-meeting-worker.timer` and
`systemctl --user start wecom-meeting-worker.service` for the first heartbeat.
Ensure that user's lingering is enabled for work to continue without an SSH login.
Inspect `systemctl --user list-timers` and the worker journal after installation.

## Cancellation and existing meetings

`cancel_managed_meeting` disables future processing first, then cancels known
future booking IDs. Failed/unknown cancellations stay visible. It does **not**
cancel sent calendar invitations: the existing compose wrapper does not expose
a reliably manageable calendar event ID.

Version 2 plan records store invitation state at series level. Any old
per-occurrence plan is quarantined as `requires_review`, without sending or
automatically converting invitations. This deployment does not import, resend,
cancel, or change existing meetings.
To repair an older series, first audit every occurrence and its provider booking
records. Do not recreate it with this tool: doing so would resend invitations.

API source: [WeCom: send schedule email](https://developer.work.weixin.qq.com/document/path/97854).
