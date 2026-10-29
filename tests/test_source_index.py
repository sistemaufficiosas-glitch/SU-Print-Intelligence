"""Verifica dei limiti e dell'assenza di contenuti nell'indice dei sorgenti."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from su_print_counters.source_index import MAX_SOURCE_BYTES, index_sources


class SourceIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="su source index ")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "app"
        self.root.mkdir()

    def source(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_exports_only_relative_paths_and_known_markers_without_executing(self):
        side_effect = self.base / "executed.txt"
        self.source("remote/ingest.py", f'''from pathlib import Path
Path({str(side_effect)!r}).write_text("ESEGUITO")
SECRET = "CREDENZIALE_PRIVATA_DA_NON_ESPORTARE"
QUERY = "INSERT INTO su_remote_telemetry_log VALUES ('DATI_PRIVATI')"
RELATION = "su_remote_agent_printers"
''')
        self.source("readings.py", 'TABLE = "machine_meter_readings"')
        self.source("other.py", 'payload_json = "DATI_PRIVATI"')
        self.source("notes.txt", 'TABLE = "su_remote_telemetry_log"')
        report = index_sources(self.root)
        self.assertTrue(report["available"])
        self.assertTrue(report["complete"])
        self.assertEqual(report["scanned_files"], 3)
        self.assertEqual(report["candidates"], [
            {"relative_path": "readings.py", "markers": ["machine_meter_readings"]},
            {"relative_path": "remote/ingest.py", "markers": ["su_remote_telemetry_log", "su_remote_agent_printers"]},
        ])
        exported = json.dumps(report)
        for forbidden in (str(self.root), "CREDENZIALE_PRIVATA", "DATI_PRIVATI", "INSERT INTO", "write_text"):
            self.assertNotIn(forbidden, exported)
        self.assertFalse(side_effect.exists())

    def test_missing_root_and_file_root_are_unavailable(self):
        plain_file = self.source("file.py", "pass")
        for root in (self.base / "missing", plain_file):
            with self.subTest(root=root):
                report = index_sources(root)
                self.assertFalse(report["available"])
                self.assertFalse(report["complete"])
                self.assertEqual(report["scanned_files"], 0)
                self.assertEqual(report["candidates"], [])

    def test_excluded_caches_and_dependencies_are_outside_search_scope(self):
        for directory in ("__pycache__", "cache", ".venv", "Vendor", "node_modules", "third_party"):
            self.source(directory + "/ignored.py", 'TABLE="machine_meter_readings"')
        self.source("actual.py", 'TABLE="su_remote_telemetry_log"')
        report = index_sources(self.root)
        self.assertTrue(report["complete"])
        self.assertEqual(report["scanned_files"], 1)
        self.assertEqual([item["relative_path"] for item in report["candidates"]], ["actual.py"])

    def test_external_symlinks_are_not_followed(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "private.py").write_text('TABLE="machine_meter_readings"', encoding="utf-8")
        try:
            (self.root / "external").symlink_to(outside, target_is_directory=True)
            (self.root / "external_file.py").symlink_to(outside / "private.py")
        except OSError as error:
            self.skipTest(f"Creazione symlink non disponibile: {error}")
        report = index_sources(self.root)
        self.assertTrue(report["available"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["scanned_files"], 0)
        self.assertEqual(report["candidates"], [])

    def test_internal_symlink_cycle_does_not_duplicate_or_loop(self):
        self.source("remote/real.py", 'TABLE="su_remote_telemetry_log"')
        try:
            (self.root / "remote" / "cycle").symlink_to(self.root, target_is_directory=True)
            (self.root / "alias.py").symlink_to(self.root / "remote/real.py")
        except OSError as error:
            self.skipTest(f"Creazione symlink non disponibile: {error}")
        report = index_sources(self.root)
        self.assertTrue(report["complete"])
        self.assertEqual(report["scanned_files"], 1)
        self.assertEqual(len(report["candidates"]), 1)
        self.assertEqual(report["candidates"][0]["relative_path"], "remote/real.py")

    def test_file_limit_reports_partial_search(self):
        for number in range(3):
            self.source(f"module{number}.py", 'TABLE="su_remote_telemetry_log"')
        report = index_sources(self.root, max_files=1)
        self.assertTrue(report["available"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["scanned_files"], 1)
        self.assertEqual(len(report["candidates"]), 1)

    def test_alias_does_not_bypass_excluded_directories_or_source_extension(self):
        vendor = self.source("vendor/private.py", 'TABLE="su_remote_telemetry_log"')
        config = self.source("private.env", 'TABLE="machine_meter_readings"')
        try:
            (self.root / "vendor_alias.py").symlink_to(vendor)
            (self.root / "config_alias.py").symlink_to(config)
        except OSError as error:
            self.skipTest(f"Creazione symlink non disponibile: {error}")
        report = index_sources(self.root)
        self.assertTrue(report["complete"])
        self.assertEqual(report["scanned_files"], 0)
        self.assertEqual(report["candidates"], [])

    def test_oversized_file_is_not_scanned(self):
        oversized = self.root / "large.py"
        oversized.write_bytes(b"x" * (MAX_SOURCE_BYTES + 1) + b"machine_meter_readings")
        report = index_sources(self.root)
        self.assertFalse(report["complete"])
        self.assertEqual(report["scanned_files"], 0)
        self.assertEqual(report["candidates"], [])

    def test_candidate_limit_is_explicit(self):
        for number in range(25):
            self.source(f"module{number}.py", 'TABLE="su_remote_telemetry_log"')
        report = index_sources(self.root)
        self.assertFalse(report["complete"])
        self.assertEqual(report["scanned_files"], 25)
        self.assertEqual(len(report["candidates"]), 20)
        self.assertEqual(report["omitted_candidates"], 5)

    def test_timeout_reports_partial_search_without_waiting(self):
        self.source("module.py", 'TABLE="su_remote_telemetry_log"')
        with patch("su_print_counters.source_index.time.monotonic", side_effect=[0.0, 2.0]):
            report = index_sources(self.root, timeout_seconds=1.0)
        self.assertTrue(report["available"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["scanned_files"], 0)

    def test_unreadable_source_is_reported_incomplete(self):
        blocked = self.source("blocked.py", 'TABLE="su_remote_telemetry_log"')
        self.source("readable.py", 'TABLE="machine_meter_readings"')
        original_open = Path.open

        def fail_for_blocked(path, *args, **kwargs):
            if path == blocked:
                raise PermissionError("Accesso negato")
            return original_open(path, *args, **kwargs)

        with patch("su_print_counters.source_index.Path.open", new=fail_for_blocked):
            report = index_sources(self.root)
        self.assertTrue(report["available"])
        self.assertFalse(report["complete"])
        self.assertEqual(report["scanned_files"], 1)
        self.assertEqual([item["relative_path"] for item in report["candidates"]], ["readable.py"])

    def test_invalid_limits_do_not_claim_complete_search(self):
        for kwargs in ({"max_files": -1}, {"max_files": True}, {"timeout_seconds": 0}, {"timeout_seconds": float("nan")}):
            with self.subTest(kwargs=kwargs):
                report = index_sources(self.root, **kwargs)
                self.assertTrue(report["available"])
                self.assertFalse(report["complete"])
                self.assertEqual(report["scanned_files"], 0)


if __name__ == "__main__":
    unittest.main()
