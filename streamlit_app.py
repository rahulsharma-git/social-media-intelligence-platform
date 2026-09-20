import random
import re
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import plotly.express as px
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
    "Build 3 — sentiment, emotion, anomalies, semantic clustering and narrative intelligence"
)


# =========================================================
# SAMPLE DATA
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

        # Simulate a recent issue spike so the monitoring system has something to detect.
        if hours_ago < 12 and random.random() < 0.72:
            topic, text, tags = random.choice([
                ("Pricing", "I am angry about this price increase. Customers deserve better.", ["pricing", "angry"]),
                ("Delivery", "Delivery is delayed again and there is still no clear update.", ["delivery", "delay"]),
                ("App", "The app keeps crashing after the update and needs a fix.", ["app", "crash"]),
            ])

        ts = now - timedelta(hours=hours_ago, minutes=random.randint(0, 59))
        likes = max(0, int(random.gauss(32, 26)))
        reposts = max(0, int(random.gauss(9, 10)))
        replies = max(0, int(random.gauss(6, 7)))
        followers = max(25, int(random.lognormvariate(6.4, 1.05)))

        rows.append({
            "timestamp": ts,
            "username": random.choice(users),
            "text": text,
            "topic_seed": topic,
            "hashtags": " ".join(f"#{t}" for t in tags),
            "likes": likes,
            "reposts": reposts,
            "replies": replies,
            "followers": followers,
        })

    return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)


# =========================================================
# SENTIMENT AGENT
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


# =========================================================
# EMOTION AGENT
# =========================================================
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


# =========================================================
# SEMANTIC TOPIC AGENT
# LSA = TF-IDF -> SVD -> normalized latent semantic vectors -> KMeans
# =========================================================
@st.cache_data
def run_semantic_topic_agent(df, requested_clusters=6):
    result = df.copy()

    if len(result) < 8:
        result["semantic_cluster"] = 0
        result["semantic_x"] = 0.0
        result["semantic_y"] = 0.0
        return result, None, None, []

    vectorizer = TfidfVectorizer(
        stop_words="english",
        ngram_range=(1, 2),
        min_df=2,
        max_df=0.95,
    )
    tfidf = vectorizer.fit_transform(result["text"])

    max_components = min(12, tfidf.shape[0] - 1, tfidf.shape[1] - 1)
    if max_components < 2:
        result["semantic_cluster"] = 0
        result["semantic_x"] = 0.0
        result["semantic_y"] = 0.0
        return result, vectorizer, tfidf, []

    svd = TruncatedSVD(n_components=max_components, random_state=42)
    latent = svd.fit_transform(tfidf)
    latent = Normalizer(copy=False).fit_transform(latent)

    cluster_count = max(2, min(requested_clusters, len(result) // 8))
    model = KMeans(n_clusters=cluster_count, random_state=42, n_init=10)
    labels = model.fit_predict(latent)

    result["semantic_cluster"] = labels
    result["semantic_x"] = latent[:, 0]
    result["semantic_y"] = latent[:, 1] if latent.shape[1] > 1 else 0.0

    feature_names = np.array(vectorizer.get_feature_names_out())
    return result, vectorizer, tfidf, feature_names


# =========================================================
# NARRATIVE AGENT
# =========================================================
# Do NOT cache this function directly.
# It receives scikit-learn / scipy objects that Streamlit cannot reliably hash.
def run_narrative_agent(df, vectorizer, tfidf, feature_names):
    if vectorizer is None or tfidf is None or len(feature_names) == 0:
        return pd.DataFrame()

    narratives = []
    latest_time = df["timestamp"].max()

    for cluster_id in sorted(df["semantic_cluster"].unique()):
        mask = df["semantic_cluster"].eq(cluster_id).to_numpy()
        cluster_df = df[mask].copy()
        cluster_matrix = tfidf[mask]

        # Top terms for the narrative
        mean_tfidf = np.asarray(cluster_matrix.mean(axis=0)).ravel()
        top_indices = mean_tfidf.argsort()[::-1][:5]
        top_terms = [feature_names[i] for i in top_indices if mean_tfidf[i] > 0]
        title_terms = top_terms[:3]
        narrative_title = " / ".join(term.title() for term in title_terms) if title_terms else f"Cluster {cluster_id}"

        # Representative post = closest post to the cluster mean vector
        centroid = np.asarray(cluster_matrix.mean(axis=0))
        similarities = cosine_similarity(cluster_matrix, centroid).ravel()
        rep_local_index = int(np.argmax(similarities))
        representative_post = cluster_df.iloc[rep_local_index]["text"]

        negative_pct = cluster_df["sentiment"].eq("Negative").mean() * 100
        avg_sentiment = cluster_df["sentiment_score"].mean()
        engagement = int(cluster_df["engagement"].sum())

        recent = cluster_df[
            cluster_df["timestamp"] >= latest_time - pd.Timedelta(hours=12)
        ]
        previous = cluster_df[
            (cluster_df["timestamp"] < latest_time - pd.Timedelta(hours=12))
            & (cluster_df["timestamp"] >= latest_time - pd.Timedelta(hours=24))
        ]

        if len(previous) == 0:
            growth_pct = 0.0
        else:
            growth_pct = ((len(recent) - len(previous)) / len(previous)) * 100

        narratives.append({
            "cluster": int(cluster_id),
            "narrative": narrative_title,
            "posts": len(cluster_df),
            "negative_pct": round(negative_pct, 1),
            "avg_sentiment": round(float(avg_sentiment), 3),
            "engagement": engagement,
            "12h_growth_pct": round(float(growth_pct), 1),
            "top_terms": ", ".join(top_terms),
            "representative_post": representative_post,
        })

    return pd.DataFrame(narratives).sort_values(
        ["12h_growth_pct", "posts"], ascending=[False, False]
    ).reset_index(drop=True)


# =========================================================
# TREND / ANOMALY AGENT
# =========================================================
def run_trend_agent(df):
    if df.empty:
        return pd.DataFrame(
            columns=["hour", "posts", "rolling_avg", "anomaly_score", "is_anomaly"]
        )

    hourly = df.groupby(df["timestamp"].dt.floor("h")).size().reset_index(name="posts")
    hourly.columns = ["hour", "posts"]

    hourly["rolling_avg"] = (
        hourly["posts"].rolling(6, min_periods=2).mean().shift(1)
    )

    def anomaly_score(row):
        baseline = row["rolling_avg"]
        if pd.isna(baseline) or baseline <= 0:
            return 0.0
        return row["posts"] / baseline

    hourly["anomaly_score"] = hourly.apply(anomaly_score, axis=1)
    hourly["is_anomaly"] = hourly["anomaly_score"] >= 2.0
    return hourly


# =========================================================
# RISK AGENT
# =========================================================
def run_risk_agent(df, trend_df, narratives):
    if df.empty:
        return {
            "risk": 0,
            "negative_pct": 0,
            "recent_growth_pct": 0,
            "anomaly_multiplier": 0,
            "fastest_narrative_growth": 0,
        }

    negative_pct = df["sentiment"].eq("Negative").mean() * 100

    latest_time = df["timestamp"].max()
    recent = df[df["timestamp"] >= latest_time - pd.Timedelta(hours=12)]
    previous = df[
        (df["timestamp"] < latest_time - pd.Timedelta(hours=12))
        & (df["timestamp"] >= latest_time - pd.Timedelta(hours=24))
    ]

    if len(previous) == 0:
        growth_pct = 0
    else:
        growth_pct = ((len(recent) - len(previous)) / len(previous)) * 100

    anomaly_multiplier = (
        float(trend_df["anomaly_score"].max()) if len(trend_df) else 0
    )

    fastest_narrative_growth = (
        max(0.0, float(narratives["12h_growth_pct"].max()))
        if len(narratives)
        else 0.0
    )

    growth_component = max(0, min(growth_pct, 100))
    anomaly_component = max(0, min((anomaly_multiplier - 1) * 50, 100))
    narrative_component = max(0, min(fastest_narrative_growth, 100))

    risk = round(
        (0.45 * negative_pct)
        + (0.20 * growth_component)
        + (0.20 * anomaly_component)
        + (0.15 * narrative_component)
    )
    risk = min(100, max(0, risk))

    return {
        "risk": risk,
        "negative_pct": negative_pct,
        "recent_growth_pct": growth_pct,
        "anomaly_multiplier": anomaly_multiplier,
        "fastest_narrative_growth": fastest_narrative_growth,
    }


# =========================================================
# ORCHESTRATOR
# =========================================================
@st.cache_data
def run_intelligence_pipeline():
    raw = make_sample_data()
    df = run_sentiment_agent(raw)
    df = run_emotion_agent(df)
    df["engagement"] = df["likes"] + df["reposts"] + df["replies"]

    df, vectorizer, tfidf, feature_names = run_semantic_topic_agent(
        df, requested_clusters=6
    )
    narratives = run_narrative_agent(df, vectorizer, tfidf, feature_names)
    trend = run_trend_agent(df)
    risk = run_risk_agent(df, trend, narratives)

    return df, trend, narratives, risk


df, trend_df, narratives_df, overall_risk = run_intelligence_pipeline()


# =========================================================
# SIDEBAR FILTERS
# =========================================================
with st.sidebar:
    st.header("Filters")

    topics = sorted(df["topic_seed"].unique())
    selected_topics = st.multiselect("Seed topics", topics, default=topics)

    sentiments = ["Positive", "Neutral", "Negative"]
    selected_sentiments = st.multiselect(
        "Sentiment", sentiments, default=sentiments
    )

    emotions = sorted(df["emotion"].unique())
    selected_emotions = st.multiselect(
        "Emotion", emotions, default=emotions
    )

    clusters = sorted(df["semantic_cluster"].unique().tolist())
    selected_clusters = st.multiselect(
        "Semantic clusters", clusters, default=clusters
    )

    search = st.text_input(
        "Search posts", placeholder="pricing, delivery, support..."
    )


filtered = df[
    df["topic_seed"].isin(selected_topics)
    & df["sentiment"].isin(selected_sentiments)
    & df["emotion"].isin(selected_emotions)
    & df["semantic_cluster"].isin(selected_clusters)
].copy()

if search:
    filtered = filtered[
        filtered["text"].str.contains(re.escape(search), case=False, na=False)
        | filtered["hashtags"].str.contains(re.escape(search), case=False, na=False)
    ]


# Recalculate filtered narrative/trend summaries without rerunning clustering.
filtered_trend = run_trend_agent(filtered)

filtered_narratives = narratives_df[
    narratives_df["cluster"].isin(selected_clusters)
].copy()

risk_data = run_risk_agent(filtered, filtered_trend, filtered_narratives)


# =========================================================
# EXECUTIVE KPIs
# =========================================================
c1, c2, c3, c4, c5, c6 = st.columns(6)

c1.metric("Posts", f"{len(filtered):,}")
c2.metric(
    "Engagement",
    f"{int(filtered['engagement'].sum()):,}" if len(filtered) else "0",
)
c3.metric("Negative", f"{risk_data['negative_pct']:.1f}%")
c4.metric("12h growth", f"{risk_data['recent_growth_pct']:+.0f}%")
c5.metric("Narratives", f"{len(filtered_narratives)}")
c6.metric("Risk", f"{risk_data['risk']}/100")

risk = risk_data["risk"]

if risk >= 65:
    st.error("🔴 High monitoring priority")
elif risk >= 40:
    st.warning("🟠 Moderate monitoring priority")
else:
    st.success("🟢 Low monitoring priority")


# =========================================================
# ALERTS
# =========================================================
anomalies = filtered_trend[filtered_trend["is_anomaly"]]

if len(anomalies):
    latest_anomaly = anomalies.sort_values("hour").iloc[-1]
    st.warning(
        f"⚠️ Trend Agent: unusual activity detected — "
        f"{latest_anomaly['posts']} posts in one hour, "
        f"{latest_anomaly['anomaly_score']:.1f}× rolling baseline."
    )

if len(filtered_narratives):
    fastest = filtered_narratives.sort_values(
        "12h_growth_pct", ascending=False
    ).iloc[0]
    if fastest["12h_growth_pct"] > 50:
        st.warning(
            f"🧠 Narrative Agent: rapidly growing narrative — "
            f"**{fastest['narrative']}** ({fastest['12h_growth_pct']:+.0f}% over the previous 12h window)."
        )


# =========================================================
# MAIN CHARTS
# =========================================================
left, right = st.columns([2, 1])

with left:
    st.subheader("Conversation trend")
    if len(filtered_trend):
        fig = px.line(
            filtered_trend,
            x="hour",
            y=["posts", "rolling_avg"],
            markers=True,
        )
        fig.update_layout(
            xaxis_title=None,
            yaxis_title="Posts",
            legend_title_text="",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No posts match the selected filters.")

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


# =========================================================
# NARRATIVE INTELLIGENCE
# =========================================================
st.subheader("🧠 Narrative intelligence")

if len(filtered_narratives):
    narrative_view = filtered_narratives[
        [
            "narrative",
            "posts",
            "12h_growth_pct",
            "negative_pct",
            "engagement",
            "top_terms",
        ]
    ].copy()

    narrative_view.columns = [
        "Narrative",
        "Posts",
        "12h growth %",
        "Negative %",
        "Engagement",
        "Key terms",
    ]

    st.dataframe(
        narrative_view,
        use_container_width=True,
        hide_index=True,
    )

    chosen_narrative = st.selectbox(
        "Inspect a narrative",
        filtered_narratives["narrative"].tolist(),
    )

    selected_row = filtered_narratives[
        filtered_narratives["narrative"] == chosen_narrative
    ].iloc[0]

    st.markdown(f"**Representative post:** {selected_row['representative_post']}")
    st.caption(
        f"Key terms: {selected_row['top_terms']} · "
        f"Negative: {selected_row['negative_pct']}% · "
        f"12h growth: {selected_row['12h_growth_pct']:+.1f}%"
    )
else:
    st.info("No narratives match the current filters.")


# =========================================================
# SEMANTIC MAP
# =========================================================
st.subheader("Semantic conversation map")
st.caption(
    "Posts that appear closer together have more similar latent text meaning. "
    "Colors represent automatically discovered conversation clusters."
)

if len(filtered):
    plot_df = filtered.copy()
    plot_df["cluster_label"] = "Cluster " + plot_df["semantic_cluster"].astype(str)

    fig = px.scatter(
        plot_df,
        x="semantic_x",
        y="semantic_y",
        color="cluster_label",
        hover_data=["topic_seed", "sentiment", "emotion", "text"],
    )
    fig.update_layout(
        xaxis_title="Semantic dimension 1",
        yaxis_title="Semantic dimension 2",
        legend_title_text="",
    )
    st.plotly_chart(fig, use_container_width=True)


# =========================================================
# EMOTION / TOPIC
# =========================================================
left, right = st.columns(2)

with left:
    st.subheader("Emotion intelligence")
    emotion_counts = filtered["emotion"].value_counts().reset_index()
    emotion_counts.columns = ["emotion", "posts"]

    fig = px.bar(
        emotion_counts,
        x="emotion",
        y="posts",
    )
    fig.update_layout(
        xaxis_title=None,
        yaxis_title="Posts",
    )
    st.plotly_chart(fig, use_container_width=True)

with right:
    st.subheader("Known topic comparison")
    topic_summary = (
        filtered.groupby("topic_seed")
        .agg(
            posts=("text", "size"),
            engagement=("engagement", "sum"),
            avg_sentiment=("sentiment_score", "mean"),
        )
        .sort_values("posts", ascending=False)
        .reset_index()
    )
    st.dataframe(topic_summary, use_container_width=True, hide_index=True)


# =========================================================
# HASHTAGS / AUTHORS
# =========================================================
left, right = st.columns(2)

with left:
    st.subheader("Top hashtags")
    hashtags = (
        filtered["hashtags"]
        .str.split()
        .explode()
        .value_counts()
        .head(10)
        .reset_index()
    )
    hashtags.columns = ["hashtag", "posts"]
    st.bar_chart(hashtags.set_index("hashtag"))

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


# =========================================================
# AGENT STATUS
# =========================================================
st.subheader("Agent status")

agent_status = pd.DataFrame(
    [
        ["Sentiment Agent", "Active", "Positive / neutral / negative"],
        ["Emotion Agent", "Active", "Emotional signal classification"],
        ["Semantic Topic Agent", "Active", "LSA + automatic clustering"],
        ["Narrative Agent", "Active", "Narrative labels, growth and representative evidence"],
        ["Trend Agent", "Active", "Volume anomalies against rolling baseline"],
        ["Risk Agent", "Active", "Combines negativity, growth, anomaly and narrative signals"],
        ["Influence Agent", "Planned", "Next build"],
        ["Network Agent", "Planned", "Next build"],
    ],
    columns=["Agent", "Status", "Role"],
)

st.dataframe(agent_status, use_container_width=True, hide_index=True)


# =========================================================
# POST EXPLORER
# =========================================================
st.subheader("Post explorer")

display_cols = [
    "timestamp",
    "username",
    "text",
    "topic_seed",
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


with st.expander("How Build 3 works"):
    st.markdown(
        """
        **Semantic Topic Agent**

        The app converts post text into TF-IDF features, reduces those features
        into latent semantic dimensions with SVD, normalizes the semantic vectors,
        and then uses KMeans to discover conversation clusters automatically.

        **Narrative Agent**

        For each discovered cluster it calculates:
        - key phrases
        - representative post
        - conversation volume
        - engagement
        - negative sentiment percentage
        - recent growth

        This is intentionally an **open-source statistical Narrative Agent** rather
        than a generative LLM. That keeps this build fast and deployable on a free
        Streamlit instance.

        A future build can use an LLM to rewrite these evidence-backed clusters
        into more natural executive narrative summaries.
        """
    )
