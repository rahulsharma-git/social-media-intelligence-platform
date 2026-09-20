# Social Media Intelligence Platform — Build 3

Build 3 adds automatic semantic clustering and Narrative Intelligence.

## Active agents

- Sentiment Agent
- Emotion Agent
- Semantic Topic Agent
- Narrative Agent
- Trend / Anomaly Agent
- Risk Agent

## Semantic approach

This build stays lightweight and free:

1. TF-IDF text representation
2. Truncated SVD for latent semantic analysis
3. Vector normalization
4. KMeans conversation clustering
5. Narrative extraction from cluster terms and representative posts

This avoids large transformer models so the app remains suitable for a free
Streamlit Community Cloud deployment.

## Run locally

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Next planned build

- Influence Agent
- author influence scoring
- interaction/network model
- community intelligence
