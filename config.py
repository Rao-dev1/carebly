import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


class Config:
    # Required in production. A random key is generated per process if unset,
    # which logs everyone out on every restart, so always set it when deployed.
    SECRET_KEY = os.environ.get("SECRET_KEY") or os.urandom(32).hex()
    # Code needed to register an admin account. If unset, admin signup is
    # only allowed while no admin exists yet (first-time setup).
    ADMIN_SIGNUP_CODE = os.environ.get("ADMIN_SIGNUP_CODE", "")

    DATABASE = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "carebly.db"))

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)

    # Background reminder job. Run it in exactly one process.
    ENABLE_SCHEDULER = _bool("ENABLE_SCHEDULER", False)
    REMINDER_HOURS_BEFORE = int(os.environ.get("REMINDER_HOURS_BEFORE", "24"))

    # Country code prepended to local numbers patients type in (India = 91).
    DEFAULT_COUNTRY_CODE = os.environ.get("DEFAULT_COUNTRY_CODE", "91")

    # Meta WhatsApp Cloud API. Messages are only logged until these are set.
    WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
    WHATSAPP_APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "")
    WHATSAPP_API_VERSION = os.environ.get("WHATSAPP_API_VERSION", "v21.0")
    WHATSAPP_TEMPLATE_LANG = os.environ.get("WHATSAPP_TEMPLATE_LANG", "en")

    # Public website details.
    CLINIC_NAME = os.environ.get("CLINIC_NAME", "Carebly Clinic")
    CLINIC_TAGLINE = os.environ.get("CLINIC_TAGLINE", "Caring for your family's health")
    CLINIC_ADDRESS = os.environ.get("CLINIC_ADDRESS", "")
    CLINIC_PHONE = os.environ.get("CLINIC_PHONE", "")
    CLINIC_EMAIL = os.environ.get("CLINIC_EMAIL", "")
    CLINIC_HOURS = os.environ.get("CLINIC_HOURS", "Mon-Sat: 10:00 - 13:00, 17:00 - 20:00")
    CLINIC_MAP_EMBED_URL = os.environ.get("CLINIC_MAP_EMBED_URL", "")
    # WhatsApp number shown on the website for "Chat with us", digits only.
    CLINIC_WHATSAPP = os.environ.get("CLINIC_WHATSAPP", "")
