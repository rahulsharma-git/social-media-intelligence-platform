import base64
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
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import plotly.express as px
import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus
import streamlit as st
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh
from sklearn.cluster import KMeans
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.preprocessing import Normalizer
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak,
    Table, LongTable, TableStyle, Image as RLImage
)
from xml.sax.saxutils import escape


st.set_page_config(
    page_title="Social Media Intelligence",
    page_icon="📡",
    layout="wide",
)

st.title("📡 Social Media Intelligence Platform")
st.caption("Build 5.11 Free Monitoring — reliable IST slot scheduler + email retry")

IST = ZoneInfo("Asia/Kolkata")


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


def fetch_gdelt_news(query, timespan="1week", max_records=50):
    max_records = int(max(10, min(100, max_records)))

    response = requests.get(
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
        headers={
            "User-Agent": "Mozilla/5.0 SocialMediaIntelligenceMVP/1.0"
        },
    )

    # Let the caller decide whether to fall back.
    if response.status_code == 429:
        raise RuntimeError("GDELT_RATE_LIMIT")

    if response.status_code != 200:
        raise RuntimeError(
            f"GDELT request failed with HTTP {response.status_code}."
        )

    try:
        payload = response.json()
    except Exception as exc:
        raise RuntimeError(
            "GDELT did not return valid JSON."
        ) from exc

    articles = payload.get("articles", []) or []
    rows = []

    for article in articles:
        url = str(article.get("url", "") or "")
        title = str(article.get("title", "") or "").strip()
        domain = str(article.get("domain", "") or "").strip()
        seen = parse_gdelt_date(article.get("seendate"))

        if not title or pd.isna(seen):
            continue

        if not domain:
            domain = "news_source"

        rows.append({
            "external_id": hashlib.sha256(
                (url or (domain + "|" + title)).encode("utf-8")
            ).hexdigest(),
            "platform": "web_news",
            "timestamp": seen,
            "username": domain,
            "text": title,
            "hashtags": "",
            "likes": 0,
            "reposts": 0,
            "replies": 0,
            "followers": 0,
            "source_url": url,
            "topic_seed": "ISRO News",
        })

    return normalize_posts(pd.DataFrame(rows))


def fetch_google_news_rss(query, max_records=50):
    max_records = int(max(10, min(100, max_records)))

    # Google News RSS search does not require an API key.
    rss_url = (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}"
        "&hl=en-IN&gl=IN&ceid=IN:en"
    )

    response = requests.get(
        rss_url,
        timeout=45,
        headers={
            "User-Agent": "Mozilla/5.0 SocialMediaIntelligenceMVP/1.0"
        },
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Google News RSS failed with HTTP {response.status_code}."
        )

    try:
        root = ET.fromstring(response.content)
    except Exception as exc:
        raise RuntimeError(
            "Google News RSS returned unreadable XML."
        ) from exc

    items = root.findall(".//item")
    rows = []

    for item in items[:max_records]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        source_node = item.find("source")
        source = (
            source_node.text.strip()
            if source_node is not None and source_node.text
            else "Google News"
        )

        ts = pd.to_datetime(pub_date, errors="coerce", utc=True)

        if not title or pd.isna(ts):
            continue

        rows.append({
            "external_id": hashlib.sha256(
                (link or (source + "|" + title)).encode("utf-8")
            ).hexdigest(),
            "platform": "web_news",
            "timestamp": ts,
            "username": source,
            "text": title,
            "hashtags": "",
            "likes": 0,
            "reposts": 0,
            "replies": 0,
            "followers": 0,
            "source_url": link,
            "topic_seed": "ISRO News",
        })

    return normalize_posts(pd.DataFrame(rows))


def fetch_free_news(query, timespan="1week", max_records=50):
    """
    Try GDELT first. If GDELT rate-limits the Streamlit host,
    automatically fall back to Google News RSS.
    """
    try:
        df = fetch_gdelt_news(
            query=query,
            timespan=timespan,
            max_records=max_records,
        )
        return df, "GDELT"
    except Exception as exc:
        if str(exc) != "GDELT_RATE_LIMIT":
            # For robustness, still try the free fallback if GDELT fails
            # for any transient reason.
            gdelt_error = str(exc)
        else:
            gdelt_error = "GDELT rate limit (HTTP 429)"

        fallback = fetch_google_news_rss(
            query=query,
            max_records=max_records,
        )
        return fallback, f"Google News RSS fallback ({gdelt_error})"


# =========================================================
# INTELLIGENCE AGENTS
# =========================================================
@st.cache_data
def run_sentiment_agent(df):
    analyzer = SentimentIntensityAnalyzer()
    result = df.copy()

    result["sentiment_score"] = result["text"].apply(
        lambda x: analyzer.polarity_scores(x)["compound"]
    )

    def label(score):
        if score >= 0.05:
            return "Positive"
        if score <= -0.05:
            return "Negative"
        return "Neutral"

    result["sentiment"] = result["sentiment_score"].apply(label)
    return result


@st.cache_data
def run_emotion_agent(df):
    emotion_terms = {
        "Anger": [
            "angry", "frustrated", "furious", "outrage",
            "unacceptable", "criticism", "slams"
        ],
        "Fear/Worry": [
            "worried", "concern", "concerned", "risk", "uncertain",
            "failure", "problem", "issue", "delay"
        ],
        "Joy": [
            "happy", "excellent", "great", "impressed", "proud",
            "congratulations", "success", "successful"
        ],
        "Sadness/Disappointment": [
            "disappointing", "disappointed", "sad", "setback"
        ],
        "Surprise": [
            "wow", "surprise", "surprising", "unexpected"
        ],
        "Relief": [
            "relieved", "finally", "resolved", "recovered"
        ],
    }

    def classify(text):
        t = text.lower()
        scores = {
            emotion: sum(1 for term in terms if term in t)
            for emotion, terms in emotion_terms.items()
        }
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else "Other/Neutral"

    result = df.copy()
    result["emotion"] = result["text"].apply(classify)
    return result


def run_semantic_topic_agent(df, requested_clusters=6):
    result = df.copy()

    if len(result) < 8:
        result["semantic_cluster"] = 0
        result["semantic_x"] = 0.0
        result["semantic_y"] = 0.0
        return result, None, None, np.array([])

    vectorizer = TfidfVectorizer(
        stop_words="english",
        ngram_range=(1, 2),
        min_df=2,
        max_df=0.95,
    )

    try:
        tfidf = vectorizer.fit_transform(result["text"])
    except ValueError:
        result["semantic_cluster"] = 0
        result["semantic_x"] = 0.0
        result["semantic_y"] = 0.0
        return result, None, None, np.array([])

    max_components = min(
        10,
        tfidf.shape[0] - 1,
        tfidf.shape[1] - 1
    )

    if max_components < 2:
        result["semantic_cluster"] = 0
        result["semantic_x"] = 0.0
        result["semantic_y"] = 0.0
        return (
            result,
            vectorizer,
            tfidf,
            np.array(vectorizer.get_feature_names_out())
        )

    latent = TruncatedSVD(
        n_components=max_components,
        random_state=42
    ).fit_transform(tfidf)

    latent = Normalizer(copy=False).fit_transform(latent)

    cluster_count = max(
        2,
        min(requested_clusters, max(2, len(result) // 8))
    )
    cluster_count = min(cluster_count, len(result))

    labels = KMeans(
        n_clusters=cluster_count,
        random_state=42,
        n_init=10
    ).fit_predict(latent)

    result["semantic_cluster"] = labels
    result["semantic_x"] = latent[:, 0]
    result["semantic_y"] = (
        latent[:, 1] if latent.shape[1] > 1 else 0.0
    )

    return (
        result,
        vectorizer,
        tfidf,
        np.array(vectorizer.get_feature_names_out())
    )


def run_narrative_agent(df, vectorizer, tfidf, feature_names):
    if (
        vectorizer is None
        or tfidf is None
        or len(feature_names) == 0
    ):
        return pd.DataFrame()

    narratives = []
    latest_time = df["timestamp"].max()

    for cluster_id in sorted(df["semantic_cluster"].unique()):
        mask = df["semantic_cluster"].eq(cluster_id).to_numpy()
        cluster_df = df[mask].copy()
        cluster_matrix = tfidf[mask]

        mean_tfidf = np.asarray(
            cluster_matrix.mean(axis=0)
        ).ravel()

        top_indices = mean_tfidf.argsort()[::-1][:5]
        top_terms = [
            feature_names[i]
            for i in top_indices
            if mean_tfidf[i] > 0
        ]

        title = (
            " / ".join(term.title() for term in top_terms[:3])
            if top_terms
            else f"Cluster {cluster_id}"
        )

        centroid = np.asarray(cluster_matrix.mean(axis=0))
        similarities = cosine_similarity(
            cluster_matrix, centroid
        ).ravel()
        representative = cluster_df.iloc[
            int(np.argmax(similarities))
        ]["text"]

        recent = cluster_df[
            cluster_df["timestamp"]
            >= latest_time - pd.Timedelta(hours=12)
        ]
        previous = cluster_df[
            (
                cluster_df["timestamp"]
                < latest_time - pd.Timedelta(hours=12)
            )
            & (
                cluster_df["timestamp"]
                >= latest_time - pd.Timedelta(hours=24)
            )
        ]

        growth = (
            0.0
            if len(previous) == 0
            else (
                (len(recent) - len(previous))
                / len(previous)
            ) * 100
        )

        narratives.append({
            "cluster": int(cluster_id),
            "narrative": title,
            "posts": len(cluster_df),
            "negative_pct": round(
                cluster_df["sentiment"]
                .eq("Negative")
                .mean() * 100,
                1,
            ),
            "engagement": int(
                cluster_df["engagement"].sum()
            ),
            "12h_growth_pct": round(
                float(growth), 1
            ),
            "top_terms": ", ".join(top_terms),
            "representative_post": representative,
        })

    return (
        pd.DataFrame(narratives)
        .sort_values(
            ["12h_growth_pct", "posts"],
            ascending=[False, False]
        )
        .reset_index(drop=True)
    )


def run_trend_agent(df):
    if df.empty:
        return pd.DataFrame(
            columns=[
                "hour", "posts", "rolling_avg",
                "anomaly_score", "is_anomaly"
            ]
        )

    hourly = (
        df.groupby(df["timestamp"].dt.floor("h"))
        .size()
        .reset_index(name="posts")
    )
    hourly.columns = ["hour", "posts"]

    hourly["rolling_avg"] = (
        hourly["posts"]
        .rolling(6, min_periods=2)
        .mean()
        .shift(1)
    )

    def score(row):
        baseline = row["rolling_avg"]
        if pd.isna(baseline) or baseline <= 0:
            return 0.0
        return row["posts"] / baseline

    hourly["anomaly_score"] = hourly.apply(
        score, axis=1
    )
    hourly["is_anomaly"] = (
        hourly["anomaly_score"] >= 2.0
    )
    return hourly


def run_risk_agent(df, trend_df, narratives):
    if df.empty:
        return {
            "risk": 0,
            "negative_pct": 0,
            "recent_growth_pct": 0,
        }

    negative_pct = (
        df["sentiment"].eq("Negative").mean() * 100
    )
    latest_time = df["timestamp"].max()

    recent = df[
        df["timestamp"]
        >= latest_time - pd.Timedelta(hours=12)
    ]
    previous = df[
        (
            df["timestamp"]
            < latest_time - pd.Timedelta(hours=12)
        )
        & (
            df["timestamp"]
            >= latest_time - pd.Timedelta(hours=24)
        )
    ]

    growth = (
        0
        if len(previous) == 0
        else (
            (len(recent) - len(previous))
            / len(previous)
        ) * 100
    )

    anomaly_multiplier = (
        float(trend_df["anomaly_score"].max())
        if len(trend_df)
        else 0
    )

    narrative_growth = (
        max(
            0.0,
            float(narratives["12h_growth_pct"].max())
        )
        if len(narratives)
        else 0.0
    )

    risk = round(
        0.45 * negative_pct
        + 0.20 * max(0, min(growth, 100))
        + 0.20 * max(
            0,
            min((anomaly_multiplier - 1) * 50, 100)
        )
        + 0.15 * max(
            0,
            min(narrative_growth, 100)
        )
    )

    return {
        "risk": min(100, max(0, risk)),
        "negative_pct": negative_pct,
        "recent_growth_pct": growth,
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

    if news:
        # News has no meaningful social engagement in this free feed,
        # so rank by sentiment strength and then recency.
        work = work.sort_values(
            ["sentiment_score", "timestamp"],
            ascending=[score_ascending, False],
        )
    else:
        # For X/manual-X, use engagement as a secondary ranking signal.
        work = work.sort_values(
            ["sentiment_score", "engagement", "timestamp"],
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
        engagement = int(row.get("engagement", 0) or 0)

        st.markdown(f"**{rank}. {summary}**")

        if news:
            st.caption(
                f"Source: {source} · sentiment {score:+.2f}"
            )
        else:
            st.caption(
                f"Source/user: {source} · sentiment {score:+.2f} · "
                f"engagement {engagement}"
            )




# =========================================================
# MONITORING / HISTORIC NEGATIVITY HELPERS
# =========================================================
def compute_negativity_history(dataframe, freq="30min"):
    """Return 30-minute negativity history for Overall, X and News."""
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
    """Keep one in-session monitoring snapshot per 30-minute bucket."""
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    bucket = now.floor("30min")
    history = st.session_state.get("monitor_snapshots_v57", [])

    if not history or history[-1]["bucket"] != bucket.isoformat():
        history.append(
            {
                "bucket": bucket.isoformat(),
                "negative_pct": float(negative_pct),
            }
        )
        history = history[-336:]  # one week at 30-minute intervals
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
    ax.bar(counts.index, counts.values)
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
    for label, group in hist.groupby("series"):
        ax.plot(
            group["bucket"],
            group["negative_pct"],
            marker="o",
            label=label,
        )
    ax.axhline(
        threshold,
        linestyle="--",
        label=f"Alert threshold ({threshold:.0f}%)",
    )
    ax.set_ylim(0, 100)
    ax.set_title("Historic negativity - 30 minute buckets (IST)")
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
    ax.bar([f"C{x}" for x in stats.index], stats.values)
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
        ax.bar(x, vals, bottom=bottom, label=col)
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

    if news:
        return work.sort_values(
            ["sentiment_score", "timestamp"],
            ascending=[score_asc, False],
        ).head(limit)

    return work.sort_values(
        ["sentiment_score", "engagement", "timestamp"],
        ascending=[score_asc, False, False],
    ).head(limit)


def _report_monitoring_score(dataframe, trend_frame, narrative_frame):
    if dataframe is None or dataframe.empty:
        return 0

    negative_pct = dataframe["sentiment"].eq("Negative").mean() * 100
    latest_time = dataframe["timestamp"].max()

    recent = dataframe[
        dataframe["timestamp"] >= latest_time - pd.Timedelta(hours=12)
    ]
    previous = dataframe[
        (dataframe["timestamp"] < latest_time - pd.Timedelta(hours=12))
        & (
            dataframe["timestamp"]
            >= latest_time - pd.Timedelta(hours=24)
        )
    ]

    growth = (
        0
        if len(previous) == 0
        else ((len(recent) - len(previous)) / len(previous)) * 100
    )

    anomaly = (
        float(trend_frame["anomaly_score"].max())
        if trend_frame is not None and len(trend_frame)
        else 0.0
    )

    narrative_growth = (
        max(0.0, float(narrative_frame["12h_growth_pct"].max()))
        if narrative_frame is not None and len(narrative_frame)
        else 0.0
    )

    score = round(
        0.45 * negative_pct
        + 0.20 * max(0, min(growth, 100))
        + 0.20 * max(0, min((anomaly - 1) * 50, 100))
        + 0.15 * max(0, min(narrative_growth, 100))
    )
    return min(100, max(0, score))


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
            "VADER compound score from -1 to +1. Positive is >= +0.05; "
            "Negative is <= -0.05; values between are Neutral. For News this "
            "is headline-framing sentiment, not a measurement of public opinion. "
            "Sarcasm and multilingual text can reduce accuracy."
        ),
        (
            "Emotion",
            "Lightweight keyword/rule signals such as Anger, Fear/Worry, Joy, "
            "Sadness/Disappointment, Surprise and Relief. 'Other/Neutral' means "
            "no configured emotional cue was found."
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
            "Hourly record volume divided by a rolling six-hour baseline. "
            "The prototype marks >= 2.0x baseline as an anomaly."
        ),
        (
            "Monitoring score",
            "Prototype composite: 45% negative share + 20% recent growth + "
            "20% anomaly component + 15% fastest narrative growth. It is a "
            "triage aid, not a validated probability or official risk rating."
        ),
        (
            "Automatic monitoring",
            "When enabled, the active Streamlit session refreshes every 30 minutes. "
            "News is refreshed automatically; CSV data is re-analysed; Supabase is reloaded. "
            "Streamlit Community Cloud may sleep when no session is active, so this is not a guaranteed 24/7 scheduler."
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
    st.header("30-minute monitoring")
    auto_monitor = st.toggle(
        "Auto refresh every 30 minutes",
        value=True,
        help=(
            "Re-runs the active app session every 30 minutes. "
            "News is fetched again automatically when News is selected."
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
            "or the 30-minute monitoring cycle advances."
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

    news_evidence_zip_enabled = st.toggle(
        "Create Web News evidence ZIP",
        value=True,
        help=(
            "Creates one numbered evidence-sheet PDF for every web_news "
            "record plus a manifest.csv. The ZIP is attached to both "
            "routine and alert emails."
        ),
    )
    st.caption(
        "Evidence PDFs document feed headlines/metadata and analysis. "
        "They do not reproduce full publisher articles."
    )

    st.divider()
    st.header("Automatic email")

    email_cfg = get_email_config()

    routine_email_enabled = st.toggle(
        "Email PDF every 30 minutes",
        value=bool(email_cfg["routine_recipients"]),
        help=(
            "Sends the generated PDF to REPORT_RECIPIENTS_30MIN at fixed "
            "IST half-hour slots (:00 and :30) while the app is awake. "
            "Failed sends retry every ~5 minutes within the slot."
        ),
    )

    alert_email_enabled = st.toggle(
        "Email PDF only on negativity alert",
        value=bool(email_cfg["alert_recipients"]),
        help=(
            "Sends an alert PDF only when negativity crosses upward "
            "through the configured threshold."
        ),
    )

    st.caption(
        f"30-min recipients: {len(email_cfg['routine_recipients'])} · "
        f"Alert-only recipients: {len(email_cfg['alert_recipients'])}"
    )
    st.caption(
        "Build 5.12 sends a separate Gmail message to each recipient "
        "and tracks delivery independently."
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
    # India Standard Time half-hour slots (:00 and :30). This avoids the
    # drift caused by scheduling from the moment the browser session opened.
    scheduler_now_ist = datetime.now(IST)
    monitor_slot_start = scheduler_now_ist.replace(
        minute=(scheduler_now_ist.minute // 30) * 30,
        second=0,
        microsecond=0,
    )
    monitor_slot_end = monitor_slot_start + timedelta(minutes=30)
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
        index=1,
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
                    f"30-minute auto refresh fetched {len(fetched):,} "
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

    overall_negative_pct = (
        df["sentiment"].eq("Negative").mean() * 100
        if len(df)
        else 0.0
    )
    snapshot_df = _record_monitor_snapshot(overall_negative_pct)

    alert_latched = bool(
        st.session_state.get("negativity_alert_latched_v57", False)
    )

    if overall_negative_pct > alert_threshold:
        st.error(
            f"🚨 NEGATIVITY ALERT: {overall_negative_pct:.1f}% negative, "
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
    "Negative",
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
    "Monitoring score",
    f"{risk_data['risk']}/100",
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
        ),
        use_container_width=True,
    )




# =========================================================
# EXTENDED MONITORING CHARTS
# =========================================================
st.subheader("📈 Extended monitoring charts")

neg_history = compute_negativity_history(df)
if len(neg_history):
    neg_fig = px.line(
        neg_history,
        x="bucket",
        y="negative_pct",
        color="series",
        markers=True,
        hover_data={"records": True, "negative_pct": ":.1f"},
        title="Historic negativity - 30 minute buckets (IST)",
    )
    neg_fig.add_hline(
        y=alert_threshold,
        line_dash="dash",
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
        title="Live 30-minute monitoring snapshots (this app session)",
    )
    snap_fig.add_hline(
        y=alert_threshold,
        line_dash="dash",
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
    "records. The live snapshot chart records each 30-minute monitoring cycle "
    "for the current active app session."
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
            ["🟢 Top positive", "🔴 Top negative"]
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
            ["🟢 Top positive", "🔴 Top negative"]
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
        - VADER compound score ranges from **-1 to +1**.
        - **Positive:** score ≥ +0.05
        - **Neutral:** between -0.05 and +0.05
        - **Negative:** score ≤ -0.05
        - For **News**, this represents *headline framing*, not public opinion.
        - Sarcasm and multilingual text can reduce accuracy.

        **Emotion**
        - Lightweight rule-based signals: Anger, Fear/Worry, Joy,
          Sadness/Disappointment, Surprise and Relief.
        - **Other/Neutral** means no configured emotional cue was detected.

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
        - Hourly volume divided by a rolling six-hour baseline.
        - This prototype flags roughly **2× baseline or higher**.

        **Monitoring score**
        - Prototype triage score combining negative share, recent growth,
          anomaly strength and narrative growth.
        - It is **not** a validated probability or official risk rating.

        **Top-10 sentiment briefs**
        - X: ranked by sentiment strength, then engagement and recency.
        - News: ranked by headline sentiment strength, then recency.

        **30-minute monitoring**
        - When enabled, the active Streamlit session reruns every 30 minutes.
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
# PDF REPORT DOWNLOAD
# =========================================================
st.subheader("📄 Analytical PDF report")
st.caption(
    "The report uses all currently loaded records before dashboard filters. "
    "Build 5.10 generates it automatically when the dataset changes and on "
    "each 30-minute monitoring cycle. All report timestamps are IST."
)

report_fingerprint = (
    f"{len(df)}|"
    f"{df['timestamp'].min() if len(df) else ''}|"
    f"{df['timestamp'].max() if len(df) else ''}|"
    f"{source_mode}|{alert_threshold}|evidence={news_evidence_zip_enabled}"
)

cycle_key = (
    f"{report_fingerprint}|slot={monitor_slot_key}"
    if auto_monitor
    else report_fingerprint
)

def _make_report_now():
    generation_time = datetime.now(IST)
    generation_stamp = generation_time.strftime("%Y%m%d_%H%M%S_IST")

    report_bytes = build_pdf_report(
        df,
        narratives_df,
        trend_df,
        alert_threshold=alert_threshold,
    )
    report_name = (
        "social_media_intelligence_report_"
        + generation_stamp
        + ".pdf"
    )

    evidence_zip_bytes = None
    evidence_zip_name = None
    evidence_count = 0
    if news_evidence_zip_enabled:
        evidence_zip_bytes, evidence_count = build_news_evidence_zip(
            df,
            narratives_df,
        )
        evidence_zip_name = (
            "ISRO_WebNews_Evidence_"
            + generation_stamp
            + ".zip"
        )

    st.session_state["pdf_report_v58"] = report_bytes
    st.session_state["pdf_report_fp_v58"] = report_fingerprint
    st.session_state["pdf_report_cycle_v58"] = cycle_key
    st.session_state["pdf_report_name_v58"] = report_name
    st.session_state["pdf_report_generated_v58"] = (
        generation_time.strftime("%Y-%m-%d %H:%M:%S IST")
    )
    st.session_state["news_evidence_zip_v510"] = evidence_zip_bytes
    st.session_state["news_evidence_zip_name_v510"] = evidence_zip_name
    st.session_state["news_evidence_count_v510"] = evidence_count
    return report_bytes, report_name


# Automatic generation: once per dataset/cycle, not on every widget rerun.
pending_alert_event = st.session_state.get(
    "pending_alert_email_event_v59"
)
alert_event_unsent = bool(
    pending_alert_event
    and st.session_state.get(
        "alert_email_sent_event_v59"
    ) != pending_alert_event
)

email_needs_pdf = (
    (routine_email_enabled and auto_monitor)
    or (alert_email_enabled and alert_event_unsent)
)

if (
    (auto_pdf or email_needs_pdf)
    and st.session_state.get("pdf_report_cycle_v58") != cycle_key
):
    try:
        with st.spinner("Automatically building the latest PDF report..."):
            auto_bytes, auto_name = _make_report_now()

        if auto_pdf_download:
            _attempt_browser_pdf_download(auto_bytes, auto_name)

        st.success(
            "Latest PDF generated automatically at "
            + st.session_state["pdf_report_generated_v58"]
            + "."
        )
    except Exception as exc:
        st.error(
            "Automatic PDF generation failed: "
            f"{type(exc).__name__}: {exc}"
        )



# =========================================================
# AUTOMATIC EMAIL DELIVERY
# =========================================================
email_cfg = get_email_config()
current_pdf = st.session_state.get("pdf_report_v58")
current_pdf_name = st.session_state.get(
    "pdf_report_name_v58",
    "social_media_intelligence_report_IST.pdf",
)
current_generated_at = st.session_state.get(
    "pdf_report_generated_v58",
    datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"),
)
current_evidence_zip = st.session_state.get("news_evidence_zip_v510")
current_evidence_zip_name = st.session_state.get(
    "news_evidence_zip_name_v510",
    "ISRO_WebNews_Evidence_IST.zip",
)
current_evidence_count = int(
    st.session_state.get("news_evidence_count_v510", 0) or 0
)

def _current_evidence_attachments():
    if news_evidence_zip_enabled and current_evidence_zip:
        return [(
            current_evidence_zip_name,
            current_evidence_zip,
            "application",
            "zip",
        )]
    return []

# Routine recipients: once per fixed IST half-hour slot (:00 / :30).
routine_cycle_key = (
    f"slot={monitor_slot_key}|source={source_mode}"
    if auto_monitor
    else None
)

routine_last_attempt_epoch = float(
    st.session_state.get("routine_email_last_attempt_epoch_v511", 0.0)
)
routine_retry_ready = (
    datetime.now(IST).timestamp() - routine_last_attempt_epoch >= 5 * 60
)

if (
    routine_email_enabled
    and auto_monitor
    and email_cfg["routine_recipients"]
    and smtp_configured()
    and current_pdf
    and routine_cycle_key
    and routine_retry_ready
    and st.session_state.get(
        "routine_email_sent_cycle_v59"
    ) != routine_cycle_key
):
    try:
        st.session_state["routine_email_last_attempt_epoch_v511"] = (
            datetime.now(IST).timestamp()
        )
        st.session_state["routine_email_last_attempt_v511"] = (
            datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        )
        routine_subject = (
            "ISRO Social Intelligence Report — "
            + datetime.now(IST).strftime(
                "%d %b %Y %H:%M IST"
            )
        )
        routine_body = (
            "Automated 30-minute Social Media Intelligence report.\n\n"
            f"Generated: {current_generated_at}\n"
            f"Data source mode: {source_mode}\n"
            f"Analyzed records: {len(df):,}\n"
            f"Negativity: {overall_negative_pct:.1f}%\n"
            f"Alert threshold: {alert_threshold:.1f}%\n"
            f"Web News evidence PDFs: {current_evidence_count}\n\n"
            "Attachments:\n"
            "1. Main analytical PDF\n"
            + (
                "2. Numbered Web News evidence ZIP (PDFs + manifest.csv)"
                if news_evidence_zip_enabled and current_evidence_zip
                else "2. Web News evidence ZIP not generated for this cycle"
            )
        )

        # Reset per-recipient completion tracking when a new
        # 30-minute slot begins.
        if (
            st.session_state.get(
                "routine_delivery_cycle_v512"
            ) != routine_cycle_key
        ):
            st.session_state["routine_delivery_cycle_v512"] = (
                routine_cycle_key
            )
            st.session_state["routine_delivered_v512"] = []

        delivered = {
            str(x).lower()
            for x in st.session_state.get(
                "routine_delivered_v512",
                [],
            )
        }

        pending_recipients = [
            address
            for address in email_cfg["routine_recipients"]
            if address.lower() not in delivered
        ]

        delivery = send_email_message(
            pending_recipients,
            routine_subject,
            routine_body,
            pdf_bytes=current_pdf,
            pdf_filename=current_pdf_name,
            extra_attachments=_current_evidence_attachments(),
        )

        delivered.update(
            address.lower()
            for address in delivery["sent"]
        )
        st.session_state["routine_delivered_v512"] = sorted(
            delivered
        )

        all_expected = {
            address.lower()
            for address in email_cfg["routine_recipients"]
        }

        if delivered >= all_expected:
            st.session_state["routine_email_sent_cycle_v59"] = (
                routine_cycle_key
            )
            st.session_state["last_routine_email_v59"] = (
                datetime.now(IST).strftime(
                    "%Y-%m-%d %H:%M:%S IST"
                )
            )
            st.session_state["last_routine_email_error_v511"] = ""
            st.success(
                "30-minute PDF delivered individually to "
                f"{len(delivered)} recipient(s)."
            )
        else:
            failed_text = "; ".join(
                f"{addr}: {err}"
                for addr, err in delivery["failed"].items()
            )
            st.session_state["last_routine_email_error_v511"] = (
                failed_text
                or "Some recipients are still pending delivery."
            )
            st.warning(
                "Routine email was only partially delivered. "
                "Successful recipients will NOT be re-sent on retry. "
                "The remaining recipient(s) will be retried in about "
                "5 minutes. "
                + (
                    f"Details: {failed_text}"
                    if failed_text
                    else ""
                )
            )
    except Exception as exc:
        st.session_state["last_routine_email_error_v511"] = (
            f"{type(exc).__name__}: {exc}"
        )
        st.error(
            "30-minute email failed. The app will retry in about 5 minutes "
            "while this monitoring session remains awake. "
            f"Technical detail: {type(exc).__name__}: {exc}"
        )


# Alert-only recipients: once per upward threshold crossing.
pending_alert_event = st.session_state.get(
    "pending_alert_email_event_v59"
)
alert_event_unsent = bool(
    pending_alert_event
    and st.session_state.get(
        "alert_email_sent_event_v59"
    ) != pending_alert_event
)

alert_last_attempt_epoch = float(
    st.session_state.get("alert_email_last_attempt_epoch_v511", 0.0)
)
alert_retry_ready = (
    datetime.now(IST).timestamp() - alert_last_attempt_epoch >= 5 * 60
)

if (
    alert_email_enabled
    and alert_event_unsent
    and email_cfg["alert_recipients"]
    and smtp_configured()
    and alert_retry_ready
):
    try:
        st.session_state["alert_email_last_attempt_epoch_v511"] = (
            datetime.now(IST).timestamp()
        )
        st.session_state["alert_email_last_attempt_v511"] = (
            datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        )
        # Build a PDF immediately if the currently cached report is missing.
        if not current_pdf:
            current_pdf, current_pdf_name = _make_report_now()
            current_evidence_zip = st.session_state.get(
                "news_evidence_zip_v510"
            )
            current_evidence_zip_name = st.session_state.get(
                "news_evidence_zip_name_v510",
                "ISRO_WebNews_Evidence_IST.zip",
            )
            current_evidence_count = int(
                st.session_state.get(
                    "news_evidence_count_v510", 0
                ) or 0
            )

        alert_negative_pct = float(
            st.session_state.get(
                "pending_alert_negative_pct_v59",
                overall_negative_pct,
            )
        )

        alert_subject = (
            f"ALERT: ISRO negativity {alert_negative_pct:.1f}% — "
            + datetime.now(IST).strftime(
                "%d %b %Y %H:%M IST"
            )
        )
        alert_body = (
            "Negativity threshold alert from the Social Media "
            "Intelligence Platform.\n\n"
            f"Alert time: "
            f"{datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}\n"
            f"Negativity: {alert_negative_pct:.1f}%\n"
            f"Configured threshold: {alert_threshold:.1f}%\n"
            f"Data source mode: {source_mode}\n"
            f"Analyzed records: {len(df):,}\n"
            f"Web News evidence PDFs: {current_evidence_count}\n\n"
            "The current analytical PDF is attached. "
            "When enabled, a ZIP containing one numbered Web News evidence "
            "PDF per feed item plus manifest.csv is also attached.\n\n"
            "This monitoring score and sentiment classification are "
            "analytical signals, not verified facts or probabilities."
        )

        # Reset per-recipient completion tracking for each new
        # threshold-crossing alert event.
        if (
            st.session_state.get(
                "alert_delivery_event_v512"
            ) != pending_alert_event
        ):
            st.session_state["alert_delivery_event_v512"] = (
                pending_alert_event
            )
            st.session_state["alert_delivered_v512"] = []

        delivered = {
            str(x).lower()
            for x in st.session_state.get(
                "alert_delivered_v512",
                [],
            )
        }

        pending_recipients = [
            address
            for address in email_cfg["alert_recipients"]
            if address.lower() not in delivered
        ]

        delivery = send_email_message(
            pending_recipients,
            alert_subject,
            alert_body,
            pdf_bytes=current_pdf,
            pdf_filename=current_pdf_name,
            extra_attachments=_current_evidence_attachments(),
        )

        delivered.update(
            address.lower()
            for address in delivery["sent"]
        )
        st.session_state["alert_delivered_v512"] = sorted(
            delivered
        )

        all_expected = {
            address.lower()
            for address in email_cfg["alert_recipients"]
        }

        if delivered >= all_expected:
            st.session_state["alert_email_sent_event_v59"] = (
                pending_alert_event
            )
            st.session_state["last_alert_email_v59"] = (
                datetime.now(IST).strftime(
                    "%Y-%m-%d %H:%M:%S IST"
                )
            )
            st.session_state["last_alert_email_error_v511"] = ""
            st.success(
                "Alert PDF delivered individually to "
                f"{len(delivered)} alert recipient(s)."
            )
        else:
            failed_text = "; ".join(
                f"{addr}: {err}"
                for addr, err in delivery["failed"].items()
            )
            st.session_state["last_alert_email_error_v511"] = (
                failed_text
                or "Some alert recipients are still pending delivery."
            )
            st.warning(
                "Alert email was only partially delivered. "
                "Successful recipients will NOT be re-sent on retry. "
                "The remaining recipient(s) will be retried in about "
                "5 minutes. "
                + (
                    f"Details: {failed_text}"
                    if failed_text
                    else ""
                )
            )
    except Exception as exc:
        st.session_state["last_alert_email_error_v511"] = (
            f"{type(exc).__name__}: {exc}"
        )
        st.error(
            "Alert email failed. The app will retry in about 5 minutes "
            "while this monitoring session remains awake. "
            f"Technical detail: {type(exc).__name__}: {exc}"
        )


with st.expander("Email delivery status"):
    st.write("Current IST time:", datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST"))
    st.write("Current 30-minute slot:", monitor_slot_key)
    st.write("Next scheduled slot:", monitor_slot_end.strftime("%Y-%m-%d %H:%M:%S IST"))
    st.write(
        "30-minute PDF recipients:",
        ", ".join(email_cfg["routine_recipients"])
        if email_cfg["routine_recipients"]
        else "None configured",
    )
    st.write(
        "Alert-only recipients:",
        ", ".join(email_cfg["alert_recipients"])
        if email_cfg["alert_recipients"]
        else "None configured",
    )
    st.write(
        "Parsed routine recipient count:",
        len(email_cfg["routine_recipients"]),
    )
    st.write(
        "Routine recipients delivered in current slot:",
        ", ".join(
            st.session_state.get(
                "routine_delivered_v512",
                [],
            )
        )
        or "None yet",
    )
    st.write(
        "Parsed alert recipient count:",
        len(email_cfg["alert_recipients"]),
    )
    st.write(
        "Alert recipients delivered in current event:",
        ", ".join(
            st.session_state.get(
                "alert_delivered_v512",
                [],
            )
        )
        or "None yet",
    )
    st.write(
        "Last 30-minute email:",
        st.session_state.get(
            "last_routine_email_v59",
            "Not sent in this session",
        ),
    )
    st.write(
        "Last alert email:",
        st.session_state.get(
            "last_alert_email_v59",
            "Not sent in this session",
        ),
    )
    st.write(
        "Last routine send attempt:",
        st.session_state.get(
            "routine_email_last_attempt_v511",
            "No attempt in this session",
        ),
    )
    st.write(
        "Last routine send error:",
        st.session_state.get(
            "last_routine_email_error_v511",
            "None",
        ) or "None",
    )
    st.write(
        "Last alert send attempt:",
        st.session_state.get(
            "alert_email_last_attempt_v511",
            "No attempt in this session",
        ),
    )
    st.write(
        "Last alert send error:",
        st.session_state.get(
            "last_alert_email_error_v511",
            "None",
        ) or "None",
    )
    st.write(
        "Last successful news refresh:",
        st.session_state.get(
            "last_news_fetch_iso_v57",
            "Not refreshed in this session",
        ),
    )
    st.write(
        "Last news refresh error:",
        st.session_state.get(
            "last_news_fetch_error_v511",
            "None",
        ) or "None",
    )
    st.write(
        "Web News evidence PDFs in latest ZIP:",
        current_evidence_count,
    )
    if current_evidence_zip:
        st.write(
            "Evidence ZIP size:",
            f"{len(current_evidence_zip) / (1024 * 1024):.2f} MB",
        )


# Manual regeneration remains available as a fallback.
if st.button(
    "Regenerate PDF now",
    key="regenerate_pdf_v58",
):
    try:
        with st.spinner("Building PDF report..."):
            manual_bytes, manual_name = _make_report_now()

        if auto_pdf_download:
            _attempt_browser_pdf_download(manual_bytes, manual_name)

        st.success(
            "PDF regenerated at "
            + st.session_state["pdf_report_generated_v58"]
            + "."
        )
    except Exception as exc:
        st.error(
            "PDF report could not be generated: "
            f"{type(exc).__name__}: {exc}"
        )


if (
    st.session_state.get("pdf_report_v58")
    and st.session_state.get("pdf_report_fp_v58") == report_fingerprint
):
    generated_at = st.session_state.get(
        "pdf_report_generated_v58",
        "unknown time",
    )
    st.info(
        f"Latest report ready · generated {generated_at}. "
        "If your browser blocked the automatic download, use the button below."
    )

    st.download_button(
        "⬇️ Download latest PDF report",
        data=st.session_state["pdf_report_v58"],
        file_name=st.session_state.get(
            "pdf_report_name_v58",
            "social_media_intelligence_report_IST.pdf",
        ),
        mime="application/pdf",
        key="download_pdf_v58",
    )

    if st.session_state.get("news_evidence_zip_v510"):
        st.download_button(
            "🗂️ Download Web News evidence ZIP",
            data=st.session_state["news_evidence_zip_v510"],
            file_name=st.session_state.get(
                "news_evidence_zip_name_v510",
                "ISRO_WebNews_Evidence_IST.zip",
            ),
            mime="application/zip",
            key="download_news_evidence_zip_v510",
        )
        st.caption(
            f"Evidence ZIP contains "
            f"{int(st.session_state.get('news_evidence_count_v510', 0) or 0)} "
            "numbered Web News evidence PDF(s) plus manifest.csv."
        )
else:
    st.info(
        "The PDF will be generated automatically when analyzed data is available."
    )


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
