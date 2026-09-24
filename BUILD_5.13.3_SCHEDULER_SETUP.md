# Build 5.13.3 — Dual Scheduler Setup

This build keeps GitHub Actions' native schedule and adds an optional
Supabase Cron fallback that dispatches the same workflow every 30 minutes.

## Why dual scheduling?

GitHub documents that scheduled workflow runs can be delayed, and in sufficiently
high load conditions some queued scheduled jobs may be dropped. Build 5.13.3
therefore uses:

1. GitHub native schedule at :07 and :37 IST.
2. Supabase Cron at the same half-hour cadence as a fallback trigger.

The monitor already deduplicates by `monitoring_runs.slot_key`, so if both
triggers arrive in the same half-hour slot the routine report is not emailed twice.

## Step 1 — Replace the workflow

Replace:

`.github/workflows/monitor-every-30-min.yml`

with the Build 5.13.3 version and commit it to the default branch (`main`).

The schedule is explicitly:

- 07 minutes past every hour, Asia/Kolkata
- 37 minutes past every hour, Asia/Kolkata

## Step 2 — Replace background_monitor.py

Replace the repository's existing `background_monitor.py` with the Build 5.13.3 file.

## Step 3 — Create a GitHub fine-grained PAT

In GitHub account settings create a fine-grained personal access token:

- Repository access: only `social-media-intelligence-platform`
- Repository permission: **Actions — Read and write**
- Choose a sensible expiry and renew it before expiry.

Do not put the token in source code or GitHub files.

## Step 4 — Store PAT in Supabase Vault

Store the token in Supabase Vault with this exact secret name:

`github_actions_token`

The SQL scheduler only reads it from Vault at runtime.

## Step 5 — Run the scheduler SQL once

Open Supabase SQL Editor and run:

`supabase_dual_scheduler_build5.13.3.sql`

It enables pg_cron/pg_net, installs the dispatch helper, and creates the
30-minute job.

## Step 6 — Verify

In Supabase SQL Editor:

```sql
select jobid, jobname, schedule, active
from cron.job
where jobname = 'smi-github-monitor-every-30-min';
```

You should see `active = true`.

After the next :07 or :37 slot, check GitHub Actions. A Supabase-triggered run
shows `trigger_source = supabase-cron` in the run summary.

## Important

For normal 30-minute runs `force_send=false`. Therefore two schedulers firing in
one slot should still produce only one routine email per configured recipient.

Manual runs can still be used for testing.
