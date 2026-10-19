# Build 6.0.0 — Master Query V8.0 Integration

Build 6.0.0 is based on the latest working 5.20 package and preserves the operational features accumulated through Build 5.19, including the hourly browser-independent monitor, Supabase persistence, X/free-source fusion, fresh scheduled scans, consequence-based impact logic, DOCX/PDF generation and email delivery.

The scheduled AM/PM media-report worker is now governed by `DoS_ISRO_Master_Query_Version_8_0.txt`.

## V8 research logic

- Strict IST windows: Morning previous day 20:00 inclusive to report day 08:00 exclusive; Evening report day 08:00 inclusive to 20:00 exclusive.
- Original publisher first-publication timestamp controls eligibility; feed/index/crawl time is never used as proof.
- Fresh scan every run.
- Five distinct discovery passes, exceeding the V8 minimum 39 submitted queries.
- All 44 V8 coverage themes checked every run.
- Independent attempts across Google News RSS, Bing News RSS, Bing Web RSS, GDELT and publisher-native RSS/Atom; extra publisher feeds can be supplied via `PUBLISHER_RSS_URLS`.
- Two sweeps: broad discovery first, then event/title gap reconciliation for additional original publishers.
- Same-event articles from different publishers are retained. Deduplication is article-identity based.
- Official ISRO/DoS/PIB/IN-SPACe/NSIL pages are corroboration/action evidence, never independent-media rows.
- Latest consolidated archive is the only historical article registry. Configure `CONSOLIDATED_ARCHIVE_FILE`; the archive is never modified automatically.
- If the archive is unavailable or the 20-minute normal research dwell target is not met, the automated delivery is marked PROVISIONAL outside the report document.

## V8 sections

1. DoS / ISRO Major News
2. Indian Private Space / Startup News — only when eligible rows exist
3. Relevant Space News — only when eligible lookback rows exist; always begins with an explicit Word page break

## V8 seven-column output

Exactly seven columns:

1. No.
2. Description / Title of News + `Source: Article` hyperlink in the same cell
3. Publisher / author
4. Publication date / Time
5. Tone
6. Impact Analysis
7. Remarks / Action

A4 landscape, Arial 12 pt body, approximately 0.46-inch side margins, 0.75-pt continuous grid, repeating shaded headers, top-aligned cells, unsplit article rows, and actual generation time with seconds in the footer on every page.

Tone values are exactly `Positive`, `Neutral-Mixed`, or `Critical-Negative`. Impact begins exactly `High Impact.`, `Medium Impact.`, or `Low Impact.` followed by one or two short concrete sentences.

## V8 action logic

Every included article receives an official-source check before the published action is drafted. Routine accurate/covered reporting uses exactly `Nil action.`. Suggested actions use the restrained V8 website-focused language and never mention OMPR.

## Free semantic-analysis chain retained

The optional chain remains:

1. Groq (`openai/gpt-oss-20b` default)
2. OpenRouter free route
3. Cloudflare Workers AI
4. deterministic local rules

A provider failure never stops report generation. The semantic output is validated before use and must follow the V8 tone/impact definitions.

## Scheduling

- Morning: 08:00 IST
- Evening: 20:00 IST
- Weekly automatic report: paused under V8. A manual weekly dispatch remains available as an explicit legacy feature.
- Hourly background monitor: preserved from Build 5.19.5 at :07 IST each hour.
