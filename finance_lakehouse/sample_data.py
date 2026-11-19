import csv
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from finance_lakehouse.rules import FIELDS


def generate(path: Path, rows: int = 1000, seed: int = 42) -> int:
    """Write synthetic transactions plus six invalid and two duplicate records."""
    if rows < 2:
        raise ValueError("rows must be at least 2")
    rng = random.Random(seed)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    records = []
    for index in range(rows):
        cents = rng.randint(1, 2_000_000)
        records.append(
            {
                "transaction_id": f"TX-{index:08d}",
                "account_id": f"AC-{rng.randrange(100):04d}",
                "event_time": (start + timedelta(minutes=index * 17)).isoformat(),
                "amount": f"{cents // 100}.{cents % 100:02d}",
                "currency": rng.choice(["USD", "EUR", "GBP", "INR"]),
                "transaction_type": rng.choice(["DEBIT", "CREDIT"]),
                "merchant_country": rng.choice(["US", "GB", "IN", "DE"]),
                "status": "DECLINED" if index % 13 == 0 else "POSTED",
            }
        )
    records.append(dict(records[0]))
    records.append(
        {**records[1], "event_time": (start + timedelta(days=30)).isoformat(), "amount": "12500.00"}
    )
    invalid = (
        {"account_id": ""},
        {"amount": "-12.00"},
        {"amount": "NaN"},
        {"currency": "XYZ"},
        {"event_time": "not-a-timestamp"},
        {"status": "UNKNOWN"},
    )
    for index, changes in enumerate(invalid):
        records.append({**records[0], "transaction_id": f"BAD-{index}", **changes})
    rng.shuffle(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(records)
    return len(records)
