# Social Media Intelligence Platform — Build 4

Build 4 introduces the persistent-data and ingestion foundation.

## New capabilities

- Data-source selector
- Demo dataset
- CSV upload from phone/computer
- Standardized social-post schema
- Optional Supabase persistence
- Supabase loading through REST
- Existing sentiment, emotion, semantic topic, narrative, trend and risk agents

## CSV minimum columns

- `timestamp`
- `username`
- `text`

Optional columns:

- `hashtags`
- `likes`
- `reposts`
- `replies`
- `followers`
- `platform`
- `source_url`
- `external_id`

## Supabase setup

1. Create a Supabase project.
2. Open its SQL editor and run `schema.sql`.
3. In Streamlit Community Cloud, open your app settings and add these secrets:

```toml
SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
SUPABASE_SERVICE_KEY = "YOUR_SERVICE_ROLE_KEY"
```

Never commit the service-role key to GitHub.

After secrets are saved, reboot/redeploy the Streamlit app.

## Architecture

Data source -> normalized social-post records -> intelligence agents -> dashboard.

This means the future live X collector can replace the demo/CSV source without
rewriting the analysis layer.
