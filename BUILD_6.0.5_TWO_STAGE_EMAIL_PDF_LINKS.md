# Build 6.0.5 — Two-stage email and mandatory PDF links

Master Query V8.0 remains unchanged. All Build 6.0.4 features retained.

1. After final article eligibility, archive-based deduplication and sorting, the workflow sends a **News Detection List** email with serial number, article title (hyperlinked to original publisher), publisher/author and publication date/time. The first email has no attachments, tone, impact analysis or suggested action. It is sent before the official-site remarks checks and DOCX/PDF generation. If the later stage fails, this first email can still be received. A failed first-email SMTP call is logged and does not prevent the report stage.
2. The **Media Report** email is separate and carries both DOCX and PDF attachments, with the normal report content. It is sent only after DOCX/PDF validation succeeds.
3. PDF source links are mandatory. Before final PDF validation, the worker locates every visible `Source: Article` label, checks its original publisher URI and inserts a clickable PDF annotation where LibreOffice omitted one. Repair fails closed if the label count differs from article count or an article URL is missing. Final PDF validation then strictly requires every canonical URI annotation. It never downgrades a missing PDF hyperlink to a warning.
4. The new PDF annotation repair uses `PyMuPDF>=1.24,<2` (added to requirements.txt).

Deploy the full ZIP contents over the existing GitHub repository, including `.github/workflows` and requirements.txt. Run a manual Morning/Evening workflow test. Live SMTP and GitHub execution cannot be tested offline.
