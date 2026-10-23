# Build 6.0.6 — Visible original source URL in News Detection List

Master Query V8.0 and all Build 6.0.5 functionality remain unchanged.

The first **News Detection List** email now shows the original publisher URL as a separate, complete line for every article:

- Article title is plain text, not a hidden hyperlink.
- A separate `Source URL: https://...` line displays the full canonical/original publisher URL.
- In the HTML alternative, the visible full URL is also clickable; the anchor text is the URL itself.
- The plain-text email contains the same complete URL so it remains visible/copyable even when HTML is disabled.
- No attachments, tone, impact analysis or suggested action are added to the first email.
- The non-blocking first-email failure behavior and the separate final report email with DOCX/PDF attachments are unchanged.
