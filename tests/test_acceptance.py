"""Esportazione locale dei risultati e privacy del riepilogo condivisibile."""

import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from su_print_counters.acceptance import render_html, shareable_summary, write_acceptance_report
from su_print_counters.snapshot import SnapshotCancelled


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="su acceptance test ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.snapshot = self.root / "snapshot.sqlite"
        self.snapshot.write_bytes(b"placeholder for mocked read-only collector")
        self.marker = self.root / "documento utente.txt"
        self.marker.write_text("da conservare", encoding="utf-8")
        self.latest = {
            "reading_id": 1234567, "agent_id": 7654321,
            "received_at": "2026-10-29T12:00:00+00:00", "collected_at": "2026-10-29T12:58:00",
            "collected_at_has_timezone": False, "black_counter": None,
            "color_counter": 0, "total_counter": 2345, "currently_authorized": True,
        }
        self.report = {
            "status": "REVIEW_REQUIRED",
            "summary": {"source_printers": 2, "included_printers": 1, "excluded_printers": 1,
                        "validated_readings": 2, "rejected_readings": 1,
                        "matricola_privata": "SERIALE_PRIVATO", "ID_NON_CONDIVISIBILE": 1234567},
            "anomalies_by_code": {"SERIAL_MISMATCH": 1, "SERIALE_PRIVATO": 2},
            "printers": [{"printer_id": 1234567, "serial": 'SERIALE_PRIVATO<script>alert("x")</script>',
                          "latest": self.latest, "history": [self.latest],
                          "latest_by_counter": {"black_counter": dict(self.latest, black_counter=987654321),
                                                "color_counter": self.latest, "total_counter": self.latest}}],
            "archive": [{"serial": "SERIALE_ARCHIVIATO_PRIVATO"}],
            "anomalies": [{"code": "SERIAL_MISMATCH", "entity_id": 1234567,
                           "detail": "CLIENTE_PRIVATO_IP_192.168.1.1"}],
        }

    def test_shareable_summary_contains_only_counts_and_known_codes(self):
        summary = shareable_summary(self.report)
        self.assertEqual(summary["status"], "REVIEW_REQUIRED")
        self.assertEqual(summary["summary"]["included_printers"], 1)
        self.assertEqual(summary["anomalies_by_code"], {"OTHER_ANOMALY": 2, "SERIAL_MISMATCH": 1})
        text = json.dumps(summary)
        for private in ("SERIALE_PRIVATO", "SERIALE_ARCHIVIATO_PRIVATO", "1234567", "7654321",
                        "CLIENTE_PRIVATO", "192.168.1.1", "2026-10-29", "987654321"):
            self.assertNotIn(private, text)

    def test_html_escapes_identity_and_does_not_fill_partial_latest_from_history(self):
        page = render_html(self.report)
        self.assertNotIn('<script>alert("x")</script>', page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("COLLAUDO DA VERIFICARE", page)
        self.assertIn("Totale dichiarato", page)
        self.assertIn("fuso non indicato", page)
        self.assertIn("<td>—</td><td>0</td><td>2.345</td>", page)
        self.assertNotIn("987.654.321", page)
        self.assertNotIn("987654321", page)

    def test_shareable_context_contains_only_typed_checks_and_snapshot_hash(self):
        report = dict(self.report,
                      snapshot_sha256="AB" * 32,
                      time_policy={"naive_receipt_utc_default_confirmed": True, "sql": "SQL_PRIVATO"},
                      total_policy={"origin_verified": False, "note": "TESTO_PRIVATO"},
                      provenance={"offline_hmac_rechecked": False, "agent_id": 1234567})
        shared = shareable_summary(report)
        self.assertEqual(shared["checks"], {"naive_receipt_utc_default_confirmed": True,
                                            "total_origin_verified": False, "offline_hmac_rechecked": False})
        self.assertEqual(shared["snapshot_sha256"], "ab" * 32)
        text = json.dumps(shared)
        for private in ("SQL_PRIVATO", "TESTO_PRIVATO", "1234567"):
            self.assertNotIn(private, text)
        report.update(snapshot_sha256="SERIALE_PRIVATO",
                      time_policy={"naive_receipt_utc_default_confirmed": "true"},
                      total_policy={"origin_verified": 1}, provenance={"offline_hmac_rechecked": None})
        shared = shareable_summary(report)
        self.assertEqual(shared["checks"], {})
        self.assertNotIn("snapshot_sha256", shared)

    def test_report_files_are_distinct_and_snapshot_and_user_files_unchanged(self):
        digest = hashlib.sha256(self.snapshot.read_bytes()).digest()
        with patch("su_print_counters.acceptance.collect_real", return_value=self.report) as collector:
            first = write_acceptance_report(self.snapshot)
            first_files = {p.name: p.read_bytes() for p in first.iterdir()}
            second = write_acceptance_report(self.snapshot)
        self.assertEqual(collector.call_count, 2)
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, self.root)
        self.assertEqual(set(first_files), {"risultati_contatori.json", "esito_collaudo.json",
                                            "risultati_contatori.html", "LEGGIMI.txt"})
        self.assertEqual({p.name: p.read_bytes() for p in first.iterdir()}, first_files)
        self.assertEqual(json.loads((first / "risultati_contatori.json").read_text(encoding="utf-8")), self.report)
        self.assertEqual(hashlib.sha256(self.snapshot.read_bytes()).digest(), digest)
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "da conservare")

    def test_collector_failure_does_not_create_outputs(self):
        before = set(self.root.iterdir())
        with patch("su_print_counters.acceptance.collect_real", side_effect=ValueError("invalid snapshot")):
            with self.assertRaises(ValueError):
                write_acceptance_report(self.snapshot)
        self.assertEqual(set(self.root.iterdir()), before)

    def test_serialization_failure_removes_only_own_incomplete_folder(self):
        before = set(self.root.iterdir())
        invalid = dict(self.report, nonserializable=object())
        with patch("su_print_counters.acceptance.collect_real", return_value=invalid):
            with self.assertRaises(TypeError):
                write_acceptance_report(self.snapshot)
        self.assertEqual(set(self.root.iterdir()), before)
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "da conservare")

    def test_cancel_before_or_after_collection_preserves_user_files(self):
        for cancelled_before in (True, False):
            with self.subTest(cancelled_before=cancelled_before):
                cancel = threading.Event()
                if cancelled_before:
                    cancel.set()

                def collecting(*args, **kwargs):
                    cancel.set()
                    return self.report

                before = set(self.root.iterdir())
                with patch("su_print_counters.acceptance.collect_real", side_effect=collecting) as collector:
                    with self.assertRaises(SnapshotCancelled):
                        write_acceptance_report(self.snapshot, cancel=cancel)
                self.assertEqual(collector.call_count, 0 if cancelled_before else 1)
                self.assertEqual(set(self.root.iterdir()), before)

    def test_cancel_during_writing_removes_only_own_partial_results(self):
        cancel = threading.Event()
        real_open = Path.open

        def opening(path, *args, **kwargs):
            stream = real_open(path, *args, **kwargs)
            if path.name == "risultati_contatori.json":
                cancel.set()
            return stream

        before = set(self.root.iterdir())
        with patch("su_print_counters.acceptance.collect_real", return_value=self.report), \
                patch("pathlib.Path.open", autospec=True, side_effect=opening):
            with self.assertRaises(SnapshotCancelled):
                write_acceptance_report(self.snapshot, cancel=cancel)
        self.assertEqual(set(self.root.iterdir()), before)
        self.assertEqual(self.marker.read_text(encoding="utf-8"), "da conservare")

    def test_active_database_name_is_rejected_before_collection(self):
        active = self.root / "su_print_intelligence.db"
        active.write_bytes(b"database attivo da non aprire")
        before = active.read_bytes()
        with patch("su_print_counters.acceptance.collect_real") as collector:
            with self.assertRaises(ValueError):
                write_acceptance_report(active)
        collector.assert_not_called()
        self.assertEqual(active.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
