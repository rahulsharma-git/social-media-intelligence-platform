-- Build 5.19.x — scheduled Morning / Evening / Friday Weekly report ledger.
-- Safe to run independently of the existing 30-minute monitoring tables.

create table if not exists public.scheduled_media_report_runs (
    report_key text primary key,
    edition text not null check (edition in ('morning','evening','weekly')),
    report_date date not null,
    window_start timestamptz not null,
    window_end timestamptz not null,
    generated_at timestamptz not null default now(),
    article_count integer not null default 0,
    docx_filename text,
    pdf_filename text,
    email_status text,
    diagnostics jsonb not null default '{}'::jsonb
);

create index if not exists idx_scheduled_media_report_runs_date
    on public.scheduled_media_report_runs (report_date desc, edition);

create table if not exists public.scheduled_media_report_articles (
    report_key text not null references public.scheduled_media_report_runs(report_key) on delete cascade,
    edition text not null check (edition in ('morning','evening','weekly')),
    report_date date not null,
    article_key text not null,
    publisher text not null,
    canonical_url text not null,
    title text not null,
    published_date date not null,
    published_time_ist text,
    catch_up boolean not null default false,
    primary key (report_key, article_key)
);

create index if not exists idx_scheduled_media_articles_lookup
    on public.scheduled_media_report_articles (report_date desc, edition, article_key);

-- Keep service-role access model consistent with the existing backend worker.
-- If RLS is enabled globally in your project, the GitHub worker should continue
-- using SUPABASE_SECRET_KEY / SUPABASE_SERVICE_KEY (service role).
