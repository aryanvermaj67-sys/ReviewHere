"""Subscriptions and one-off tag orders.

Two separate money flows, deliberately kept apart:

  * **Tag orders** - a one-time purchase of physical stickers, priced per tag.
    A business can order tags at any time, trial or not, because that is hardware
    they are buying from you.
  * **Subscription** - free for the first `TRIAL_DAYS`, then monthly. When it
    lapses the dashboard locks, but review collection never stops: the guest pages
    keep working and the data keeps arriving. Punishing a business's customers for
    the business's unpaid invoice would be both cruel and bad for conversion - the
    waiting reviews are the reason to pay.

Amounts are integers in paise everywhere. No floats touch money.
"""
import base64
import hashlib
import hmac
import json
import math
from datetime import datetime, timedelta, timezone
from urllib import error, request

from . import config, db

SUBSCRIBED_STATES = {"active", "authenticated"}


# --------------------------------------------------------------------------- #
# money formatting
# --------------------------------------------------------------------------- #
def rupees(paise: int) -> str:
    """1234500 -> '12,345'. Indian grouping, no decimals when they are all zero."""
    whole, part = divmod(int(paise), 100)
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    return digits if part == 0 else "%s.%02d" % (digits, part)


# --------------------------------------------------------------------------- #
# entitlement
# --------------------------------------------------------------------------- #
def _parse(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def status(business) -> dict:
    """Everything the interface needs to know about this account's standing."""
    now = datetime.now(timezone.utc)
    plan = business["plan"] if "plan" in business.keys() else "trial"
    trial_end = _parse(business["trial_ends_at"] if "trial_ends_at" in business.keys() else "")
    sub_state = (business["subscription_status"] or "").lower() if "subscription_status" in business.keys() else ""

    paying = plan == "active" and sub_state in SUBSCRIBED_STATES
    in_trial = plan == "trial" and trial_end is not None and now < trial_end
    days_left = 0
    if in_trial and trial_end:
        # Round up: with 6 hours left a business is still "on their last day".
        days_left = max(0, math.ceil((trial_end - now).total_seconds() / 86400))

    return {
        "plan": plan,
        "paying": paying,
        "in_trial": in_trial,
        "active": paying or in_trial,
        "trial_days_left": days_left,
        "trial_ends_at": business["trial_ends_at"] if "trial_ends_at" in business.keys() else "",
        "subscription_status": sub_state,
        "period_end": business["period_end"] if "period_end" in business.keys() else "",
        "price": rupees(config.SUBSCRIPTION_PRICE_PAISE),
        "tag_price": rupees(config.TAG_PRICE_PAISE),
        "payments_configured": config.PAYMENTS_CONFIGURED,
    }


def trial_end_stamp() -> str:
    end = datetime.now(timezone.utc) + timedelta(days=config.TRIAL_DAYS)
    return end.strftime("%Y-%m-%d %H:%M:%S")


def expire_lapsed_trials(conn) -> int:
    """Flip trials whose clock has run out. Cheap enough to run on dashboard load."""
    cur = conn.execute(
        """UPDATE businesses SET plan = 'expired'
            WHERE plan = 'trial' AND trial_ends_at != ''
              AND trial_ends_at <= datetime('now')"""
    )
    return cur.rowcount


# --------------------------------------------------------------------------- #
# Razorpay - plain HTTPS, no SDK
# --------------------------------------------------------------------------- #
API = "https://api.razorpay.com/v1"


class PaymentError(RuntimeError):
    pass


def _call(method: str, path: str, payload: dict | None = None) -> dict:
    if not config.PAYMENTS_CONFIGURED:
        raise PaymentError("Payments are not configured on this server.")
    body = json.dumps(payload).encode() if payload is not None else None
    req = request.Request(API + path, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    credentials = "%s:%s" % (config.RAZORPAY_KEY_ID, config.RAZORPAY_KEY_SECRET)
    req.add_header("Authorization", "Basic " + base64.b64encode(credentials.encode()).decode())
    try:
        with request.urlopen(req, timeout=20) as response:
            return json.loads(response.read())
    except error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:400]
        raise PaymentError("Razorpay said %s: %s" % (exc.code, detail)) from exc
    except OSError as exc:
        raise PaymentError("Could not reach Razorpay: %s" % exc) from exc


def create_tag_order(order_id: int, quantity: int, amount_paise: int) -> dict:
    """A one-time order for physical tags."""
    return _call("POST", "/orders", {
        "amount": amount_paise,
        "currency": config.CURRENCY,
        "receipt": "tags-%d" % order_id,
        "notes": {"kind": "tags", "quantity": str(quantity), "local_order_id": str(order_id)},
    })


def create_subscription(business_id: int, email: str) -> dict:
    """A recurring subscription against the plan configured in Razorpay."""
    if not config.RAZORPAY_PLAN_ID:
        raise PaymentError("No subscription plan id configured (NFC_RAZORPAY_PLAN_ID).")
    return _call("POST", "/subscriptions", {
        "plan_id": config.RAZORPAY_PLAN_ID,
        "total_count": 120,          # ten years of monthly cycles
        "customer_notify": 1,
        "notes": {"business_id": str(business_id), "email": email},
    })


def verify_payment_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """Checkout hands back a signature over 'order_id|payment_id'."""
    if not config.RAZORPAY_KEY_SECRET:
        return False
    expected = hmac.new(
        config.RAZORPAY_KEY_SECRET.encode(),
        ("%s|%s" % (order_id, payment_id)).encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def verify_webhook(raw_body: bytes, signature: str) -> bool:
    """Webhooks are signed with their own secret, over the exact raw body."""
    if not config.RAZORPAY_WEBHOOK_SECRET:
        return False
    expected = hmac.new(
        config.RAZORPAY_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")


# --------------------------------------------------------------------------- #
# applying results
# --------------------------------------------------------------------------- #
def mark_order_paid(conn, order_row_id: int, payment_id: str) -> None:
    db.execute(
        conn,
        """UPDATE orders SET status = 'paid', provider_payment_id = ?,
                             paid_at = datetime('now')
            WHERE id = ? AND status != 'paid'""",
        (payment_id[:60], order_row_id),
    )


def activate_subscription(conn, business_id: int, subscription_id: str,
                          state: str, period_end: str = "") -> None:
    plan = "active" if state.lower() in SUBSCRIBED_STATES else "expired"
    db.execute(
        conn,
        """UPDATE businesses
              SET plan = ?, subscription_id = ?, subscription_status = ?, period_end = ?
            WHERE id = ?""",
        (plan, subscription_id[:60], state.lower()[:30], period_end[:30], business_id),
    )


def seen_event(conn, event_id: str, event_type: str, payload: str) -> bool:
    """True if this webhook was already processed. Records it if not."""
    if not event_id:
        return False
    if db.one(conn, "SELECT id FROM billing_events WHERE event_id = ?", (event_id,)):
        return True
    db.execute(
        conn,
        "INSERT INTO billing_events (event_id, event_type, payload) VALUES (?,?,?)",
        (event_id[:120], event_type[:60], payload[:4000]),
    )
    return False
