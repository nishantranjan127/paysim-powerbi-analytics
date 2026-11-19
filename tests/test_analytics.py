import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from finance_lakehouse.analytics import analyze, load_transactions
from finance_lakehouse.local import transform


def silver_rows():
    base = {
        "account_id": "AC-1", "event_time": "2026-01-01T00:00:00Z",
        "currency": "USD", "transaction_type": "CREDIT",
        "merchant_country": "US", "status": "POSTED",
    }
    changes = [
        {"amount": "0.10"},
        {"amount": "0.20"},
        {"amount": "0.05", "transaction_type": "DEBIT"},
        {"amount": "99999.00", "status": "DECLINED"},
        {"amount": "300.00", "currency": "EUR"},
        {"amount": "10000.00", "event_time": "2026-01-02T00:00:00Z"},
    ]
    return transform([
        {**base, "transaction_id": f"TX-{index}", **change}
        for index, change in enumerate(changes)
    ])[0]


class AnalyticsTests(unittest.TestCase):
    def test_exact_money_currency_date_and_status_filters(self):
        report = analyze(silver_rows(), "USD", date(2026, 1, 1), date(2026, 1, 1))
        self.assertEqual(report["attempts"], 4)
        self.assertEqual(report["posted_count"], 3)
        self.assertEqual(report["decline_rate"], Decimal("0.25"))
        self.assertEqual(report["inflow"], Decimal("0.30"))
        self.assertEqual(report["outflow"], Decimal("0.05"))
        self.assertEqual(report["net_flow"], Decimal("0.25"))
        self.assertEqual(report["average_posted_amount"], Decimal("0.35") / 3)
        self.assertEqual(report["daily"][0]["net_flow"], Decimal("0.25"))
        self.assertEqual(report["countries"][0]["posted_amount"], Decimal("0.35"))
        self.assertEqual(report["high_value_posted"], [])

    def test_empty_and_declined_only_selections(self):
        empty = analyze(silver_rows(), "GBP", date(2026, 1, 1), date(2026, 1, 2))
        self.assertEqual(empty["attempts"], 0)
        self.assertIsNone(empty["decline_rate"])
        self.assertIsNone(empty["average_posted_amount"])
        declined = [row for row in silver_rows() if row["status"] == "DECLINED"]
        report = analyze(declined, "USD", date(2026, 1, 1), date(2026, 1, 2))
        self.assertEqual(report["net_flow"], Decimal(0))
        self.assertEqual(report["decline_rate"], Decimal(1))
        self.assertIsNone(report["average_posted_amount"])
        self.assertEqual(report["countries"], [])

    def test_inclusive_dates_and_high_value_posted(self):
        report = analyze(silver_rows(), "USD", date(2026, 1, 1), date(2026, 1, 2))
        self.assertEqual(report["attempts"], 5)
        self.assertEqual(len(report["daily"]), 2)
        self.assertEqual([row["amount"] for row in report["high_value_posted"]], ["10000.00"])
        for currency, start, end in [
            ("USD", date(2026, 1, 2), date(2026, 1, 1)),
            ("XXX", date(2026, 1, 1), date(2026, 1, 2)),
        ]:
            with self.assertRaises(ValueError):
                analyze(silver_rows(), currency, start, end)

    def test_load_validates_snapshot_and_recomputes_derived_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "silver.jsonl"
            row = {**silver_rows()[0], "is_high_value": True, "transaction_date": "1999-01-01"}
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            loaded = load_transactions(path)
            self.assertFalse(loaded[0]["is_high_value"])
            self.assertEqual(loaded[0]["transaction_date"], "2026-01-01")
            for records in ([row, row], [{**row, "amount": "-1"}], [[]]):
                path.write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_transactions(path)


if __name__ == "__main__":
    unittest.main()
