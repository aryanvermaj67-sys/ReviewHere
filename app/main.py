"""NFC review system - public routes (tap target, review capture, login, scan API)."""
import io
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import qrcode
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, billing, business_types, config, db, limits, mailer
from .billing_routes import router as billing_router
from .dashboard import router as dashboard_router
from .deps import base_url, current_user, redirect, templates

BASE_DIR = Path(__file__).resolve().parent

@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="ReviewHere", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(dashboard_router)
app.include_router(billing_router)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _tag_context(conn, token: str):
    tag = db.one(conn, "SELECT * FROM tags WHERE token = ?", (token,))
    if tag is None or not tag["active"]:
        return None, None
    business = db.one(conn, "SELECT * FROM businesses WHERE id = ?", (tag["business_id"],))
    return tag, business


def _clamp(value, low=1, high=5):
    """Form ratings arrive as strings; anything unusable becomes None."""
    if value in (None, "", "0"):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return max(low, min(high, n))


def _guard_review(request: Request, tag_id) -> None:
    """Keep a public form from being turned into a firehose.

    Per-IP is deliberately loose: a whole business often shares one wifi address,
    so the tighter ceiling is per table, which is what a spam run would hammer.
    """
    ip = limits.client_ip(request)
    if not limits.allow("review-ip:" + ip, limit=30, window_seconds=3600):
        raise HTTPException(status_code=429, detail="Too many reviews from this network.")
    if tag_id is not None and not limits.allow("review-tag:%s" % tag_id, limit=15, window_seconds=3600):
        raise HTTPException(status_code=429, detail="This table has had too many reviews this hour.")


def _save_review(conn, business, tag_id, form: dict) -> int:
    rating = _clamp(form.get("rating"))
    if rating is None:
        raise HTTPException(status_code=400, detail="A star rating is required.")
    if (form.get("website") or "").strip():
        raise HTTPException(status_code=400, detail="Rejected.")
    # Who gets invited to post publicly.
    #   open   - everyone, which is what Google's policy expects.
    #   gated  - only ratings at or above the threshold. Higher conversion, but this
    #            is "review gating" and Google prohibits it.
    # Either way the private feedback below is captured in full.
    has_link = bool(business["google_review_url"])
    if business["routing"] == "gated":
        routed_public = int(has_link and rating >= business["positive_threshold"])
    else:
        routed_public = int(has_link)
    return db.execute(
        conn,
        """INSERT INTO reviews (business_id, tag_id, rating, quality, service, environment,
                                value, comment, customer_name, customer_contact,
                                routed_public)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            business["id"],
            tag_id,
            rating,
            _clamp(form.get("quality")),
            _clamp(form.get("service")),
            _clamp(form.get("environment")),
            _clamp(form.get("value")),
            (form.get("comment") or "").strip()[:2000],
            (form.get("customer_name") or "").strip()[:80],
            (form.get("customer_contact") or "").strip()[:120],
            routed_public,
        ),
    )


@app.middleware("http")
async def _no_index_guest_pages(request: Request, call_next):
    """Guest review pages carry customer comments - they should never be indexed."""
    response = await call_next(request)
    if request.url.path.startswith(("/t/", "/r/", "/thanks/", "/dashboard")):
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return templates.TemplateResponse(
        request, "privacy.html", {"support": config.SUPPORT_EMAIL}
    )


@app.get("/robots.txt", response_class=Response)
def robots():
    lines = ["User-agent: *", "Disallow: /t/", "Disallow: /r/", "Disallow: /thanks/",
             "Disallow: /dashboard", ""]
    return Response("\n".join(lines), media_type="text/plain")


# --------------------------------------------------------------------------- #
# landing
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    with db.get_db() as conn:
        demo = db.one(conn, "SELECT * FROM businesses ORDER BY id LIMIT 1")
        demo_tag = None
        if demo:
            demo_tag = db.one(
                conn,
                "SELECT * FROM tags WHERE business_id = ? AND active = 1 ORDER BY id LIMIT 1",
                (demo["id"],),
            )
    return templates.TemplateResponse(request, "landing.html",
        {"user": current_user(request), "demo": demo, "demo_tag": demo_tag},
    )


# --------------------------------------------------------------------------- #
# the tap target: what the NFC tag encodes -> /t/<token>
# --------------------------------------------------------------------------- #
@app.get("/t/{token}", response_class=HTMLResponse)
def tap(request: Request, token: str):
    with db.get_db() as conn:
        tag, business = _tag_context(conn, token)
        if tag is None:
            return templates.TemplateResponse(request, "invalid.html", {}, status_code=404)
        db.execute(
            conn,
            "INSERT INTO scans (tag_id, source, user_agent) VALUES (?,?,?)",
            (tag["id"], "web", request.headers.get("user-agent", "")[:300]),
        )
    # Direct mode: the tag is a shortcut to the public review page. The tap is still
    # counted, but nothing is asked and nothing is stored about this customer.
    if business["tap_mode"] == "direct" and business["google_review_url"]:
        return RedirectResponse(business["google_review_url"], status_code=303)
    return templates.TemplateResponse(request, "review.html",
        {"business": business, "tag": tag, "action": "/t/" + token},
    )


@app.post("/t/{token}")
async def tap_submit(request: Request, token: str):
    form = dict(await request.form())
    with db.get_db() as conn:
        tag, business = _tag_context(conn, token)
        if tag is None:
            raise HTTPException(status_code=404, detail="Unknown tag")
        _guard_review(request, tag["id"])
        review_id = _save_review(conn, business, tag["id"], form)
    return redirect("/thanks/%d" % review_id)


# --------------------------------------------------------------------------- #
# business-level review page (printed QR, website link, no specific table)
# --------------------------------------------------------------------------- #
@app.get("/r/{slug}", response_class=HTMLResponse)
def review_by_slug(request: Request, slug: str):
    with db.get_db() as conn:
        business = db.one(conn, "SELECT * FROM businesses WHERE slug = ?", (slug,))
    if business is None:
        return templates.TemplateResponse(request, "invalid.html", {}, status_code=404)
    if business["tap_mode"] == "direct" and business["google_review_url"]:
        return RedirectResponse(business["google_review_url"], status_code=303)
    return templates.TemplateResponse(request, "review.html",
        {"business": business, "tag": None, "action": "/r/" + slug},
    )


@app.post("/r/{slug}")
async def review_by_slug_submit(request: Request, slug: str):
    form = dict(await request.form())
    with db.get_db() as conn:
        business = db.one(conn, "SELECT * FROM businesses WHERE slug = ?", (slug,))
        if business is None:
            raise HTTPException(status_code=404, detail="Unknown business")
        _guard_review(request, None)
        review_id = _save_review(conn, business, None, form)
    return redirect("/thanks/%d" % review_id)


@app.get("/thanks/{review_id}", response_class=HTMLResponse)
def thanks(request: Request, review_id: int):
    with db.get_db() as conn:
        review = db.one(conn, "SELECT * FROM reviews WHERE id = ?", (review_id,))
        if review is None:
            return templates.TemplateResponse(request, "invalid.html", {}, status_code=404)
        business = db.one(
            conn, "SELECT * FROM businesses WHERE id = ?", (review["business_id"],)
        )
    return templates.TemplateResponse(request, "thanks.html", {"review": review, "business": business}
    )


# --------------------------------------------------------------------------- #
# QR fallback for any tag - same destination the NFC chip carries
# --------------------------------------------------------------------------- #
@app.get("/qr/{token}.png")
def qr_png(request: Request, token: str):
    with db.get_db() as conn:
        tag = db.one(conn, "SELECT id FROM tags WHERE token = ?", (token,))
    if tag is None:
        raise HTTPException(status_code=404, detail="Unknown tag")
    img = qrcode.make(base_url(request) + "/t/" + token, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(
        buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
def _set_session(response, user_id: int) -> None:
    response.set_cookie(
        auth.COOKIE_NAME,
        auth.sign_session({"uid": user_id}),
        max_age=auth.SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=config.COOKIE_SECURE,
    )


def _slugify(name: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in name.strip()]
    slug = "".join(keep).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:40] or "business"


def _unique_slug(conn, name: str) -> str:
    base = _slugify(name)
    slug, n = base, 2
    while db.one(conn, "SELECT id FROM businesses WHERE slug = ?", (slug,)):
        slug = "%s-%d" % (base, n)
        n += 1
    return slug


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request, error: str = ""):
    if current_user(request):
        return redirect("/dashboard")
    return templates.TemplateResponse(
        request, "signup.html",
        {
            "error": error,
            "closed": config.SIGNUP_CLOSED,
            "support": config.SUPPORT_EMAIL,
            "unit_map": {k: v["unit"] for k, v in business_types.TYPES.items()},
        },
    )


@app.post("/signup")
def signup(
    request: Request,
    business_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    business_type: str = Form(business_types.DEFAULT),
    tables: int = Form(8),
    tz_offset: int = Form(0),
    website: str = Form(""),  # honeypot: real people never see this field
):
    if config.SIGNUP_CLOSED:
        return redirect("/signup?error=Signup+is+currently+closed")
    if website:
        return redirect("/signup?error=Signup+failed")
    if not limits.allow("signup:" + limits.client_ip(request), limit=10, window_seconds=3600):
        return redirect("/signup?error=Too+many+signups+from+this+network.+Try+later.")

    email = email.strip().lower()
    name = business_name.strip()[:80]
    if len(password) < 8:
        return redirect("/signup?error=Password+must+be+at+least+8+characters")
    if "@" not in email or len(email) > 120 or not name:
        return redirect("/signup?error=Enter+a+valid+name+and+email")

    with db.get_db() as conn:
        if db.one(conn, "SELECT id FROM users WHERE email = ?", (email,)):
            return redirect("/signup?error=That+email+already+has+an+account")
        if business_type not in business_types.TYPES:
            business_type = business_types.DEFAULT
        rid = db.execute(
            conn,
            """INSERT INTO businesses (slug, name, business_type, tz_offset, plan, trial_ends_at)
               VALUES (?,?,?,?,'trial',?)""",
            (_unique_slug(conn, name), name, business_type, max(-720, min(840, tz_offset)),
             billing.trial_end_stamp()),
        )
        uid = db.execute(
            conn,
            "INSERT INTO users (business_id, email, password_hash) VALUES (?,?,?)",
            (rid, email, auth.hash_password(password)),
        )
        unit = business_types.preset(business_type)["unit"]
        for i in range(max(1, min(60, tables))):
            db.execute(
                conn,
                "INSERT INTO tags (business_id, token, label) VALUES (?,?,?)",
                (rid, auth.new_token(), "%s %d" % (unit, i + 1)),
            )
    response = RedirectResponse("/dashboard/tags", status_code=303)
    _set_session(response, uid)
    return response


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/dashboard", error: str = ""):
    if current_user(request):
        return redirect("/dashboard")
    return templates.TemplateResponse(request, "login.html", {"next": next, "error": error}
    )


@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/dashboard"),
):
    if not limits.allow("login:" + limits.client_ip(request), limit=10, window_seconds=300):
        return redirect("/login?error=Too+many+attempts.+Wait+a+few+minutes.")
    with db.get_db() as conn:
        user = db.one(conn, "SELECT * FROM users WHERE email = ?", (email.strip().lower(),))
    if user is None or not auth.verify_password(password, user["password_hash"]):
        return redirect("/login?error=Incorrect+email+or+password")
    response = RedirectResponse(next if next.startswith("/") else "/dashboard", status_code=303)
    _set_session(response, user["id"])
    return response


@app.get("/logout")
def logout():
    response = redirect("/")
    response.delete_cookie(auth.COOKIE_NAME)
    return response


# --------------------------------------------------------------------------- #
# password reset
# --------------------------------------------------------------------------- #
@app.get("/forgot", response_class=HTMLResponse)
def forgot_form(request: Request, sent: str = "", error: str = ""):
    return templates.TemplateResponse(
        request, "forgot.html",
        {"sent": bool(sent), "error": error, "support": config.SUPPORT_EMAIL},
    )


@app.post("/forgot")
def forgot(request: Request, email: str = Form(...)):
    if not limits.allow("forgot:" + limits.client_ip(request), limit=5, window_seconds=900):
        return redirect("/forgot?error=Too+many+requests.+Try+again+later.")

    address = email.strip().lower()
    with db.get_db() as conn:
        user = db.one(conn, "SELECT id, email FROM users WHERE email = ?", (address,))
        if user is not None:
            token = secrets.token_urlsafe(32)
            db.execute(
                conn,
                """INSERT INTO password_resets (user_id, token_hash, expires_at)
                   VALUES (?,?, datetime('now', '+1 hour'))""",
                (user["id"], auth.hash_token(token)),
            )
            link = base_url(request) + "/reset/" + token
            mailer.send(
                user["email"],
                "Reset your ReviewHere password",
                "Someone asked to reset the password for this account.\n\n"
                "Open this link within the hour to choose a new one:\n\n"
                + link
                + "\n\nIf that wasn't you, ignore this email and nothing changes.\n",
            )
    # The same answer either way: revealing which emails exist would leak your customer list.
    return redirect("/forgot?sent=1")


@app.get("/reset/{token}", response_class=HTMLResponse)
def reset_form(request: Request, token: str, error: str = ""):
    with db.get_db() as conn:
        row = _valid_reset(conn, token)
    return templates.TemplateResponse(
        request, "reset.html", {"token": token, "valid": row is not None, "error": error}
    )


@app.post("/reset/{token}")
def reset(request: Request, token: str, password: str = Form(...)):
    if len(password) < 8:
        return redirect("/reset/" + token + "?error=Password+must+be+at+least+8+characters")
    with db.get_db() as conn:
        row = _valid_reset(conn, token)
        if row is None:
            return redirect("/forgot?error=That+link+has+expired.+Request+a+new+one.")
        db.execute(
            conn,
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (auth.hash_password(password), row["user_id"]),
        )
        # Burn this token and any other outstanding ones for the account.
        db.execute(
            conn, "UPDATE password_resets SET used = 1 WHERE user_id = ?", (row["user_id"],)
        )
    return redirect("/login?error=Password+updated.+Log+in+with+it.")


def _valid_reset(conn, token: str):
    return db.one(
        conn,
        """SELECT * FROM password_resets
            WHERE token_hash = ? AND used = 0 AND expires_at > datetime('now')""",
        (auth.hash_token(token),),
    )


# --------------------------------------------------------------------------- #
# machine API - what the future hardware reader will talk to
# --------------------------------------------------------------------------- #
class ScanIn(BaseModel):
    token: str | None = None
    uid: str | None = None
    source: str = "reader"


@app.post("/api/scan")
def api_scan(request: Request, payload: ScanIn):
    """A reader posts either the token written on the tag or the raw chip UID.

    Responds with the URL the customer should land on, so a standalone reader
    (ESP32 / USB desk reader) can display, print or forward it.
    """
    with db.get_db() as conn:
        tag = None
        if payload.token:
            tag = db.one(conn, "SELECT * FROM tags WHERE token = ?", (payload.token,))
        if tag is None and payload.uid:
            tag = db.one(conn, "SELECT * FROM tags WHERE uid = ? AND uid != ''", (payload.uid,))
        if tag is None or not tag["active"]:
            return JSONResponse({"ok": False, "error": "unknown_tag"}, status_code=404)
        db.execute(
            conn,
            "INSERT INTO scans (tag_id, source, user_agent) VALUES (?,?,?)",
            (tag["id"], payload.source[:40], request.headers.get("user-agent", "")[:300]),
        )
        business = db.one(conn, "SELECT * FROM businesses WHERE id = ?", (tag["business_id"],))
    return {
        "ok": True,
        "url": base_url(request) + "/t/" + tag["token"],
        "tag": {"token": tag["token"], "label": tag["label"], "zone": tag["zone"]},
        "business": {"name": business["name"], "slug": business["slug"]},
    }


@app.get("/api/health")
def health():
    with db.get_db() as conn:
        counts = db.one(
            conn,
            "SELECT (SELECT COUNT(*) FROM businesses) AS businesses,"
            " (SELECT COUNT(*) FROM tags) AS tags,"
            " (SELECT COUNT(*) FROM reviews) AS reviews",
        )
    return {"ok": True, **dict(counts)}
