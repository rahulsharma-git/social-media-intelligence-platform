import hashlib
import random
import re
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.express as px
import requests
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
    "Build 4 — persistent-data foundation, CSV ingestion, Supabase, semantic narratives and risk"
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
        "platform": "x",
        "hashtags": "",
        "likes": 0,
        "reposts": 0,
        "replies": 0,
        "followers": 0,
        "source_url": "",
        "topic_seed": "Unassigned",
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
# DEMO COLLECTOR
# =========================================================
@st.cache_data
def make_sample_data(n=320):
    random.seed(42)

    templates = [
        ("Launch", "Really impressed with the new product launch. Great design and smooth experience.", ["launch", "product"]),
        ("Launch", "The latest feature is surprisingly useful and makes the product much easier to use.", ["launch", "feature"]),
        ("Launch", "A new version of the product was announced today with several changes.", ["launch", "update"]),
        ("Support", "Customer support resolved my issue quickly. Excellent service.", ["support", "service"]),
        ("Support", "Very frustrated with customer support. I have been waiting for hours.", ["support", "frustrated"]),
        ("Support", "Finally got help after a long wait. Relieved that the issue is fixed.", ["support", "resolved"]),
        ("Quality", "The product quality is much better than expected. Very happy with it.", ["quality", "product"]),
        ("Quality", "The product stopped working after a few days. Very disappointing.", ["quality", "problem"]),
        ("Quality", "The build feels solid but there are still a few reliability issues.", ["quality", "reliability"]),
        ("Pricing", "The company announced a new pricing plan today. Need to compare the details.", ["pricing", "update"]),
        ("Pricing", "The new pricing is too expensive. This increase makes no sense.", ["pricing", "expensive"]),
        ("Pricing", "I am angry about this price increase. Customers deserve better.", ["pricing", "angry"]),
        ("Delivery", "My order status changed this morning. Waiting for the delivery update.", ["delivery", "order"]),
        ("Delivery", "Delivery is delayed again and there is still no clear update.", ["delivery", "delay"]),
        ("Delivery", "I am worried the shipment may not arrive before the event.", ["delivery", "worried"]),
        ("App", "The mobile app feels faster after the latest update.", ["app", "update"]),
        ("App", "The app keeps crashing after the update and needs a fix.", ["app", "crash"]),
        ("App", "Login is failing for several users after the latest release.", ["app", "login"]),
    ]

    users = [f"user_{i:03d}" for i in range(1, 81)]
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    rows = []

    for _ in range(n):
        topic, text, tags = random.choice(templates)
        hours_ago = random.randint(0, 95)

        if hours_ago < 12 and random.random() < 0.72:
            topic, text, tags = random.choice([
                ("Pricing", "I am angry about this price increase. Customers deserve better.", ["pricing", "angry"]),
                ("Delivery", "Delivery is delayed again and there is still no clear update.", ["delivery", "delay"]),
                ("App", "The app keeps crashing after the update and needs a fix.", ["app", "crash"]),
            ])

        ts = now - timedelta(hours=hours_ago, minutes=random.randint(0, 59))
        rows.append({
            "external_id": "",
            "platform": "demo",
            "timestamp": ts,
            "username": random.choice(users),
            "text": text,
            "topic_seed": topic,
            "hashtags": " ".join(f"#{t}" for t in tags),
            "likes": max(0, int(random.gauss(32, 26))),
            "reposts": max(0, int(random.gauss(9, 10))),
            "replies": max(0, int(random.gauss(6, 7))),
            "followers": max(25, int(random.lognormvariate(6.4, 1.05))),
            "source_url": "",
        })

    return normalize_posts(pd.DataFrame(rows))


# =========================================================
# SUPABASE REST CONNECTOR
# =========================================================
def get_supabase_config():
    try:
        url = str(st.secrets.get("SUPABASE_URL", "")).strip().rstrip("/")
        # Prefer Supabase's current server-side secret key.
        # Keep the legacy variable as a fallback for older projects.
        key = str(
            st.secrets.get(
                "SUPABASE_SECRET_KEY",
                st.secrets.get("SUPABASE_SERVICE_KEY", "")
            )
        ).strip()
    except Exception:
        url, key = "", ""
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

    # Legacy service_role keys are JWTs and may be sent as Bearer tokens.
    # New sb_secret_* keys are API keys, not JWTs, and should stay in apikey.
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"

    return headers


def load_supabase_posts(limit=5000):
    url, _ = get_supabase_config()
    if not supabase_enabled():
        raise RuntimeError("Supabase secrets are not configured.")

    endpoint = f"{url}/rest/v1/social_posts"
    params = {
        "select": "external_id,platform,timestamp,username,text,hashtags,likes,reposts,replies,followers,source_url",
        "order": "timestamp.desc",
        "limit": str(limit),
    }

    response = requests.get(
        endpoint,
        headers=supabase_headers(),
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()

    if not data:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    df = pd.DataFrame(data)
    df["topic_seed"] = "Unassigned"
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

    endpoint = f"{url}/rest/v1/social_posts"
    headers = supabase_headers()
    headers["Prefer"] = "resolution=merge-duplicates,return=minimal"

    response = requests.post(
        endpoint,
        headers=headers,
        json=records,
        timeout=45,
    )
    response.raise_for_status()
    return len(records)


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
        "Anger": ["angry", "frustrated", "ridiculous", "furious", "unacceptable", "deserve better"],
        "Fear/Worry": ["worried", "afraid", "concerned", "risk", "may not", "uncertain"],
        "Joy": ["happy", "excellent", "great", "impressed", "love", "pleasant"],
        "Sadness/Disappointment": ["disappointing", "disappointed", "sad", "stopped working"],
        "Surprise": ["wow", "surprise", "surprisingly", "unexpected", "did not expect"],
        "Relief": ["relieved", "finally", "resolved", "fixed"],
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


@st.cache_data
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

    max_components = min(12, tfidf.shape[0] - 1, tfidf.shape[1] - 1)
    if max_components < 2:
        result["semantic_cluster"] = 0
        result["semantic_x"] = 0.0
        result["semantic_y"] = 0.0
        return result, vectorizer, tfidf, np.array(vectorizer.get_feature_names_out())

    svd = TruncatedSVD(n_components=max_components, random_state=42)
    latent = svd.fit_transform(tfidf)
    latent = Normalizer(copy=False).fit_transform(latent)

    cluster_count = max(2, min(requested_clusters, max(2, len(result) // 8)))
    cluster_count = min(cluster_count, len(result))

    model = KMeans(n_clusters=cluster_count, random_state=42, n_init=10)
    labels = model.fit_predict(latent)

    result["semantic_cluster"] = labels
    result["semantic_x"] = latent[:, 0]
    result["semantic_y"] = latent[:, 1] if latent.shape[1] > 1 else 0.0

    feature_names = np.array(vectorizer.get_feature_names_out())
    return result, vectorizer, tfidf, feature_names


# Do not cache: receives scipy/sklearn objects that Streamlit cannot reliably hash.
def run_narrative_agent(df, vectorizer, tfidf, feature_names):
    if vectorizer is None or tfidf is None or len(feature_names) == 0:
        return pd.DataFrame()

    narratives = []
    latest_time = df["timestamp"].max()

    for cluster_id in sorted(df["semantic_cluster"].unique()):
        mask = df["semantic_cluster"].eq(cluster_id).to_numpy()
        cluster_df = df[mask].copy()
        cluster_matrix = tfidf[mask]

        mean_tfidf = np.asarray(cluster_matrix.mean(axis=0)).ravel()
        top_indices = mean_tfidf.argsort()[::-1][:5]
        top_terms = [feature_names[i] for i in top_indices if mean_tfidf[i] > 0]

        narrative_title = (
            " / ".join(term.title() for term in top_terms[:3])
            if top_terms
            else f"Cluster {cluster_id}"
        )

        centroid = np.asarray(cluster_matrix.mean(axis=0))
        similarities = cosine_similarity(cluster_matrix, centroid).ravel()
        representative_post = cluster_df.iloc[int(np.argmax(similarities))]["text"]

        negative_pct = cluster_df["sentiment"].eq("Negative").mean() * 100
        engagement = int(cluster_df["engagement"].sum())

        recent = cluster_df[
            cluster_df["timestamp"] >= latest_time - pd.Timedelta(hours=12)
        ]
        previous = cluster_df[
            (cluster_df["timestamp"] < latest_time - pd.Timedelta(hours=12))
            & (cluster_df["timestamp"] >= latest_time - pd.Timedelta(hours=24))
        ]

        growth_pct = (
            0.0
            if len(previous) == 0
            else ((len(recent) - len(previous)) / len(previous)) * 100
        )

        narratives.append({
            "cluster": int(cluster_id),
            "narrative": narrative_title,
            "posts": len(cluster_df),
            "negative_pct": round(negative_pct, 1),
            "engagement": engagement,
            "12h_growth_pct": round(float(growth_pct), 1),
            "top_terms": ", ".join(top_terms),
            "representative_post": representative_post,
        })

    return (
        pd.DataFrame(narratives)
        .sort_values(["12h_growth_pct", "posts"], ascending=[False, False])
        .reset_index(drop=True)
    )


def run_trend_agent(df):
    if df.empty:
        return pd.DataFrame(
            columns=["hour", "posts", "rolling_avg", "anomaly_score", "is_anomaly"]
        )

    hourly = df.groupby(df["timestamp"].dt.floor("h")).size().reset_index(name="posts")
    hourly.columns = ["hour", "posts"]
    hourly["rolling_avg"] = hourly["posts"].rolling(6, min_periods=2).mean().shift(1)

    def score(row):
        baseline = row["rolling_avg"]
        if pd.isna(baseline) or baseline <= 0:
            return 0.0
        return row["posts"] / baseline

    hourly["anomaly_score"] = hourly.apply(score, axis=1)
    hourly["is_anomaly"] = hourly["anomaly_score"] >= 2.0
    return hourly


def run_risk_agent(df, trend_df, narratives):
    if df.empty:
        return {
            "risk": 0,
            "negative_pct": 0,
            "recent_growth_pct": 0,
        }

    negative_pct = df["sentiment"].eq("Negative").mean() * 100
    latest_time = df["timestamp"].max()

    recent = df[df["timestamp"] >= latest_time - pd.Timedelta(hours=12)]
    previous = df[
        (df["timestamp"] < latest_time - pd.Timedelta(hours=12))
        & (df["timestamp"] >= latest_time - pd.Timedelta(hours=24))
    ]

    growth_pct = (
        0
        if len(previous) == 0
        else ((len(recent) - len(previous)) / len(previous)) * 100
    )

    anomaly_multiplier = (
        float(trend_df["anomaly_score"].max()) if len(trend_df) else 0
    )

    narrative_growth = (
        max(0.0, float(narratives["12h_growth_pct"].max()))
        if len(narratives)
        else 0.0
    )

    risk = round(
        0.45 * negative_pct
        + 0.20 * max(0, min(growth_pct, 100))
        + 0.20 * max(0, min((anomaly_multiplier - 1) * 50, 100))
        + 0.15 * max(0, min(narrative_growth, 100))
    )

    return {
        "risk": min(100, max(0, risk)),
        "negative_pct": negative_pct,
        "recent_growth_pct": growth_pct,
    }


# =========================================================
# DATA SOURCE
# =========================================================
with st.sidebar:
    st.header("Data source")
    source_mode = st.radio(
        "Choose source",
        ["Demo sample", "Upload CSV", "Supabase database"],
    )

raw_df = pd.DataFrame()

if source_mode == "Demo sample":
    raw_df = make_sample_data()

elif source_mode == "Upload CSV":
    uploaded = st.file_uploader("Upload social posts CSV", type=["csv"])

    st.caption(
        "Required columns: timestamp, username, text. "
        "Optional: hashtags, likes, reposts, replies, followers, platform, source_url."
    )

    if uploaded is not None:
        try:
            raw_df = normalize_posts(pd.read_csv(uploaded))
            st.success(f"Loaded {len(raw_df):,} valid posts from CSV.")
        except Exception as exc:
            st.error(f"CSV could not be loaded: {exc}")
    else:
        st.info("Choose a CSV file from your phone to begin.")

elif source_mode == "Supabase database":
    if not supabase_enabled():
        st.warning(
            "Supabase is not configured yet. Add SUPABASE_URL and "
            "SUPABASE_SECRET_KEY in Streamlit app secrets."
        )
    else:
        try:
            raw_df = load_supabase_posts()
            st.success(f"Loaded {len(raw_df):,} posts from Supabase.")
        except Exception as exc:
            st.error(f"Could not load Supabase data: {exc}")


if raw_df.empty:
    st.info("No posts are available from the selected data source.")
    st.stop()


# Optional persistence action.
if supabase_enabled() and source_mode != "Supabase database":
    with st.sidebar:
        if st.button("Save current posts to Supabase"):
            try:
                count = save_posts_to_supabase(raw_df)
                st.success(f"Sent {count:,} posts to Supabase.")
            except Exception as exc:
                st.error(f"Save failed: {exc}")


# =========================================================
# RUN PIPELINE
# =========================================================
df = run_sentiment_agent(raw_df)
df = run_emotion_agent(df)
df["engagement"] = df["likes"] + df["reposts"] + df["replies"]

df, vectorizer, tfidf, feature_names = run_semantic_topic_agent(df)
narratives_df = run_narrative_agent(df, vectorizer, tfidf, feature_names)
trend_df = run_trend_agent(df)


# =========================================================
# ANALYSIS FILTERS
# =========================================================
with st.sidebar:
    st.divider()
    st.header("Analysis filters")

    sentiments = ["Positive", "Neutral", "Negative"]
    selected_sentiments = st.multiselect(
        "Sentiment", sentiments, default=sentiments
    )

    emotions = sorted(df["emotion"].unique())
    selected_emotions = st.multiselect("Emotion", emotions, default=emotions)

    clusters = sorted(df["semantic_cluster"].unique().tolist())
    selected_clusters = st.multiselect(
        "Semantic clusters", clusters, default=clusters
    )

    search = st.text_input(
        "Search posts", placeholder="pricing, delivery, support..."
    )


filtered = df[
    df["sentiment"].isin(selected_sentiments)
    & df["emotion"].isin(selected_emotions)
    & df["semantic_cluster"].isin(selected_clusters)
].copy()

if search:
    filtered = filtered[
        filtered["text"].str.contains(re.escape(search), case=False, na=False)
        | filtered["hashtags"].str.contains(re.escape(search), case=False, na=False)
    ]

filtered_trend = run_trend_agent(filtered)
filtered_narratives = narratives_df[
    narratives_df["cluster"].isin(selected_clusters)
].copy()
risk_data = run_risk_agent(filtered, filtered_trend, filtered_narratives)


# =========================================================
# DASHBOARD
# =========================================================
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Posts", f"{len(filtered):,}")
c2.metric("Engagement", f"{int(filtered['engagement'].sum()):,}")
c3.metric("Negative", f"{risk_data['negative_pct']:.1f}%")
c4.metric("12h growth", f"{risk_data['recent_growth_pct']:+.0f}%")
c5.metric("Narratives", len(filtered_narratives))
c6.metric("Risk", f"{risk_data['risk']}/100")

if risk_data["risk"] >= 65:
    st.error("🔴 High monitoring priority")
elif risk_data["risk"] >= 40:
    st.warning("🟠 Moderate monitoring priority")
else:
    st.success("🟢 Low monitoring priority")


anomalies = filtered_trend[filtered_trend["is_anomaly"]]
if len(anomalies):
    latest_anomaly = anomalies.sort_values("hour").iloc[-1]
    st.warning(
        f"⚠️ Trend Agent: {latest_anomaly['posts']} posts in one hour, "
        f"{latest_anomaly['anomaly_score']:.1f}× rolling baseline."
    )


left, right = st.columns([2, 1])

with left:
    st.subheader("Conversation trend")
    fig = px.line(
        filtered_trend,
        x="hour",
        y=["posts", "rolling_avg"],
        markers=True,
    )
    fig.update_layout(xaxis_title=None, yaxis_title="Posts", legend_title_text="")
    st.plotly_chart(fig, use_container_width=True)

with right:
    st.subheader("Sentiment")
    sent = (
        filtered["sentiment"]
        .value_counts()
        .reindex(["Positive", "Neutral", "Negative"], fill_value=0)
        .reset_index()
    )
    sent.columns = ["sentiment", "posts"]
    fig = px.pie(sent, names="sentiment", values="posts", hole=0.55)
    st.plotly_chart(fig, use_container_width=True)


st.subheader("🧠 Narrative intelligence")
if len(filtered_narratives):
    view = filtered_narratives[
        ["narrative", "posts", "12h_growth_pct", "negative_pct", "engagement", "top_terms"]
    ].copy()
    view.columns = [
        "Narrative",
        "Posts",
        "12h growth %",
        "Negative %",
        "Engagement",
        "Key terms",
    ]
    st.dataframe(view, use_container_width=True, hide_index=True)

    chosen = st.selectbox(
        "Inspect a narrative",
        filtered_narratives["narrative"].tolist(),
    )
    row = filtered_narratives[
        filtered_narratives["narrative"] == chosen
    ].iloc[0]
    st.markdown(f"**Representative post:** {row['representative_post']}")
else:
    st.info("No narrative clusters are available.")


st.subheader("Semantic conversation map")
if len(filtered):
    plot_df = filtered.copy()
    plot_df["cluster_label"] = "Cluster " + plot_df["semantic_cluster"].astype(str)
    fig = px.scatter(
        plot_df,
        x="semantic_x",
        y="semantic_y",
        color="cluster_label",
        hover_data=["sentiment", "emotion", "text"],
    )
    fig.update_layout(
        xaxis_title="Semantic dimension 1",
        yaxis_title="Semantic dimension 2",
        legend_title_text="",
    )
    st.plotly_chart(fig, use_container_width=True)


left, right = st.columns(2)

with left:
    st.subheader("Emotion intelligence")
    emotions = filtered["emotion"].value_counts().reset_index()
    emotions.columns = ["emotion", "posts"]
    fig = px.bar(emotions, x="emotion", y="posts")
    st.plotly_chart(fig, use_container_width=True)

with right:
    st.subheader("Top authors")
    authors = (
        filtered.groupby("username")
        .agg(
            engagement=("engagement", "sum"),
            posts=("text", "size"),
            followers=("followers", "max"),
        )
        .sort_values("engagement", ascending=False)
        .head(10)
        .reset_index()
    )
    st.dataframe(authors, use_container_width=True, hide_index=True)


st.subheader("Agent status")
agent_status = pd.DataFrame(
    [
        ["Data Source Layer", "Active", "Demo, CSV or Supabase"],
        ["Sentiment Agent", "Active", "Sentiment classification"],
        ["Emotion Agent", "Active", "Basic emotional signals"],
        ["Semantic Topic Agent", "Active", "Automatic conversation clustering"],
        ["Narrative Agent", "Active", "Cluster evidence and growth"],
        ["Trend Agent", "Active", "Volume anomalies"],
        ["Risk Agent", "Active", "Multi-signal risk score"],
        ["Live X Collector", "Planned", "Future connector"],
        ["Influence / Network", "Planned", "Future build"],
    ],
    columns=["Component", "Status", "Role"],
)
st.dataframe(agent_status, use_container_width=True, hide_index=True)


st.subheader("Post explorer")
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
]
st.dataframe(
    filtered[display_cols].sort_values("timestamp", ascending=False),
    use_container_width=True,
    hide_index=True,
)


with st.expander("CSV format"):
    st.code(
        """timestamp,username,text,hashtags,likes,reposts,replies,followers,platform,source_url
2026-09-20T10:30:00Z,user123,"Example post","#example",12,3,2,500,x,https://example.com/post"""
    )

with st.expander("Build 4 architecture"):
    st.markdown(
        """
        The analytics agents no longer depend on the data source.

        **Collector / data source → standardized post model → intelligence agents → dashboard**

        That allows a live X collector to be added later without rewriting the
        sentiment, semantic, narrative, trend or risk layers.
        """
    )
