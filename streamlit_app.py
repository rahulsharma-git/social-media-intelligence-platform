import base64
import json
import hashlib
import io
import math
import os
import random
import re
import struct
import wave
import zipfile
import smtplib
import ssl
from email.message import EmailMessage
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import plotly.express as px
import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus, urljoin, urlparse, parse_qs, unquote
import streamlit as st
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh
from sklearn.cluster import KMeans
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.metrics import silhouette_score, silhouette_samples
from sklearn.preprocessing import Normalizer
from scipy.sparse import hstack
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pypdf import PdfReader, PdfWriter
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak,
    Table, LongTable, TableStyle, Image as RLImage, KeepTogether
)
from xml.sax.saxutils import escape


if os.environ.get("SMI_BACKGROUND_IMPORT") != "1":
    st.set_page_config(
        page_title="Social Media Intelligence",
        page_icon="📡",
        layout="wide",
    )

    st.title("📡 Social Media Intelligence Platform")
    st.caption("Build 5.18.2 - freshness + cross-cycle dedup + single-email five-PDF pack")

IST = ZoneInfo("Asia/Kolkata")

# User-defined sentiment palette used consistently in dashboard + PDF.
POSITIVE_BLUE = "#1f77b4"
NEGATIVE_RED = "#d62728"
NEUTRAL_GREY = "#9e9e9e"
SENTIMENT_COLORS = {
    "Positive": POSITIVE_BLUE,
    "Negative": NEGATIVE_RED,
    "Neutral": NEUTRAL_GREY,
}


# =========================================================
# STANDARD DATA MODEL
# =========================================================
STANDARD_COLUMNS = [
    "external_id",
    "platform",
    "timestamp",
    "username",
    "text",
    "hashtags",
    "likes",
    "reposts",
    "replies",
    "followers",
    "source_url",
    "topic_seed",
]


def deterministic_id(row):
    material = "|".join([
        str(row.get("platform", "unknown")),
        str(row.get("username", "")),
        str(row.get("timestamp", "")),
        str(row.get("text", "")),
        str(row.get("source_url", "")),
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def normalize_posts(df):
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    result = df.copy()
    result.columns = [str(c).strip().lower() for c in result.columns]

    required = {"timestamp", "username", "text"}
    missing = required - set(result.columns)
    if missing:
        raise ValueError(
            "Missing required column(s): " + ", ".join(sorted(missing))
        )

    defaults = {
        "external_id": "",
        "platform": "x_manual",
        "hashtags": "",
        "likes": 0,
        "reposts": 0,
        "replies": 0,
        "followers": 0,
        "source_url": "",
        "topic_seed": "ISRO",
    }

    for col, default in defaults.items():
        if col not in result.columns:
            result[col] = default

    result["timestamp"] = pd.to_datetime(
        result["timestamp"], errors="coerce", utc=True
    ).dt.tz_convert(IST)
    result = result.dropna(subset=["timestamp", "username", "text"]).copy()

    for col in ["likes", "reposts", "replies", "followers"]:
        result[col] = (
            pd.to_numeric(result[col], errors="coerce")
            .fillna(0)
            .clip(lower=0)
            .astype(int)
        )

    for col in [
        "external_id",
        "platform",
        "username",
        "text",
        "hashtags",
        "source_url",
        "topic_seed",
    ]:
        result[col] = result[col].fillna("").astype(str)

    result["platform"] = result["platform"].replace("", "x_manual")
    result["topic_seed"] = result["topic_seed"].replace("", "ISRO")

    missing_ids = result["external_id"].str.strip().eq("")
    if missing_ids.any():
        result.loc[missing_ids, "external_id"] = result.loc[
            missing_ids
        ].apply(deterministic_id, axis=1)

    return (
        result[STANDARD_COLUMNS]
        .drop_duplicates(subset=["external_id"])
        .sort_values("timestamp")
        .reset_index(drop=True)
    )


# =========================================================
# CSV TEMPLATE
# =========================================================
CSV_TEMPLATE = (
    "timestamp,username,text,hashtags,likes,reposts,replies,"
    "followers,platform,source_url\n"
)

BUILTIN_ISRO_SAMPLE = """timestamp,username,text,hashtags,likes,reposts,replies,followers,platform,source_url
2026-09-20T10:00:00Z,test_user_1,"ISRO and NASA cooperation is expanding","#ISRO #NASA",4,1,1,0,x_manual,
2026-09-20T09:30:00Z,test_user_2,"ISRO budget should be increased for more missions","#ISRO",8,2,3,0,x_manual,
2026-09-20T09:00:00Z,test_user_3,"Proud of ISRO scientists and their achievements","#ISRO",12,3,2,0,x_manual,
2026-09-20T08:30:00Z,test_user_4,"Concern about delays in an upcoming ISRO launch","#ISRO",2,0,1,0,x_manual,
2026-09-20T08:00:00Z,test_user_5,"ISRO satellite capabilities are improving rapidly","#ISRO #satellite",6,1,0,0,x_manual,
2026-09-20T07:30:00Z,test_user_6,"NASA official praises scientific capability being developed by ISRO","#ISRO #NASA",9,2,1,0,x_manual,
2026-09-20T07:00:00Z,test_user_7,"Discussion about recruitment and talent at ISRO","#ISRO",1,0,1,0,x_manual,
2026-09-20T06:30:00Z,test_user_8,"India needs more investment in ISRO research and development","#ISRO",7,1,2,0,x_manual,
"""



# =========================================================
# DEMO DATA
# =========================================================
@st.cache_data
def make_sample_data(n=180):
    random.seed(42)

    templates = [
        ("ISRO", "ISRO successfully completed another important mission milestone.", ["ISRO", "space"]),
        ("ISRO", "Exciting progress from India's space programme and its latest launch.", ["ISRO", "launch"]),
        ("ISRO", "People are waiting for more details about the upcoming ISRO mission.", ["ISRO", "mission"]),
        ("ISRO", "Congratulations to the scientists and engineers behind the mission.", ["ISRO", "science"]),
        ("ISRO", "Some users are questioning the mission timeline and reported delay.", ["ISRO", "delay"]),
        ("ISRO", "A technical issue has raised concern about the next launch window.", ["ISRO", "technical"]),
    ]

    users = [f"sample_user_{i:03d}" for i in range(1, 50)]
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    rows = []

    for _ in range(n):
        topic, text, tags = random.choice(templates)
        ts = now - timedelta(
            hours=random.randint(0, 95),
            minutes=random.randint(0, 59)
        )
        rows.append({
            "external_id": "",
            "platform": "demo",
            "timestamp": ts,
            "username": random.choice(users),
            "text": text,
            "hashtags": " ".join(f"#{t}" for t in tags),
            "likes": max(0, int(random.gauss(25, 20))),
            "reposts": max(0, int(random.gauss(6, 7))),
            "replies": max(0, int(random.gauss(4, 5))),
            "followers": max(10, int(random.lognormvariate(6.0, 1.0))),
            "source_url": "",
            "topic_seed": topic,
        })

    return normalize_posts(pd.DataFrame(rows))


# =========================================================
# SECRETS + SUPABASE
# =========================================================
def get_secret(name, fallback=""):
    try:
        return str(st.secrets.get(name, fallback)).strip()
    except Exception:
        return fallback


def get_secret_value(name, fallback=None):
    """Return a Streamlit secret without coercing lists to strings."""
    try:
        return st.secrets.get(name, fallback)
    except Exception:
        return fallback


def parse_recipient_secret(name):
    """
    Accept either:
      REPORT_RECIPIENTS_30MIN = "a@example.com,b@example.com"
    or:
      REPORT_RECIPIENTS_30MIN = ["a@example.com", "b@example.com"]
    """
    raw = get_secret_value(name, "")

    if raw is None:
        return []

    if isinstance(raw, (list, tuple)):
        values = [str(x).strip() for x in raw]
    else:
        values = [
            x.strip()
            for x in re.split(r"[,;\n]+", str(raw))
        ]

    recipients = []
    seen = set()
    for value in values:
        if not value or "@" not in value:
            continue
        key = value.lower()
        if key not in seen:
            seen.add(key)
            recipients.append(value)

    return recipients


def get_email_config():
    sender = get_secret("SMTP_EMAIL")
    password = get_secret("SMTP_APP_PASSWORD")
    routine_recipients = parse_recipient_secret(
        "REPORT_RECIPIENTS_30MIN"
    )
    alert_recipients = parse_recipient_secret(
        "ALERT_RECIPIENTS"
    )

    return {
        "sender": sender,
        "password": password,
        "routine_recipients": routine_recipients,
        "alert_recipients": alert_recipients,
    }


def smtp_configured():
    cfg = get_email_config()
    return bool(cfg["sender"] and cfg["password"])


def send_email_message(
    recipients,
    subject,
    body,
    pdf_bytes=None,
    pdf_filename=None,
    extra_attachments=None,
):
    """
    Send ONE independent message per recipient through Gmail SMTP.

    This deliberately avoids a shared multi-recipient To: envelope. It makes
    recipient-level delivery observable and prevents one recipient's SMTP
    refusal from hiding the status of the others.

    Returns:
        {
            "sent": ["a@example.com", ...],
            "failed": {"b@example.com": "error text", ...},
        }
    """
    cfg = get_email_config()
    sender = cfg["sender"]
    password = cfg["password"]

    # Normalize and de-duplicate while preserving order.
    clean_recipients = []
    seen = set()
    for raw in recipients:
        address = str(raw).strip()
        if not address or "@" not in address:
            continue
        key = address.lower()
        if key not in seen:
            seen.add(key)
            clean_recipients.append(address)

    if not sender or not password:
        raise RuntimeError(
            "SMTP_EMAIL / SMTP_APP_PASSWORD are not configured "
            "in Streamlit Secrets."
        )

    if not clean_recipients:
        raise RuntimeError(
            "No valid recipients are configured for this email type."
        )

    attachments = []
    if pdf_bytes is not None:
        attachments.append((
            pdf_filename or "social_media_intelligence_report.pdf",
            pdf_bytes,
            "application",
            "pdf",
        ))

    for item in (extra_attachments or []):
        if len(item) != 4:
            raise ValueError(
                "Each extra attachment must be "
                "(filename, bytes, maintype, subtype)."
            )
        filename, payload, maintype, subtype = item
        if payload is not None:
            attachments.append((
                str(filename),
                payload,
                str(maintype),
                str(subtype),
            ))

    # MIME/base64 encoding increases message size. Keep raw attachments under
    # a conservative threshold so Gmail is less likely to reject the message.
    raw_attachment_bytes = sum(
        len(payload) for _, payload, _, _ in attachments
    )
    safe_raw_limit = 18 * 1024 * 1024
    if raw_attachment_bytes > safe_raw_limit:
        raise RuntimeError(
            "Email attachments exceed the app's 18 MB safe raw-size limit. "
            "Reduce the number/size of Web News evidence PDFs or download "
            "the evidence ZIP from the dashboard."
        )

    context = ssl.create_default_context()
    sent = []
    failed = {}

    with smtplib.SMTP_SSL(
        "smtp.gmail.com",
        465,
        context=context,
        timeout=30,
    ) as smtp:
        smtp.login(sender, password)

        for recipient in clean_recipients:
            try:
                message = EmailMessage()
                message["From"] = sender
                message["To"] = recipient
                message["Subject"] = subject
                message.set_content(body)

                for filename, payload, maintype, subtype in attachments:
                    message.add_attachment(
                        payload,
                        maintype=maintype,
                        subtype=subtype,
                        filename=filename,
                    )

                # Explicit SMTP envelope recipient: one recipient per message.
                refused = smtp.send_message(
                    message,
                    from_addr=sender,
                    to_addrs=[recipient],
                )

                if refused:
                    failed[recipient] = (
                        "SMTP server refused this recipient: "
                        + str(refused)
                    )
                else:
                    sent.append(recipient)

            except Exception as exc:
                failed[recipient] = (
                    f"{type(exc).__name__}: {exc}"
                )

    return {
        "sent": sent,
        "failed": failed,
    }



def get_supabase_config():
    url = get_secret("SUPABASE_URL").rstrip("/")
    key = get_secret(
        "SUPABASE_SECRET_KEY",
        get_secret("SUPABASE_SERVICE_KEY", "")
    )
    return url, key


def supabase_enabled():
    url, key = get_supabase_config()
    return bool(url and key)


def supabase_headers():
    _, key = get_supabase_config()
    headers = {
        "apikey": key,
        "Content-Type": "application/json",
    }
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


def load_supabase_posts(limit=5000):
    url, _ = get_supabase_config()
    if not supabase_enabled():
        raise RuntimeError("Supabase secrets are not configured.")

    response = requests.get(
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
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    if not data:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    df = pd.DataFrame(data)
    df["topic_seed"] = "Stored"
    return normalize_posts(df)


def save_posts_to_supabase(df):
    url, _ = get_supabase_config()
    if not supabase_enabled():
        raise RuntimeError("Supabase secrets are not configured.")

    clean = normalize_posts(df)
    records = []

    for _, row in clean.iterrows():
        records.append({
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
        })

    if not records:
        return 0

    headers = supabase_headers()
    headers["Prefer"] = "resolution=merge-duplicates,return=minimal"

    response = requests.post(
        f"{url}/rest/v1/social_posts",
        headers=headers,
        json=records,
        timeout=45,
    )
    response.raise_for_status()
    return len(records)




def load_background_monitoring_runs(limit=336):
    """Load browser-independent GitHub Actions monitoring history from Supabase."""
    if not supabase_enabled():
        return pd.DataFrame()

    url, _ = get_supabase_config()
    response = requests.get(
        f"{url}/rest/v1/monitoring_runs",
        headers=supabase_headers(),
        params={
            "select": (
                "slot_key,run_timestamp,source_records,x_records,news_records,"
                "positive_pct,neutral_pct,negative_pct,alert_threshold,"
                "alert_triggered,pdf_filename,routine_email_sent,"
                "alert_email_sent,status,error"
            ),
            "order": "run_timestamp.desc",
            "limit": str(int(max(1, min(2000, limit)))),
        },
        timeout=20,
    )

    # Table is optional until supabase_build513.sql has been run.
    if response.status_code in (404, 400):
        return pd.DataFrame()
    response.raise_for_status()

    payload = response.json() or []
    if not payload:
        return pd.DataFrame()

    frame = pd.DataFrame(payload)
    frame["run_timestamp"] = pd.to_datetime(
        frame["run_timestamp"], errors="coerce", utc=True
    ).dt.tz_convert(IST)
    frame = frame.dropna(subset=["run_timestamp"]).copy()
    return frame.sort_values("run_timestamp").reset_index(drop=True)


# =========================================================
# FREE WEB / NEWS COLLECTOR
# Primary: GDELT DOC 2.0
# Fallback: Google News RSS
# =========================================================

def parse_gdelt_date(value):
    if not value:
        return pd.NaT
    value = str(value).strip()
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
        try:
            dt = datetime.strptime(value, fmt)
            return pd.Timestamp(dt, tz="UTC")
        except ValueError:
            pass
    return pd.to_datetime(value, errors="coerce", utc=True)


_LAST_NEWS_FETCH_DIAGNOSTICS = {}


def _news_user_agent():
    return {"User-Agent": "Mozilla/5.0 SocialMediaIntelligence/5.18.2"}


def _request_get(url, *, params=None, timeout=45, retries=3):
    last = None
    for attempt in range(max(1, retries)):
        try:
            r = requests.get(
                url,
                params=params,
                timeout=timeout,
                headers=_news_user_agent(),
                allow_redirects=True,
            )
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                import time as _time
                _time.sleep(1.2 * (attempt + 1))
                last = RuntimeError(f"HTTP {r.status_code}")
                continue
            return r
        except Exception as exc:
            last = exc
            if attempt < retries - 1:
                import time as _time
                _time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"Request failed: {type(last).__name__}: {last}")


def _clean_news_title(title, source=""):
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    source = re.sub(r"\s+", " ", str(source or "")).strip()
    if source:
        for sep in (" - ", " | ", " — ", " – "):
            suffix = sep + source
            if title.lower().endswith(suffix.lower()):
                title = title[: -len(suffix)].strip()
                break
    return title


def _news_title_key(title):
    value = re.sub(r"https?://\S+", " ", str(title or "").lower())
    value = re.sub(r"[^\w\u0900-\u097f]+", " ", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _source_key(source, url=""):
    value = re.sub(r"\s+", " ", str(source or "").lower()).strip()
    if value and value not in {"google news", "bing news", "news_source"}:
        return value
    try:
        host = urlparse(str(url or "")).netloc.lower().replace("www.", "")
        if host:
            return host
    except Exception:
        pass
    return value or "news_source"


def _news_external_id(title, source, url=""):
    material = _source_key(source, url) + "|" + _news_title_key(title)
    return "news:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _news_row(title, source, timestamp, url, topic_seed="ISRO News"):
    title = _clean_news_title(title, source)
    ts = pd.to_datetime(timestamp, errors="coerce", utc=True)
    if not title or pd.isna(ts):
        return None
    return {
        "external_id": _news_external_id(title, source, url),
        "platform": "web_news",
        "timestamp": ts,
        "username": str(source or "news_source").strip() or "news_source",
        "text": title,
        "hashtags": "",
        "likes": 0,
        "reposts": 0,
        "replies": 0,
        "followers": 0,
        "source_url": str(url or "").strip(),
        "topic_seed": topic_seed,
    }


def fetch_gdelt_news(query, timespan="24h", max_records=100):
    max_records = int(max(10, min(250, max_records)))
    response = _request_get(
        "https://api.gdeltproject.org/api/v2/doc/doc",
        params={
            "query": query,
            "mode": "artlist",
            "maxrecords": max_records,
            "timespan": timespan,
            "sort": "datedesc",
            "format": "json",
        },
        timeout=45,
        retries=3,
    )
    if response.status_code == 429:
        raise RuntimeError("GDELT_RATE_LIMIT")
    if response.status_code != 200:
        raise RuntimeError(f"GDELT HTTP {response.status_code}")
    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError("GDELT returned invalid JSON") from exc

    rows = []
    for article in payload.get("articles", []) or []:
        row = _news_row(
            article.get("title"),
            article.get("domain") or "news_source",
            parse_gdelt_date(article.get("seendate")),
            article.get("url") or "",
            "ISRO News | GDELT",
        )
        if row:
            rows.append(row)
    return normalize_posts(pd.DataFrame(rows))


def fetch_google_news_rss(query, max_records=100):
    max_records = int(max(10, min(100, max_records)))
    response = _request_get(
        "https://news.google.com/rss/search",
        params={
            "q": query,
            "hl": "en-IN",
            "gl": "IN",
            "ceid": "IN:en",
        },
        timeout=45,
        retries=2,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Google News RSS HTTP {response.status_code}")
    try:
        root = ET.fromstring(response.content)
    except Exception as exc:
        raise RuntimeError("Google News RSS returned unreadable XML") from exc

    rows = []
    for item in root.findall(".//item")[:max_records]:
        source_node = item.find("source")
        source = (
            source_node.text.strip()
            if source_node is not None and source_node.text
            else "Google News"
        )
        row = _news_row(
            item.findtext("title") or "",
            source,
            item.findtext("pubDate") or "",
            item.findtext("link") or "",
            "ISRO News | Google News RSS",
        )
        if row:
            rows.append(row)
    return normalize_posts(pd.DataFrame(rows))


def _unwrap_bing_news_url(link):
    link = str(link or "").strip()
    if not link:
        return link
    try:
        parsed = urlparse(link)
        qs = parse_qs(parsed.query)
        for key in ("url", "u", "r"):
            candidate = (qs.get(key) or [""])[0]
            if candidate.startswith("http"):
                return unquote(candidate)
    except Exception:
        pass
    return link


def fetch_bing_news_rss(query, max_records=50):
    max_records = int(max(5, min(50, max_records)))
    response = _request_get(
        "https://www.bing.com/news/search",
        params={
            "q": query,
            "format": "RSS",
            "qft": 'sortbydate="1"',
        },
        timeout=35,
        retries=2,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Bing News RSS HTTP {response.status_code}")
    try:
        root = ET.fromstring(response.content)
    except Exception as exc:
        raise RuntimeError("Bing News RSS returned unreadable XML") from exc

    rows = []
    for item in root.findall(".//item")[:max_records]:
        title = (item.findtext("title") or "").strip()
        link = _unwrap_bing_news_url(item.findtext("link") or "")
        source = ""
        for node in list(item):
            tag = str(node.tag).lower()
            if tag.endswith("source") and node.text:
                source = node.text.strip()
                break
        if not source:
            try:
                source = urlparse(link).netloc.replace("www.", "") or "Bing News"
            except Exception:
                source = "Bing News"
        row = _news_row(
            title,
            source,
            item.findtext("pubDate") or "",
            link,
            "ISRO News | Bing News RSS",
        )
        if row:
            rows.append(row)
    return normalize_posts(pd.DataFrame(rows))


def fetch_isro_official_press(max_records=40):
    """Best-effort official ISRO press-release source; no API key required."""
    url = "https://www.isro.gov.in/Press.html"
    response = _request_get(url, timeout=40, retries=2)
    if response.status_code != 200:
        raise RuntimeError(f"ISRO Press HTTP {response.status_code}")
    soup = BeautifulSoup(response.text, "html.parser")
    rows = []
    seen = set()
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        texts = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)) for c in cells]
        joined = " | ".join(texts)
        date = pd.NaT
        date_idx = None
        for idx, cell_text in enumerate(texts):
            candidate = pd.to_datetime(cell_text, errors="coerce", utc=True)
            if not pd.isna(candidate):
                date = candidate
                date_idx = idx
                break
        if pd.isna(date):
            continue
        title = ""
        href = ""
        for idx, cell in enumerate(cells):
            if idx == date_idx:
                continue
            txt = re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
            if len(txt) > len(title) and len(txt) > 8 and not txt.isdigit():
                title = txt
                a = cell.find("a", href=True)
                if a:
                    href = urljoin(url, a.get("href"))
        if not title:
            continue
        key = _news_title_key(title)
        if not key or key in seen:
            continue
        seen.add(key)
        row = _news_row(
            title,
            "ISRO (official)",
            date,
            href or url,
            "ISRO Official Press Release",
        )
        if row:
            rows.append(row)
        if len(rows) >= max_records:
            break
    return normalize_posts(pd.DataFrame(rows))


def _filter_news_to_timespan(df, timespan):
    if df is None or df.empty:
        return df
    span = str(timespan or "").strip().lower()
    m = re.fullmatch(r"(\d+)\s*(min|h|hours|d|days|w|weeks)", span)
    if m:
        amount = int(m.group(1))
        unit = m.group(2)
        if unit == "min":
            hours = amount / 60
        elif unit in {"h", "hours"}:
            hours = amount
        elif unit in {"d", "days"}:
            hours = amount * 24
        else:
            hours = amount * 24 * 7
    else:
        hours_map = {"24h": 24, "1day": 24, "1week": 168, "7days": 168, "1month": 744, "30days": 720}
        hours = hours_map.get(span)
    if not hours:
        return normalize_posts(df)
    result = normalize_posts(df)
    now = pd.Timestamp.now(tz=IST)
    cutoff = now - pd.Timedelta(hours=float(hours))
    future_limit = now + pd.Timedelta(minutes=15)
    return (
        result[
            (result["timestamp"] >= cutoff)
            & (result["timestamp"] <= future_limit)
        ]
        .sort_values("timestamp", ascending=False)
        .reset_index(drop=True)
    )


def _title_token_set(value):
    """Canonical information-bearing title tokens for story comparison."""
    stop = {
        "isro", "the", "a", "an", "of", "to", "in", "on", "for", "and",
        "with", "from", "by", "at", "as", "after", "amid", "says", "said",
        "news", "india", "indian", "latest", "live", "update", "updates",
        "exclusive", "watch", "video", "photos", "photo", "breaking",
    }
    return {
        t for t in _news_story_signature(value).split()
        if len(t) > 2 and t not in stop
    }


def _news_story_signature(title):
    """Normalize headline boilerplate so syndicated copies map together."""
    value = _news_title_key(title)

    # Remove common feed/publisher boilerplate that frequently creates
    # artificial duplicates while leaving the actual story wording intact.
    boilerplate_patterns = [
        r"\blive updates?\b",
        r"\blatest updates?\b",
        r"\bbreaking news\b",
        r"\bindia news\b",
        r"\bworld news\b",
        r"\btop news\b",
        r"\bexplained\b$",
        r"\bwatch video\b",
        r"\bvideo\b$",
    ]
    for pat in boilerplate_patterns:
        value = re.sub(pat, " ", value, flags=re.I)

    # Feed items sometimes append a publisher/domain even after source cleanup.
    value = re.sub(
        r"\s+(rediff|rediff com|times of india|timesofindia indiatimes com|"
        r"hindustan times|the hindu|india today|ndtv|news18|the indian express|"
        r"deccan herald|business standard|firstpost|the print|theprint|"
        r"reuters|ani|pti)\s*$",
        " ",
        value,
        flags=re.I,
    )

    value = re.sub(r"\s+", " ", value).strip()
    return value


def news_story_signature(title):
    """Public wrapper used by the background monitor for cycle-level deltas."""
    return _news_story_signature(title)


def suppress_resurfaced_news(
    current_df,
    history_df,
    query="ISRO",
    max_history_rows=2500,
):
    """Suppress current rows that are the same story as older historical coverage.

    Free search/RSS feeds sometimes re-surface an old article with a fresh feed
    timestamp or a slightly modified publisher suffix. Those repeats should not
    become a fresh intelligence event.
    """
    diagnostics = {
        "current_input": 0,
        "historical_rows_checked": 0,
        "resurfaced_exact_removed": 0,
        "resurfaced_near_removed": 0,
        "output_rows": 0,
    }
    if current_df is None or current_df.empty:
        empty = pd.DataFrame(columns=STANDARD_COLUMNS)
        return empty, diagnostics

    current = normalize_posts(current_df).copy()
    diagnostics["current_input"] = int(len(current))

    if history_df is None or history_df.empty:
        diagnostics["output_rows"] = int(len(current))
        return current, diagnostics

    history = normalize_posts(history_df).copy()
    history = history[history["platform"].eq("web_news")].copy()
    history = history.sort_values("timestamp", ascending=False).head(max_history_rows)
    diagnostics["historical_rows_checked"] = int(len(history))

    historical_sigs = []
    for title in history["text"].astype(str):
        sig = _news_story_signature(title)
        if sig:
            historical_sigs.append(sig)

    exact_set = set(historical_sigs)
    kept = []
    for _, row in current.sort_values("timestamp", ascending=False).iterrows():
        sig = _news_story_signature(row.get("text", ""))
        if not sig:
            continue
        if sig in exact_set:
            diagnostics["resurfaced_exact_removed"] += 1
            continue

        near = False
        # Limit expensive similarity checks to plausible candidates sharing
        # at least two information-bearing words.
        tokens = _title_token_set(sig)
        for old_sig in historical_sigs:
            old_tokens = _title_token_set(old_sig)
            if len(tokens & old_tokens) < 2:
                continue
            if _near_duplicate_title(sig, old_sig):
                near = True
                break
        if near:
            diagnostics["resurfaced_near_removed"] += 1
            continue

        kept.append(row.to_dict())

    result = (
        normalize_posts(pd.DataFrame(kept))
        if kept
        else pd.DataFrame(columns=STANDARD_COLUMNS)
    )
    diagnostics["output_rows"] = int(len(result))
    return result, diagnostics


_NEWS_STRICT_TOPIC_ANCHORS = {
    "isro",
    "indian space research organisation",
    "indian space research organization",
    "gaganyaan",
    "chandrayaan",
    "aditya l1",
    "aditya-l1",
    "pslv",
    "gslv",
    "lvm3",
    "sslv",
    "nisar",
    "spadex",
    "navic",
    "vyommitra",
    "bharatiya antariksh station",
    "indian space station",
    "satish dhawan space centre",
    "satish dhawan space center",
    "sriharikota",
    "vssc",
    "vikram sarabhai space centre",
    "u r rao satellite centre",
    "ur rao satellite centre",
}


def _is_isro_topic_query(query):
    q = _news_title_key(query)
    return (
        q in {"isro", "indian space research organisation", "indian space research organization"}
        or "isro" in q.split()
        or "indian space research" in q
    )


def _news_relevance_score(title, source="", query="ISRO"):
    """Conservative topic relevance score for ISRO-focused monitoring.

    Free search/RSS endpoints occasionally return fuzzy matches. For an ISRO
    monitoring run, a headline must contain an ISRO/mission anchor, come from
    the official ISRO source, or contain a strong India-space-agency phrase.
    """
    if not _is_isro_topic_query(query):
        return 1.0

    text = _news_story_signature(title)
    source_key = _source_key(source)
    if not text:
        return 0.0

    if "isro" in source_key or source_key.startswith("isro "):
        return 10.0

    score = 0.0
    for anchor in _NEWS_STRICT_TOPIC_ANCHORS:
        a = _news_title_key(anchor)
        if a and a in text:
            score += 5.0 if a in {"isro", "indian space research organisation", "indian space research organization"} else 3.0

    # A small number of legitimate headlines use only a descriptive phrase.
    if (
        "india" in text
        and ("space agency" in text or "space programme" in text or "space program" in text)
    ):
        score += 2.5

    return score


def _near_duplicate_title(a, b):
    """Cross-publisher story similarity, intentionally stricter than clustering."""
    sa = _news_story_signature(a)
    sb = _news_story_signature(b)
    if not sa or not sb:
        return False
    if sa == sb:
        return True

    aa, bb = _title_token_set(sa), _title_token_set(sb)
    if not aa or not bb:
        return False

    common = len(aa & bb)
    union = len(aa | bb)
    jaccard = common / max(1, union)
    containment = common / max(1, min(len(aa), len(bb)))
    sequence = SequenceMatcher(None, sa, sb).ratio()

    # Strong exact-ish rephrasing.
    if sequence >= 0.90:
        return True

    # Syndicated headlines with a few changed words or punctuation.
    if jaccard >= 0.72 and containment >= 0.78:
        return True

    # One headline may contain a publisher-added clause around the same core.
    if containment >= 0.88 and sequence >= 0.68:
        return True

    # Short headlines such as "'Leave ISRO ALONE!'" need a slightly different
    # rule because a single appended source token materially changes Jaccard.
    if min(len(aa), len(bb)) <= 5 and containment >= 0.80 and sequence >= 0.72:
        return True

    return False


def _story_quality_score(row):
    """Choose the cleaner representative when several outlets repeat a story."""
    title = str(row.get("text", "") or "")
    source = str(row.get("username", "") or "")
    sig = _news_story_signature(title)
    score = 0.0

    if source.lower().startswith("isro") or "official" in source.lower():
        score += 8.0
    if "isro" in sig:
        score += 2.0
    if 35 <= len(title) <= 180:
        score += 1.0
    if title.count("|") + title.count(" - ") <= 1:
        score += 0.5
    return score


def deduplicate_news_for_analysis(
    df,
    query="ISRO",
    max_records=150,
    return_diagnostics=False,
):
    """Return topic-relevant, story-level unique News rows.

    This is applied *after loading Supabase* as well as at fetch time. That is
    important because older builds may already have persisted duplicate feed
    rows under different external IDs or publisher names.
    """
    diagnostics = {
        "input_rows": 0,
        "irrelevant_removed": 0,
        "exact_story_duplicates_removed": 0,
        "near_story_duplicates_removed": 0,
        "output_unique_stories": 0,
    }

    if df is None or df.empty:
        empty = pd.DataFrame(columns=STANDARD_COLUMNS)
        return (empty, diagnostics) if return_diagnostics else empty

    # Normalize the canonical source columns, but preserve any analysis columns
    # already attached to the frame (sentiment_score, sentiment_confidence,
    # emotion, intent, semantic_cluster, engagement, etc.). Build 5.17.2
    # accidentally discarded these columns by calling normalize_posts() inside
    # the report-level deduper, causing KeyError: sentiment_score.
    original = df.copy()
    work = normalize_posts(original).copy()

    extra_columns = [
        c for c in original.columns
        if c not in STANDARD_COLUMNS and c != "external_id"
    ]
    if (
        extra_columns
        and "external_id" in original.columns
        and "external_id" in work.columns
    ):
        extras = original[["external_id"] + extra_columns].copy()
        extras["external_id"] = extras["external_id"].fillna("").astype(str)
        extras = extras[
            extras["external_id"].str.strip().ne("")
        ].drop_duplicates(subset=["external_id"], keep="first")
        if not extras.empty:
            work = work.merge(
                extras,
                on="external_id",
                how="left",
                validate="one_to_one",
            )

    work = work[work["platform"].eq("web_news")].copy()
    work = work.sort_values("timestamp", ascending=False).reset_index(drop=True)
    diagnostics["input_rows"] = int(len(work))

    # Relevance filtering prevents fuzzy search results such as unrelated
    # examination/paper-leak stories from entering sentiment/clustering.
    relevant_rows = []
    for _, row in work.iterrows():
        if _news_relevance_score(
            row.get("text", ""),
            row.get("username", ""),
            query=query,
        ) <= 0:
            diagnostics["irrelevant_removed"] += 1
            continue
        relevant_rows.append(row.to_dict())

    if not relevant_rows:
        empty = pd.DataFrame(columns=STANDARD_COLUMNS)
        return (empty, diagnostics) if return_diagnostics else empty

    # Do not normalize again here: the rows already carry canonical source
    # columns plus any enriched analysis columns that must survive into reports.
    relevant = pd.DataFrame(relevant_rows)
    relevant = relevant.sort_values("timestamp", ascending=False).reset_index(drop=True)

    representatives = []
    signatures = []

    for _, row in relevant.iterrows():
        row_dict = row.to_dict()
        sig = _news_story_signature(row_dict.get("text", ""))
        if not sig:
            continue

        duplicate_idx = None
        exact = False
        for idx, existing_sig in enumerate(signatures):
            if sig == existing_sig:
                duplicate_idx = idx
                exact = True
                break
            if _near_duplicate_title(sig, existing_sig):
                duplicate_idx = idx
                break

        if duplicate_idx is None:
            representatives.append(row_dict)
            signatures.append(sig)
            continue

        if exact:
            diagnostics["exact_story_duplicates_removed"] += 1
        else:
            diagnostics["near_story_duplicates_removed"] += 1

        # If the later row is materially cleaner (for example official ISRO),
        # use it as the representative without increasing analytical weight.
        current = representatives[duplicate_idx]
        if _story_quality_score(row_dict) > _story_quality_score(current):
            representatives[duplicate_idx] = row_dict
            signatures[duplicate_idx] = sig

    # Preserve enriched analysis columns in the representative rows. Canonical
    # source columns were already normalized at function entry.
    result = (
        pd.DataFrame(representatives)
        if representatives
        else pd.DataFrame(columns=list(work.columns))
    )
    if not result.empty:
        result = (
            result.sort_values("timestamp", ascending=False)
            .head(max(1, int(max_records)))
            .reset_index(drop=True)
        )

    diagnostics["output_unique_stories"] = int(len(result))
    return (result, diagnostics) if return_diagnostics else result


def _merge_news_frames(frames, max_records=150, query="ISRO"):
    """Merge feeds, remove irrelevant/fuzzy results, then dedupe story families."""
    usable = [f for f in frames if f is not None and not f.empty]
    if not usable:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    merged = normalize_posts(pd.concat(usable, ignore_index=True))
    merged = merged.sort_values("timestamp", ascending=False).reset_index(drop=True)

    unique = deduplicate_news_for_analysis(
        merged,
        query=query,
        max_records=max_records,
        return_diagnostics=False,
    )
    if unique.empty:
        return unique

    # Keep recency dominant but avoid one publisher consuming the whole pack.
    cap = max(6, int(max_records * 0.30))
    selected = []
    source_counts = {}
    overflow = []
    for _, row in unique.sort_values("timestamp", ascending=False).iterrows():
        skey = _source_key(row.get("username"), row.get("source_url"))
        if source_counts.get(skey, 0) < cap:
            selected.append(row.to_dict())
            source_counts[skey] = source_counts.get(skey, 0) + 1
        else:
            overflow.append(row.to_dict())
        if len(selected) >= max_records:
            break

    if len(selected) < max_records:
        selected.extend(overflow[: max_records - len(selected)])

    return (
        normalize_posts(pd.DataFrame(selected))
        .sort_values("timestamp", ascending=False)
        .reset_index(drop=True)
    )

def _news_query_variants(query):
    base = re.sub(r"\s+", " ", str(query or "ISRO")).strip() or "ISRO"
    variants = [
        base,
        '"Indian Space Research Organisation"',
        f'({base} OR "Indian Space Research Organisation") (launch OR mission OR satellite OR rocket OR Gaganyaan OR PSLV OR GSLV OR LVM3 OR NISAR)',
    ]
    out = []
    for v in variants:
        if v not in out:
            out.append(v)
    return out


def get_last_news_fetch_diagnostics():
    return dict(_LAST_NEWS_FETCH_DIAGNOSTICS)


def fetch_free_news(query, timespan="24h", max_records=150):
    """Multi-source, no-key news fusion with local recency filtering and dedupe."""
    global _LAST_NEWS_FETCH_DIAGNOSTICS
    max_records = int(max(20, min(200, max_records)))
    frames = []
    diag = {"timespan": timespan, "query": query, "sources": {}, "errors": {}}

    gdelt_query = f'({query} OR "Indian Space Research Organisation")' if str(query).strip().upper() == "ISRO" else str(query)
    try:
        g = fetch_gdelt_news(gdelt_query, timespan=timespan, max_records=min(250, max_records * 2))
        frames.append(g)
        diag["sources"]["GDELT"] = int(len(g))
    except Exception as exc:
        diag["errors"]["GDELT"] = f"{type(exc).__name__}: {exc}"

    variants = _news_query_variants(query)
    google_rows = []
    for q in variants:
        try:
            f = fetch_google_news_rss(q, max_records=100)
            google_rows.append(f)
        except Exception as exc:
            diag["errors"][f"Google:{q}"] = f"{type(exc).__name__}: {exc}"
    if google_rows:
        gnews = _merge_news_frames(google_rows, max_records=120, query=query)
        frames.append(gnews)
        diag["sources"]["Google News RSS"] = int(len(gnews))

    bing_rows = []
    for q in variants[:2]:
        try:
            f = fetch_bing_news_rss(q, max_records=50)
            bing_rows.append(f)
        except Exception as exc:
            diag["errors"][f"Bing:{q}"] = f"{type(exc).__name__}: {exc}"
    if bing_rows:
        bnews = _merge_news_frames(bing_rows, max_records=60, query=query)
        frames.append(bnews)
        diag["sources"]["Bing News RSS"] = int(len(bnews))

    try:
        official = fetch_isro_official_press(max_records=40)
        frames.append(official)
        diag["sources"]["ISRO official"] = int(len(official))
    except Exception as exc:
        diag["errors"]["ISRO official"] = f"{type(exc).__name__}: {exc}"

    raw_candidate_count = int(sum(len(f) for f in frames if f is not None))
    merged = _merge_news_frames(frames, max_records=max_records, query=query)
    merged = _filter_news_to_timespan(merged, timespan)
    merged, final_quality = deduplicate_news_for_analysis(
        merged,
        query=query,
        max_records=max_records,
        return_diagnostics=True,
    )
    merged = merged.head(max_records).reset_index(drop=True)
    diag["raw_candidate_records"] = raw_candidate_count
    diag["quality_filter"] = final_quality
    diag["final_unique_records"] = int(len(merged))
    diag["final_unique_stories"] = int(len(merged))
    diag["publisher_count"] = int(merged["username"].nunique()) if len(merged) else 0
    diag["newest_timestamp"] = (
        merged["timestamp"].max().isoformat() if len(merged) else None
    )
    _LAST_NEWS_FETCH_DIAGNOSTICS = diag
    source_text = "Fusion: " + ", ".join(
        f"{k} {v}" for k, v in diag["sources"].items()
    )
    if diag["errors"]:
        source_text += f"; {len(diag['errors'])} source/query warning(s)"
    return merged, source_text


# =========================================================
# INTELLIGENCE AGENTS — Build 5.17 confidence-aware ensemble
# =========================================================
_POSITIVE_LEXICON = {
    "success": 1.3, "successful": 1.4, "successfully": 1.3, "achievement": 1.2,
    "milestone": 1.1, "proud": 1.3, "congratulations": 1.4, "excellent": 1.4,
    "great": 1.0, "historic": 1.0, "breakthrough": 1.3, "progress": 0.8,
    "improved": 0.8, "improvement": 0.8, "ready": 0.5, "safe": 0.6,
    "nominal": 0.7, "stable": 0.6, "recovered": 1.0, "resolved": 1.0,
    "inaugurated": 0.5, "launched successfully": 1.6, "successful launch": 1.6,
    "hot test successful": 1.5, "विश्वसनीय": 0.8, "सफल": 1.4, "सफलता": 1.3,
    "गर्व": 1.3, "बधाई": 1.4, "उपलब्धि": 1.2, "शानदार": 1.2,
    "safal": 1.3, "badhaai": 1.3, "garv": 1.1, "shandaar": 1.1,
}
_NEGATIVE_LEXICON = {
    "fail": -1.3, "failed": -1.5, "failure": -1.5, "unsuccessful": -1.4,
    "delay": -0.9, "delayed": -1.0, "postponed": -0.9, "abort": -1.4,
    "aborted": -1.5, "anomaly": -1.0, "malfunction": -1.4, "problem": -0.8,
    "issue": -0.6, "concern": -0.7, "concerns": -0.7, "risk": -0.8,
    "crash": -1.6, "lost": -1.1, "loss": -1.1, "damage": -1.3,
    "criticism": -0.9, "criticised": -0.9, "criticized": -0.9, "slams": -1.0,
    "controversy": -0.8, "setback": -1.2, "error": -0.8, "uncertain": -0.6,
    "unsafe": -1.1, "cancelled": -1.1, "canceled": -1.1, "warning": -0.7,
    "विफल": -1.5, "असफल": -1.5, "देरी": -1.0, "चिंता": -0.8,
    "समस्या": -0.9, "खतरा": -1.0, "दुर्घटना": -1.5, "नुकसान": -1.2,
    "नाकाम": -1.4, "vifal": -1.4, "asafal": -1.4, "deri": -0.9,
    "chinta": -0.7, "samasya": -0.8, "khatra": -1.0,
}
_NEGATORS = {"not", "no", "never", "without", "hardly", "isn't", "wasn't", " नहीं", "नहीं", "नही", "मत", "bina", "nahi"}
_CONTRAST_RE = re.compile(r"\b(?:but|however|yet|although|though|nevertheless|लेकिन|मगर|पर)\b", re.I)
_SARCASM_RE = re.compile(r"(?:/s\b|yeah\s+right|sure[,!. ]|as if\b|वाह\s+क्या)", re.I)
_EMOJI_POLARITY = {"🚀": 0.8, "🎉": 1.0, "👏": 0.8, "❤️": 0.8, "❤": 0.8, "👍": 0.6, "🔥": 0.5, "😊": 0.7, "😀": 0.7, "😄": 0.7, "😢": -0.8, "😭": -1.0, "😡": -1.0, "🤬": -1.0, "👎": -0.7, "⚠": -0.5, "⚠️": -0.5, "💔": -0.9}


def _analysis_text(value):
    t = re.sub(r"https?://\S+", " ", str(value or ""))
    t = re.sub(r"@([A-Za-z0-9_]+)", r" \1 ", t)
    t = re.sub(r"#([\w_]+)", r" \1 ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def _detect_text_language(text):
    t = str(text or "")
    devanagari = len(re.findall(r"[\u0900-\u097F]", t))
    latin = len(re.findall(r"[A-Za-z]", t))
    if devanagari and latin:
        return "Hindi/Mixed"
    if devanagari:
        return "Hindi/Indic"
    return "English/Latin"


def _lexicon_score(text):
    t = _analysis_text(text).lower()
    score = 0.0
    hits = 0
    for lexicon in (_POSITIVE_LEXICON, _NEGATIVE_LEXICON):
        for phrase, weight in lexicon.items():
            # Unicode-friendly boundary approximation.
            pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"
            for match in re.finditer(pattern, t, flags=re.I):
                prefix = t[max(0, match.start() - 26):match.start()]
                negated = any(re.search(r"(?<!\w)" + re.escape(n.strip()) + r"(?!\w)", prefix) for n in _NEGATORS if n.strip())
                score += (-0.85 * weight) if negated else weight
                hits += 1
    for emoji, weight in _EMOJI_POLARITY.items():
        c = t.count(emoji)
        if c:
            score += weight * min(c, 2)
            hits += min(c, 2)
    return float(np.tanh(score / 3.0)), hits


def _sentiment_one(text, platform=""):
    clean = _analysis_text(text)
    analyzer = SentimentIntensityAnalyzer()
    vader = float(analyzer.polarity_scores(clean)["compound"])
    lex, hits = _lexicon_score(clean)
    score = 0.68 * vader + 0.32 * lex

    parts = _CONTRAST_RE.split(clean)
    if len(parts) > 1 and parts[-1].strip():
        tail = parts[-1].strip()
        tail_v = float(analyzer.polarity_scores(tail)["compound"])
        tail_l, tail_hits = _lexicon_score(tail)
        tail_score = 0.68 * tail_v + 0.32 * tail_l
        score = 0.58 * score + 0.42 * tail_score
        hits += tail_hits

    # Headlines are often factual statements containing polarity-bearing words.
    # Shrink weak News scores unless explicit evaluative cues are present.
    platform = str(platform or "")
    threshold = 0.12
    if platform == "web_news":
        threshold = 0.16
        if hits == 0 and abs(vader) < 0.35:
            score *= 0.72

    score = float(max(-1.0, min(1.0, score)))
    sarcasm = bool(_SARCASM_RE.search(clean))
    sign_agreement = (vader * lex > 0.01) or (abs(lex) < 0.05)
    lang = _detect_text_language(clean)
    signal = min(1.0, hits / 3.0)

    if abs(score) < threshold:
        label = "Neutral"
        confidence = 0.58 + 0.22 * (1 - min(1, abs(score) / max(threshold, 0.01)))
        if hits >= 2 and abs(vader) > 0.2 and vader * lex < 0:
            confidence -= 0.18
    else:
        label = "Positive" if score > 0 else "Negative"
        confidence = 0.34 + 0.34 * abs(score) + 0.18 * float(sign_agreement) + 0.14 * signal

    if sarcasm:
        confidence *= 0.62
    if len(clean) < 24:
        confidence *= 0.88
    if lang != "English/Latin" and hits == 0:
        confidence *= 0.72
    confidence = float(max(0.20, min(0.98, confidence)))

    mag = abs(score)
    if label == "Neutral":
        strength = "Neutral"
    elif mag >= 0.58:
        strength = "Strong"
    elif mag >= 0.30:
        strength = "Moderate"
    else:
        strength = "Mild"

    basis = "agreement" if sign_agreement else "mixed-signals"
    if hits == 0 and abs(vader) < 0.1:
        basis = "low-signal"
    if sarcasm:
        basis += "+sarcasm-flag"
    return score, label, confidence, vader, lex, strength, basis, lang


@st.cache_data
def run_sentiment_agent(df):
    result = df.copy()
    values = [
        _sentiment_one(row.get("text", ""), row.get("platform", ""))
        for _, row in result.iterrows()
    ]
    if values:
        cols = list(zip(*values))
        result["sentiment_score"] = cols[0]
        result["sentiment"] = cols[1]
        result["sentiment_confidence"] = cols[2]
        result["vader_score"] = cols[3]
        result["lexical_score"] = cols[4]
        result["sentiment_strength"] = cols[5]
        result["sentiment_basis"] = cols[6]
        result["analysis_language"] = cols[7]
    else:
        result["sentiment_score"] = pd.Series(dtype=float)
        result["sentiment"] = pd.Series(dtype=str)
        result["sentiment_confidence"] = pd.Series(dtype=float)
        result["vader_score"] = pd.Series(dtype=float)
        result["lexical_score"] = pd.Series(dtype=float)
        result["sentiment_strength"] = pd.Series(dtype=str)
        result["sentiment_basis"] = pd.Series(dtype=str)
        result["analysis_language"] = pd.Series(dtype=str)
    return result


_EMOTION_LEXICON = {
    "Anger": ["angry", "furious", "outrage", "outraged", "unacceptable", "rage", "slams", "बेहद नाराज़", "गुस्सा", "naraz", "gussa"],
    "Fear/Concern": ["worried", "worry", "concern", "concerned", "risk", "danger", "threat", "uncertain", "unsafe", "warning", "चिंता", "खतरा", "डर", "chinta", "khatra", "dar"],
    "Joy": ["happy", "delighted", "excellent", "great", "congratulations", "celebrate", "celebration", "wonderful", "खुश", "बधाई", "khush", "badhaai"],
    "Pride": ["proud", "pride", "historic", "milestone", "achievement", "inspiring", "गर्व", "उपलब्धि", "garv", "uplabdhi"],
    "Sadness/Disappointment": ["sad", "disappointed", "disappointing", "setback", "regret", "loss", "दुख", "निराश", "निराशा", "dukh", "nirash"],
    "Surprise": ["wow", "surprise", "surprised", "surprising", "unexpected", "astonishing", "चौंक", "अप्रत्याशित", "hairan"],
    "Trust/Confidence": ["confident", "confidence", "reliable", "trusted", "trust", "assured", "nominal", "विश्वास", "भरोसा", "bharosa", "vishwas"],
    "Anticipation": ["await", "awaiting", "expected", "upcoming", "soon", "ready for", "looking forward", "anticipat", "इंतजार", "उम्मीद", "intezar", "umeed"],
    "Relief": ["relieved", "relief", "finally", "resolved", "recovered", "restored", "राहत", "समाधान", "rahat"],
}


def _emotion_scores(text):
    t = _analysis_text(text).lower()
    scores = {k: 0.0 for k in _EMOTION_LEXICON}
    for emotion, terms in _EMOTION_LEXICON.items():
        for term in terms:
            pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
            for m in re.finditer(pattern, t, flags=re.I):
                prefix = t[max(0, m.start() - 24):m.start()]
                negated = any(n.strip() and n.strip() in prefix.split()[-4:] for n in _NEGATORS)
                scores[emotion] += 0.35 if negated else 1.0
    # Emoji cues.
    for emo, val in {"Joy": ["😀", "😄", "😊", "🎉", "👏", "❤️", "❤"], "Anger": ["😡", "🤬"], "Sadness/Disappointment": ["😢", "😭", "💔"], "Surprise": ["😮", "🤯"]}.items():
        scores[emo] += sum(min(2, t.count(e)) * 0.8 for e in val)
    if "!" in t:
        top = max(scores, key=scores.get)
        if scores[top] > 0:
            scores[top] += min(0.5, t.count("!") * 0.12)
    return scores


@st.cache_data
def run_emotion_agent(df):
    result = df.copy()
    primaries, secondaries, strengths, confidences, intensities = [], [], [], [], []
    for _, row in result.iterrows():
        scores = _emotion_scores(row.get("text", ""))
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        best_name, best = ranked[0]
        second_name, second = ranked[1]
        sent_mag = abs(float(row.get("sentiment_score", 0) or 0))
        total = sum(scores.values())
        if best < 0.75:
            primary = "Other/Neutral"
            secondary = ""
            strength = 0.0
            confidence = 0.62 if sent_mag < 0.18 else 0.45
            intensity = min(1.0, 0.35 * sent_mag)
        else:
            primary = best_name
            secondary = second_name if second >= max(0.75, best * 0.60) else ""
            strength = min(1.0, best / 3.0)
            margin = max(0.0, best - second)
            confidence = min(0.97, 0.48 + 0.28 * strength + 0.20 * min(1.0, margin / max(best, 0.1)))
            intensity = min(1.0, 0.60 * min(1.0, total / 4.0) + 0.40 * sent_mag)
        if bool(_SARCASM_RE.search(str(row.get("text", "")))):
            confidence *= 0.65
        primaries.append(primary)
        secondaries.append(secondary)
        strengths.append(round(float(strength), 4))
        confidences.append(round(float(max(0.2, confidence)), 4))
        intensities.append(round(float(intensity), 4))
    result["emotion"] = primaries
    result["emotion_secondary"] = secondaries
    result["emotion_score"] = strengths
    result["emotion_confidence"] = confidences
    result["emotional_intensity"] = intensities
    return result


@st.cache_data
def run_intent_agent(df):
    result = df.copy()
    intents = []
    confidences = []
    for _, row in result.iterrows():
        t = _analysis_text(row.get("text", "")).lower()
        scores = {
            "Question/Request": 2.0 if ("?" in t or re.search(r"\b(why|when|where|how|please|can|could|क्या|कब|क्यों|कैसे)\b", t)) else 0.0,
            "Praise/Support": 1.5 if re.search(r"\b(proud|congratulations|great|excellent|support|salute|गर्व|बधाई|शानदार)\b", t) else 0.0,
            "Concern/Criticism": 1.5 if re.search(r"\b(concern|worry|fail|failed|delay|criticism|unacceptable|problem|risk|चिंता|विफल|देरी|समस्या)\b", t) else 0.0,
            "Speculation/Prediction": 1.5 if re.search(r"\b(may|might|could|likely|possibly|expect|prediction|rumou?r|perhaps|शायद|संभावना|umeed)\b", t) else 0.0,
            "Humor/Sarcasm": 2.0 if _SARCASM_RE.search(t) else 0.0,
            "Information/Update": 1.0,
        }
        if str(row.get("platform", "")) == "web_news":
            scores["Information/Update"] += 0.7
        best = max(scores, key=scores.get)
        sorted_vals = sorted(scores.values(), reverse=True)
        margin = sorted_vals[0] - sorted_vals[1] if len(sorted_vals) > 1 else sorted_vals[0]
        intents.append(best)
        confidences.append(round(float(min(0.95, 0.55 + 0.15 * margin)), 4))
    result["intent"] = intents
    result["intent_confidence"] = confidences
    return result


def _semantic_clean(value):
    t = _analysis_text(value).lower()
    t = re.sub(r"\b(?:https|http|amp|rt)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def run_semantic_topic_agent(df, requested_clusters=7):
    result = df.copy()
    n = len(result)
    result["semantic_cluster"] = 0
    result["semantic_x"] = 0.0
    result["semantic_y"] = 0.0
    result["cluster_confidence"] = 0.5
    if n < 6:
        return result, None, None, np.array([])

    corpus = result["text"].map(_semantic_clean)
    min_df = 1 if n < 25 else 2
    word_vectorizer = TfidfVectorizer(
        stop_words="english",
        ngram_range=(1, 2),
        min_df=min_df,
        max_df=0.98,
        max_features=5000,
        sublinear_tf=True,
        strip_accents="unicode",
    )
    char_vectorizer = TfidfVectorizer(
        analyzer="char_wb",
        ngram_range=(3, 5),
        min_df=2 if n >= 12 else 1,
        max_features=3500,
        sublinear_tf=True,
    )
    try:
        word_tfidf = word_vectorizer.fit_transform(corpus)
        char_tfidf = char_vectorizer.fit_transform(corpus)
        combined = hstack([word_tfidf, char_tfidf * 0.45], format="csr")
    except ValueError:
        return result, None, None, np.array([])

    max_components = min(16, combined.shape[0] - 1, combined.shape[1] - 1)
    if max_components < 2:
        return result, word_vectorizer, word_tfidf, np.array(word_vectorizer.get_feature_names_out())

    latent = TruncatedSVD(n_components=max_components, random_state=42).fit_transform(combined)
    latent = Normalizer(copy=False).fit_transform(latent)

    max_k = min(requested_clusters, max(2, int(round(math.sqrt(n)))), n - 1)
    candidates = range(2, max_k + 1)
    best = None
    for k in candidates:
        try:
            model = KMeans(n_clusters=k, random_state=42, n_init=20).fit(latent)
            labels = model.labels_
            if len(set(labels)) < 2:
                continue
            sil = float(silhouette_score(latent, labels))
            min_size = min(np.bincount(labels))
            # discourage singleton/tiny clusters
            adjusted = sil - (0.08 if min_size < 2 else 0.0)
            if best is None or adjusted > best[0]:
                best = (adjusted, sil, model, labels)
        except Exception:
            continue

    if best is None or (n >= 12 and best[1] < 0.045):
        labels = np.zeros(n, dtype=int)
        sil_samples = np.zeros(n)
    else:
        labels = best[3]
        try:
            sil_samples = silhouette_samples(latent, labels)
        except Exception:
            sil_samples = np.zeros(n)

    result["semantic_cluster"] = labels
    result["semantic_x"] = latent[:, 0]
    result["semantic_y"] = latent[:, 1] if latent.shape[1] > 1 else 0.0
    result["cluster_confidence"] = np.clip((np.asarray(sil_samples) + 1.0) / 2.0, 0.0, 1.0)
    return result, word_vectorizer, word_tfidf, np.array(word_vectorizer.get_feature_names_out())


def run_narrative_agent(df, vectorizer, tfidf, feature_names):
    if vectorizer is None or tfidf is None or len(feature_names) == 0 or df.empty:
        return pd.DataFrame()
    narratives = []
    latest_time = df["timestamp"].max()
    global_mean = np.asarray(tfidf.mean(axis=0)).ravel() + 1e-6
    generic = {"isro", "india", "indian", "space", "research", "organisation", "organization", "says", "news"}

    for cluster_id in sorted(df["semantic_cluster"].unique()):
        mask = df["semantic_cluster"].eq(cluster_id).to_numpy()
        cluster_df = df[mask].copy()
        cluster_matrix = tfidf[mask]
        mean_tfidf = np.asarray(cluster_matrix.mean(axis=0)).ravel()
        distinctiveness = mean_tfidf * np.log1p(mean_tfidf / global_mean)
        order = distinctiveness.argsort()[::-1]
        top_terms = []
        for i in order:
            term = str(feature_names[i])
            if distinctiveness[i] <= 0:
                continue
            if term.lower() in generic:
                continue
            top_terms.append(term)
            if len(top_terms) >= 6:
                break
        if not top_terms:
            top_terms = [str(feature_names[i]) for i in mean_tfidf.argsort()[::-1][:5] if mean_tfidf[i] > 0]

        title = " / ".join(term.title() for term in top_terms[:3]) if top_terms else f"Cluster {cluster_id}"
        centroid = np.asarray(cluster_matrix.mean(axis=0))
        similarities = cosine_similarity(cluster_matrix, centroid).ravel()
        representative = cluster_df.iloc[int(np.argmax(similarities))]["text"]

        recent = cluster_df[cluster_df["timestamp"] >= latest_time - pd.Timedelta(hours=12)]
        previous = cluster_df[(cluster_df["timestamp"] < latest_time - pd.Timedelta(hours=12)) & (cluster_df["timestamp"] >= latest_time - pd.Timedelta(hours=24))]
        if len(previous) == 0:
            growth = 100.0 if len(recent) >= 2 else 0.0
            emerging = bool(len(recent) >= 2)
        else:
            growth = ((len(recent) - len(previous)) / len(previous)) * 100
            emerging = False

        conf = cluster_df.get("sentiment_confidence", pd.Series(1.0, index=cluster_df.index)).astype(float).clip(0.2, 1.0)
        neg_weight = float(conf[cluster_df["sentiment"].eq("Negative")].sum())
        weighted_neg = 100 * neg_weight / max(float(conf.sum()), 1e-9)
        sentiment_index = 100 * float(np.average(cluster_df["sentiment_score"], weights=conf))
        expressive = cluster_df[cluster_df.get("emotion", "Other/Neutral") != "Other/Neutral"] if "emotion" in cluster_df else pd.DataFrame()
        dominant_emotion = expressive["emotion"].value_counts().idxmax() if len(expressive) else "Other/Neutral"
        cohesion = 100 * float(cluster_df.get("cluster_confidence", pd.Series(0.5, index=cluster_df.index)).mean())
        source_div = int(cluster_df["username"].nunique())
        momentum = max(0.0, min(100.0, 0.55 * max(0.0, min(growth, 100.0)) + 0.45 * weighted_neg))

        narratives.append({
            "cluster": int(cluster_id),
            "narrative": title,
            "posts": len(cluster_df),
            "negative_pct": round(weighted_neg, 1),
            "engagement": int(cluster_df.get("engagement", pd.Series(0, index=cluster_df.index)).sum()),
            "12h_growth_pct": round(float(growth), 1),
            "top_terms": ", ".join(top_terms),
            "representative_post": representative,
            "sentiment_index": round(sentiment_index, 1),
            "dominant_emotion": dominant_emotion,
            "cohesion_pct": round(cohesion, 1),
            "source_diversity": source_div,
            "momentum_score": round(momentum, 1),
            "is_emerging": emerging,
        })
    return pd.DataFrame(narratives).sort_values(["momentum_score", "posts"], ascending=[False, False]).reset_index(drop=True)


def run_trend_agent(df):
    cols = ["hour", "posts", "rolling_avg", "anomaly_score", "robust_z", "is_anomaly"]
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)
    hourly = df.groupby(df["timestamp"].dt.floor("h")).size()
    full_index = pd.date_range(hourly.index.min(), hourly.index.max(), freq="h", tz=hourly.index.tz)
    hourly = hourly.reindex(full_index, fill_value=0).rename("posts").reset_index().rename(columns={"index": "hour"})
    history = hourly["posts"].shift(1)
    baseline = history.rolling(12, min_periods=3).median()
    mad = history.rolling(12, min_periods=3).apply(lambda x: float(np.median(np.abs(x - np.median(x)))), raw=True)
    hourly["rolling_avg"] = baseline
    hourly["anomaly_score"] = hourly.apply(lambda r: 0.0 if pd.isna(r["rolling_avg"]) else float(r["posts"] / max(0.5, r["rolling_avg"])), axis=1)
    denom = 1.4826 * mad.replace(0, np.nan)
    robust_z = (hourly["posts"] - baseline) / denom
    robust_z = robust_z.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    hourly["robust_z"] = robust_z
    hourly["is_anomaly"] = ((hourly["robust_z"] >= 3.0) & (hourly["posts"] >= 2)) | ((hourly["anomaly_score"] >= 2.5) & (hourly["posts"] >= 3))
    return hourly[cols]


def _weighted_sentiment_shares(df):
    if df is None or df.empty:
        return {"Positive": 0.0, "Neutral": 0.0, "Negative": 0.0}
    weights = df.get("sentiment_confidence", pd.Series(1.0, index=df.index)).astype(float).clip(0.2, 1.0)
    denom = max(float(weights.sum()), 1e-9)
    return {label: 100.0 * float(weights[df["sentiment"].eq(label)].sum()) / denom for label in ("Positive", "Neutral", "Negative")}


def compute_intelligence_summary(df, narratives=None, trend_df=None):
    if df is None or df.empty:
        return {
            "weighted_positive_pct": 0.0, "weighted_neutral_pct": 0.0, "weighted_negative_pct": 0.0,
            "raw_negative_pct": 0.0, "sentiment_index": 0.0, "sentiment_confidence_pct": 0.0,
            "dominant_emotion": "Other/Neutral", "emotional_intensity_pct": 0.0,
            "source_diversity_pct": 0.0, "narrative_concentration_pct": 0.0,
            "volume_velocity_pct": 0.0, "negative_velocity_pct": 0.0,
            "max_robust_anomaly_z": 0.0, "attention_index": 0, "assessment_confidence": 0,
            "strong_negative_pct": 0.0,
        }
    shares = _weighted_sentiment_shares(df)
    weights = df.get("sentiment_confidence", pd.Series(1.0, index=df.index)).astype(float).clip(0.2, 1.0)
    sentiment_index = 100.0 * float(np.average(df["sentiment_score"].astype(float), weights=weights))
    raw_negative = 100.0 * float(df["sentiment"].eq("Negative").mean())
    strong_negative = 100.0 * float(((df["sentiment_score"] <= -0.45) & (weights >= 0.55)).mean())

    expressive = df[df.get("emotion", pd.Series("Other/Neutral", index=df.index)).ne("Other/Neutral")]
    dominant_emotion = expressive["emotion"].value_counts().idxmax() if len(expressive) else "Other/Neutral"
    emotional_intensity = 100.0 * float(df.get("emotional_intensity", pd.Series(0.0, index=df.index)).astype(float).mean())
    avg_sent_conf = 100.0 * float(weights.mean())

    source_counts = df["username"].fillna("Unknown").astype(str).value_counts()
    if len(source_counts) <= 1:
        source_div = 0.0
    else:
        p = source_counts / source_counts.sum()
        entropy = -float((p * np.log(p)).sum())
        source_div = 100.0 * entropy / max(np.log(len(p)), 1e-9)

    if "semantic_cluster" in df and len(df):
        narrative_concentration = 100.0 * float(df["semantic_cluster"].value_counts(normalize=True).iloc[0])
    else:
        narrative_concentration = 100.0

    latest = df["timestamp"].max()
    recent = df[df["timestamp"] >= latest - pd.Timedelta(hours=6)]
    previous = df[(df["timestamp"] < latest - pd.Timedelta(hours=6)) & (df["timestamp"] >= latest - pd.Timedelta(hours=12))]
    volume_velocity = 0.0 if len(previous) == 0 else 100.0 * (len(recent) - len(previous)) / len(previous)
    recent_neg = float(recent["sentiment"].eq("Negative").sum())
    prev_neg = float(previous["sentiment"].eq("Negative").sum())
    negative_velocity = 0.0 if prev_neg == 0 else 100.0 * (recent_neg - prev_neg) / prev_neg

    max_z = float(trend_df.get("robust_z", pd.Series(dtype=float)).max()) if trend_df is not None and len(trend_df) else 0.0
    if not np.isfinite(max_z):
        max_z = 0.0
    negative_cluster_concentration = 0.0
    neg_df = df[df["sentiment"].eq("Negative")]
    if len(neg_df) and "semantic_cluster" in neg_df:
        negative_cluster_concentration = 100.0 * float(neg_df["semantic_cluster"].value_counts(normalize=True).iloc[0])

    neg_pressure = shares["Negative"]
    anomaly_component = max(0.0, min(100.0, max_z * 18.0))
    neg_velocity_component = max(0.0, min(100.0, negative_velocity))
    attention = round(
        0.43 * neg_pressure
        + 0.17 * strong_negative
        + 0.14 * negative_cluster_concentration * (neg_pressure / 100.0)
        + 0.14 * anomaly_component * (0.35 + 0.65 * neg_pressure / 100.0)
        + 0.12 * neg_velocity_component
    )

    now = pd.Timestamp.now(tz=IST)
    latest_age_h = max(0.0, (now - latest).total_seconds() / 3600.0)
    freshness = 100.0 if latest_age_h <= 2 else 85.0 if latest_age_h <= 6 else 70.0 if latest_age_h <= 12 else 55.0 if latest_age_h <= 24 else 30.0 if latest_age_h <= 72 else 10.0
    sample_score = min(100.0, 100.0 * math.sqrt(len(df) / 50.0))
    confidence = round(0.38 * avg_sent_conf + 0.27 * sample_score + 0.18 * source_div + 0.17 * freshness)

    return {
        "weighted_positive_pct": round(shares["Positive"], 2),
        "weighted_neutral_pct": round(shares["Neutral"], 2),
        "weighted_negative_pct": round(shares["Negative"], 2),
        "raw_negative_pct": round(raw_negative, 2),
        "sentiment_index": round(sentiment_index, 2),
        "sentiment_confidence_pct": round(avg_sent_conf, 1),
        "dominant_emotion": dominant_emotion,
        "emotional_intensity_pct": round(emotional_intensity, 1),
        "source_diversity_pct": round(source_div, 1),
        "narrative_concentration_pct": round(narrative_concentration, 1),
        "volume_velocity_pct": round(volume_velocity, 1),
        "negative_velocity_pct": round(negative_velocity, 1),
        "max_robust_anomaly_z": round(max_z, 2),
        "attention_index": min(100, max(0, attention)),
        "assessment_confidence": min(100, max(0, confidence)),
        "strong_negative_pct": round(strong_negative, 2),
    }


def run_risk_agent(df, trend_df, narratives):
    summary = compute_intelligence_summary(df, narratives, trend_df)
    return {
        "risk": summary["attention_index"],
        "negative_pct": summary["weighted_negative_pct"],
        "raw_negative_pct": summary["raw_negative_pct"],
        "recent_growth_pct": summary["volume_velocity_pct"],
        "assessment_confidence": summary["assessment_confidence"],
        "sentiment_index": summary["sentiment_index"],
        "dominant_emotion": summary["dominant_emotion"],
        "emotional_intensity_pct": summary["emotional_intensity_pct"],
    }

# =========================================================
# TOP SENTIMENT BRIEFING HELPERS
# =========================================================
def brief_text(value, max_chars=180):
    """Create a short local/extractive summary without a paid LLM."""
    t = re.sub(r"https?://\S+", "", str(value or ""))
    t = re.sub(r"\s+", " ", t).strip()

    if not t:
        return "No text available."

    sentences = re.split(r"(?<=[.!?])\s+", t)
    candidate = sentences[0].strip() if sentences else t

    # Very short first sentences are usually not informative enough.
    if len(candidate) < 45:
        candidate = t

    if len(candidate) > max_chars:
        candidate = candidate[: max_chars - 1].rstrip() + "…"

    return candidate


def top_sentiment_items(df, polarity="positive", limit=10, news=False):
    """Rank strongest positive/negative items for X or News."""
    if df is None or df.empty:
        return pd.DataFrame()

    work = df.copy()

    if polarity == "positive":
        work = work[work["sentiment"].eq("Positive")].copy()
        score_ascending = False
    else:
        work = work[work["sentiment"].eq("Negative")].copy()
        score_ascending = True

    if work.empty:
        return work

    work["_rank_sentiment"] = work["sentiment_score"].astype(float) * work.get(
        "sentiment_confidence", pd.Series(1.0, index=work.index)
    ).astype(float).clip(0.2, 1.0)
    if news:
        # Story-level dedupe is repeated here as a display safety net.
        work = deduplicate_news_for_analysis(
            work,
            query="ISRO",
            max_records=max(limit * 4, limit),
        )
        if work.empty:
            return work
        work["_rank_sentiment"] = work["sentiment_score"].astype(float) * work.get(
            "sentiment_confidence", pd.Series(1.0, index=work.index)
        ).astype(float).clip(0.2, 1.0)
        work = work.sort_values(
            ["_rank_sentiment", "timestamp"],
            ascending=[score_ascending, False],
        )
    else:
        # Confidence-weighted sentiment, then engagement and recency.
        work = work.sort_values(
            ["_rank_sentiment", "engagement", "timestamp"],
            ascending=[score_ascending, False, False],
        )

    return work.head(limit).copy()


def render_brief_list(df, polarity="positive", news=False):
    items = top_sentiment_items(
        df,
        polarity=polarity,
        limit=10,
        news=news,
    )

    if items.empty:
        st.info(f"No {polarity} items are available in the loaded data.")
        return

    for rank, (_, row) in enumerate(items.iterrows(), start=1):
        summary = brief_text(row.get("text", ""))
        source = str(row.get("username", "") or "Unknown")
        score = float(row.get("sentiment_score", 0.0))
        confidence = float(row.get("sentiment_confidence", 0.0) or 0.0)
        engagement = int(row.get("engagement", 0) or 0)

        st.markdown(f"**{rank}. {summary}**")

        if news:
            st.caption(
                f"Source: {source} · sentiment {score:+.2f} · confidence {confidence:.0%}"
            )
        else:
            st.caption(
                f"Source/user: {source} · sentiment {score:+.2f} · confidence {confidence:.0%} · "
                f"engagement {engagement}"
            )




# =========================================================
# MONITORING / HISTORIC NEGATIVITY HELPERS
# =========================================================
def compute_negativity_history(dataframe, freq="1h"):
    """Return hourly negativity history for Overall, X and News."""
    cols = ["bucket", "series", "negative_pct", "records"]
    if dataframe is None or dataframe.empty:
        return pd.DataFrame(columns=cols)

    work = dataframe.copy()
    work["timestamp"] = pd.to_datetime(
        work["timestamp"], errors="coerce", utc=True
    ).dt.tz_convert(IST)
    work = work.dropna(subset=["timestamp"]).copy()
    if work.empty:
        return pd.DataFrame(columns=cols)

    work["bucket"] = work["timestamp"].dt.floor(freq)
    work["is_negative"] = work["sentiment"].eq("Negative").astype(int)

    pieces = []

    def add_series(frame, label):
        if frame.empty:
            return
        g = (
            frame.groupby("bucket")
            .agg(
                records=("text", "size"),
                negative_pct=("is_negative", "mean"),
            )
            .reset_index()
        )
        g["negative_pct"] = g["negative_pct"] * 100
        g["series"] = label
        pieces.append(g[cols])

    add_series(work, "Overall")
    add_series(
        work[work["platform"].isin(["x_manual", "x"])],
        "X",
    )
    add_series(
        work[work["platform"].eq("web_news")],
        "News",
    )

    if not pieces:
        return pd.DataFrame(columns=cols)

    return pd.concat(pieces, ignore_index=True).sort_values(
        ["bucket", "series"]
    )


def _make_alarm_wav(duration_seconds=1.6, sample_rate=22050):
    """Generate a short two-tone WAV alarm entirely in memory."""
    output = io.BytesIO()
    total = int(duration_seconds * sample_rate)

    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)

        frames = bytearray()
        for i in range(total):
            t = i / sample_rate
            # Alternating tone blocks create a noticeable alarm pattern.
            block = int(t / 0.20) % 2
            freq = 880 if block == 0 else 660
            envelope = 0.55
            sample = int(
                32767 * envelope * math.sin(2 * math.pi * freq * t)
            )
            frames.extend(struct.pack("<h", sample))

        wav_file.writeframes(bytes(frames))

    return output.getvalue()


def _play_alarm_sound():
    wav_bytes = _make_alarm_wav()
    encoded = base64.b64encode(wav_bytes).decode("ascii")
    components.html(
        f"""
        <audio autoplay>
          <source src="data:audio/wav;base64,{encoded}" type="audio/wav">
        </audio>
        """,
        height=0,
    )


def _record_monitor_snapshot(negative_pct):
    """Keep one in-session monitoring snapshot per hourly bucket."""
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    bucket = now.floor("1h")
    history = st.session_state.get("monitor_snapshots_v57", [])

    if not history or history[-1]["bucket"] != bucket.isoformat():
        history.append(
            {
                "bucket": bucket.isoformat(),
                "negative_pct": float(negative_pct),
            }
        )
        history = history[-168:]  # one week at hourly intervals
        st.session_state["monitor_snapshots_v57"] = history

    return pd.DataFrame(history)


# =========================================================
# PDF CHART HELPERS
# =========================================================
def _fig_to_png_buffer(fig):
    buf = io.BytesIO()
    fig.savefig(
        buf,
        format="png",
        dpi=150,
        bbox_inches="tight",
        facecolor="white",
    )
    plt.close(fig)
    buf.seek(0)
    return buf


def _pdf_chart_sentiment(dataframe):
    counts = (
        dataframe["sentiment"]
        .value_counts()
        .reindex(["Positive", "Neutral", "Negative"], fill_value=0)
    )
    fig, ax = plt.subplots(figsize=(7.4, 3.4))
    ax.bar(
        counts.index,
        counts.values,
        color=[SENTIMENT_COLORS[label] for label in counts.index],
    )
    ax.set_title("Sentiment distribution")
    ax.set_ylabel("Records")
    ax.grid(axis="y", alpha=0.2)
    fig.tight_layout()
    return _fig_to_png_buffer(fig)


def _pdf_chart_volume(dataframe):
    hourly = (
        dataframe.groupby(dataframe["timestamp"].dt.floor("h"))
        .size()
        .reset_index(name="records")
    )
    if hourly.empty:
        return None
    fig, ax = plt.subplots(figsize=(7.4, 3.4))
    ax.plot(hourly["timestamp"], hourly["records"], marker="o")
    ax.set_title("Conversation / coverage volume over time")
    ax.set_ylabel("Records per hour")
    ax.tick_params(axis="x", rotation=30)
    ax.grid(alpha=0.2)
    fig.tight_layout()
    return _fig_to_png_buffer(fig)


def _pdf_chart_negativity(dataframe, threshold=35.0):
    hist = compute_negativity_history(dataframe)
    if hist.empty:
        return None
    fig, ax = plt.subplots(figsize=(7.4, 3.5))
    line_styles = ["-", "--", ":", "-."]
    marker_styles = ["o", "s", "^", "D"]
    for idx, (label, group) in enumerate(hist.groupby("series")):
        ax.plot(
            group["bucket"],
            group["negative_pct"],
            marker=marker_styles[idx % len(marker_styles)],
            linestyle=line_styles[idx % len(line_styles)],
            color=NEGATIVE_RED,
            label=label,
        )
    ax.axhline(
        threshold,
        linestyle="--",
        color=NEUTRAL_GREY,
        label=f"Alert threshold ({threshold:.0f}%)",
    )
    ax.set_ylim(0, 100)
    ax.set_title("Historic negativity - hourly buckets (IST)")
    ax.set_ylabel("Negative share (%)")
    ax.tick_params(axis="x", rotation=30)
    ax.legend(loc="best", fontsize=8)
    ax.grid(alpha=0.2)
    fig.tight_layout()
    return _fig_to_png_buffer(fig)


def _pdf_chart_emotions(dataframe):
    counts = dataframe["emotion"].value_counts().head(10)
    if counts.empty:
        return None
    fig, ax = plt.subplots(figsize=(7.4, 3.5))
    ax.barh(counts.index[::-1], counts.values[::-1])
    ax.set_title("Emotion signals")
    ax.set_xlabel("Records")
    ax.grid(axis="x", alpha=0.2)
    fig.tight_layout()
    return _fig_to_png_buffer(fig)


def _pdf_chart_intents(dataframe):
    if dataframe is None or dataframe.empty or "intent" not in dataframe:
        return None
    counts = dataframe["intent"].fillna("Information/Update").value_counts().head(10)
    fig, ax = plt.subplots(figsize=(8.2, 3.5))
    ax.barh(counts.index[::-1], counts.values[::-1])
    ax.set_title("Communication intent signals")
    ax.set_xlabel("Records")
    fig.tight_layout()
    out = io.BytesIO()
    fig.savefig(out, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    out.seek(0)
    return out


def _pdf_chart_clusters(dataframe):
    counts = dataframe["semantic_cluster"].value_counts().sort_index()
    if counts.empty:
        return None
    fig, ax = plt.subplots(figsize=(7.4, 3.4))
    ax.bar([f"C{x}" for x in counts.index], counts.values)
    ax.set_title("Semantic cluster volume")
    ax.set_ylabel("Records")
    ax.grid(axis="y", alpha=0.2)
    fig.tight_layout()
    return _fig_to_png_buffer(fig)


def _pdf_chart_cluster_negativity(dataframe):
    stats = (
        dataframe.groupby("semantic_cluster")["sentiment"]
        .apply(lambda s: s.eq("Negative").mean() * 100)
        .sort_index()
    )
    if stats.empty:
        return None
    fig, ax = plt.subplots(figsize=(7.4, 3.4))
    ax.bar(
        [f"C{x}" for x in stats.index],
        stats.values,
        color=NEGATIVE_RED,
    )
    ax.set_ylim(0, 100)
    ax.set_title("Negative share by semantic cluster")
    ax.set_ylabel("Negative share (%)")
    ax.grid(axis="y", alpha=0.2)
    fig.tight_layout()
    return _fig_to_png_buffer(fig)


def _pdf_chart_platform_sentiment(dataframe):
    work = dataframe.copy()
    work["source_group"] = np.where(
        work["platform"].isin(["x_manual", "x"]),
        "X",
        np.where(
            work["platform"].eq("web_news"),
            "News",
            work["platform"],
        ),
    )
    table = pd.crosstab(work["source_group"], work["sentiment"])
    table = table.reindex(
        columns=["Positive", "Neutral", "Negative"],
        fill_value=0,
    )
    if table.empty:
        return None
    fig, ax = plt.subplots(figsize=(7.4, 3.5))
    bottom = np.zeros(len(table))
    x = np.arange(len(table.index))
    for col in table.columns:
        vals = table[col].to_numpy()
        ax.bar(
            x,
            vals,
            bottom=bottom,
            label=col,
            color=SENTIMENT_COLORS.get(col, NEUTRAL_GREY),
        )
        bottom += vals
    ax.set_xticks(x)
    ax.set_xticklabels(table.index)
    ax.set_title("Sentiment mix by source type")
    ax.set_ylabel("Records")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.2)
    fig.tight_layout()
    return _fig_to_png_buffer(fig)


def _pdf_chart_top_sources(dataframe):
    source_stats = (
        dataframe.groupby("username")
        .agg(records=("text", "size"), engagement=("engagement", "sum"))
        .sort_values(["records", "engagement"], ascending=False)
        .head(12)
        .sort_values("records")
    )
    if source_stats.empty:
        return None
    fig, ax = plt.subplots(figsize=(7.4, 4.0))
    ax.barh(source_stats.index, source_stats["records"])
    ax.set_title("Top sources / authors by record volume")
    ax.set_xlabel("Records")
    ax.grid(axis="x", alpha=0.2)
    fig.tight_layout()
    return _fig_to_png_buffer(fig)


def _pdf_chart_semantic_map(dataframe):
    if not {"semantic_x", "semantic_y", "semantic_cluster"}.issubset(
        dataframe.columns
    ):
        return None
    fig, ax = plt.subplots(figsize=(7.4, 4.0))
    for cluster, group in dataframe.groupby("semantic_cluster"):
        ax.scatter(
            group["semantic_x"],
            group["semantic_y"],
            s=18,
            alpha=0.7,
            label=f"C{cluster}",
        )
    ax.set_title("Semantic conversation map")
    ax.set_xlabel("Semantic dimension 1")
    ax.set_ylabel("Semantic dimension 2")
    if dataframe["semantic_cluster"].nunique() <= 10:
        ax.legend(fontsize=7, ncol=2)
    ax.grid(alpha=0.15)
    fig.tight_layout()
    return _fig_to_png_buffer(fig)


# =========================================================
# PDF REPORTING
# =========================================================
def _report_clean_text(value):
    """Normalize text for reliable PDF rendering."""
    t = str(value or "")
    t = t.replace("\u2013", "-").replace("\u2014", "-")
    t = t.replace("\u2018", "'").replace("\u2019", "'")
    t = t.replace("\u201c", '"').replace("\u201d", '"')
    # Remove common emoji/symbol ranges that can render as tofu squares.
    t = re.sub(
        r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF]",
        "",
        t,
    )
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _register_report_fonts():
    """
    Prefer a broad Latin Unicode font already installed on the host and
    separately register Devanagari when available. No font file is bundled.
    """
    base_candidates = [
        (
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        ),
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ),
        (
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ),
    ]

    normal_name, bold_name = "Helvetica", "Helvetica-Bold"

    for regular_path, bold_path in base_candidates:
        if os.path.exists(regular_path) and os.path.exists(bold_path):
            try:
                pdfmetrics.registerFont(
                    TTFont("SMI-Regular", regular_path)
                )
                pdfmetrics.registerFont(
                    TTFont("SMI-Bold", bold_path)
                )
                normal_name, bold_name = "SMI-Regular", "SMI-Bold"
                break
            except Exception:
                pass

    deva_regular = (
        "/usr/share/fonts/truetype/noto/"
        "NotoSansDevanagari-Regular.ttf"
    )
    if os.path.exists(deva_regular):
        try:
            pdfmetrics.registerFont(
                TTFont("SMI-Deva", deva_regular)
            )
        except Exception:
            pass

    return normal_name, bold_name


def _report_para(text_value, style):
    clean = _report_clean_text(text_value)

    # Helvetica fallback cannot encode many scripts.
    if style.fontName == "Helvetica":
        clean = clean.encode("latin-1", "replace").decode("latin-1")

    marked = escape(clean).replace("\n", "<br/>")

    # Use a Devanagari font only for Devanagari runs, while retaining
    # the broad Latin font for the rest of the paragraph.
    if "SMI-Deva" in pdfmetrics.getRegisteredFontNames():
        marked = re.sub(
            r"([\u0900-\u097F]+)",
            r'<font name="SMI-Deva">\1</font>',
            marked,
        )
    else:
        marked = re.sub(r"[\u0900-\u097F]+", "?", marked)

    return Paragraph(marked, style)


def _report_brief(value, max_chars=190):
    t = _report_clean_text(value)
    if not t:
        return "No text available."

    sentences = re.split(r"(?<=[.!?])\s+", t)
    candidate = sentences[0].strip() if sentences else t
    if len(candidate) < 45:
        candidate = t

    if len(candidate) > max_chars:
        candidate = candidate[: max_chars - 1].rstrip() + "..."
    return candidate


def _report_top_items(dataframe, polarity, limit=10, news=False):
    if dataframe is None or dataframe.empty:
        return pd.DataFrame()

    work = dataframe.copy()

    if polarity == "Positive":
        work = work[work["sentiment"].eq("Positive")].copy()
        score_asc = False
    else:
        work = work[work["sentiment"].eq("Negative")].copy()
        score_asc = True

    if work.empty:
        return work

    if "sentiment_score" not in work.columns or "sentiment" not in work.columns:
        work = run_sentiment_agent(work)

    work["_rank_sentiment"] = work["sentiment_score"].astype(float) * work.get(
        "sentiment_confidence", pd.Series(1.0, index=work.index)
    ).astype(float).clip(0.2, 1.0)
    if news:
        work = deduplicate_news_for_analysis(
            work,
            query="ISRO",
            max_records=max(limit * 4, limit),
        )
        if work.empty:
            return work
        work["_rank_sentiment"] = work["sentiment_score"].astype(float) * work.get(
            "sentiment_confidence", pd.Series(1.0, index=work.index)
        ).astype(float).clip(0.2, 1.0)
        return work.sort_values(
            ["_rank_sentiment", "timestamp"],
            ascending=[score_asc, False],
        ).head(limit)

    return work.sort_values(
        ["_rank_sentiment", "engagement", "timestamp"],
        ascending=[score_asc, False, False],
    ).head(limit)


def _report_monitoring_score(dataframe, trend_frame, narrative_frame):
    return int(compute_intelligence_summary(dataframe, narrative_frame, trend_frame)["attention_index"])

def _prepare_news_evidence_frame(report_df):
    """Return Web News rows with stable evidence numbers (newest first)."""
    if report_df is None or report_df.empty:
        return pd.DataFrame()

    news = report_df[
        report_df["platform"].eq("web_news")
    ].copy()

    if news.empty:
        news["_evidence_no"] = pd.Series(dtype="int64")
        return news

    news = news.sort_values(
        ["timestamp", "username", "text"],
        ascending=[False, True, True],
        kind="stable",
    ).reset_index(drop=True)
    news["_evidence_no"] = range(1, len(news) + 1)
    return news


def _safe_evidence_filename(value, max_len=42):
    value = _report_clean_text(value)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    if not value:
        value = "news_source"
    return value[:max_len].rstrip("_") or "news_source"


def _narrative_for_cluster(narrative_df, cluster_id):
    if narrative_df is None or narrative_df.empty:
        return "Not available"
    try:
        cluster_value = int(cluster_id)
    except Exception:
        return "Not available"

    match = narrative_df[
        narrative_df["cluster"].eq(cluster_value)
    ]
    if match.empty:
        return "Not available"
    return _report_clean_text(match.iloc[0].get("narrative", "")) or "Not available"


def build_news_evidence_pdf(row, evidence_no, narrative_label="Not available"):
    """
    Build one compact evidence sheet for a Web News feed item.
    This intentionally documents feed metadata/headline only; it does not
    reproduce the full copyrighted news article.
    """
    buffer = io.BytesIO()
    normal_font, bold_font = _register_report_fonts()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Web News Evidence {int(evidence_no):03d}",
        author="Social Media Intelligence Platform",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "EvidenceTitle",
        parent=styles["Title"],
        fontName=bold_font,
        fontSize=16,
        leading=20,
        alignment=TA_CENTER,
        spaceAfter=8,
    )
    h1 = ParagraphStyle(
        "EvidenceH1",
        parent=styles["Heading1"],
        fontName=bold_font,
        fontSize=11,
        leading=14,
        spaceBefore=7,
        spaceAfter=5,
    )
    body = ParagraphStyle(
        "EvidenceBody",
        parent=styles["BodyText"],
        fontName=normal_font,
        fontSize=9,
        leading=12,
        spaceAfter=5,
    )
    small = ParagraphStyle(
        "EvidenceSmall",
        parent=body,
        fontSize=7.5,
        leading=9.5,
    )
    label_style = ParagraphStyle(
        "EvidenceLabel",
        parent=small,
        fontName=bold_font,
    )

    def p(value, style=body):
        return _report_para(value, style)

    timestamp = pd.to_datetime(
        row.get("timestamp"), errors="coerce", utc=True
    )
    if pd.isna(timestamp):
        timestamp_text = "Unavailable"
    else:
        timestamp_text = timestamp.tz_convert(IST).strftime(
            "%Y-%m-%d %H:%M:%S IST"
        )

    source = _report_clean_text(row.get("username", "")) or "Unknown source"
    headline = _report_clean_text(row.get("text", "")) or "No headline available"
    source_url = _report_clean_text(row.get("source_url", "")) or "Not provided by feed"
    sentiment = _report_clean_text(row.get("sentiment", "")) or "Unclassified"
    try:
        sentiment_score = float(row.get("sentiment_score", 0) or 0)
    except Exception:
        sentiment_score = 0.0
    emotion = _report_clean_text(row.get("emotion", "")) or "Other/Neutral"
    cluster = row.get("semantic_cluster", "Not available")

    story = [
        p(f"Web News Evidence #{int(evidence_no):03d}", title_style),
        p(
            "Feed evidence sheet - generated from the news headline and "
            "metadata available to the Social Media Intelligence Platform. "
            "It is not a PDF copy of the full publisher article.",
            small,
        ),
        Spacer(1, 6),
        p("Headline", h1),
        p(headline, body),
        Spacer(1, 4),
    ]

    metadata = [
        [p("Field", label_style), p("Value", label_style)],
        [p("Evidence number", label_style), p(f"{int(evidence_no):03d}", small)],
        [p("Time", label_style), p(timestamp_text, small)],
        [p("Media agency/source", label_style), p(source, small)],
        [p("Author", label_style), p("Not provided by feed", small)],
        [p("Sentiment", label_style), p(sentiment, small)],
        [p("Sentiment score", label_style), p(f"{sentiment_score:+.3f}", small)],
        [p("Emotion signal", label_style), p(emotion, small)],
        [p("Semantic cluster", label_style), p(str(cluster), small)],
        [p("Narrative", label_style), p(narrative_label, small)],
    ]

    tbl = Table(metadata, colWidths=[43 * mm, 125 * mm], hAlign="LEFT")
    tbl.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9EEF7")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9D2DF")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ])
    )
    story.extend([
        tbl,
        Spacer(1, 8),
        p("Source URL", h1),
        p(source_url, small),
        Spacer(1, 6),
        p("Interpretation note", h1),
        p(
            "Sentiment is calculated from headline framing using VADER. "
            "It does not represent verified public opinion, publisher intent, "
            "or the truth/falsity of the underlying claim. Semantic cluster "
            "and narrative labels are automated analytical groupings.",
            small,
        ),
        p(
            "Generated: "
            + datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
            small,
        ),
    ])

    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def build_news_evidence_zip(report_df, narrative_df):
    """
    Build a ZIP containing one numbered evidence PDF per Web News item plus
    a manifest.csv that maps evidence numbers to source metadata.
    """
    news = _prepare_news_evidence_frame(report_df)
    zip_buffer = io.BytesIO()
    manifest_rows = []

    with zipfile.ZipFile(
        zip_buffer,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        if news.empty:
            archive.writestr(
                "README.txt",
                "No web_news records were present in this analysis cycle.\n",
            )
        else:
            for _, row in news.iterrows():
                evidence_no = int(row["_evidence_no"])
                sentiment = (
                    _report_clean_text(row.get("sentiment", "neutral"))
                    or "neutral"
                ).lower()
                source_slug = _safe_evidence_filename(
                    row.get("username", "news_source")
                )
                filename = (
                    f"{evidence_no:03d}_{sentiment}_{source_slug}.pdf"
                )
                narrative_label = _narrative_for_cluster(
                    narrative_df,
                    row.get("semantic_cluster", ""),
                )
                pdf_bytes = build_news_evidence_pdf(
                    row,
                    evidence_no,
                    narrative_label=narrative_label,
                )
                archive.writestr(filename, pdf_bytes)

                timestamp = pd.to_datetime(
                    row.get("timestamp"), errors="coerce", utc=True
                )
                if pd.isna(timestamp):
                    timestamp_text = ""
                else:
                    timestamp_text = timestamp.tz_convert(IST).strftime(
                        "%Y-%m-%d %H:%M:%S IST"
                    )

                manifest_rows.append({
                    "evidence_no": f"{evidence_no:03d}",
                    "time_ist": timestamp_text,
                    "sentiment": _report_clean_text(row.get("sentiment", "")),
                    "sentiment_score": row.get("sentiment_score", 0),
                    "semantic_cluster": row.get("semantic_cluster", ""),
                    "narrative": narrative_label,
                    "media_source": _report_clean_text(row.get("username", "")),
                    "author": "Not provided by feed",
                    "headline": _report_clean_text(row.get("text", "")),
                    "source_url": _report_clean_text(row.get("source_url", "")),
                    "pdf_filename": filename,
                })

        manifest_df = pd.DataFrame(
            manifest_rows,
            columns=[
                "evidence_no",
                "time_ist",
                "sentiment",
                "sentiment_score",
                "semantic_cluster",
                "narrative",
                "media_source",
                "author",
                "headline",
                "source_url",
                "pdf_filename",
            ],
        )
        archive.writestr(
            "manifest.csv",
            manifest_df.to_csv(index=False).encode("utf-8-sig"),
        )

    zip_buffer.seek(0)
    return zip_buffer.getvalue(), int(len(news))


def build_pdf_report(report_df, narrative_df, trend_df, alert_threshold=35.0):
    """
    Build a complete PDF from all currently loaded records.
    Includes top sentiment briefs, methodology/legends, cluster definitions,
    and annexures containing all X and News items.
    """
    buffer = io.BytesIO()
    normal_font, bold_font = _register_report_fonts()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=15 * mm,
        bottomMargin=16 * mm,
        title="Social Media Intelligence Report",
        author="Social Media Intelligence Platform",
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "SMITitle",
        parent=styles["Title"],
        fontName=bold_font,
        fontSize=18,
        leading=22,
        alignment=TA_CENTER,
        spaceAfter=8,
    )
    h1 = ParagraphStyle(
        "SMIH1",
        parent=styles["Heading1"],
        fontName=bold_font,
        fontSize=13,
        leading=16,
        spaceBefore=7,
        spaceAfter=5,
    )
    h2 = ParagraphStyle(
        "SMIH2",
        parent=styles["Heading2"],
        fontName=bold_font,
        fontSize=10.5,
        leading=13,
        spaceBefore=6,
        spaceAfter=4,
    )
    body = ParagraphStyle(
        "SMIBody",
        parent=styles["BodyText"],
        fontName=normal_font,
        fontSize=8.5,
        leading=11,
        spaceAfter=4,
    )
    small = ParagraphStyle(
        "SMISmall",
        parent=body,
        fontSize=7.2,
        leading=9,
    )
    table_text = ParagraphStyle(
        "SMITable",
        parent=body,
        fontSize=6.6,
        leading=8,
        spaceAfter=0,
    )
    table_head = ParagraphStyle(
        "SMITableHead",
        parent=table_text,
        fontName=bold_font,
        fontSize=6.6,
        leading=8,
    )

    story = []

    def p(value, style=body):
        return _report_para(value, style)

    def add_long_table(rows, widths, repeat_rows=1):
        tbl = LongTable(
            rows,
            colWidths=widths,
            repeatRows=repeat_rows,
            hAlign="LEFT",
        )
        tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9EEF7")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1F2937")),
                    ("FONTNAME", (0, 0), (-1, 0), bold_font),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9D2DF")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(tbl)
        story.append(Spacer(1, 5))

    generated = datetime.now(IST)
    x_df = report_df[
        report_df["platform"].isin(["x_manual", "x"])
    ].copy()
    news_df = _prepare_news_evidence_frame(report_df)

    sent_counts = (
        report_df["sentiment"]
        .value_counts()
        .reindex(["Positive", "Neutral", "Negative"], fill_value=0)
    )

    engagement_total = int(report_df["engagement"].sum())
    monitoring_score = _report_monitoring_score(
        report_df, trend_df, narrative_df
    )

    # Cover / report metadata
    story.append(p("Social Media Intelligence Report", title_style))
    story.append(
        p(
            "Automated analytical report generated from the records currently "
            "loaded in the dashboard. Annexures contain the full analyzed X "
            "and News item lists.",
            body,
        )
    )
    story.append(
        p(
            f"Generated: {generated.strftime('%Y-%m-%d %H:%M:%S IST')} | "
            f"Total records: {len(report_df):,} | X records: {len(x_df):,} | "
            f"News records: {len(news_df):,}",
            small,
        )
    )
    story.append(Spacer(1, 6))

    # Executive summary
    story.append(p("1. Executive overview", h1))
    summary_rows = [
        [p("Metric", table_head), p("Value", table_head)],
        [p("Total analyzed records", table_text), p(f"{len(report_df):,}", table_text)],
        [p("X/manual X records", table_text), p(f"{len(x_df):,}", table_text)],
        [p("News records", table_text), p(f"{len(news_df):,}", table_text)],
        [p("Positive", table_text), p(f"{int(sent_counts['Positive']):,}", table_text)],
        [p("Neutral", table_text), p(f"{int(sent_counts['Neutral']):,}", table_text)],
        [p("Negative", table_text), p(f"{int(sent_counts['Negative']):,}", table_text)],
        [p("X engagement total", table_text), p(f"{engagement_total:,}", table_text)],
        [p("Prototype monitoring score", table_text), p(f"{monitoring_score}/100", table_text)],
        [p("Negativity sound-alert threshold", table_text), p(f"{alert_threshold:.0f}%", table_text)],
        [p("Detected semantic clusters", table_text), p(f"{report_df['semantic_cluster'].nunique()}", table_text)],
    ]
    add_long_table(summary_rows, [62 * mm, 45 * mm])

    # Charts
    story.append(p("2. Charts and monitoring visuals", h1))
    story.append(
        p(
            "The following charts are generated directly from the same "
            "records used for the analytical tables and annexures.",
            small,
        )
    )

    chart_specs = [
        ("Sentiment distribution", _pdf_chart_sentiment(report_df)),
        ("Historic negativity", _pdf_chart_negativity(report_df, alert_threshold)),
        ("Conversation / coverage volume", _pdf_chart_volume(report_df)),
        ("Sentiment mix by source", _pdf_chart_platform_sentiment(report_df)),
        ("Emotion signals", _pdf_chart_emotions(report_df)),
        ("Semantic cluster volume", _pdf_chart_clusters(report_df)),
        ("Negativity by cluster", _pdf_chart_cluster_negativity(report_df)),
        ("Top sources / authors", _pdf_chart_top_sources(report_df)),
        ("Semantic conversation map", _pdf_chart_semantic_map(report_df)),
    ]

    for chart_title, chart_buffer in chart_specs:
        if chart_buffer is None:
            continue
        story.append(p(chart_title, h2))
        chart_image = RLImage(
            chart_buffer,
            width=180 * mm,
            height=82 * mm,
        )
        story.append(chart_image)
        story.append(Spacer(1, 5))

    # Top sentiment briefs
    story.append(p("3. Top sentiment briefs", h1))
    story.append(
        p(
            "X lists are ranked by sentiment strength, then engagement and "
            "recency. News lists are ranked by headline sentiment strength and "
            "recency. These are extractive briefs, not generative claims.",
            small,
        )
    )

    for label, frame, news_flag in [
        ("X - Top 10 positive", x_df, False),
        ("X - Top 10 negative", x_df, False),
        ("News - Top 10 positive", news_df, True),
        ("News - Top 10 negative", news_df, True),
    ]:
        polarity = "Positive" if "positive" in label.lower() else "Negative"
        story.append(p(label, h2))
        items = _report_top_items(
            frame,
            polarity=polarity,
            limit=10,
            news=news_flag,
        )
        if items.empty:
            story.append(p(f"No {polarity.lower()} items were available.", body))
        else:
            for rank, (_, row) in enumerate(items.iterrows(), start=1):
                brief = _report_brief(row.get("text", ""))
                source = _report_clean_text(row.get("username", "Unknown"))
                score = float(row.get("sentiment_score", 0.0))
                if news_flag:
                    meta = f"Source: {source} | Sentiment score: {score:+.2f}"
                else:
                    meta = (
                        f"User/source: {source} | Sentiment score: {score:+.2f} | "
                        f"Engagement: {int(row.get('engagement', 0) or 0)}"
                    )
                story.append(p(f"{rank}. {brief}", body))
                story.append(p(meta, small))

    # Cluster interpretation
    story.append(p("4. Cluster and narrative interpretation", h1))
    story.append(
        p(
            "Semantic clusters are automatically discovered groups of records "
            "with similar text. Cluster numbers are arbitrary identifiers: "
            "Cluster 0 is not better, worse, earlier, or more important than "
            "Cluster 1. Cluster IDs may change when the dataset changes.",
            body,
        )
    )
    story.append(
        p(
            "The semantic map axes are latent dimensions created by the text "
            "model. Their numeric values do not have a direct real-world unit. "
            "Distance is the useful signal: points closer together tend to have "
            "more similar wording/themes.",
            body,
        )
    )

    if narrative_df is not None and len(narrative_df):
        cluster_rows = [
            [
                p("Cluster", table_head),
                p("Narrative / key terms", table_head),
                p("Records", table_head),
                p("Negative %", table_head),
                p("12h growth %", table_head),
                p("Engagement", table_head),
            ]
        ]
        for _, row in narrative_df.sort_values("cluster").iterrows():
            cluster_rows.append(
                [
                    p(str(row.get("cluster", "")), table_text),
                    p(
                        f"{row.get('narrative', '')} | {row.get('top_terms', '')}",
                        table_text,
                    ),
                    p(str(row.get("posts", "")), table_text),
                    p(str(row.get("negative_pct", "")), table_text),
                    p(str(row.get("12h_growth_pct", "")), table_text),
                    p(str(row.get("engagement", "")), table_text),
                ]
            )
        add_long_table(
            cluster_rows,
            [13 * mm, 75 * mm, 16 * mm, 18 * mm, 19 * mm, 18 * mm],
        )
    else:
        story.append(p("No semantic narrative table was available.", body))

    # Legends & methodology
    story.append(p("5. Legends and methodology", h1))
    legends = [
        (
            "Sentiment",
            "Confidence-aware sentiment ensemble combining VADER, domain/event lexicons, negation, contrast, emoji and Hindi/roman-Hindi cues. News uses stricter neutral calibration and remains headline framing rather than public opinion."
        ),
        (
            "Emotion",
            "Multi-cue emotion analysis with primary/secondary emotion, confidence and intensity. Other/Neutral means evidence was insufficient for a specific emotion."
        ),
        (
            "Engagement",
            "For X/manual X: likes + reposts + replies. News feeds in this free "
            "build do not provide comparable social engagement, so these fields "
            "are normally zero for News."
        ),
        (
            "Semantic cluster",
            "An automatically discovered text-similarity group using TF-IDF, "
            "latent semantic reduction and KMeans. Cluster numbers and colors "
            "are identifiers only; they do not indicate severity or rank."
        ),
        (
            "Narrative",
            "A cluster label derived from its strongest terms plus a "
            "representative record. It is an analytical shorthand, not a "
            "verified factual claim."
        ),
        (
            "12h growth",
            "Change in record count in the most recent 12-hour window compared "
            "with the preceding 12-hour window. Zero can also occur when there "
            "is insufficient prior-window data."
        ),
        (
            "Trend anomaly",
            "Hourly volume is compared with a rolling 12-hour median baseline and robust MAD z-score; sparse low-volume spikes require minimum absolute volume before being flagged."
        ),
        (
            "Attention index",
            "Confidence-weighted triage index combining negative pressure, strong negativity, negative-cluster concentration, robust anomaly and negative velocity. It is not a probability or official risk rating."
        ),
        (
            "Automatic monitoring",
            "Build 5.13 supports a browser-independent GitHub Actions worker every 30 minutes. "
            "It can reload Supabase, refresh free News, run analysis, generate the PDF and email it even when Chrome is minimized or closed. "
            "The Streamlit browser heartbeat remains only as an optional fallback."
        ),
        (
            "Negativity alert",
            f"A sound alarm is triggered once when overall loaded-data negativity crosses above {alert_threshold:.0f}%. "
            "The alarm re-arms only after negativity falls back to or below the threshold. Browser autoplay policies may require prior interaction."
        ),
        (
            "Top-10 ranking",
            "X: sentiment strength, then engagement, then recency. "
            "News: headline sentiment strength, then recency."
        ),
        (
            "News author field",
            "The free GDELT/Google News feeds used here often supply a media "
            "source but not the article byline. Where unavailable, the report "
            "states 'Not provided by feed'."
        ),
    ]
    legend_rows = [[p("Legend", table_head), p("Meaning", table_head)]]
    for key, meaning in legends:
        legend_rows.append([p(key, table_text), p(meaning, table_text)])
    add_long_table(legend_rows, [38 * mm, 142 * mm])

    # Annexure A
    story.append(PageBreak())
    story.append(p("Annexure A - All X posts analyzed", h1))
    if x_df.empty:
        story.append(p("No X/manual-X records were present in the loaded dataset.", body))
    else:
        x_rows = [
            [
                p("#", table_head),
                p("Time (IST)", table_head),
                p("User/source", table_head),
                p("Cluster", table_head),
                p("Sentiment", table_head),
                p("Score", table_head),
                p("Eng.", table_head),
                p("Post text", table_head),
            ]
        ]
        for idx_num, (_, row) in enumerate(
            x_df.sort_values("timestamp", ascending=False).iterrows(),
            start=1,
        ):
            timestamp = (
                pd.to_datetime(row["timestamp"], utc=True)
                .tz_convert(IST)
                .strftime("%Y-%m-%d %H:%M:%S IST")
            )
            x_rows.append(
                [
                    p(str(idx_num), table_text),
                    p(timestamp, table_text),
                    p(row.get("username", ""), table_text),
                    p(row.get("semantic_cluster", ""), table_text),
                    p(row.get("sentiment", ""), table_text),
                    p(f"{float(row.get('sentiment_score', 0)):+.2f}", table_text),
                    p(str(int(row.get("engagement", 0) or 0)), table_text),
                    p(row.get("text", ""), table_text),
                ]
            )
        add_long_table(
            x_rows,
            [
                7 * mm,
                25 * mm,
                27 * mm,
                12 * mm,
                17 * mm,
                13 * mm,
                12 * mm,
                77 * mm,
            ],
        )

    # Annexure B
    story.append(PageBreak())
    story.append(p("Annexure B - News items analyzed", h1))
    story.append(
        p(
            "News is grouped by headline sentiment. Evidence numbers match "
            "the individual PDFs inside the Web News evidence ZIP attached "
            "to automated emails. Free feeds generally expose the media "
            "source/agency but not the individual article author.",
            body,
        )
    )

    if news_df.empty:
        story.append(p("No News records were present in the loaded dataset.", body))
    else:
        for category in ["Positive", "Negative", "Neutral"]:
            subset = news_df[
                news_df["sentiment"].eq(category)
            ].sort_values("timestamp", ascending=False)

            story.append(
                p(
                    f"{category} News ({len(subset):,})",
                    h2,
                )
            )

            if subset.empty:
                story.append(p(f"No {category.lower()} news items.", body))
                continue

            n_rows = [
                [
                    p("#", table_head),
                    p("Time (IST)", table_head),
                    p("Title", table_head),
                    p("Author", table_head),
                    p("Media agency/source", table_head),
                    p("Score", table_head),
                ]
            ]

            for _, row in subset.iterrows():
                evidence_no = int(row.get("_evidence_no", 0) or 0)
                timestamp = (
                    pd.to_datetime(row["timestamp"], utc=True)
                    .tz_convert(IST)
                    .strftime("%Y-%m-%d %H:%M:%S IST")
                )

                n_rows.append(
                    [
                        p(f"{evidence_no:03d}", table_text),
                        p(timestamp, table_text),
                        p(row.get("text", ""), table_text),
                        p("Not provided by feed", table_text),
                        p(row.get("username", ""), table_text),
                        p(
                            f"{float(row.get('sentiment_score', 0)):+.2f}",
                            table_text,
                        ),
                    ]
                )

            add_long_table(
                n_rows,
                [
                    8 * mm,
                    25 * mm,
                    80 * mm,
                    28 * mm,
                    32 * mm,
                    14 * mm,
                ],
            )

    def page_footer(canvas, document):
        canvas.saveState()
        canvas.setFont(normal_font, 7)
        canvas.setFillColor(colors.HexColor("#6B7280"))
        canvas.drawString(
            document.leftMargin,
            8 * mm,
            "Social Media Intelligence Platform - analytical report",
        )
        canvas.drawRightString(
            A4[0] - document.rightMargin,
            8 * mm,
            f"Page {document.page}",
        )
        canvas.restoreState()

    doc.build(
        story,
        onFirstPage=page_footer,
        onLaterPages=page_footer,
    )

    buffer.seek(0)
    return buffer.getvalue()


# =========================================================
# BUILD 5.14 REPORTING FRAMEWORK
# =========================================================
def _prepare_x_evidence_frame(report_df):
    """Return X/manual-X rows with stable evidence numbers (newest first)."""
    if report_df is None or report_df.empty:
        return pd.DataFrame()

    x_df = report_df[
        report_df["platform"].isin(["x", "x_manual"])
    ].copy()
    if x_df.empty:
        x_df["_evidence_no"] = pd.Series(dtype="int64")
        return x_df

    x_df = x_df.sort_values(
        ["timestamp", "username", "text"],
        ascending=[False, True, True],
        kind="stable",
    ).reset_index(drop=True)
    x_df["_evidence_no"] = range(1, len(x_df) + 1)
    return x_df


def build_x_evidence_pdf(row, evidence_no, narrative_label="Not available"):
    """One evidence sheet for one stored X post."""
    buffer = io.BytesIO()
    normal_font, bold_font = _register_report_fonts()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"X Evidence {int(evidence_no):03d}",
        author="Social Media Intelligence Platform",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "XEvidTitle", parent=styles["Title"], fontName=bold_font,
        fontSize=16, leading=20, alignment=TA_CENTER, spaceAfter=8,
    )
    h1 = ParagraphStyle(
        "XEvidH1", parent=styles["Heading1"], fontName=bold_font,
        fontSize=11, leading=14, spaceBefore=7, spaceAfter=5,
    )
    body = ParagraphStyle(
        "XEvidBody", parent=styles["BodyText"], fontName=normal_font,
        fontSize=9, leading=12, spaceAfter=5,
    )
    small = ParagraphStyle(
        "XEvidSmall", parent=body, fontSize=7.5, leading=9.5,
    )
    label_style = ParagraphStyle(
        "XEvidLabel", parent=small, fontName=bold_font,
    )

    def p(v, s=body):
        return _report_para(v, s)

    timestamp = pd.to_datetime(row.get("timestamp"), errors="coerce", utc=True)
    timestamp_text = (
        "Unavailable" if pd.isna(timestamp)
        else timestamp.tz_convert(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    )
    username = _report_clean_text(row.get("username", "")) or "Unknown user"
    post_text = _report_clean_text(row.get("text", "")) or "No post text available"
    source_url = _report_clean_text(row.get("source_url", "")) or "Not available"
    hashtags = _report_clean_text(row.get("hashtags", "")) or "None recorded"
    sentiment = _report_clean_text(row.get("sentiment", "")) or "Unclassified"
    emotion = _report_clean_text(row.get("emotion", "")) or "Other/Neutral"
    cluster = row.get("semantic_cluster", "Not available")
    try:
        score = float(row.get("sentiment_score", 0) or 0)
    except Exception:
        score = 0.0

    likes = int(row.get("likes", 0) or 0)
    reposts = int(row.get("reposts", 0) or 0)
    replies = int(row.get("replies", 0) or 0)
    followers = int(row.get("followers", 0) or 0)
    engagement = int(row.get("engagement", likes + reposts + replies) or 0)

    story = [
        p(f"X / Social Media Evidence #{int(evidence_no):03d}", title_style),
        p(
            "Evidence sheet generated from the post and metrics stored in the "
            "platform at analysis time. Engagement values may change later on X.",
            small,
        ),
        Spacer(1, 6),
        p("Post text", h1),
        p(post_text, body),
        Spacer(1, 4),
    ]

    rows = [
        [p("Field", label_style), p("Value", label_style)],
        [p("Evidence number", label_style), p(f"{int(evidence_no):03d}", small)],
        [p("Time", label_style), p(timestamp_text, small)],
        [p("Username", label_style), p(username, small)],
        [p("Hashtags", label_style), p(hashtags, small)],
        [p("Likes", label_style), p(str(likes), small)],
        [p("Reposts", label_style), p(str(reposts), small)],
        [p("Replies", label_style), p(str(replies), small)],
        [p("Followers", label_style), p(str(followers), small)],
        [p("Engagement", label_style), p(str(engagement), small)],
        [p("Sentiment", label_style), p(sentiment, small)],
        [p("Sentiment score", label_style), p(f"{score:+.3f}", small)],
        [p("Emotion signal", label_style), p(emotion, small)],
        [p("Semantic cluster", label_style), p(str(cluster), small)],
        [p("Narrative", label_style), p(narrative_label, small)],
    ]
    tbl = Table(rows, colWidths=[43 * mm, 125 * mm], hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9EEF7")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9D2DF")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.extend([
        tbl,
        Spacer(1, 8),
        p("Source URL", h1),
        p(source_url, small),
        Spacer(1, 6),
        p("Interpretation note", h1),
        p(
            "Sentiment, emotion, cluster and narrative are automated analytical "
            "labels. They should be treated as monitoring signals rather than "
            "verified statements about intent, truth or real-world impact.",
            small,
        ),
        p("Generated: " + datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"), small),
    ])
    doc.build(story)
    buffer.seek(0)
    return buffer.getvalue()


def build_evidence_book_from_zip(zip_bytes, title="Evidence Book"):
    """Merge numbered evidence PDFs from a ZIP into one consolidated PDF."""
    if not zip_bytes:
        raise ValueError("Evidence ZIP bytes are empty.")

    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as archive:
        pdf_names = sorted(
            name for name in archive.namelist()
            if name.lower().endswith(".pdf")
        )

        if not pdf_names:
            empty = io.BytesIO()
            normal_font, bold_font = _register_report_fonts()
            doc = SimpleDocTemplate(
                empty,
                pagesize=A4,
                rightMargin=18 * mm,
                leftMargin=18 * mm,
                topMargin=18 * mm,
                bottomMargin=18 * mm,
                title=title,
                author="Social Media Intelligence Platform",
            )
            styles = getSampleStyleSheet()
            doc.build([
                Paragraph(
                    escape(title),
                    ParagraphStyle(
                        "EvidenceBookEmptyTitle",
                        parent=styles["Title"],
                        fontName=bold_font,
                        fontSize=16,
                    ),
                ),
                Spacer(1, 8),
                Paragraph(
                    "No evidence records were available in this cycle.",
                    ParagraphStyle(
                        "EvidenceBookEmptyBody",
                        parent=styles["BodyText"],
                        fontName=normal_font,
                        fontSize=9,
                    ),
                ),
            ])
            empty.seek(0)
            return empty.getvalue(), 0

        writer = PdfWriter()
        for name in pdf_names:
            reader = PdfReader(io.BytesIO(archive.read(name)))
            for page in reader.pages:
                writer.add_page(page)

        writer.add_metadata({
            "/Title": title,
            "/Author": "Social Media Intelligence Platform",
            "/Subject": "Consolidated evidence pages",
        })
        out = io.BytesIO()
        writer.write(out)
        out.seek(0)
        return out.getvalue(), len(pdf_names)



def build_x_evidence_zip(report_df, narrative_df):
    """ZIP with one X evidence PDF per post plus manifest.csv."""
    x_df = _prepare_x_evidence_frame(report_df)
    zip_buffer = io.BytesIO()
    manifest_rows = []

    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        if x_df.empty:
            archive.writestr("README.txt", "No X/manual-X records were present in this analysis cycle.\n")
        else:
            for _, row in x_df.iterrows():
                evidence_no = int(row["_evidence_no"])
                sentiment = (_report_clean_text(row.get("sentiment", "neutral")) or "neutral").lower()
                user_slug = _safe_evidence_filename(row.get("username", "x_user"))
                filename = f"{evidence_no:03d}_{sentiment}_{user_slug}.pdf"
                narrative_label = _narrative_for_cluster(narrative_df, row.get("semantic_cluster", ""))
                archive.writestr(
                    filename,
                    build_x_evidence_pdf(row, evidence_no, narrative_label=narrative_label),
                )

                ts = pd.to_datetime(row.get("timestamp"), errors="coerce", utc=True)
                ts_text = "" if pd.isna(ts) else ts.tz_convert(IST).strftime("%Y-%m-%d %H:%M:%S IST")
                manifest_rows.append({
                    "evidence_no": f"{evidence_no:03d}",
                    "time_ist": ts_text,
                    "username": _report_clean_text(row.get("username", "")),
                    "sentiment": _report_clean_text(row.get("sentiment", "")),
                    "sentiment_score": row.get("sentiment_score", 0),
                    "semantic_cluster": row.get("semantic_cluster", ""),
                    "narrative": narrative_label,
                    "likes": int(row.get("likes", 0) or 0),
                    "reposts": int(row.get("reposts", 0) or 0),
                    "replies": int(row.get("replies", 0) or 0),
                    "engagement": int(row.get("engagement", 0) or 0),
                    "hashtags": _report_clean_text(row.get("hashtags", "")),
                    "text": _report_clean_text(row.get("text", "")),
                    "source_url": _report_clean_text(row.get("source_url", "")),
                    "pdf_filename": filename,
                })

        manifest_df = pd.DataFrame(manifest_rows)
        archive.writestr("manifest.csv", manifest_df.to_csv(index=False).encode("utf-8-sig"))
        archive.writestr(
            "README.txt",
            (
                "Social Media Intelligence evidence pack. Each numbered PDF documents one "
                "stored X/manual-X post and the analytical labels used in the report.\n"
            ),
        )

    zip_buffer.seek(0)
    return zip_buffer.getvalue(), int(len(x_df))


def _report_subset_analysis(report_df, report_kind):
    """Re-cluster each report on its own source population."""
    if report_df is None:
        report_df = pd.DataFrame()
    source = report_df.copy()
    if report_kind == "social":
        source = source[source["platform"].isin(["x", "x_manual"])].copy()
    elif report_kind == "news":
        source = source[source["platform"].eq("web_news")].copy()

    if source.empty:
        return source, pd.DataFrame(), pd.DataFrame()

    analyzed, vectorizer, tfidf, feature_names = run_semantic_topic_agent(source)
    narratives = run_narrative_agent(analyzed, vectorizer, tfidf, feature_names)
    trend = run_trend_agent(analyzed)
    return analyzed, narratives, trend


def _sentiment_counts(dataframe):
    if dataframe is None or dataframe.empty:
        return pd.Series({"Positive": 0, "Neutral": 0, "Negative": 0})
    return dataframe["sentiment"].value_counts().reindex(
        ["Positive", "Neutral", "Negative"], fill_value=0
    )


def _pct(count, total):
    return 0.0 if not total else (float(count) / float(total)) * 100.0


def _top_hashtag_text(dataframe, limit=6):
    if dataframe is None or dataframe.empty:
        return "None recorded"
    tags = []
    for value in dataframe.get("hashtags", pd.Series(dtype=str)).fillna(""):
        tags.extend(re.findall(r"#[\w]+", str(value), flags=re.UNICODE))
    if not tags:
        for value in dataframe.get("text", pd.Series(dtype=str)).fillna(""):
            tags.extend(re.findall(r"#[\w]+", str(value), flags=re.UNICODE))
    if not tags:
        return "None recorded"
    s = pd.Series([t.lower() for t in tags]).value_counts().head(limit)
    return ", ".join(f"{idx} ({int(val)})" for idx, val in s.items())


def _source_summary_text(dataframe, limit=5):
    if dataframe is None or dataframe.empty:
        return "No source data available"
    counts = dataframe["username"].fillna("Unknown").astype(str).value_counts().head(limit)
    return ", ".join(f"{name} ({int(count)})" for name, count in counts.items())


def _dominant_sentiment_text(dataframe, news=False):
    if dataframe is None or dataframe.empty:
        return "No records were available for automated tone classification."
    s = compute_intelligence_summary(dataframe)
    shares = {
        "Positive": s["weighted_positive_pct"],
        "Neutral": s["weighted_neutral_pct"],
        "Negative": s["weighted_negative_pct"],
    }
    dominant = max(shares, key=shares.get)
    label = "headline framing" if news else "sentiment"
    return (
        f"{dominant} is the largest confidence-weighted {label} category at "
        f"{shares[dominant]:.1f}%. The overall sentiment index is "
        f"{s['sentiment_index']:+.1f}/100 with analytical confidence "
        f"{s['assessment_confidence']}/100."
    )

def _cluster_takeaway_text(dataframe, narratives):
    if dataframe is None or dataframe.empty or narratives is None or narratives.empty:
        return "The dataset was too small or semantically diffuse for a reliable multi-cluster narrative summary."
    largest = narratives.sort_values(["posts", "momentum_score"], ascending=[False, False]).iloc[0]
    fastest = narratives.sort_values(["momentum_score", "posts"], ascending=[False, False]).iloc[0]
    return (
        f"The largest cluster is C{int(largest['cluster'])} ({largest['narrative']}) with "
        f"{int(largest['posts'])} records and cohesion {float(largest.get('cohesion_pct', 0)):.0f}%. "
        f"The strongest current narrative momentum is C{int(fastest['cluster'])} "
        f"at {float(fastest.get('momentum_score', 0)):.0f}/100."
    )

def _overall_conclusion_text(dataframe, narratives, alert_threshold, report_kind):
    if dataframe is None or dataframe.empty:
        return "No records were available in this reporting window, so no substantive intelligence conclusion can be drawn."
    trend = run_trend_agent(dataframe)
    s = compute_intelligence_summary(dataframe, narratives, trend)
    alert_text = (
        f"Confidence-weighted negative share is {s['weighted_negative_pct']:.1f}%, above the configured {alert_threshold:.0f}% monitoring threshold."
        if s["weighted_negative_pct"] > alert_threshold
        else f"Confidence-weighted negative share is {s['weighted_negative_pct']:.1f}%, below the configured {alert_threshold:.0f}% monitoring threshold."
    )
    largest_text = ""
    if narratives is not None and not narratives.empty:
        largest = narratives.sort_values(["posts", "momentum_score"], ascending=[False, False]).iloc[0]
        largest_text = (
            f" The largest narrative cluster is C{int(largest['cluster'])}, labelled "
            f"'{_report_clean_text(largest['narrative'])}', with {int(largest['posts'])} records "
            f"and cohesion {float(largest.get('cohesion_pct', 0)):.0f}%."
        )
    if report_kind == "news":
        context = " News values describe headline framing rather than public opinion."
    elif report_kind == "social":
        context = f" Stored social engagement totals {int(dataframe.get('engagement', pd.Series(dtype=int)).sum()):,}."
    else:
        x_df = dataframe[dataframe["platform"].isin(["x", "x_manual"])]
        n_df = dataframe[dataframe["platform"].eq("web_news")]
        xs = compute_intelligence_summary(x_df) if len(x_df) else None
        ns = compute_intelligence_summary(n_df) if len(n_df) else None
        context = (
            f" Cross-source weighted negative signals are {xs['weighted_negative_pct']:.1f}% for social and {ns['weighted_negative_pct']:.1f}% for News headline framing."
            if xs and ns else ""
        )
    return (
        f"Sentiment index is {s['sentiment_index']:+.1f}/100; dominant expressive emotion is {s['dominant_emotion']} "
        f"with mean emotional intensity {s['emotional_intensity_pct']:.1f}%. {alert_text} "
        f"Attention index is {s['attention_index']}/100 and assessment confidence is {s['assessment_confidence']}/100."
        f"{largest_text}{context} These are triage signals, not verified factual conclusions or probabilities."
    )

def build_intelligence_report_pdf(report_df, alert_threshold=35.0, report_kind="combined", query="ISRO", run_context=None):
    """
    Build one of three cleaner intelligence reports:
      social   -> X/social-media only
      news     -> Web News only
      combined -> cross-source combined intelligence
    """
    data, narratives, trend = _report_subset_analysis(report_df, report_kind)
    generated = datetime.now(IST)

    titles = {
        "social": "Social Media Intelligence Report",
        "news": "News Media Intelligence Report",
        "combined": "Combined Intelligence Report",
    }
    title = titles.get(report_kind, titles["combined"])

    buffer = io.BytesIO()
    normal_font, bold_font = _register_report_fonts()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=13 * mm, leftMargin=13 * mm,
        topMargin=14 * mm, bottomMargin=16 * mm,
        title=title, author="Social Media Intelligence Platform",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "R14Title", parent=styles["Title"], fontName=bold_font,
        fontSize=20, leading=24, alignment=TA_CENTER, spaceAfter=8,
        textColor=colors.HexColor("#1F2937"),
    )
    subtitle = ParagraphStyle(
        "R14Subtitle", parent=styles["BodyText"], fontName=normal_font,
        fontSize=9, leading=12, alignment=TA_CENTER, spaceAfter=6,
        textColor=colors.HexColor("#4B5563"),
    )
    h1 = ParagraphStyle(
        "R14H1", parent=styles["Heading1"], fontName=bold_font,
        fontSize=14, leading=17, spaceBefore=5, spaceAfter=5,
        textColor=colors.HexColor("#1F3B63"),
    )
    h2 = ParagraphStyle(
        "R14H2", parent=styles["Heading2"], fontName=bold_font,
        fontSize=10.5, leading=13, spaceBefore=5, spaceAfter=4,
        textColor=colors.HexColor("#374151"),
    )
    body = ParagraphStyle(
        "R14Body", parent=styles["BodyText"], fontName=normal_font,
        fontSize=8.5, leading=11.2, spaceAfter=4,
    )
    small = ParagraphStyle(
        "R14Small", parent=body, fontSize=7.2, leading=9.2,
        textColor=colors.HexColor("#4B5563"),
    )
    table_text = ParagraphStyle(
        "R14Table", parent=body, fontSize=6.7, leading=8.2, spaceAfter=0,
    )
    table_head = ParagraphStyle(
        "R14TableHead", parent=table_text, fontName=bold_font,
        textColor=colors.HexColor("#1F2937"),
    )

    story = []

    def p(v, s=body):
        return _report_para(v, s)

    def callout(label, value, bg="#F4F7FB", border="#CBD5E1"):
        t = Table(
            [[p(label, table_head), p(value, body)]],
            colWidths=[36 * mm, 142 * mm], hAlign="LEFT",
        )
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg)),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(border)),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(t)
        story.append(Spacer(1, 5))

    def add_table(rows, widths, repeat_rows=1):
        tbl = LongTable(rows, colWidths=widths, repeatRows=repeat_rows, hAlign="LEFT")
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E9EEF7")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C9D2DF")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.extend([tbl, Spacer(1, 5)])

    def add_chart(chart_title, chart_buffer, caption=None):
        if chart_buffer is None:
            return
        items = [p(chart_title, h2), RLImage(chart_buffer, width=172 * mm, height=72 * mm)]
        if caption:
            items.extend([Spacer(1, 2), p(caption, small)])
        story.extend([KeepTogether(items), Spacer(1, 5)])

    counts = _sentiment_counts(data)
    total = int(len(data))
    x_data = data[data["platform"].isin(["x", "x_manual"])] if total else data
    news_data = data[data["platform"].eq("web_news")] if total else data
    neg_pct = _pct(counts["Negative"], total)
    monitoring_score = _report_monitoring_score(data, trend, narratives) if total else 0

    # Cover
    story.append(p(title, title_style))
    if report_kind == "social":
        scope_text = "X / social-media records only. Focus: conversation, engagement, sentiment, actors and narratives."
    elif report_kind == "news":
        scope_text = "Web News records only. Focus: coverage volume, headline framing, media sources and narratives."
    else:
        scope_text = "Cross-source view combining stored X/social-media records with Web News coverage."
    story.append(p(scope_text, subtitle))
    story.append(p(
        f"Query/topic: {query} | Generated: {generated:%Y-%m-%d %H:%M:%S IST} | "
        f"Records: {total:,} | X: {len(x_data):,} | News: {len(news_data):,}",
        small,
    ))
    story.append(Spacer(1, 6))

    if run_context:
        if report_kind == "social":
            freshness = (
                f"X mode: {run_context.get('x_data_mode', 'n/a')}. "
                f"New X this cycle: {int(run_context.get('x_new_count', 0) or 0)}. "
                f"Newest X: {run_context.get('x_latest_ist', 'n/a')}. "
                f"Collector status: {run_context.get('x_collector_status', 'n/a')}."
            )
        elif report_kind == "news":
            freshness = (
                f"Rolling News window: {int(run_context.get('news_window_hours', 24) or 24)} hours. "
                f"Unique story families analyzed: {int(run_context.get('news_window_count', 0) or 0)}. "
                f"New story families this cycle: {int(run_context.get('news_new_count', 0) or 0)}. "
                f"Duplicate/near-duplicate rows suppressed: "
                f"{int(run_context.get('news_duplicates_removed', 0) or 0)}. "
                f"Irrelevant fuzzy matches rejected: "
                f"{int(run_context.get('news_irrelevant_removed', 0) or 0)}. "
                f"Newest News: {run_context.get('news_latest_ist', 'n/a')}."
            )
        else:
            freshness = (
                f"X: {run_context.get('x_data_mode', 'n/a')}; "
                f"+{int(run_context.get('x_new_count', 0) or 0)} new this cycle; "
                f"newest {run_context.get('x_latest_ist', 'n/a')}. "
                f"News: {int(run_context.get('news_window_count', 0) or 0)} unique story families in a "
                f"{int(run_context.get('news_window_hours', 24) or 24)}h rolling window; "
                f"+{int(run_context.get('news_new_count', 0) or 0)} new; "
                f"{int(run_context.get('news_duplicates_removed', 0) or 0)} duplicate rows suppressed."
            )
        callout("Data freshness", freshness, bg="#FFF8E7", border="#D7B35C")

    # 1 Executive overview
    story.append(p("1. Executive overview", h1))
    callout(
        "Purpose",
        "This section gives the decision-level snapshot before the detailed charts. Values are calculated only from records available in this reporting window.",
    )
    summary_rows = [
        [p("Metric", table_head), p("Value", table_head)],
        [p("Analyzed records", table_text), p(f"{total:,}", table_text)],
        [p("Positive", table_text), p(f"{int(counts['Positive']):,} ({_pct(counts['Positive'], total):.1f}%)", table_text)],
        [p("Neutral", table_text), p(f"{int(counts['Neutral']):,} ({_pct(counts['Neutral'], total):.1f}%)", table_text)],
        [p("Negative", table_text), p(f"{int(counts['Negative']):,} ({neg_pct:.1f}%)", table_text)],
        [p("Attention index", table_text), p(f"{monitoring_score}/100", table_text)],
        [p("Assessment confidence", table_text), p(f"{compute_intelligence_summary(data, narratives, trend)['assessment_confidence']}/100", table_text)],
        [p("Sentiment index", table_text), p(f"{compute_intelligence_summary(data, narratives, trend)['sentiment_index']:+.1f}/100", table_text)],
        [p("Weighted negative", table_text), p(f"{compute_intelligence_summary(data, narratives, trend)['weighted_negative_pct']:.1f}%", table_text)],
        [p("Dominant emotion", table_text), p(str(compute_intelligence_summary(data, narratives, trend)['dominant_emotion']), table_text)],
        [p("Emotional intensity", table_text), p(f"{compute_intelligence_summary(data, narratives, trend)['emotional_intensity_pct']:.1f}%", table_text)],
        [p("Negative-share threshold", table_text), p(f"{alert_threshold:.0f}%", table_text)],
        [p("Semantic clusters", table_text), p(str(data['semantic_cluster'].nunique()) if total else "0", table_text)],
    ]
    if report_kind in {"social", "combined"}:
        summary_rows.append([p("Stored X engagement", table_text), p(f"{int(x_data.get('engagement', pd.Series(dtype=int)).sum()):,}", table_text)])
        if run_context:
            summary_rows.append([p("New X this cycle", table_text), p(str(int(run_context.get("x_new_count", 0) or 0)), table_text)])
            summary_rows.append([p("X data mode", table_text), p(run_context.get("x_data_mode", "n/a"), table_text)])
            summary_rows.append([p("Newest X record", table_text), p(run_context.get("x_latest_ist", "n/a"), table_text)])
    if report_kind in {"news", "combined"} and run_context:
        summary_rows.append([p("New News this cycle", table_text), p(str(int(run_context.get("news_new_count", 0) or 0)), table_text)])
        summary_rows.append([p("Retained News in window", table_text), p(str(int(run_context.get("news_retained_count", 0) or 0)), table_text)])
    if report_kind == "social":
        summary_rows.append([p("Top hashtags", table_text), p(_top_hashtag_text(data), table_text)])
    if report_kind == "news":
        summary_rows.append([p("Leading media sources", table_text), p(_source_summary_text(data), table_text)])
    add_table(summary_rows, [64 * mm, 106 * mm])
    callout("Executive takeaway", _dominant_sentiment_text(data, news=(report_kind == "news")), bg="#EEF5FF")

    # 2 Dynamics
    story.append(PageBreak())
    story.append(p("2. Volume, tone and source dynamics", h1))
    callout(
        "What this section shows",
        "This section explains how much activity is present, how the automated tone is distributed, and how the activity changes over time. For News, sentiment means headline framing rather than public opinion.",
    )
    add_chart("Tone / sentiment distribution", _pdf_chart_sentiment(data), "Blue = Positive, Grey = Neutral, Red = Negative.")
    add_chart("Hourly historic negativity", _pdf_chart_negativity(data, alert_threshold), "The dashed line is the configured negative-share monitoring threshold.")
    add_chart("Conversation / coverage volume over time", _pdf_chart_volume(data), "Shows hourly record volume in the analyzed source population.")
    if report_kind == "combined":
        add_chart("Sentiment mix by source", _pdf_chart_platform_sentiment(data), "Compares X/social sentiment with News headline framing; the two measures should not be interpreted as identical constructs.")
    add_chart("Top sources / authors", _pdf_chart_top_sources(data), "For social media this is author volume; for News this is media-source volume.")
    callout(
        "Section takeaway",
        _dominant_sentiment_text(data, news=(report_kind == "news")) + " " + f"Leading sources/authors: {_source_summary_text(data)}.",
        bg="#EEF5FF",
    )

    # 3 Content signals
    story.append(PageBreak())
    story.append(p("3. Content signals and priority items", h1))
    callout(
        "What this section shows",
        "This section surfaces the strongest positive and negative items using deterministic ranking. It is intended to help an analyst decide what to inspect first, not to validate the underlying claims.",
    )
    add_chart("Emotion signals", _pdf_chart_emotions(data), "Confidence-aware multi-cue emotional signals; Other/Neutral means insufficient evidence for a specific emotion.")
    add_chart("Communication intent signals", _pdf_chart_intents(data), "Heuristic communication intent classification: update, support, concern, question, speculation or humor/sarcasm.")

    if report_kind == "social":
        list_defs = [("Top 10 positive posts", "Positive"), ("Top 10 negative posts", "Negative")]
    elif report_kind == "news":
        list_defs = [("Top 10 positive headlines", "Positive"), ("Top 10 negative headlines", "Negative")]
    else:
        list_defs = [("Top 10 positive records", "Positive"), ("Top 10 negative records", "Negative")]

    for label, polarity in list_defs:
        story.append(p(label, h2))
        items = _report_top_items(data, polarity=polarity, limit=10, news=(report_kind == "news"))
        if items.empty:
            story.append(p(f"No {polarity.lower()} records were available.", body))
        else:
            for rank, (_, row) in enumerate(items.iterrows(), start=1):
                source = _report_clean_text(row.get("username", "Unknown"))
                score = float(row.get("sentiment_score", 0) or 0)
                engagement = int(row.get("engagement", 0) or 0)
                story.append(p(f"{rank}. {_report_brief(row.get('text', ''), 260)}", body))
                meta = f"Source: {source} | score {score:+.2f}"
                if row.get("platform") in ["x", "x_manual"]:
                    meta += f" | engagement {engagement}"
                story.append(p(meta, small))

    if report_kind in {"social", "combined"} and not x_data.empty:
        top_eng = x_data.sort_values(["engagement", "likes", "reposts", "replies", "timestamp"], ascending=[False, False, False, False, False]).head(10)
        story.append(p("Top social posts by stored engagement", h2))
        top_rows = [[p("#", table_head), p("User", table_head), p("Eng.", table_head), p("Post", table_head)]]
        for rank, (_, row) in enumerate(top_eng.iterrows(), 1):
            top_rows.append([
                p(str(rank), table_text), p(row.get("username", ""), table_text),
                p(str(int(row.get("engagement", 0) or 0)), table_text),
                p(_report_brief(row.get("text", ""), 220), table_text),
            ])
        add_table(top_rows, [8 * mm, 32 * mm, 18 * mm, 112 * mm])

    callout(
        "Section takeaway",
        "Priority lists are ranked from the available records only. Analysts should open the corresponding evidence PDF/source URL before treating a headline or post as verified context.",
        bg="#EEF5FF",
    )

    # 4 Clusters
    story.append(PageBreak())
    story.append(p("4. Semantic clusters and narrative interpretation", h1))
    callout(
        "How clusters are made",
        "Records are grouped by textual similarity using TF-IDF word/bigram weighting, TruncatedSVD latent semantic reduction (up to 10 dimensions), L2 normalization and KMeans (n_init=10, random_state=42). The cluster count is selected from dataset size up to the configured cap; datasets with fewer than 8 records use a single cluster. Cluster IDs are arbitrary labels, not ranks or severity levels.",
    )
    add_chart("Semantic cluster volume", _pdf_chart_clusters(data), "Shows how many records fall into each text-similarity group.")
    add_chart("Negative share by cluster", _pdf_chart_cluster_negativity(data), "Helps identify which text themes contain a higher share of negative classifications.")
    add_chart("Semantic conversation map", _pdf_chart_semantic_map(data), "Distance is the meaningful signal: nearby points have more similar text. Axis values have no direct real-world unit.")

    if narratives is None or narratives.empty:
        story.append(p("No detailed multi-cluster narrative table was available for this dataset.", body))
    else:
        for _, nrow in narratives.sort_values("cluster").iterrows():
            cid = int(nrow.get("cluster", 0))
            cdf = data[data["semantic_cluster"].eq(cid)].copy()
            cc = _sentiment_counts(cdf)
            sources = _source_summary_text(cdf, limit=4)
            top_terms = _report_clean_text(nrow.get("top_terms", "")) or "No stable terms available"
            rep = _report_clean_text(nrow.get("representative_post", "")) or "No representative record available"
            story.append(p(f"Cluster C{cid}: {_report_clean_text(nrow.get('narrative', ''))}", h2))
            callout(
                "Why these records are together",
                f"Shared high-weight terms/bigrams: {top_terms}. The representative record nearest the cluster centroid is shown below. Leading sources/authors: {sources}.",
                bg="#F8FAFC",
            )
            metrics = [
                [p("Records", table_head), p("Positive", table_head), p("Neutral", table_head), p("Negative", table_head), p("12h growth", table_head), p("Engagement", table_head)],
                [
                    p(str(len(cdf)), table_text),
                    p(f"{int(cc['Positive'])} ({_pct(cc['Positive'], len(cdf)):.1f}%)", table_text),
                    p(f"{int(cc['Neutral'])} ({_pct(cc['Neutral'], len(cdf)):.1f}%)", table_text),
                    p(f"{int(cc['Negative'])} ({_pct(cc['Negative'], len(cdf)):.1f}%)", table_text),
                    p(f"{float(nrow.get('12h_growth_pct', 0) or 0):+.1f}%", table_text),
                    p(str(int(nrow.get('engagement', 0) or 0)), table_text),
                ],
            ]
            add_table(metrics, [22 * mm, 27 * mm, 27 * mm, 27 * mm, 30 * mm, 27 * mm])
            story.append(p("Representative record", h2))
            story.append(p(rep, body))
            samples = cdf.sort_values("timestamp", ascending=False).head(3)
            story.append(p("Recent examples", h2))
            for _, srow in samples.iterrows():
                story.append(p("• " + _report_brief(srow.get("text", ""), 260), small))
            story.append(Spacer(1, 4))

    callout("Section takeaway", _cluster_takeaway_text(data, narratives), bg="#EEF5FF")

    # 5 Overall conclusion
    story.append(PageBreak())
    story.append(p("5. Overall conclusion", h1))
    callout(
        "Conclusion",
        _overall_conclusion_text(data, narratives, alert_threshold, report_kind),
        bg="#EAF2FF",
        border="#9DB7D8",
    )

    if report_kind == "combined" and total:
        story.append(p("Cross-source comparison", h2))
        cross_rows = [[p("Measure", table_head), p("X / social", table_head), p("Web News", table_head)]]
        xs = compute_intelligence_summary(x_data) if len(x_data) else None
        ns = compute_intelligence_summary(news_data) if len(news_data) else None
        for label, key in [("Weighted positive", "weighted_positive_pct"), ("Weighted neutral", "weighted_neutral_pct"), ("Weighted negative", "weighted_negative_pct")]:
            cross_rows.append([
                p(label, table_text),
                p(f"{xs[key]:.1f}%" if xs else "n/a", table_text),
                p(f"{ns[key]:.1f}%" if ns else "n/a", table_text),
            ])
        cross_rows.extend([
            [p("Sentiment index", table_text), p(f"{xs['sentiment_index']:+.1f}/100" if xs else "n/a", table_text), p(f"{ns['sentiment_index']:+.1f}/100" if ns else "n/a", table_text)],
            [p("Assessment confidence", table_text), p(f"{xs['assessment_confidence']}/100" if xs else "n/a", table_text), p(f"{ns['assessment_confidence']}/100" if ns else "n/a", table_text)],
        ])
        add_table(cross_rows, [58 * mm, 56 * mm, 56 * mm])
        story.append(p(
            "Interpretation: X/social sentiment and News headline framing are different signals. A divergence can be analytically useful, but it should not be read as a direct causal relationship.",
            small,
        ))

    story.append(p("Methodology and limitations", h2))
    methodology = [
        ("Sentiment", "Confidence-aware ensemble: VADER + domain/event lexicon + negation, contrast-clause, emoji and multilingual Hindi/roman-Hindi cues. News uses stricter neutral calibration. Each record has a confidence score; News remains headline framing, not public opinion."),
        ("Emotion", "Multi-cue primary/secondary emotion analysis with strength, confidence and emotional intensity. Categories include Anger, Fear/Concern, Joy, Pride, Sadness/Disappointment, Surprise, Trust/Confidence, Anticipation and Relief."),
        ("Engagement", "For X/manual X: likes + reposts + replies from stored values. News engagement is not comparable and is normally zero."),
        ("Clusters", "Word/bigram TF-IDF plus character n-grams -> TruncatedSVD -> normalization -> KMeans. Candidate cluster counts are selected using silhouette quality, with penalties for tiny clusters. Cluster IDs can change as data changes."),
        ("Narrative", "Derived from strongest cluster terms plus a representative record. It is shorthand for analyst navigation, not a verified claim."),
        ("Growth", "12-hour cluster volume compared with the preceding 12 hours; sparse windows can produce zero or volatile percentages."),
        ("Attention index", "Triage index combining confidence-weighted negative pressure, strong negativity, negative-cluster concentration, robust volume anomaly and negative velocity. It is not a probability or validated official risk score."),
        ("Assessment confidence", "Quality indicator combining classifier confidence, sample adequacy, source diversity and recency. A low value means conclusions should be treated cautiously."),
        ("Evidence", "Use the separate Social Media and News evidence ZIPs to trace report items back to stored post/feed metadata and source URLs."),
    ]
    meth_rows = [[p("Method", table_head), p("Interpretation", table_head)]]
    for k, v in methodology:
        meth_rows.append([p(k, table_text), p(v, table_text)])
    add_table(meth_rows, [38 * mm, 132 * mm])

    # Evidence index in source-specific reports only.
    if report_kind == "social":
        evidence_df = _prepare_x_evidence_frame(data)
        story.append(PageBreak())
        story.append(p("Appendix - Social-media evidence index", h1))
        callout("Purpose", "Each row maps to one numbered PDF in the Social Media evidence ZIP.")
        if evidence_df.empty:
            story.append(p("No X/social-media evidence records were available.", body))
        else:
            rows = [[p("#", table_head), p("Time (IST)", table_head), p("User", table_head), p("Sentiment", table_head), p("Eng.", table_head), p("Post excerpt", table_head)]]
            for _, row in evidence_df.iterrows():
                ts = pd.to_datetime(row.get("timestamp"), utc=True).tz_convert(IST).strftime("%Y-%m-%d %H:%M")
                rows.append([
                    p(f"{int(row['_evidence_no']):03d}", table_text), p(ts, table_text),
                    p(row.get("username", ""), table_text), p(row.get("sentiment", ""), table_text),
                    p(str(int(row.get("engagement", 0) or 0)), table_text),
                    p(_report_brief(row.get("text", ""), 150), table_text),
                ])
            add_table(rows, [8*mm, 25*mm, 27*mm, 18*mm, 14*mm, 78*mm])

    if report_kind == "news":
        evidence_df = _prepare_news_evidence_frame(data)
        story.append(PageBreak())
        story.append(p("Appendix - News evidence index", h1))
        callout("Purpose", "Each row maps to one numbered PDF in the News Media evidence ZIP. Feed evidence documents headline/source metadata only and does not reproduce full publisher articles.")
        if evidence_df.empty:
            story.append(p("No News evidence records were available.", body))
        else:
            rows = [[p("#", table_head), p("Time (IST)", table_head), p("Media source", table_head), p("Tone", table_head), p("Headline", table_head)]]
            for _, row in evidence_df.iterrows():
                ts = pd.to_datetime(row.get("timestamp"), utc=True).tz_convert(IST).strftime("%Y-%m-%d %H:%M")
                rows.append([
                    p(f"{int(row['_evidence_no']):03d}", table_text), p(ts, table_text),
                    p(row.get("username", ""), table_text), p(row.get("sentiment", ""), table_text),
                    p(_report_brief(row.get("text", ""), 190), table_text),
                ])
            add_table(rows, [8*mm, 25*mm, 33*mm, 18*mm, 86*mm])

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont(normal_font, 7)
        canvas.setFillColor(colors.HexColor("#6B7280"))
        canvas.drawString(document.leftMargin, 8 * mm, f"{title} - automated intelligence")
        canvas.drawRightString(A4[0] - document.rightMargin, 8 * mm, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    buffer.seek(0)
    return buffer.getvalue()


def build_social_media_report(report_df, alert_threshold=35.0, query="ISRO", run_context=None):
    return build_intelligence_report_pdf(report_df, alert_threshold=alert_threshold, report_kind="social", query=query, run_context=run_context)


def build_news_media_report(report_df, alert_threshold=35.0, query="ISRO", run_context=None):
    return build_intelligence_report_pdf(report_df, alert_threshold=alert_threshold, report_kind="news", query=query, run_context=run_context)


def build_combined_intelligence_report(report_df, alert_threshold=35.0, query="ISRO", run_context=None):
    return build_intelligence_report_pdf(report_df, alert_threshold=alert_threshold, report_kind="combined", query=query, run_context=run_context)


# Compatibility: legacy callers requesting build_pdf_report receive the new combined report.
def build_pdf_report(report_df, narrative_df=None, trend_df=None, alert_threshold=35.0):
    return build_combined_intelligence_report(report_df, alert_threshold=alert_threshold, query="ISRO")


if os.environ.get("SMI_BACKGROUND_IMPORT") != "1":
    # =========================================================
    # DATA SOURCE UI
    # =========================================================
    with st.sidebar:
        st.header("Data source")

        source_mode = st.radio(
            "Choose source",
            [
                "CSV / manual X posts",
                "Free web/news",
                "Combine CSV + web/news",
                "Supabase database",
                "Demo sample",
            ],
        )

        st.divider()
        st.header("Hourly monitoring")
        st.success(
            "Primary scheduler in Build 5.13: GitHub Actions background worker. "
            "It does not depend on Chrome staying active."
        )
        auto_monitor = st.toggle(
            "Browser fallback auto-refresh",
            value=False,
            help=(
                "Optional fallback only. The browser-based timer stops when the "
                "session sleeps; use the included GitHub Actions workflow for "
                "reliable background execution."
            ),
        )
        alert_threshold = st.slider(
            "Negativity alarm threshold (%)",
            min_value=10,
            max_value=90,
            value=35,
            step=5,
        )
        sound_alerts = st.toggle(
            "Enable sound alarm",
            value=True,
        )

        st.divider()
        st.header("Automatic PDF")
        auto_pdf = st.toggle(
            "Auto-generate PDF every cycle",
            value=True,
            help=(
                "Generates a fresh PDF automatically when the dataset changes "
                "or the hourly monitoring cycle advances."
            ),
        )
        auto_pdf_download = st.toggle(
            "Attempt automatic browser download",
            value=True,
            help=(
                "The app will attempt to start the PDF download automatically. "
                "Some mobile/desktop browsers block downloads that were not "
                "started by a user tap."
            ),
        )
        st.caption(
            "Automatic PDF generation is reliable. Automatic download to the "
            "device depends on browser security settings."
        )

        evidence_packs_enabled = st.toggle(
            "Create Social + News evidence ZIPs",
            value=True,
            help=(
                "Creates one numbered evidence PDF per X/social post and one "
                "per Web News item, plus manifest.csv in each ZIP."
            ),
        )
        # Backward-compatible alias used by older UI state code.
        news_evidence_zip_enabled = evidence_packs_enabled
        st.caption(
            "Social evidence preserves stored post text/metrics. News evidence "
            "documents feed headline/source metadata and does not reproduce full articles."
        )

        st.divider()
        st.header("Automatic email")

        email_cfg = get_email_config()

        routine_email_enabled = st.toggle(
            "In-app email fallback every hour",
            value=False,
            help=(
                "Optional browser-session fallback. The GitHub Actions worker "
                "is the recommended hourly email scheduler."
            ),
        )

        alert_email_enabled = st.toggle(
            "In-app alert email fallback",
            value=False,
            help=(
                "Sends an alert PDF only when negativity crosses upward "
                "through the configured threshold."
            ),
        )

        st.caption(
            f"Hourly recipients: {len(email_cfg['routine_recipients'])} · "
            f"Alert-only recipients: {len(email_cfg['alert_recipients'])}"
        )
        st.caption(
            "Build 5.13 keeps these in-app controls only as fallback. "
            "Background emails are sent by GitHub Actions using GitHub Secrets."
        )

        if not smtp_configured():
            st.warning(
                "Email sender is not configured. Add SMTP_EMAIL and "
                "SMTP_APP_PASSWORD in Streamlit Secrets."
            )

        if routine_email_enabled and not email_cfg["routine_recipients"]:
            st.warning(
                "REPORT_RECIPIENTS_30MIN is empty."
            )

        if alert_email_enabled and not email_cfg["alert_recipients"]:
            st.warning(
                "ALERT_RECIPIENTS is empty."
            )

        if st.button("Test alarm sound", key="test_alarm_v57"):
            _play_alarm_sound()
            st.caption("Alarm test sent to the browser.")

        # Build 5.11 uses a short heartbeat but schedules work by fixed
        # India Standard Time hourly slots (:00). This avoids the
        # drift caused by scheduling from the moment the browser session opened.
        scheduler_now_ist = datetime.now(IST)
        monitor_slot_start = scheduler_now_ist.replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        monitor_slot_end = monitor_slot_start + timedelta(hours=1)
        monitor_slot_key = monitor_slot_start.strftime("%Y%m%d_%H%M_IST")

        if auto_monitor:
            refresh_tick = st_autorefresh(
                interval=60 * 1000,
                limit=None,
                key="monitor_heartbeat_v511",
            )
            st.caption(
                "Scheduler heartbeat: every 60 seconds · "
                f"current slot {monitor_slot_start.strftime('%H:%M')}–"
                f"{monitor_slot_end.strftime('%H:%M')} IST · "
                f"next scheduled slot {monitor_slot_end.strftime('%H:%M')} IST."
            )
        else:
            refresh_tick = 0

        st.caption(
            "The 60-second heartbeat improves timing and retry behavior while the "
            "app is awake. Streamlit Community Cloud can still sleep an inactive "
            "app, so this in-app scheduler is not a guaranteed 24/7 background job."
        )


    csv_df = pd.DataFrame(columns=STANDARD_COLUMNS)
    news_df = st.session_state.get(
        "gdelt_posts",
        pd.DataFrame(columns=STANDARD_COLUMNS)
    )


    # ---------- CSV controls ----------
    if source_mode in [
        "CSV / manual X posts",
        "Combine CSV + web/news",
    ]:
        st.subheader("Option A — X / CSV")
        st.caption(
            "Build 5.4 uses an explicit Load & analyze step and stores the "
            "validated CSV in session state, so mobile browser reruns cannot lose it."
        )

        st.download_button(
            "Download CSV template",
            data=CSV_TEMPLATE,
            file_name="isro_x_template.csv",
            mime="text/csv",
        )

        uploaded = st.file_uploader(
            "Choose X CSV file",
            type=["csv"],
            key="x_csv_upload_v54",
        )

        c_load, c_test, c_clear = st.columns(3)

        with c_load:
            load_clicked = st.button(
                "Load & analyze CSV",
                type="primary",
                key="load_csv_v54",
                disabled=(uploaded is None),
            )

        with c_test:
            test_clicked = st.button(
                "Test built-in CSV",
                key="test_csv_v54",
            )

        with c_clear:
            clear_clicked = st.button(
                "Clear CSV",
                key="clear_csv_v54",
            )

        if clear_clicked:
            st.session_state.pop("active_csv_df_v54", None)
            st.session_state.pop("active_csv_name_v54", None)
            st.session_state.pop("active_csv_error_v54", None)

        if test_clicked:
            try:
                test_raw = pd.read_csv(io.StringIO(BUILTIN_ISRO_SAMPLE))
                test_norm = normalize_posts(test_raw)
                st.session_state["active_csv_df_v54"] = test_norm
                st.session_state["active_csv_name_v54"] = "built-in ISRO test CSV"
                st.session_state.pop("active_csv_error_v54", None)
            except Exception as exc:
                st.session_state["active_csv_error_v54"] = (
                    f"{type(exc).__name__}: {exc}"
                )

        if load_clicked and uploaded is not None:
            try:
                upload_bytes = uploaded.getvalue()
                if not upload_bytes:
                    raise ValueError("Uploaded file is empty.")

                # utf-8-sig handles both normal UTF-8 and BOM CSV exports.
                incoming = pd.read_csv(
                    io.BytesIO(upload_bytes),
                    encoding="utf-8-sig",
                )

                required = {"timestamp", "username", "text"}
                incoming_cols = {
                    str(c).strip().lower()
                    for c in incoming.columns
                }
                missing = required - incoming_cols
                if missing:
                    raise ValueError(
                        "Missing required column(s): "
                        + ", ".join(sorted(missing))
                    )

                normalized = normalize_posts(incoming)

                if normalized.empty:
                    raise ValueError(
                        "The CSV was read but no valid rows remained after validation."
                    )

                st.session_state["active_csv_df_v54"] = normalized
                st.session_state["active_csv_name_v54"] = uploaded.name
                st.session_state.pop("active_csv_error_v54", None)

            except Exception as exc:
                st.session_state["active_csv_error_v54"] = (
                    f"{type(exc).__name__}: {exc}"
                )

        if st.session_state.get("active_csv_error_v54"):
            st.error(
                "CSV load failed: "
                + st.session_state["active_csv_error_v54"]
            )

        csv_df = st.session_state.get(
            "active_csv_df_v54",
            pd.DataFrame(columns=STANDARD_COLUMNS),
        )

        if len(csv_df):
            csv_name = st.session_state.get(
                "active_csv_name_v54",
                "CSV dataset",
            )

            st.success(
                f"✅ ACTIVE X/CSV DATASET: {csv_name} · "
                f"{len(csv_df):,} valid records"
            )

            d1, d2, d3, d4 = st.columns(4)
            d1.metric("Rows", f"{len(csv_df):,}")
            d2.metric("Likes", f"{int(csv_df['likes'].sum()):,}")
            d3.metric("Reposts", f"{int(csv_df['reposts'].sum()):,}")
            d4.metric("Replies", f"{int(csv_df['replies'].sum()):,}")

            st.dataframe(
                csv_df[
                    [
                        "timestamp",
                        "username",
                        "text",
                        "likes",
                        "reposts",
                        "replies",
                        "platform",
                    ]
                ].head(12),
                use_container_width=True,
                hide_index=True,
            )

            st.info(
                "This CSV is now locked into the app session and will feed "
                "the intelligence dashboard below until you tap Clear CSV."
            )
        else:
            st.info(
                "Choose a file and tap **Load & analyze CSV**, "
                "or tap **Test built-in CSV** to verify the CSV pipeline."
            )


    # ---------- GDELT controls ----------
    if source_mode in [
        "Free web/news",
        "Combine CSV + web/news",
    ]:
        st.subheader("Option B — Free web/news")
        st.caption("Uses GDELT first and automatically falls back to Google News RSS if rate-limited.")

        query = st.text_input(
            "News query",
            value='(ISRO OR "Indian Space Research Organisation")',
        )

        period_label = st.selectbox(
            "Look-back period",
            ["24 hours", "7 days", "1 month"],
            index=0,
        )

        timespan_map = {
            "24 hours": "24h",
            "7 days": "1week",
            "1 month": "1month",
        }

        max_records = st.slider(
            "News items",
            min_value=10,
            max_value=100,
            value=50,
            step=10,
        )

        manual_news_fetch = st.button(
            "Fetch free news",
            type="primary"
        )

        now_epoch = datetime.now(IST).timestamp()
        last_news_fetch = float(
            st.session_state.get("last_news_fetch_epoch_v57", 0.0)
        )
        last_news_slot = st.session_state.get(
            "last_news_fetch_slot_v511", ""
        )
        last_news_attempt = float(
            st.session_state.get("last_news_attempt_epoch_v511", 0.0)
        )
        news_retry_ready = (now_epoch - last_news_attempt) >= 5 * 60
        auto_news_due = (
            auto_monitor
            and last_news_slot != monitor_slot_key
            and news_retry_ready
        )

        if manual_news_fetch or auto_news_due:
            st.session_state["last_news_attempt_epoch_v511"] = now_epoch
            try:
                fetched, provider = fetch_free_news(
                    query=query,
                    timespan=timespan_map[period_label],
                    max_records=max_records,
                )
                st.session_state["gdelt_posts"] = fetched
                st.session_state["news_provider"] = provider
                st.session_state["last_news_fetch_epoch_v57"] = now_epoch
                st.session_state["last_news_fetch_iso_v57"] = (
                    datetime.now(IST).isoformat()
                )
                st.session_state["last_news_fetch_slot_v511"] = monitor_slot_key
                st.session_state["last_news_fetch_error_v511"] = ""
                news_df = fetched
                if manual_news_fetch:
                    st.success(
                        f"Fetched {len(fetched):,} news items via {provider}."
                    )
                else:
                    st.success(
                        f"Hourly auto refresh fetched {len(fetched):,} "
                        f"news items via {provider}."
                    )
            except Exception as exc:
                # Do not mark the half-hour slot as successfully refreshed.
                # The 60-second heartbeat will retry after the 5-minute cooldown.
                st.session_state["last_news_fetch_error_v511"] = (
                    f"{type(exc).__name__}: {exc}"
                )
                st.error(
                    "News refresh failed; cached news will be used for this run. "
                    "Automatic retry is scheduled in about 5 minutes. "
                    f"Technical detail: {type(exc).__name__}: {exc}"
                )

        news_df = st.session_state.get(
            "gdelt_posts",
            pd.DataFrame(columns=STANDARD_COLUMNS)
        )

        if len(news_df):
            provider = st.session_state.get("news_provider", "Free news source")
            last_iso = st.session_state.get("last_news_fetch_iso_v57", "")
            refresh_note = f" · refreshed {last_iso}" if last_iso else ""
            st.info(
                f"Current news dataset: {len(news_df):,} items · "
                f"Source: {provider}{refresh_note}"
            )


    # ---------- Build raw dataset ----------
    if source_mode == "CSV / manual X posts":
        raw_df = csv_df

    elif source_mode == "Free web/news":
        raw_df = news_df

    elif source_mode == "Combine CSV + web/news":
        raw_df = normalize_posts(
            pd.concat(
                [csv_df, news_df],
                ignore_index=True
            )
        )

    elif source_mode == "Supabase database":
        if not supabase_enabled():
            st.warning(
                "Supabase secrets are not configured."
            )
            raw_df = pd.DataFrame(
                columns=STANDARD_COLUMNS
            )
        else:
            try:
                raw_df = load_supabase_posts()
                st.success(
                    f"Loaded {len(raw_df):,} stored records."
                )
            except Exception as exc:
                st.error(
                    f"Could not load Supabase: {exc}"
                )
                raw_df = pd.DataFrame(
                    columns=STANDARD_COLUMNS
                )

    else:
        raw_df = make_sample_data()


    # ---------- Optional persistence ----------
    if (
        len(raw_df)
        and supabase_enabled()
        and source_mode != "Supabase database"
    ):
        with st.sidebar:
            if st.button(
                "Save current data to Supabase"
            ):
                try:
                    count = save_posts_to_supabase(
                        raw_df
                    )
                    st.success(
                        f"Sent {count:,} records to Supabase."
                    )
                except Exception as exc:
                    st.error(
                        f"Save failed: {exc}"
                    )


    if raw_df.empty:
        if source_mode == "CSV / manual X posts":
            st.info(
                "No active CSV yet. Choose a file and tap "
                "**Load & analyze CSV**, or use **Test built-in CSV**."
            )
        elif source_mode == "Free web/news":
            st.info(
                "Tap **Fetch free news** to begin."
            )
        elif source_mode == "Combine CSV + web/news":
            st.info(
                "Upload a CSV and/or tap **Fetch free news**."
            )
        else:
            st.info(
                "No records are available from this source."
            )
        st.stop()



    st.caption(
        f"Runtime check: source mode = {source_mode} · "
        f"raw rows = {len(raw_df):,}"
    )

    # =========================================================
    # LOADED SOURCE DIAGNOSTICS
    # =========================================================
    st.divider()
    st.subheader("Loaded data")

    source_counts = (
        raw_df["platform"]
        .value_counts()
        .rename_axis("source")
        .reset_index(name="records")
    )

    st.dataframe(
        source_counts,
        use_container_width=True,
        hide_index=True,
    )

    st.success(
        f"✅ {len(raw_df):,} total records are ready for intelligence analysis."
    )

    # =========================================================
    # RUN INTELLIGENCE PIPELINE
    # =========================================================
    try:
        with st.spinner("Analyzing uploaded/loaded records..."):
            df = run_sentiment_agent(raw_df)
            df = run_emotion_agent(df)
            df = run_intent_agent(df)
            df["engagement"] = (
                df["likes"] + df["reposts"] + df["replies"]
            )

            df, vectorizer, tfidf, feature_names = (
                run_semantic_topic_agent(df)
            )

            narratives_df = run_narrative_agent(
                df,
                vectorizer,
                tfidf,
                feature_names,
            )

            trend_df = run_trend_agent(df)

        st.success(
            f"✅ Intelligence analysis complete for {len(df):,} loaded records."
        )

        overall_intel = compute_intelligence_summary(df, narratives_df, trend_df)
        overall_negative_pct = float(overall_intel["weighted_negative_pct"])
        snapshot_df = _record_monitor_snapshot(overall_negative_pct)

        alert_latched = bool(
            st.session_state.get("negativity_alert_latched_v57", False)
        )

        if overall_negative_pct > alert_threshold:
            st.error(
                f"🚨 NEGATIVITY ALERT: {overall_negative_pct:.1f}% confidence-weighted negative, "
                f"above the {alert_threshold:.0f}% threshold."
            )
            if not alert_latched:
                if sound_alerts:
                    _play_alarm_sound()

                st.session_state["negativity_alert_latched_v57"] = True

                # One unique alert event per upward threshold crossing.
                alert_event_id = (
                    datetime.now(IST).strftime("%Y%m%d_%H%M%S_IST")
                    + f"|neg={overall_negative_pct:.2f}"
                    + f"|threshold={alert_threshold:.2f}"
                )
                st.session_state["pending_alert_email_event_v59"] = (
                    alert_event_id
                )
                st.session_state["pending_alert_negative_pct_v59"] = (
                    float(overall_negative_pct)
                )
        else:
            st.session_state["negativity_alert_latched_v57"] = False

    except Exception as exc:
        st.error(
            "The CSV loaded, but the intelligence pipeline failed. "
            f"Technical detail: {type(exc).__name__}: {exc}"
        )
        st.stop()


    # =========================================================
    # FILTERS
    # =========================================================
    with st.sidebar:
        st.divider()
        st.header("Analysis filters")

        platforms = sorted(
            df["platform"].unique().tolist()
        )
        selected_platforms = st.multiselect(
            "Source/platform",
            platforms,
            default=platforms,
        )

        sentiments = [
            "Positive", "Neutral", "Negative"
        ]
        selected_sentiments = st.multiselect(
            "Sentiment",
            sentiments,
            default=sentiments,
        )

        emotions = sorted(
            df["emotion"].unique().tolist()
        )
        selected_emotions = st.multiselect(
            "Emotion",
            emotions,
            default=emotions,
        )

        clusters = sorted(
            df["semantic_cluster"].unique().tolist()
        )
        selected_clusters = st.multiselect(
            "Semantic clusters",
            clusters,
            default=clusters,
        )

        search = st.text_input(
            "Search within loaded data",
            placeholder="launch, satellite, mission...",
        )


    filtered = df[
        df["platform"].isin(selected_platforms)
        & df["sentiment"].isin(selected_sentiments)
        & df["emotion"].isin(selected_emotions)
        & df["semantic_cluster"].isin(
            selected_clusters
        )
    ].copy()

    if search:
        filtered = filtered[
            filtered["text"].str.contains(
                re.escape(search),
                case=False,
                na=False,
            )
            | filtered["hashtags"].str.contains(
                re.escape(search),
                case=False,
                na=False,
            )
        ]


    filtered_trend = run_trend_agent(filtered)

    filtered_narratives = narratives_df[
        narratives_df["cluster"].isin(
            selected_clusters
        )
    ].copy()

    risk_data = run_risk_agent(
        filtered,
        filtered_trend,
        filtered_narratives,
    )


    # =========================================================
    # DASHBOARD
    # =========================================================
    c1, c2, c3, c4, c5, c6 = st.columns(6)

    c1.metric("Records", f"{len(filtered):,}")
    c2.metric(
        "Engagement",
        f"{int(filtered['engagement'].sum()):,}",
    )
    c3.metric(
        "Weighted negative",
        f"{risk_data['negative_pct']:.1f}%",
    )
    c4.metric(
        "12h growth",
        f"{risk_data['recent_growth_pct']:+.0f}%",
    )
    c5.metric(
        "Narratives",
        len(filtered_narratives),
    )
    c6.metric(
        "Attention index",
        f"{risk_data['risk']}/100",
    )
    st.caption(
        f"Assessment confidence: {risk_data['assessment_confidence']}/100 · "
        f"Sentiment index: {risk_data['sentiment_index']:+.1f}/100 · "
        f"Dominant expressive emotion: {risk_data['dominant_emotion']} · "
        f"Emotional intensity: {risk_data['emotional_intensity_pct']:.1f}%"
    )


    if (
        len(filtered)
        and filtered["platform"]
        .eq("web_news")
        .all()
    ):
        st.info(
            "News mode: sentiment/emotion are calculated from "
            "article headlines, so treat them as headline-framing "
            "signals rather than public-opinion measurements."
        )


    left, right = st.columns([2, 1])

    with left:
        st.subheader("Conversation / coverage trend")
        fig = px.line(
            filtered_trend,
            x="hour",
            y=["posts", "rolling_avg"],
            markers=True,
        )
        fig.update_layout(
            xaxis_title=None,
            yaxis_title="Records",
            legend_title_text="",
        )
        st.plotly_chart(
            fig,
            use_container_width=True,
        )

    with right:
        st.subheader("Sentiment")
        sent = (
            filtered["sentiment"]
            .value_counts()
            .reindex(
                ["Positive", "Neutral", "Negative"],
                fill_value=0,
            )
            .reset_index()
        )
        sent.columns = ["sentiment", "records"]

        st.plotly_chart(
            px.pie(
                sent,
                names="sentiment",
                values="records",
                hole=0.55,
                color="sentiment",
                color_discrete_map=SENTIMENT_COLORS,
                category_orders={
                    "sentiment": ["Positive", "Neutral", "Negative"]
                },
            ),
            use_container_width=True,
        )




    # =========================================================
    # EXTENDED MONITORING CHARTS
    # =========================================================
    st.subheader("📈 Extended monitoring charts")

    # Persistent browser-independent history written by GitHub Actions.
    background_runs = pd.DataFrame()
    if supabase_enabled():
        try:
            background_runs = load_background_monitoring_runs(limit=336)
        except Exception as exc:
            st.caption(
                "Background monitoring history is not available yet: "
                f"{type(exc).__name__}: {exc}"
            )

    if len(background_runs):
        last_bg = background_runs.iloc[-1]
        bg_time = last_bg["run_timestamp"].strftime(
            "%Y-%m-%d %H:%M:%S IST"
        )
        st.success(
            "Background scheduler last completed: "
            f"{bg_time} · negativity {float(last_bg['negative_pct']):.1f}% · "
            f"routine emails {int(last_bg.get('routine_email_sent', 0) or 0)}"
        )

        bg_fig = px.line(
            background_runs,
            x="run_timestamp",
            y="negative_pct",
            markers=True,
            title="Browser-independent hourly negativity history",
        )
        bg_fig.update_traces(
            line_color=NEGATIVE_RED,
            marker_color=NEGATIVE_RED,
        )
        bg_fig.add_hline(
            y=alert_threshold,
            line_dash="dash",
            line_color=NEUTRAL_GREY,
            annotation_text=f"Alarm threshold {alert_threshold:.0f}%",
        )
        bg_fig.update_yaxes(title="Negative share (%)", range=[0, 100])
        bg_fig.update_xaxes(title=None)
        st.plotly_chart(bg_fig, use_container_width=True)
    else:
        st.caption(
            "No GitHub background-run history found yet. Run "
            "supabase_build513.sql once, add GitHub Actions secrets, and enable "
            "the included workflow."
        )

    neg_history = compute_negativity_history(df)
    if len(neg_history):
        neg_fig = px.line(
            neg_history,
            x="bucket",
            y="negative_pct",
            color="series",
            line_dash="series",
            markers=True,
            color_discrete_sequence=[NEGATIVE_RED],
            hover_data={"records": True, "negative_pct": ":.1f"},
            title="Historic negativity - hourly buckets (IST)",
        )
        neg_fig.add_hline(
            y=alert_threshold,
            line_dash="dash",
            line_color=NEUTRAL_GREY,
            annotation_text=f"Alarm threshold {alert_threshold:.0f}%",
        )
        neg_fig.update_yaxes(
            title="Negative share (%)",
            range=[0, 100],
        )
        neg_fig.update_xaxes(title=None)
        st.plotly_chart(neg_fig, use_container_width=True)

    if len(snapshot_df) >= 2:
        snap_plot = snapshot_df.copy()
        snap_plot["bucket"] = pd.to_datetime(
            snap_plot["bucket"], errors="coerce", utc=True
        )
        snap_fig = px.line(
            snap_plot,
            x="bucket",
            y="negative_pct",
            markers=True,
            title="Live hourly monitoring snapshots (this app session)",
        )
        snap_fig.update_traces(
            line_color=NEGATIVE_RED,
            marker_color=NEGATIVE_RED,
        )
        snap_fig.add_hline(
            y=alert_threshold,
            line_dash="dash",
            line_color=NEUTRAL_GREY,
            annotation_text=f"Alarm threshold {alert_threshold:.0f}%",
        )
        snap_fig.update_yaxes(title="Negative share (%)", range=[0, 100])
        snap_fig.update_xaxes(title=None)
        st.plotly_chart(snap_fig, use_container_width=True)

    source_mix_df = filtered.copy()
    source_mix_df["source_group"] = np.where(
        source_mix_df["platform"].isin(["x_manual", "x"]),
        "X",
        np.where(
            source_mix_df["platform"].eq("web_news"),
            "News",
            source_mix_df["platform"],
        ),
    )
    source_mix = (
        source_mix_df.groupby(["source_group", "sentiment"])
        .size()
        .reset_index(name="records")
    )

    chart_left, chart_right = st.columns(2)
    with chart_left:
        st.markdown("**Sentiment mix by source**")
        if len(source_mix):
            st.plotly_chart(
                px.bar(
                    source_mix,
                    x="source_group",
                    y="records",
                    color="sentiment",
                    barmode="stack",
                    color_discrete_map=SENTIMENT_COLORS,
                    category_orders={
                        "sentiment": ["Positive", "Neutral", "Negative"]
                    },
                ),
                use_container_width=True,
            )

    with chart_right:
        st.markdown("**Semantic cluster volume**")
        cluster_volume = (
            filtered.groupby("semantic_cluster")
            .size()
            .reset_index(name="records")
        )
        if len(cluster_volume):
            cluster_volume["cluster"] = (
                "Cluster " + cluster_volume["semantic_cluster"].astype(str)
            )
            st.plotly_chart(
                px.bar(
                    cluster_volume,
                    x="cluster",
                    y="records",
                ),
                use_container_width=True,
            )

    chart_left, chart_right = st.columns(2)
    with chart_left:
        st.markdown("**Negative share by cluster**")
        cluster_neg = (
            filtered.groupby("semantic_cluster")["sentiment"]
            .apply(lambda s: s.eq("Negative").mean() * 100)
            .reset_index(name="negative_pct")
        )
        if len(cluster_neg):
            cluster_neg["cluster"] = (
                "Cluster " + cluster_neg["semantic_cluster"].astype(str)
            )
            cluster_neg_fig = px.bar(
                cluster_neg,
                x="cluster",
                y="negative_pct",
            )
            cluster_neg_fig.update_traces(marker_color=NEGATIVE_RED)
            cluster_neg_fig.update_yaxes(
                title="Negative share (%)",
                range=[0, 100],
            )
            st.plotly_chart(
                cluster_neg_fig,
                use_container_width=True,
            )

    with chart_right:
        st.markdown("**Top sources / authors by record volume**")
        source_volume = (
            filtered.groupby(["platform", "username"])
            .size()
            .reset_index(name="records")
            .sort_values("records", ascending=False)
            .head(12)
            .sort_values("records")
        )
        if len(source_volume):
            st.plotly_chart(
                px.bar(
                    source_volume,
                    x="records",
                    y="username",
                    orientation="h",
                    hover_data=["platform"],
                ),
                use_container_width=True,
            )

    st.caption(
        "The historic negativity chart uses the timestamps inside the loaded "
        "records. The GitHub background chart persists independent hourly "
        "runs in Supabase even when the browser is closed; the live snapshot chart "
        "is retained only as an in-session diagnostic."
    )


    # =========================================================
    # TOP 10 POSITIVE / NEGATIVE SUMMARIES
    # =========================================================
    st.subheader("📝 Top 10 positive & negative briefs")
    st.caption(
        "Brief local summaries of the strongest sentiment items. "
        "X rankings use sentiment + engagement; news rankings use "
        "sentiment + recency. No paid LLM is required."
    )

    x_brief_df = filtered[
        filtered["platform"].isin(["x_manual", "x"])
    ].copy()

    news_brief_df = filtered[
        filtered["platform"].eq("web_news")
    ].copy()

    x_tab, news_tab = st.tabs(["X", "News"])

    with x_tab:
        if x_brief_df.empty:
            st.info(
                "No X/manual-X records are currently loaded. "
                "Upload an X CSV or use Combined mode."
            )
        else:
            positive_tab, negative_tab = st.tabs(
                ["🔵 Top positive", "🔴 Top negative"]
            )

            with positive_tab:
                render_brief_list(
                    x_brief_df,
                    polarity="positive",
                    news=False,
                )

            with negative_tab:
                render_brief_list(
                    x_brief_df,
                    polarity="negative",
                    news=False,
                )

    with news_tab:
        if news_brief_df.empty:
            st.info(
                "No news records are currently loaded. "
                "Fetch free news or use Combined mode."
            )
        else:
            positive_tab, negative_tab = st.tabs(
                ["🔵 Top positive", "🔴 Top negative"]
            )

            with positive_tab:
                render_brief_list(
                    news_brief_df,
                    polarity="positive",
                    news=True,
                )

            with negative_tab:
                render_brief_list(
                    news_brief_df,
                    polarity="negative",
                    news=True,
                )

    st.divider()

    st.subheader("🧠 Narrative intelligence")

    if len(filtered_narratives):
        view = filtered_narratives[
            [
                "narrative",
                "posts",
                "12h_growth_pct",
                "negative_pct",
                "engagement",
                "top_terms",
            ]
        ].copy()

        view.columns = [
            "Narrative",
            "Records",
            "12h growth %",
            "Negative %",
            "Engagement",
            "Key terms",
        ]

        st.dataframe(
            view,
            use_container_width=True,
            hide_index=True,
        )

        chosen = st.selectbox(
            "Inspect a narrative",
            filtered_narratives[
                "narrative"
            ].tolist(),
        )

        row = filtered_narratives[
            filtered_narratives["narrative"]
            == chosen
        ].iloc[0]

        st.markdown(
            f"**Representative item:** "
            f"{row['representative_post']}"
        )
    else:
        st.info(
            "More records or more textual variation may be "
            "needed for narrative clustering."
        )


    st.subheader("Semantic conversation map")

    if len(filtered):
        plot_df = filtered.copy()
        plot_df["cluster_label"] = (
            "Cluster "
            + plot_df["semantic_cluster"].astype(str)
        )

        fig = px.scatter(
            plot_df,
            x="semantic_x",
            y="semantic_y",
            color="cluster_label",
            hover_data=[
                "platform",
                "username",
                "sentiment",
                "emotion",
                "text",
            ],
        )

        fig.update_layout(
            xaxis_title="Semantic dimension 1",
            yaxis_title="Semantic dimension 2",
            legend_title_text="",
        )

        st.plotly_chart(
            fig,
            use_container_width=True,
        )


    left, right = st.columns(2)

    with left:
        st.subheader("Emotion intelligence")
        emotions = (
            filtered["emotion"]
            .value_counts()
            .reset_index()
        )
        emotions.columns = ["emotion", "records"]

        st.plotly_chart(
            px.bar(
                emotions,
                x="emotion",
                y="records",
            ),
            use_container_width=True,
        )

    with right:
        st.subheader("Top sources / authors")
        sources = (
            filtered.groupby(
                ["platform", "username"]
            )
            .agg(
                records=("text", "size"),
                engagement=("engagement", "sum"),
            )
            .sort_values(
                ["records", "engagement"],
                ascending=False,
            )
            .head(15)
            .reset_index()
        )

        st.dataframe(
            sources,
            use_container_width=True,
            hide_index=True,
        )



    # =========================================================
    # LEGEND / METHODOLOGY
    # =========================================================
    with st.expander("📚 Legend & methodology — what the clusters, sentiment and scores mean"):
        st.markdown(
            """
            **Sentiment**
            - Ensemble score combines **VADER + domain/event lexicon + negation + contrast + emoji + Hindi/roman-Hindi cues**.
            - Each item gets a **sentiment confidence** score; low-confidence items have less influence on overall weighted percentages.
            - News uses a stricter neutral calibration and represents **headline framing**, not public opinion.
            - Sarcasm is flagged as uncertainty rather than automatically inverted.

            **Emotion**
            - Multi-cue primary/secondary emotion model: Anger, Fear/Concern, Joy, Pride,
              Sadness/Disappointment, Surprise, Trust/Confidence, Anticipation and Relief.
            - Emotion strength, confidence and overall emotional intensity are calculated.
            - **Other/Neutral** means evidence was insufficient for a specific emotional label.

            **Semantic cluster**
            - An automatically discovered group of textually similar records.
            - Cluster numbers such as **0, 1, 2... are arbitrary IDs**, not ranks.
            - A cluster color does **not** mean positive/negative or high/low risk.
            - On the semantic map, points closer together tend to discuss
              more similar themes.
            - The X/Y axes are latent text dimensions and have no direct
              real-world unit.

            **Narrative**
            - A shorthand label derived from a cluster's strongest key terms
              and representative item. It should be treated as an analytical
              summary, not as a verified factual claim.

            **Engagement**
            - X/manual X = **likes + reposts + replies**.
            - Free News feeds do not provide equivalent social engagement.

            **12h growth**
            - Compares the latest 12-hour record count with the previous
              12-hour period.

            **Trend anomaly**
            - Uses a continuous hourly series, rolling **12-hour median** baseline and robust MAD z-score.
            - An anomaly requires strong deviation and a minimum absolute volume, reducing false spikes from sparse data.

            **Attention index**
            - Combines confidence-weighted negative pressure, strong negativity, negative-cluster concentration, robust anomalies and negative velocity.
            - It is **not** a probability or official risk rating.

            **Assessment confidence**
            - Combines item-level classifier confidence, sample size, source diversity and recency.
            - Low confidence means the interpretation should be treated more cautiously.

            **Top-10 sentiment briefs**
            - X: ranked by sentiment strength, then engagement and recency.
            - News: ranked by headline sentiment strength, then recency.

            **Hourly monitoring**
            - When enabled, the active Streamlit session runs one monitoring cycle per hour.
            - News is automatically fetched again; Supabase is reloaded; CSV data is re-analysed.
            - Free Streamlit hosting may sleep when nobody is using the app, so this is not a guaranteed always-on scheduler.

            **Negativity alarm**
            - Overall negativity is calculated across all currently loaded records.
            - A sound alert is triggered when negativity crosses above the configured threshold (default **35%**).
            - It sounds once per crossing and re-arms only after negativity falls back to or below the threshold.
            - Mobile browser autoplay rules may require you to interact with the page first.
            """
        )

        if len(filtered_narratives):
            st.markdown("**Current cluster legend**")
            legend_table = filtered_narratives[
                [
                    "cluster",
                    "narrative",
                    "posts",
                    "negative_pct",
                    "12h_growth_pct",
                    "top_terms",
                ]
            ].copy()
            legend_table.columns = [
                "Cluster",
                "Narrative",
                "Records",
                "Negative %",
                "12h growth %",
                "Key terms",
            ]
            st.dataframe(
                legend_table.sort_values("Cluster"),
                use_container_width=True,
                hide_index=True,
            )




    def _attempt_browser_pdf_download(pdf_bytes, filename):
        """
        Attempt a zero-click browser download.
        Browser security may block this, especially on mobile.
        """
        encoded = base64.b64encode(pdf_bytes).decode("ascii")
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename)
        components.html(
            f"""
            <script>
            (() => {{
                try {{
                    const b64 = "{encoded}";
                    const binary = atob(b64);
                    const bytes = new Uint8Array(binary.length);
                    for (let i = 0; i < binary.length; i++) {{
                        bytes[i] = binary.charCodeAt(i);
                    }}
                    const blob = new Blob([bytes], {{type: "application/pdf"}});
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = "{safe_name}";
                    a.style.display = "none";
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                    setTimeout(() => URL.revokeObjectURL(url), 5000);
                }} catch (e) {{
                    console.log("Automatic PDF download was blocked:", e);
                }}
            }})();
            </script>
            """,
            height=0,
        )


    # =========================================================
    # BUILD 5.14 REPORT PACK: 3 PDFs + 2 EVIDENCE ZIPs
    # =========================================================
    st.subheader("📄 Intelligence report pack")
    st.caption(
        "Build 5.17 generates three cleaner reports: Social Media Intelligence, "
        "News Media Intelligence, and Combined Intelligence. Each analytical "
        "section includes a purpose statement and a section takeaway; cluster "
        "profiles explain why records were grouped. All timestamps are IST."
    )

    report_fingerprint = (
        f"{len(df)}|{df['timestamp'].min() if len(df) else ''}|"
        f"{df['timestamp'].max() if len(df) else ''}|{source_mode}|"
        f"{alert_threshold}|evidence={evidence_packs_enabled}"
    )
    cycle_key = (
        f"{report_fingerprint}|slot={monitor_slot_key}" if auto_monitor else report_fingerprint
    )

    def _make_report_now():
        generation_time = datetime.now(IST)
        stamp = generation_time.strftime("%Y%m%d_%H%M%S_IST")

        social_pdf = build_social_media_report(df, alert_threshold=alert_threshold, query="ISRO")
        news_pdf = build_news_media_report(df, alert_threshold=alert_threshold, query="ISRO")
        combined_pdf = build_combined_intelligence_report(df, alert_threshold=alert_threshold, query="ISRO")

        names = {
            "social": f"ISRO_Social_Media_Intelligence_{stamp}.pdf",
            "news": f"ISRO_News_Media_Intelligence_{stamp}.pdf",
            "combined": f"ISRO_Combined_Intelligence_{stamp}.pdf",
        }

        x_zip = news_zip = None
        x_count = news_count = 0
        x_zip_name = f"ISRO_SocialMedia_Evidence_{stamp}.zip"
        news_zip_name = f"ISRO_NewsMedia_Evidence_{stamp}.zip"
        if evidence_packs_enabled:
            x_subset, x_narr, _ = _report_subset_analysis(df, "social")
            news_subset, news_narr, _ = _report_subset_analysis(df, "news")
            x_zip, x_count = build_x_evidence_zip(x_subset, x_narr)
            news_zip, news_count = build_news_evidence_zip(news_subset, news_narr)

        st.session_state["report_pack_fp_v514"] = report_fingerprint
        st.session_state["report_pack_cycle_v514"] = cycle_key
        st.session_state["report_pack_generated_v514"] = generation_time.strftime("%Y-%m-%d %H:%M:%S IST")
        st.session_state["social_pdf_v514"] = social_pdf
        st.session_state["news_pdf_v514"] = news_pdf
        st.session_state["combined_pdf_v514"] = combined_pdf
        st.session_state["social_pdf_name_v514"] = names["social"]
        st.session_state["news_pdf_name_v514"] = names["news"]
        st.session_state["combined_pdf_name_v514"] = names["combined"]
        st.session_state["x_evidence_zip_v514"] = x_zip
        st.session_state["news_evidence_zip_v514"] = news_zip
        st.session_state["x_evidence_zip_name_v514"] = x_zip_name
        st.session_state["news_evidence_zip_name_v514"] = news_zip_name
        st.session_state["x_evidence_count_v514"] = x_count
        st.session_state["news_evidence_count_v514"] = news_count

        # Compatibility for older alert/routine logic that expects one current PDF.
        st.session_state["pdf_report_v58"] = combined_pdf
        st.session_state["pdf_report_name_v58"] = names["combined"]
        st.session_state["pdf_report_fp_v58"] = report_fingerprint
        st.session_state["pdf_report_cycle_v58"] = cycle_key
        st.session_state["pdf_report_generated_v58"] = st.session_state["report_pack_generated_v514"]
        st.session_state["news_evidence_zip_v510"] = news_zip
        st.session_state["news_evidence_zip_name_v510"] = news_zip_name
        st.session_state["news_evidence_count_v510"] = news_count

        return combined_pdf, names["combined"]

    pending_alert_event = st.session_state.get("pending_alert_email_event_v59")
    alert_event_unsent = bool(
        pending_alert_event
        and st.session_state.get("alert_email_sent_event_v59") != pending_alert_event
    )
    email_needs_pdf = (
        (routine_email_enabled and auto_monitor)
        or (alert_email_enabled and alert_event_unsent)
    )

    if (
        (auto_pdf or email_needs_pdf)
        and st.session_state.get("report_pack_cycle_v514") != cycle_key
    ):
        try:
            with st.spinner("Building the 3-report intelligence pack..."):
                auto_bytes, auto_name = _make_report_now()
            if auto_pdf_download:
                _attempt_browser_pdf_download(auto_bytes, auto_name)
            st.success(
                "Three intelligence PDFs generated at "
                + st.session_state["report_pack_generated_v514"] + "."
            )
        except Exception as exc:
            st.error(f"Automatic report-pack generation failed: {type(exc).__name__}: {exc}")

    email_cfg = get_email_config()
    current_pdf = st.session_state.get("combined_pdf_v514")
    current_pdf_name = st.session_state.get("combined_pdf_name_v514", "ISRO_Combined_Intelligence_IST.pdf")
    current_generated_at = st.session_state.get(
        "report_pack_generated_v514", datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    )
    current_evidence_zip = st.session_state.get("news_evidence_zip_v514")
    current_evidence_zip_name = st.session_state.get("news_evidence_zip_name_v514", "ISRO_NewsMedia_Evidence_IST.zip")
    current_evidence_count = int(st.session_state.get("news_evidence_count_v514", 0) or 0)

    def _current_report_attachments():
        items = []
        if st.session_state.get("social_pdf_v514"):
            items.append((st.session_state["social_pdf_name_v514"], st.session_state["social_pdf_v514"], "application", "pdf"))
        if st.session_state.get("news_pdf_v514"):
            items.append((st.session_state["news_pdf_name_v514"], st.session_state["news_pdf_v514"], "application", "pdf"))
        # combined report is passed as the primary pdf_bytes attachment by existing email code.
        if evidence_packs_enabled and st.session_state.get("x_evidence_zip_v514"):
            items.append((st.session_state["x_evidence_zip_name_v514"], st.session_state["x_evidence_zip_v514"], "application", "zip"))
        if evidence_packs_enabled and st.session_state.get("news_evidence_zip_v514"):
            items.append((st.session_state["news_evidence_zip_name_v514"], st.session_state["news_evidence_zip_v514"], "application", "zip"))
        return items

    def _current_evidence_attachments():
        return _current_report_attachments()

    routine_cycle_key = f"slot={monitor_slot_key}|source={source_mode}" if auto_monitor else None
    routine_last_attempt_epoch = float(st.session_state.get("routine_email_last_attempt_epoch_v511", 0.0))
    routine_retry_ready = datetime.now(IST).timestamp() - routine_last_attempt_epoch >= 5 * 60

    if (
        routine_email_enabled and auto_monitor and email_cfg["routine_recipients"]
        and smtp_configured() and current_pdf and routine_cycle_key and routine_retry_ready
        and st.session_state.get("routine_email_sent_cycle_v59") != routine_cycle_key
    ):
        try:
            st.session_state["routine_email_last_attempt_epoch_v511"] = datetime.now(IST).timestamp()
            st.session_state["routine_email_last_attempt_v511"] = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
            routine_subject = "ISRO Intelligence Pack — " + datetime.now(IST).strftime("%d %b %Y %H:%M IST")
            routine_body = (
                "Automated hourly intelligence pack.\n\n"
                f"Generated: {current_generated_at}\n"
                f"Analyzed records: {len(df):,}\n"
                f"Negativity: {overall_negative_pct:.1f}%\n"
                f"Alert threshold: {alert_threshold:.1f}%\n\n"
                "Reports: Social Media Intelligence, News Media Intelligence, Combined Intelligence.\n"
                "Evidence packs: numbered Social Media and News Media evidence PDFs + manifest.csv."
            )
            if st.session_state.get("routine_delivery_cycle_v512") != routine_cycle_key:
                st.session_state["routine_delivery_cycle_v512"] = routine_cycle_key
                st.session_state["routine_delivered_v512"] = []
            delivered = {str(x).lower() for x in st.session_state.get("routine_delivered_v512", [])}
            pending = [a for a in email_cfg["routine_recipients"] if a.lower() not in delivered]
            delivery = send_email_message(
                pending, routine_subject, routine_body,
                pdf_bytes=current_pdf, pdf_filename=current_pdf_name,
                extra_attachments=_current_report_attachments(),
            )
            delivered.update(a.lower() for a in delivery["sent"])
            st.session_state["routine_delivered_v512"] = sorted(delivered)
            expected = {a.lower() for a in email_cfg["routine_recipients"]}
            if delivered >= expected:
                st.session_state["routine_email_sent_cycle_v59"] = routine_cycle_key
                st.session_state["last_routine_email_v59"] = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
                st.session_state["last_routine_email_error_v511"] = ""
                st.success(f"Intelligence pack delivered to {len(delivered)} recipient(s).")
            else:
                failed_text = "; ".join(f"{a}: {e}" for a, e in delivery["failed"].items())
                st.session_state["last_routine_email_error_v511"] = failed_text or "Some recipients remain pending."
                st.warning("Routine email partially delivered; pending recipients will be retried. " + failed_text)
        except Exception as exc:
            st.session_state["last_routine_email_error_v511"] = f"{type(exc).__name__}: {exc}"
            st.error(f"Hourly intelligence-pack email failed: {type(exc).__name__}: {exc}")

    # Alert-only recipients: combined report + the same supporting pack.
    pending_alert_event = st.session_state.get("pending_alert_email_event_v59")
    alert_event_unsent = bool(pending_alert_event and st.session_state.get("alert_email_sent_event_v59") != pending_alert_event)
    alert_last_attempt_epoch = float(st.session_state.get("alert_email_last_attempt_epoch_v511", 0.0))
    alert_retry_ready = datetime.now(IST).timestamp() - alert_last_attempt_epoch >= 5 * 60
    if (
        alert_email_enabled and alert_event_unsent and email_cfg["alert_recipients"]
        and smtp_configured() and alert_retry_ready
    ):
        try:
            st.session_state["alert_email_last_attempt_epoch_v511"] = datetime.now(IST).timestamp()
            st.session_state["alert_email_last_attempt_v511"] = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
            if not current_pdf:
                current_pdf, current_pdf_name = _make_report_now()
            alert_negative_pct = float(st.session_state.get("pending_alert_negative_pct_v59", overall_negative_pct))
            subject = f"ALERT: ISRO negativity {alert_negative_pct:.1f}% — " + datetime.now(IST).strftime("%d %b %Y %H:%M IST")
            body_text = (
                "Negativity threshold alert.\n\n"
                f"Negativity: {alert_negative_pct:.1f}%\n"
                f"Threshold: {alert_threshold:.1f}%\n"
                "Combined Intelligence is the primary attachment; source-specific reports and evidence packs are included when size permits."
            )
            if st.session_state.get("alert_delivery_event_v512") != pending_alert_event:
                st.session_state["alert_delivery_event_v512"] = pending_alert_event
                st.session_state["alert_delivered_v512"] = []
            delivered = {str(x).lower() for x in st.session_state.get("alert_delivered_v512", [])}
            pending = [a for a in email_cfg["alert_recipients"] if a.lower() not in delivered]
            delivery = send_email_message(
                pending, subject, body_text,
                pdf_bytes=current_pdf, pdf_filename=current_pdf_name,
                extra_attachments=_current_report_attachments(),
            )
            delivered.update(a.lower() for a in delivery["sent"])
            st.session_state["alert_delivered_v512"] = sorted(delivered)
            expected = {a.lower() for a in email_cfg["alert_recipients"]}
            if delivered >= expected:
                st.session_state["alert_email_sent_event_v59"] = pending_alert_event
                st.session_state["last_alert_email_v59"] = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
                st.session_state["last_alert_email_error_v511"] = ""
                st.success(f"Alert intelligence pack delivered to {len(delivered)} recipient(s).")
            else:
                failed_text = "; ".join(f"{a}: {e}" for a, e in delivery["failed"].items())
                st.session_state["last_alert_email_error_v511"] = failed_text or "Some alert recipients remain pending."
                st.warning("Alert email partially delivered; pending recipients will be retried. " + failed_text)
        except Exception as exc:
            st.session_state["last_alert_email_error_v511"] = f"{type(exc).__name__}: {exc}"
            st.error(f"Alert intelligence-pack email failed: {type(exc).__name__}: {exc}")

    with st.expander("Email/report-pack status"):
        st.write("Generated:", st.session_state.get("report_pack_generated_v514", "Not generated"))
        st.write("Social evidence PDFs:", int(st.session_state.get("x_evidence_count_v514", 0) or 0))
        st.write("News evidence PDFs:", int(st.session_state.get("news_evidence_count_v514", 0) or 0))
        st.write("Last hourly email:", st.session_state.get("last_routine_email_v59", "Not sent in this session"))
        st.write("Last alert email:", st.session_state.get("last_alert_email_v59", "Not sent in this session"))
        st.write("Last routine send error:", st.session_state.get("last_routine_email_error_v511", "None") or "None")
        st.write("Last alert send error:", st.session_state.get("last_alert_email_error_v511", "None") or "None")

    if st.button("Regenerate all 3 PDFs now", key="regenerate_report_pack_v514"):
        try:
            with st.spinner("Building report pack..."):
                manual_bytes, manual_name = _make_report_now()
            if auto_pdf_download:
                _attempt_browser_pdf_download(manual_bytes, manual_name)
            st.success("All three PDFs and evidence packs regenerated.")
        except Exception as exc:
            st.error(f"Report pack could not be generated: {type(exc).__name__}: {exc}")

    if st.session_state.get("report_pack_fp_v514") == report_fingerprint:
        st.info("Latest Build 5.17 report pack is ready.")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.download_button(
                "⬇️ Social Media Intel PDF",
                data=st.session_state.get("social_pdf_v514", b""),
                file_name=st.session_state.get("social_pdf_name_v514", "ISRO_Social_Media_Intelligence_IST.pdf"),
                mime="application/pdf", key="download_social_pdf_v514",
            )
        with c2:
            st.download_button(
                "⬇️ News Media Intel PDF",
                data=st.session_state.get("news_pdf_v514", b""),
                file_name=st.session_state.get("news_pdf_name_v514", "ISRO_News_Media_Intelligence_IST.pdf"),
                mime="application/pdf", key="download_news_pdf_v514",
            )
        with c3:
            st.download_button(
                "⬇️ Combined Intel PDF",
                data=st.session_state.get("combined_pdf_v514", b""),
                file_name=st.session_state.get("combined_pdf_name_v514", "ISRO_Combined_Intelligence_IST.pdf"),
                mime="application/pdf", key="download_combined_pdf_v514",
            )

        z1, z2 = st.columns(2)
        with z1:
            if st.session_state.get("x_evidence_zip_v514"):
                st.download_button(
                    "🗂️ Social Media evidence ZIP",
                    data=st.session_state["x_evidence_zip_v514"],
                    file_name=st.session_state.get("x_evidence_zip_name_v514", "ISRO_SocialMedia_Evidence_IST.zip"),
                    mime="application/zip", key="download_x_evidence_v514",
                )
        with z2:
            if st.session_state.get("news_evidence_zip_v514"):
                st.download_button(
                    "🗂️ News Media evidence ZIP",
                    data=st.session_state["news_evidence_zip_v514"],
                    file_name=st.session_state.get("news_evidence_zip_name_v514", "ISRO_NewsMedia_Evidence_IST.zip"),
                    mime="application/zip", key="download_news_evidence_v514",
                )
    else:
        st.info("The report pack will be generated automatically when analyzed data is available.")


    st.subheader("Record explorer — timestamps in IST")

    display_cols = [
        "timestamp",
        "platform",
        "username",
        "text",
        "semantic_cluster",
        "sentiment",
        "sentiment_score",
        "emotion",
        "engagement",
        "hashtags",
        "source_url",
    ]

    st.dataframe(
        filtered[display_cols]
        .sort_values(
            "timestamp",
            ascending=False
        ),
        use_container_width=True,
        hide_index=True,
    )


    st.subheader("Agent status")

    agent_status = pd.DataFrame(
        [
            [
                "CSV / X Sample Collector",
                "Active",
                "Manual or exported X-post data",
            ],
            [
                "GDELT News Collector",
                "Active",
                "Free web/news coverage",
            ],
            [
                "Supabase Connector",
                "Active",
                "Persistent storage",
            ],
            [
                "Sentiment Agent",
                "Active",
                "Sentiment classification",
            ],
            [
                "Emotion Agent",
                "Active",
                "Basic emotion signals",
            ],
            [
                "Semantic Topic Agent",
                "Active",
                "Automatic clustering",
            ],
            [
                "Narrative Agent",
                "Active",
                "Themes and representative evidence",
            ],
            [
                "Trend Agent",
                "Active",
                "Volume / coverage anomalies",
            ],
            [
                "Risk Agent",
                "Active",
                "Multi-signal monitoring score",
            ],
        ],
        columns=["Component", "Status", "Role"],
    )

    st.dataframe(
        agent_status,
        use_container_width=True,
        hide_index=True,
    )


    with st.expander("How to use the free ISRO workflow"):
        st.markdown(
            """
            **Option A — X samples**

            Put manually collected or legitimately exported X posts
            into the CSV template and upload it.

            **Option B — Free web/news**

            Search GDELT for ISRO-related news coverage. No X token
            is required.

            **Combined mode**

            Upload your X CSV and fetch GDELT news, then analyze both
            together. Use the Source/platform filter to separate
            `x_manual` from `web_news`.

            You can save either or both sources into Supabase.
            """
        )
