# Social Media Intelligence Platform - Build 5.7 Free Monitoring

Build 5.7 retains the stable X/CSV and free-News workflows and adds:

- Charts embedded in the downloadable PDF report
- More dashboard charts
- 30-minute automatic active-session refresh
- Automatic News refresh every 30 minutes when News/Combined mode is active
- Historic negativity plot in 30-minute buckets
- In-session 30-minute negativity snapshots
- Configurable negativity threshold (default 35%)
- Sound alarm once when negativity crosses above the threshold
- Alarm re-arms after negativity falls back to or below the threshold

## PDF charts

The report now includes:

- Sentiment distribution
- Historic negativity trend with threshold
- Conversation/coverage volume over time
- Sentiment mix by source
- Emotion signals
- Semantic cluster volume
- Negative share by cluster
- Top sources/authors
- Semantic conversation map

The existing Top-10 briefs, methodology, cluster legend, X annexure and News annexure are retained.

## 30-minute automation limitation

`streamlit-autorefresh` reruns the active browser/app session every 30 minutes.
On Streamlit Community Cloud the app can sleep when nobody is using it, so this
is not a guaranteed 24/7 background scheduler. A separate scheduler would be
needed for unattended monitoring while the app is asleep.

## Sound alert

The alarm is generated locally as a WAV tone and played in the browser. Some
mobile browsers block autoplay until the user has interacted with the page.
Use **Test alarm sound** once after opening the app if needed.
