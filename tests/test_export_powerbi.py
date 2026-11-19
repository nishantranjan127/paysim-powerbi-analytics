import json
import tempfile
import unittest
from pathlib import Path

from finance_lakehouse.local import run
from finance_lakehouse.sample_data import generate
from tools.export_powerbi import build_project, export_project


class PowerBIExportTests(unittest.TestCase):
    def test_project_links_visual_fields_and_model_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            generate(root / "input.csv", rows=10)
            run(root / "input.csv", root / "data")
            source = root / "data" / "silver_transactions.jsonl"
            entry = export_project(source, root / "report")
            shortcut = json.loads(entry.read_text())
            report = root / "report" / shortcut["artifacts"][0]["report"]["path"]
            definition = json.loads((report / "definition.pbir").read_text())
            model_path = report / definition["datasetReference"]["byPath"]["path"] / "model.bim"
            table = json.loads(model_path.read_text())["model"]["tables"][0]
            columns = {column["name"] for column in table["columns"]}
            measures = {measure["name"] for measure in table["measures"]}
            visuals = list(report.glob("definition/pages/*/visuals/*/visual.json"))
            self.assertEqual(len(visuals), 19)
            for path in visuals:
                visual = json.loads(path.read_text())
                page = json.loads((path.parents[2] / "page.json").read_text())
                bounds = visual["position"]
                self.assertLessEqual(bounds["x"] + bounds["width"], page["width"])
                self.assertLessEqual(bounds["y"] + bounds["height"], page["height"])
                if visual["name"] == "currency":
                    selection = visual["visual"]["objects"]["general"][0]["properties"]["filter"]["filter"]
                    condition = selection["Where"][0]["Condition"]["In"]
                    self.assertIn(selection["From"][0]["Entity"], {table["name"]})
                    self.assertIn(condition["Expressions"][0]["Column"]["Property"], columns)
                    self.assertEqual(condition["Values"], [[{"Literal": {"Value": "'USD'"}}]])
                for role in visual["visual"]["query"]["queryState"].values():
                    for item in role["projections"]:
                        kind, expression = next(iter(item["field"].items()))
                        self.assertIn(expression["Property"], measures if kind == "Measure" else columns)
            original = entry.read_bytes()
            with self.assertRaises(FileExistsError):
                export_project(source, root / "report")
            self.assertEqual(entry.read_bytes(), original)

    def test_empty_invalid_or_out_of_range_data_does_not_create_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "silver.jsonl"
            for text in ("", "not-json\n", '{"amount": "NaN"}\n'):
                source.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    export_project(source, root / "report")
                self.assertFalse((root / "report").exists())
            source.write_text(json.dumps({
                "transaction_id": "TX-1", "account_id": "AC-1",
                "event_time": "2026-01-01T00:00:00Z", "amount": "9999999999999999.99",
                "currency": "USD", "transaction_type": "DEBIT",
                "merchant_country": "US", "status": "POSTED",
            }) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fixed-decimal"):
                build_project(source)


if __name__ == "__main__":
    unittest.main()
