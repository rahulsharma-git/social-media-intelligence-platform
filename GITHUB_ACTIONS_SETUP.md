# GitHub Actions Setup — Build 6.0.0 (hourly monitor + V8 AM/PM reports)

The browser-independent monitor runs hourly outside the Chrome/Streamlit
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

The workflow runs once per hour at **:07 in Asia/Kolkata**. The small offset avoids GitHub's busiest top-of-hour
schedule window.

## 5. Test once manually

GitHub -> Actions -> `SMI Background Monitor - Hourly` -> Run workflow.
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
received a given hourly slot, so retries do not resend successful recipients.

## Build 5.17 additional recommended variables

Add/update under **Settings → Secrets and variables → Actions → Variables**:

```text
NEWS_MAX_RECORDS = 150
X_DISCOVERED_PROFILE_LIMIT = 12
```

Existing recommended values remain:

```text
BACKGROUND_LOOKBACK_HOURS = 24
BACKGROUND_MAX_RECORDS = 1500
X_MIN_POSTS = 10
X_MIN_REPORT_POSTS = 10
X_MAX_POSTS = 50
X_FALLBACK_MAX_POSTS = 50
X_FRESH_HOURS = 24
X_FREE_TOPIC = ISRO
X_PROFILE_HANDLES = isro
X_ENABLE_WEB_INDEX = true
X_ENABLE_OEMBED = true
EMAIL_ONLY_ON_CHANGE = false
NEGATIVITY_ALERT_THRESHOLD = 35
```

Build 5.17 does not require an X bearer token.

## Build 5.20.0 — optional free semantic-analysis setup

The scheduled media report can improve its descriptions with free hosted
open-weight/open-model inference. Providers are tried in this order:

1. Groq (`openai/gpt-oss-20b` by default)
2. OpenRouter (`openrouter/free` by default)
3. Cloudflare Workers AI (`@cf/google/gemma-4-26b-a4b-it` by default)
4. Existing local rules

### GitHub repository secrets

Go to **Repository -> Settings -> Secrets and variables -> Actions -> Secrets**
and add whichever providers you want to use:

```text
GROQ_API_KEY
OPENROUTER_API_KEY
CLOUDFLARE_API_TOKEN
CLOUDFLARE_ACCOUNT_ID
```

The LLM secrets are optional. Missing providers are skipped automatically.

For Cloudflare, `CLOUDFLARE_ACCOUNT_ID` is the account ID shown in the
Cloudflare dashboard. Create an API token that is allowed to invoke Workers AI
for that account and store the token as `CLOUDFLARE_API_TOKEN`.

### Optional GitHub repository variables

Under **Settings -> Secrets and variables -> Actions -> Variables**, you can add:

```text
ENABLE_FREE_LLM_ANALYSIS=1
GROQ_MODEL=openai/gpt-oss-20b
OPENROUTER_MODEL=openrouter/free
CLOUDFLARE_AI_MODEL=@cf/google/gemma-4-26b-a4b-it
```

These variables are optional because the code has the values above as defaults.
Use `ENABLE_FREE_LLM_ANALYSIS=0` if you want to disable all remote model calls.

### Safety / billing design

- `OPENROUTER_MODEL` defaults to `openrouter/free`, not to a paid model ID.
- Cloudflare is attempted only if both its token and account ID are present.
- Provider HTTP/rate-limit failures do not stop report generation.
- If no provider succeeds, the existing local tone/impact/analysis rules are used.
- Keep provider accounts on their free plans if zero-cost operation is required.

## Build 6.0.0 — V8 scheduled-report setup

The active scheduled report workflow is `.github/workflows/scheduled-media-reports.yml` and the active policy file is `DoS_ISRO_Master_Query_Version_8_0.txt`.

Automatic schedule:
- Morning 08:00 IST (`30 2 * * *` UTC)
- Evening 20:00 IST (`30 14 * * *` UTC)
- Weekly automatic delivery is paused. `weekly` remains available only through explicit manual workflow dispatch.

Existing optional free-model secrets remain unchanged:

```text
GROQ_API_KEY
OPENROUTER_API_KEY
CLOUDFLARE_API_TOKEN
CLOUDFLARE_ACCOUNT_ID
```

Recommended/optional Build 6 variables:

```text
ENABLE_FREE_LLM_ANALYSIS=1
GROQ_MODEL=openai/gpt-oss-20b
OPENROUTER_MODEL=openrouter/free
CLOUDFLARE_AI_MODEL=@cf/google/gemma-4-26b-a4b-it
MIN_RESEARCH_DWELL_SECONDS=1200
CONSOLIDATED_ARCHIVE_FILE=<latest archive filename committed to the repository>
V8_TEMPLATE_FILE=DoS_ISRO_Media_Report_V7_0_Template.docx
PUBLISHER_RSS_URLS=<optional comma/newline-separated extra publisher RSS/Atom URLs>
```

`CONSOLIDATED_ARCHIVE_FILE` is strongly recommended. V8 permits only the latest consolidated archive to act as the historical article registry; the worker never modifies it automatically. If it is unavailable, the document can still be generated but delivery is identified as PROVISIONAL outside the DOCX/PDF because historical article deduplication cannot be confirmed.

If the original V7 template DOCX is not present in the GitHub repository, Build 6 programmatically reproduces the specified seven-column A4-landscape design and records the template limitation outside the publication. Add the exact template as `DoS_ISRO_Media_Report_V7_0_Template.docx` (or set `V8_TEMPLATE_FILE`) when available.
