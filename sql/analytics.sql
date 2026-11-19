-- Select your project catalog and schema in the SQL editor before running.

-- Posted cash-flow by date and currency. No cross-currency addition or FX assumptions.
SELECT
    transaction_date,
    currency,
    SUM(CASE WHEN transaction_type = 'CREDIT' THEN total_amount ELSE -total_amount END) AS net_flow,
    SUM(transaction_count) AS posted_count
FROM gold_daily_metrics
WHERE status = 'POSTED'
GROUP BY transaction_date, currency
ORDER BY transaction_date, currency;

-- Decline rate by currency.
SELECT
    currency,
    SUM(CASE WHEN status = 'DECLINED' THEN transaction_count ELSE 0 END) * 1.0
        / SUM(transaction_count) AS decline_rate
FROM gold_daily_metrics
GROUP BY currency;

-- Triage candidates only: this threshold is not a fraud classifier.
SELECT transaction_id, account_id, event_time, amount, currency, status
FROM silver_transactions
WHERE is_high_value AND status = 'POSTED'
ORDER BY amount DESC;

SELECT reason, COUNT(*) AS rejected_rows
FROM quarantine_transactions
GROUP BY reason
ORDER BY rejected_rows DESC;

-- Country contribution within each currency; gross posted activity is not revenue.
WITH country_activity AS (
    SELECT
        currency,
        merchant_country,
        COUNT(*) AS posted_count,
        SUM(amount) AS posted_amount
    FROM silver_transactions
    WHERE status = 'POSTED'
    GROUP BY currency, merchant_country
)
SELECT
    *,
    posted_amount / SUM(posted_amount) OVER (PARTITION BY currency) AS activity_share
FROM country_activity
ORDER BY currency, posted_amount DESC;

-- Daily posted net flow and change from the previous observed posted date.
-- LAG skips dates without posted activity; this is not necessarily a calendar-day change.
WITH daily_flow AS (
    SELECT
        transaction_date,
        currency,
        SUM(CASE WHEN transaction_type = 'CREDIT' THEN total_amount ELSE -total_amount END) AS net_flow
    FROM gold_daily_metrics
    WHERE status = 'POSTED'
    GROUP BY transaction_date, currency
)
SELECT
    *,
    net_flow - LAG(net_flow) OVER (
        PARTITION BY currency ORDER BY transaction_date
    ) AS change_from_previous_observed_date
FROM daily_flow
ORDER BY currency, transaction_date;
