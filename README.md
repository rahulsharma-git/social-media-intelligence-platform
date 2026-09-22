# Social Media Intelligence Platform — Build 5.12

This hotfix addresses multi-recipient email delivery.

## What changed

- Sends one independent Gmail message per recipient.
- Uses an explicit SMTP envelope (`to_addrs=[recipient]`) for each address.
- A failure for recipient 2 no longer hides behind a successful send to recipient 1.
- Successful recipients are tracked per 30-minute slot / alert event.
- Retries only the recipients that have not yet succeeded, so recipient 1 is not spammed again when recipient 2 needs a retry.
- Dashboard shows parsed recipient counts, delivered recipient addresses, and SMTP error details.
- Existing PDF + Web News evidence ZIP attachments are retained.
- Existing fixed IST half-hour scheduling and ~5-minute failure retry are retained.

## Recommended Streamlit Secrets syntax

TOML arrays are the clearest option:

```toml
SMTP_EMAIL = "yourgmail@gmail.com"
SMTP_APP_PASSWORD = "your-16-character-app-password"

REPORT_RECIPIENTS_30MIN = [
  "routine1@example.com",
  "routine2@example.com"
]

ALERT_RECIPIENTS = [
  "alert1@example.com",
  "alert2@example.com"
]
```

Comma-separated strings still work:

```toml
REPORT_RECIPIENTS_30MIN = "routine1@example.com,routine2@example.com"
ALERT_RECIPIENTS = "alert1@example.com,alert2@example.com"
```

Do not use a normal Gmail password.

## Hosting limitation

Streamlit Community Cloud may sleep an inactive app. Email scheduling inside
the app cannot execute while the app is asleep.
