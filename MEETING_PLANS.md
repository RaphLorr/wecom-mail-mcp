# Managed meeting plans

`create_managed_meeting(plan)` persists an authorized weekly plan, handles the
initial room/invitation pass, and returns every occurrence's independent state.
For a single occurrence, set `until_date` to the first meeting's local date.
Weekly occurrences preserve the first meeting's weekday and wall-clock time.
The end date is inclusive. Maximum: 52 occurrences and one year.

This is **not a native recurring calendar event**: the existing compose API wrapper
sends one invitation per occurrence. The tool explicitly describes this behavior;
obtain the user's agreement to this delivery mode before calling it. Do not call
the old send/book tools as well for the same dates.

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
- Explicit booking API rejections retry at most three write attempts, separated by
  at least one hour. Conflicts require a user-requested retry with
  `retry_managed_room_booking(plan_id,start_time)`.
- Timeouts, malformed responses after a write, and unfinished writes after a crash
  become `unknown`. They are not automatically retried: compare actual provider
  records before an operator reconciles the stored outcome. Invitation failures
  also do not trigger automatic resends.
- Availability query failures are never treated as proof that a room is empty.
- `get_managed_meeting` reports separate room/invite counts, worker health and
  per-occurrence booking IDs. `complete=true` requires all rooms booked and all
  invitations confirmed sent. `list_managed_meetings` finds existing plans.

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

This deployment does not import, resend, cancel, or change existing meetings.
To repair an older series, first audit every occurrence and its provider booking
records. Do not recreate it with this tool: doing so would resend invitations.
