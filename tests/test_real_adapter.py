"""Collaudo del contratto letto dai sorgenti Agent, senza importare il gestionale."""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from su_print_counters.real_adapter import collect_real
from su_print_counters.snapshot import SnapshotCancelled


class RealAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="su real adapter test ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.database = self.root / "snapshot.sqlite"
        self.marker = self.root / "documento utente.txt"
        self.marker.write_text("da conservare", encoding="utf-8")
        self.now = datetime(2026, 2, 1, tzinfo=timezone.utc)
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.executescript('''
                CREATE TABLE printers(
                    id INTEGER PRIMARY KEY, serial_number TEXT, remote_agent_excluded INTEGER,
                    ip_address TEXT, total_counter INTEGER);
                CREATE TABLE su_remote_agents(
                    id INTEGER PRIMARY KEY, status TEXT, revoked_at TEXT, enrolled_at TEXT,
                    certificate_serial TEXT);
                CREATE TABLE su_remote_agent_printers(
                    agent_id INTEGER, printer_id INTEGER, allowed_address TEXT,
                    snmp_readonly INTEGER, web_proxy_allowed INTEGER,
                    PRIMARY KEY(agent_id,printer_id));
                CREATE TABLE su_remote_monitoring_exclusions(
                    printer_id INTEGER, agent_id INTEGER, serial_number TEXT,
                    allowed_address TEXT, snmp_readonly INTEGER, web_proxy_allowed INTEGER,
                    excluded_at TEXT, PRIMARY KEY(printer_id,agent_id));
                CREATE TABLE su_remote_agent_ignored_devices(
                    id INTEGER PRIMARY KEY, agent_id INTEGER, serial_number TEXT,
                    allowed_address TEXT, reason TEXT, created_at TEXT);
                CREATE TABLE machine_meter_readings(
                    id INTEGER PRIMARY KEY, machine_id INTEGER, read_at TEXT,
                    black_counter INTEGER, color_counter INTEGER, total_counter INTEGER,
                    source TEXT, agent_id INTEGER, raw_json TEXT);
                CREATE TABLE printer_monitor_cache(printer_id INTEGER, total_counter INTEGER);
                INSERT INTO printers VALUES(1,'SN-001',0,'192.168.1.40',999999);
                INSERT INTO printers VALUES(2,'SN-002',0,'192.168.1.41',999999);
                INSERT INTO su_remote_agents VALUES(10,'Attivo',NULL,NULL,'CERT-10');
                INSERT INTO su_remote_agents VALUES(20,'Disattivato',NULL,NULL,'CERT-20');
                INSERT INTO su_remote_agent_printers VALUES(10,1,'192.168.1.40',1,0);
            ''')
            self.create_log(conn)

    @staticmethod
    def create_log(conn, default="CURRENT_TIMESTAMP"):
        clause = " DEFAULT " + default if default else ""
        conn.execute("CREATE TABLE su_remote_telemetry_log("
                     "id INTEGER PRIMARY KEY, agent_id INTEGER, printer_id INTEGER, "
                     "target_ip TEXT, client_ip TEXT, received_at TEXT" + clause + ", "
                     "accepted INTEGER, payload_json TEXT)")

    def execute(self, sql, args=()):
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute(sql, args)

    def reading(self, rid, *, received=None, agent=10, printer=1, accepted=1, raw=None, **changes):
        payload = {
            "printer_id": printer, "serial_number": "SN-001" if printer == 1 else "SN-002",
            "snmp_ok": True, "collected_at": "2026-01-01T09:00:00",
            "total_counter_candidate": 100, "black_counter": 60, "color_counter": 40,
            "target_ip": "192.168.1.40", "warnings": ["DATI_LIBERI_NON_ESPORTARE"],
        }
        payload.update(changes)
        self.execute("INSERT INTO su_remote_telemetry_log VALUES(?,?,?,?,?,?,?,?)",
                     (rid, agent, printer, "192.168.1.40", "192.168.1.99",
                      received or f"2026-01-01T10:00:{rid:02d}Z", accepted,
                      json.dumps(payload) if raw is None else raw))

    def collect(self):
        return collect_real(self.database, now=self.now)

    @staticmethod
    def codes(report):
        return {issue["code"] for issue in report["anomalies"]}

    def test_valid_log_reading_uses_declared_fields_and_requires_review(self):
        self.reading(1, serial_number=" sn-001 ")
        report = self.collect()
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertEqual([p["serial"] for p in report["printers"]], ["SN-001"])
        latest = report["printers"][0]["latest"]
        self.assertEqual(latest["total_counter"], 100)
        self.assertEqual(latest["black_counter"], 60)
        self.assertEqual(latest["color_counter"], 40)
        self.assertTrue(latest["currently_authorized"])
        self.assertFalse(latest["collected_at_has_timezone"])
        self.assertEqual(latest["collected_at"], "2026-01-01T09:00:00")
        self.assertEqual(report["summary"]["source_printers"], 2)
        self.assertEqual(report["summary"]["included_printers"], 1)
        self.assertNotIn("DATI_LIBERI_NON_ESPORTARE", json.dumps(report))

    def test_accepted_flag_does_not_replace_snmp_and_counter_checks(self):
        for rid, flag in enumerate((0, 2, "true", None), 1):
            self.reading(rid, accepted=flag)
        self.reading(5, snmp_ok=False)
        self.reading(6, snmp_ok="true")
        self.reading(7, snmp_ok=1)
        self.reading(8, total_counter_candidate=None, black_counter=None, color_counter=None)
        report = self.collect()
        self.assertEqual(report["printers"], [])
        self.assertEqual(report["summary"]["rejected_readings"], 8)

    def test_serial_mismatch_is_not_reattached_to_other_printer(self):
        self.execute("UPDATE printers SET serial_number='SN001',ip_address='192.168.1.40' WHERE id=2")
        self.execute("INSERT INTO su_remote_agent_printers VALUES(10,2,'192.168.1.40',1,0)")
        self.reading(1, serial_number="SN001")
        report = self.collect()
        self.assertEqual(report["printers"], [])
        self.assertIn("SERIAL_MISMATCH", self.codes(report))

    def test_duplicate_and_missing_inventory_serials_are_excluded(self):
        self.execute("INSERT INTO printers VALUES(3,' sn-001 ',0,'192.168.1.42',0)")
        self.execute("UPDATE printers SET serial_number=' ' WHERE id=2")
        self.execute("INSERT INTO su_remote_agent_printers VALUES(10,2,'192.168.1.41',1,0)")
        self.reading(1)
        self.reading(2, printer=2, serial_number="SN-002")
        report = self.collect()
        self.assertEqual(report["printers"], [])
        self.assertEqual(report["summary"]["excluded_printers"], 3)

    def test_placeholder_serials_are_rejected_without_normalizing_valid_punctuation(self):
        placeholders = ("N/D", " nd ", "Non Disponibile", "-- / ??", "0-00", "000 00", "0000")
        for value in placeholders:
            for location in ("inventory", "payload"):
                with self.subTest(value=value, location=location):
                    self.execute("DELETE FROM su_remote_telemetry_log")
                    self.execute("UPDATE printers SET serial_number=? WHERE id=1",
                                 (value if location == "inventory" else "SN-001",))
                    self.reading(1, serial_number=value)
                    self.assertEqual(self.collect()["printers"], [])
        self.execute("DELETE FROM su_remote_telemetry_log")
        self.execute("UPDATE printers SET serial_number='00-1A' WHERE id=1")
        self.execute("UPDATE printers SET serial_number='001A' WHERE id=2")
        self.execute("INSERT INTO su_remote_agent_printers VALUES(10,2,'192.168.1.41',1,0)")
        self.reading(1, serial_number=" 00-1a ")
        self.reading(2, printer=2, serial_number="001A")
        self.reading(3, serial_number="001A")
        report = self.collect()
        self.assertEqual([p["serial"] for p in report["printers"]], ["00-1A", "001A"])
        self.assertEqual([[row["reading_id"] for row in p["history"]]
                          for p in report["printers"]], [[1], [2]])
        self.assertIn("SERIAL_MISMATCH", self.codes(report))

    def test_payload_printer_identifier_must_be_exact_integer_matching_log(self):
        for rid, value in enumerate((True, "1", 2, None), 1):
            self.reading(rid, raw=json.dumps({
                "printer_id": value, "serial_number": "SN-001", "snmp_ok": True,
                "collected_at": "2026-01-01T09:00:00", "total_counter_candidate": 100,
            }))
        self.assertEqual(self.collect()["printers"], [])

    def test_unregistered_agent_does_not_gain_provenance_from_accepted_flag(self):
        self.reading(1, agent=999)
        report = self.collect()
        self.assertEqual(report["printers"], [])
        self.assertEqual(report["summary"]["validated_readings"], 0)

    def test_manual_import_and_caches_are_not_candidates_or_baselines(self):
        for rid, source in enumerate(("Manuale", "Importazione", "Remote Agent"), 1):
            self.execute("INSERT INTO machine_meter_readings VALUES(?,?,?,?,?,?,?,?,?)",
                         (rid, 1, "2026-01-30T10:00:00Z", 800000, 900000, 999999,
                          source, 10, "{}"))
        self.execute("INSERT INTO printer_monitor_cache VALUES(1,999999)")
        self.reading(1)
        report = self.collect()
        self.assertEqual(report["summary"]["source_telemetry"], 1)
        self.assertEqual(report["printers"][0]["latest"]["total_counter"], 100)

    def test_strict_counters_reject_coercions_negative_and_overflow(self):
        rid = 0
        for key in ("total_counter_candidate", "black_counter", "color_counter"):
            for value in (-1, True, 1.5, "100", 2147483648):
                rid += 1
                self.reading(rid, **{key: value})
        report = self.collect()
        self.assertEqual(report["printers"], [])
        self.assertEqual(report["summary"]["rejected_readings"], rid)

    def test_total_below_component_is_invalid_and_absent_total_is_not_fabricated(self):
        self.reading(1, total_counter_candidate=30)
        self.reading(2, total_counter_candidate=None)
        report = self.collect()
        self.assertEqual([r["reading_id"] for r in report["printers"][0]["history"]], [2])
        self.assertIsNone(report["printers"][0]["latest"]["total_counter"])
        self.assertIsNone(report["printers"][0]["latest_by_counter"]["total_counter"])

    def test_partial_readings_preserve_baseline_for_every_counter(self):
        self.reading(1, black_counter=100, color_counter=40, total_counter_candidate=140)
        self.reading(2, black_counter=None, color_counter=45, total_counter_candidate=None)
        self.reading(3, black_counter=90, color_counter=None, total_counter_candidate=None)
        self.reading(4, black_counter=110, color_counter=None, total_counter_candidate=None)
        report = self.collect()
        printer = report["printers"][0]
        self.assertEqual([r["reading_id"] for r in printer["history"]], [1, 2, 4])
        self.assertEqual(printer["latest"]["reading_id"], 4)
        self.assertEqual(printer["latest_by_counter"]["black_counter"]["reading_id"], 4)
        self.assertEqual(printer["latest_by_counter"]["color_counter"]["reading_id"], 2)
        self.assertEqual(printer["latest_by_counter"]["total_counter"]["reading_id"], 1)
        self.assertIn("COUNTER_REGRESSION", self.codes(report))

    def test_conflicting_same_receipt_time_is_quarantined(self):
        self.reading(1, received="2026-01-01T10:00:00Z")
        self.reading(2, received="2026-01-01T10:00:00Z", total_counter_candidate=101)
        self.reading(3, received="2026-01-02T10:00:00Z", total_counter_candidate=110)
        report = self.collect()
        self.assertEqual([r["reading_id"] for r in report["printers"][0]["history"]], [3])
        self.assertIn("CONFLICTING_READING", self.codes(report))
        self.assertEqual(report["summary"]["rejected_readings"], 2)

    def test_coherent_duplicate_receipts_keep_both_provenances(self):
        self.reading(1, received="2026-01-01T10:00:00Z")
        self.reading(2, received="2026-01-01T10:00:00Z")
        printer = self.collect()["printers"][0]
        self.assertEqual([r["reading_id"] for r in printer["history"]], [1, 2])
        self.assertEqual(printer["latest"]["reading_id"], 2)

    def test_received_utc_orders_history_not_collected_local_string(self):
        self.reading(1, received="2026-01-01T12:00:00+02:00",
                     collected_at="2026-01-01T09:00:00")
        self.reading(2, received="2026-01-01T11:00:00Z",
                     collected_at="2026-01-01T08:00:00", total_counter_candidate=110)
        printer = self.collect()["printers"][0]
        self.assertEqual([r["reading_id"] for r in printer["history"]], [1, 2])
        self.assertEqual(printer["latest"]["reading_id"], 2)
        self.assertEqual(printer["history"][0]["received_at"], "2026-01-01T10:00:00+00:00")

    def test_naive_received_time_needs_confirmed_sqlite_utc_default(self):
        self.reading(1, received="2026-01-01 10:00:00")
        self.assertEqual(self.collect()["printers"][0]["latest"]["received_at"],
                         "2026-01-01T10:00:00+00:00")
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("DROP TABLE su_remote_telemetry_log")
            self.create_log(conn, default=None)
        self.reading(1, received="2026-01-01 10:00:00")
        self.assertEqual(self.collect()["printers"], [])
        self.execute("DELETE FROM su_remote_telemetry_log")
        self.reading(2, received="2026-01-01T10:00:00Z")
        self.assertEqual(len(self.collect()["printers"]), 1)

    def test_trigger_prevents_assuming_naive_receipt_is_sqlite_utc(self):
        self.execute("CREATE TRIGGER receipt_hook AFTER INSERT ON su_remote_telemetry_log BEGIN SELECT 1; END")
        self.reading(1, received="2026-01-01 10:00:00")
        self.assertEqual(self.collect()["printers"], [])

    def test_missing_date_only_malformed_and_future_timestamps_are_rejected(self):
        cases = (
            {"collected_at": ""}, {"collected_at": "broken"},
            {"collected_at": "2026-01-01"}, {"received": "broken"},
            {"received": "2026-01-01"}, {"received": "2099-01-01T00:00:00Z"},
        )
        for rid, options in enumerate(cases, 1):
            self.reading(rid, **options)
        report = self.collect()
        self.assertEqual(report["printers"], [])
        self.assertEqual(report["summary"]["rejected_readings"], len(cases))

    def test_historical_acceptance_survives_current_agent_and_binding_changes(self):
        self.reading(1, agent=20, received="2026-01-01T10:00:00Z")
        self.reading(2, agent=10, received="2026-01-02T10:00:00Z", total_counter_candidate=110)
        self.reading(3, agent=20, received="2026-01-03T10:00:00Z", total_counter_candidate=120)
        report = self.collect()
        printer = report["printers"][0]
        self.assertEqual([r["reading_id"] for r in printer["history"]], [1, 2, 3])
        self.assertEqual([r["currently_authorized"] for r in printer["history"]], [False, True, False])
        self.assertEqual(printer["latest"]["reading_id"], 2)
        self.assertEqual(printer["latest_by_counter"]["total_counter"]["reading_id"], 2)
        self.assertEqual(report["summary"]["historical_readings"], 2)

    def test_no_current_agent_excludes_machine_but_preserves_valid_archive(self):
        self.execute("DELETE FROM su_remote_agent_printers")
        self.reading(1, agent=20)
        report = self.collect()
        self.assertEqual(report["printers"], [])
        archived = next(p for p in report["archive"] if p["printer_id"] == 1)
        self.assertEqual([r["reading_id"] for r in archived["history"]], [1])
        self.assertTrue(archived["reason"])

    def test_many_to_many_agent_bindings_do_not_duplicate_machine_identity(self):
        self.execute("UPDATE su_remote_agents SET status=' Attivo ' WHERE id=20")
        self.execute("INSERT INTO su_remote_agent_printers VALUES(20,1,'192.168.1.40',1,0)")
        self.execute("INSERT INTO su_remote_agent_printers VALUES(10,2,'192.168.1.41',1,0)")
        self.reading(1)
        self.reading(2, agent=20, total_counter_candidate=110)
        self.reading(3, printer=2)
        report = self.collect()
        self.assertEqual([p["serial"] for p in report["printers"]], ["SN-001", "SN-002"])
        self.assertEqual(report["summary"]["included_printers"], 2)

    def test_current_status_permission_revocation_and_exclusions_are_respected(self):
        self.reading(1)
        scenarios = (
            ("UPDATE su_remote_agents SET status='active' WHERE id=10", ()),
            ("UPDATE su_remote_agents SET revoked_at='2026-01-20T00:00:00Z' WHERE id=10", ()),
            ("UPDATE su_remote_agent_printers SET snmp_readonly=0", ()),
            ("UPDATE printers SET remote_agent_excluded=1 WHERE id=1", ()),
            ("INSERT INTO su_remote_monitoring_exclusions VALUES(1,10,'SN-001','',1,0,'2026-01-20')", ()),
            ("INSERT INTO su_remote_agent_ignored_devices VALUES(1,10,'sn-001','','scelta utente','2026-01-20')", ()),
        )
        for sql, args in scenarios:
            with self.subTest(sql=sql):
                self.execute("UPDATE su_remote_agents SET status='Attivo',revoked_at=NULL WHERE id=10")
                self.execute("UPDATE su_remote_agent_printers SET snmp_readonly=1")
                self.execute("UPDATE printers SET remote_agent_excluded=0")
                self.execute("DELETE FROM su_remote_monitoring_exclusions")
                self.execute("DELETE FROM su_remote_agent_ignored_devices")
                self.execute(sql, args)
                report = self.collect()
                self.assertEqual(report["printers"], [])
                archived = next(p for p in report["archive"] if p["printer_id"] == 1)
                self.assertEqual(len(archived["history"]), 1)
                self.assertFalse(archived["history"][0]["currently_authorized"])

    def test_malformed_duplicate_or_nonstandard_json_is_rejected(self):
        cases = ("{RAW_SECRET", "[]", "null", '{"printer_id":1,"printer_id":2}',
                 '{"total_counter_candidate":NaN}', '{"total_counter_candidate":Infinity}')
        for rid, raw in enumerate(cases, 1):
            self.reading(rid, raw=raw)
        report = self.collect()
        self.assertEqual(report["printers"], [])
        self.assertEqual(report["summary"]["rejected_readings"], len(cases))
        self.assertNotIn("RAW_SECRET", json.dumps(report))

    def test_database_and_directory_remain_unchanged(self):
        self.reading(1)
        before = hashlib.sha256(self.database.read_bytes()).digest()
        files = set(self.root.iterdir())
        self.collect()
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).digest(), before)
        self.assertEqual(set(self.root.iterdir()), files)
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "da conservare")

    def test_cli_su_print_profile_exports_checked_ledger_without_database_changes(self):
        self.reading(1)
        before = hashlib.sha256(self.database.read_bytes()).digest()
        output = self.root / "report.json"
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        base = [sys.executable, "-m", "su_print_counters", "--schema", "su-print", str(self.database)]
        result = subprocess.run(base + ["--output", str(output)], env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "REVIEW_REQUIRED")
        self.assertEqual(report["provenance"]["source"], "su_remote_telemetry_log")
        self.assertEqual(report["printers"][0]["latest"]["total_counter"], 100)
        self.assertEqual(report["printers"][0]["latest"]["reading_id"], 1)
        mapping = self.root / "mapping.json"
        mapping.write_text("{}", encoding="utf-8")
        unexpected = self.root / "unexpected.json"
        result = subprocess.run(base + ["--mapping", str(mapping), "--output", str(unexpected)],
                                env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("--mapping", result.stderr)
        self.assertFalse(unexpected.exists())
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).digest(), before)

    def test_source_is_read_only_and_handles_close_while_references_are_retained(self):
        self.reading(1)
        original_connect = sqlite3.connect
        opened = []
        observations = []

        class ProbeConnection(sqlite3.Connection):
            checked = False

            def close(connection):
                if not connection.checked:
                    connection.checked = True
                    query_only = connection.execute("PRAGMA query_only").fetchone()[0]
                    try:
                        connection.execute("INSERT INTO printers(id,serial_number) VALUES(99,'WRITE')")
                    except sqlite3.OperationalError as error:
                        observations.append((query_only, error.sqlite_errorcode & 255))
                    else:
                        observations.append((query_only, None))
                super().close()

        def track(*args, **kwargs):
            query = parse_qs(urlsplit(str(args[0])).query)
            self.assertEqual(query.get("mode"), ["ro"])
            self.assertEqual(query.get("immutable"), ["1"])
            self.assertTrue(kwargs.get("uri"))
            kwargs["factory"] = ProbeConnection
            connection = original_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        try:
            with patch("su_print_counters.real_adapter.sqlite3.connect", side_effect=track):
                self.collect()
            self.assertTrue(opened)
            self.assertTrue(observations)
            self.assertTrue(all(pair == (1, sqlite3.SQLITE_READONLY) for pair in observations))
            for connection in opened:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")
        finally:
            for connection in opened:
                connection.close()

    def test_active_filename_and_sidecars_are_rejected_without_changes(self):
        active = self.root / "su_print_intelligence.db"
        shutil.copyfile(self.database, active)
        with self.assertRaises(ValueError):
            collect_real(active, now=self.now)
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(self.database) + suffix)
            sidecar.touch()
            try:
                with self.assertRaises(ValueError):
                    self.collect()
            finally:
                sidecar.unlink()

    def test_cancellation_and_timeout_do_not_write_or_create_outputs(self):
        self.reading(1)
        before = self.database.read_bytes()
        files = set(self.root.iterdir())
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(SnapshotCancelled):
            collect_real(self.database, now=self.now, cancel=cancel)
        clock_calls = []

        def clock():
            clock_calls.append(True)
            return 0.0 if len(clock_calls) == 1 else 1000.0

        with patch("su_print_counters.real_adapter.time.monotonic", side_effect=clock):
            with self.assertRaises(TimeoutError):
                collect_real(self.database, now=self.now, timeout_seconds=1)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(set(self.root.iterdir()), files)


if __name__ == "__main__":
    unittest.main()
