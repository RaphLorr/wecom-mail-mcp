from __future__ import annotations

import base64
from collections.abc import Iterable, Sequence
from pathlib import Path

# Official limits for /cgi-bin/exmail/app/compose_send:
#   "附件个数不能超过200个"
#   "所有附件加正文的大小不允许超过50M"
# The size cap is enforced against what actually goes on the wire (base64 is
# ~4/3 of the raw bytes), so a 50M budget accepts roughly 37M of real files.
MAX_ATTACHMENT_COUNT = 200
MAX_TOTAL_PAYLOAD_BYTES = 50 * 1024 * 1024


class AttachmentError(ValueError):
    """An attachment could not be read, or its path is not permitted."""


def _resolved_roots(allowed_roots: Iterable[str]) -> list[Path]:
    roots: list[Path] = []
    for raw in allowed_roots:
        text = raw.strip()
        if text:
            roots.append(Path(text).expanduser().resolve())
    return roots


def resolve_attachment_path(path: str, allowed_roots: Iterable[str]) -> Path:
    """Resolve one attachment path, refusing anything outside the allowed roots.

    Attachments are the one place where a caller names an arbitrary file to be
    sent off the machine, so the path is confined to explicitly configured
    directories. `resolve()` follows symlinks *before* the check, so a link
    planted inside an allowed root cannot be used to reach outside it.
    """

    roots = _resolved_roots(allowed_roots)
    if not roots:
        raise AttachmentError(
            "Attachments are disabled: no allowed roots are configured. "
            "Set WECOM_ATTACHMENT_ROOTS to a list of directories "
            "(separated by the platform path separator) to enable them."
        )

    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise AttachmentError(f"Attachment path must be absolute: {path}")

    resolved = candidate.resolve()
    if not any(resolved.is_relative_to(root) for root in roots):
        allowed = ", ".join(str(root) for root in roots)
        raise AttachmentError(
            f"Attachment path is outside the allowed roots: {path}. Allowed roots: {allowed}"
        )
    if not resolved.exists():
        raise AttachmentError(f"Attachment not found: {path}")
    if not resolved.is_file():
        raise AttachmentError(f"Attachment is not a regular file: {path}")
    return resolved


def build_attachment_list(
    paths: Sequence[str],
    allowed_roots: Iterable[str],
    *,
    body_bytes: int = 0,
) -> list[dict[str, str]]:
    """Read each path and return the API's `attachment_list` payload.

    Raises rather than skipping: a silently dropped attachment is worse than a
    failed send, because the caller reports the mail as sent with the file on it.
    """

    if not paths:
        return []
    if len(paths) > MAX_ATTACHMENT_COUNT:
        raise AttachmentError(
            f"Too many attachments: {len(paths)} (the API allows at most {MAX_ATTACHMENT_COUNT})"
        )

    roots = list(allowed_roots)
    total = body_bytes
    items: list[dict[str, str]] = []
    for path in paths:
        resolved = resolve_attachment_path(path, roots)
        try:
            raw = resolved.read_bytes()
        except OSError as exc:
            raise AttachmentError(f"Cannot read attachment {path}: {exc}") from exc

        encoded = base64.b64encode(raw).decode("ascii")
        total += len(encoded)
        if total > MAX_TOTAL_PAYLOAD_BYTES:
            raise AttachmentError(
                "Attachments plus body exceed the 50M limit "
                f"(reached {total} bytes at {resolved.name})"
            )
        items.append({"file_name": resolved.name, "content": encoded})
    return items
