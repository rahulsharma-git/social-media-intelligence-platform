# Build 6.0.4 — PDF hyperlink-validation hotfix

Fixes GitHub scheduled-report failures such as:

    RuntimeError: PDF validation failed: source hyperlink missing for article: Prime Industries Subsidiary Linga Agri ...

## Root cause

The prior validator required every original source URL to survive DOCX-to-PDF conversion as a PDF hyperlink annotation. LibreOffice can omit or transform annotations even when the article remains in the rendered PDF and the source link is present in the DOCX.

## Changes

- Added strict DOCX relationship validation for every report article's canonical source URL before conversion. An actually missing DOCX link still stops publication.
- Changed missing PDF link annotations to logged warnings, not fatal errors. Missing/empty canonical source URLs still fail.
- Retained the PDF header, Latin headline extraction, Relevant Space News page separation, and page-render checks.
- Added regression tests that create a real DOCX and PDF and simulate a PDF whose annotations have been stripped.
- Retains Master Query V8.0, Groq fallback, report-date hotfix, and email body news list.

## Deployment

Extract the ZIP into the repository root, preserving `.github/workflows`, and commit. Run the GitHub Actions workflow manually. If your run is already marked as emailed, follow your existing `FORCE_SCHEDULED_SEND` setting to resend only when appropriate.

**Limit:** A PDF link-annotation warning means the PDF's clickable link may not work even though the Word link is valid. This fix prevents unnecessary delivery failure, but does not claim to repair every converter-produced PDF annotation. Verify the PDF link manually if clickable PDF links are mandatory.
