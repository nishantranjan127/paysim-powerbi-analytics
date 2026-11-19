"""Currency-isolated analyst metrics over the local Silver snapshot."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from finance_lakehouse.rules import CURRENCIES, normalize_record


def load_transactions(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected a transaction object")
            normalized = normalize_record(row)
            if normalized["error_code"]:
                raise ValueError(f"{path}:{line_number}: {normalized['error_code']}")
            normalized.pop("error_code")
            records.append(normalized)
    identifiers = [row["transaction_id"] for row in records]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Silver snapshot contains duplicate transaction IDs; rerun the pipeline")
    return records


def analyze(
    transactions: list[dict], currency: str, start: date, end: date
) -> dict:
    if start > end:
        raise ValueError("Start date must not be after end date")
    if currency not in CURRENCIES:
        raise ValueError(f"Unsupported currency: {currency}")
    selected = [
        row for row in transactions
        if row["currency"] == currency
        and start.isoformat() <= row["transaction_date"] <= end.isoformat()
    ]
    posted = [row for row in selected if row["status"] == "POSTED"]
    declined = len(selected) - len(posted)
    inflow = sum(
        (Decimal(row["amount"]) for row in posted if row["transaction_type"] == "CREDIT"),
        Decimal(0),
    )
    outflow = sum(
        (Decimal(row["amount"]) for row in posted if row["transaction_type"] == "DEBIT"),
        Decimal(0),
    )
    daily = {}
    countries = {}
    for row in selected:
        day = daily.setdefault(row["transaction_date"], {
            "attempts": 0, "declined": 0, "inflow": Decimal(0), "outflow": Decimal(0),
        })
        day["attempts"] += 1
        day["declined"] += int(row["status"] == "DECLINED")
        if row["status"] == "POSTED":
            amount = Decimal(row["amount"])
            day["inflow" if row["transaction_type"] == "CREDIT" else "outflow"] += amount
            country = countries.setdefault(row["merchant_country"], {
                "posted_count": 0, "posted_amount": Decimal(0),
            })
            country["posted_count"] += 1
            country["posted_amount"] += amount
    return {
        "currency": currency,
        "attempts": len(selected),
        "posted_count": len(posted),
        "declined_count": declined,
        "decline_rate": Decimal(declined) / len(selected) if selected else None,
        "active_accounts": len({row["account_id"] for row in selected}),
        "inflow": inflow,
        "outflow": outflow,
        "net_flow": inflow - outflow,
        "average_posted_amount": (inflow + outflow) / len(posted) if posted else None,
        "daily": [
            {"date": day, **values, "net_flow": values["inflow"] - values["outflow"]}
            for day, values in sorted(daily.items())
        ],
        "countries": [
            {"country": country, **values}
            for country, values in sorted(
                countries.items(), key=lambda item: (-item[1]["posted_amount"], item[0])
            )
        ],
        "transactions": selected,
        "high_value_posted": sorted(
            (row for row in posted if row["is_high_value"]),
            key=lambda row: (-Decimal(row["amount"]), row["transaction_id"]),
        ),
    }
