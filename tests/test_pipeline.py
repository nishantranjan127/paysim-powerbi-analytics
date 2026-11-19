import csv
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from finance_lakehouse.local import read_csv, run, transform
from finance_lakehouse.rules import FIELDS, normalize_record
from finance_lakehouse.sample_data import generate


def transaction(**changes):
    return {
        "transaction_id": "TX-1",
        "account_id": "AC-1",
        "event_time": "2026-01-01T00:30:00+02:00",
        "amount": "10.10",
        "currency": "usd",
        "transaction_type": "debit",
        "merchant_country": "us",
        "status": "posted",
        **changes,
    }


class ValidationTests(unittest.TestCase):
    def test_timezone_normalization_and_threshold(self):
        row = normalize_record(transaction(amount="10000.00"))
        self.assertEqual(row["transaction_date"], "2025-12-31")
        self.assertEqual(row["event_time"], "2025-12-31T22:30:00.000000+00:00")
        self.assertEqual(row["currency"], "USD")
        self.assertTrue(row["is_high_value"])
        self.assertFalse(normalize_record(transaction(amount="9999.99"))["is_high_value"])
        self.assertFalse(normalize_record(transaction(amount="10000", currency="EUR"))["is_high_value"])

    def test_invalid_money_is_quarantined_without_rounding(self):
        for amount in ("NaN", "Infinity", "-1", "0", "0.00", "1.001", "1e2", "10000000000000000.00"):
            with self.subTest(amount=amount):
                self.assertEqual(normalize_record(transaction(amount=amount))["error_code"], "invalid_amount")
        self.assertIsNone(normalize_record(transaction(amount="9999999999999999.99"))["error_code"])

    def test_invalid_fields_and_timestamps_have_reasons(self):
        cases = [
            ({"account_id": ""}, "missing_required_field"),
            ({"transaction_id": "bad id"}, "invalid_transaction_id"),
            ({"currency": "XXX"}, "unsupported_currency"),
            ({"merchant_country": "USA"}, "invalid_country"),
            ({"transaction_type": "REFUND"}, "invalid_transaction_type"),
            ({"status": "PENDING"}, "invalid_status"),
        ]
        cases += [
            ({"event_time": value}, "invalid_event_time")
            for value in (
                "2026-01-01T12:00:00",
                "2026-02-30T12:00:00Z",
                "0001-01-01T00:00:00Z",
                "2026-01-01T00:00:00+00:99",
                "2026-01-01T00:00:00+24:00",
            )
        ]
        for changes, reason in cases:
            with self.subTest(changes=changes):
                self.assertEqual(normalize_record(transaction(**changes))["error_code"], reason)


class PipelineTests(unittest.TestCase):
    def test_latest_valid_record_and_ties_are_order_independent(self):
        rows = [
            transaction(),
            transaction(event_time="2026-01-02T00:00:00Z", amount="20"),
            transaction(event_time="2026-01-02T00:00:00Z", amount="30"),
            transaction(event_time="2026-01-03T00:00:00Z", amount="-1"),
        ]
        silver, quarantine, gold = transform(rows)
        reverse = transform(list(reversed(rows)))
        self.assertEqual(silver, reverse[0])
        self.assertEqual(gold, reverse[2])
        self.assertEqual(silver[0]["amount"], "30.00")
        self.assertEqual(len(quarantine), 3)
        self.assertCountEqual(
            [row["reason"] for row in quarantine],
            ["duplicate_transaction", "duplicate_transaction", "invalid_amount"],
        )

    def test_gold_keeps_currency_status_and_direction_separate(self):
        rows = [
            transaction(transaction_id="1", amount="0.10"),
            transaction(transaction_id="2", amount="0.20"),
            transaction(transaction_id="3", amount="100", currency="EUR"),
            transaction(transaction_id="4", amount="20", status="DECLINED"),
            transaction(transaction_id="5", amount="40", transaction_type="CREDIT"),
        ]
        silver, quarantine, gold = transform(rows)
        self.assertEqual(len(quarantine), 0)
        self.assertEqual(len(gold), 4)
        debit = next(row for row in gold if row["currency"] == "USD" and row["status"] == "POSTED"
                     and row["transaction_type"] == "DEBIT")
        self.assertEqual(debit["total_amount"], "0.30")
        self.assertEqual(sum(row["transaction_count"] for row in gold), len(silver))
        self.assertEqual(sum(Decimal(row["total_amount"]) for row in gold), Decimal("160.30"))

    def test_all_invalid_batch_has_no_gold(self):
        silver, quarantine, gold = transform([transaction(amount="NaN")])
        self.assertEqual(silver, [])
        self.assertEqual(gold, [])
        self.assertEqual(quarantine[0]["reason"], "invalid_amount")

    def test_generated_demo_is_reconciled_and_rerun_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "transactions.csv"
            self.assertEqual(generate(source, rows=100), 108)
            first = run(source, root / "output")
            self.assertEqual(first["bronze_rows"], 108)
            self.assertEqual(first["silver_rows"], 100)
            self.assertEqual(first["quarantine_rows"], 8)
            self.assertEqual(first["quarantine_reasons"]["duplicate_transaction"], 2)
            silver_path = root / "output" / "silver_transactions.jsonl"
            original = silver_path.read_bytes()
            self.assertEqual(run(source, root / "output"), first)
            self.assertEqual(silver_path.read_bytes(), original)
            gold = [
                json.loads(line)
                for line in (root / "output" / "gold_daily_metrics.jsonl").read_text().splitlines()
            ]
            self.assertEqual(sum(row["transaction_count"] for row in gold), 100)
            self.assertEqual(
                json.loads((root / "output" / "quality_report.json").read_text()), first
            )

    def test_bad_file_contract_fails_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.csv"
            for content in ("wrong,header\n1,2\n", ",".join(FIELDS) + "\n",
                            ",".join(FIELDS) + "\n1,2\n"):
                with self.subTest(content=content):
                    path.write_text(content, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        read_csv(path)
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=FIELDS)
                writer.writeheader()
                writer.writerow(transaction())
            self.assertEqual(len(read_csv(path)), 1)


if __name__ == "__main__":
    unittest.main()
