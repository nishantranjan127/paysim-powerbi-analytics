"""Export a separate Power BI star-schema report over prepared Kaggle PaySim data."""

import argparse
import json
from pathlib import Path

from tools.export_powerbi import SCHEMAS, field, literal, projection, visual, write_project


REPORT = "PaySimFraudAnalytics.Report"
MODEL = "PaySimFraudAnalytics.SemanticModel"
ENTRYPOINT = "PaySim Fraud Analytics.pbip"
TABLES = {
    "Activity": ("activity_summary", (
        ("step", "int64"), ("transaction_type", "string"), ("is_fraud", "int64"),
        ("is_flagged", "int64"), ("transaction_count", "int64"), ("total_amount", "decimal"),
    )),
    "SimulationHours": ("simulation_hours", (
        ("step", "int64"), ("simulation_day", "int64"), ("hour_in_day", "int64"),
    )),
    "TransactionTypes": ("transaction_types", (("transaction_type", "string"),)),
    "FraudDetails": ("fraud_details", (
        ("source_row_id", "int64"), ("step", "int64"), ("transaction_type", "string"),
        ("amount", "decimal"), ("originator_id", "string"), ("recipient_id", "string"),
        ("is_flagged", "int64"),
    )),
    "AccountProfiles": ("account_profiles", (
        ("profile_rank", "int64"), ("originator_id", "string"), ("transaction_count", "int64"),
        ("total_amount", "decimal"), ("fraud_count", "int64"), ("fraud_amount", "decimal"),
        ("first_step", "int64"), ("last_step", "int64"),
    )),
}


def column(name: str, table: str) -> dict:
    return projection(name, table=table)


def measure(name: str) -> dict:
    return projection(name, True, table="Activity")


def build_model(prepared: Path, metadata: dict) -> dict:
    metrics = metadata["metrics"]
    measures = [
        ("Transactions", "COALESCE(SUM(Activity[transaction_count]), 0)", "#,0"),
        ("Transaction Amount", "COALESCE(SUM(Activity[total_amount]), 0)", "#,0.00"),
        ("Fraud Transactions",
         "CALCULATE([Transactions], KEEPFILTERS(Activity[is_fraud] = 1))", "#,0"),
        ("Fraud Amount",
         "CALCULATE([Transaction Amount], KEEPFILTERS(Activity[is_fraud] = 1))", "#,0.00"),
        ("Fraud Rate", "DIVIDE([Fraud Transactions], [Transactions])", "0.0000%"),
        ("Flagged Transactions",
         "CALCULATE([Transactions], KEEPFILTERS(Activity[is_flagged] = 1))", "#,0"),
        ("True Positives",
         "CALCULATE([Transactions], KEEPFILTERS(Activity[is_fraud] = 1), "
         "KEEPFILTERS(Activity[is_flagged] = 1))", "#,0"),
        ("False Positives",
         "CALCULATE([Transactions], KEEPFILTERS(Activity[is_fraud] = 0), "
         "KEEPFILTERS(Activity[is_flagged] = 1))", "#,0"),
        ("False Negatives",
         "CALCULATE([Transactions], KEEPFILTERS(Activity[is_fraud] = 1), "
         "KEEPFILTERS(Activity[is_flagged] = 0))", "#,0"),
        ("True Negatives",
         "CALCULATE([Transactions], KEEPFILTERS(Activity[is_fraud] = 0), "
         "KEEPFILTERS(Activity[is_flagged] = 0))", "#,0"),
        ("Flag Precision", "DIVIDE([True Positives], [Flagged Transactions])", "0.00%"),
        ("Flag Recall", "DIVIDE([True Positives], [Fraud Transactions])", "0.0000%"),
        ("Originators (snapshot)", str(metrics["distinct_originators"]), "#,0"),
        ("Recipients (snapshot)", str(metrics["distinct_recipients"]), "#,0"),
        ("Profiled Accounts", "COUNTROWS(AccountProfiles)", "#,0"),
        ("Profiled Transactions", "SUM(AccountProfiles[transaction_count])", "#,0"),
        ("Report Heading", '"PAYSIM | TRANSACTION & FRAUD ANALYTICS"', None),
        ("Report Note", '"SYNTHETIC | Simulation hours, not dates | Unspecified local currency | '
         'Fraud amount is not realized loss"', None),
        ("Account Note", '"TOP 1,000 ORIGINATORS by count, then amount, then ID | '
         'Whole snapshot; no time/type slicers"', None),
    ]
    tables = []
    for name, (filename, columns) in TABLES.items():
        path = str((prepared / f"{filename}.parquet").resolve()).replace('"', '""')
        conversions = ", ".join(
            '{"' + prop + '", ' + {"int64": "Int64.Type", "decimal": "Currency.Type", "string": "type text"}[kind] + "}"
            for prop, kind in columns
        )
        table = {
            "name": name,
            "columns": [
                {"name": prop, "sourceColumn": prop, "dataType": kind, "summarizeBy": "none",
                 "formatString": "#,0.00" if kind == "decimal" else "0"}
                if kind != "string" else
                {"name": prop, "sourceColumn": prop, "dataType": kind, "summarizeBy": "none"}
                for prop, kind in columns
            ],
            "partitions": [{
                "name": name, "mode": "import", "source": {"type": "m", "expression": [
                    "let", f'    Source = Parquet.Document(File.Contents("{path}")),',
                    f'    Typed = Table.TransformColumnTypes(Source, {{{conversions}}}, "en-US")',
                    "in", "    Typed",
                ]},
            }],
        }
        if name == "Activity":
            table["measures"] = [
                {"name": prop, "expression": dax, **({"formatString": fmt} if fmt else {})}
                for prop, dax, fmt in measures
            ]
        tables.append(table)
    relationships = [
        {
            "name": f"{fact}_{dimension}", "fromTable": fact, "fromColumn": key,
            "toTable": dimension, "toColumn": key, "fromCardinality": "many",
            "toCardinality": "one", "crossFilteringBehavior": "oneDirection",
        }
        for fact in ("Activity", "FraudDetails")
        for dimension, key in (("SimulationHours", "step"), ("TransactionTypes", "transaction_type"))
    ]
    return {
        "name": "PaySimFraudAnalytics", "compatibilityLevel": 1600,
        "model": {
            "culture": "en-US", "sourceQueryCulture": "en-US",
            "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "dataAccessOptions": {"legacyRedirects": True, "returnErrorValuesAsNull": False},
            "tables": tables, "relationships": relationships,
        },
    }


def report_visual(name, kind, title, bounds, roles, objects=None):
    item = visual(name, kind, title, bounds, roles, objects)
    item["visual"]["visualContainerObjects"]["title"][0]["properties"]["fontColor"] = {
        "solid": {"color": literal("'#243746'")},
    }
    return item


def card(name, measure_name, title, bounds, size=24):
    return report_visual(
        name, "card", title, bounds, {"Values": [measure(measure_name)]},
        {"labels": [{"properties": {
            "fontSize": literal(f"{size}D"), "labelDisplayUnits": literal("1D"),
            "color": {"solid": {"color": literal("'#15324F'")}},
        }}]},
    )


def build_project(prepared: Path) -> dict[str, dict]:
    metadata = json.loads((prepared / "validation.json").read_text(encoding="utf-8"))
    if metadata["metrics"]["transactions"] <= 0:
        raise ValueError("Prepare a nonempty PaySim dataset first")
    for filename, _ in TABLES.values():
        if not (prepared / f"{filename}.parquet").is_file():
            raise FileNotFoundError(prepared / f"{filename}.parquet")
    pages = [
        ("overview", "Transaction overview"),
        ("fraud", "Fraud labels and rule performance"),
        ("accounts", "Top originators - snapshot"),
    ]
    files = {
        ENTRYPOINT: {
            "$schema": f"{SCHEMAS}/pbip/pbipProperties/1.0.0/schema.json", "version": "1.0",
            "artifacts": [{"report": {"path": REPORT}}], "settings": {"enableAutoRecovery": True},
        },
        f"{REPORT}/definition.pbir": {
            "$schema": f"{SCHEMAS}/item/report/definitionProperties/2.0.0/schema.json",
            "version": "4.0", "datasetReference": {"byPath": {"path": f"../{MODEL}"}},
        },
        f"{MODEL}/definition.pbism": {
            "$schema": f"{SCHEMAS}/item/semanticModel/definitionProperties/1.0.0/schema.json",
            "version": "1.0", "settings": {"qnaEnabled": False},
        },
        f"{MODEL}/model.bim": build_model(prepared, metadata),
        f"{REPORT}/definition/version.json": {
            "$schema": f"{SCHEMAS}/item/report/definition/versionMetadata/1.0.0/schema.json", "version": "2.0.0",
        },
        f"{REPORT}/definition/report.json": {
            "$schema": f"{SCHEMAS}/item/report/definition/report/3.0.0/schema.json", "themeCollection": {},
        },
        f"{REPORT}/definition/pages/pages.json": {
            "$schema": f"{SCHEMAS}/item/report/definition/pagesMetadata/1.0.0/schema.json",
            "pageOrder": [name for name, _ in pages], "activePageName": "overview",
        },
    }
    for page_name, display_name in pages:
        page_path = f"{REPORT}/definition/pages/{page_name}"
        files[f"{page_path}/page.json"] = {
            "$schema": f"{SCHEMAS}/item/report/definition/page/2.0.0/schema.json",
            "name": page_name, "displayName": display_name,
            "displayOption": "FitToPage", "width": 1280, "height": 720,
        }
        visuals = [
            card("heading", "Report Heading", "", (24, 8, 1232, 60), 20),
            card("note", "Account Note" if page_name == "accounts" else "Report Note",
                 "", (24, 665, 1232, 50), 11),
        ]
        if page_name != "accounts":
            visuals.extend([
                report_visual(
                    "types", "slicer", "Transaction type", (24, 72, 360, 95),
                    {"Values": [column("transaction_type", "TransactionTypes")]},
                    {"data": [{"properties": {"mode": literal("'Dropdown'")}}],
                     "header": [{"properties": {"show": literal("false")}}]},
                ),
                report_visual(
                    "hours", "slicer", "Simulation hour (step) - no calendar date", (408, 72, 650, 95),
                    {"Values": [column("step", "SimulationHours")]},
                    {"data": [{"properties": {"mode": literal("'Between'")}}]},
                ),
            ])
        kpis = {
            "overview": [("Transactions", "Transactions"), ("Transaction Amount", "Amount (local units)"),
                         ("Fraud Transactions", "Fraud-labelled transactions"), ("Fraud Rate", "Fraud label rate")],
            "fraud": [("Fraud Transactions", "Fraud-labelled transactions"), ("Fraud Amount", "Fraud-labelled amount"),
                      ("Flag Precision", "Supplied rule precision"), ("Flag Recall", "Supplied rule recall")],
            "accounts": [("Originators (snapshot)", "Originators - whole snapshot"),
                         ("Recipients (snapshot)", "Recipients - whole snapshot"),
                         ("Profiled Accounts", "Top originators profiled"),
                         ("Profiled Transactions", "Transactions in this cohort")],
        }[page_name]
        for index, (measure_name, title) in enumerate(kpis):
            visuals.append(card(
                f"kpi{index}", measure_name, title,
                (24 + index * 312, 184 if page_name != "accounts" else 90, 296, 112),
                20 if "Amount" in measure_name else 24,
            ))
        if page_name == "overview":
            trend = report_visual("volume", "lineChart", "Transactions by simulation hour",
                                 (24, 312, 748, 337), {
                                     "Category": [column("step", "SimulationHours")],
                                     "Y": [measure("Transactions")],
                                 })
            trend["visual"]["query"]["sortDefinition"] = {
                "sort": [{"field": field("step", table="SimulationHours"), "direction": "Ascending"}],
            }
            visuals.extend([
                trend,
                report_visual("bytype", "clusteredBarChart", "Transaction amount by type",
                              (796, 312, 460, 337), {
                                  "Category": [column("transaction_type", "TransactionTypes")],
                                  "Y": [measure("Transaction Amount")],
                              }),
            ])
        elif page_name == "fraud":
            visuals.extend([
                report_visual("confusion", "tableEx", "Supplied isFlaggedFraud rule versus simulator labels",
                              (24, 304, 1232, 105), {"Values": [
                                  measure(name) for name in
                                  ("Flagged Transactions", "True Positives", "False Positives",
                                   "False Negatives", "True Negatives")
                              ]}),
                report_visual("cases", "tableEx", "All fraud-labelled cases - not a sample; source row is a surrogate",
                              (24, 425, 1232, 224), {"Values": [
                                  column(name, "FraudDetails") for name, _ in TABLES["FraudDetails"][1]
                              ]}),
            ])
        else:
            profiles = report_visual(
                "profiles", "tableEx", "Top 1,000 originators by count, then amount, then ID - full snapshot",
                (24, 224, 1232, 425),
                {"Values": [column(name, "AccountProfiles") for name, _ in TABLES["AccountProfiles"][1]]},
            )
            profiles["visual"]["query"]["sortDefinition"] = {
                "sort": [{"field": field("profile_rank", table="AccountProfiles"), "direction": "Ascending"}],
            }
            visuals.append(profiles)
        for item in visuals:
            files[f"{page_path}/visuals/{item['name']}/visual.json"] = item
    return files


def export_project(prepared: Path, output: Path) -> Path:
    return write_project(build_project(prepared), output, ENTRYPOINT)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data") / "paysim")
    parser.add_argument("--output", type=Path, default=Path("powerbi-paysim"))
    args = parser.parse_args()
    print(f"Created {export_project(args.data, args.output)}. Open in Power BI Desktop and click Refresh.")


if __name__ == "__main__":
    main()
