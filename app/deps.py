"""Shared helpers: template environment, current-user lookup, small formatters."""
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import auth, billing, business_types, config, db

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def stars(value) -> str:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return ""
    return "★" * n + "☆" * (5 - n)


def rating_word(value) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    if n >= 4.5:
        return "Excellent"
    if n >= 4.0:
        return "Good"
    if n >= 3.0:
        return "Mixed"
    if n >= 2.0:
        return "Poor"
    return "Bad"


def localdt(value, tz_offset: int = 0) -> str:
    """Render a stored UTC timestamp in the business's own local time."""
    if not value:
        return ""
    try:
        moment = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(value)
    return (moment + timedelta(minutes=tz_offset or 0)).strftime("%d %b %Y, %H:%M")


templates.env.filters["stars"] = stars
templates.env.filters["rating_word"] = rating_word
templates.env.filters["localdt"] = localdt


def vocab(business_type):
    """Wording for this kind of business - what a tag is stuck to, what the four
    rating categories are called, and what its customers are called."""
    return business_types.preset(business_type)


templates.env.globals["vocab"] = vocab
templates.env.globals["billing_status"] = billing.status
templates.env.globals["business_type_choices"] = business_types.choices


def base_url(request: Request) -> str:
    """The address to hand out in links and burn onto tags.

    A configured public URL always wins - behind a proxy the request host can be
    an internal name, and a wrong URL on a printed tag is expensive to fix.
    """
    if config.PUBLIC_BASE_URL:
        return config.PUBLIC_BASE_URL
    return str(request.base_url).rstrip("/")


def current_user(request: Request):
    """Returns the logged-in user row joined with their business, or None."""
    session = auth.session_from_request(request)
    if not session:
        return None
    with db.get_db() as conn:
        return db.one(
            conn,
            """SELECT u.id, u.email, u.name, u.business_id,
                      r.slug, r.name AS business_name, r.google_review_url,
                      r.positive_threshold, r.accent, r.tagline, r.logo_url,
                      r.tz_offset, r.business_type, r.plan, r.trial_ends_at,
                      r.subscription_id, r.subscription_status, r.period_end
                 FROM users u JOIN businesses r ON r.id = u.business_id
                WHERE u.id = ?""",
            (session.get("uid"),),
        )


# Pages an account can still reach once its subscription has lapsed. Everything
# else redirects to billing - but note that guest review pages are public and are
# never affected, so a lapsed account keeps collecting reviews.
ALWAYS_ALLOWED = ("/dashboard/billing", "/dashboard/settings", "/logout")


def require_user(request: Request):
    user = current_user(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/login?next=" + str(request.url.path)},
        )
    standing = billing.status(user)
    if not standing["active"] and not request.url.path.startswith(ALWAYS_ALLOWED):
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/dashboard/billing?locked=1"},
        )
    return user


def require_active(user):
    """For actions that must not happen on a lapsed account, like minting new tags."""
    if not billing.status(user)["active"]:
        raise HTTPException(status_code=402, detail="Subscription required")
    return user


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)
