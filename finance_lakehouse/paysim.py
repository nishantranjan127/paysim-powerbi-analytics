"""Validate the full Kaggle PaySim CSV/ZIP and prepare a compact Power BI model."""

import argparse
import csv
import hashlib
import json
import re
import shutil
import tempfile
from decimal import Decimal, localcontext
from pathlib import Path
from zipfile import ZipFile


SOURCE_URL = "https://www.kaggle.com/datasets/ealaxi/paysim1"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
FIELDS = (
    "step", "type", "amount", "nameOrig", "oldbalanceOrg", "newbalanceOrig",
    "nameDest", "oldbalanceDest", "newbalanceDest", "isFraud", "isFlaggedFraud",
)
MONEY_FIELDS = ("amount", "oldbalanceOrg", "newbalanceOrig", "oldbalanceDest", "newbalanceDest")
POWERBI_MONEY_MAX = "922337203685477.5807"


def normalize_scientific_money(value: str) -> str | None:
    if value is None or len(value) > 64 or not re.fullmatch(
        r"[0-9]+(?:\.[0-9]+)?[eE][+-]?[0-9]+", value
    ):
        return None
    if abs(int(re.split("[eE]", value)[1])) > 24:
        return None
    number = Decimal(value)
    if not 0 <= number <= Decimal("9999999999999999.99"):
        return None
    with localcontext() as context:
        context.prec = 38
        cents = number.quantize(Decimal("0.01"))
    return format(cents, "f") if cents == number else None


def sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def prepare(source: Path, output: Path) -> Path:
    import duckdb

    source, output = source.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite prepared data: {output}")
    if not source.is_file():
        raise FileNotFoundError(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="paysim-", dir=output.parent) as directory:
        work = Path(directory)
        csv_path = source
        if source.suffix.lower() == ".zip":
            with ZipFile(source) as archive:
                members = [info for info in archive.infolist()
                           if not info.is_dir() and info.filename.lower().endswith(".csv")]
                if len(members) != 1:
                    raise ValueError("PaySim archive must contain exactly one CSV")
                csv_path = work / "source.csv"
                with archive.open(members[0]) as original, csv_path.open("wb") as target:
                    shutil.copyfileobj(original, target, length=1024 * 1024)
        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            if next(csv.reader(handle), None) != list(FIELDS):
                raise ValueError("CSV header does not match the original PaySim schema")
        staged = work / "prepared"
        staged.mkdir()
        with duckdb.connect(str(work / "prepare.duckdb")) as connection:
            connection.execute("SET memory_limit = '512MB'")
            connection.execute("SET threads = 2")
            connection.execute(f"SET temp_directory = {sql_string(work / 'spill')}")
            connection.execute(
                "CREATE TABLE raw AS SELECT row_number() OVER () AS source_row_id, * "
                "FROM read_csv(?, header=true, all_varchar=true, strict_mode=true)",
                [str(csv_path)],
            )
            connection.create_function(
                "normalize_scientific_money", normalize_scientific_money,
                ["VARCHAR"], "VARCHAR", null_handling="special",
            )
            for name in MONEY_FIELDS:
                connection.execute(
                    f"UPDATE raw SET {name} = normalize_scientific_money({name}) "
                    f"WHERE contains(lower({name}), 'e')"
                )
            money_checks = " AND ".join(
                f"regexp_full_match({name}, '[0-9]+(\\.[0-9]{{1,2}})?') "
                f"AND try_cast({name} AS DECIMAL(18,2)) IS NOT NULL"
                for name in MONEY_FIELDS
            )
            valid = (
                "regexp_full_match(step, '[0-9]+') AND try_cast(step AS INTEGER) BETWEEN 1 AND 744 "
                "AND type IN ('CASH_IN','CASH_OUT','DEBIT','PAYMENT','TRANSFER') "
                "AND regexp_full_match(nameOrig, 'C[0-9]+') "
                "AND regexp_full_match(nameDest, '[CM][0-9]+') "
                "AND isFraud IN ('0','1') AND isFlaggedFraud IN ('0','1') AND " + money_checks
            )
            invalid = connection.execute(
                f"SELECT source_row_id FROM raw WHERE ({valid}) IS NOT TRUE LIMIT 1"
            ).fetchone()
            if invalid:
                raise ValueError(f"Invalid PaySim values at CSV data row {invalid[0]}; no rows were dropped")
            connection.execute("""
                CREATE TABLE transactions AS SELECT source_row_id,
                    cast(step AS INTEGER) AS step, type AS transaction_type,
                    cast(amount AS DECIMAL(18,2)) AS amount,
                    nameOrig AS originator_id, nameDest AS recipient_id,
                    cast(oldbalanceOrg AS DECIMAL(18,2)) AS old_balance_originator,
                    cast(newbalanceOrig AS DECIMAL(18,2)) AS new_balance_originator,
                    cast(oldbalanceDest AS DECIMAL(18,2)) AS old_balance_recipient,
                    cast(newbalanceDest AS DECIMAL(18,2)) AS new_balance_recipient,
                    cast(isFraud AS INTEGER) AS is_fraud,
                    cast(isFlaggedFraud AS INTEGER) AS is_flagged
                FROM raw
            """)
            connection.execute("DROP TABLE raw")
            totals = connection.execute("""
                SELECT count(*) AS transactions, sum(amount) AS transaction_amount,
                    sum(is_fraud) AS fraud_transactions,
                    sum(CASE WHEN is_fraud=1 THEN amount ELSE 0 END) AS fraud_amount,
                    sum(is_flagged) AS flagged_transactions,
                    count(*) FILTER (WHERE is_fraud=1 AND is_flagged=1) AS true_positives,
                    count(*) FILTER (WHERE is_fraud=0 AND is_flagged=1) AS false_positives,
                    count(*) FILTER (WHERE is_fraud=1 AND is_flagged=0) AS false_negatives,
                    count(*) FILTER (WHERE is_fraud=0 AND is_flagged=0) AS true_negatives,
                    count(DISTINCT originator_id) AS distinct_originators,
                    count(DISTINCT recipient_id) AS distinct_recipients,
                    min(step) AS first_step, max(step) AS last_step
                FROM transactions
            """)
            metrics = dict(zip([column[0] for column in totals.description], totals.fetchone()))
            if not metrics["transactions"]:
                raise ValueError("PaySim CSV is empty")
            if connection.execute(
                f"SELECT sum(amount) > {POWERBI_MONEY_MAX} FROM transactions"
            ).fetchone()[0]:
                raise ValueError("Transaction amount total exceeds Power BI fixed-decimal capacity")
            connection.execute("""
                CREATE TABLE activity_summary AS SELECT step, transaction_type, is_fraud, is_flagged,
                    count(*) AS transaction_count, sum(amount)::DECIMAL(18,2) AS total_amount
                FROM transactions GROUP BY ALL
            """)
            reconciliation = connection.execute("""
                SELECT sum(transaction_count), sum(total_amount),
                    sum(transaction_count) FILTER (WHERE is_fraud=1),
                    sum(total_amount) FILTER (WHERE is_fraud=1),
                    sum(transaction_count) FILTER (WHERE is_flagged=1)
                FROM activity_summary
            """).fetchone()
            expected = tuple(metrics[key] for key in (
                "transactions", "transaction_amount", "fraud_transactions", "fraud_amount", "flagged_transactions",
            ))
            actual = tuple(value if value is not None else 0 for value in reconciliation)
            if actual != expected:
                raise ValueError(f"Summary reconciliation failed: {actual} != {expected}")
            connection.execute("""
                CREATE TABLE simulation_hours AS SELECT DISTINCT step,
                    ((step - 1) // 24 + 1)::INTEGER AS simulation_day,
                    ((step - 1) % 24)::INTEGER AS hour_in_day
                FROM transactions ORDER BY step
            """)
            connection.execute("""
                CREATE TABLE transaction_types AS SELECT DISTINCT transaction_type
                FROM transactions ORDER BY transaction_type
            """)
            connection.execute("""
                CREATE TABLE fraud_details AS SELECT source_row_id, step, transaction_type,
                    amount, originator_id, recipient_id, is_flagged
                FROM transactions WHERE is_fraud=1 ORDER BY source_row_id
            """)
            connection.execute("""
                CREATE TABLE account_profiles AS
                WITH accounts AS (
                    SELECT originator_id, count(*) AS transaction_count,
                        sum(amount)::DECIMAL(18,2) AS total_amount,
                        sum(is_fraud) AS fraud_count,
                        sum(CASE WHEN is_fraud=1 THEN amount ELSE 0 END)::DECIMAL(18,2) AS fraud_amount,
                        min(step) AS first_step, max(step) AS last_step
                    FROM transactions GROUP BY originator_id
                )
                SELECT row_number() OVER (
                    ORDER BY transaction_count DESC, total_amount DESC, originator_id
                ) AS profile_rank, * FROM accounts
                ORDER BY profile_rank LIMIT 1000
            """)
            row_counts = {}
            for table in (
                "transactions", "activity_summary", "simulation_hours",
                "transaction_types", "fraud_details", "account_profiles",
            ):
                destination = staged / f"{table}.parquet"
                connection.execute(
                    f"COPY {table} TO {sql_string(destination)} (FORMAT PARQUET, COMPRESSION ZSTD)"
                )
                row_counts[table] = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            type_totals = connection.execute("""
                SELECT transaction_type, count(*), sum(amount), sum(is_fraud)
                FROM transactions GROUP BY transaction_type ORDER BY transaction_type
            """).fetchall()
        metadata = {
            "source": SOURCE_URL, "dataset_version": 2,
            "creator": "Edgar Lopez-Rojas", "license": "CC BY-SA 4.0", "license_url": LICENSE_URL,
            "input_file": str(source), "input_sha256": sha256(source), "csv_sha256": sha256(csv_path),
            "row_counts": row_counts,
            "metrics": {key: str(value) if key.endswith("amount") else value for key, value in metrics.items()},
            "by_type": [
                {"transaction_type": kind, "transactions": count, "amount": str(amount), "fraud": fraud}
                for kind, count, amount, fraud in type_totals
            ],
            "semantics": {
                "time": "step is a simulation hour; day=(step-1)//24+1; no calendar date or timezone",
                "amount": "unspecified local currency, not USD/INR; fraud amount is not realized loss",
                "accounts": "top 1000 originators by count, then amount, then ID; whole-snapshot cohort",
                "labels": "isFraud is simulator ground truth; isFlaggedFraud is a separate supplied rule",
                "changes": "renamed fields; exact decimal types; source-row surrogate; summaries and top-account cohort",
                "license": "Derived data is shared under CC BY-SA 4.0; retain attribution and license.",
            },
        }
        manifest = staged / "validation.json"
        manifest.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        staged.rename(output)
    return output / "validation.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("data") / "paysim")
    args = parser.parse_args()
    print(f"Prepared and reconciled full dataset: {prepare(args.input, args.output)}")


if __name__ == "__main__":
    main()
