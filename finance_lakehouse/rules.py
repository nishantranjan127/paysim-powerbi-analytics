"""Shared validation contract used by the local runner and Spark workers."""

import re
from datetime import datetime, timezone
from decimal import Decimal

FIELDS = (
    "transaction_id",
    "account_id",
    "event_time",
    "amount",
    "currency",
    "transaction_type",
    "merchant_country",
    "status",
)
CURRENCIES = {"USD", "EUR", "GBP", "INR"}
TIE_BREAK_FIELDS = (
    "event_time",
    "account_id",
    "amount",
    "currency",
    "transaction_type",
    "merchant_country",
    "status",
)


def normalize_record(raw: dict) -> dict:
    """Return canonical data or a specific quarantine reason; never round money."""
    row = {field: str(raw.get(field) or "").strip() for field in FIELDS}
    if any(not value for value in row.values()):
        return {"error_code": "missing_required_field"}
    for field in ("transaction_id", "account_id"):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", row[field]):
            return {"error_code": f"invalid_{field}"}
    for field in ("currency", "transaction_type", "merchant_country", "status"):
        row[field] = row[field].upper()
    if row["currency"] not in CURRENCIES:
        return {"error_code": "unsupported_currency"}
    if row["transaction_type"] not in {"DEBIT", "CREDIT"}:
        return {"error_code": "invalid_transaction_type"}
    if row["status"] not in {"POSTED", "DECLINED"}:
        return {"error_code": "invalid_status"}
    if not re.fullmatch(r"[A-Z]{2}", row["merchant_country"]):
        return {"error_code": "invalid_country"}
    if not re.fullmatch(r"[0-9]{1,16}(?:\.[0-9]{1,2})?", row["amount"]):
        return {"error_code": "invalid_amount"}
    amount = Decimal(row["amount"])
    if amount <= 0:
        return {"error_code": "invalid_amount"}
    # Require an explicit offset rather than guessing the source time zone.
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])",
        row["event_time"],
    ):
        return {"error_code": "invalid_event_time"}
    try:
        timestamp = datetime.fromisoformat(row["event_time"].replace("Z", "+00:00"))
        timestamp = timestamp.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return {"error_code": "invalid_event_time"}
    # Keep local JSON, Spark timestamp and date conversions in the same range.
    if timestamp.year < 1900 or timestamp.year > 2100:
        return {"error_code": "invalid_event_time"}
    row["event_time"] = timestamp.isoformat(timespec="microseconds")
    row["amount"] = format(amount, ".2f")
    return {
        **row,
        "transaction_date": timestamp.date().isoformat(),
        "is_high_value": row["currency"] == "USD" and amount >= Decimal("10000"),
        "error_code": None,
    }


def winner_key(row: dict) -> tuple:
    """Latest event wins; canonical lexical fields break equal-time conflicts."""
    return tuple(row[field] for field in TIE_BREAK_FIELDS)
