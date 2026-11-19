"""Run with: python -m streamlit run dashboard.py"""

import csv
import io
from datetime import date
from pathlib import Path

import streamlit as st

from finance_lakehouse.analytics import analyze, load_transactions


st.set_page_config(page_title="Finance Analyst Studio", page_icon="$", layout="wide")
st.title("Finance Analyst Studio")
st.caption("Synthetic portfolio data | UTC dates | No FX conversion | Not financial advice")

source = Path(__file__).parent / "data" / "output" / "silver_transactions.jsonl"
try:
    transactions = load_transactions(source)
except (OSError, ValueError, TypeError) as error:
    st.error(f"Cannot load the Silver snapshot: {error}")
    st.code("python -m finance_lakehouse generate\npython -m finance_lakehouse run")
    st.stop()

if not transactions:
    st.warning("The Silver snapshot is empty. Check quarantine results and rerun with valid input.")
    st.stop()

st.sidebar.header("Analysis filters")
currency = st.sidebar.selectbox("Currency", sorted({row["currency"] for row in transactions}))
dates = [date.fromisoformat(row["transaction_date"]) for row in transactions]
period = st.sidebar.date_input(
    "Date range (UTC)", value=(min(dates), max(dates)),
    min_value=min(dates), max_value=max(dates),
)
if len(period) != 2:
    st.info("Select both a start date and an end date.")
    st.stop()
report = analyze(transactions, currency, period[0], period[1])
st.sidebar.caption(f"Source: {source.name}. Rerun this page after refreshing the pipeline.")
st.info(
    "Cash-flow amounts include POSTED transactions only. Decline rate uses all "
    "valid, deduplicated attempts in the selected currency and date range."
)
if not report["attempts"]:
    st.warning("No transactions match these filters.")
    st.stop()

metrics = st.columns(4)
metrics[0].metric("Posted inflow", f"{currency} {report['inflow']:,.2f}")
metrics[1].metric("Posted outflow", f"{currency} {report['outflow']:,.2f}")
metrics[2].metric("Net cash flow", f"{currency} {report['net_flow']:,.2f}")
metrics[3].metric("Decline rate", f"{report['decline_rate']:.2%}")
counts = st.columns(4)
counts[0].metric("Valid attempts", report["attempts"])
counts[1].metric("Posted transactions", report["posted_count"])
counts[2].metric("Active accounts (all attempts)", report["active_accounts"])
average = report["average_posted_amount"]
counts[3].metric("Average posted amount", f"{currency} {average:,.2f}" if average is not None else "N/A")

overview, geography, detail = st.tabs(["Daily trends", "Country analysis", "Transaction explorer"])
with overview:
    st.subheader(f"Daily posted cash flow ({currency})")
    st.line_chart([
        {"Date": row["date"], "Inflow": float(row["inflow"]),
         "Outflow": float(row["outflow"]), "Net flow": float(row["net_flow"])}
        for row in report["daily"]
    ], x="Date", y=["Inflow", "Outflow", "Net flow"])
    st.subheader("Daily attempts and declines")
    st.bar_chart([
        {"Date": row["date"], "Posted": row["attempts"] - row["declined"],
         "Declined": row["declined"]}
        for row in report["daily"]
    ], x="Date", y=["Posted", "Declined"])
    st.caption("Only dates with attempts are shown. Chart amounts are approximate; tables and exports retain exact decimal amounts.")
with geography:
    st.subheader("Posted activity by merchant country")
    st.caption("Posted amount is gross activity (credits + debits), not net cash flow or revenue.")
    st.dataframe([
        {**row, "posted_amount": format(row["posted_amount"], ".2f")}
        for row in report["countries"]
    ], width="stretch")
    st.subheader("High-value posted USD transactions")
    st.caption("USD >= 10,000 only. A triage heuristic, not a fraud classifier; other currencies have no defined threshold.")
    st.dataframe(report["high_value_posted"], width="stretch")
with detail:
    st.subheader("Filtered transactions")
    st.dataframe(report["transactions"], width="stretch")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(report["transactions"][0]))
    writer.writeheader()
    writer.writerows(report["transactions"])
    st.download_button(
        "Download filtered CSV", output.getvalue(),
        file_name=f"transactions_{currency}_{period[0]}_{period[1]}.csv", mime="text/csv",
    )

st.divider()
st.subheader("Data quality scope")
st.write(
    "This dashboard reads only valid, deduplicated Silver transactions. Invalid and duplicate "
    "rows are excluded from every KPI. See data/output/quality_report.json and "
    "quarantine_transactions.jsonl for whole-snapshot ingestion quality; those counts "
    "are not date- or currency-filtered dashboard metrics."
)
