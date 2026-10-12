"""Environment-driven settings. Everything deployment-specific lives here."""
import os
import secrets
import sys

ENV = os.environ.get("NFC_ENV", "development").lower()
IS_PRODUCTION = ENV == "production"

# Where SQLite lives. On Fly this points at the mounted volume (/data/nfc.db).
DB_PATH = os.environ.get("NFC_DB_PATH", "")

# Signs session cookies. A generated-per-boot secret is fine locally (it just logs
# everyone out on restart) but in production it MUST be set, or every deploy would
# invalidate sessions and two machines would reject each other's cookies.
SECRET_KEY = os.environ.get("NFC_SECRET_KEY", "")
if not SECRET_KEY:
    if IS_PRODUCTION:
        sys.exit(
            "NFC_SECRET_KEY is not set. Generate one with:\n"
            "  python -c \"import secrets; print(secrets.token_urlsafe(48))\"\n"
            "then set it with:  fly secrets set NFC_SECRET_KEY=..."
        )
    SECRET_KEY = "dev-" + secrets.token_urlsafe(16)

# Canonical public address, e.g. https://reviewhere.in - used for the links written
# onto tags and shown in the dashboard. Without it we fall back to the request host,
# which is right in development and usually right behind a proxy.
PUBLIC_BASE_URL = os.environ.get("NFC_PUBLIC_BASE_URL", "").rstrip("/")

# Cookies must be https-only in production, but that would break plain-http local dev.
COOKIE_SECURE = IS_PRODUCTION

# Set to "1" to close public signup (useful if you ever want invite-only onboarding).
SIGNUP_CLOSED = os.environ.get("NFC_SIGNUP_CLOSED", "") == "1"

# Support address shown to businesses on the signup page.
SUPPORT_EMAIL = os.environ.get("NFC_SUPPORT_EMAIL", "")

# Outgoing mail, used only for password resets. Plain SMTP so any provider works
# (Resend, Brevo, Zoho, Gmail app password) without adding a dependency or locking
# you to one vendor. Leave unset and reset links are written to the server log
# instead, which is workable while you are the only account holder.
SMTP_HOST = os.environ.get("NFC_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("NFC_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("NFC_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("NFC_SMTP_PASSWORD", "")
MAIL_FROM = os.environ.get("NFC_MAIL_FROM", "")
MAIL_CONFIGURED = bool(SMTP_HOST and MAIL_FROM)

# --------------------------------------------------------------------------- #
# billing
# --------------------------------------------------------------------------- #
# Everything is stored and sent to Razorpay in paise, because floats and money
# do not mix. 30000 paise = Rs 300.
TAG_PRICE_PAISE = int(os.environ.get("NFC_TAG_PRICE_PAISE", "30000"))
SUBSCRIPTION_PRICE_PAISE = int(os.environ.get("NFC_SUBSCRIPTION_PRICE_PAISE", "49900"))
TRIAL_DAYS = int(os.environ.get("NFC_TRIAL_DAYS", "7"))
CURRENCY = os.environ.get("NFC_CURRENCY", "INR")

# Razorpay. Keys come from the dashboard; the webhook secret is set when you create
# the webhook. Without these, billing runs in "not configured" mode: the pages still
# render and you can activate an account by hand, but no payment can be taken.
RAZORPAY_KEY_ID = os.environ.get("NFC_RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("NFC_RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("NFC_RAZORPAY_WEBHOOK_SECRET", "")
RAZORPAY_PLAN_ID = os.environ.get("NFC_RAZORPAY_PLAN_ID", "")
PAYMENTS_CONFIGURED = bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)
