-- Build 5.13.2 Supabase upsert repair/check
-- Safe to run in Supabase SQL Editor.
-- The existing Build 5.13 schema should already have these unique constraints.
-- This script adds them only if they are missing.

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conrelid = 'public.social_posts'::regclass
          and contype = 'u'
          and pg_get_constraintdef(oid) ilike '%(external_id)%'
    ) then
        alter table public.social_posts
            add constraint social_posts_external_id_key unique (external_id);
    end if;
end $$;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conrelid = 'public.monitoring_runs'::regclass
          and contype = 'u'
          and pg_get_constraintdef(oid) ilike '%(slot_key)%'
    ) then
        alter table public.monitoring_runs
            add constraint monitoring_runs_slot_key_key unique (slot_key);
    end if;
end $$;

create index if not exists monitoring_runs_run_timestamp_idx
    on public.monitoring_runs (run_timestamp desc);
