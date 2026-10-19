# Build 6.0.2 — Manual dispatch report-date hotfix

Fixes `ValueError: Invalid isoformat string: 'interim'` in the GitHub scheduled report job.

The **report_date** field in Actions > Run workflow accepts `YYYY-MM-DD` or blank. For compatibility with earlier manual runs, `interim`, `today` and `current` now select the current IST date with a log warning. Other invalid strings fail with a clear argument-validation message instead of an unhandled Python traceback.

Preserves Build 6.0.1's Groq 429 circuit breaker, provider fallback, multilingual PDF validation, Master Query V8.0 and all other existing features. No GitHub secrets or configuration changes are required.

Recommended manual run: Edition = morning; Report date = blank; Force send = true only if resending a previously delivered edition.
