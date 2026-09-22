# Build 5.13 — 30-minute background scheduler setup

This build moves the important 30-minute work out of the Chrome/Streamlit
session and into **GitHub Actions**. Chrome may be minimized, closed, or the
phone may be locked; the scheduled GitHub job is independent of the browser.

## 1. Run the Supabase SQL once

Open your Supabase project -> SQL Editor, paste the contents of
`supabase_build513.sql`, and run it.

This creates `monitoring_runs`, which stores each half-hour run, PDF/email
status, and recipient-level delivery state. It also lets the Streamlit
dashboard show browser-independent negativity history.

## 2. Add GitHub Actions repository secrets

GitHub repository -> Settings -> Secrets and variables -> Actions -> Secrets.
Add these **repository secrets** (they are separate from Streamlit Secrets):

- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY` (preferred) OR `SUPABASE_SERVICE_KEY`
- `SMTP_EMAIL`
- `SMTP_APP_PASSWORD`
- `REPORT_RECIPIENTS_30MIN`
- `ALERT_RECIPIENTS`

For multiple recipients use one comma-separated secret, for example:

`person1@example.com,person2@example.com`

Do not put any password or secret in the repository files.

## 3. Optional GitHub Actions variables

Under Settings -> Secrets and variables -> Actions -> Variables, you may add:

- `MONITOR_QUERY` = `ISRO`
- `NEGATIVITY_ALERT_THRESHOLD` = `35`
- `BACKGROUND_LOOKBACK_HOURS` = `168`
- `BACKGROUND_MAX_RECORDS` = `1500`

If omitted, the script uses those defaults.

## 4. Upload all Build 5.13 files to the repository

The workflow file must exist on the default branch at:

`.github/workflows/monitor-every-30-min.yml`

The worker file is:

`background_monitor.py`

The workflow runs at **:07 and :37 every hour in Asia/Kolkata**, exactly a
30-minute interval. The small offset avoids GitHub's busiest top-of-hour
schedule window.

## 5. Test once manually

GitHub -> Actions -> `SMI Background Monitor - Every 30 Minutes` -> Run workflow.
Leave `force_send = true` for the test.

A successful run should:

1. load posts from Supabase;
2. refresh free Web News;
3. run sentiment/emotion/semantic/narrative analysis;
4. generate the PDF with IST timestamps;
5. email the PDF to the routine recipient(s);
6. send an alert-only email only on an upward crossing of the negativity threshold;
7. write the run to `monitoring_runs`;
8. upload the PDF and JSON run summary as a GitHub Actions artifact for 7 days.

## Important X limitation

The background worker can analyze **X/manual-X posts already stored in
Supabase**. It does not bypass X authentication or paid API restrictions.
Fresh Web News is fetched automatically every run.

## Avoid duplicate emails

Build 5.13 turns the in-app Streamlit scheduler/email controls into optional
fallback controls and disables them by default. Keep them off when GitHub
Actions is enabled. `monitoring_runs` also remembers which recipients already
received a given half-hour slot, so retries do not resend successful recipients.
