# Build 5.13.2 — Supabase upsert conflict hotfix

The GitHub run reached the monitor, generated the PDF, and successfully sent
routine email, but Supabase returned HTTP 409 Conflict for two REST upserts.

Cause:
- `social_posts` uses `external_id` as its unique deduplication key.
- `monitoring_runs` uses `slot_key` as its unique half-hour execution key.
- PostgREST needs the matching `on_conflict` query parameter when an upsert
  targets a UNIQUE column other than the table primary key.

Fix:
- social_posts POST now uses `?on_conflict=external_id`
- monitoring_runs POST now uses `?on_conflict=slot_key`
- 409 errors now print a specific schema/constraint message.

News continues to default to the past 24 hours.
All report timestamps remain IST.

Deploy:
Replace only `background_monitor.py` for this hotfix.
If a 409 persists, run `supabase_build5132_repair.sql` once in Supabase SQL Editor.
