from __future__ import annotations

import base64
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wecom_mail_mcp.attachments import (
    MAX_ATTACHMENT_COUNT,
    AttachmentError,
    build_attachment_list,
    resolve_attachment_path,
)
from wecom_mail_mcp.config import Settings, load_settings


class ResolveAttachmentPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.allowed = self.root / "allowed"
        self.allowed.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def _file(self, parent: Path, name: str, data: bytes = b"hello") -> Path:
        path = parent / name
        path.write_bytes(data)
        return path

    def test_accepts_a_file_inside_an_allowed_root(self) -> None:
        target = self._file(self.allowed, "report.csv")
        resolved = resolve_attachment_path(str(target), [str(self.allowed)])
        self.assertEqual(resolved, target.resolve())

    def test_rejects_a_file_outside_the_allowed_roots(self) -> None:
        target = self._file(self.outside, "secret.json")
        with self.assertRaisesRegex(AttachmentError, "outside the allowed roots"):
            resolve_attachment_path(str(target), [str(self.allowed)])

    def test_rejects_a_symlink_escaping_an_allowed_root(self) -> None:
        secret = self._file(self.outside, "secret.json", b"token")
        link = self.allowed / "innocent.csv"
        try:
            link.symlink_to(secret)
        except (OSError, NotImplementedError):  # pragma: no cover - platform dependent
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(AttachmentError, "outside the allowed roots"):
            resolve_attachment_path(str(link), [str(self.allowed)])

    def test_rejects_traversal_out_of_an_allowed_root(self) -> None:
        self._file(self.outside, "secret.json")
        sneaky = self.allowed / ".." / "outside" / "secret.json"
        with self.assertRaisesRegex(AttachmentError, "outside the allowed roots"):
            resolve_attachment_path(str(sneaky), [str(self.allowed)])

    def test_rejects_everything_when_no_roots_are_configured(self) -> None:
        target = self._file(self.allowed, "report.csv")
        with self.assertRaisesRegex(AttachmentError, "Attachments are disabled"):
            resolve_attachment_path(str(target), [])

    def test_rejects_a_relative_path(self) -> None:
        with self.assertRaisesRegex(AttachmentError, "must be absolute"):
            resolve_attachment_path("report.csv", [str(self.allowed)])

    def test_rejects_a_missing_file(self) -> None:
        with self.assertRaisesRegex(AttachmentError, "Attachment not found"):
            resolve_attachment_path(str(self.allowed / "nope.csv"), [str(self.allowed)])

    def test_rejects_a_directory(self) -> None:
        (self.allowed / "sub").mkdir()
        with self.assertRaisesRegex(AttachmentError, "not a regular file"):
            resolve_attachment_path(str(self.allowed / "sub"), [str(self.allowed)])


class BuildAttachmentListTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

    def test_returns_nothing_without_paths(self) -> None:
        self.assertEqual(build_attachment_list([], [str(self.root)]), [])

    def test_encodes_each_file_as_base64(self) -> None:
        target = self.root / "report.csv"
        target.write_bytes(b"a,b\n1,2\n")

        items = build_attachment_list([str(target)], [str(self.root)])

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["file_name"], "report.csv")
        self.assertEqual(base64.b64decode(items[0]["content"]), b"a,b\n1,2\n")

    def test_keeps_an_empty_file(self) -> None:
        target = self.root / "empty.csv"
        target.write_bytes(b"")

        items = build_attachment_list([str(target)], [str(self.root)])

        self.assertEqual(items, [{"file_name": "empty.csv", "content": ""}])

    def test_rejects_more_than_the_api_allows(self) -> None:
        target = self.root / "one.txt"
        target.write_bytes(b"x")
        paths = [str(target)] * (MAX_ATTACHMENT_COUNT + 1)
        with self.assertRaisesRegex(AttachmentError, "Too many attachments"):
            build_attachment_list(paths, [str(self.root)])

    def test_rejects_when_the_total_exceeds_the_limit(self) -> None:
        target = self.root / "big.bin"
        target.write_bytes(b"x" * 1024)
        with self.assertRaisesRegex(AttachmentError, "exceed the 50M limit"):
            build_attachment_list(
                [str(target)],
                [str(self.root)],
                body_bytes=50 * 1024 * 1024,
            )

    def test_reports_the_offending_file_rather_than_skipping_it(self) -> None:
        good = self.root / "good.txt"
        good.write_bytes(b"ok")
        with self.assertRaisesRegex(AttachmentError, "Attachment not found"):
            build_attachment_list(
                [str(good), str(self.root / "missing.txt")],
                [str(self.root)],
            )


class SettingsAttachmentRootsTests(unittest.TestCase):
    """Settings are alias-only, so go through the environment like test_config does."""

    def setUp(self) -> None:
        self._original_cwd = Path.cwd()
        self._temp_dir = Path(tempfile.mkdtemp())
        os.chdir(self._temp_dir)
        self.addCleanup(shutil.rmtree, self._temp_dir)
        self.addCleanup(os.chdir, self._original_cwd)

    def _settings(self, roots: str | None) -> Settings:
        env = {"CORPID": "corp", "CORPSECRET": "secret"}
        if roots is not None:
            env["WECOM_ATTACHMENT_ROOTS"] = roots
        with patch.dict(os.environ, env, clear=True):
            return load_settings()

    def test_defaults_to_disabled(self) -> None:
        self.assertEqual(self._settings(None).attachment_roots, ())

    def test_splits_on_the_platform_separator(self) -> None:
        roots = self._settings(f" /a {os.pathsep} /b ").attachment_roots
        self.assertEqual(roots, ("/a", "/b"))

    def test_drops_empty_segments(self) -> None:
        roots = self._settings(f"/a{os.pathsep}{os.pathsep}/b").attachment_roots
        self.assertEqual(roots, ("/a", "/b"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
