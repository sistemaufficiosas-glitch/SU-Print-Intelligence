"""Coerenza fra letture parziali, senza imporre limiti da totali più vecchi."""

import unittest

import test_real_adapter as fixtures


class PartialConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RealAdapterTests(
            "test_valid_log_reading_uses_declared_fields_and_requires_review"
        )
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()

    @staticmethod
    def reading_codes(report):
        return {
            item["entity_id"]: item["code"]
            for item in report["anomalies"]
            if item["entity_type"] == "reading"
        }

    def test_new_total_below_previously_observed_black_is_rejected(self):
        self.fixture.reading(
            1, received="2026-01-01T10:00:00Z",
            black_counter=100, color_counter=None, total_counter_candidate=None,
        )
        self.fixture.reading(
            2, received="2026-01-02T10:00:00Z",
            black_counter=None, color_counter=None, total_counter_candidate=90,
        )

        report = self.fixture.collect()

        self.assertEqual(self.reading_codes(report).get(2), "INCONSISTENT_COUNTERS")
        self.assertEqual(
            [row["reading_id"] for row in report["printers"][0]["history"]], [1]
        )
        self.assertEqual(report["summary"]["validated_readings"], 1)
        self.assertEqual(report["summary"]["rejected_readings"], 1)

    def test_complementary_partial_counters_conflict_at_same_receipt_time(self):
        stamp = "2026-01-01T10:00:00Z"
        self.fixture.reading(
            1, received=stamp,
            black_counter=100, color_counter=None, total_counter_candidate=None,
        )
        self.fixture.reading(
            2, received=stamp,
            black_counter=None, color_counter=None, total_counter_candidate=90,
        )

        report = self.fixture.collect()

        self.assertEqual(
            self.reading_codes(report),
            {1: "CONFLICTING_READING", 2: "CONFLICTING_READING"},
        )
        self.assertEqual(report["printers"], [])
        self.assertEqual(report["summary"]["validated_readings"], 0)
        self.assertEqual(report["summary"]["rejected_readings"], 2)

    def test_new_black_may_exceed_an_older_total_that_was_not_reread(self):
        self.fixture.reading(
            1, received="2026-01-01T10:00:00Z",
            black_counter=80, color_counter=None, total_counter_candidate=90,
        )
        self.fixture.reading(
            2, received="2026-01-02T10:00:00Z",
            black_counter=100, color_counter=None, total_counter_candidate=None,
        )

        report = self.fixture.collect()
        printer = report["printers"][0]

        self.assertEqual([row["reading_id"] for row in printer["history"]], [1, 2])
        self.assertEqual(self.reading_codes(report), {})
        self.assertEqual(printer["latest"]["reading_id"], 2)
        self.assertIsNone(printer["latest"]["total_counter"])
        self.assertEqual(printer["latest_by_counter"]["total_counter"]["reading_id"], 1)

    def test_new_total_consistent_with_prior_partial_components_is_valid(self):
        self.fixture.reading(
            1, received="2026-01-01T10:00:00Z",
            black_counter=100, color_counter=40, total_counter_candidate=140,
        )
        self.fixture.reading(
            2, received="2026-01-02T10:00:00Z",
            black_counter=None, color_counter=45, total_counter_candidate=None,
        )
        self.fixture.reading(
            3, received="2026-01-03T10:00:00Z",
            black_counter=None, color_counter=None, total_counter_candidate=150,
        )

        report = self.fixture.collect()
        printer = report["printers"][0]

        self.assertEqual([row["reading_id"] for row in printer["history"]], [1, 2, 3])
        self.assertEqual(self.reading_codes(report), {})
        self.assertEqual(printer["latest"]["total_counter"], 150)
        self.assertEqual(printer["latest_by_counter"]["black_counter"]["reading_id"], 1)
        self.assertEqual(printer["latest_by_counter"]["color_counter"]["reading_id"], 2)
        self.assertEqual(printer["latest_by_counter"]["total_counter"]["reading_id"], 3)


if __name__ == "__main__":
    unittest.main()
