"""Selezione automatica: nessuna scelta silenziosa di una copia vecchia."""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from su_print_counters.simple_selection import find_latest_snapshot


class SimpleSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="su simple desktop ")
        self.addCleanup(self.tmp.cleanup)
        self.desktop = Path(self.tmp.name)

    def copy(self, stamp="20261029_192453", suffix="abcdefgh"):
        folder = self.desktop / f"SU_Print_Collaudo_{stamp}_{suffix}"
        folder.mkdir()
        (folder / "snapshot.sqlite").write_bytes(b"SQLite format 3\x00" + b"synthetic fixture")
        (folder / "schema_collaudo.json").write_text(json.dumps(
            {"format_version": 1, "source_file": "synthetic.db", "objects": []}
        ), encoding="utf-8")
        return folder

    def test_newest_creation_name_wins_over_database_mtime_and_preserves_files(self):
        old = self.copy("20261029_131713")
        new = self.copy()
        os.utime(old / "snapshot.sqlite", (2_000_000_000, 2_000_000_000))
        os.utime(new / "snapshot.sqlite", (1_000_000_000, 1_000_000_000))
        marker = self.desktop / "su_print_intelligence.db"
        marker.write_bytes(b"active database must never be inspected")
        before = {p: hashlib.sha256(p.read_bytes()).digest()
                  for p in self.desktop.rglob("*") if p.is_file()}
        self.assertEqual(find_latest_snapshot(self.desktop), new / "snapshot.sqlite")
        self.assertEqual({p: hashlib.sha256(p.read_bytes()).digest()
                          for p in self.desktop.rglob("*") if p.is_file()}, before)

    def test_unrelated_and_impossible_folder_names_are_ignored(self):
        good = self.copy()
        for name in ("SU_Print_Collaudo_20269999_123456_abcdefgh", "SU_Print_Collaudo_backup",
                     "SU_Print_Collaudo_20261030_192453_ABCDEFGH", "other"):
            (self.desktop / name).mkdir()
        nested = self.desktop / "other" / "SU_Print_Collaudo_20261030_192453_abcdefgh"
        nested.mkdir()
        self.assertEqual(find_latest_snapshot(self.desktop), good / "snapshot.sqlite")

    def test_no_matching_copy_does_not_search_other_locations(self):
        (self.desktop / "snapshot.sqlite").write_bytes(b"not a guided copy")
        with self.assertRaises(FileNotFoundError):
            find_latest_snapshot(self.desktop)

    def test_missing_or_non_directory_desktop_has_a_clear_error(self):
        with self.assertRaisesRegex(FileNotFoundError, "Desktop"):
            find_latest_snapshot(self.desktop / "missing desktop")
        regular = self.desktop / "not a desktop"
        regular.write_text("not a directory", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Desktop"):
            find_latest_snapshot(regular)

    def test_ambiguous_same_timestamp_is_rejected(self):
        self.copy(suffix="aaaaaaaa")
        self.copy(suffix="zzzzzzzz")
        with self.assertRaisesRegex(ValueError, "stesso istante"):
            find_latest_snapshot(self.desktop)

    def test_incomplete_newest_never_falls_back_to_older_copy(self):
        self.copy("20261029_131713")
        newest = self.copy()
        for missing in ("schema_collaudo.json", "snapshot.sqlite"):
            with self.subTest(missing=missing):
                original = (newest / missing).read_bytes()
                (newest / missing).unlink()
                with self.assertRaises(FileNotFoundError):
                    find_latest_snapshot(self.desktop)
                (newest / missing).write_bytes(original)

    def test_each_sidecar_rejects_newest_copy(self):
        self.copy("20261029_131713")
        newest = self.copy()
        for suffix in ("-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix):
                sidecar = newest / ("snapshot.sqlite" + suffix)
                sidecar.write_bytes(b"uncommitted sidecar")
                with self.assertRaisesRegex(ValueError, "aggiuntivi"):
                    find_latest_snapshot(self.desktop)
                sidecar.unlink()

    def test_broken_sidecar_link_is_also_rejected(self):
        newest = self.copy()
        link = newest / "snapshot.sqlite-wal"
        try:
            link.symlink_to(newest / "nonexistent")
        except OSError:
            self.skipTest("Symbolic links unavailable")
        with self.assertRaisesRegex(ValueError, "aggiuntivi"):
            find_latest_snapshot(self.desktop)

    def test_newest_folder_link_is_not_followed_or_ignored(self):
        old = self.copy("20261029_131713")
        link = self.desktop / "SU_Print_Collaudo_20261029_192453_abcdefgh"
        try:
            link.symlink_to(old, target_is_directory=True)
        except OSError:
            self.skipTest("Symbolic links unavailable")
        with self.assertRaisesRegex(ValueError, "collegamento"):
            find_latest_snapshot(self.desktop)

    def test_snapshot_or_schema_links_are_rejected(self):
        newest = self.copy()
        for name in ("snapshot.sqlite", "schema_collaudo.json"):
            with self.subTest(name=name):
                original = (newest / name).read_bytes()
                target = self.desktop / (name + ".target")
                target.write_bytes(original)
                (newest / name).unlink()
                try:
                    (newest / name).symlink_to(target)
                except OSError:
                    (newest / name).write_bytes(original)
                    self.skipTest("Symbolic links unavailable")
                with self.assertRaisesRegex(ValueError, "collegamento"):
                    find_latest_snapshot(self.desktop)
                (newest / name).unlink()
                (newest / name).write_bytes(original)

    def test_windows_reparse_attribute_is_rejected_without_following_target(self):
        newest = self.copy()
        original_lstat = Path.lstat

        def lstat(path, *args, **kwargs):
            value = original_lstat(path, *args, **kwargs)
            if path == newest:
                return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400)
            return value

        with patch.object(Path, "lstat", lstat):
            with self.assertRaisesRegex(ValueError, "collegamento"):
                find_latest_snapshot(self.desktop)

    def test_invalid_sqlite_header_is_rejected_without_fallback(self):
        self.copy("20261029_131713")
        newest = self.copy()
        (newest / "snapshot.sqlite").write_bytes(b"not SQLite")
        with self.assertRaisesRegex(ValueError, "SQLite"):
            find_latest_snapshot(self.desktop)

    def test_malformed_incomplete_or_oversized_schema_is_rejected(self):
        newest = self.copy()
        schema = newest / "schema_collaudo.json"
        malformed = (b"{", b"\xff", b"[]", b"{}",
                     b'{"format_version":true,"source_file":"a.db","objects":[]}',
                     b'{"format_version":1,"source_file":"","objects":[]}',
                     b'{"format_version":1,"source_file":"a.db","objects":{}}',
                     b'{"format_version":1,"format_version":1,"source_file":"a.db","objects":[]}',
                     b'{"format_version":1,"source_file":"a.db","objects":[NaN]}',
                     b" " * (1024 * 1024 + 1))
        for content in malformed:
            with self.subTest(content=content[:80]):
                schema.write_bytes(content)
                with self.assertRaisesRegex(ValueError, "struttura valido"):
                    find_latest_snapshot(self.desktop)

    def test_named_entry_or_snapshot_must_be_regular_expected_type(self):
        newest = self.copy()
        (newest / "snapshot.sqlite").unlink()
        (newest / "snapshot.sqlite").mkdir()
        with self.assertRaisesRegex(ValueError, "tipo previsto"):
            find_latest_snapshot(self.desktop)

    def test_newest_named_regular_file_does_not_make_an_older_copy_win(self):
        self.copy("20261029_131713")
        (self.desktop / "SU_Print_Collaudo_20261029_192453_abcdefgh").write_bytes(b"not a folder")
        with self.assertRaisesRegex(ValueError, "tipo previsto"):
            find_latest_snapshot(self.desktop)


if __name__ == "__main__":
    unittest.main()
