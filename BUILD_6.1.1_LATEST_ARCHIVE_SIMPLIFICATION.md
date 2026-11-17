# Build 6.1.1 — latest-archive.csv simplification

- The scheduled report always reads `latest-archive.csv` from the repository root.
- No `CONSOLIDATED_ARCHIVE_FILE` GitHub variable is required.
- No old archive is auto-selected and no XLSX archive fallback is used.
- Replace the same `latest-archive.csv` file whenever a newer manually maintained archive is available.
- The file is read-only to the workflow; it is never appended, regenerated, renamed or overwritten by scheduled runs.
- Fixed historical expectations such as the 16-Nov filename / 754-row count were removed so newer archives validate by their actual content.
