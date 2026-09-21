# Social Media Intelligence Platform — Build 5.9 Free Monitoring

Build 5.9 adds two independent automatic email recipient groups.

## Streamlit Secrets

Add these in Streamlit Community Cloud -> App settings -> Secrets:

```toml
SMTP_EMAIL = "yourgmail@gmail.com"
SMTP_APP_PASSWORD = "your-16-character-gmail-app-password"

# Receives the normal PDF every active 30-minute monitoring cycle.
REPORT_RECIPIENTS_30MIN = "routine1@example.com,routine2@example.com"

# Receives a PDF only when negativity crosses upward through the alarm threshold.
ALERT_RECIPIENTS = "alert1@example.com,alert2@example.com"
```

The recipient settings may also be TOML arrays:

```toml
REPORT_RECIPIENTS_30MIN = [
  "routine1@example.com",
  "routine2@example.com"
]

ALERT_RECIPIENTS = [
  "alert1@example.com",
  "alert2@example.com"
]
```

## Email behavior

### 30-minute report list
Recipients in `REPORT_RECIPIENTS_30MIN` receive the PDF once per active
30-minute monitoring cycle.

### Alert-only list
Recipients in `ALERT_RECIPIENTS` receive an alert PDF only when negativity
crosses upward through the configured threshold (35% by default). They do not
receive the normal 30-minute report unless their address is also placed in
`REPORT_RECIPIENTS_30MIN`.

The same address may be present in both lists if desired.

## Important hosting limitation

The in-app 30-minute cycle runs while the Streamlit session is active.
Streamlit Community Cloud may sleep an unused app, so this is not a guaranteed
24/7 scheduler. Alert emails can only be sent when the app is actively running
and analyzing data.

## Existing features retained

- IST timestamps throughout the dashboard/PDF
- Automatic PDF generation
- Browser auto-download attempt plus manual fallback
- PDF charts and annexures
- Historic negativity
- 35% configurable negativity threshold
- Sound alarm
- X CSV + News analysis
- GDELT -> Google News RSS fallback
