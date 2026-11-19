# Databricks notebook source
# MAGIC %md
# MAGIC # Financial Transactions Lakehouse
# MAGIC Batch snapshot pipeline: CSV -> Bronze -> Silver + Quarantine -> Gold.
# MAGIC
# MAGIC **Before running:** use a dedicated Unity Catalog schema and upload the
# MAGIC complete CSV snapshot into a readable Unity Catalog volume. Set `catalog`,
# MAGIC `schema`, and `input_path` below. This works with volumes on AWS or Azure;
# MAGIC Azure Data Factory and ADLS are not required for an interactive AWS run.
# MAGIC
# MAGIC Each run **replaces all four project tables** in the selected schema.
# MAGIC Do not use a shared production schema. Compute is not started by importing
# MAGIC this notebook. Running cells can start compute and incur charges.

# COMMAND ----------

import json
import sys
from pathlib import Path

# Modern Databricks runtimes use the notebook's workspace directory as cwd.
sys.path.insert(0, str(Path.cwd().parent))

from finance_lakehouse.spark_pipeline import run

dbutils.widgets.text("catalog", "")
dbutils.widgets.text("schema", "")
dbutils.widgets.text("input_path", "")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Run only after configuring the widgets
# MAGIC `input_path` must identify one complete CSV, for example
# MAGIC `/Volumes/your_catalog/finance_portfolio/landing/transactions.csv`.
# MAGIC The default synthetic fixture should produce 1008 Bronze, 1000 Silver,
# MAGIC and 8 quarantined rows. Inspect the returned quality report before querying.

# COMMAND ----------

report = run(
    spark,
    dbutils.widgets.get("catalog"),
    dbutils.widgets.get("schema"),
    dbutils.widgets.get("input_path"),
)
print(json.dumps(report, indent=2))
dbutils.notebook.exit(json.dumps(report))
