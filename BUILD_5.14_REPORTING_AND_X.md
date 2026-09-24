# Build 5.14 - Reporting redesign + optional X collection

## Reports generated every monitoring cycle

1. `ISRO_Social_Media_Intelligence_<IST>.pdf`
2. `ISRO_News_Media_Intelligence_<IST>.pdf`
3. `ISRO_Combined_Intelligence_<IST>.pdf`

Evidence packs:

- `ISRO_SocialMedia_Evidence_<IST>.zip` - one numbered PDF per X/manual-X post + `manifest.csv`
- `ISRO_NewsMedia_Evidence_<IST>.zip` - one numbered PDF per Web News item + `manifest.csv`

## Report improvements

- Every major section starts with **What this section shows / Purpose**.
- Every analytical section ends with a **Section takeaway**.
- A dedicated **Overall conclusion** is included.
- Clusters are re-built independently for Social, News and Combined reports.
- Cluster methodology is stated explicitly: TF-IDF word/bigrams -> TruncatedSVD -> L2 normalization -> KMeans.
- Every cluster profile explains its basis using top weighted terms, leading sources, a representative record, sentiment mix, 12-hour growth and recent examples.
- Positive = blue, Negative = red, Neutral = grey.
- News wording explicitly distinguishes headline framing from public sentiment.
- Source-specific evidence indices map to numbered PDFs in each evidence ZIP.

## X automation

Build 5.14 adds `x_collector.py` to the GitHub workflow. It is intentionally optional.

If `X_BEARER_TOKEN` exists in GitHub Actions Secrets, each scheduled run searches the official X Recent Search endpoint for:

`(ISRO OR @isro OR #ISRO) -is:retweet`

and upserts the returned public posts/metrics into `social_posts` before analysis.

If the token is absent or X API access is unavailable, the collector exits successfully and the monitor continues using X/manual-X posts already stored in Supabase. No unofficial scraping is used.

Optional GitHub Actions variables:

- `X_QUERY`
- `X_MAX_POSTS` (default 100, capped at 500 per monitoring run)

GitHub secret:

- `X_BEARER_TOKEN`

The scheduler remains Supabase Cron / GitHub Actions and does not depend on Chrome or Streamlit being active.
