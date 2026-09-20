# Social Media Intelligence Platform — Build 5

Build 5 adds live X Recent Search with ISRO monitoring presets.

## New data source

**X Recent Search**

Presets:
- Public conversation about ISRO
- Official `@isro` posts
- Custom X query

The first live collector intentionally requests only one API page (10–100 posts)
to keep testing controlled.

## Required Streamlit secret

Add your X App Bearer Token in Streamlit Community Cloud:

```toml
X_BEARER_TOKEN = "YOUR_X_BEARER_TOKEN"
```

Do not commit the token to GitHub.

## Existing Supabase secrets

```toml
SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
SUPABASE_SECRET_KEY = "sb_secret_..."
```

## Workflow

X Recent Search
-> standardized social post records
-> sentiment/emotion
-> semantic clustering
-> narrative intelligence
-> trend/risk analysis
-> optional Save to Supabase

## ISRO public-conversation query

```text
(ISRO OR @isro OR #ISRO) lang:en -is:retweet
```

## ISRO official-account query

```text
from:isro -is:retweet
```
