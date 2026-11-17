# Build 6.1.0 — Master Query V10.3 Single Sequential Daily Execution

This build makes `DoS_ISRO_Master_Query_Version_10_3.txt` the active governing policy for scheduled Morning and Evening reports.

## Active daily schedule
- Morning: 06:05 IST (`35 0 * * *` UTC), publication window `[previous day 18:00, current day 06:00)` IST.
- Evening: 18:05 IST (`35 12 * * *` UTC), publication window `[current day 06:00, current day 18:00)` IST.
- Weekly workflow remains unchanged/manual.

## Single-run sequence
1. Read and hash the complete V10.3 master query.
2. Load only the read-only repository-root archive `latest-archive.csv`. The user replaces this same file whenever a newer manually maintained archive is available; no older archive is auto-selected.
3. Execute passes A–E sequentially, 39-query minimum target, all 44 themes, native/aggregator/feed attempts and sweep 1.
4. Verify original publisher, full URL, article body, English-language eligibility and original first-publication evidence.
5. Apply strict main-window test and rolling-24-hour Relevant Space News test. No Hold disposition: unresolved items are documented and dropped.
6. Deduplicate by article identity, not event/topic; retain independent publisher coverage.
7. Run second gap-reconciliation sweep and repeat verification/deduplication.
8. Classify tone and impact using optional free semantic providers (Groq → OpenRouter Free → Cloudflare Workers AI) with local ISRO-perspective rules as fallback.
9. Sort within each section exactly Positive High/Medium/Low → Neutral High/Medium/Low → Negative High/Medium/Low, newest verified time first within a group; assign continuous final numbers.
10. Send attachment-free News Detection List with visible full original source URLs. Failure is logged and is non-blocking.
11. Perform official ISRO/DoS/PIB/IN-SPACe/NSIL checks. Remarks/Action defaults exactly to `Nil action.`. Optional exception wording is disabled unless `ENABLE_REMARKS_EXCEPTIONS=1` is explicitly configured.
12. Generate synchronized DOCX, PDF and XLSX from the same final dataset.
13. Validate article counts/order, DOCX hyperlinks, PDF URI annotations, seven-column structure, impact paragraph separation, footer and XLSX identities.
14. Save consolidated evidence ledger and authoritative dataset under `output/DoS_ISRO_Media_Monitoring/Preparation/` for the same run and upload them as GitHub Actions artifacts.
15. Send the final email with DOCX + PDF + XLSX attachments.

## Recipient configuration
Both the first News Detection List and the final report use the same `recipients()` chain:
1. `REPORT_RECIPIENTS_SCHEDULED` (preferred)
2. `REPORT_RECIPIENTS_30MIN` (legacy compatibility fallback)
3. `REPORT_RECIPIENTS` (general fallback)

Update the preferred list in GitHub: **Repository → Settings → Secrets and variables → Actions → Repository secrets → `REPORT_RECIPIENTS_SCHEDULED`**. Use comma, semicolon or newline separated addresses. The actual addresses are not stored in repository code.

The hourly monitor continues to use `REPORT_RECIPIENTS_30MIN` by default and is unchanged by the V10.3 daily-report schedule update.
