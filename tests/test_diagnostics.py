"""Controlli della diagnostica locale: sola lettura, privacy e limiti dichiarati."""

import hashlib
import json
import shutil
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from su_print_counters.diagnostics import diagnose_snapshot
from su_print_counters.snapshot import SnapshotCancelled


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="su diagnostic test ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.database = self.root / "snapshot.sqlite"
        self.marker = self.root / "documento utente.txt"
        self.marker.write_text("da conservare", encoding="utf-8")
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.executescript('''
                CREATE TABLE printers(id INTEGER PRIMARY KEY, serial_number TEXT);
                CREATE TABLE su_remote_agents(
                    id INTEGER PRIMARY KEY, status TEXT, revoked_at TEXT,
                    certificate_serial TEXT, auth_key_protected TEXT);
                CREATE TABLE su_remote_agent_printers(
                    agent_id INTEGER, printer_id INTEGER,
                    PRIMARY KEY(agent_id, printer_id));
                CREATE TABLE su_remote_telemetry_log(
                    id INTEGER PRIMARY KEY, agent_id INTEGER, printer_id INTEGER,
                    accepted INTEGER, received_at TEXT, payload_json TEXT);
                CREATE TABLE machine_meter_readings(
                    id INTEGER PRIMARY KEY, machine_id INTEGER, agent_id INTEGER,
                    source TEXT, read_at TEXT, raw_json TEXT);
                INSERT INTO printers VALUES (1, 'SERIALE_PRIVATO_12345');
                INSERT INTO printers VALUES (2, 'ALTRO_SERIALE_PRIVATO');
                INSERT INTO su_remote_agents VALUES
                    (10, 'active', NULL, 'CERTIFICATO_PRIVATO_67890', 'SEGRETO_AUTENTICAZIONE');
                INSERT INTO su_remote_agents VALUES
                    (20, 'STATO_LIBERO_PRIVATO', NULL, 'ALTRO_CERTIFICATO', 'ALTRO_SEGRETO');
                INSERT INTO su_remote_agent_printers VALUES (10, 1), (10, 2), (20, 1);
            ''')
            payload = {
                "serial_number": "SERIALE_PRIVATO_12345",
                "total_counter": 987654321,
                "observed_at": "2026-10-29T10:30:00+01:00",
                "status": {"success": True},
                "customer_name": "CLIENTE_PRIVATO_NON_ESPORTARE",
                "certificate_serial": "CERTIFICATO_PRIVATO_67890",
                "auth_key": "SEGRETO_AUTENTICAZIONE",
                "192.168.1.99": {"serial_number": "SERIALE_DINAMICO_PRIVATO"},
                "CHIAVE_DINAMICA_PRIVATA": {"value": "VALORE_DINAMICO_PRIVATO"},
            }
            conn.execute("INSERT INTO su_remote_telemetry_log VALUES(?,?,?,?,?,?)",
                         (1, 10, 1, 1, "2026-10-29 10:31:00", json.dumps(payload)))
            conn.execute("INSERT INTO su_remote_telemetry_log VALUES(?,?,?,?,?,?)",
                         (2, 10, 1, 0, "2026-10-29T10:31:00Z",
                          json.dumps({"error": "DETTAGLIO_ERRORE_PRIVATO"})))
            conn.execute("INSERT INTO su_remote_telemetry_log VALUES(?,?,?,?,?,?)",
                         (3, 10, 1, 2, "2026-10-29T10:31:00Z", "{}"))
            conn.execute("INSERT INTO machine_meter_readings VALUES(?,?,?,?,?,?)",
                         (1, 1, 10, "remote_agent", "2026-10-29 10:31:00", "{}"))
            conn.execute("INSERT INTO machine_meter_readings VALUES(?,?,?,?,?,?)",
                         (2, 1, None, "FONTE_LIBERA_PRIVATA", "2026-10-29 10:32:00", "{}"))

    def test_read_only_database_and_directory_remain_unchanged(self):
        before = hashlib.sha256(self.database.read_bytes()).digest()
        files = set(self.root.iterdir())
        report = diagnose_snapshot(self.database)
        self.assertEqual(report["format_version"], 1)
        self.assertEqual(report["status"], "SEMANTICS_UNCONFIRMED")
        self.assertEqual(hashlib.sha256(self.database.read_bytes()).digest(), before)
        self.assertEqual(set(self.root.iterdir()), files)
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "da conservare")

    def test_complete_schema_does_not_certify_readings_or_agent_identity(self):
        report = diagnose_snapshot(self.database)
        self.assertEqual(report["status"], "SEMANTICS_UNCONFIRMED")
        self.assertEqual(report["table_rows"]["su_remote_agent_printers"], 3)
        self.assertEqual(report["table_rows"]["printers"], 2)
        # Una matrice molti-a-molti non deve essere spacciata per una matricola Agent.
        self.assertNotIn("included_printers", report)
        self.assertNotIn("latest", report)

    def test_no_records_free_values_or_dynamic_json_keys_are_exported(self):
        report = diagnose_snapshot(self.database)
        encoded = json.dumps(report, ensure_ascii=False)
        for private in (
            "SERIALE_PRIVATO_12345", "ALTRO_SERIALE_PRIVATO", "987654321",
            "2026-10-29T10:30:00+01:00", "CLIENTE_PRIVATO_NON_ESPORTARE",
            "CERTIFICATO_PRIVATO_67890", "SEGRETO_AUTENTICAZIONE", "192.168.1.99",
            "SERIALE_DINAMICO_PRIVATO", "CHIAVE_DINAMICA_PRIVATA",
            "VALORE_DINAMICO_PRIVATO", "DETTAGLIO_ERRORE_PRIVATO",
            "FONTE_LIBERA_PRIVATA", "STATO_LIBERO_PRIVATO",
        ):
            with self.subTest(private=private):
                self.assertNotIn(private, encoded)
        accepted = report["json_profiles"]["accepted_1"]
        self.assertIn("$.serial_number", accepted["paths"])
        self.assertGreater(accepted["redacted_key_occurrences"], 0)
        self.assertNotIn("$.certificate_serial", accepted["paths"])

    def test_accepted_rejected_and_other_flags_are_profiled_separately(self):
        report = diagnose_snapshot(self.database)
        profiles = report["json_profiles"]
        self.assertEqual(set(profiles), {"accepted_1", "accepted_0", "other_accepted", "meter_readings"})
        self.assertEqual(profiles["accepted_1"]["sampled_rows"], 1)
        self.assertEqual(profiles["accepted_0"]["sampled_rows"], 1)
        self.assertEqual(profiles["other_accepted"]["sampled_rows"], 1)
        self.assertEqual(profiles["meter_readings"]["sampled_rows"], 2)
        self.assertIn("$.serial_number", profiles["accepted_1"]["paths"])
        self.assertNotIn("$.serial_number", profiles["accepted_0"]["paths"])
        self.assertEqual(report["status"], "SEMANTICS_UNCONFIRMED")

    def test_invalid_json_is_counted_without_echoing_its_contents(self):
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("INSERT INTO su_remote_telemetry_log VALUES(?,?,?,?,?,?)",
                         (4, 10, 1, 1, "2026-10-29 11:00:00", "{SEGRETO_JSON_ROTTO"))
        report = diagnose_snapshot(self.database)
        profile = report["json_profiles"]["accepted_1"]
        self.assertEqual(profile["sampled_rows"], 2)
        self.assertEqual(profile["invalid_json"], 1)
        self.assertNotIn("SEGRETO_JSON_ROTTO", json.dumps(report))

    def test_duplicate_keys_and_nonstandard_json_numbers_are_rejected(self):
        invalid_payloads = [
            '{"serial_number":"PRIMA_MATRICOLA_PRIVATA","serial_number":"SECONDA_MATRICOLA_PRIVATA"}',
            '{"data":{"total_counter":10,"total_counter":20}}',
            '{"total_counter":NaN}',
            '{"total_counter":Infinity}',
            '{"total_counter":-Infinity}',
        ]
        with closing(sqlite3.connect(self.database)) as conn, conn:
            for rid, payload in enumerate(invalid_payloads, 4):
                conn.execute("INSERT INTO su_remote_telemetry_log VALUES(?,?,?,?,?,?)",
                             (rid, 10, 1, 1, "2026-10-29 11:00:00", payload))
        report = diagnose_snapshot(self.database)
        profile = report["json_profiles"]["accepted_1"]
        self.assertEqual(profile["sampled_rows"], len(invalid_payloads) + 1)
        self.assertEqual(profile["invalid_json"], len(invalid_payloads))
        encoded = json.dumps(report)
        self.assertNotIn("PRIMA_MATRICOLA_PRIVATA", encoded)
        self.assertNotIn("SECONDA_MATRICOLA_PRIVATA", encoded)
        self.assertEqual(report["status"], "SEMANTICS_UNCONFIRMED")

    def test_flag_distributions_distinguish_booleans_integers_and_free_values(self):
        values = [True, False, 1, 0, "1", "FLAG_PRIVATO_NON_ESPORTARE", None]
        with closing(sqlite3.connect(self.database)) as conn, conn:
            for rid, value in enumerate(values, 4):
                conn.execute("INSERT INTO su_remote_telemetry_log VALUES(?,?,?,?,?,?)",
                             (rid, 10, 1, 1, "2026-10-29 11:00:00",
                              json.dumps({"success": value, "total_counter": 100})))
        report = diagnose_snapshot(self.database)
        paths = report["json_profiles"]["accepted_1"]["paths"]
        self.assertEqual(paths["$.success"]["flags"], {
            "boolean_true": 1, "boolean_false": 1,
            "integer_one": 1, "integer_zero": 1, "other": 3,
        })
        self.assertNotIn("flags", paths["$.total_counter"])
        self.assertNotIn("FLAG_PRIVATO_NON_ESPORTARE", json.dumps(report))
        self.assertEqual(report["status"], "SEMANTICS_UNCONFIRMED")

    def test_sampling_limit_is_respected_for_each_group_and_counts_are_full(self):
        with closing(sqlite3.connect(self.database)) as conn, conn:
            for rid in range(4, 16):
                conn.execute("INSERT INTO su_remote_telemetry_log VALUES(?,?,?,?,?,?)",
                             (rid, 10, 1, 1, "2026-10-29 11:00:00", "{}"))
        report = diagnose_snapshot(self.database, sample_limit=3)
        self.assertEqual(report["table_rows"]["su_remote_telemetry_log"], 15)
        self.assertEqual(report["json_profiles"]["accepted_1"]["sampled_rows"], 3)
        self.assertEqual(report["json_profiles"]["accepted_0"]["sampled_rows"], 1)
        self.assertEqual(report["json_profiles"]["other_accepted"]["sampled_rows"], 1)
        self.assertTrue(all(profile["sampled_rows"] <= 3 for profile in report["json_profiles"].values()))
        self.assertEqual(report["status"], "SEMANTICS_UNCONFIRMED")

    def test_certificate_serial_never_becomes_printer_serial(self):
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("UPDATE su_remote_telemetry_log SET payload_json=? WHERE accepted=1",
                         (json.dumps({"certificate_serial": "SERIALE_PRIVATO_12345", "total_counter": 100}),))
        report = diagnose_snapshot(self.database)
        profile = report["json_profiles"]["accepted_1"]
        self.assertNotIn("$.serial_number", profile["paths"])
        self.assertNotIn("$.certificate_serial", profile["paths"])
        self.assertEqual(report["status"], "SEMANTICS_UNCONFIRMED")

    def test_incomplete_schema_is_explicit_and_not_a_success(self):
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("DROP TABLE su_remote_agent_printers")
        report = diagnose_snapshot(self.database)
        self.assertEqual(report["status"], "SCHEMA_INCOMPLETE")
        self.assertIn("su_remote_agent_printers", json.dumps(report["missing_columns"]))

    def test_non_snapshot_filename_and_sidecars_are_rejected(self):
        active = self.root / "su_print_intelligence.db"
        shutil.copyfile(self.database, active)
        with self.assertRaises(ValueError):
            diagnose_snapshot(active)
        for suffix in ("-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix):
                sidecar = Path(str(self.database) + suffix)
                sidecar.touch()
                try:
                    with self.assertRaises(ValueError):
                        diagnose_snapshot(self.database)
                finally:
                    sidecar.unlink()

    def test_connections_are_read_only_query_only_and_explicitly_closed(self):
        original_connect = sqlite3.connect
        opened = []
        observations = []

        class ProbeConnection(sqlite3.Connection):
            verified = False

            def close(connection):
                if not connection.verified:
                    connection.verified = True
                    query_only = connection.execute("PRAGMA query_only").fetchone()[0]
                    try:
                        connection.execute("INSERT INTO printers VALUES(999, 'PROVA_SCRITTURA')")
                    except sqlite3.OperationalError as failure:
                        observations.append((query_only, failure.sqlite_errorcode & 255))
                    else:
                        observations.append((query_only, None))
                super().close()

        def track_connect(*args, **kwargs):
            self.assertTrue(kwargs.get("uri"))
            self.assertEqual(parse_qs(urlsplit(args[0]).query).get("mode"), ["ro"])
            self.assertEqual(parse_qs(urlsplit(args[0]).query).get("immutable"), ["1"])
            kwargs["factory"] = ProbeConnection
            connection = original_connect(*args, **kwargs)
            opened.append(connection)
            return connection

        try:
            with patch("su_print_counters.diagnostics.sqlite3.connect", side_effect=track_connect):
                diagnose_snapshot(self.database)
            self.assertTrue(opened)
            self.assertTrue(observations)
            self.assertTrue(all(observation == (1, sqlite3.SQLITE_READONLY) for observation in observations))
            for connection in opened:
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")
        finally:
            for connection in opened:
                connection.close()

    def test_cancel_before_start_leaves_everything_unchanged(self):
        cancel = threading.Event()
        cancel.set()
        before = self.database.read_bytes()
        files = set(self.root.iterdir())
        with self.assertRaises(SnapshotCancelled):
            diagnose_snapshot(self.database, cancel=cancel)
        self.assertEqual(self.database.read_bytes(), before)
        self.assertEqual(set(self.root.iterdir()), files)

    def test_timeout_is_reported_without_real_waits_or_database_writes(self):
        calls = []

        def clock():
            calls.append(True)
            return 0.0 if len(calls) == 1 else 1000.0

        before = self.database.read_bytes()
        with patch("su_print_counters.diagnostics.time.monotonic", side_effect=clock):
            with self.assertRaises(TimeoutError):
                diagnose_snapshot(self.database, timeout_seconds=1.0)
        self.assertEqual(self.database.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
