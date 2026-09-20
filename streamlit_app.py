import hashlib
import io
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


st.set_page_config(
    page_title="Social Media Intelligence",
    page_icon="📡",
    layout="wide",
)

st.title("📡 Social Media Intelligence Platform")
st.caption(
    "Build 5 Free — CSV/X samples + free GDELT web/news monitoring + persistent intelligence"
)


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
    st.subheader("Option A — CSV / X samples")
    st.caption(
        "Upload the CSV and it will be validated and analyzed immediately. "
        "No extra Analyze button is required."
    )

    st.download_button(
        "Download CSV template",
        data=CSV_TEMPLATE,
        file_name="isro_x_template.csv",
        mime="text/csv",
    )

    uploaded = st.file_uploader(
        "Upload CSV",
        type=["csv"],
        key="x_csv_upload_v53",
        help=(
            "Required columns: timestamp, username, text. "
            "Optional: hashtags, likes, reposts, replies, followers, "
            "platform, source_url."
        ),
    )

    if uploaded is not None:
        try:
            # Use immutable bytes so reruns/mobile browser behavior cannot
            # leave the file pointer at EOF.
            uploaded_bytes = uploaded.getvalue()

            if not uploaded_bytes:
                raise ValueError("The uploaded file is empty.")

            preview_df = pd.read_csv(
                io.BytesIO(uploaded_bytes),
                encoding="utf-8",
            )

            normalized_columns = {
                str(c).strip().lower()
                for c in preview_df.columns
            }
            missing_required = (
                {"timestamp", "username", "text"}
                - normalized_columns
            )

            if missing_required:
                st.error(
                    "CSV received, but required column(s) are missing: "
                    + ", ".join(sorted(missing_required))
                )
            else:
                csv_df = normalize_posts(preview_df)

                st.success(
                    f"✅ X/CSV loaded: {uploaded.name} · "
                    f"{len(csv_df):,} valid records"
                )

                d1, d2, d3, d4 = st.columns(4)
                d1.metric("X/CSV rows", f"{len(csv_df):,}")
                d2.metric("Likes", f"{int(csv_df['likes'].sum()):,}")
                d3.metric("Reposts", f"{int(csv_df['reposts'].sum()):,}")
                d4.metric("Replies", f"{int(csv_df['replies'].sum()):,}")

                with st.expander("Preview uploaded X/CSV data", expanded=True):
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
                        ].head(10),
                        use_container_width=True,
                        hide_index=True,
                    )

                st.info(
                    "CSV is ready. The intelligence dashboard below "
                    "is using these uploaded records."
                )

        except UnicodeDecodeError:
            try:
                preview_df = pd.read_csv(
                    io.BytesIO(uploaded_bytes),
                    encoding="utf-8-sig",
                )
                csv_df = normalize_posts(preview_df)
                st.success(
                    f"✅ X/CSV loaded: {uploaded.name} · "
                    f"{len(csv_df):,} valid records"
                )
            except Exception as exc:
                st.error(
                    "CSV could not be read. "
                    f"{type(exc).__name__}: {exc}"
                )

        except Exception as exc:
            st.error(
                "CSV could not be loaded. "
                f"{type(exc).__name__}: {exc}"
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
        if uploaded is not None:
            st.warning(
                "A CSV was selected but produced no valid records. "
                "Check the validation message above."
            )
        else:
            st.info(
                "Upload your ISRO-related X CSV to begin."
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
