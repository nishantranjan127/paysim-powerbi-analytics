# Azure setup guide

## 1. Prepare resources manually

You need an Azure subscription and permission to create resources, assign roles,
and configure Unity Catalog. These steps can incur charges; set a budget first.
No subscription IDs, passwords or tokens belong in project files.

Create or reuse:

1. A resource group in a region supporting Azure Databricks.
2. An ADLS Gen2 storage account with hierarchical namespace enabled and a
   dedicated filesystem/container, such as `finance`.
3. An Azure Databricks workspace attached to a Unity Catalog metastore.
4. An Azure Databricks Access Connector with managed identity.
5. An Azure Data Factory instance with system-assigned managed identity.

Grant the Access Connector identity Storage Blob Data Contributor on the
dedicated container, rather than broadly across the subscription. In Unity
Catalog, an administrator creates a storage credential referencing that
connector and an external location pointing to:

```text
abfss://finance@<storage-account>.dfs.core.windows.net/
```

Configure storage firewall/private networking separately if used. Role
assignments alone do not open blocked network paths.

Use an existing catalog with managed storage configured. Create a dedicated
project schema, for example `your_catalog.finance_portfolio`. The job principal
needs USE CATALOG, USE SCHEMA and CREATE TABLE, plus ownership/MODIFY of its
output tables. Have the schema owner create an external volume named `landing`
at a non-overlapping external-location subdirectory, for example
`abfss://finance@<storage-account>.dfs.core.windows.net/landing/`.
Grant the job principal READ VOLUME. Grant your uploader WRITE VOLUME only if
uploading through Databricks. Do not mix the landing path with managed table
storage.

See [managed identities for Unity Catalog storage](https://learn.microsoft.com/en-us/azure/databricks/connect/unity-catalog/cloud-storage/azure-managed-identities).
Do not use legacy DBFS mounts or account keys.

## 2. Upload the snapshot

Generate the synthetic input locally:

```powershell
python -m finance_lakehouse generate
```

Upload `data/landing/transactions.csv` to the external volume using the Catalog
Explorer upload UI or an approved ADLS upload tool. Its Databricks path should be:

```text
/Volumes/your_catalog/finance_portfolio/landing/transactions.csv
```

The pipeline intentionally reads one CSV file, not a recursively scanned folder.
Keep its contents immutable until processing completes.

## 3. Configure and run Databricks

Install the current standalone Databricks CLI using your organization's approved
method. This is different from the legacy Python `databricks-cli` package.
Use OAuth, not committed personal access tokens.

Create/reuse Unity Catalog enabled compute running Databricks Runtime 15.4 LTS
or newer, with an appropriate access mode and no additional Python libraries.
Use a small approved node type, auto-termination and no idle SQL warehouse.
The bundle uses an existing cluster; record its cluster ID. The job's Run as
identity must have CAN ATTACH TO permission and storage/catalog grants.

From the project root, replace the example values below:

```powershell
databricks auth login --host https://adb-<workspace-id>.<region-id>.azuredatabricks.net --profile finance
$env:DATABRICKS_CONFIG_PROFILE = "finance"
$env:BUNDLE_VAR_catalog = "your_catalog"
$env:BUNDLE_VAR_schema = "finance_portfolio"
$env:BUNDLE_VAR_input_path = "/Volumes/your_catalog/finance_portfolio/landing/transactions.csv"
$env:BUNDLE_VAR_cluster_id = "<existing-cluster-id>"
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run -t dev finance_lakehouse
databricks bundle summary -t dev
```

Stop if validation fails; do not deploy an invalid configuration. These commands
must run in the same PowerShell session to preserve environment variables.
The bundle synchronizes the Python package and notebook to workspace files.
The notebook imports the package from its parent directory, using the modern
runtime's notebook working-directory behavior.

The task logs print the quality report and return it as notebook output.
For the default fixture expect 1008 Bronze, 1000 Silver and 8 quarantine rows.
Run the same input a second time and check that counts and Gold totals match.

## 4. Connect Azure Data Factory

In ADF Studio, create an Azure Databricks linked service named
`AzureDatabricks`. Prefer managed-identity authentication: set the Databricks
workspace URL and Azure resource ID. Follow your tenant's workspace access
requirements; Azure resource permissions are separate from Databricks job and
Unity Catalog permissions. Grant only the roles needed by your linked-service
authentication mode.

Provision/assign the Data Factory managed identity as an allowed Databricks
service principal where required, and grant CAN MANAGE RUN on this deployed job.
The job Run as identity, not merely the ADF caller, needs the data grants.
For an existing-job activity, compute is defined by the job; do not supply a
new cluster configuration to replace this bundle's cluster.

Create a pipeline in ADF Studio and replace its code with the contents of
`adf/pipeline.json` (or import it through your normal Git authoring workflow).
This is a pipeline definition, **not a complete ARM deployment template**.
Resolve the linked service reference, validate and publish.

Set the pipeline parameters:

| Parameter | Example |
| --- | --- |
| `job_id` | Numeric job ID from the deployed job URL/bundle summary |
| `catalog` | `your_catalog` |
| `schema` | `finance_portfolio` |
| `input_path` | `/Volumes/your_catalog/finance_portfolio/landing/transactions.csv` |

Debug once, then add a schedule trigger only after the complete snapshot upload
is coordinated. The template supplies orchestration, not source-system extraction
or an automatic copy activity. ADF activity retries are disabled to avoid
uncoordinated reruns; investigate a failed run and replay the unchanged snapshot.
Monitor the ADF activity **and** Databricks job status, not just trigger delivery.

See [ADF Databricks Job activity](https://learn.microsoft.com/en-us/azure/data-factory/transform-data-databricks-job)
and [Databricks linked services](https://learn.microsoft.com/en-us/azure/data-factory/compute-linked-services#azure-databricks-linked-service).

## 5. Cloud smoke test

Select your catalog and schema in Databricks SQL. Run:

```sql
SELECT COUNT(*) AS bronze_rows FROM bronze_transactions; -- 1008
SELECT COUNT(*) AS silver_rows FROM silver_transactions; -- 1000
SELECT COUNT(*) AS quarantine_rows FROM quarantine_transactions; -- 8
SELECT SUM(transaction_count) AS gold_transactions FROM gold_daily_metrics; -- 1000
SELECT reason, COUNT(*) FROM quarantine_transactions GROUP BY reason;

-- Must return zero rows:
SELECT transaction_id
FROM silver_transactions
GROUP BY transaction_id HAVING COUNT(*) > 1;

-- Must return zero:
SELECT COUNT(*) AS invalid_silver
FROM silver_transactions
WHERE amount IS NULL OR amount <= 0 OR event_time IS NULL;
```

Run `sql/analytics.sql` for portfolio dashboard queries. Keep amounts separated
by currency. Optionally connect Power BI to Databricks SQL with approved OAuth
authentication; a SQL warehouse may incur separate compute charges.

Before calling the cloud setup verified, confirm the Databricks run and ADF
activity both succeed, reconciliation passes, and a second run preserves results.
Local tests alone do not exercise Spark, managed identities or Azure networking.

## Costs and cleanup

Do not leave compute running. Stop the cluster after testing and stop any SQL
warehouse and ADF schedule you created. A bundle deployment does not create the
Azure storage account, workspace, connector, metastore, cluster or Data Factory.
Those are manual prerequisites.

To remove only this bundle's job/workspace deployment:

```powershell
databricks bundle destroy -t dev
```

Review the CLI's resource list before confirming. This does **not** remove managed
Delta tables or external landing files. Delete only the dedicated demo tables,
volume and files after checking ownership and retention requirements; then
remove exclusively created Azure resources through your approved process.
Never delete shared catalogs, resource groups or storage accounts.
