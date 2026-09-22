# Social Media Intelligence Platform — Build 5.13 / Build 13

Build 5.13 is based on the uploaded Build 5.12 recipient-email hotfix.

## Main fixes

### 1. Consistent sentiment colors everywhere

Whenever a chart encodes sentiment, the build now uses:

- **Positive = blue** (`#1f77b4`)
- **Negative = red** (`#d62728`)
- **Neutral = grey** (`#9e9e9e`)

This applies to the dashboard sentiment donut, Sentiment mix by source, PDF
sentiment distribution, PDF Sentiment mix by source, and negative-only charts
such as historic negativity and negative share by cluster.

### 2. Browser-independent 30-minute execution

The Streamlit/Chrome heartbeat is no longer the primary scheduler. It is kept
only as an optional fallback and is **off by default**.

The new primary scheduler is:

`GitHub Actions -> background_monitor.py -> Supabase -> analysis -> PDF -> Gmail`

The included workflow is:

`.github/workflows/monitor-every-30-min.yml`

It runs every 30 minutes at **:07 and :37 in Asia/Kolkata**. Chrome can be
minimized or closed; the phone can be locked. The worker runs on GitHub's
runner, not inside the phone/browser session.

### 3. Persistent run status and deduplication

Run `supabase_build513.sql` once in the Supabase SQL Editor. It creates a
`monitoring_runs` table that stores:

- each half-hour slot;
- run time;
- positive / neutral / negative percentages;
- alert threshold and alert state;
- generated PDF filename;
- recipient-level routine and alert delivery state;
- errors/status.

This prevents successful recipients from being emailed twice when a run is
retried. The Streamlit dashboard also reads this table to display persistent
browser-independent negativity history.

### 4. Automatic PDF + email outside Streamlit

Every background run can:

1. load stored X/manual-X and other records from Supabase;
2. refresh free Web News;
3. run sentiment, emotion, semantic clustering, narratives and trend analysis;
4. generate the analytical PDF with IST timestamps;
5. email it separately to each `REPORT_RECIPIENTS_30MIN` recipient;
6. send `ALERT_RECIPIENTS` an alert PDF only on an upward crossing of the
   configured negativity threshold;
7. save execution status to Supabase;
8. keep the generated PDF + JSON summary as a GitHub Actions artifact for
   seven days.

## One-time setup

Read `GITHUB_ACTIONS_SETUP.md` and follow it in order.

You must copy the required credentials into **GitHub Actions repository
secrets**. Streamlit Secrets and GitHub Secrets are separate stores.

Required GitHub secrets:

```text
SUPABASE_URL
SUPABASE_SECRET_KEY
SMTP_EMAIL
SMTP_APP_PASSWORD
REPORT_RECIPIENTS_30MIN
ALERT_RECIPIENTS
```

`SUPABASE_SERVICE_KEY` can be used instead of `SUPABASE_SECRET_KEY` for older
Supabase setups.

Optional GitHub Actions variables:

```text
MONITOR_QUERY=ISRO
NEGATIVITY_ALERT_THRESHOLD=35
BACKGROUND_LOOKBACK_HOURS=168
BACKGROUND_MAX_RECORDS=1500
```

## Important X limitation

The worker does **not** bypass X authentication or API restrictions. Automatic
background analysis includes X/manual-X posts that have already been saved in
Supabase. Fresh free Web News is fetched automatically on each run.

## Files added in Build 5.13

- `background_monitor.py`
- `.github/workflows/monitor-every-30-min.yml`
- `supabase_build513.sql`
- `GITHUB_ACTIONS_SETUP.md`

## Deployment

Upload/replace the complete Build 5.13 contents in the GitHub repository,
including the hidden `.github` folder. Streamlit continues to deploy
`streamlit_app.py` as before, while GitHub Actions handles the background job.
