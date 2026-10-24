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

## Deploy on Render (free)

1. In Render choose **New > Blueprint** and pick this repo. `render.yaml`
   uses the **free** plan; no card is charged.
2. Fill in the env vars marked `sync: false` in the dashboard (clinic details,
   WhatsApp keys). `SECRET_KEY` and `CRON_SECRET` are generated for you.
3. Free services sleep after 15 minutes idle, which also pauses reminders.
   Create a free job at cron-job.org that calls
   `https://<your-app>.onrender.com/tasks/send-reminders?key=<CRON_SECRET>`
   every 10 minutes.
4. The site is live at `https://<your-app>.onrender.com`. A custom domain can
   be added later under **Settings > Custom Domains** (the domain itself costs
   money at a registrar).

**Free plan limit:** the disk is wiped on every deploy or restart, so the
database resets. Use it for testing and demos only.

### Going live (paid, when you're ready)

Change `plan: free` to `plan: starter` (about $7/month) and add a persistent disk
(about $0.25/GB/month) so the database survives:

```yaml
    disk:
      name: carebly-data
      mountPath: /var/data
      sizeGB: 1
```

Then set `DATABASE_PATH=/var/data/carebly.db`. Render snapshots the disk daily.
Run only one instance, because the reminder job runs inside the web process.

### WhatsApp costs

Meta's test number is free and can message up to 5 numbers you verify, which
is enough for testing. For real patients, replies to a patient within 24 hours
of their message are free, but confirmation and reminder templates are billed
per message by Meta (a small amount per message in India).

## WhatsApp setup (Meta Cloud API)

1. Create a Meta Business account at business.facebook.com and verify the business.
2. At developers.facebook.com create an app of type **Business** and add the
   **WhatsApp** product.
3. For testing, use the free test number Meta gives you in **WhatsApp > API Setup**
   and add up to 5 recipient numbers there. For real use, add the clinic's phone number. It must not be registered on the WhatsApp
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
