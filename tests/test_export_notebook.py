import unittest
from pathlib import Path

from finance_lakehouse.rules import normalize_record
from tools.export_notebook import build_notebook


class NotebookExportTests(unittest.TestCase):
    def test_configured_widgets_are_encoded_without_execution(self):
        notebook = build_notebook(
            Path(__file__).resolve().parents[1],
            catalog="workspace",
            schema="finance_portfolio",
            input_path='/Volumes/workspace/finance_portfolio/landing/quoted"file.csv',
        )
        recorded = {}

        class Widgets:
            @staticmethod
            def text(name, value):
                recorded[name] = value

        class Utilities:
            widgets = Widgets()

        exec("".join(notebook["cells"][1]["source"]), {"dbutils": Utilities()})
        self.assertEqual(recorded, {
            "catalog": "workspace",
            "schema": "finance_portfolio",
            "input_path": '/Volumes/workspace/finance_portfolio/landing/quoted"file.csv',
        })

    def test_export_is_self_contained_and_preserves_validation(self):
        notebook = build_notebook(Path(__file__).resolve().parents[1])
        code = [cell["source"] for cell in notebook["cells"] if cell["cell_type"] == "code"]
        namespace = {}
        for source in code[1:3]:
            text = "".join(source)
            self.assertNotIn("from finance_lakehouse", text)
            exec(compile(text, "<test-notebook>", "exec"), namespace)
        raw = {
            "transaction_id": "TX-1", "account_id": "AC-1",
            "event_time": "2026-01-01T12:00:00Z", "amount": "10000",
            "currency": "USD", "transaction_type": "DEBIT",
            "merchant_country": "US", "status": "POSTED",
        }
        for changes in ({}, {"amount": "NaN"}, {"event_time": "bad"}):
            with self.subTest(changes=changes):
                row = {**raw, **changes}
                self.assertEqual(namespace["normalize_record"](row), normalize_record(row))
        self.assertTrue(callable(namespace["run"]))
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                self.assertIsNone(cell["execution_count"])
                self.assertEqual(cell["outputs"], [])
