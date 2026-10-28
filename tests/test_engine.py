import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from su_print_counters import collect
from su_print_counters.engine import DEFAULT_MAPPING


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "snapshot.sqlite"
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.executescript('''
                CREATE TABLE printers(id INTEGER PRIMARY KEY, serial TEXT);
                CREATE TABLE remote_agents(id INTEGER PRIMARY KEY, printer_id INTEGER, serial TEXT);
                CREATE TABLE telemetry(id INTEGER PRIMARY KEY, agent_id INTEGER, serial TEXT,
                    observed_at TEXT, total_counter, is_valid);
                INSERT INTO printers VALUES(1, 'SN-001'), (2, 'NO-AGENT'), (3, 'NO-VALID');
                INSERT INTO remote_agents VALUES(10, 1, 'sn-001'), (30, 3, 'NO-VALID');
            ''')

    def reading(self, rid, total=100, day=1, agent=10, serial="SN-001", valid=1, when=None):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO telemetry VALUES(?,?,?,?,?,?)", (rid, agent, serial, when or f"2026-01-{day:02d}T10:00:00Z", total, valid))

    def run_collect(self):
        return collect(self.db, now=datetime(2026, 2, 1, tzinfo=timezone.utc))

    def codes(self, report):
        return {a["code"] for a in report["anomalies"]}

    def test_history_latest_and_exclusions(self):
        self.reading(2, 200, 2)
        self.reading(1)
        self.reading(3, agent=30, serial="NO-VALID", valid=0)
        report = self.run_collect()
        self.assertEqual([p["serial"] for p in report["printers"]], ["SN-001"])
        printer = report["printers"][0]
        self.assertEqual([r["reading_id"] for r in printer["history"]], [1, 2])
        self.assertEqual(printer["latest"]["total_counter"], 200)
        self.assertEqual(report["summary"]["excluded_printers"], 2)

    def test_discordant_telemetry_never_merged(self):
        self.reading(1, serial="NO-AGENT")
        report = self.run_collect()
        self.assertEqual(report["printers"], [])
        self.assertIn("SERIAL_MISMATCH", self.codes(report))

    def test_discordant_agent_excluded(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("UPDATE remote_agents SET serial='OTHER' WHERE id=10")
        self.reading(1)
        report = self.run_collect()
        self.assertEqual(report["printers"], [])
        self.assertIn("SERIAL_MISMATCH", self.codes(report))

    def test_duplicate_serials_excluded(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO printers VALUES(4,' sn-001 ')")
        self.reading(1)
        report = self.run_collect()
        self.assertEqual(report["printers"], [])
        self.assertIn("AMBIGUOUS_PRINTER_SERIAL", self.codes(report))

    def test_unregistered_agent(self):
        self.reading(1, agent=999)
        self.assertIn("UNTRUSTED_AGENT", self.codes(self.run_collect()))

    def test_invalid_values(self):
        cases = [{"total": -1}, {"total": 1.5}, {"total": "100"}, {"valid": 0}, {"valid": "true"}, {"when": "broken"}, {"when": "2026-01-01T00:00:00"}, {"when": "2099-01-01T00:00:00Z"}]
        for rid, kwargs in enumerate(cases):
            self.reading(rid, **kwargs)
        report = self.run_collect()
        self.assertEqual(report["printers"], [])
        self.assertEqual(report["summary"]["rejected_readings"], len(cases))

    def test_regression_does_not_replace_latest(self):
        self.reading(1, 100, 1)
        self.reading(2, 50, 2)
        self.reading(3, 110, 3)
        report = self.run_collect()
        self.assertEqual([r["total_counter"] for r in report["printers"][0]["history"]], [100, 110])
        self.assertIn("COUNTER_REGRESSION", self.codes(report))

    def test_conflict_excluded_and_coherent_duplicates_preserved(self):
        self.reading(1, 100, 1)
        self.reading(2, 100, 1)
        self.reading(3, 200, 2)
        self.reading(4, 201, 2)
        report = self.run_collect()
        self.assertEqual(len(report["printers"][0]["history"]), 2)
        self.assertIn("CONFLICTING_READING", self.codes(report))

    def test_timezone_order(self):
        self.reading(1, 100, when="2026-01-01T12:00:00+02:00")
        self.reading(2, 110, when="2026-01-01T11:00:00Z")
        self.assertEqual(self.run_collect()["printers"][0]["latest"]["reading_id"], 2)

    def test_database_and_directory_unchanged(self):
        self.reading(1)
        before = hashlib.sha256(self.db.read_bytes()).digest()
        files = sorted(Path(self.tmp.name).iterdir())
        self.run_collect()
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).digest())
        self.assertEqual(files, sorted(Path(self.tmp.name).iterdir()))

    def test_sidecar_rejected(self):
        Path(str(self.db) + "-wal").touch()
        with self.assertRaisesRegex(ValueError, "Snapshot non autonomo"):
            self.run_collect()

    def test_mapping_and_sql_identifier_guard(self):
        mapping = json.loads(json.dumps(DEFAULT_MAPPING))
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("ALTER TABLE telemetry RENAME TO samples")
        mapping["readings"]["table"] = "samples"
        path = Path(self.tmp.name) / "mapping.json"
        path.write_text(json.dumps(mapping))
        self.assertEqual(collect(self.db, path)["printers"], [])
        mapping["readings"]["table"] = 'samples; DROP TABLE printers'
        path.write_text(json.dumps(mapping))
        with self.assertRaises(ValueError):
            collect(self.db, path)

    def test_missing_database_not_created(self):
        path = Path(self.tmp.name) / "missing.sqlite"
        with self.assertRaises(FileNotFoundError):
            collect(path)
        self.assertFalse(path.exists())

    def test_cli_export_and_refuses_overwrite_source(self):
        self.reading(1)
        output = Path(self.tmp.name) / "report.json"
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        base = [sys.executable, "-m", "su_print_counters", str(self.db), "--output"]
        run = subprocess.run(base + [str(output)], env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(output.read_text())["summary"]["included_printers"], 1)
        before = self.db.read_bytes()
        run = subprocess.run(base + [str(self.db)], env=env, capture_output=True, text=True)
        self.assertEqual(run.returncode, 1)
        self.assertEqual(before, self.db.read_bytes())


class ConnectionLifetimeTests(unittest.TestCase):
    def test_fixture_and_engine_release_database_handles(self):
        # Mantenere riferimenti impedisce al GC di nascondere handle aperti su Linux.
        original_connect = sqlite3.connect
        opened = []

        def tracked_connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        fixture = CollectionTests("test_history_latest_and_exclusions")
        try:
            with patch("sqlite3.connect", side_effect=tracked_connect):
                fixture.setUp()
                fixture.test_history_latest_and_exclusions()
                # Anche un errore SQL deve rilasciare il file della sorgente.
                connection = original_connect(fixture.db)
                try:
                    connection.execute("ALTER TABLE telemetry RENAME TO other_samples")
                    connection.commit()
                finally:
                    connection.close()
                with self.assertRaises(sqlite3.OperationalError):
                    fixture.run_collect()
            self.assertTrue(opened)
            for connection in opened:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")
        finally:
            for connection in opened:
                connection.close()
            fixture.doCleanups()


if __name__ == "__main__":
    unittest.main()
