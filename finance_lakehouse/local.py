"""Dependency-free reference execution; cloud storage uses Delta, not these JSON files."""

import csv
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from finance_lakehouse.rules import FIELDS, normalize_record, winner_key


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(FIELDS):
            raise ValueError(f"CSV header must be exactly {','.join(FIELDS)}")
        rows = list(reader)
    if not rows:
        raise ValueError("Input must contain at least one transaction")
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("Malformed CSV: row width does not match the header")
    return rows


def transform(rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    winners = {}
    quarantine = []
    for raw in rows:
        normalized = normalize_record(raw)
        if normalized["error_code"]:
            quarantine.append({"raw": raw, "reason": normalized["error_code"]})
            continue
        normalized.pop("error_code")
        identifier = normalized["transaction_id"]
        previous = winners.get(identifier)
        if previous is None:
            winners[identifier] = (normalized, raw)
        elif winner_key(normalized) > winner_key(previous[0]):
            quarantine.append({"raw": previous[1], "reason": "duplicate_transaction"})
            winners[identifier] = (normalized, raw)
        else:
            quarantine.append({"raw": raw, "reason": "duplicate_transaction"})
    silver = sorted((value[0] for value in winners.values()), key=lambda row: row["transaction_id"])
    groups = {}
    for row in silver:
        key = (row["transaction_date"], row["currency"], row["transaction_type"], row["status"])
        if key not in groups:
            groups[key] = {"transaction_count": 0, "total_amount": Decimal(0), "high_value_count": 0}
        group = groups[key]
        group["transaction_count"] += 1
        group["total_amount"] += Decimal(row["amount"])
        group["high_value_count"] += int(row["is_high_value"])
    gold = [
        {
            "transaction_date": key[0],
            "currency": key[1],
            "transaction_type": key[2],
            "status": key[3],
            **metrics,
            "total_amount": format(metrics["total_amount"], ".2f"),
        }
        for key, metrics in sorted(groups.items())
    ]
    return silver, quarantine, gold


def run(input_path: Path, output_path: Path) -> dict:
    rows = read_csv(input_path)
    silver, quarantine, gold = transform(rows)
    if len(rows) != len(silver) + len(quarantine):
        raise RuntimeError("Row reconciliation failed")
    report = {
        "bronze_rows": len(rows),
        "silver_rows": len(silver),
        "quarantine_rows": len(quarantine),
        "gold_groups": len(gold),
        "quarantine_reasons": dict(sorted(Counter(row["reason"] for row in quarantine).items())),
    }
    output_path.mkdir(parents=True, exist_ok=True)
    for name, records in (
        ("bronze_transactions", rows),
        ("silver_transactions", silver),
        ("quarantine_transactions", quarantine),
        ("gold_daily_metrics", gold),
    ):
        with (output_path / f"{name}.jsonl").open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
    (output_path / "quality_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
