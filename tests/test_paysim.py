import csv
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile

try:
    import duckdb
except ImportError:
    duckdb = None

from finance_lakehouse.paysim import FIELDS, prepare
from tools.export_paysim_powerbi import MODEL, TABLES, export_project


def transaction(**changes):
    return {
        "step": "1", "type": "TRANSFER", "amount": "0.10", "nameOrig": "C1",
        "oldbalanceOrg": "1.00", "newbalanceOrig": "0.90", "nameDest": "C2",
        "oldbalanceDest": "0.00", "newbalanceDest": "0.10", "isFraud": "0",
        "isFlaggedFraud": "0", **changes,
    }


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


@unittest.skipIf(duckdb is None, "Install the paysim extra to run PaySim preparation tests")
class PaySimTests(unittest.TestCase):
    def test_exact_reconciliation_labels_day_boundaries_and_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.csv"
            rows = [
                transaction(amount="1E-1", oldbalanceOrg="1.0E7"),
                transaction(amount="0.20", isFraud="1", isFlaggedFraud="1"),
                transaction(step="24", amount="0.00", isFraud="1", nameOrig="C3"),
                transaction(step="25", amount="0.30", isFlaggedFraud="1", nameOrig="C4"),
                transaction(step="743", amount="0.40", nameOrig="C4"),
            ]
            write_csv(source, rows)
            archive = root / "input.zip"
            with ZipFile(archive, "w") as zipped:
                zipped.write(source, arcname="nested/input.csv")
            manifest = prepare(archive, root / "result")
            report = json.loads(manifest.read_text())
            self.assertEqual(report["metrics"]["transaction_amount"], "1.00")
            self.assertEqual(report["metrics"]["fraud_amount"], "0.20")
            self.assertEqual(report["metrics"]["true_positives"], 1)
            self.assertEqual(report["metrics"]["false_positives"], 1)
            self.assertEqual(report["metrics"]["false_negatives"], 1)
            with duckdb.connect() as connection:
                connection.read_parquet(str(root / "result" / "transactions.parquet")).create_view("tx")
                self.assertEqual(connection.execute("SELECT count(*), sum(amount) FROM tx").fetchone(),
                                 (5, Decimal("1.00")))
                hours = connection.read_parquet(str(root / "result" / "simulation_hours.parquet")).fetchall()
                self.assertEqual(hours, [(1, 1, 0), (24, 1, 23), (25, 2, 0), (743, 31, 22)])
                profiles = connection.read_parquet(str(root / "result" / "account_profiles.parquet")).fetchall()
                self.assertEqual(profiles[0][1:4], ("C4", 2, Decimal("0.70")))
            with self.assertRaises(FileExistsError):
                prepare(source, root / "result")
            self.assertEqual(json.loads(manifest.read_text()), report)

    def test_invalid_input_never_publishes_partial_output_or_rounds_money(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.csv"
            cases = [
                [], [transaction(amount="0.001")], [transaction(amount="NaN")],
                [transaction(amount="1.001E0")],
                [transaction(amount="-1")], [transaction(step="0")],
                [transaction(step="745")], [transaction(isFraud="2")],
                [transaction(type="REFUND")], [transaction(nameOrig="")],
                [transaction(amount="922337203685477.59")],
            ]
            for rows in cases:
                with self.subTest(rows=rows):
                    write_csv(source, rows)
                    with self.assertRaises(ValueError):
                        prepare(source, root / "result")
                    self.assertFalse((root / "result").exists())

    def test_clean_data_with_no_fraud_or_flags_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.csv"
            write_csv(source, [transaction()])
            manifest = prepare(source, root / "result")
            report = json.loads(manifest.read_text())
            self.assertEqual(report["metrics"]["fraud_transactions"], 0)
            self.assertEqual(report["row_counts"]["fraud_details"], 0)

    def test_report_relationships_preserve_filtered_totals_and_fields_resolve(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.csv"
            write_csv(source, [
                transaction(amount="0.10"), transaction(amount="0.20", isFraud="1"),
                transaction(step="25", type="PAYMENT", amount="100.00"),
            ])
            prepare(source, root / "data")
            entry = export_project(root / "data", root / "report")
            model = json.loads((entry.parent / MODEL / "model.bim").read_text())["model"]
            tables = {table["name"]: table for table in model["tables"]}
            with duckdb.connect() as connection:
                for name, (filename, columns) in TABLES.items():
                    relation = connection.read_parquet(str(root / "data" / f"{filename}.parquet"))
                    self.assertEqual(relation.columns, [prop for prop, _ in columns])
                    relation.create_view(name)
                for relationship in model["relationships"]:
                    target, key = relationship["toTable"], relationship["toColumn"]
                    self.assertEqual(connection.execute(
                        f"SELECT count(*) - count(DISTINCT {key}) FROM {target}"
                    ).fetchone()[0], 0)
                actual = connection.execute("""
                    SELECT sum(a.transaction_count), sum(a.total_amount)
                    FROM Activity a JOIN SimulationHours h USING (step)
                    JOIN TransactionTypes t USING (transaction_type)
                    WHERE h.simulation_day=1 AND t.transaction_type='TRANSFER'
                """).fetchone()
                self.assertEqual(actual, (2, Decimal("0.30")))
            for path in entry.parent.rglob("visual.json"):
                item = json.loads(path.read_text())
                page = json.loads((path.parents[2] / "page.json").read_text())
                self.assertLessEqual(item["position"]["x"] + item["position"]["width"], page["width"])
                self.assertLessEqual(item["position"]["y"] + item["position"]["height"], page["height"])
                for role in item["visual"]["query"]["queryState"].values():
                    for projection in role["projections"]:
                        kind, expression = next(iter(projection["field"].items()))
                        table = tables[expression["Expression"]["SourceRef"]["Entity"]]
                        fields = table["measures"] if kind == "Measure" else table["columns"]
                        self.assertIn(expression["Property"], {prop["name"] for prop in fields})
            with self.assertRaises(FileExistsError):
                export_project(root / "data", root / "report")


if __name__ == "__main__":
    unittest.main()
