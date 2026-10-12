"""Owner-facing dashboard: stats, review inbox, tag management, settings."""
import csv
import io

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from . import auth, business_types, db
from .deps import base_url, redirect, require_active, require_user, templates

router = APIRouter(prefix="/dashboard")


# --------------------------------------------------------------------------- #
# stats
# --------------------------------------------------------------------------- #
def _summary(conn, rid: int) -> dict:
    totals = db.one(
        conn,
        """SELECT COUNT(*) AS total,
                  COALESCE(AVG(rating), 0) AS avg_rating,
                  SUM(CASE WHEN rating >= 4 THEN 1 ELSE 0 END) AS promoters,
                  SUM(CASE WHEN rating <= 2 THEN 1 ELSE 0 END) AS detractors,
                  SUM(CASE WHEN rating <= 3 AND status = 'new' THEN 1 ELSE 0 END) AS needs_action,
                  SUM(routed_public) AS routed_public
             FROM reviews WHERE business_id = ?""",
        (rid,),
    )
    recent = db.one(
        conn,
        """SELECT COUNT(*) AS total, COALESCE(AVG(rating), 0) AS avg_rating
             FROM reviews
            WHERE business_id = ? AND created_at >= datetime('now', '-7 days')""",
        (rid,),
    )
    prior = db.one(
        conn,
        """SELECT COUNT(*) AS total, COALESCE(AVG(rating), 0) AS avg_rating
             FROM reviews
            WHERE business_id = ?
              AND created_at >= datetime('now', '-14 days')
              AND created_at <  datetime('now', '-7 days')""",
        (rid,),
    )
    scans = db.one(
        conn,
        """SELECT COUNT(*) AS total FROM scans s
             JOIN tags t ON t.id = s.tag_id WHERE t.business_id = ?""",
        (rid,),
    )
    categories = db.one(
        conn,
        """SELECT AVG(quality) AS quality, AVG(service) AS service,
                  AVG(environment) AS environment, AVG(value) AS value
             FROM reviews WHERE business_id = ?""",
        (rid,),
    )
    dist_rows = db.query(
        conn,
        "SELECT rating, COUNT(*) AS n FROM reviews WHERE business_id = ? GROUP BY rating",
        (rid,),
    )
    dist = {r: 0 for r in range(1, 6)}
    for row in dist_rows:
        dist[row["rating"]] = row["n"]

    total = totals["total"] or 0
    scan_total = scans["total"] or 0
    return {
        "total": total,
        "avg_rating": round(totals["avg_rating"] or 0, 2),
        "promoters": totals["promoters"] or 0,
        "detractors": totals["detractors"] or 0,
        "needs_action": totals["needs_action"] or 0,
        "routed_public": totals["routed_public"] or 0,
        "week_total": recent["total"] or 0,
        "week_avg": round(recent["avg_rating"] or 0, 2),
        "prev_total": prior["total"] or 0,
        "prev_avg": round(prior["avg_rating"] or 0, 2),
        "avg_delta": round((recent["avg_rating"] or 0) - (prior["avg_rating"] or 0), 2),
        "scans": scan_total,
        "conversion": round(100.0 * total / scan_total, 1) if scan_total else 0.0,
        "categories": {k: (round(v, 2) if v is not None else None) for k, v in dict(categories).items()},
        "dist": dist,
        "dist_max": max(dist.values()) if total else 0,
    }


def _daily(conn, rid: int, days: int = 14, tz_offset: int = 0):
    """Reviews per day, bucketed by the business's local midnight, not UTC's."""
    rows = db.query(
        conn,
        """SELECT date(created_at, ?) AS day, COUNT(*) AS n, AVG(rating) AS avg_rating
             FROM reviews
            WHERE business_id = ? AND created_at >= datetime('now', ?)
            GROUP BY day ORDER BY day""",
        ("%+d minutes" % tz_offset, rid, "-%d days" % days),
    )
    series = [dict(r) for r in rows]
    peak = max([r["n"] for r in series], default=0)
    for r in series:
        r["pct"] = round(100.0 * r["n"] / peak) if peak else 0
        r["avg_rating"] = round(r["avg_rating"] or 0, 1)
    return series


def _tag_stats(conn, rid: int):
    return db.query(
        conn,
        """SELECT t.*,
                  (SELECT COUNT(*) FROM scans s WHERE s.tag_id = t.id)   AS scan_count,
                  (SELECT COUNT(*) FROM reviews r WHERE r.tag_id = t.id) AS review_count,
                  (SELECT AVG(rating) FROM reviews r WHERE r.tag_id = t.id) AS avg_rating
             FROM tags t
            WHERE t.business_id = ?
            ORDER BY t.active DESC, t.id""",
        (rid,),
    )


# --------------------------------------------------------------------------- #
# pages
# --------------------------------------------------------------------------- #
@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, user=Depends(require_user)):
    rid = user["business_id"]
    with db.get_db() as conn:
        summary = _summary(conn, rid)
        daily = _daily(conn, rid, tz_offset=user["tz_offset"])
        tags = _tag_stats(conn, rid)
        latest = db.query(
            conn,
            """SELECT r.*, t.label AS tag_label
                 FROM reviews r LEFT JOIN tags t ON t.id = r.tag_id
                WHERE r.business_id = ?
                ORDER BY r.created_at DESC, r.id DESC LIMIT 6""",
            (rid,),
        )
    ranked = [t for t in tags if t["review_count"] >= 1]
    ranked.sort(key=lambda t: t["avg_rating"] or 0)
    return templates.TemplateResponse(request, "dashboard.html",
        {
            "request": request,
            "user": user,
            "page": "overview",
            "s": summary,
            "daily": daily,
            "latest": latest,
            "worst": ranked[:3],
            "best": list(reversed(ranked))[:3],
        },
    )


@router.get("/reviews", response_class=HTMLResponse)
def reviews(
    request: Request,
    user=Depends(require_user),
    status: str = "",
    rating: str = "",
    tag_id: str = "",
    days: str = "",
    page: int = 1,
):
    rid = user["business_id"]
    where = ["r.business_id = ?"]
    params: list = [rid]
    if status in ("new", "resolved"):
        where.append("r.status = ?")
        params.append(status)
    if rating.isdigit():
        where.append("r.rating = ?")
        params.append(int(rating))
    if rating == "negative":
        where.append("r.rating <= 3")
    if tag_id.isdigit():
        where.append("r.tag_id = ?")
        params.append(int(tag_id))
    if days.isdigit():
        where.append("r.created_at >= datetime('now', ?)")
        params.append("-%d days" % int(days))

    clause = " AND ".join(where)
    per_page = 20
    page = max(1, page)
    with db.get_db() as conn:
        total = db.one(
            conn, "SELECT COUNT(*) AS n FROM reviews r WHERE " + clause, tuple(params)
        )["n"]
        rows = db.query(
            conn,
            "SELECT r.*, t.label AS tag_label FROM reviews r "
            "LEFT JOIN tags t ON t.id = r.tag_id WHERE " + clause +
            " ORDER BY r.created_at DESC, r.id DESC LIMIT ? OFFSET ?",
            tuple(params) + (per_page, (page - 1) * per_page),
        )
        tags = db.query(
            conn, "SELECT id, label FROM tags WHERE business_id = ? ORDER BY id", (rid,)
        )
    return templates.TemplateResponse(request, "reviews.html",
        {
            "request": request,
            "user": user,
            "page": "reviews",
            "reviews": rows,
            "tags": tags,
            "total": total,
            "page_no": page,
            "pages": max(1, -(-total // per_page)),
            "f": {"status": status, "rating": rating, "tag_id": tag_id, "days": days},
        },
    )


@router.post("/reviews/{review_id}/update")
def update_review(
    review_id: int,
    request: Request,
    user=Depends(require_user),
    status: str = Form("resolved"),
    owner_note: str = Form(""),
):
    with db.get_db() as conn:
        row = db.one(
            conn,
            "SELECT id FROM reviews WHERE id = ? AND business_id = ?",
            (review_id, user["business_id"]),
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Review not found")
        db.execute(
            conn,
            "UPDATE reviews SET status = ?, owner_note = ? WHERE id = ?",
            ("resolved" if status == "resolved" else "new", owner_note.strip()[:1000], review_id),
        )
    return redirect(request.headers.get("referer") or "/dashboard/reviews")


@router.get("/tags", response_class=HTMLResponse)
def tags_page(request: Request, user=Depends(require_user), msg: str = ""):
    with db.get_db() as conn:
        tags = _tag_stats(conn, user["business_id"])
    return templates.TemplateResponse(request, "tags.html",
        {
            "request": request,
            "user": user,
            "page": "tags",
            "tags": tags,
            "base": base_url(request),
            "msg": msg,
        },
    )


@router.post("/tags/new")
def create_tags(
    user=Depends(require_user),
    label: str = Form(...),
    zone: str = Form(""),
    count: int = Form(1),
):
    """Create one tag, or a numbered batch when count > 1 ('Table' -> Table 1..N)."""
    require_active(user)
    count = max(1, min(100, count))
    label = label.strip()[:60] or "Tag"
    with db.get_db() as conn:
        existing = db.one(
            conn,
            "SELECT COUNT(*) AS n FROM tags WHERE business_id = ?",
            (user["business_id"],),
        )["n"]
        for i in range(count):
            name = label if count == 1 else "%s %d" % (label, existing + i + 1)
            db.execute(
                conn,
                "INSERT INTO tags (business_id, token, label, zone) VALUES (?,?,?,?)",
                (user["business_id"], auth.new_token(), name, zone.strip()[:40]),
            )
    return redirect("/dashboard/tags?msg=Created+%d+tag(s)" % count)


@router.post("/tags/{tag_id}/toggle")
def toggle_tag(tag_id: int, user=Depends(require_user)):
    with db.get_db() as conn:
        tag = db.one(
            conn,
            "SELECT active FROM tags WHERE id = ? AND business_id = ?",
            (tag_id, user["business_id"]),
        )
        if tag is None:
            raise HTTPException(status_code=404, detail="Tag not found")
        db.execute(conn, "UPDATE tags SET active = ? WHERE id = ?", (0 if tag["active"] else 1, tag_id))
    return redirect("/dashboard/tags?msg=Tag+updated")


@router.post("/tags/{tag_id}/uid")
def set_tag_uid(tag_id: int, user=Depends(require_user), uid: str = Form("")):
    """Record the physical chip UID once a tag is actually written (hardware step)."""
    with db.get_db() as conn:
        tag = db.one(
            conn,
            "SELECT id FROM tags WHERE id = ? AND business_id = ?",
            (tag_id, user["business_id"]),
        )
        if tag is None:
            raise HTTPException(status_code=404, detail="Tag not found")
        db.execute(conn, "UPDATE tags SET uid = ? WHERE id = ?", (uid.strip()[:40], tag_id))
    return redirect("/dashboard/tags?msg=Chip+UID+saved")


@router.get("/tags/write", response_class=HTMLResponse)
def write_page(request: Request, user=Depends(require_user)):
    """Web NFC writer - encodes each tag's URL onto a real sticker from an Android phone."""
    with db.get_db() as conn:
        tags = db.query(
            conn,
            "SELECT * FROM tags WHERE business_id = ? AND active = 1 ORDER BY id",
            (user["business_id"],),
        )
    return templates.TemplateResponse(request, "write.html",
        {"user": user, "page": "tags", "tags": tags, "base": base_url(request)},
    )


@router.post("/tags/{tag_id}/uid.json")
def set_tag_uid_json(tag_id: int, user=Depends(require_user), uid: str = Form("")):
    """Same as the UID form, but for the writer page's background fetch."""
    with db.get_db() as conn:
        tag = db.one(
            conn,
            "SELECT id FROM tags WHERE id = ? AND business_id = ?",
            (tag_id, user["business_id"]),
        )
        if tag is None:
            raise HTTPException(status_code=404, detail="Tag not found")
        db.execute(conn, "UPDATE tags SET uid = ? WHERE id = ?", (uid.strip()[:40], tag_id))
    return {"ok": True, "uid": uid.strip()[:40]}


@router.get("/tags/print", response_class=HTMLResponse)
def print_sheet(request: Request, user=Depends(require_user)):
    with db.get_db() as conn:
        tags = db.query(
            conn,
            "SELECT * FROM tags WHERE business_id = ? AND active = 1 ORDER BY id",
            (user["business_id"],),
        )
    return templates.TemplateResponse(request, "print.html",
        {"user": user, "tags": tags, "base": base_url(request)},
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, user=Depends(require_user), msg: str = ""):
    with db.get_db() as conn:
        business = db.one(
            conn, "SELECT * FROM businesses WHERE id = ?", (user["business_id"],)
        )
    return templates.TemplateResponse(request, "settings.html",
        {
            "request": request,
            "user": user,
            "page": "settings",
            "r": business,
            "base": base_url(request),
            "msg": msg,
        },
    )


@router.post("/settings")
def save_settings(
    user=Depends(require_user),
    name: str = Form(...),
    slug: str = Form(""),
    business_type: str = Form(business_types.DEFAULT),
    tagline: str = Form(""),
    logo_url: str = Form(""),
    google_review_url: str = Form(""),
    accent: str = Form("#e0603a"),
    positive_threshold: int = Form(4),
    tz_offset: int = Form(0),
    google_auto: str = Form(""),
    tap_mode: str = Form("rate_first"),
    routing: str = Form("open"),
):
    with db.get_db() as conn:
        # The slug is part of every printed and written link, so it changes only when
        # asked for, and never onto one another business already owns.
        wanted = "".join(c.lower() if c.isalnum() else "-" for c in slug.strip()).strip("-")[:40]
        while "--" in wanted:
            wanted = wanted.replace("--", "-")
        if wanted:
            clash = db.one(
                conn,
                "SELECT id FROM businesses WHERE slug = ? AND id != ?",
                (wanted, user["business_id"]),
            )
            if clash is None:
                db.execute(
                    conn, "UPDATE businesses SET slug = ? WHERE id = ?",
                    (wanted, user["business_id"]),
                )
        db.execute(
            conn,
            """UPDATE businesses
                  SET name = ?, business_type = ?, tagline = ?, logo_url = ?,
                      google_review_url = ?, accent = ?, positive_threshold = ?, tz_offset = ?,
                      google_auto = ?, tap_mode = ?, routing = ?
                WHERE id = ?""",
            (
                name.strip()[:80],
                business_type if business_type in business_types.TYPES else business_types.DEFAULT,
                tagline.strip()[:120],
                logo_url.strip()[:300],
                google_review_url.strip()[:500],
                accent.strip()[:20],
                max(1, min(5, positive_threshold)),
                max(-720, min(840, tz_offset)),
                1 if google_auto else 0,
                "direct" if tap_mode == "direct" else "rate_first",
                "gated" if routing == "gated" else "open",
                user["business_id"],
            ),
        )
    return redirect("/dashboard/settings?msg=Settings+saved")


@router.post("/password")
def change_password(
    user=Depends(require_user),
    current_password: str = Form(...),
    new_password: str = Form(...),
):
    if len(new_password) < 8:
        return redirect("/dashboard/settings?msg=Password+must+be+8%2B+characters")
    with db.get_db() as conn:
        row = db.one(conn, "SELECT password_hash FROM users WHERE id = ?", (user["id"],))
        if not auth.verify_password(current_password, row["password_hash"]):
            return redirect("/dashboard/settings?msg=Current+password+is+wrong")
        db.execute(
            conn,
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (auth.hash_password(new_password), user["id"]),
        )
    return redirect("/dashboard/settings?msg=Password+changed")


@router.get("/export.csv")
def export_csv(user=Depends(require_user)):
    with db.get_db() as conn:
        rows = db.query(
            conn,
            """SELECT r.id, r.created_at, t.label AS tag_label, r.rating, r.quality, r.service,
                      r.environment, r.value, r.comment, r.customer_name,
                      r.customer_contact, r.routed_public, r.status, r.owner_note
                 FROM reviews r LEFT JOIN tags t ON t.id = r.tag_id
                WHERE r.business_id = ? ORDER BY r.created_at DESC""",
            (user["business_id"],),
        )
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        ["id", "created_at", "tag", "rating", "quality", "service", "environment", "value",
         "comment", "customer_name", "customer_contact", "sent_to_google", "status", "owner_note"]
    )
    for row in rows:
        writer.writerow(list(row))
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="reviews.csv"'},
    )
