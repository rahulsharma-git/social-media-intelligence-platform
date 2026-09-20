# Social Media Intelligence Platform — Build 5.3 Free Stable

This build combines both working free data paths.

## Option A — X / CSV
- Upload is processed immediately.
- No Analyze button or session-state dependency.
- Shows filename, valid row count, engagement totals, and a 10-row preview.
- The supplied `isro_x_posts_TEMPLATE_READY.csv` was smoke-tested with this build.

## Option B — Free web/news
- Tries GDELT first.
- Automatically falls back to Google News RSS when GDELT returns HTTP 429 or another transient error.
- No paid API key is required.

## Combined mode
Choose `Combine CSV + web/news` to analyze both datasets together.

## Supabase
Existing Streamlit secrets remain unchanged:

```toml
SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
SUPABASE_SECRET_KEY = "sb_secret_..."
```
