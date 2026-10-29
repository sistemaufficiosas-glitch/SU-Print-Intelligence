"""Ricerca delle copie dai file effettivi anziché dal nome della cartella."""

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from su_print_counters.file_discovery import SnapshotNotFound, discover_snapshot


class FileDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="su files discovery ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def copy(self, name, *, completed=1_000_000_000):
        folder = self.root / name
        folder.mkdir(parents=True)
        snapshot = folder / "snapshot.sqlite"
        snapshot.write_bytes(b"SQLite format 3\0synthetic fixture")
        schema = folder / "schema_collaudo.json"
        schema.write_text(json.dumps({"format_version": 1, "source_file": "synthetic.db", "objects": []}), encoding="utf-8")
        os.utime(snapshot, (completed - 1, completed - 1))
        os.utime(schema, (completed, completed))
        return snapshot

    def test_renamed_nested_and_mixed_case_folder_is_found_and_unchanged(self):
        snapshot = self.copy("Cartella Rinominata/SubFOLDER/Copia nuova x")
        digest = hashlib.sha256(snapshot.read_bytes()).digest()
        self.assertEqual(discover_snapshot([self.root]), snapshot)
        self.assertEqual(hashlib.sha256(snapshot.read_bytes()).digest(), digest)

    def test_random_suffix_length_and_timestamp_name_are_irrelevant(self):
        snapshot = self.copy("SU_Print_Collaudo_not-a-date_extra-long-suffix")
        self.assertEqual(discover_snapshot([self.root]), snapshot)

    def test_latest_completion_marker_wins_over_touched_old_database(self):
        old = self.copy("old", completed=1_000_000_000)
        new = self.copy("new", completed=1_000_000_100)
        os.utime(old, (2_000_000_000, 2_000_000_000))
        self.assertEqual(discover_snapshot([self.root]), new)

    def test_newer_snapshot_without_schema_blocks_older_completed_copy(self):
        self.copy("old", completed=1_000_000_000)
        new = self.copy("new", completed=1_000_000_100)
        (new.parent / "schema_collaudo.json").unlink()
        with self.assertRaises(FileNotFoundError) as error:
            discover_snapshot([self.root])
        self.assertNotIsInstance(error.exception, SnapshotNotFound)

    def test_newer_schema_without_snapshot_also_blocks_older_copy(self):
        self.copy("old", completed=1_000_000_000)
        new = self.copy("new", completed=1_000_000_100)
        new.unlink()
        with self.assertRaises(FileNotFoundError) as error:
            discover_snapshot([self.root])
        self.assertNotIsInstance(error.exception, SnapshotNotFound)

    def test_equal_completion_timestamps_are_ambiguous(self):
        self.copy("one")
        self.copy("two")
        with self.assertRaisesRegex(ValueError, "stessa data"):
            discover_snapshot([self.root])

    def test_root_itself_and_duplicate_overlapping_roots_are_supported(self):
        snapshot = self.copy("child")
        self.assertEqual(discover_snapshot([self.root, snapshot.parent, self.root]), snapshot)
        self.assertEqual(discover_snapshot([snapshot.parent]), snapshot)

    def test_overlapping_root_order_preserves_each_roots_full_depth_scope(self):
        snapshot = self.copy("nested/copy")
        nested = self.root / "nested"
        for roots in ([nested, self.root], [self.root, nested]):
            with self.subTest(roots=roots):
                self.assertEqual(discover_snapshot(roots, max_depth=1), snapshot)

    def test_directory_links_are_never_followed(self):
        outside = self.copy("outside")
        search = self.root / "search"
        search.mkdir()
        try:
            (search / "linked").symlink_to(outside.parent, target_is_directory=True)
        except OSError:
            self.skipTest("Symbolic links unavailable")
        with self.assertRaises(SnapshotNotFound):
            discover_snapshot([search])

    def test_newer_sidecar_is_rejected_instead_of_using_old_copy(self):
        self.copy("old", completed=1_000_000_000)
        new = self.copy("new", completed=1_000_000_100)
        Path(str(new) + "-wal").write_bytes(b"sidecar")
        with self.assertRaisesRegex(ValueError, "aggiuntivi"):
            discover_snapshot([self.root])

    def test_newer_malformed_schema_is_not_a_normal_not_found_case(self):
        self.copy("old", completed=1_000_000_000)
        new = self.copy("new", completed=1_000_000_100)
        schema = new.parent / "schema_collaudo.json"
        schema.write_text("{", encoding="utf-8")
        with self.assertRaises(ValueError):
            discover_snapshot([self.root])

    def test_schema_and_snapshot_links_are_rejected(self):
        snapshot = self.copy("linked-copy")
        for name in ("snapshot.sqlite", "schema_collaudo.json"):
            original = snapshot.parent / name
            target = self.root / (name + ".target")
            original.rename(target)
            try:
                original.symlink_to(target)
            except OSError:
                target.rename(original)
                self.skipTest("Symbolic links unavailable")
            with self.assertRaisesRegex(ValueError, "collegamento"):
                discover_snapshot([snapshot.parent])
            original.unlink()
            target.rename(original)

    def test_budget_exhaustion_never_returns_a_partial_result(self):
        self.copy("copy")
        for kwargs in ({"max_directories": 1}, {"max_entries": 1}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, "completa"):
                discover_snapshot([self.root], **kwargs)
        with patch("su_print_counters.file_discovery.time.monotonic", side_effect=[0, 10]):
            with self.assertRaisesRegex(ValueError, "incompleta"):
                discover_snapshot([self.root], timeout_seconds=1)

    def test_depth_limit_defines_scope_without_failing_on_unrelated_deep_folders(self):
        snapshot = self.copy("copy")
        (self.root / "unrelated" / "one" / "two" / "three").mkdir(parents=True)
        self.assertEqual(discover_snapshot([self.root], max_depth=1), snapshot)
        with self.assertRaises(SnapshotNotFound):
            discover_snapshot([self.root], max_depth=0)

    def test_already_found_copy_is_not_returned_when_remaining_scan_exceeds_budget(self):
        snapshot = self.copy("copy")
        (snapshot.parent / "still-to-scan").mkdir()
        with self.assertRaisesRegex(ValueError, "completa"):
            discover_snapshot([snapshot.parent], max_directories=1)

    def test_absence_is_the_only_not_found_outcome(self):
        with self.assertRaises(SnapshotNotFound):
            discover_snapshot([self.root / "missing"])
        with self.assertRaises(SnapshotNotFound):
            discover_snapshot([self.root])

    def test_ignored_dependency_directories_are_not_scanned(self):
        self.copy("node_modules/nested")
        with self.assertRaises(SnapshotNotFound):
            discover_snapshot([self.root])

    def test_scan_errors_are_not_reported_as_normal_absence(self):
        with patch("su_print_counters.file_discovery.os.scandir", side_effect=PermissionError("blocked")):
            with self.assertRaises(PermissionError):
                discover_snapshot([self.root])


if __name__ == "__main__":
    unittest.main()
