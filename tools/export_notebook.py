"""Export the shared pipeline as an importable, dependency-free Databricks notebook."""

import argparse
import json
from pathlib import Path


def build_notebook(
    root: Path,
    catalog: str = "",
    schema: str = "finance_portfolio",
    input_path: str = "",
) -> dict:
    rules = (root / "finance_lakehouse" / "rules.py").read_text(encoding="utf-8")
    pipeline = (root / "finance_lakehouse" / "spark_pipeline.py").read_text(encoding="utf-8")
    shared_import = "from finance_lakehouse.rules import FIELDS, TIE_BREAK_FIELDS, normalize_record"
    if pipeline.count(shared_import) != 1:
        raise ValueError("Shared rules import changed; update the standalone notebook exporter")
    pipeline = pipeline.replace(shared_import, "")
    cells = []

    def cell(kind: str, source: str) -> None:
        entry = {"cell_type": kind, "metadata": {}, "source": source.splitlines(keepends=True)}
        if kind == "code":
            compile(source, "<exported-notebook-cell>", "exec")
            entry.update(execution_count=None, outputs=[])
        cells.append(entry)

    cell(
        "markdown",
        "# Financial Transactions Lakehouse\n\n"
        "Self-contained batch notebook generated from the project's shared Python sources.\n\n"
        "**Not executed during setup.** Running cells can start compute and incur charges.\n\n"
        "1. Set the catalog, dedicated schema, and Unity Catalog volume CSV path below.\n"
        "2. Upload the complete synthetic transaction CSV to that volume before running.\n"
        "3. Run only after approving compute usage. Every run replaces four project tables.\n\n"
        "Works with AWS or Azure Unity Catalog volumes. Azure Data Factory is not required.\n"
        "The default fixture should yield 1008 Bronze, 1000 Silver, and 8 quarantine rows.\n"
        "This is a full-snapshot pipeline, not streaming or incremental ingestion.\n",
    )
    cell(
        "code",
        f'dbutils.widgets.text("catalog", {json.dumps(catalog)})\n'
        f'dbutils.widgets.text("schema", {json.dumps(schema)})\n'
        f'dbutils.widgets.text("input_path", {json.dumps(input_path)})\n',
    )
    cell("markdown", "## Shared data-quality rules\n")
    cell("code", rules)
    cell("markdown", "## Bronze, Silver, Quarantine, and Gold transformations\n")
    cell("code", pipeline)
    cell(
        "markdown",
        "## Execute the complete snapshot\n\n"
        "Example input path: `/Volumes/your_catalog/finance_portfolio/landing/transactions.csv`.\n"
        "Stop here until the widgets and data are configured. The schema must be dedicated "
        "to this project because its tables are overwritten.\n",
    )
    cell(
        "code",
        "report = run(\n"
        "    spark,\n"
        '    dbutils.widgets.get("catalog"),\n'
        '    dbutils.widgets.get("schema"),\n'
        '    dbutils.widgets.get("input_path"),\n'
        ")\n"
        "print(json.dumps(report, indent=2))\n",
    )
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "cells": cells,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--catalog", default="")
    parser.add_argument("--schema", default="finance_portfolio")
    parser.add_argument("--input-path", default="")
    args = parser.parse_args()
    notebook = build_notebook(
        Path(__file__).resolve().parents[1], args.catalog, args.schema, args.input_path
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(notebook, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {len(notebook['cells'])} cells to {args.output}")


if __name__ == "__main__":
    main()
