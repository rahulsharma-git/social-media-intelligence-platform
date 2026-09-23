"""Browser-independent 30-minute monitor for Build 5.13.

Designed for GitHub Actions. It does not require an active Streamlit/Chrome
session. Each run:
  1) loads stored posts from Supabase,
  2) refreshes free Web News,
  3) runs the existing intelligence pipeline,
  4) generates the analytical PDF in IST,
  5) emails the PDF,
  6) persists run status/history in Supabase when monitoring_runs exists.
"""

from __future__ import annotations

import json
import os
import re
import smtplib
import ssl
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

# Prevent Streamlit UI execution while reusing the tested analysis/report code.
os.environ["SMI_BACKGROUND_IMPORT"] = "1"
import streamlit_app as core  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def env(name: str, default: str = "") -> str:
    return str(os.getenv(name, default) or default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "y", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)))
    except Exception:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(env(name, str(default)))
    except Exception:
        return default


def parse_recipients(name: str) -> list[str]:
    raw = env(name)
    if not raw:
        return []
    values = [x.strip() for x in re.split(r"[,;\n]+", raw)]
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or "@" not in value:
            continue
        key = value.lower()
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def supabase_config() -> tuple[str, str]:
    url = env("SUPABASE_URL").rstrip("/")
    key = env("SUPABASE_SECRET_KEY") or env("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SECRET_KEY (or SUPABASE_SERVICE_KEY) "
            "must be configured in GitHub Actions repository secrets."
        )
    return url, key


def supabase_headers(prefer: str | None = None) -> dict[str, str]:
    _, key = supabase_config()
    headers = {"apikey": key, "Content-Type": "application/json"}
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    if prefer:
        headers["Prefer"] = prefer
    return headers


def load_supabase_posts(limit: int = 5000) -> pd.DataFrame:
    url, _ = supabase_config()
    r = requests.get(
        f"{url}/rest/v1/social_posts",
        headers=supabase_headers(),
        params={
            "select": (
                "external_id,platform,timestamp,username,text,hashtags,"
                "likes,reposts,replies,followers,source_url"
            ),
            "order": "timestamp.desc",
            "limit": str(limit),
        },
        timeout=45,
    )
    r.raise_for_status()
    rows = r.json() or []
    if not rows:
        return pd.DataFrame(columns=core.STANDARD_COLUMNS)
    frame = pd.DataFrame(rows)
    frame["topic_seed"] = "Stored"
    return core.normalize_posts(frame)


def upsert_posts(frame: pd.DataFrame) -> int:
    if frame is None or frame.empty:
        return 0
    url, _ = supabase_config()
    clean = core.normalize_posts(frame)
    payload = []
    for _, row in clean.iterrows():
        payload.append(
            {
                "external_id": row["external_id"],
                "platform": row["platform"],
                "timestamp": row["timestamp"].isoformat(),
                "username": row["username"],
                "text": row["text"],
                "hashtags": row["hashtags"],
                "likes": int(row["likes"]),
                "reposts": int(row["reposts"]),
                "replies": int(row["replies"]),
                "followers": int(row["followers"]),
                "source_url": row["source_url"] or None,
            }
        )
    r = requests.post(
        f"{url}/rest/v1/social_posts",
        headers=supabase_headers("resolution=merge-duplicates,return=minimal"),
        params={"on_conflict": "external_id"},
        json=payload,
        timeout=60,
    )
    if r.status_code == 409:
        raise RuntimeError(
            "Supabase social_posts upsert conflict. Verify that "
            "social_posts.external_id has a UNIQUE constraint. "
            f"Response: {r.text[:500]}"
        )
    r.raise_for_status()
    return len(payload)


def monitoring_table_get(params: dict[str, str]) -> tuple[bool, list[dict]]:
    """Return (table_available, rows). Missing optional table is non-fatal."""
    url, _ = supabase_config()
    r = requests.get(
        f"{url}/rest/v1/monitoring_runs",
        headers=supabase_headers(),
        params=params,
        timeout=30,
    )
    if r.status_code in (400, 404):
        print(
            "WARNING: monitoring_runs table is unavailable. Run "
            "supabase_build513.sql for persistent dedup/history."
        )
        return False, []
    r.raise_for_status()
    return True, (r.json() or [])


def monitoring_current(slot_key: str) -> tuple[bool, dict]:
    ok, rows = monitoring_table_get(
        {
            "select": "*",
            "slot_key": f"eq.{slot_key}",
            "limit": "1",
        }
    )
    return ok, (rows[0] if rows else {})


def monitoring_previous(slot_key: str) -> tuple[bool, dict]:
    ok, rows = monitoring_table_get(
        {
            "select": "*",
            "slot_key": f"neq.{slot_key}",
            "order": "run_timestamp.desc",
            "limit": "1",
        }
    )
    return ok, (rows[0] if rows else {})


def monitoring_upsert(record: dict) -> bool:
    url, _ = supabase_config()
    r = requests.post(
        f"{url}/rest/v1/monitoring_runs",
        headers=supabase_headers("resolution=merge-duplicates,return=minimal"),
        params={"on_conflict": "slot_key"},
        json=[record],
        timeout=30,
    )
    if r.status_code in (400, 404):
        print("WARNING: monitoring status could not be persisted:", r.text[:300])
        return False
    if r.status_code == 409:
        raise RuntimeError(
            "Supabase monitoring_runs upsert conflict. Verify that "
            "monitoring_runs.slot_key has a UNIQUE constraint. "
            f"Response: {r.text[:500]}"
        )
    r.raise_for_status()
    return True


def clean_delivered(value) -> set[str]:
    if isinstance(value, list):
        return {str(x).strip().lower() for x in value if str(x).strip()}
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return {str(x).strip().lower() for x in parsed if str(x).strip()}
        except Exception:
            pass
    return set()


def send_email_individually(
    recipients: list[str],
    subject: str,
    body: str,
    pdf_bytes: bytes,
    pdf_filename: str,
) -> dict:
    """
    Send one message per recipient.

    Build 5.13.1 retries transient SMTP connection/login failures and returns
    failures to the caller instead of crashing before diagnostics are written.
    """
    sender = env("SMTP_EMAIL")
    password = env("SMTP_APP_PASSWORD").replace(" ", "")

    recipients = [x for x in recipients if x and "@" in x]
    if not recipients:
        return {"sent": [], "failed": {}}

    if not sender or not password:
        msg = (
            "SMTP_EMAIL and SMTP_APP_PASSWORD are not configured in "
            "GitHub Actions repository secrets."
        )
        return {"sent": [], "failed": {x: msg for x in recipients}}

    if len(pdf_bytes) > 18 * 1024 * 1024:
        msg = (
            f"PDF is {len(pdf_bytes)/(1024*1024):.1f} MB; above the "
            "18 MB safe raw attachment limit."
        )
        return {"sent": [], "failed": {x: msg for x in recipients}}

    context = ssl.create_default_context()
    pending = list(recipients)
    sent: list[str] = []
    failed: dict[str, str] = {}

    last_connection_error = ""
    for attempt in range(1, 4):
        try:
            with smtplib.SMTP_SSL(
                "smtp.gmail.com",
                465,
                context=context,
                timeout=45,
            ) as smtp:
                smtp.login(sender, password)

                remaining = []
                for recipient in pending:
                    try:
                        msg = EmailMessage()
                        msg["From"] = sender
                        msg["To"] = recipient
                        msg["Subject"] = subject
                        msg.set_content(body)
                        msg.add_attachment(
                            pdf_bytes,
                            maintype="application",
                            subtype="pdf",
                            filename=pdf_filename,
                        )
                        refused = smtp.send_message(
                            msg,
                            from_addr=sender,
                            to_addrs=[recipient],
                        )
                        if refused:
                            failed[recipient] = str(refused)
                            remaining.append(recipient)
                        else:
                            sent.append(recipient)
                            failed.pop(recipient, None)
                    except Exception as exc:
                        failed[recipient] = f"{type(exc).__name__}: {exc}"
                        remaining.append(recipient)

                pending = remaining
                if not pending:
                    break

        except Exception as exc:
            last_connection_error = f"{type(exc).__name__}: {exc}"
            for recipient in pending:
                failed[recipient] = last_connection_error

        if pending and attempt < 3:
            wait_seconds = attempt * 5
            print(
                f"SMTP attempt {attempt}/3 incomplete; retrying in "
                f"{wait_seconds}s."
            )
            time.sleep(wait_seconds)

    if pending and last_connection_error:
        print(f"::warning::SMTP delivery issue: {last_connection_error}")

    return {"sent": sent, "failed": failed}


def build_analysis(raw_df: pd.DataFrame):
    df = core.run_sentiment_agent(raw_df)
    df = core.run_emotion_agent(df)
    df["engagement"] = df["likes"] + df["reposts"] + df["replies"]
    df, vectorizer, tfidf, feature_names = core.run_semantic_topic_agent(df)
    narratives = core.run_narrative_agent(
        df, vectorizer, tfidf, feature_names
    )
    trend = core.run_trend_agent(df)
    return df, narratives, trend


def main() -> None:
    now_ist = datetime.now(IST)
    slot_start = now_ist.replace(
        minute=(now_ist.minute // 30) * 30,
        second=0,
        microsecond=0,
    )
    slot_key = slot_start.strftime("%Y%m%d_%H%M_IST")

    query = env("MONITOR_QUERY", "ISRO")
    alert_threshold = env_float("NEGATIVITY_ALERT_THRESHOLD", 35.0)
    lookback_hours = max(1, env_int("BACKGROUND_LOOKBACK_HOURS", 24))
    report_max_records = max(50, env_int("BACKGROUND_MAX_RECORDS", 1500))
    force_send = env_bool("FORCE_SEND", False)
    fail_on_email_error = env_bool("FAIL_ON_EMAIL_ERROR", False)

    routine_recipients = parse_recipients("REPORT_RECIPIENTS_30MIN")
    if not routine_recipients:
        # Backward-compatible fallback for earlier builds/secrets.
        routine_recipients = parse_recipients("REPORT_RECIPIENTS")
    alert_recipients = parse_recipients("ALERT_RECIPIENTS")

    print(f"Build 5.13.2 background run: {now_ist:%Y-%m-%d %H:%M:%S IST}")
    print(f"Slot: {slot_key}; query={query!r}; lookback={lookback_hours}h")

    table_ok, current_state = monitoring_current(slot_key)
    _, previous_state = monitoring_previous(slot_key)

    routine_delivered = clean_delivered(current_state.get("routine_delivered", []))
    alert_delivered = clean_delivered(current_state.get("alert_delivered", []))
    if force_send:
        routine_delivered.clear()
        alert_delivered.clear()

    # Load persistent posts first.
    stored = load_supabase_posts(limit=5000)
    print(f"Loaded {len(stored):,} stored posts from Supabase.")

    # Refresh free News independently of the Streamlit browser.
    fresh_news = pd.DataFrame(columns=core.STANDARD_COLUMNS)
    news_source = "not fetched"
    try:
        fresh_news, news_source = core.fetch_free_news(
            query=query,
            timespan="24h",
            max_records=100,
        )
        if len(fresh_news):
            saved = upsert_posts(fresh_news)
            print(f"Fetched/saved {saved:,} news rows via {news_source}.")
    except Exception as exc:
        print(f"WARNING: News refresh failed: {type(exc).__name__}: {exc}")

    raw = pd.concat([stored, fresh_news], ignore_index=True)
    raw = core.normalize_posts(raw)

    cutoff = pd.Timestamp(now_ist - timedelta(hours=lookback_hours))
    raw = raw[raw["timestamp"] >= cutoff].copy()
    raw = raw.sort_values("timestamp", ascending=False).head(report_max_records)
    raw = raw.sort_values("timestamp").reset_index(drop=True)

    if raw.empty:
        raise RuntimeError(
            "No records are available in the configured background lookback window."
        )

    df, narratives, trend = build_analysis(raw)

    positive_pct = float(df["sentiment"].eq("Positive").mean() * 100)
    neutral_pct = float(df["sentiment"].eq("Neutral").mean() * 100)
    negative_pct = float(df["sentiment"].eq("Negative").mean() * 100)
    alert_triggered = negative_pct > alert_threshold

    x_records = int(df["platform"].isin(["x", "x_manual"]).sum())
    news_records = int(df["platform"].eq("web_news").sum())

    previous_negative = None
    if previous_state:
        try:
            previous_negative = float(previous_state.get("negative_pct"))
        except Exception:
            previous_negative = None

    # Alert email is sent only on an upward threshold crossing.
    crossed_up = alert_triggered and (
        previous_negative is None or previous_negative <= alert_threshold
    )

    run_record = {
        "slot_key": slot_key,
        "run_timestamp": now_ist.isoformat(),
        "source_records": int(len(df)),
        "x_records": x_records,
        "news_records": news_records,
        "positive_pct": round(positive_pct, 3),
        "neutral_pct": round(neutral_pct, 3),
        "negative_pct": round(negative_pct, 3),
        "alert_threshold": float(alert_threshold),
        "alert_triggered": bool(alert_triggered),
        "routine_delivered": sorted(routine_delivered),
        "alert_delivered": sorted(alert_delivered),
        "routine_email_sent": len(routine_delivered),
        "alert_email_sent": len(alert_delivered),
        "status": "processing",
        "error": None,
    }
    if table_ok:
        monitoring_upsert(run_record)

    # Generate the same analytical PDF used by the dashboard.
    pdf_bytes = core.build_pdf_report(
        df,
        narratives,
        trend,
        alert_threshold=alert_threshold,
    )
    pdf_filename = (
        "social_media_intelligence_report_"
        + now_ist.strftime("%Y%m%d_%H%M%S_IST")
        + ".pdf"
    )
    pdf_path = OUTPUT_DIR / pdf_filename
    pdf_path.write_bytes(pdf_bytes)
    run_record["pdf_filename"] = pdf_filename
    print(f"Generated {pdf_filename} ({len(pdf_bytes)/(1024*1024):.2f} MB).")

    status_lines = [
        "Automated Social Media Intelligence background report.",
        "",
        f"Run time: {now_ist:%Y-%m-%d %H:%M:%S IST}",
        f"Scheduled slot: {slot_start:%Y-%m-%d %H:%M IST}",
        f"Query: {query}",
        f"Records analyzed: {len(df):,}",
        f"X/manual X records: {x_records:,}",
        f"Web News records: {news_records:,}",
        f"Positive: {positive_pct:.1f}%",
        f"Neutral: {neutral_pct:.1f}%",
        f"Negative: {negative_pct:.1f}%",
        f"Alert threshold: {alert_threshold:.1f}%",
        f"Free news source: {news_source}",
        "",
        "All displayed/report timestamps are India Standard Time (IST).",
        "X data in this background run comes from posts already stored in Supabase; "
        "the free background collector refreshes Web News, not authenticated X search.",
    ]
    body = "\n".join(status_lines)

    # Routine email: every scheduled slot; only pending recipients are retried.
    routine_pending = [
        addr
        for addr in routine_recipients
        if addr.lower() not in routine_delivered
    ]
    routine_failed = {}
    if routine_pending:
        subject_prefix = "ALERT · " if alert_triggered else ""
        routine_subject = (
            f"{subject_prefix}ISRO Social Intelligence Report — "
            + now_ist.strftime("%d %b %Y %H:%M IST")
        )
        result = send_email_individually(
            routine_pending,
            routine_subject,
            body,
            pdf_bytes,
            pdf_filename,
        )
        routine_delivered.update(x.lower() for x in result["sent"])
        routine_failed = result["failed"]
        print(
            f"Routine email: sent={len(result['sent'])}, failed={len(result['failed'])}."
        )
    elif routine_recipients:
        print("Routine email already completed for this slot; no duplicate send.")
    else:
        print("No REPORT_RECIPIENTS_30MIN configured; routine email skipped.")

    # Alert-only email: only on an upward threshold crossing.
    alert_failed = {}
    if crossed_up and alert_recipients:
        alert_pending = [
            addr
            for addr in alert_recipients
            if addr.lower() not in alert_delivered
        ]
        if alert_pending:
            alert_subject = (
                f"ALERT: ISRO negativity {negative_pct:.1f}% — "
                + now_ist.strftime("%d %b %Y %H:%M IST")
            )
            result = send_email_individually(
                alert_pending,
                alert_subject,
                body,
                pdf_bytes,
                pdf_filename,
            )
            alert_delivered.update(x.lower() for x in result["sent"])
            alert_failed = result["failed"]
            print(
                f"Alert email: sent={len(result['sent'])}, failed={len(result['failed'])}."
            )
    elif alert_triggered:
        print("Negativity remains above threshold; no duplicate crossing alert email.")

    failed_parts = []
    if routine_failed:
        failed_parts.append("routine=" + json.dumps(routine_failed, ensure_ascii=False))
    if alert_failed:
        failed_parts.append("alert=" + json.dumps(alert_failed, ensure_ascii=False))

    run_record.update(
        {
            "routine_delivered": sorted(routine_delivered),
            "alert_delivered": sorted(alert_delivered),
            "routine_email_sent": len(routine_delivered),
            "alert_email_sent": len(alert_delivered),
            "status": "partial_email_failure" if failed_parts else "success",
            "error": " | ".join(failed_parts) if failed_parts else None,
        }
    )
    if table_ok:
        monitoring_upsert(run_record)

    summary = {
        "build": "5.13.2",
        "run_time_ist": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
        "slot_key": slot_key,
        "records": len(df),
        "x_records": x_records,
        "news_records": news_records,
        "positive_pct": round(positive_pct, 3),
        "neutral_pct": round(neutral_pct, 3),
        "negative_pct": round(negative_pct, 3),
        "alert_threshold": alert_threshold,
        "alert_triggered": alert_triggered,
        "crossed_up": crossed_up,
        "routine_delivered": sorted(routine_delivered),
        "alert_delivered": sorted(alert_delivered),
        "pdf_filename": pdf_filename,
        "status": run_record["status"],
        "error": run_record["error"],
    }
    (OUTPUT_DIR / "background_run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary_md = "\n".join(
        [
            "# SMI Background Monitor",
            "",
            f"- Build: **5.13.2**",
            f"- Run time: **{now_ist:%Y-%m-%d %H:%M:%S IST}**",
            f"- Status: **{run_record['status']}**",
            f"- Records analyzed: **{len(df):,}**",
            f"- X/manual X: **{x_records:,}**",
            f"- Web News: **{news_records:,}**",
            f"- Positive: **{positive_pct:.1f}%**",
            f"- Neutral: **{neutral_pct:.1f}%**",
            f"- Negative: **{negative_pct:.1f}%**",
            f"- PDF: `{pdf_filename}`",
            f"- Routine recipients delivered: **{len(routine_delivered)}**",
            f"- Alert recipients delivered: **{len(alert_delivered)}**",
            "",
            "News collection default: **past 24 hours**.",
            (
                "Email errors: " + run_record["error"]
                if run_record["error"]
                else "Email errors: none"
            ),
        ]
    )
    (OUTPUT_DIR / "github_step_summary.md").write_text(
        summary_md,
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    # Email failures are recorded but are non-fatal by default so the
    # 30-minute scheduler continues to run, generate PDFs, and preserve data.
    # Set GitHub variable FAIL_ON_EMAIL_ERROR=true if a red workflow is desired.
    if failed_parts:
        message = "Some email deliveries failed: " + " | ".join(failed_parts)
        print("::warning::" + message)
        if fail_on_email_error:
            raise RuntimeError(message)


if __name__ == "__main__":
    main()
