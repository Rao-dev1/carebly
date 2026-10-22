# Carebly

Clinic website and appointment system: a public website for the clinic, patient
booking, a doctor workflow (approve, visit notes, prescriptions, follow-ups),
an admin panel, and WhatsApp confirmations and reminders through Meta's
WhatsApp Cloud API.

## Run locally

```bash
python -m venv venv && . venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # edit values, then:
set -a; . ./.env; set +a
flask --app app run --debug     # http://127.0.0.1:5000
pytest                          # run the tests
```

Tables are created automatically on startup (`flask --app app init-db` also works).

**First login:** open `/register/admin` and create the first admin. After that,
admin signup is closed unless `ADMIN_SIGNUP_CODE` is set. Admins add doctors
from **Users > Add doctor**; patients register themselves from the website.

## Deploy on Render

1. Push this repo to GitHub, then in Render choose **New > Blueprint** and pick it.
   `render.yaml` creates a web service with a 1 GB persistent disk for the
   SQLite database (Starter plan, about $7/month plus $0.25/GB for the disk).
2. Fill in the env vars marked `sync: false` in the dashboard (clinic details,
   WhatsApp keys). `SECRET_KEY` is generated for you.
3. Custom domain: **Settings > Custom Domains**, then add the DNS record Render
   shows at your domain registrar. HTTPS is automatic.
4. Backups: Render snapshots the disk daily. You can also download
   `/var/data/carebly.db` from the Render shell.

Run only one instance: the reminder job runs inside the web process.

## WhatsApp setup (Meta Cloud API)

1. Create a Meta Business account at business.facebook.com and verify the business.
2. At developers.facebook.com create an app of type **Business** and add the
   **WhatsApp** product.
3. Add the clinic's phone number. It must not be registered on the WhatsApp
   or WhatsApp Business app (delete it there first, or use a new number).
4. Create a **System User** in Business Settings, give it the app and the
   WhatsApp account, and generate a permanent token with
   `whatsapp_business_messaging` and `whatsapp_business_management`.
   This is `WHATSAPP_TOKEN`.
5. Copy the **Phone number ID** (not the phone number) into
   `WHATSAPP_PHONE_NUMBER_ID`, and the app's **App secret** (App settings >
   Basic) into `WHATSAPP_APP_SECRET`.
6. Webhook: in the app's WhatsApp > Configuration, set the callback URL to
   `https://<your-domain>/webhooks/whatsapp`, pick any random string as the
   verify token and put the same value in `WHATSAPP_VERIFY_TOKEN`. Subscribe
   to the `messages` field.
7. Create these **Utility** message templates in WhatsApp Manager, language
   English (`en`). Each body uses `{{1}}` patient name, `{{2}}` doctor name,
   `{{3}}` date and time:

| Template name | Suggested body |
| --- | --- |
| `appointment_received` | Hi {{1}}, we have received your appointment request with {{2}} for {{3}}. We will confirm it shortly. |
| `appointment_confirmed` | Hi {{1}}, your appointment with {{2}} on {{3}} is confirmed. Reply 2 if you need to cancel. |
| `appointment_rejected` | Hi {{1}}, sorry, {{2}} is not available on {{3}}. Please book another time on our website. |
| `appointment_reminder` | Reminder: Hi {{1}}, you have an appointment with {{2}} on {{3}}. Reply 1 to confirm or 2 to cancel. |

Until the WhatsApp variables are set, messages are only written to the log and
to the `whatsapp_messages` table, so the app works without WhatsApp.

Patients reply **1** (or yes/confirm) to confirm and **2** (or no/cancel) to
cancel their next upcoming appointment.

## Configuration

All settings are environment variables; see `.env.example` and `config.py`.
