# Social Media Intelligence Platform — Build 5.11

Build 5.11 fixes periodic email timing logic.

## What was wrong in Build 5.10

The scheduler used a 30-minute browser-session timer (`st_autorefresh`) and
identified email cycles by its counter. That means the schedule drifted from
whenever the browser session started. A failed send also had no fast automatic
retry because the next automatic rerun could be 30 minutes later. Closing the
browser or a sleeping Streamlit Community Cloud app stops all in-app scheduling.

## Build 5.11 changes

- 60-second lightweight scheduler heartbeat while the app is awake.
- Work is keyed to fixed IST half-hour slots: :00 and :30.
- Main PDF is generated once per half-hour slot, not once per heartbeat.
- Routine email is sent once per IST half-hour slot.
- Failed routine or alert sends retry after about 5 minutes.
- News refresh is attempted once per slot; failed refreshes retry after about 5 minutes.
- Email health panel shows current slot, next slot, send attempts, errors, and news refresh status.
- Existing main PDF + numbered Web News evidence ZIP attachments are retained.
- All timestamps remain IST.

## Important limitation

This makes an *awake Streamlit session* much more reliable, but it does not turn
Streamlit Community Cloud into a guaranteed background scheduler. If the app is
asleep or there is no active session, scheduled code may not run. For guaranteed
24/7 :00/:30 delivery, move the scheduler/email job to a real external scheduler
(e.g. GitHub Actions or another always-on/scheduled worker) and use Streamlit only
as the dashboard.

## Secrets

```toml
SMTP_EMAIL = "yourgmail@gmail.com"
SMTP_APP_PASSWORD = "your-app-password"
REPORT_RECIPIENTS_30MIN = "routine1@example.com,routine2@example.com"
ALERT_RECIPIENTS = "alert1@example.com,alert2@example.com"
```
