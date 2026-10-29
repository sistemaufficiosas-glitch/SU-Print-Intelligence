"""Contesto utile degli scarti, senza copiare payload o dati estranei."""

import json
import unittest

import test_real_adapter as fixtures


class AuditContextTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RealAdapterTests("test_valid_log_reading_uses_declared_fields_and_requires_review")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_mismatch_identifies_both_serials_without_reattaching(self):
        self.fixture.reading(1, serial_number="SN-002", auth_key="SECRET_NOT_TO_EXPORT", customer_name="PRIVATE_CUSTOMER")
        report = self.fixture.collect()
        issue = next(item for item in report["anomalies"] if item["code"] == "SERIAL_MISMATCH")
        context = issue["context"]
        self.assertEqual(context["printer_id"], 1)
        self.assertEqual(context["agent_id"], 10)
        self.assertEqual(context["inventory_serial"], "SN-001")
        self.assertEqual(context["declared_serial"], "SN-002")
        self.assertEqual(context["counters"]["total_counter"], 100)
        self.assertEqual(report["printers"], [])
        text = json.dumps(context)
        for value in ("SECRET_NOT_TO_EXPORT", "PRIVATE_CUSTOMER", "192.168.1.", "raw_json", "payload_json"):
            self.assertNotIn(value, text)
        # Un numero JSON con esponente enorme non deve rompere il report dello scarto.
        self.fixture.reading(2, raw='{"printer_id":1,"serial_number":"SN-001","snmp_ok":true,"collected_at":"2026-01-01T09:00:00","black_counter":1e309,"total_counter_candidate":100}')
        report = self.fixture.collect()
        invalid = next(item for item in report["anomalies"] if item["entity_type"] == "reading" and item["entity_id"] == 2)
        self.assertEqual(invalid["code"], "INVALID_COUNTER")
        self.assertEqual(invalid["context"]["counters"]["black_counter"], {"type": "float", "finite": False})
        json.dumps(report, allow_nan=False)

    def test_regression_records_value_and_last_known_metric_baseline(self):
        self.fixture.reading(1, black_counter=100, color_counter=40, total_counter_candidate=140)
        self.fixture.reading(2, black_counter=None, color_counter=45, total_counter_candidate=None)
        self.fixture.reading(3, black_counter=90, color_counter=None, total_counter_candidate=None)
        report = self.fixture.collect()
        issue = next(item for item in report["anomalies"] if item["code"] == "COUNTER_REGRESSION")
        context = issue["context"]
        self.assertEqual(context["printer_id"], 1)
        self.assertEqual(context["counters"]["black_counter"], 90)
        self.assertEqual(context["regressed_metrics"], ["black_counter"])
        self.assertEqual(context["baseline"]["black_counter"]["value"], 100)
        self.assertEqual(context["baseline"]["black_counter"]["reading_id"], 1)
        self.assertEqual(context["baseline"]["black_counter"]["agent_id"], 10)
        self.assertNotIn("declared_serial", context)
        self.assertTrue(context["declared_serial_matches_inventory"])

    def test_conflict_records_peers_and_declares_sampling_limit(self):
        self.fixture.reading(1, agent=10, received="2026-01-01T10:00:00Z")
        self.fixture.reading(2, agent=20, received="2026-01-01T10:00:00Z", total_counter_candidate=101)
        for rid in range(10, 31):
            self.fixture.reading(rid, received="2026-01-02T10:00:00Z", total_counter_candidate=100 + rid)
        report = self.fixture.collect()
        issues = [item for item in report["anomalies"] if item["code"] == "CONFLICTING_READING"]
        first = next(item["context"] for item in issues if item["entity_id"] == 1)
        self.assertEqual(first["group_size"], 2)
        self.assertEqual({row["agent_id"] for row in first["conflict_peers"]}, {10, 20})
        self.assertIn("total_counter", first["conflicting_metrics"])
        self.assertFalse(first["conflict_peers_truncated"])
        large = next(item["context"] for item in issues if item["entity_id"] == 10)
        self.assertEqual(large["group_size"], 21)
        self.assertEqual(len(large["conflict_peers"]), 20)
        self.assertTrue(large["conflict_peers_truncated"])


if __name__ == "__main__":
    unittest.main()
