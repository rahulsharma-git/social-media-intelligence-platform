# Social Media Intelligence Platform - Build 5.6.1 Free Stable

Build 5.6.1 retains the stable X/CSV and free-News workflows and adds a
downloadable analytical PDF report.

## Dashboard additions

- X: Top 10 positive briefs
- X: Top 10 negative briefs
- News: Top 10 positive briefs
- News: Top 10 negative briefs
- Expanded Legend & Methodology section
- Dynamic cluster legend table
- Generate + Download PDF report

## PDF contents

1. Executive overview
2. Top sentiment briefs
   - X positive
   - X negative
   - News positive
   - News negative
3. Cluster and narrative interpretation
4. Detailed legends and methodology
5. Annexure A - all X posts analyzed
6. Annexure B - all News items analyzed, separated into:
   - Positive
   - Negative
   - Neutral (included so every analyzed item is accounted for)

For News, the free feeds usually provide the media source but not an
individual article byline. The Author field is therefore shown as
`Not provided by feed` when unavailable.

## PDF dependency

`reportlab` has been added to requirements.txt.

## Existing workflows retained

- Stable Android X/CSV loading
- Built-in CSV test
- GDELT news with automatic Google News RSS fallback
- Combined X + News mode
- Supabase persistence
- Sentiment, emotion, clustering, narratives, trend/anomaly and monitoring score


PDF font handling was updated to use Noto Sans for Latin text and a separate Devanagari font when available.
