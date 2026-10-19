# Build 6.0.1 — Stability Fix (Master Query V8.0)

This ZIP is a patch to the **uploaded Build 6.0** (not Build 5.20.0).

- Free AI providers: HTTP 429, authorization, or service errors disable that provider for the remainder of the current scheduled run, so Groq is not called dozens of times after exhausting its quota. Other providers are still tried in order, then local rules.
- Analysis caching: repeat analysis of identical article inputs in the same run reuses the first result.
- PDF validation: require the clickable original-publisher URL for **every** article in the PDF; also check extracted title for Latin-language headlines. Indic-script titles are not rejected solely because PDF text extraction mangles their glyphs.
- Existing V8 page separation, header checks, render checks, report delivery and workflows remain intact.

## Deployment
Upload all ZIP files to the existing GitHub repository, preserving `.github/workflows/` paths. Retain your existing repository secrets and variables. No new secrets required. Run `python test_build6_v8.py` and `python test_stability_fix.py`, then manually trigger scheduled-media-reports workflow.

**Limitations:** Tests are offline; live Groq, SMTP and GitHub workflow execution cannot be verified here.
