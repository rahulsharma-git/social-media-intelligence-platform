# Social Media Intelligence Platform — Build 5 Free

This build keeps the project at zero API cost by supporting:

1. **CSV / manual X-post import**
2. **Free web/news monitoring through GDELT**
3. **Combined CSV + web/news analysis**
4. **Supabase persistence**
5. Existing sentiment, emotion, semantic-clustering, narrative, trend and risk agents

## CSV minimum columns

- `timestamp`
- `username`
- `text`

Optional:

- `hashtags`
- `likes`
- `reposts`
- `replies`
- `followers`
- `platform`
- `source_url`
- `external_id`

A header-only template is included as `isro_x_template.csv`.

## GDELT

The app uses GDELT DOC 2.0 ArticleList JSON output. No X developer
credential is required.

Default query:

`(ISRO OR "Indian Space Research Organisation")`

## Supabase secrets

Keep these only in Streamlit Secrets:

```toml
SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
SUPABASE_SECRET_KEY = "sb_secret_..."
```

Do not commit secrets to GitHub.
