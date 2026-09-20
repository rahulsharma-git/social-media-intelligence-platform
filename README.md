# Build 5.4 Free Stable

This build explicitly fixes the Android CSV workflow while preserving the
working free-news workflow.

## CSV
1. Choose file
2. Tap **Load & analyze CSV**
3. The normalized dataframe is stored in Streamlit session state
4. You immediately see row count, engagement totals and preview
5. Dashboard analyzes the active dataset

There is also a **Test built-in CSV** button. If that works, the app's CSV
pipeline is healthy even before choosing a phone file.

## News
GDELT remains primary and Google News RSS remains the automatic free fallback.

## Version check
The app visibly says:
`Build 5.4 Free Stable — VERIFIED CSV + News dual-source build`
