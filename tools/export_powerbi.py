"""Export an editable local Power BI project; no cloud connection is required."""

import argparse
import json
from decimal import Decimal
from pathlib import Path

from finance_lakehouse.analytics import load_transactions


SCHEMAS = "https://developer.microsoft.com/json-schemas/fabric"
TABLE = "Transactions"


def literal(value: str) -> dict:
    return {"expr": {"Literal": {"Value": value}}}


def field(name: str, measure: bool = False, *, table: str = TABLE) -> dict:
    return {
        "Measure" if measure else "Column": {
            "Expression": {"SourceRef": {"Entity": table}}, "Property": name,
        }
    }


def projection(name: str, measure: bool = False, *, table: str = TABLE) -> dict:
    return {"field": field(name, measure, table=table), "queryRef": f"{table}.{name}"}


def visual(
    name: str, kind: str, title: str, bounds: tuple[int, int, int, int],
    roles: dict[str, list[dict]], objects: dict | None = None,
) -> dict:
    x, y, width, height = bounds
    if kind == "card":
        objects = {
            "labels": [{"properties": {
                "fontSize": literal("24D"), "labelDisplayUnits": literal("1D"),
            }}],
            "categoryLabels": [{"properties": {"show": literal("false")}}],
            **(objects or {}),
        }
    return {
        "$schema": f"{SCHEMAS}/item/report/definition/visualContainer/2.4.0/schema.json",
        "name": name,
        "position": {"x": x, "y": y, "width": width, "height": height, "tabOrder": y + x},
        "visual": {
            "visualType": kind,
            "query": {"queryState": {
                role: {"projections": values} for role, values in roles.items()
            }},
            "objects": objects or {},
            "visualContainerObjects": {
                "title": [{"properties": {
                    "show": literal("true" if title else "false"),
                    "text": literal("'" + title + "'"),
                    "fontSize": literal("12D"),
                }}],
                "background": [{"properties": {
                    "show": literal("true"),
                    "color": {"solid": {"color": literal("'#FFFFFF'")}},
                    "transparency": literal("0D"),
                }}],
            },
            "drillFilterOtherVisuals": True,
        },
    }


def currency_slicer() -> dict:
    return visual(
        "currency", "slicer", "Currency - choose one", (24, 72, 260, 100),
        {"Values": [projection("currency")]},
        {
            "selection": [{"properties": {"singleSelect": literal("true")}}],
            "data": [{"properties": {"mode": literal("'Dropdown'")}}],
            "header": [{"properties": {"show": literal("false")}}],
            "general": [{"properties": {
                "filter": {"filter": {
                    "Version": 2,
                    "From": [{"Name": "t", "Entity": TABLE, "Type": 0}],
                    "Where": [{"Condition": {"In": {
                        "Expressions": [{"Column": {
                            "Expression": {"SourceRef": {"Source": "t"}},
                            "Property": "currency",
                        }}],
                        "Values": [[{"Literal": {"Value": "'USD'"}}]],
                    }}}],
                }},
            }}],
        },
    )


def build_model(source: Path) -> dict:
    quoted_path = str(source.resolve()).replace('"', '""')
    columns = [
        {"name": name, "dataType": kind, "sourceColumn": name, "summarizeBy": "none"}
        for name, kind in (
            ("transaction_id", "string"), ("account_id", "string"),
            ("event_time", "string"), ("amount", "decimal"), ("currency", "string"),
            ("transaction_type", "string"), ("merchant_country", "string"),
            ("status", "string"), ("transaction_date", "dateTime"),
            ("is_high_value", "boolean"),
        )
    ]
    columns[3]["formatString"] = "#,0.00"
    columns[8]["formatString"] = "yyyy-MM-dd"
    measures = [
        ("Valid Attempts", "COUNTROWS(Transactions)", "#,0"),
        ("Posted Transactions",
         'CALCULATE([Valid Attempts], KEEPFILTERS(Transactions[status] = "POSTED"))', "#,0"),
        ("Declined Attempts",
         'CALCULATE([Valid Attempts], KEEPFILTERS(Transactions[status] = "DECLINED"))', "#,0"),
        ("Decline Rate", "DIVIDE([Declined Attempts], [Valid Attempts])", "0.00%"),
        ("Active Accounts", "DISTINCTCOUNT(Transactions[account_id])", "#,0"),
        ("Posted Inflow",
         'IF(HASONEVALUE(Transactions[currency]), COALESCE(CALCULATE(SUM(Transactions[amount]), '
         'KEEPFILTERS(Transactions[status] = "POSTED"), '
         'KEEPFILTERS(Transactions[transaction_type] = "CREDIT")), 0))', "#,0.00"),
        ("Posted Outflow",
         'IF(HASONEVALUE(Transactions[currency]), COALESCE(CALCULATE(SUM(Transactions[amount]), '
         'KEEPFILTERS(Transactions[status] = "POSTED"), '
         'KEEPFILTERS(Transactions[transaction_type] = "DEBIT")), 0))', "#,0.00"),
        ("Net Cash Flow",
         "IF(HASONEVALUE(Transactions[currency]), [Posted Inflow] - [Posted Outflow])", "#,0.00"),
        ("Posted Gross Activity",
         "IF(HASONEVALUE(Transactions[currency]), [Posted Inflow] + [Posted Outflow])", "#,0.00"),
        ("Average Posted Amount", "DIVIDE([Posted Gross Activity], [Posted Transactions])", "#,0.00"),
        ("Report Heading",
         '"FINANCE ANALYST STUDIO | " & SELECTEDVALUE(Transactions[currency], "SELECT ONE CURRENCY")',
         None),
        ("Report Note",
         '"SYNTHETIC DATA | UTC dates | Posted cash flow only | No FX conversion"', None),
    ]
    expression = [
        "let",
        f'    Source = Lines.FromBinary(File.Contents("{quoted_path}"), null, false, 65001),',
        "    Records = List.Transform(Source, each Json.Document(_)),",
        "    Rows = Table.FromRecords(Records),",
        "    Typed = Table.TransformColumnTypes(Rows, {",
        '        {"transaction_id", type text}, {"account_id", type text},',
        '        {"event_time", type text}, {"amount", Currency.Type}, {"currency", type text},',
        '        {"transaction_type", type text}, {"merchant_country", type text},',
        '        {"status", type text}, {"transaction_date", type date},',
        '        {"is_high_value", type logical}',
        '    }, "en-US")',
        "in",
        "    Typed",
    ]
    return {
        "name": "FinanceAnalystStudio", "compatibilityLevel": 1600,
        "model": {
            "culture": "en-US", "defaultPowerBIDataSourceVersion": "powerBI_V3",
            "sourceQueryCulture": "en-US",
            "dataAccessOptions": {"legacyRedirects": True, "returnErrorValuesAsNull": False},
            "tables": [{
                "name": TABLE, "columns": columns,
                "measures": [
                    {"name": name, "expression": dax, **({"formatString": fmt} if fmt else {})}
                    for name, dax, fmt in measures
                ],
                "partitions": [{
                    "name": TABLE, "mode": "import",
                    "source": {"type": "m", "expression": expression},
                }],
            }],
        },
    }


def build_project(source: Path) -> dict[str, dict]:
    records = load_transactions(source)
    if not records:
        raise ValueError("Silver snapshot is empty; provide valid transactions before exporting")
    if any(Decimal(row["amount"]) > Decimal("922337203685477.5807") for row in records):
        raise ValueError("An amount exceeds Power BI fixed-decimal capacity")
    report_path = "FinanceAnalystStudio.Report"
    model_path = "FinanceAnalystStudio.SemanticModel"
    files = {
        "Finance Analyst Studio.pbip": {
            "$schema": f"{SCHEMAS}/pbip/pbipProperties/1.0.0/schema.json",
            "version": "1.0",
            "artifacts": [{"report": {"path": report_path}}],
            "settings": {"enableAutoRecovery": True},
        },
        f"{report_path}/definition.pbir": {
            "$schema": f"{SCHEMAS}/item/report/definitionProperties/2.0.0/schema.json",
            "version": "4.0", "datasetReference": {"byPath": {"path": f"../{model_path}"}},
        },
        f"{model_path}/definition.pbism": {
            "$schema": f"{SCHEMAS}/item/semanticModel/definitionProperties/1.0.0/schema.json",
            "version": "1.0", "settings": {"qnaEnabled": False},
        },
        f"{model_path}/model.bim": build_model(source),
        f"{report_path}/definition/version.json": {
            "$schema": f"{SCHEMAS}/item/report/definition/versionMetadata/1.0.0/schema.json",
            "version": "2.0.0",
        },
        f"{report_path}/definition/report.json": {
            "$schema": f"{SCHEMAS}/item/report/definition/report/3.0.0/schema.json",
            "themeCollection": {},
        },
        f"{report_path}/definition/pages/pages.json": {
            "$schema": f"{SCHEMAS}/item/report/definition/pagesMetadata/1.0.0/schema.json",
            "pageOrder": ["overview", "transactions"], "activePageName": "overview",
        },
    }
    for page_name, display_name in (("overview", "Financial overview"), ("transactions", "Transaction analysis")):
        page_path = f"{report_path}/definition/pages/{page_name}"
        files[f"{page_path}/page.json"] = {
            "$schema": f"{SCHEMAS}/item/report/definition/page/2.0.0/schema.json",
            "name": page_name, "displayName": display_name,
            "displayOption": "FitToPage", "width": 1280, "height": 720,
        }
        visuals = [
            visual("heading", "card", "", (24, 8, 1232, 60),
                   {"Values": [projection("Report Heading", True)]},
                   {"labels": [{"properties": {"fontSize": literal("20D")}}]}),
            currency_slicer(),
            visual("dates", "slicer", "Transaction date (UTC)", (308, 72, 540, 100),
                   {"Values": [projection("transaction_date")]},
                   {"data": [{"properties": {"mode": literal("'Between'")}}]}),
            visual("note", "card", "", (24, 665, 1232, 50),
                   {"Values": [projection("Report Note", True)]},
                   {"labels": [{"properties": {"fontSize": literal("12D")}}]}),
        ]
        if page_name == "overview":
            for index, measure in enumerate(
                ("Posted Inflow", "Posted Outflow", "Net Cash Flow", "Decline Rate")
            ):
                visuals.append(visual(
                    f"kpi{index}", "card", measure, (24 + index * 312, 184, 296, 112),
                    {"Values": [projection(measure, True)]},
                ))
            visuals.extend([
                visual("cashflow", "lineChart", "Daily posted cash flow", (24, 312, 748, 337), {
                    "Category": [projection("transaction_date")],
                    "Y": [projection(name, True) for name in ("Posted Inflow", "Posted Outflow", "Net Cash Flow")],
                }),
                visual("countries", "clusteredBarChart", "Posted gross activity by merchant country",
                       (796, 312, 460, 337), {
                           "Category": [projection("merchant_country")],
                           "Y": [projection("Posted Gross Activity", True)],
                       }),
            ])
        else:
            for index, measure in enumerate(
                ("Valid Attempts", "Posted Transactions", "Declined Attempts", "Active Accounts")
            ):
                visuals.append(visual(
                    f"count{index}", "card", measure, (24 + index * 312, 184, 296, 100),
                    {"Values": [projection(measure, True)]},
                ))
            visuals.append(visual(
                "details", "tableEx", "Valid deduplicated transactions - amount in row currency",
                (24, 300, 1232, 349), {
                    "Values": [projection(name) for name in (
                        "transaction_id", "account_id", "transaction_date", "currency",
                        "amount", "transaction_type", "status", "merchant_country", "is_high_value",
                    )],
                },
            ))
        for item in visuals:
            files[f"{page_path}/visuals/{item['name']}/visual.json"] = item
    return files


def write_project(files: dict[str, dict], output: Path, entrypoint: str) -> Path:
    output.mkdir(parents=True, exist_ok=False)
    for relative, content in files.items():
        path = output.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content, indent=2) + "\n", encoding="utf-8")
    return output / entrypoint


def export_project(source: Path, output: Path) -> Path:
    return write_project(build_project(source), output, "Finance Analyst Studio.pbip")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data") / "output" / "silver_transactions.jsonl")
    parser.add_argument("--output", type=Path, default=Path("powerbi"))
    args = parser.parse_args()
    print(f"Created {export_project(args.input, args.output)}. Open in Power BI Desktop and click Refresh.")


if __name__ == "__main__":
    main()
