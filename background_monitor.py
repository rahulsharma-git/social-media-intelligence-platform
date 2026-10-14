"""Browser-independent hourly monitor for Build 5.19.5.

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
import io
import os
import re
import smtplib
import ssl
import time
import zipfile
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
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


def _encoded_email_size_estimate(raw_bytes: int) -> int:
    """Conservative MIME/base64 size estimate for attachment payload."""
    return int(raw_bytes * 1.40) + 256_000


def _smtp_connect(sender: str, password: str, context):
    """Try Gmail SMTPS first, then STARTTLS fallback."""
    first_error = None
    try:
        smtp = smtplib.SMTP_SSL(
            "smtp.gmail.com", 465, context=context, timeout=45
        )
        smtp.login(sender, password)
        return smtp, "smtp.gmail.com:465/SSL"
    except Exception as exc:
        first_error = exc

    try:
        smtp = smtplib.SMTP("smtp.gmail.com", 587, timeout=45)
        smtp.ehlo()
        smtp.starttls(context=context)
        smtp.ehlo()
        smtp.login(sender, password)
        return smtp, "smtp.gmail.com:587/STARTTLS"
    except Exception as exc:
        raise RuntimeError(
            "Gmail SMTP connection failed on both 465/SSL and "
            f"587/STARTTLS. SSL error={type(first_error).__name__}: "
            f"{first_error}; STARTTLS error={type(exc).__name__}: {exc}"
        ) from exc


def send_email_individually(
    recipients: list[str],
    subject: str,
    body: str,
    attachments: list[tuple[str, bytes, str, str]],
    message_tag: str = "report",
    raw_limit_mb_override: float | None = None,
    require_all_attachments: bool = False,
) -> dict:
    """Send one message per recipient and record SMTP *acceptance* accurately.

    Important: SMTP 250/accepted means Gmail accepted responsibility for the
    message. It is not proof that the recipient inbox displayed the message.
    """
    sender = env("SMTP_EMAIL")
    password = env("SMTP_APP_PASSWORD").replace(" ", "")
    recipients = [x.strip() for x in recipients if x and "@" in x]
    if not recipients:
        return {"accepted": [], "failed": {}, "diagnostics": []}
    if not sender or not password:
        msg = (
            "SMTP_EMAIL and SMTP_APP_PASSWORD are not configured in "
            "GitHub Actions repository secrets."
        )
        return {
            "accepted": [],
            "failed": {x: msg for x in recipients},
            "diagnostics": [],
        }

    # Gmail's nominal message size limit includes MIME/base64 overhead.
    # Keep automated report mail well below the limit for reliability.
    if raw_limit_mb_override is None:
        raw_limit_mb = max(
            1.0, min(env_float("EMAIL_ATTACHMENT_RAW_LIMIT_MB", 10.0), 15.0)
        )
    else:
        raw_limit_mb = max(1.0, min(float(raw_limit_mb_override), 17.0))
    raw_limit = int(raw_limit_mb * 1024 * 1024)

    clean = [
        (str(n), p, str(mt), str(st))
        for n, p, mt, st in attachments
        if p is not None
    ]
    selected = []
    used = 0
    omitted = []
    for item in clean:
        name, payload, mt, st = item
        if used + len(payload) <= raw_limit:
            selected.append(item)
            used += len(payload)
        else:
            omitted.append(name)

    estimated_wire = _encoded_email_size_estimate(used)
    if omitted:
        print(
            "::warning::Email attachment safety cap applied. "
            f"Raw selected={used/(1024*1024):.2f} MB, "
            f"estimated MIME={estimated_wire/(1024*1024):.2f} MB, "
            f"omitted={omitted}"
        )
        if require_all_attachments:
            detail = (
                f"Required attachment exceeds the configured raw-email limit "
                f"of {raw_limit_mb:.1f} MB. Omitted={omitted}. "
                "No attachment-free placeholder email was sent."
            )
            return {
                "accepted": [],
                "failed": {x: detail for x in recipients},
                "diagnostics": [],
                "selected_attachments": [x[0] for x in selected],
                "omitted_attachments": omitted,
                "raw_attachment_bytes": used,
                "estimated_mime_bytes": estimated_wire,
            }

    context = ssl.create_default_context()
    pending = list(recipients)
    accepted: list[str] = []
    failed: dict[str, str] = {}
    diagnostics = []
    last_connection_error = ""

    for attempt in range(1, 4):
        smtp = None
        transport = ""
        try:
            smtp, transport = _smtp_connect(sender, password, context)
            remaining = []
            for recipient in pending:
                message_id = make_msgid(
                    idstring=f"smi-{message_tag}",
                    domain=(sender.split("@", 1)[1] if "@" in sender else None),
                )
                try:
                    msg = EmailMessage()
                    msg["From"] = sender
                    msg["To"] = recipient
                    msg["Subject"] = subject
                    msg["Date"] = formatdate(localtime=False)
                    msg["Message-ID"] = message_id
                    msg["Reply-To"] = sender
                    msg["X-SMI-Message-Type"] = message_tag
                    msg.set_content(body)

                    for filename, payload, maintype, subtype in selected:
                        msg.add_attachment(
                            payload,
                            maintype=maintype,
                            subtype=subtype,
                            filename=filename,
                        )

                    refused = smtp.send_message(
                        msg,
                        from_addr=sender,
                        to_addrs=[recipient],
                    )
                    if refused:
                        failed[recipient] = str(refused)
                        remaining.append(recipient)
                        diagnostics.append(
                            {
                                "recipient": recipient,
                                "message_id": message_id,
                                "transport": transport,
                                "smtp_accepted": False,
                                "detail": str(refused),
                            }
                        )
                    else:
                        accepted.append(recipient)
                        failed.pop(recipient, None)
                        diagnostics.append(
                            {
                                "recipient": recipient,
                                "message_id": message_id,
                                "transport": transport,
                                "smtp_accepted": True,
                                "raw_attachment_bytes": used,
                                "estimated_mime_bytes": estimated_wire,
                                "attachments": [x[0] for x in selected],
                                "omitted_attachments": omitted,
                            }
                        )
                        print(
                            f"SMTP accepted message for {recipient} "
                            f"via {transport}; Message-ID={message_id}; "
                            f"raw_attachments={used/(1024*1024):.2f} MB."
                        )
                except Exception as exc:
                    failed[recipient] = f"{type(exc).__name__}: {exc}"
                    remaining.append(recipient)

            pending = remaining
            try:
                smtp.quit()
            except Exception:
                try:
                    smtp.close()
                except Exception:
                    pass

            if not pending:
                break

        except Exception as exc:
            last_connection_error = f"{type(exc).__name__}: {exc}"
            for recipient in pending:
                failed[recipient] = last_connection_error
        finally:
            if smtp is not None:
                try:
                    smtp.close()
                except Exception:
                    pass

        if pending and attempt < 3:
            time.sleep(attempt * 5)

    if pending and last_connection_error:
        print(f"::warning::SMTP delivery issue: {last_connection_error}")

    return {
        "accepted": accepted,
        "failed": failed,
        "diagnostics": diagnostics,
        "selected_attachments": [x[0] for x in selected],
        "omitted_attachments": omitted,
        "raw_attachment_bytes": used,
        "estimated_mime_bytes": estimated_wire,
    }

def load_x_collection_summary() -> dict:
    path = OUTPUT_DIR / "x_collection_summary.json"
    if not path.exists():
        return {
            "status": "not_run",
            "reason": "x_collection_summary.json was not created",
            "fetched": 0,
            "new_count": 0,
            "upserted": 0,
        }
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception as exc:
        return {
            "status": "summary_error",
            "reason": f"{type(exc).__name__}: {exc}",
            "fetched": 0,
            "new_count": 0,
            "upserted": 0,
        }


def load_local_x_fusion():
    """Direct handoff from x_collector.py in the same GitHub Actions run."""
    path = OUTPUT_DIR / "x_latest_fused.csv"
    if not path.exists():
        return pd.DataFrame(columns=core.STANDARD_COLUMNS)
    try:
        frame = pd.read_csv(path, encoding="utf-8-sig")
        if frame.empty:
            return pd.DataFrame(columns=core.STANDARD_COLUMNS)
        return core.normalize_posts(frame)
    except Exception as exc:
        print(
            f"::warning::Could not load collector handoff {path}: "
            f"{type(exc).__name__}: {exc}"
        )
        return pd.DataFrame(columns=core.STANDARD_COLUMNS)


def _fmt_ts_ist(value):
    if value is None or value == "":
        return "n/a"
    try:
        ts = pd.to_datetime(value, utc=True).tz_convert(IST)
        return ts.strftime("%Y-%m-%d %H:%M:%S IST")
    except Exception:
        return str(value)


def build_analysis(raw_df: pd.DataFrame):
    df = core.run_sentiment_agent(raw_df)
    df = core.run_emotion_agent(df)
    df = core.run_intent_agent(df)
    df["engagement"] = df["likes"] + df["reposts"] + df["replies"]
    df, vectorizer, tfidf, feature_names = core.run_semantic_topic_agent(df)
    narratives = core.run_narrative_agent(
        df, vectorizer, tfidf, feature_names
    )
    trend = core.run_trend_agent(df)
    return df, narratives, trend


def _verify_evidence_zip(filename: str, payload: bytes, expected_count: int) -> dict:
    """Validate evidence ZIP bytes before saving/emailing."""
    if payload is None or len(payload) == 0:
        raise RuntimeError(f"Evidence ZIP is empty: {filename}")

    try:
        with zipfile.ZipFile(io.BytesIO(payload), "r") as zf:
            bad = zf.testzip()
            if bad:
                raise RuntimeError(
                    f"Evidence ZIP contains a corrupt member: {filename} -> {bad}"
                )
            members = [n for n in zf.namelist() if n and not n.endswith("/")]
            pdf_members = [n for n in members if n.lower().endswith(".pdf")]
            manifest_present = any(
                n.lower().endswith("manifest.csv") for n in members
            )
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"Invalid evidence ZIP: {filename}") from exc

    if expected_count > 0 and len(pdf_members) < expected_count:
        raise RuntimeError(
            f"Evidence ZIP {filename} contains {len(pdf_members)} PDFs, "
            f"expected at least {expected_count}."
        )

    return {
        "filename": filename,
        "bytes": len(payload),
        "members": len(members),
        "pdfs": len(pdf_members),
        "manifest_present": bool(manifest_present),
    }


def _send_evidence_messages(
    recipients: list[str],
    evidence_files: list[tuple[str, bytes, str, str]],
    body: str,
    now_ist,
) -> dict:
    """Send each mandatory evidence ZIP in its own email.

    One ZIP per message makes Gmail delivery more reliable and makes failures
    attributable to a specific evidence pack. Each send requires the ZIP to be
    attached; an oversized attachment is reported as a failure rather than
    silently omitted.
    """
    result = {
        "accepted": [],
        "failed": {},
        "messages": [],
        "by_file": {},
    }
    recipients = [r.strip() for r in recipients if r and "@" in r]
    if not recipients or not evidence_files:
        return result

    evidence_limit_mb = max(
        1.0,
        min(env_float("EVIDENCE_EMAIL_RAW_LIMIT_MB", 15.0), 17.0),
    )

    # Give Gmail a short gap after the primary analytical report message.
    time.sleep(4)

    for idx, item in enumerate(evidence_files, start=1):
        filename, payload, maintype, subtype = item
        label = (
            "Social Media Evidence ZIP"
            if "SocialMedia" in filename
            else "News Media Evidence ZIP"
        )
        subject = (
            f"ISRO Intelligence | {label} | "
            + now_ist.strftime("%d %b %Y %H:%M IST")
        )
        message_body = (
            body
            + "\n\n"
            + f"Evidence delivery: {label}\n"
            + f"Attached file: {filename}\n"
            + f"Raw ZIP size: {len(payload)/(1024*1024):.2f} MB"
        )

        ev = send_email_individually(
            recipients,
            subject,
            message_body,
            [item],
            message_tag=f"evidence-{idx}",
            raw_limit_mb_override=evidence_limit_mb,
            require_all_attachments=True,
        )

        file_result = {
            "file": filename,
            "size_mb": round(len(payload) / (1024 * 1024), 3),
            "accepted": list(ev.get("accepted", [])),
            "failed": dict(ev.get("failed", {})),
            "diagnostics": list(ev.get("diagnostics", [])),
        }
        result["messages"].append(file_result)
        result["by_file"][filename] = file_result

        for addr in ev.get("accepted", []):
            if addr not in result["accepted"]:
                result["accepted"].append(addr)

        for addr, error in ev.get("failed", {}).items():
            # Keep both failures if two different ZIPs fail for one recipient.
            key = f"{filename} -> {addr}"
            result["failed"][key] = error

        print(
            f"Evidence send result: file={filename}; "
            f"size={len(payload)/(1024*1024):.2f} MB; "
            f"SMTP accepted={len(ev.get('accepted', []))}; "
            f"failed={len(ev.get('failed', {}))}."
        )

        # Separate SMTP sessions/messages and avoid immediate back-to-back bursts.
        if idx < len(evidence_files):
            time.sleep(3)

    return result


def _parse_handle_set(raw: str, default: str = "") -> set[str]:
    value = str(raw or default)
    return {
        h.strip().lstrip("@").lower()
        for h in re.split(r"[,;\s]+", value)
        if h.strip()
    }


def _x_topic_match(text: str, query: str = "ISRO") -> bool:
    value = str(text or "").lower()
    terms = {
        str(query or "isro").lower(),
        "isro", "@isro", "#isro",
        "indian space research organisation",
        "indian space research organization",
        "gaganyaan", "chandrayaan", "pslv", "gslv", "lvm3",
        "aditya-l1", "aditya l1", "nisar", "spadex", "navic",
    }
    return any(term and term in value for term in terms)


def _select_diverse_x_frame(
    frame: pd.DataFrame,
    limit: int,
    minimum: int,
    per_author_cap: int,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame.copy() if frame is not None else pd.DataFrame()

    work = frame.sort_values("timestamp", ascending=False).copy()
    selected, overflow, counts = [], [], {}
    for idx, row in work.iterrows():
        handle = str(row.get("username", "") or "").strip().lstrip("@").lower() or "unknown"
        if counts.get(handle, 0) < per_author_cap and len(selected) < limit:
            selected.append(idx)
            counts[handle] = counts.get(handle, 0) + 1
        else:
            overflow.append(idx)

    if len(selected) < minimum:
        for idx in overflow:
            if len(selected) >= minimum or len(selected) >= limit:
                break
            selected.append(idx)

    return (
        work.loc[selected]
        .sort_values("timestamp", ascending=False)
        .head(limit)
        .reset_index(drop=True)
    )



def main() -> None:
    now_ist = datetime.now(IST)
    slot_start = now_ist.replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    slot_key = slot_start.strftime("%Y%m%d_%H%M_IST")

    query = env("MONITOR_QUERY", "ISRO")
    alert_threshold = env_float("NEGATIVITY_ALERT_THRESHOLD", 35.0)
    # News remains a rolling 24-hour intelligence window. X is handled
    # separately so stored/manual X does not disappear merely because no
    # live X API token is configured.
    news_lookback_hours = max(1, env_int("BACKGROUND_LOOKBACK_HOURS", 24))
    news_max_records = max(50, min(200, env_int("NEWS_MAX_RECORDS", 150)))
    x_fresh_hours = max(1, env_int("X_MAX_AGE_HOURS", env_int("X_FRESH_HOURS", 24)))
    news_max_age_hours = max(
        1, min(news_lookback_hours, env_int("NEWS_MAX_AGE_HOURS", 24))
    )
    news_history_dedup_days = max(
        1, min(90, env_int("NEWS_HISTORY_DEDUP_DAYS", 30))
    )
    x_fallback_max_posts = min(50, max(10, env_int("X_FALLBACK_MAX_POSTS", 50)))
    x_min_report_posts = max(10, env_int("X_MIN_REPORT_POSTS", env_int("X_MIN_POSTS", 10)))
    x_excluded_handles = _parse_handle_set(
        env("X_EXCLUDE_HANDLES", "isro"),
        default="isro",
    )
    x_per_author_cap = max(1, min(10, env_int("X_MAX_POSTS_PER_AUTHOR", 3)))
    five_pdf_email_limit_mb = max(
        5.0, min(17.0, env_float("EMAIL_FIVE_PDF_RAW_LIMIT_MB", 17.0))
    )
    report_max_records = max(50, env_int("BACKGROUND_MAX_RECORDS", 1500))
    force_send = env_bool("FORCE_SEND", False)
    fail_on_email_error = env_bool("FAIL_ON_EMAIL_ERROR", False)
    email_only_on_change = env_bool("EMAIL_ONLY_ON_CHANGE", False)
    trigger_source = env("TRIGGER_SOURCE", "unknown")

    routine_recipients = parse_recipients("REPORT_RECIPIENTS_30MIN")
    if not routine_recipients:
        # Backward-compatible fallback for earlier builds/secrets.
        routine_recipients = parse_recipients("REPORT_RECIPIENTS")
    alert_recipients = parse_recipients("ALERT_RECIPIENTS")

    print(f"Build 5.18.2 background run: {now_ist:%Y-%m-%d %H:%M:%S IST}")
    print(f"Trigger source: {trigger_source}")
    print(f"Slot: {slot_key}; query={query!r}; news_max_age={news_max_age_hours}h; x_max_age={x_fresh_hours}h")

    table_ok, current_state = monitoring_current(slot_key)
    _, previous_state = monitoring_previous(slot_key)

    routine_delivered = clean_delivered(current_state.get("routine_delivered", []))
    alert_delivered = clean_delivered(current_state.get("alert_delivered", []))
    if force_send:
        routine_delivered.clear()
        alert_delivered.clear()

    # Load persistent posts first. The optional X collector has already run
    # in the workflow, so any newly fetched X rows are visible here.
    stored = load_supabase_posts(limit=5000)
    print(f"Loaded {len(stored):,} stored posts from Supabase.")

    # Directly merge the collector output from this same workflow run.
    # This prevents a successful X collector from being lost to analytics
    # even if Supabase persistence is delayed or a platform value is unusual.
    local_x_fusion = load_local_x_fusion()
    if len(local_x_fusion):
        print(
            f"Loaded {len(local_x_fusion):,} X rows directly from "
            "output/x_latest_fused.csv."
        )
        stored = core.normalize_posts(
            pd.concat([stored, local_x_fusion], ignore_index=True)
        )

    stored_ids_before_news = (
        set(stored["external_id"].astype(str)) if not stored.empty else set()
    )

    stored_news_before = stored[
        stored["platform"].eq("web_news")
    ].copy() if not stored.empty else pd.DataFrame(columns=core.STANDARD_COLUMNS)
    stored_news_story_keys_before = {
        core.news_story_signature(v)
        for v in stored_news_before.get("text", pd.Series(dtype=str)).astype(str)
        if core.news_story_signature(v)
    }

    # Read the X collector diagnostic even when it was disabled/denied.
    x_collection = load_x_collection_summary()
    x_new_count = int(x_collection.get("new_count", 0) or 0)
    x_collector_status = str(x_collection.get("status", "unknown") or "unknown")
    x_collector_reason = str(x_collection.get("reason", "") or "")
    x_source_diag = x_collection.get("sources", {}) if isinstance(
        x_collection.get("sources", {}), dict
    ) else {}

    # Refresh free News independently of the Streamlit browser. Repeated feed
    # items are retained in the 24-hour snapshot but counted as new only when
    # their external_id was not already stored before this run.
    fresh_news = pd.DataFrame(columns=core.STANDARD_COLUMNS)
    news_source = "not fetched"
    new_news_count = 0
    try:
        fresh_news, news_source = core.fetch_free_news(
            query=query,
            timespan=f"{news_max_age_hours}h",
            max_records=news_max_records,
        )
        if len(fresh_news):
            fresh_news = core.normalize_posts(fresh_news)
            fresh_story_keys = {
                core.news_story_signature(v)
                for v in fresh_news["text"].astype(str)
                if core.news_story_signature(v)
            }
            new_news_count = len(
                fresh_story_keys - stored_news_story_keys_before
            )
            saved = upsert_posts(fresh_news)
            print(
                f"Fetched {len(fresh_news):,} unique/relevant News story rows "
                f"via {news_source}; {new_news_count:,} were new story families; "
                f"upserted {saved:,}."
            )
    except Exception as exc:
        print(f"WARNING: News refresh failed: {type(exc).__name__}: {exc}")

    news_fetch_diag = core.get_last_news_fetch_diagnostics() if hasattr(core, "get_last_news_fetch_diagnostics") else {}
    try:
        (OUTPUT_DIR / "news_collection_summary.json").write_text(
            json.dumps(news_fetch_diag, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"::warning::Could not write news_collection_summary.json: {exc}")

    combined_store = core.normalize_posts(pd.concat([stored, fresh_news], ignore_index=True))

    # NEWS: strict current window. Old rows remain in Supabase history but are
    # not allowed to backfill the current report.
    news_cutoff = pd.Timestamp(now_ist - timedelta(hours=news_max_age_hours))
    news_future_limit = pd.Timestamp(now_ist + timedelta(minutes=15))
    news_report_raw = combined_store[
        combined_store["platform"].eq("web_news")
        & (combined_store["timestamp"] >= news_cutoff)
        & (combined_store["timestamp"] <= news_future_limit)
    ].copy()

    # Reject old stories re-surfaced by search/RSS with a newly indexed URL or
    # a slightly changed publisher suffix.
    history_floor = pd.Timestamp(
        now_ist - timedelta(days=news_history_dedup_days)
    )
    news_history = combined_store[
        combined_store["platform"].eq("web_news")
        & (combined_store["timestamp"] < news_cutoff)
        & (combined_store["timestamp"] >= history_floor)
    ].copy()
    news_report_raw, news_resurfacing_diag = core.suppress_resurfaced_news(
        news_report_raw,
        news_history,
        query=query,
    )

    news_report, news_quality_diag = core.deduplicate_news_for_analysis(
        news_report_raw,
        query=query,
        max_records=news_max_records,
        return_diagnostics=True,
    )
    news_report = news_report.sort_values("timestamp", ascending=False)
    news_duplicates_removed = int(
        news_quality_diag.get("exact_story_duplicates_removed", 0)
        + news_quality_diag.get("near_story_duplicates_removed", 0)
    )
    news_irrelevant_removed = int(
        news_quality_diag.get("irrelevant_removed", 0)
    )
    news_resurfaced_removed = int(
        news_resurfacing_diag.get("resurfaced_exact_removed", 0)
        + news_resurfacing_diag.get("resurfaced_near_removed", 0)
    )

    # X objective: public conversation ABOUT ISRO, not the official @isro feed.
    stored_x_all = combined_store[
        combined_store["platform"].isin(["x", "x_manual"])
    ].copy()
    stored_x_all["_handle_key"] = (
        stored_x_all["username"].fillna("").astype(str)
        .str.strip().str.lstrip("@").str.lower()
    )
    official_mask = stored_x_all["_handle_key"].isin(x_excluded_handles)
    topic_mask = stored_x_all["text"].fillna("").astype(str).apply(
        lambda value: _x_topic_match(value, query)
    )
    x_official_excluded_count = int(official_mask.sum())
    x_offtopic_excluded_count = int((~official_mask & ~topic_mask).sum())

    stored_x = stored_x_all[
        (~official_mask) & topic_mask
    ].drop(columns=["_handle_key"], errors="ignore")
    stored_x = stored_x.sort_values("timestamp", ascending=False)

    x_cutoff = pd.Timestamp(now_ist - timedelta(hours=x_fresh_hours))
    x_fresh_count = int((stored_x["timestamp"] >= x_cutoff).sum()) if len(stored_x) else 0

    # Strict freshness: never backfill current X intelligence with older rows.
    stored_x = stored_x[
        (stored_x["timestamp"] >= x_cutoff)
        & (stored_x["timestamp"] <= pd.Timestamp(now_ist + timedelta(minutes=15)))
    ].copy()

    # Suppress repeated text copies before author-diverse sampling.
    # IMPORTANT: use Python's regex engine here, not pandas/pyarrow ``str.replace``.
    # PyArrow/RE2 rejects ``\u0900`` style escapes ("invalid escape sequence: \u"),
    # which previously crashed the scheduled background run after news collection.
    if len(stored_x):
        def _dedup_signature(value: object) -> str:
            text = str(value or "").lower()
            text = re.sub(r"^\s*rt\s+@[a-z0-9_]{1,15}\s*:\s*", "", text, flags=re.I)
            text = re.sub(r"https?://\S+", " ", text, flags=re.I)
            text = re.sub(r"[^\w#@ऀ-ॿ]+", " ", text, flags=re.UNICODE)
            text = re.sub(r"\s+", " ", text).strip()
            return text

        stored_x["_dedup_sig"] = stored_x["text"].fillna("").map(_dedup_signature)
        x_before_text_dedup = len(stored_x)
        stored_x = stored_x.drop_duplicates(
            subset=["_dedup_sig"],
            keep="first",
        ).drop(columns=["_dedup_sig"], errors="ignore")
        x_text_duplicates_removed = int(x_before_text_dedup - len(stored_x))
    else:
        x_text_duplicates_removed = 0

    x_report_limit = min(50, x_fallback_max_posts)
    x_report = _select_diverse_x_frame(
        stored_x,
        limit=x_report_limit,
        minimum=x_min_report_posts,
        per_author_cap=x_per_author_cap,
    )
    x_unique_authors = int(
        x_report["username"].fillna("").astype(str).str.lower().nunique()
    ) if len(x_report) else 0

    if x_report.empty:
        x_data_mode = (
            f"no public-conversation X records within the latest {x_fresh_hours}h"
        )
        x_is_stale_fallback = False
    else:
        x_data_mode = (
            f"{len(x_report)} public-conversation X rows from "
            f"{x_unique_authors} authors; all within latest {x_fresh_hours}h; "
            f"{x_text_duplicates_removed} repeated-text rows suppressed"
        )
        x_is_stale_fallback = False
        if len(x_report) < x_min_report_posts:
            x_data_mode += (
                f"; below minimum target {x_min_report_posts} because only "
                f"{len(x_report)} fresh real rows are available"
            )

    x_latest = x_report["timestamp"].max() if not x_report.empty else None
    x_age_hours = None
    if x_latest is not None and not pd.isna(x_latest):
        x_age_hours = max(0.0, (pd.Timestamp(now_ist) - x_latest).total_seconds() / 3600.0)

    news_latest = news_report["timestamp"].max() if not news_report.empty else None
    new_news_count = min(int(new_news_count), int(len(news_report)))
    news_retained_count = max(0, int(len(news_report)) - int(new_news_count))

    raw = core.normalize_posts(pd.concat([x_report, news_report], ignore_index=True))
    raw = raw.sort_values("timestamp", ascending=False).head(report_max_records)
    raw = raw.sort_values("timestamp").reset_index(drop=True)

    if raw.empty:
        raise RuntimeError(
            "No X fallback records and no News records are available for this run."
        )

    print(
        f"X reporting mode: {x_data_mode}; records={len(x_report)}; "
        f"new_this_cycle={x_new_count}; newest={_fmt_ts_ist(x_latest)}."
    )
    print(
        f"News current-window story snapshot: unique_stories={len(news_report)}; "
        f"max_age={news_max_age_hours}h; new_story_families={new_news_count}; "
        f"retained={news_retained_count}; duplicates_suppressed={news_duplicates_removed}; "
        f"resurfaced_old_stories_suppressed={news_resurfaced_removed}; "
        f"irrelevant_rejected={news_irrelevant_removed}; "
        f"newest={_fmt_ts_ist(news_latest)}."
    )

    df, narratives, trend = build_analysis(raw)

    intel = core.compute_intelligence_summary(df, narratives, trend)
    positive_pct = float(intel["weighted_positive_pct"])
    neutral_pct = float(intel["weighted_neutral_pct"])
    negative_pct = float(intel["weighted_negative_pct"])
    raw_negative_pct = float(intel["raw_negative_pct"])
    sentiment_index = float(intel["sentiment_index"])
    assessment_confidence = int(intel["assessment_confidence"])
    emotional_intensity_pct = float(intel["emotional_intensity_pct"])
    dominant_emotion = str(intel["dominant_emotion"])
    attention_index = int(intel["attention_index"])
    alert_triggered = negative_pct > alert_threshold and len(df) >= 5

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

    # Build three analytical PDFs plus separate evidence packs.
    report_context = {
        "x_data_mode": x_data_mode,
        "x_is_stale_fallback": bool(x_is_stale_fallback),
        "x_collector_status": x_collector_status,
        "x_collector_reason": x_collector_reason,
        "x_new_count": int(x_new_count),
        "x_fresh_count": int(x_fresh_count),
        "x_report_count": int(len(x_report)),
        "x_unique_authors": int(x_unique_authors),
        "x_official_excluded_count": int(x_official_excluded_count),
        "x_offtopic_excluded_count": int(x_offtopic_excluded_count),
        "x_text_duplicates_removed": int(x_text_duplicates_removed),
        "x_latest_ist": _fmt_ts_ist(x_latest),
        "x_age_hours": None if x_age_hours is None else round(float(x_age_hours), 1),
        "news_new_count": int(new_news_count),
        "news_window_count": int(len(news_report)),
        "news_retained_count": int(news_retained_count),
        "news_duplicates_removed": int(news_duplicates_removed),
        "news_resurfaced_removed": int(news_resurfaced_removed),
        "news_irrelevant_removed": int(news_irrelevant_removed),
        "news_latest_ist": _fmt_ts_ist(news_latest),
        "news_source": news_source,
        "news_window_hours": int(news_lookback_hours),
        "weighted_negative_pct": round(negative_pct, 2),
        "raw_negative_pct": round(raw_negative_pct, 2),
        "sentiment_index": round(sentiment_index, 2),
        "assessment_confidence": int(assessment_confidence),
        "attention_index": int(attention_index),
        "dominant_emotion": dominant_emotion,
        "emotional_intensity_pct": round(emotional_intensity_pct, 1),
        "news_fetch_diagnostics": news_fetch_diag,
    }
    stamp = now_ist.strftime("%Y%m%d_%H%M%S_IST")
    social_pdf = core.build_social_media_report(
        df, alert_threshold=alert_threshold, query=query, run_context=report_context
    )
    news_pdf = core.build_news_media_report(
        df, alert_threshold=alert_threshold, query=query, run_context=report_context
    )
    combined_pdf = core.build_combined_intelligence_report(
        df, alert_threshold=alert_threshold, query=query, run_context=report_context
    )

    report_files = [
        (f"ISRO_Social_Media_Intelligence_{stamp}.pdf", social_pdf, "application", "pdf"),
        (f"ISRO_News_Media_Intelligence_{stamp}.pdf", news_pdf, "application", "pdf"),
        (f"ISRO_Combined_Intelligence_{stamp}.pdf", combined_pdf, "application", "pdf"),
    ]
    for filename, payload, _, _ in report_files:
        (OUTPUT_DIR / filename).write_bytes(payload)
        print(f"Generated {filename} ({len(payload)/(1024*1024):.2f} MB).")

    x_subset, x_narr, _ = core._report_subset_analysis(df, "social")
    news_subset, news_narr, _ = core._report_subset_analysis(df, "news")
    x_zip, x_evidence_count = core.build_x_evidence_zip(x_subset, x_narr)
    news_zip, news_evidence_count = core.build_news_evidence_zip(news_subset, news_narr)

    x_evidence_pdf, x_book_count = core.build_evidence_book_from_zip(
        x_zip, title="ISRO Social Media Evidence Book"
    )
    news_evidence_pdf, news_book_count = core.build_evidence_book_from_zip(
        news_zip, title="ISRO News Media Evidence Book"
    )
    if int(x_book_count) != int(x_evidence_count):
        raise RuntimeError(
            f"Social evidence PDF count mismatch: book={x_book_count}, zip={x_evidence_count}"
        )
    if int(news_book_count) != int(news_evidence_count):
        raise RuntimeError(
            f"News evidence PDF count mismatch: book={news_book_count}, zip={news_evidence_count}"
        )

    evidence_pdf_files = [
        (f"ISRO_SocialMedia_Evidence_{stamp}.pdf", x_evidence_pdf, "application", "pdf"),
        (f"ISRO_NewsMedia_Evidence_{stamp}.pdf", news_evidence_pdf, "application", "pdf"),
    ]
    for filename, payload, _, _ in evidence_pdf_files:
        (OUTPUT_DIR / filename).write_bytes(payload)
        print(
            f"Generated consolidated evidence PDF {filename} "
            f"({len(payload)/(1024*1024):.2f} MB)."
        )

    evidence_files = [
        (f"ISRO_SocialMedia_Evidence_{stamp}.zip", x_zip, "application", "zip"),
        (f"ISRO_NewsMedia_Evidence_{stamp}.zip", news_zip, "application", "zip"),
    ]

    evidence_validation = []
    expected_counts = {
        evidence_files[0][0]: int(x_evidence_count),
        evidence_files[1][0]: int(news_evidence_count),
    }
    for filename, payload, _, _ in evidence_files:
        check = _verify_evidence_zip(
            filename,
            payload,
            expected_counts.get(filename, 0),
        )
        evidence_validation.append(check)
        output_path = OUTPUT_DIR / filename
        output_path.write_bytes(payload)
        if not output_path.exists() or output_path.stat().st_size != len(payload):
            raise RuntimeError(
                f"Evidence ZIP write verification failed: {output_path}"
            )
        print(
            f"Generated and verified {filename} "
            f"({len(payload)/(1024*1024):.2f} MB; "
            f"{check['pdfs']} PDFs; manifest={check['manifest_present']})."
        )

    five_pdf_files = report_files + evidence_pdf_files
    attachments = five_pdf_files
    pdf_filename = report_files[2][0]
    run_record["pdf_filename"] = pdf_filename
    status_lines = [
        "Automated ISRO intelligence report pack (Social Media + News Media + Combined).",
        "",
        f"Run time: {now_ist:%Y-%m-%d %H:%M:%S IST}",
        f"Scheduled slot: {slot_start:%Y-%m-%d %H:%M IST}",
        f"Query: {query}",
        f"Records analyzed: {len(df):,}",
        f"X/manual X records in report: {x_records:,}",
        f"X new this cycle: {x_new_count:,}",
        f"X data mode: {x_data_mode}",
        f"X conversation authors represented: {x_unique_authors}",
        f"Official X rows excluded: {x_official_excluded_count}",
        f"Off-topic X rows excluded: {x_offtopic_excluded_count}",
        f"X collector status: {x_collector_status}",
        (
            "X source counts: "
            f"stored={x_source_diag.get('existing_supabase', 0)}, "
            f"CSV={x_source_diag.get('csv_inbox_rows', 0)}, "
            f"web URLs={x_source_diag.get('web_indexed_urls', 0)}, "
            f"profile={x_source_diag.get('profile_timeline_rows', 0)}, "
            f"hydrated={x_source_diag.get('hydrated_url_rows', 0)}"
        ),
        f"Newest X record: {_fmt_ts_ist(x_latest)}" + (f" ({x_age_hours:.1f}h old)" if x_age_hours is not None else ""),
        f"Web News records in rolling {news_lookback_hours}h: {news_records:,}",
        f"News new this cycle: {new_news_count:,}",
        f"News retained from prior fetches: {news_retained_count:,}",
        f"Newest News record: {_fmt_ts_ist(news_latest)}",
        f"Confidence-weighted Positive: {positive_pct:.1f}%",
        f"Confidence-weighted Neutral: {neutral_pct:.1f}%",
        f"Confidence-weighted Negative: {negative_pct:.1f}%",
        f"Raw negative label share: {raw_negative_pct:.1f}%",
        f"Sentiment index: {sentiment_index:+.1f}/100",
        f"Dominant expressive emotion: {dominant_emotion}",
        f"Mean emotional intensity: {emotional_intensity_pct:.1f}%",
        f"Attention index: {attention_index}/100",
        f"Assessment confidence: {assessment_confidence}/100",
        f"Alert threshold: {alert_threshold:.1f}%",
        f"Free news source: {news_source}",
        f"Scheduler trigger: {trigger_source}",
        f"Social evidence pages: {x_evidence_count}",
        f"News evidence pages: {news_evidence_count}",
        "Email pack: 5 PDFs in one message (Social, News, Combined, Social Evidence, News Evidence)",
        "",
        "All displayed/report timestamps are India Standard Time (IST).",
        "Email status means SMTP accepted, not guaranteed inbox placement.",
        "X data targets public conversation ABOUT ISRO. The official @isro account is "
        "excluded by default; acquisition remains free and uses no paid Bearer Token.",
    ]
    body = "\n".join(status_lines)

    # Routine email: every hourly scheduled slot; only pending recipients are retried.
    routine_pending = [
        addr
        for addr in routine_recipients
        if addr.lower() not in routine_delivered
    ]
    routine_failed = {}
    no_new_content = (int(x_new_count) == 0 and int(new_news_count) == 0)
    if email_only_on_change and no_new_content and not alert_triggered and not force_send:
        routine_pending = []
        print("Routine email skipped because EMAIL_ONLY_ON_CHANGE=true and no new X/News IDs were found.")

    if routine_pending:
        subject_prefix = "ALERT · " if alert_triggered else ""
        routine_subject = (
            f"{subject_prefix}ISRO Intelligence | "
            f"Weighted Neg {negative_pct:.1f}% | "
            f"X {x_records} (+{x_new_count}) | "
            f"News {news_records} (+{new_news_count}) | "
            + now_ist.strftime("%d %b %Y %H:%M IST")
        )
        # One routine email per recipient with exactly five PDFs:
        # 3 intelligence reports + 2 consolidated evidence books.
        result = send_email_individually(
            routine_pending,
            routine_subject,
            body,
            five_pdf_files,
            message_tag="routine-five-pdf-pack",
            raw_limit_mb_override=five_pdf_email_limit_mb,
            require_all_attachments=True,
        )
        routine_delivered.update(x.lower() for x in result["accepted"])
        routine_failed = result["failed"]
        print(
            f"Routine email SMTP accepted={len(result['accepted'])}, "
            f"failed={len(result['failed'])}."
        )

        # Evidence ZIP archives remain in the GitHub artifact; no second email is sent.\n    elif routine_recipients:
        print("Routine email already completed for this slot; no duplicate send.")
    else:
        print("No routine recipients configured (REPORT_RECIPIENTS_30MIN remains supported as a legacy secret); routine email skipped.")

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
                f"ALERT: ISRO weighted negativity {negative_pct:.1f}% — "
                + now_ist.strftime("%d %b %Y %H:%M IST")
            )
            result = send_email_individually(
                alert_pending,
                alert_subject,
                body,
                five_pdf_files,
                message_tag="alert-five-pdf-pack",
                raw_limit_mb_override=five_pdf_email_limit_mb,
                require_all_attachments=True,
            )
            alert_delivered.update(x.lower() for x in result["accepted"])
            alert_failed = result["failed"]
            print(
                f"Alert email SMTP accepted={len(result['accepted'])}, "
                f"failed={len(result['failed'])}."
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
        "build": "5.18.2",
        "run_time_ist": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
        "slot_key": slot_key,
        "records": len(df),
        "x_records": x_records,
        "news_records": news_records,
        "x_new_count": int(x_new_count),
        "x_data_mode": x_data_mode,
        "x_unique_authors": int(x_unique_authors),
        "x_official_excluded_count": int(x_official_excluded_count),
        "x_offtopic_excluded_count": int(x_offtopic_excluded_count),
        "x_collector_status": x_collector_status,
        "x_latest_ist": _fmt_ts_ist(x_latest),
        "x_age_hours": None if x_age_hours is None else round(float(x_age_hours), 1),
        "news_new_count": int(new_news_count),
        "news_retained_count": int(news_retained_count),
        "news_duplicates_removed": int(news_duplicates_removed),
        "news_irrelevant_removed": int(news_irrelevant_removed),
        "news_latest_ist": _fmt_ts_ist(news_latest),
        "positive_pct": round(positive_pct, 3),
        "neutral_pct": round(neutral_pct, 3),
        "negative_pct": round(negative_pct, 3),
        "raw_negative_pct": round(raw_negative_pct, 3),
        "sentiment_index": round(sentiment_index, 3),
        "dominant_emotion": dominant_emotion,
        "emotional_intensity_pct": round(emotional_intensity_pct, 2),
        "attention_index": attention_index,
        "assessment_confidence": assessment_confidence,
        "news_fetch_diagnostics": news_fetch_diag,
        "alert_threshold": alert_threshold,
        "alert_triggered": alert_triggered,
        "crossed_up": crossed_up,
        "routine_delivered": sorted(routine_delivered),
        "alert_delivered": sorted(alert_delivered),
        "pdf_filename": pdf_filename,
        "report_files": [x[0] for x in report_files],
        "email_pdf_files": [x[0] for x in five_pdf_files],
        "evidence_pdf_files": [x[0] for x in evidence_pdf_files],
        "evidence_zip_files": [x[0] for x in evidence_files],
        "evidence_validation": evidence_validation,
        "x_evidence_count": x_evidence_count,
        "news_evidence_count": news_evidence_count,
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
            f"- Build: **5.18.2**",
            f"- Run time: **{now_ist:%Y-%m-%d %H:%M:%S IST}**",
            f"- Status: **{run_record['status']}**",
            f"- Records analyzed: **{len(df):,}**",
            f"- X/manual X in report: **{x_records:,}**",
            f"- New X this cycle: **{x_new_count:,}**",
            f"- X data mode: **{x_data_mode}**",
            f"- X conversation authors represented: **{x_unique_authors}**",
            f"- Official X rows excluded: **{x_official_excluded_count}**",
            f"- Off-topic X rows excluded: **{x_offtopic_excluded_count}**",
            f"- Repeated-text X rows suppressed: **{x_text_duplicates_removed}**",
            f"- X freshness window: **latest {x_fresh_hours}h only**",
            f"- X collector status: **{x_collector_status}**",
            f"- Newest X: **{_fmt_ts_ist(x_latest)}**",
            f"- Unique News story families in rolling {news_lookback_hours}h: **{news_records:,}**",
            f"- New News story families this cycle: **{new_news_count:,}**",
            f"- Retained News story families: **{news_retained_count:,}**",
            f"- Duplicate/near-duplicate News rows suppressed: **{news_duplicates_removed:,}**",
            f"- Resurfaced old News stories suppressed: **{news_resurfaced_removed:,}**",
            f"- Irrelevant fuzzy News matches rejected: **{news_irrelevant_removed:,}**",
            f"- News freshness window: **latest {news_max_age_hours}h only**",
            f"- Weighted Positive: **{positive_pct:.1f}%**",
            f"- Weighted Neutral: **{neutral_pct:.1f}%**",
            f"- Weighted Negative: **{negative_pct:.1f}%**",
            f"- Raw negative label share: **{raw_negative_pct:.1f}%**",
            f"- Sentiment index: **{sentiment_index:+.1f}/100**",
            f"- Dominant expressive emotion: **{dominant_emotion}**",
            f"- Emotional intensity: **{emotional_intensity_pct:.1f}%**",
            f"- Attention index: **{attention_index}/100**",
            f"- Assessment confidence: **{assessment_confidence}/100**",
            f"- Combined PDF: `{pdf_filename}`",
            f"- Social evidence PDF: **{evidence_pdf_files[0][0]}** ({x_evidence_count} evidence pages)",
            f"- News evidence PDF: **{evidence_pdf_files[1][0]}** ({news_evidence_count} evidence pages)",
            f"- Evidence ZIP archives retained in artifact: **2**",
            f"- Routine email attachments: **5 PDFs in one message**",
            f"- Routine recipients SMTP-accepted: **{len(routine_delivered)}**",
            f"- Alert recipients SMTP-accepted: **{len(alert_delivered)}**",
            "",
            "News collection: **multi-source fusion over the past 24 hours by default**.",
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
    # Hourly scheduler continues to run, generate PDFs, and preserve data.
    # Set GitHub variable FAIL_ON_EMAIL_ERROR=true if a red workflow is desired.
    if failed_parts:
        message = "Some email deliveries failed: " + " | ".join(failed_parts)
        print("::warning::" + message)
        if fail_on_email_error:
            raise RuntimeError(message)


if __name__ == "__main__":
    main()
