"""Meta WhatsApp Cloud API client.

Business-initiated messages (confirmations, reminders) must use templates
approved in WhatsApp Manager. Free-form text is only allowed within 24 hours
of the patient's last message, so it is used for replies to the patient.
"""
import hashlib
import hmac
import logging
import re

import requests

log = logging.getLogger(__name__)

GRAPH_URL = "https://graph.facebook.com/{version}/{phone_number_id}/messages"


def normalize_phone(raw, default_country_code="91"):
    """Return digits-only E.164 (no '+'), or None if it doesn't look valid."""
    if not raw:
        return None
    raw = raw.strip()
    has_plus = raw.startswith("+") or raw.startswith("00")
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("00"):
        digits = digits[2:]
    if not has_plus:
        digits = digits.lstrip("0")
        if len(digits) <= 10:
            digits = default_country_code + digits
    if not 8 <= len(digits) <= 15:
        return None
    return digits


class WhatsAppClient:
    def __init__(self, token, phone_number_id, api_version="v21.0", lang="en", timeout=10):
        self.token = token
        self.phone_number_id = phone_number_id
        self.api_version = api_version
        self.lang = lang
        self.timeout = timeout

    @property
    def enabled(self):
        return bool(self.token and self.phone_number_id)

    def _post(self, payload):
        """Send a payload. Returns (status, wa_message_id, error)."""
        if not self.enabled:
            log.info("WhatsApp not configured, would send: %s", payload)
            return "logged", None, None
        url = GRAPH_URL.format(version=self.api_version, phone_number_id=self.phone_number_id)
        try:
            resp = requests.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=self.timeout,
            )
            data = resp.json() if resp.content else {}
        except (requests.RequestException, ValueError) as exc:
            log.warning("WhatsApp send failed: %s", exc)
            return "failed", None, str(exc)
        if resp.status_code >= 400:
            error = data.get("error", {}).get("message") or resp.text[:500]
            log.warning("WhatsApp API error %s: %s", resp.status_code, error)
            return "failed", None, error
        msg_id = (data.get("messages") or [{}])[0].get("id")
        return "sent", msg_id, None

    def send_template(self, to, template_name, params=()):
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": self.lang},
            },
        }
        if params:
            payload["template"]["components"] = [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": str(p)} for p in params],
                }
            ]
        return self._post(payload)

    def send_text(self, to, text):
        return self._post(
            {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": text},
            }
        )


def verify_signature(app_secret, raw_body, header_value):
    """Check Meta's X-Hub-Signature-256 header against the raw request body."""
    if not app_secret:
        return False
    if not header_value or not header_value.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_value.split("=", 1)[1])


def parse_webhook(payload):
    """Flatten a webhook payload into (messages, statuses).

    messages: list of {"from", "id", "text"} where text is the typed text or
              the pressed button's payload/title.
    statuses: list of {"id", "status"} delivery updates for sent messages.
    """
    messages, statuses = [], []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for m in value.get("messages", []):
                text = ""
                mtype = m.get("type")
                if mtype == "text":
                    text = m.get("text", {}).get("body", "")
                elif mtype == "button":
                    btn = m.get("button", {})
                    text = btn.get("payload") or btn.get("text", "")
                elif mtype == "interactive":
                    inter = m.get("interactive", {})
                    reply = inter.get("button_reply") or inter.get("list_reply") or {}
                    text = reply.get("id") or reply.get("title", "")
                messages.append({"from": m.get("from", ""), "id": m.get("id"), "text": text, "type": mtype})
            for s in value.get("statuses", []):
                statuses.append({"id": s.get("id"), "status": s.get("status")})
    return messages, statuses


CONFIRM_WORDS = {"1", "yes", "y", "confirm", "confirmed", "ok", "haan", "ha"}
CANCEL_WORDS = {"2", "no", "n", "cancel", "cancelled", "nahi"}


def classify_reply(text):
    t = (text or "").strip().lower()
    if t in CONFIRM_WORDS:
        return "confirm"
    if t in CANCEL_WORDS:
        return "cancel"
    return None
