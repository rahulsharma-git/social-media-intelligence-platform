import random
import re
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
st.caption("Version 1 — sample data, basic sentiment, trends, topics, hashtags and risk")


@st.cache_data
def make_sample_data(n=180):
    random.seed(42)

    templates = [
        ("Positive", "New launch", "Really impressed with the new product launch. Great design and smooth experience.", ["launch", "product"]),
        ("Positive", "Customer support", "Customer support resolved my issue quickly. Excellent service.", ["support", "service"]),
        ("Positive", "Product quality", "The product quality is much better than expected. Very happy with it.", ["quality", "product"]),
        ("Neutral", "Pricing", "The company announced a new pricing plan today. Need to compare the details.", ["pricing", "update"]),
        ("Neutral", "Delivery", "My order status changed this morning. Waiting for the delivery update.", ["delivery", "order"]),
        ("Neutral", "New launch", "A new version of the product was announced today.", ["launch", "news"]),
        ("Negative", "Pricing", "The new pricing is too expensive. This increase makes no sense.", ["pricing", "expensive"]),
        ("Negative", "Delivery", "Delivery is delayed again and there is still no clear update.", ["delivery", "delay"]),
        ("Negative", "Customer support", "Very frustrated with customer support. I have been waiting for hours.", ["support", "frustrated"]),
        ("Negative", "Product quality", "The product stopped working after a few days. Very disappointing.", ["quality", "problem"]),
    ]

    users = [f"user_{i:03d}" for i in range(1, 51)]
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    rows = []

    for i in range(n):
        sentiment_label, topic, text, tags = random.choice(templates)

        # Add a mild spike in negative delivery/pricing discussion in the most recent period
        hours_ago = random.randint(0, 71)
        if hours_ago < 12 and random.random() < 0.55:
            sentiment_label, topic, text, tags = random.choice([
                ("Negative", "Pricing", "The new pricing is too expensive. This increase makes no sense.", ["pricing", "expensive"]),
                ("Negative", "Delivery", "Delivery is delayed again and there is still no clear update.", ["delivery", "delay"]),
            ])

        ts = now - timedelta(hours=hours_ago, minutes=random.randint(0, 59))
        likes = max(0, int(random.gauss(28, 22)))
        reposts = max(0, int(random.gauss(7, 8)))
        replies = max(0, int(random.gauss(5, 6)))
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

    df = pd.DataFrame(rows).sort_values("timestamp")
    return df


@st.cache_data
def enrich_data(df):
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
    result["engagement"] = result["likes"] + result["reposts"] + result["replies"]
    result["hour"] = result["timestamp"].dt.floor("h")
    return result


def calculate_risk(df):
    if df.empty:
        return 0, 0.0, 0.0

    negative_pct = (df["sentiment"].eq("Negative").mean()) * 100

    midpoint = df["timestamp"].min() + (df["timestamp"].max() - df["timestamp"].min()) / 2
    early = len(df[df["timestamp"] < midpoint])
    recent = len(df[df["timestamp"] >= midpoint])

    if early == 0:
        growth_pct = 0
    else:
        growth_pct = ((recent - early) / early) * 100

    growth_component = max(0, min(growth_pct, 100))
    risk = min(100, round((0.65 * negative_pct) + (0.35 * growth_component)))

    return risk, negative_pct, growth_pct


df = enrich_data(make_sample_data())

with st.sidebar:
    st.header("Filters")
    topics = sorted(df["topic_seed"].unique())
    selected_topics = st.multiselect("Topics", topics, default=topics)

    sentiments = ["Positive", "Neutral", "Negative"]
    selected_sentiments = st.multiselect("Sentiment", sentiments, default=sentiments)

    search = st.text_input("Search posts", placeholder="pricing, delivery, support...")

filtered = df[
    df["topic_seed"].isin(selected_topics)
    & df["sentiment"].isin(selected_sentiments)
].copy()

if search:
    filtered = filtered[
        filtered["text"].str.contains(re.escape(search), case=False, na=False)
        | filtered["hashtags"].str.contains(re.escape(search), case=False, na=False)
    ]

risk, negative_pct, growth_pct = calculate_risk(filtered)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Posts", f"{len(filtered):,}")
c2.metric("Engagement", f"{int(filtered['engagement'].sum()):,}" if len(filtered) else "0")
c3.metric("Negative sentiment", f"{negative_pct:.1f}%")
c4.metric("Risk score", f"{risk}/100")

if risk >= 65:
    st.error("High monitoring priority — elevated negative discussion and/or conversation growth.")
elif risk >= 40:
    st.warning("Moderate monitoring priority — watch current conversation closely.")
else:
    st.success("Low monitoring priority — no major signal in the current sample.")

st.caption(
    "Risk score is an illustrative MVP formula based on negative sentiment and conversation growth. "
    "Later we will replace it with a calibrated multi-signal risk model."
)

left, right = st.columns([2, 1])

with left:
    st.subheader("Conversation trend")
    volume = filtered.groupby("hour").size().reset_index(name="posts")
    fig = px.line(volume, x="hour", y="posts", markers=True)
    fig.update_layout(xaxis_title=None, yaxis_title="Posts")
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
    hashtag_series = (
        filtered["hashtags"]
        .str.split()
        .explode()
        .value_counts()
        .head(10)
        .reset_index()
    )
    hashtag_series.columns = ["hashtag", "posts"]
    st.bar_chart(hashtag_series.set_index("hashtag"))

with right:
    st.subheader("Top authors by engagement")
    authors = (
        filtered.groupby("username")["engagement"]
        .sum()
        .sort_values(ascending=False)
        .head(10)
        .reset_index()
    )
    st.dataframe(authors, use_container_width=True, hide_index=True)

st.subheader("Post explorer")
display_cols = [
    "timestamp",
    "username",
    "text",
    "topic_seed",
    "sentiment",
    "sentiment_score",
    "engagement",
    "hashtags",
]
st.dataframe(
    filtered[display_cols].sort_values("timestamp", ascending=False),
    use_container_width=True,
    hide_index=True,
)

with st.expander("What comes next?"):
    st.markdown(
        """
        **Version 2**
        - relevance agent
        - stronger sentiment/emotion model
        - anomaly detection
        - topic clustering
        - narrative detection
        - Supabase persistence
        - scheduled collection
        - live X collector
        """
    )
