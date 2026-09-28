# Social Media Intelligence Platform — Build 5.14.2

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


## Build 5.13.1 hotfix

- News look-back defaults to **24 hours** in the Streamlit UI.
- Background news collection now requests **24h** and defaults the analysis window to 24 hours.
- The Google News RSS fallback is filtered locally so older items are excluded.
- GitHub Actions workflow upgraded to current Node 24-compatible action versions.
- Added a configuration-presence check with clear GitHub error annotations.
- `REPORT_RECIPIENTS` is accepted as a fallback to `REPORT_RECIPIENTS_30MIN`.
- SMTP sending retries up to three times.
- Email delivery failures are recorded as warnings by default rather than stopping future monitoring cycles.
- A GitHub Step Summary and JSON diagnostic summary are written on each completed run.
- All report/run timestamps remain in IST.

### Required GitHub Actions repository secrets

At minimum:
- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY` (or legacy `SUPABASE_SERVICE_KEY`)

For email:
- `SMTP_EMAIL`
- `SMTP_APP_PASSWORD`
- `REPORT_RECIPIENTS_30MIN` (or `REPORT_RECIPIENTS`)
- optional `ALERT_RECIPIENTS`

These are GitHub Actions secrets and are separate from Streamlit Secrets.

## Build 5.13.2

Fixes Supabase HTTP 409 conflicts in the GitHub background monitor by specifying
the correct PostgREST conflict targets (`external_id` and `slot_key`).


## Build 5.13.3 — Dual Scheduler

- GitHub Actions schedule is explicitly timezone-aware for Asia/Kolkata.
- Native schedule runs at :07 and :37 IST.
- Optional Supabase Cron fallback dispatches the GitHub workflow every 30 minutes.
- Slot-based deduplication prevents duplicate routine emails if both schedulers fire.
- Trigger source is shown in GitHub run summaries and email/report status text.
- News remains past 24 hours by default.
- PDF generation, emailing and all timestamps remain browser-independent and IST.


## Build 5.14

Three source-specific intelligence PDFs, a combined PDF, two evidence ZIPs, improved cluster explanations/section summaries, and an optional official X API collector. See `BUILD_5.14_REPORTING_AND_X.md`.


## Build 5.14.2 freshness/delta behavior

- News is a rolling 24-hour unique snapshot; reports separately show new vs retained items.
- X prefers fresh 24-hour data but falls back to the latest stored X posts when the live collector is unavailable.
- Stale X fallback is labelled explicitly with newest-record timestamp.
- Optional `EMAIL_ONLY_ON_CHANGE=true` suppresses no-change routine emails.


## Build 5.14.3 — X minimum 10 target

The official X collector now targets at least 10 latest real matching X posts.
It prefers original/non-retweet posts and, only when necessary to reach the
minimum, makes one broader pass that permits reposts. It never fabricates data.

Set GitHub Actions variables:
`X_MIN_POSTS=10`, `X_MIN_REPORT_POSTS=10`, `X_MAX_POSTS=25`.

If fewer than 10 fresh X posts are available but Supabase has older X records,
the report backfills with the latest stored rows and labels them as backfill.


## Build 5.15 — Free X Fusion

Build 5.15 removes the requirement for `X_BEARER_TOKEN`.

Free X inputs are combined:
1. existing/manual Supabase X rows + `x_inbox/*.csv`
2. free public web-index discovery of X status URLs
3. official keyless X oEmbed hydration of discovered/manual URLs

The pool is deduplicated, sorted newest-first, and capped at 50 rows.
The reporting target is at least 10 latest real rows whenever at least 10 are
actually available across the combined free sources. No synthetic posts are created.


## Build 5.16 — X Fusion Repair

The free X collector now has an independent X public-profile syndication source
(default handle `isro`) in addition to stored/manual input and public web-index
discovery. Known/discovered post URLs are hydrated with X's public tweet-result
endpoint, with oEmbed as fallback.

`background_monitor.py` directly merges `output/x_latest_fused.csv` from the
collector into the same analytics run, then uses up to the latest 50 X rows.
Routine email subjects include the overall Negative percentage.

## Build 5.17 — Source Fusion + Confidence-Aware Intelligence

Build 5.17 is based on the supplied Build 5.16.1 hotfix. It preserves the
working 30-minute scheduler and report/email architecture while upgrading both
acquisition and analysis.

News now fuses GDELT, Google News RSS, Bing News RSS and the official ISRO press
page. X free fusion adds more robust public-index discovery, retry/backoff,
redirect decoding and bounded expansion into topic-relevant public profile
timelines discovered from indexed status URLs.

Analysis now uses confidence-aware sentiment, richer emotion/intensity,
communication intent, adaptive semantic clustering, robust anomaly detection,
Attention Index and Assessment Confidence. See `BUILD_5.17_INTELLIGENCE_UPGRADE.txt`
for full methodology and deployment variables.


## Build 5.17.1 — email delivery reliability

Routine and alert emails now send the analytical PDFs as a small core message.
Evidence ZIPs are not attached to that primary email unless
`EMAIL_SEND_EVIDENCE_SEPARATELY=true`.

The workflow now reports **SMTP accepted** rather than **delivered**, because an
SMTP 250 acceptance does not guarantee inbox placement.
Recommended variable: `EMAIL_ATTACHMENT_RAW_LIMIT_MB=10`.


## Build 5.17.2 — News story-level deduplication

News analysis now operates on unique topic-relevant **story families** rather
than raw RSS/feed rows. Near-identical headlines across publishers are collapsed
before sentiment/emotion/clustering. Fuzzy off-topic results are rejected.
This also cleans historical duplicate rows already present in Supabase without
deleting audit data.


## Build 5.17.3 — report dedup column-preservation hotfix

Fixes `KeyError: sentiment_score` introduced by Build 5.17.2. Story-level News
deduplication now preserves all enriched analysis columns used by sentiment,
emotion, clustering, narratives, evidence generation and PDF ranking.
