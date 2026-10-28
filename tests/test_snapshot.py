"""Collaudo della copia SQLite e del rapporto condivisibile, senza GUI."""

import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing, contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from su_print_counters.snapshot import SnapshotCancelled, prepare_snapshot


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="su snapshot test ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "database originale con spazi.db"
        self.output = self.root / "copie di collaudo"
        self.output.mkdir()
        self.marker = self.output / "documento utente.txt"
        self.marker.write_text("da conservare", encoding="utf-8")
        with closing(sqlite3.connect(self.source)) as conn, conn:
            conn.executescript('''
                CREATE TABLE printers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    serial TEXT NOT NULL,
                    private_note TEXT DEFAULT 'DEFAULT_PRIVATO_NON_ESPORTARE'
                );
                CREATE TABLE readings (
                    id INTEGER PRIMARY KEY,
                    printer_id INTEGER REFERENCES printers(id),
                    total INTEGER NOT NULL
                );
                CREATE VIEW printer_list AS SELECT id, serial FROM printers;
                INSERT INTO printers(serial, private_note)
                    VALUES ('MATRICOLA_PRIVATA_NON_ESPORTARE', 'NOTA_PRIVATA_NON_ESPORTARE');
                INSERT INTO readings VALUES (1, 1, 12345);
            ''')

    def assert_user_files_preserved(self):
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "da conservare")

    @contextmanager
    def track_connections(self):
        original_connect = sqlite3.connect
        opened = []

        def tracked_connect(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            opened.append((args, kwargs, connection))
            return connection

        try:
            with patch("su_print_counters.snapshot.sqlite3.connect", side_effect=tracked_connect):
                yield opened
        finally:
            # Ripulire anche quando la verifica fallisce, senza mascherare leak col GC.
            for _, _, connection in opened:
                connection.close()

    def test_copy_is_independent_source_unchanged_and_progress_complete(self):
        before = hashlib.sha256(self.source.read_bytes()).digest()
        progress = []
        folder = prepare_snapshot(
            self.source, self.output,
            on_progress=lambda copied, total: progress.append((copied, total)),
        )
        self.assertEqual(folder.parent, self.output)
        database = folder / "snapshot.sqlite"
        self.assertTrue(database.is_file())
        self.assertFalse(any(Path(str(database) + suffix).exists()
                             for suffix in ("-wal", "-shm", "-journal")))
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
            self.assertEqual(conn.execute("SELECT total FROM readings").fetchall(), [(12345,)])
            self.assertEqual(conn.execute("PRAGMA quick_check").fetchone(), ("ok",))
        self.assertEqual(before, hashlib.sha256(self.source.read_bytes()).digest())
        self.assertTrue(progress)
        self.assertGreater(progress[-1][1], 0)
        self.assertEqual(progress[-1][0], progress[-1][1])
        self.assertTrue(all(0 <= copied <= total for copied, total in progress))
        self.assert_user_files_preserved()

    def test_backup_includes_committed_wal_without_changing_source_files(self):
        with closing(sqlite3.connect(self.source)) as writer:
            self.assertEqual(writer.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            writer.execute("INSERT INTO readings VALUES (2, 1, 67890)")
            writer.commit()
            wal = Path(str(self.source) + "-wal")
            self.assertTrue(wal.is_file())
            before = {path: hashlib.sha256(path.read_bytes()).digest()
                      for path in (self.source, wal)}
            folder = prepare_snapshot(self.source, self.output)
            database = folder / "snapshot.sqlite"
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)) as copy:
                self.assertEqual(copy.execute("SELECT total FROM readings ORDER BY id").fetchall(),
                                 [(12345,), (67890,)])
            for path, digest in before.items():
                self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), digest)
            self.assertFalse(any(Path(str(database) + suffix).exists()
                                 for suffix in ("-wal", "-shm", "-journal")))

    def test_schema_report_contains_structure_but_no_row_or_default_values(self):
        folder = prepare_snapshot(self.source, self.output)
        text = (folder / "schema_collaudo.json").read_text(encoding="utf-8")
        schema = json.loads(text)
        self.assertEqual(schema["format_version"], 1)
        self.assertEqual(schema["source_file"], self.source.name)
        objects = {obj["name"]: obj for obj in schema["objects"]}
        self.assertEqual(set(objects), {"printers", "readings", "printer_list"})
        self.assertEqual(objects["printer_list"]["kind"], "view")
        self.assertEqual(objects["printers"]["kind"], "table")
        columns = {column["name"]: column for column in objects["printers"]["columns"]}
        self.assertEqual(columns["id"]["primary_key_position"], 1)
        self.assertTrue(columns["serial"]["not_null"])
        self.assertEqual(columns["serial"]["type"], "TEXT")
        self.assertEqual(columns["serial"]["hidden"], 0)
        relation = objects["readings"]["foreign_keys"][0]
        self.assertEqual((relation["table"], relation["from"], relation["to"]),
                         ("printers", "printer_id", "id"))
        self.assertNotIn("DEFAULT_PRIVATO_NON_ESPORTARE", text)
        self.assertNotIn("MATRICOLA_PRIVATA_NON_ESPORTARE", text)
        self.assertNotIn("NOTA_PRIVATA_NON_ESPORTARE", text)
        self.assertNotIn("12345", text)
        self.assertNotIn('"sql"', text)
        self.assertNotIn('"dflt_value"', text)

    def test_repeat_creates_distinct_outputs_without_overwriting_user_files(self):
        first = prepare_snapshot(self.source, self.output)
        first_content = (first / "snapshot.sqlite").read_bytes()
        second = prepare_snapshot(self.source, self.output)
        self.assertNotEqual(first, second)
        self.assertEqual((first / "snapshot.sqlite").read_bytes(), first_content)
        self.assertTrue((second / "snapshot.sqlite").is_file())
        self.assert_user_files_preserved()

    def test_invalid_database_does_not_leave_partial_outputs(self):
        invalid = self.root / "invalid.sqlite"
        invalid.write_bytes(b"Non e un database SQLite")
        before = set(self.output.iterdir())
        with self.assertRaises((sqlite3.DatabaseError, ValueError)):
            prepare_snapshot(invalid, self.output)
        self.assertEqual(set(self.output.iterdir()), before)
        self.assertEqual(invalid.read_bytes(), b"Non e un database SQLite")
        self.assert_user_files_preserved()

    def test_missing_source_is_not_created(self):
        missing = self.root / "missing.sqlite"
        before = set(self.output.iterdir())
        with self.assertRaises((OSError, ValueError)):
            prepare_snapshot(missing, self.output)
        self.assertFalse(missing.exists())
        self.assertEqual(set(self.output.iterdir()), before)

    def test_cancel_before_start_preserves_user_files(self):
        cancel = threading.Event()
        cancel.set()
        before = set(self.output.iterdir())
        with self.assertRaises(SnapshotCancelled):
            prepare_snapshot(self.source, self.output, cancel=cancel)
        self.assertEqual(set(self.output.iterdir()), before)
        self.assert_user_files_preserved()

    def test_cancel_during_backup_removes_only_own_partial_copy(self):
        with closing(sqlite3.connect(self.source)) as conn, conn:
            conn.execute("CREATE TABLE payload(value BLOB)")
            conn.execute("INSERT INTO payload VALUES (?)", (bytes(2 * 1024 * 1024),))
        cancel = threading.Event()
        progress = []

        def stop_after_first_chunk(copied, total):
            progress.append((copied, total))
            cancel.set()

        before = set(self.output.iterdir())
        with self.assertRaises(SnapshotCancelled):
            prepare_snapshot(self.source, self.output, cancel=cancel,
                             on_progress=stop_after_first_chunk)
        self.assertTrue(progress)
        self.assertEqual(set(self.output.iterdir()), before)
        self.assert_user_files_preserved()

    def test_source_and_destination_handles_close_on_success_and_cancellation(self):
        for cancel_requested in (False, True):
            with self.subTest(cancel_requested=cancel_requested):
                cancel = threading.Event()
                before = set(self.output.iterdir())
                with self.track_connections() as opened:
                    if cancel_requested:
                        with self.assertRaises(SnapshotCancelled):
                            prepare_snapshot(self.source, self.output, cancel=cancel,
                                             on_progress=lambda *_: cancel.set())
                        self.assertEqual(set(self.output.iterdir()), before)
                    else:
                        prepare_snapshot(self.source, self.output)
                    self.assertGreaterEqual(len(opened), 2)
                    # Gli handle restano referenziati: il controllo vale anche su Linux.
                    for _, _, connection in opened:
                        with self.assertRaises(sqlite3.ProgrammingError):
                            connection.execute("SELECT 1")
        self.assert_user_files_preserved()

    def test_source_is_opened_read_only_and_rejects_writes_during_backup(self):
        checked = []
        with self.track_connections() as opened:
            def verify_source_is_read_only(*_):
                matches = [(args, kwargs, connection) for args, kwargs, connection in opened
                           if args and str(args[0]).startswith(self.source.as_uri() + "?")]
                self.assertEqual(len(matches), 1)
                args, kwargs, connection = matches[0]
                self.assertTrue(kwargs.get("uri"))
                self.assertEqual(parse_qs(urlsplit(args[0]).query).get("mode"), ["ro"])
                self.assertEqual(connection.execute("PRAGMA query_only").fetchone(), (1,))
                with self.assertRaises(sqlite3.OperationalError) as failure:
                    connection.execute("INSERT INTO readings VALUES (3, 1, 99999)")
                self.assertEqual(failure.exception.sqlite_errorcode & 255, sqlite3.SQLITE_READONLY)
                checked.append(True)

            prepare_snapshot(self.source, self.output, on_progress=verify_source_is_read_only)
        self.assertTrue(checked)
        with closing(sqlite3.connect(self.source)) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM readings").fetchone(), (1,))

    def test_timeout_after_output_creation_cleans_up_without_real_waits(self):
        before = set(self.output.iterdir())

        def clock():
            # Scadere solo dopo che il helper ha creato il proprio output parziale.
            return 2.0 if set(self.output.iterdir()) != before else 0.0

        with patch("su_print_counters.snapshot.time.monotonic", side_effect=clock):
            with self.assertRaises(TimeoutError):
                prepare_snapshot(self.source, self.output, timeout_seconds=1.0)
        self.assertEqual(set(self.output.iterdir()), before)
        self.assert_user_files_preserved()


if __name__ == "__main__":
    unittest.main()
