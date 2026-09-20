# Social Media Intelligence Platform — Build 4.1

Small Supabase compatibility update.

## Change

The app now prefers Supabase's current server-side secret API key:

```toml
SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
SUPABASE_SECRET_KEY = "sb_secret_..."
```

The old `SUPABASE_SERVICE_KEY` variable remains supported as a legacy fallback.

Do not commit either secret key to GitHub. Store it only in Streamlit Community
Cloud's Secrets settings.

All Build 4 analytics and CSV features are unchanged.
