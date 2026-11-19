"""Databricks batch snapshot pipeline; imports Spark only on the cloud path."""

import json
import re

from finance_lakehouse.rules import FIELDS, TIE_BREAK_FIELDS, normalize_record


def run(spark, catalog: str, schema: str, input_path: str) -> dict:
    from pyspark import StorageLevel
    from pyspark.sql import Window, functions as F, types as T

    for identifier in (catalog, schema):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
            raise ValueError("Catalog and schema must be simple SQL identifiers")
    if not input_path.startswith("/Volumes/") or not input_path.endswith(".csv"):
        raise ValueError("input_path must point to a CSV file inside a Unity Catalog volume")
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    namespace = f"`{catalog}`.`{schema}`"
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {namespace}")
    raw_schema = T.StructType([T.StructField(field, T.StringType()) for field in FIELDS])
    bronze = (
        spark.read.schema(raw_schema)
        .option("header", True)
        .option("enforceSchema", False)
        .option("mode", "FAILFAST")
        .csv(input_path)
        .withColumn("source_file", F.lit(input_path))
        .withColumn("ingested_at", F.current_timestamp())
    )
    bronze.write.format("delta").mode("overwrite").saveAsTable(f"{namespace}.bronze_transactions")
    bronze = spark.table(f"{namespace}.bronze_transactions")
    if bronze.isEmpty():
        raise ValueError("Input must contain at least one transaction")
    result_schema = T.StructType(
        [T.StructField(field, T.StringType()) for field in (*FIELDS, "transaction_date", "error_code")]
        + [T.StructField("is_high_value", T.BooleanType())]
    )

    @F.udf(result_schema)
    def validate(raw):
        return normalize_record(raw.asDict())

    checked = bronze.withColumn("normalized", validate(F.struct(*FIELDS))).persist(StorageLevel.MEMORY_AND_DISK)
    try:
        valid = checked.filter(F.col("normalized.error_code").isNull())
        ordering = [F.col(f"normalized.{field}").desc() for field in TIE_BREAK_FIELDS]
        ranked = valid.withColumn(
            "row_rank",
            F.row_number().over(Window.partitionBy("normalized.transaction_id").orderBy(*ordering)),
        )
        silver = (
            ranked.filter("row_rank = 1")
            .select("normalized.*")
            .drop("error_code")
            .withColumn("event_time", F.to_timestamp("event_time"))
            .withColumn("transaction_date", F.to_date("transaction_date"))
            .withColumn("amount", F.col("amount").cast("decimal(18,2)"))
        )
        invalid = checked.filter(F.col("normalized.error_code").isNotNull()).select(
            *FIELDS, "source_file", "ingested_at", F.col("normalized.error_code").alias("reason")
        )
        duplicates = ranked.filter("row_rank > 1").select(
            *FIELDS, "source_file", "ingested_at", F.lit("duplicate_transaction").alias("reason")
        )
        quarantine = invalid.unionByName(duplicates)
        silver.write.format("delta").mode("overwrite").saveAsTable(f"{namespace}.silver_transactions")
        quarantine.write.format("delta").mode("overwrite").saveAsTable(f"{namespace}.quarantine_transactions")
    finally:
        checked.unpersist()

    silver = spark.table(f"{namespace}.silver_transactions")
    gold = silver.groupBy("transaction_date", "currency", "transaction_type", "status").agg(
        F.count("*").alias("transaction_count"),
        F.sum("amount").alias("total_amount"),
        F.sum(F.col("is_high_value").cast("long")).alias("high_value_count"),
    )
    gold.write.format("delta").mode("overwrite").saveAsTable(f"{namespace}.gold_daily_metrics")
    quarantine = spark.table(f"{namespace}.quarantine_transactions")
    report = {
        "bronze_rows": bronze.count(),
        "silver_rows": silver.count(),
        "quarantine_rows": quarantine.count(),
        "gold_groups": spark.table(f"{namespace}.gold_daily_metrics").count(),
        "quarantine_reasons": {row["reason"]: row["count"] for row in quarantine.groupBy("reason").count().collect()},
    }
    if report["bronze_rows"] != report["silver_rows"] + report["quarantine_rows"]:
        raise RuntimeError(f"Row reconciliation failed: {json.dumps(report)}")
    gold_count = spark.table(f"{namespace}.gold_daily_metrics").agg(F.sum("transaction_count")).first()[0] or 0
    if gold_count != report["silver_rows"]:
        raise RuntimeError(f"Gold reconciliation failed: {json.dumps(report)}")
    return report
