# Social Media Intelligence Platform — Build 5.8 Free Monitoring

Build 5.8 adds:

- Automatic PDF generation when the loaded dataset changes
- Automatic PDF regeneration on every 30-minute monitoring cycle
- Optional zero-click browser download attempt
- Always-ready manual download fallback
- All normalized timestamps displayed/stored in India Standard Time (IST, Asia/Kolkata)
- PDF report generation time and annexure timestamps in IST
- PDF filenames include IST date/time
- Historic negativity buckets and monitoring snapshots use IST

## Browser download limitation

Browsers can block downloads that were not triggered by a user gesture,
especially on Android/iOS. The app attempts automatic download when enabled,
but this cannot be guaranteed by Streamlit. Automatic PDF generation itself
does not require a click, and the newest generated PDF remains ready in the
dashboard for download.

## Existing Build 5.7 features retained

- 30-minute active-session monitoring
- News refresh
- Historic negativity chart
- 35% configurable negativity alarm
- Sound alert
- PDF charts and annexures
- X CSV + News combined analysis
