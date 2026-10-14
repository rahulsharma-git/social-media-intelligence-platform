-- ============================================================
-- Social Media Intelligence Platform — Build 5.13.3
-- SUPABASE CRON FALLBACK -> GITHUB ACTIONS
--
-- Purpose:
--   GitHub's own schedule remains enabled, but Supabase Cron also
--   dispatches the same workflow every 30 minutes.
--
-- Result:
--   Browser/Chrome is irrelevant.
--   If both schedulers fire in the same half-hour slot, the app's
--   monitoring_runs.slot_key deduplication prevents duplicate routine
--   emails for that slot.
--
-- BEFORE RUNNING THIS FILE:
-- 1) Create a GitHub fine-grained PAT restricted to this repository.
-- 2) Repository permission required: Actions = Read and write.
-- 3) Store that token in Supabase Vault under the name:
--        github_actions_token
--
-- DO NOT commit the real token to GitHub.
-- ============================================================

create extension if not exists pg_cron;
create extension if not exists pg_net;

-- The helper only reads the token at execution time from Vault.
create or replace function public.trigger_smi_github_monitor()
returns bigint
language plpgsql
security definer
set search_path = public, vault, net
as $$
declare
    github_token text;
    request_id bigint;
begin
    select decrypted_secret
      into github_token
      from vault.decrypted_secrets
     where name = 'github_actions_token'
     order by created_at desc
     limit 1;

    if github_token is null or length(trim(github_token)) = 0 then
        raise exception
          'Missing Supabase Vault secret: github_actions_token';
    end if;

    select net.http_post(
        url :=
          'https://api.github.com/repos/rahulsharma-git/social-media-intelligence-platform/actions/workflows/monitor-every-30-min.yml/dispatches',
        headers := jsonb_build_object(
            'Accept', 'application/vnd.github+json',
            'Authorization', 'Bearer ' || github_token,
            'X-GitHub-Api-Version', '2026-03-10',
            'Content-Type', 'application/json',
            'User-Agent', 'Supabase-Cron-SMI'
        ),
        body := jsonb_build_object(
            'ref', 'main',
            'inputs', jsonb_build_object(
                'force_send', 'false',
                'trigger_source', 'supabase-cron'
            )
        ),
        timeout_milliseconds := 15000
    )
    into request_id;

    return request_id;
end;
$$;

revoke all on function public.trigger_smi_github_monitor() from public;
revoke all on function public.trigger_smi_github_monitor() from anon;
revoke all on function public.trigger_smi_github_monitor() from authenticated;

-- Replace the job if it already exists.
do $$
declare
    rec record;
begin
    -- Remove either the legacy half-hour job or a prior hourly job so this
    -- script is safe to run repeatedly without creating duplicate dispatches.
    for rec in
        select jobid
          from cron.job
         where jobname in ('smi-github-monitor-every-30-min', 'smi-github-monitor-hourly')
    loop
        perform cron.unschedule(rec.jobid);
    end loop;
end $$;

-- pg_cron fallback now dispatches once per hour at minute 07.
select cron.schedule(
    'smi-github-monitor-hourly',
    '7 * * * *',
    $$select public.trigger_smi_github_monitor();$$
);

-- Verification queries:
select jobid, jobname, schedule, active
from cron.job
where jobname in ('smi-github-monitor-every-30-min', 'smi-github-monitor-hourly');

-- Recent scheduler executions:
select d.jobid, d.status, d.start_time, d.end_time, d.return_message
from cron.job_run_details d
join cron.job j on j.jobid = d.jobid
where j.jobname = 'smi-github-monitor-hourly'
order by start_time desc
limit 20;
