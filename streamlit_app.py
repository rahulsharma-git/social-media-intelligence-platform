import hashlib
import io
import os
import random
import re
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import plotly.express as px
import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus
import streamlit as st
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
    Table, LongTable, TableStyle
)
from xml.sax.saxutils import escape


st.set_page_config(
    page_title="Social Media Intelligence",
    page_icon="📡",
    layout="wide",
)

st.title("📡 Social Media Intelligence Platform")
st.caption("Build 5.6.2 Free Stable — PDF generation hotfix")


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
    )
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


def build_pdf_report(report_df, narrative_df, trend_df):
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

    generated = datetime.now(timezone.utc)
    x_df = report_df[
        report_df["platform"].isin(["x_manual", "x"])
    ].copy()
    news_df = report_df[
        report_df["platform"].eq("web_news")
    ].copy()

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
            f"Generated: {generated.strftime('%Y-%m-%d %H:%M UTC')} | "
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
        [p("Detected semantic clusters", table_text), p(f"{report_df['semantic_cluster'].nunique()}", table_text)],
    ]
    add_long_table(summary_rows, [62 * mm, 45 * mm])

    # Top sentiment briefs
    story.append(p("2. Top sentiment briefs", h1))
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
    story.append(p("3. Cluster and narrative interpretation", h1))
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
    story.append(p("4. Legends and methodology", h1))
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
                p("Time (UTC)", table_head),
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
            timestamp = pd.to_datetime(row["timestamp"], utc=True).strftime(
                "%Y-%m-%d %H:%M"
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
            "News is grouped by the sentiment category assigned to each "
            "headline. The free feeds generally expose the media source/agency "
            "but not the individual article author.",
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
                    p("Time (UTC)", table_head),
                    p("Title", table_head),
                    p("Author", table_head),
                    p("Media agency/source", table_head),
                    p("Score", table_head),
                ]
            ]

            for idx_num, (_, row) in enumerate(subset.iterrows(), start=1):
                timestamp = pd.to_datetime(
                    row["timestamp"], utc=True
                ).strftime("%Y-%m-%d %H:%M")

                n_rows.append(
                    [
                        p(str(idx_num), table_text),
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

    if st.button(
        "Fetch free news",
        type="primary"
    ):
        try:
            fetched, provider = fetch_free_news(
                query=query,
                timespan=timespan_map[period_label],
                max_records=max_records,
            )
            st.session_state["gdelt_posts"] = fetched
            st.session_state["news_provider"] = provider
            news_df = fetched
            st.success(
                f"Fetched {len(fetched):,} news items via {provider}."
            )
        except Exception as exc:
            st.error(str(exc))

    news_df = st.session_state.get(
        "gdelt_posts",
        pd.DataFrame(columns=STANDARD_COLUMNS)
    )

    if len(news_df):
        provider = st.session_state.get("news_provider", "Free news source")
        st.info(
            f"Current news dataset: {len(news_df):,} items · Source: {provider}"
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



# =========================================================
# PDF REPORT DOWNLOAD
# =========================================================
st.subheader("📄 Download analytical PDF report")
st.caption(
    "The PDF uses all currently loaded records (before dashboard filters), "
    "so its annexures are complete. It includes top positive/negative briefs, "
    "cluster legends, methodology, all X posts analyzed, and all News items "
    "grouped by sentiment category."
)

report_fingerprint = (
    f"{len(df)}|"
    f"{df['timestamp'].min() if len(df) else ''}|"
    f"{df['timestamp'].max() if len(df) else ''}|"
    f"{source_mode}"
)

if st.button(
    "Generate PDF report",
    type="primary",
    key="generate_pdf_v56",
):
    try:
        with st.spinner("Building PDF report..."):
            report_bytes = build_pdf_report(
                df,
                narratives_df,
                trend_df,
            )
        st.session_state["pdf_report_v56"] = report_bytes
        st.session_state["pdf_report_fp_v56"] = report_fingerprint
        st.session_state["pdf_report_name_v56"] = (
            "social_media_intelligence_report_"
            + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
            + ".pdf"
        )
        st.success(
            "PDF report generated. Use the download button below."
        )
    except Exception as exc:
        st.error(
            "PDF report could not be generated: "
            f"{type(exc).__name__}: {exc}"
        )

if (
    st.session_state.get("pdf_report_v56")
    and st.session_state.get("pdf_report_fp_v56")
    == report_fingerprint
):
    st.download_button(
        "⬇️ Download PDF report",
        data=st.session_state["pdf_report_v56"],
        file_name=st.session_state.get(
            "pdf_report_name_v56",
            "social_media_intelligence_report.pdf",
        ),
        mime="application/pdf",
        key="download_pdf_v56",
    )
elif st.session_state.get("pdf_report_v56"):
    st.info(
        "The loaded dataset changed after the previous PDF was generated. "
        "Tap **Generate PDF report** again."
    )


st.subheader("Record explorer")

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
