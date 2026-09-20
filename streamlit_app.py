import random
import re
from collections import Counter
from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer


st.set_page_config(
    page_title="Social Media Intelligence",
    page_icon="📡",
    layout="wide",
)

st.title("📡 Social Media Intelligence Platform")
st.caption("Build 2 — sentiment, emotion, trends, anomalies, topics and risk")


# ---------- SAMPLE DATA ----------
@st.cache_data
def make_sample_data(n=220):
    random.seed(42)

    templates = [
        ("New launch", "Really impressed with the new product launch. Great design and smooth experience.", ["launch", "product"]),
        ("Customer support", "Customer support resolved my issue quickly. Excellent service.", ["support", "service"]),
        ("Product quality", "The product quality is much better than expected. Very happy with it.", ["quality", "product"]),
        ("Pricing", "The company announced a new pricing plan today. Need to compare the details.", ["pricing", "update"]),
        ("Delivery", "My order status changed this morning. Waiting for the delivery update.", ["delivery", "order"]),
        ("New launch", "A new version of the product was announced today.", ["launch", "news"]),
        ("Pricing", "The new pricing is too expensive. This increase makes no sense.", ["pricing", "expensive"]),
        ("Delivery", "Delivery is delayed again and there is still no clear update.", ["delivery", "delay"]),
        ("Customer support", "Very frustrated with customer support. I have been waiting for hours.", ["support", "frustrated"]),
        ("Product quality", "The product stopped working after a few days. Very disappointing.", ["quality", "problem"]),
        ("Delivery", "I am worried the shipment may not arrive before the event.", ["delivery", "worried"]),
        ("New launch", "Wow, I did not expect this feature. This is a pleasant surprise.", ["launch", "surprise"]),
        ("Pricing", "I am angry about this price increase. Customers deserve better.", ["pricing", "angry"]),
        ("Customer support", "Finally got help after a long wait. Relieved that it is fixed.", ["support", "resolved"]),
    ]

    users = [f"user_{i:03d}" for i in range(1, 61)]
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    rows = []

    for _ in range(n):
        topic, text, tags = random.choice(templates)

        hours_ago = random.randint(0, 71)

        # Simulate a recent negative spike
        if hours_ago < 10 and random.random() < 0.65:
            topic, text, tags = random.choice([
                ("Pricing", "I am angry about this price increase. Customers deserve better.", ["pricing", "angry"]),
                ("Delivery", "Delivery is delayed again and there is still no clear update.", ["delivery", "delay"]),
                ("Customer support", "Very frustrated with customer support. I have been waiting for hours.", ["support", "frustrated"]),
            ])

        ts = now - timedelta(hours=hours_ago, minutes=random.randint(0, 59))
        likes = max(0, int(random.gauss(30, 24)))
        reposts = max(0, int(random.gauss(8, 9)))
        replies = max(0, int(random.gauss(6, 7)))
        followers = max(25, int(random.lognormvariate(6.2, 1.0)))

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

    return pd.DataFrame(rows).sort_values("timestamp")


# ---------- SENTIMENT AGENT ----------
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


# ---------- EMOTION AGENT ----------
@st.cache_data
def run_emotion_agent(df):
    emotion_terms = {
        "Anger": ["angry", "frustrated", "ridiculous", "furious", "unacceptable", "deserve better"],
        "Fear/Worry": ["worried", "afraid", "concerned", "risk", "may not", "uncertain"],
        "Joy": ["happy", "excellent", "great", "impressed", "love", "pleasant"],
        "Sadness/Disappointment": ["disappointing", "disappointed", "sad", "stopped working"],
        "Surprise": ["wow", "surprise", "unexpected", "did not expect"],
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


# ---------- TREND / ANOMALY AGENT ----------
def run_trend_agent(df):
    if df.empty:
        return pd.DataFrame(columns=["hour", "posts", "rolling_avg", "anomaly_score", "is_anomaly"])

    hourly = df.groupby(df["timestamp"].dt.floor("h")).size().reset_index(name="posts")
    hourly.columns = ["hour", "posts"]

    hourly["rolling_avg"] = hourly["posts"].rolling(6, min_periods=2).mean().shift(1)

    def anomaly_score(row):
        baseline = row["rolling_avg"]
        if pd.isna(baseline) or baseline <= 0:
            return 0.0
        return row["posts"] / baseline

    hourly["anomaly_score"] = hourly.apply(anomaly_score, axis=1)
    hourly["is_anomaly"] = hourly["anomaly_score"] >= 2.0
    return hourly


# ---------- RISK AGENT ----------
def run_risk_agent(df, trend_df):
    if df.empty:
        return {
            "risk": 0,
            "negative_pct": 0,
            "recent_growth_pct": 0,
            "anomaly_multiplier": 0,
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

    growth_component = max(0, min(growth_pct, 100))
    anomaly_component = max(0, min((anomaly_multiplier - 1) * 50, 100))

    risk = round(
        (0.55 * negative_pct)
        + (0.25 * growth_component)
        + (0.20 * anomaly_component)
    )
    risk = min(100, max(0, risk))

    return {
        "risk": risk,
        "negative_pct": negative_pct,
        "recent_growth_pct": growth_pct,
        "anomaly_multiplier": anomaly_multiplier,
    }


# ---------- PIPELINE / ORCHESTRATOR ----------
def run_intelligence_pipeline():
    raw = make_sample_data()
    df = run_sentiment_agent(raw)
    df = run_emotion_agent(df)
    df["engagement"] = df["likes"] + df["reposts"] + df["replies"]
    trend = run_trend_agent(df)
    risk = run_risk_agent(df, trend)
    return df, trend, risk


df, trend_df, overall_risk = run_intelligence_pipeline()


# ---------- SIDEBAR ----------
with st.sidebar:
    st.header("Filters")

    topics = sorted(df["topic_seed"].unique())
    selected_topics = st.multiselect("Topics", topics, default=topics)

    sentiments = ["Positive", "Neutral", "Negative"]
    selected_sentiments = st.multiselect(
        "Sentiment", sentiments, default=sentiments
    )

    emotions = sorted(df["emotion"].unique())
    selected_emotions = st.multiselect(
        "Emotion", emotions, default=emotions
    )

    search = st.text_input(
        "Search posts", placeholder="pricing, delivery, support..."
    )


filtered = df[
    df["topic_seed"].isin(selected_topics)
    & df["sentiment"].isin(selected_sentiments)
    & df["emotion"].isin(selected_emotions)
].copy()

if search:
    filtered = filtered[
        filtered["text"].str.contains(re.escape(search), case=False, na=False)
        | filtered["hashtags"].str.contains(re.escape(search), case=False, na=False)
    ]


filtered_trend = run_trend_agent(filtered)
risk_data = run_risk_agent(filtered, filtered_trend)


# ---------- EXECUTIVE KPIs ----------
c1, c2, c3, c4, c5 = st.columns(5)

c1.metric("Posts", f"{len(filtered):,}")
c2.metric(
    "Engagement",
    f"{int(filtered['engagement'].sum()):,}" if len(filtered) else "0",
)
c3.metric("Negative", f"{risk_data['negative_pct']:.1f}%")
c4.metric(
    "12h growth",
    f"{risk_data['recent_growth_pct']:+.0f}%",
)
c5.metric("Risk", f"{risk_data['risk']}/100")


risk = risk_data["risk"]

if risk >= 65:
    st.error("🔴 High monitoring priority")
elif risk >= 40:
    st.warning("🟠 Moderate monitoring priority")
else:
    st.success("🟢 Low monitoring priority")


# ---------- ALERT / ANOMALY ----------
anomalies = filtered_trend[filtered_trend["is_anomaly"]]

if len(anomalies):
    latest_anomaly = anomalies.sort_values("hour").iloc[-1]
    st.warning(
        f"⚠️ Trend Agent detected unusual activity: "
        f"{latest_anomaly['posts']} posts in one hour, "
        f"{latest_anomaly['anomaly_score']:.1f}× its rolling baseline."
    )


# ---------- CHARTS ----------
left, right = st.columns([2, 1])

with left:
    st.subheader("Conversation trend")
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


left, right = st.columns(2)

with left:
    st.subheader("Emotion intelligence")
    emotion_counts = (
        filtered["emotion"]
        .value_counts()
        .reset_index()
    )
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
    st.subheader("Topic intelligence")
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


# ---------- AGENT STATUS ----------
st.subheader("Agent status")
agent_status = pd.DataFrame(
    [
        ["Sentiment Agent", "Active", "Classifies positive / neutral / negative"],
        ["Emotion Agent", "Active", "Detects basic emotional signals"],
        ["Trend Agent", "Active", "Tracks volume and anomaly multipliers"],
        ["Risk Agent", "Active", "Combines negativity, growth and anomalies"],
        ["Narrative Agent", "Planned", "Build 3"],
        ["Influence Agent", "Planned", "Build 4"],
    ],
    columns=["Agent", "Status", "Role"],
)
st.dataframe(agent_status, use_container_width=True, hide_index=True)


# ---------- POST EXPLORER ----------
st.subheader("Post explorer")

display_cols = [
    "timestamp",
    "username",
    "text",
    "topic_seed",
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


with st.expander("How Build 2 works"):
    st.markdown(
        """
        This version introduces a lightweight **agent pipeline**:

        1. Sentiment Agent
        2. Emotion Agent
        3. Trend / Anomaly Agent
        4. Risk Agent

        The current emotion model is deliberately lightweight so the app
        remains fast and free. Later builds can replace it with a transformer
        model without changing the dashboard architecture.

        **Next build:** semantic topic clustering and Narrative Agent.
        """
    )
