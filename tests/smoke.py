"""End-to-end smoke test - runs every route against a throwaway database.

    python -m tests.smoke
"""
import hashlib
import hmac
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DB = Path(tempfile.gettempdir()) / "reviewhere_smoke.db"
for leftover in (DB, Path(str(DB) + "-wal"), Path(str(DB) + "-shm")):
    leftover.unlink(missing_ok=True)
os.environ["NFC_DB_PATH"] = str(DB)
os.environ["NFC_SECRET_KEY"] = "smoke-test-key"
os.environ["NFC_RAZORPAY_WEBHOOK_SECRET"] = "smoke-webhook-secret"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import auth as auth_mod  # noqa: E402
from app import billing, db, limits, seed  # noqa: E402
from app.main import app  # noqa: E402

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(("  PASS  " if condition else "  FAIL  ") + name + (("  <- " + str(detail)) if not condition else ""))


def main():
    seed.seed(reset=True)
    client = TestClient(app)

    with db.get_db() as conn:
        tag = db.one(conn, "SELECT * FROM tags ORDER BY id LIMIT 1")
        weak_tag = db.one(conn, "SELECT * FROM tags ORDER BY id LIMIT 1 OFFSET 2")
    token = tag["token"]

    print("\n--- public pages ---")
    check("landing renders", client.get("/").status_code == 200)
    r = client.get("/t/" + token)
    check("guest page renders", r.status_code == 200 and "How was your experience?" in r.text)
    check("guest page shows table label", tag["label"] in r.text)
    check("unknown tag returns 404", client.get("/t/nope-nope").status_code == 404)
    check("business-wide page renders", client.get("/r/spice-route").status_code == 200)
    check("qr png served", client.get("/qr/%s.png" % token).headers["content-type"] == "image/png")

    print("\n--- scan counting ---")
    with db.get_db() as conn:
        before = db.one(conn, "SELECT COUNT(*) AS n FROM scans WHERE tag_id = ?", (tag["id"],))["n"]
    client.get("/t/" + token)
    with db.get_db() as conn:
        after = db.one(conn, "SELECT COUNT(*) AS n FROM scans WHERE tag_id = ?", (tag["id"],))["n"]
    check("tap records a scan", after == before + 1, "%d -> %d" % (before, after))

    print("\n--- happy path: 5 stars is routed to Google ---")
    r = client.post(
        "/t/" + token,
        data={"rating": "5", "comment": "Excellent lamb chops", "quality": "5", "service": "4"},
        follow_redirects=True,
    )
    check("5-star submit lands on thanks page", r.status_code == 200 and "Thank you" in r.text)
    check("5-star sees the Google button", "Post this on Google" in r.text)
    with db.get_db() as conn:
        last = db.one(conn, "SELECT * FROM reviews ORDER BY id DESC LIMIT 1")
    check("5-star stored with routed_public=1", last["rating"] == 5 and last["routed_public"] == 1)
    check("sub-ratings stored", last["quality"] == 5 and last["service"] == 4)

    print("\n--- unhappy path: 2 stars stays private ---")
    r = client.post(
        "/t/" + weak_tag["token"],
        data={
            "rating": "2",
            "comment": "Cold food",
            "customer_name": "Test Guest",
            "customer_contact": "test@example.com",
        },
        follow_redirects=True,
    )
    check("2-star is told it reached the manager",
          r.status_code == 200 and "straight to the manager" in r.text)
    check("2-star is never auto-redirected", "Taking you to Google" not in r.text)
    with db.get_db() as conn:
        last = db.one(conn, "SELECT * FROM reviews ORDER BY id DESC LIMIT 1")
    check("2-star reaches the inbox", last["rating"] == 2 and last["status"] == "new")
    check("contact captured", last["customer_contact"] == "test@example.com")

    print("\n--- routing policy: open vs gated ---")
    with db.get_db() as conn:
        demo = db.one(conn, "SELECT * FROM businesses WHERE slug = 'spice-route'")
        db.execute(
            conn,
            "UPDATE businesses SET routing = 'open', google_review_url = ? WHERE id = ?",
            ("https://g.page/r/demo/review", demo["id"]),
        )
    low = TestClient(app).post(
        "/t/" + weak_tag["token"], data={"rating": "2", "comment": "Slow"}, follow_redirects=True
    )
    check("open routing invites an unhappy customer too", "Post this on Google" in low.text)
    check("open routing still apologises first", "straight to the manager" in low.text)
    high = TestClient(app).post(
        "/t/" + token, data={"rating": "5"}, follow_redirects=True
    )
    check("open routing invites a happy customer", "Post this on Google" in high.text)
    with db.get_db() as conn:
        stored = db.one(conn, "SELECT * FROM reviews ORDER BY id DESC LIMIT 1")
    check("open routing marks it public", stored["routed_public"] == 1)

    with db.get_db() as conn:
        db.execute(conn, "UPDATE businesses SET routing = 'gated' WHERE id = ?", (demo["id"],))
    low = TestClient(app).post(
        "/t/" + weak_tag["token"], data={"rating": "2"}, follow_redirects=True
    )
    check("gated routing hides Google from an unhappy customer",
          "Post this on Google" not in low.text)
    high = TestClient(app).post("/t/" + token, data={"rating": "5"}, follow_redirects=True)
    check("gated routing still invites a happy customer", "Post this on Google" in high.text)
    with db.get_db() as conn:
        db.execute(conn, "UPDATE businesses SET routing = 'open' WHERE id = ?", (demo["id"],))
        captured = db.one(
            conn,
            "SELECT COUNT(*) AS n FROM reviews WHERE business_id = ? AND rating <= 2",
            (demo["id"],),
        )["n"]
    check("private feedback is captured under both policies", captured >= 2, captured)

    print("\n--- validation ---")
    check("rating is required", client.post("/t/" + token, data={"comment": "no stars"}).status_code == 400)
    r = client.post("/t/" + token, data={"rating": "99"}, follow_redirects=True)
    with db.get_db() as conn:
        last = db.one(conn, "SELECT rating FROM reviews ORDER BY id DESC LIMIT 1")
    check("out-of-range rating clamped to 5", last["rating"] == 5, last["rating"])

    print("\n--- machine API ---")
    r = client.post("/api/scan", json={"token": token, "source": "esp32"})
    check("scan API returns guest url", r.status_code == 200 and r.json()["url"].endswith("/t/" + token))
    check("scan API rejects unknown tag", client.post("/api/scan", json={"token": "zzzz"}).status_code == 404)
    check("health endpoint", client.get("/api/health").json()["ok"] is True)

    print("\n--- auth ---")
    check("dashboard redirects anonymous users", client.get("/dashboard", follow_redirects=False).status_code == 307)
    bad = client.post(
        "/login", data={"email": seed.DEMO_EMAIL, "password": "wrong"}, follow_redirects=True
    )
    check("bad password is rejected", "Incorrect email or password" in bad.text)
    r = client.post(
        "/login",
        data={"email": seed.DEMO_EMAIL, "password": seed.DEMO_PASSWORD, "next": "/dashboard"},
        follow_redirects=True,
    )
    check("owner can log in", r.status_code == 200 and "Overview" in r.text)

    print("\n--- dashboard ---")
    r = client.get("/dashboard")
    check("overview renders stats", "Average rating" in r.text and "Taps → reviews" in r.text)
    r = client.get("/dashboard/reviews")
    check("review inbox renders", r.status_code == 200)
    check("filter by negative", client.get("/dashboard/reviews?rating=negative&status=new").status_code == 200)
    check("filter by table", client.get("/dashboard/reviews?tag_id=%d" % tag["id"]).status_code == 200)
    check("pagination page 2", client.get("/dashboard/reviews?page=2").status_code == 200)

    with db.get_db() as conn:
        open_neg = db.one(conn, "SELECT id FROM reviews WHERE rating <= 3 AND status = 'new' LIMIT 1")
    client.post(
        "/dashboard/reviews/%d/update" % open_neg["id"],
        data={"status": "resolved", "owner_note": "Called the guest, comped dessert"},
        follow_redirects=True,
    )
    with db.get_db() as conn:
        updated = db.one(conn, "SELECT * FROM reviews WHERE id = ?", (open_neg["id"],))
    check("review can be resolved with a note", updated["status"] == "resolved" and "comped" in updated["owner_note"])

    print("\n--- tag management ---")
    check("tag list renders", "NFC tags" in client.get("/dashboard/tags").text)
    client.post("/dashboard/tags/new", data={"label": "Terrace", "zone": "Outside", "count": "3"}, follow_redirects=True)
    with db.get_db() as conn:
        made = db.query(conn, "SELECT * FROM tags WHERE label LIKE 'Terrace%'")
    check("bulk tag creation numbers them", len(made) == 3, [m["label"] for m in made])
    check("tokens are unique", len({m["token"] for m in made}) == 3)
    client.post("/dashboard/tags/%d/toggle" % made[0]["id"], follow_redirects=True)
    with db.get_db() as conn:
        toggled = db.one(conn, "SELECT active FROM tags WHERE id = ?", (made[0]["id"],))
    check("tag can be disabled", toggled["active"] == 0)
    check("disabled tag stops accepting taps", client.get("/t/" + made[0]["token"]).status_code == 404)
    client.post("/dashboard/tags/%d/uid" % made[1]["id"], data={"uid": "04:A2:B3:C4"}, follow_redirects=True)
    r = client.post("/api/scan", json={"uid": "04:A2:B3:C4"})
    check("reader can resolve a tag by chip UID", r.status_code == 200 and r.json()["ok"] is True)
    check("print sheet renders", client.get("/dashboard/tags/print").status_code == 200)

    print("\n--- settings & export ---")
    client.post(
        "/dashboard/settings",
        data={
            "name": "Spice Route Kitchen",
            "tagline": "Wood-fired",
            "logo_url": "",
            "google_review_url": "",
            "accent": "#2f6f4f",
            "positive_threshold": "5",
        },
        follow_redirects=True,
    )
    r = client.get("/t/" + token)
    check("guest page picks up new name", "Spice Route Kitchen" in r.text)
    r = client.post("/t/" + token, data={"rating": "4"}, follow_redirects=True)
    check("no Google link configured -> nothing public", "Post this on Google" not in r.text)

    csv_out = client.get("/dashboard/export.csv")
    check("csv export downloads", csv_out.headers["content-type"].startswith("text/csv"))
    check("csv has a header row", csv_out.text.splitlines()[0].startswith("id,created_at,tag,rating"))

    client.post(
        "/dashboard/password",
        data={"current_password": seed.DEMO_PASSWORD, "new_password": "newpass123"},
        follow_redirects=True,
    )
    client.get("/logout")
    r = client.post(
        "/login", data={"email": seed.DEMO_EMAIL, "password": "newpass123"}, follow_redirects=True
    )
    check("password change works", "Overview" in r.text)

    print("")
    print("--- signup & multi-tenancy ---")
    fresh = TestClient(app)
    r = fresh.post(
        "/signup",
        data={"business_name": "Cafe Aroma", "email": "aroma@example.com",
              "password": "aroma12345", "tables": "5", "tz_offset": "330"},
        follow_redirects=True,
    )
    check("signup lands in the dashboard", r.status_code == 200 and "Cafe Aroma" in r.text)
    with db.get_db() as conn:
        newr = db.one(conn, "SELECT * FROM businesses WHERE name = 'Cafe Aroma'")
        newtags = db.query(conn, "SELECT * FROM tags WHERE business_id = ?", (newr["id"],))
    check("signup creates a slug", newr["slug"] == "cafe-aroma")
    check("signup stores the timezone", newr["tz_offset"] == 330)
    check("signup creates one tag per table", len(newtags) == 5)
    def signup_error(**overrides):
        """Post a signup as a brand-new visitor and return where it bounced to."""
        payload = {"business_name": "Test Place", "email": "new@example.com",
                   "password": "password123", "tables": "2"}
        payload.update(overrides)
        r = TestClient(app).post("/signup", data=payload, follow_redirects=False)
        return r.headers.get("location", "")

    check("duplicate email is rejected",
          "already+has+an+account" in signup_error(email="aroma@example.com"))
    check("short password is rejected",
          "at+least+8" in signup_error(email="tiny@example.com", password="short"))
    check("honeypot silently rejects bots",
          "Signup+failed" in signup_error(email="bot@example.com", website="http://spam"))
    check("malformed email is rejected", "valid+name" in signup_error(email="notanemail"))
    with db.get_db() as conn:
        rejected = db.one(conn, "SELECT COUNT(*) AS n FROM businesses WHERE name = 'Test Place'")
    check("no rejected signup created a business", rejected["n"] == 0)

    # A new business must not see the demo business's data.
    r = fresh.get("/dashboard/reviews")
    check("new business cannot see demo reviews", "Excellent lamb chops" not in r.text)
    other = fresh.post("/dashboard/tags/%d/toggle" % tag["id"], follow_redirects=False)
    check("cannot touch another business's tag", other.status_code == 404)

    print("")
    print("--- abuse protection ---")
    with db.get_db() as conn:
        spam_tag = db.one(conn, "SELECT * FROM tags WHERE business_id = ?", (newr["id"],))
    codes = [
        TestClient(app).post("/t/" + spam_tag["token"], data={"rating": "5"},
                             follow_redirects=False).status_code
        for _ in range(17)
    ]
    check("per-table rate limit kicks in", 429 in codes, codes[-3:])
    check("honeypot blocks review spam",
          TestClient(app).post("/t/" + token, data={"rating": "5", "website": "spam"},
                               follow_redirects=False).status_code == 400)

    print("")
    print("--- business types ---")
    # Earlier signup attempts (including the rejected ones) have eaten into the
    # per-IP signup allowance, and every TestClient shares one address.
    limits._hits.clear()
    salon = TestClient(app)
    salon.post(
        "/signup",
        data={"business_name": "Sharp Cuts", "email": "salon@example.com",
              "password": "salon12345", "tables": "4", "business_type": "salon"},
        follow_redirects=True,
    )
    with db.get_db() as conn:
        sb = db.one(conn, "SELECT * FROM businesses WHERE name = 'Sharp Cuts'")
        stags = db.query(conn, "SELECT * FROM tags WHERE business_id = ?", (sb["id"],))
    check("business type is stored", sb["business_type"] == "salon")
    check("tags are named for the right unit", stags[0]["label"] == "Station 1", stags[0]["label"])

    guest = TestClient(app).get("/t/" + stags[0]["token"]).text
    check("salon guest page uses salon categories", "Result" in guest and "Cleanliness" in guest)
    check("salon guest page drops restaurant words", "Ambience" not in guest and "Food" not in guest)
    board = salon.get("/dashboard").text
    check("salon dashboard says stations, not tables",
          "Stations needing attention" in board and "Tables needing attention" not in board)

    clinic = TestClient(app)
    clinic.post(
        "/signup",
        data={"business_name": "City Dental", "email": "clinic@example.com",
              "password": "clinic12345", "tables": "3", "business_type": "clinic"},
        follow_redirects=True,
    )
    with db.get_db() as conn:
        cb = db.one(conn, "SELECT * FROM businesses WHERE name = 'City Dental'")
        ctag = db.one(conn, "SELECT * FROM tags WHERE business_id = ?", (cb["id"],))
    cguest = TestClient(app).get("/t/" + ctag["token"]).text
    check("clinic guest page uses clinic categories", "Care" in cguest and "Facilities" in cguest)
    check("clinic tags are rooms", ctag["label"] == "Room 1", ctag["label"])

    # Ratings are stored in the same four columns whatever the wording.
    TestClient(app).post(
        "/t/" + ctag["token"],
        data={"rating": "5", "quality": "5", "service": "4", "environment": "3", "value": "2"},
        follow_redirects=True,
    )
    with db.get_db() as conn:
        cr = db.one(conn, "SELECT * FROM reviews WHERE business_id = ? ORDER BY id DESC", (cb["id"],))
    check("shared schema stores all four categories",
          (cr["quality"], cr["service"], cr["environment"], cr["value"]) == (5, 4, 3, 2))

    # Switching type relabels an existing account without touching its data.
    salon.post(
        "/dashboard/settings",
        data={"name": "Sharp Cuts", "business_type": "gym", "positive_threshold": "4",
              "accent": "#e0603a", "tz_offset": "330", "google_review_url": ""},
        follow_redirects=True,
    )
    check("changing type relabels the dashboard",
          "Equipment" in salon.get("/dashboard").text)

    print("")
    print("--- Google routing is a choice, not a rule ---")
    with db.get_db() as conn:
        demo = db.one(conn, "SELECT * FROM businesses WHERE slug = 'spice-route'")
        # An earlier test cleared the link; this block is about what happens when
        # one *is* configured.
        db.execute(
            conn,
            "UPDATE businesses SET google_review_url = ?, google_auto = 0 WHERE id = ?",
            ("https://g.page/r/demo/review", demo["id"]),
        )
    # Default: a button, never an automatic hand-off.
    r = TestClient(app).post("/t/" + token, data={"rating": "5"}, follow_redirects=True)
    check("button is offered by default", "Post this on Google" in r.text)
    check("no auto-redirect by default", "Taking you to Google" not in r.text)

    with db.get_db() as conn:
        db.execute(conn, "UPDATE businesses SET google_auto = 1 WHERE id = ?", (demo["id"],))
    r = TestClient(app).post("/t/" + token, data={"rating": "5"}, follow_redirects=True)
    check("auto-redirect happens when switched on", "Taking you to Google" in r.text)
    check("the button still works as a fallback", "Post this on Google" in r.text)

    # With no Google link there is nothing public to send anyone to, switch or no switch.
    with db.get_db() as conn:
        db.execute(
            conn, "UPDATE businesses SET google_review_url = '' WHERE id = ?", (demo["id"],)
        )
    r = TestClient(app).post("/t/" + token, data={"rating": "5"}, follow_redirects=True)
    check("no Google link means no redirect", "Taking you to Google" not in r.text)
    check("no Google link means no button", "Post this on Google" not in r.text)
    with db.get_db() as conn:
        db.execute(
            conn,
            "UPDATE businesses SET google_review_url = ?, google_auto = 0 WHERE id = ?",
            ("https://g.page/r/demo/review", demo["id"]),
        )

    print("")
    print("--- direct mode: tap goes straight to Google ---")
    with db.get_db() as conn:
        db.execute(
            conn,
            "UPDATE businesses SET tap_mode = 'direct', google_review_url = ? WHERE id = ?",
            ("https://g.page/r/demo/review", demo["id"]),
        )
    hop = TestClient(app).get("/t/" + token, follow_redirects=False)
    check("direct mode forwards the tap", hop.status_code == 303)
    check("it forwards to the Google link",
          hop.headers.get("location") == "https://g.page/r/demo/review")
    with db.get_db() as conn:
        scans_before = db.one(
            conn, "SELECT COUNT(*) AS n FROM scans WHERE tag_id = ?", (tag["id"],)
        )["n"]
    TestClient(app).get("/t/" + token, follow_redirects=False)
    with db.get_db() as conn:
        scans_after = db.one(
            conn, "SELECT COUNT(*) AS n FROM scans WHERE tag_id = ?", (tag["id"],)
        )["n"]
    check("taps are still counted in direct mode", scans_after == scans_before + 1)

    # Without a Google link there is nowhere to forward to, so it must not strand anyone.
    with db.get_db() as conn:
        db.execute(
            conn, "UPDATE businesses SET google_review_url = '' WHERE id = ?", (demo["id"],)
        )
    check("direct mode falls back to the rating page when no link is set",
          TestClient(app).get("/t/" + token).status_code == 200)
    with db.get_db() as conn:
        db.execute(
            conn,
            "UPDATE businesses SET tap_mode = 'rate_first', google_review_url = ? WHERE id = ?",
            ("https://g.page/r/demo/review", demo["id"]),
        )
    check("switching back restores the rating page",
          "How was your experience?" in TestClient(app).get("/t/" + token).text)

    # A business-level tag (/r/<slug>) must behave exactly like a per-spot one.
    with db.get_db() as conn:
        db.execute(
            conn,
            "UPDATE businesses SET tap_mode = 'direct', google_review_url = ? WHERE id = ?",
            ("https://g.page/r/demo/review", demo["id"]),
        )
    slug_hop = TestClient(app).get("/r/spice-route", follow_redirects=False)
    check("slug link forwards in direct mode too", slug_hop.status_code == 303)
    check("slug link forwards to the same place",
          slug_hop.headers.get("location") == "https://g.page/r/demo/review")
    with db.get_db() as conn:
        db.execute(conn, "UPDATE businesses SET tap_mode = 'rate_first' WHERE id = ?", (demo["id"],))
    check("slug link shows the rating page otherwise",
          "How was your experience?" in TestClient(app).get("/r/spice-route").text)

    print("")
    print("--- the link name (slug) is editable ---")
    client.post(
        "/dashboard/settings",
        data={"name": "Spice Route", "slug": "Spice Route Cafe!!", "business_type": "restaurant",
              "positive_threshold": "4", "accent": "#e0603a", "tz_offset": "330",
              "google_review_url": "https://g.page/r/demo/review", "routing": "open"},
        follow_redirects=True,
    )
    with db.get_db() as conn:
        renamed = db.one(conn, "SELECT slug FROM businesses WHERE id = ?", (demo["id"],))
    check("messy input becomes a clean slug", renamed["slug"] == "spice-route-cafe", renamed["slug"])
    check("the new link works", TestClient(app).get("/r/spice-route-cafe").status_code == 200)
    check("the old link is gone", TestClient(app).get("/r/spice-route").status_code == 404)

    # Someone else's slug must not be stealable.
    with db.get_db() as conn:
        other_id = db.execute(
            conn, "INSERT INTO businesses (slug, name) VALUES ('taken-name', 'Someone Else')"
        )
    client.post(
        "/dashboard/settings",
        data={"name": "Spice Route", "slug": "taken-name", "business_type": "restaurant",
              "positive_threshold": "4", "accent": "#e0603a", "tz_offset": "330",
              "google_review_url": "https://g.page/r/demo/review", "routing": "open"},
        follow_redirects=True,
    )
    with db.get_db() as conn:
        still = db.one(conn, "SELECT slug FROM businesses WHERE id = ?", (demo["id"],))
        db.execute(conn, "DELETE FROM businesses WHERE id = ?", (other_id,))
        db.execute(conn, "UPDATE businesses SET slug = 'spice-route' WHERE id = ?", (demo["id"],))
    check("a taken slug is refused, not silently applied", still["slug"] == "spice-route-cafe",
          still["slug"])

    print("")
    print("--- billing: trial, orders, webhooks ---")
    limits._hits.clear()
    shop = TestClient(app)
    shop.post(
        "/signup",
        data={"business_name": "Corner Store", "email": "shop@example.com",
              "password": "shop123456", "tables": "3", "business_type": "retail"},
        follow_redirects=True,
    )
    with db.get_db() as conn:
        sb = db.one(conn, "SELECT * FROM businesses WHERE name = 'Corner Store'")
    check("signup starts a trial", sb["plan"] == "trial" and sb["trial_ends_at"] != "")
    days = (billing._parse(sb["trial_ends_at"]) - datetime.now(timezone.utc)).days
    check("trial is 7 days", days == 6, "%d full days remaining" % days)

    page = shop.get("/dashboard/billing").text
    check("billing page renders", "Order NFC tags" in page)
    check("tag price is shown as rupees", "₹300" in page)
    check("trial banner appears on the dashboard", "Free trial" in shop.get("/dashboard").text)
    check("subscribe is disabled with no payment keys", "disabled" in page)

    shop.post(
        "/dashboard/billing/order",
        data={"quantity": "12", "ship_name": "Corner Store", "ship_phone": "9990001111",
              "ship_address": "12 Main Road, Rajpura 140401"},
        follow_redirects=True,
    )
    with db.get_db() as conn:
        order = db.one(conn, "SELECT * FROM orders WHERE business_id = ?", (sb["id"],))
    check("order is priced per tag", order["amount_paise"] == 12 * 30000, order["amount_paise"])
    check("order starts unpaid", order["status"] == "created")
    check("shipping address is kept", "Rajpura" in order["ship_address"])
    check("order appears in history", "12" in shop.get("/dashboard/billing").text)

    # An expired trial locks the dashboard but must never stop review collection.
    with db.get_db() as conn:
        db.execute(
            conn,
            "UPDATE businesses SET trial_ends_at = datetime('now', '-1 day') WHERE id = ?",
            (sb["id"],),
        )
        shop_tag = db.one(conn, "SELECT * FROM tags WHERE business_id = ?", (sb["id"],))
    check("expired trial redirects to billing",
          shop.get("/dashboard", follow_redirects=False).headers.get("location", "")
          .startswith("/dashboard/billing"))
    check("billing page stays reachable when locked",
          shop.get("/dashboard/billing").status_code == 200)
    # The lock sends them to billing rather than showing a bare error, so what
    # matters is that no tag is actually created.
    with db.get_db() as conn:
        before_tags = db.one(
            conn, "SELECT COUNT(*) AS n FROM tags WHERE business_id = ?", (sb["id"],)
        )["n"]
    blocked = shop.post("/dashboard/tags/new", data={"label": "Extra", "count": "1"},
                        follow_redirects=False)
    with db.get_db() as conn:
        after_tags = db.one(
            conn, "SELECT COUNT(*) AS n FROM tags WHERE business_id = ?", (sb["id"],)
        )["n"]
    check("locked account cannot mint new tags",
          after_tags == before_tags and blocked.status_code in (307, 402),
          "%d -> %d, status %d" % (before_tags, after_tags, blocked.status_code))
    check("guest page keeps working when locked",
          TestClient(app).get("/t/" + shop_tag["token"]).status_code == 200)
    submitted = TestClient(app).post(
        "/t/" + shop_tag["token"], data={"rating": "5"}, follow_redirects=True
    )
    check("reviews are still collected when locked", submitted.status_code == 200)
    with db.get_db() as conn:
        still = db.one(
            conn, "SELECT COUNT(*) AS n FROM reviews WHERE business_id = ?", (sb["id"],)
        )["n"]
    check("the waiting review is stored", still >= 1, still)

    # Webhooks: signature is mandatory, replays are ignored.
    def signed(body: dict, event_id: str):
        raw = json.dumps(body).encode()
        sig = hmac.new(b"smoke-webhook-secret", raw, hashlib.sha256).hexdigest()
        return TestClient(app).post(
            "/api/razorpay/webhook", content=raw,
            headers={"x-razorpay-signature": sig, "x-razorpay-event-id": event_id,
                     "content-type": "application/json"},
        )

    unsigned = TestClient(app).post("/api/razorpay/webhook", json={"event": "order.paid"})
    check("webhook without a signature is rejected", unsigned.status_code == 400)
    bad = TestClient(app).post(
        "/api/razorpay/webhook", json={"event": "order.paid"},
        headers={"x-razorpay-signature": "deadbeef"},
    )
    check("webhook with a wrong signature is rejected", bad.status_code == 400)

    with db.get_db() as conn:
        db.execute(
            conn, "UPDATE orders SET provider_order_id = 'order_TEST123' WHERE id = ?",
            (order["id"],),
        )
    paid_event = {
        "event": "order.paid",
        "payload": {"payment": {"entity": {"id": "pay_TEST999", "order_id": "order_TEST123"}}},
    }
    check("signed webhook is accepted", signed(paid_event, "evt_1").status_code == 200)
    with db.get_db() as conn:
        after_pay = db.one(conn, "SELECT * FROM orders WHERE id = ?", (order["id"],))
    check("webhook marks the order paid", after_pay["status"] == "paid")
    check("payment id is recorded", after_pay["provider_payment_id"] == "pay_TEST999")
    check("replayed webhook is ignored",
          signed(paid_event, "evt_1").json().get("duplicate") is True)

    sub_event = {
        "event": "subscription.activated",
        "payload": {"subscription": {"entity": {
            "id": "sub_TEST1", "status": "active", "current_end": "1767225600",
            "notes": {"business_id": str(sb["id"])}}}},
    }
    check("subscription webhook accepted", signed(sub_event, "evt_2").status_code == 200)
    with db.get_db() as conn:
        subbed = db.one(conn, "SELECT * FROM businesses WHERE id = ?", (sb["id"],))
    check("subscription activates the account", subbed["plan"] == "active")
    check("dashboard unlocks after paying", shop.get("/dashboard").status_code == 200)

    cancel_event = {
        "event": "subscription.cancelled",
        "payload": {"subscription": {"entity": {
            "id": "sub_TEST1", "status": "cancelled",
            "notes": {"business_id": str(sb["id"])}}}},
    }
    signed(cancel_event, "evt_3")
    check("cancelling locks the dashboard again",
          shop.get("/dashboard", follow_redirects=False).status_code == 307)

    check("rupee formatting", billing.rupees(30000) == "300" and billing.rupees(1234500) == "12,345")

    print("")
    print("--- password reset ---")
    anon = TestClient(app)
    r = anon.post("/forgot", data={"email": "aroma@example.com"}, follow_redirects=True)
    check("forgot page confirms without leaking", "on its way" in r.text)
    r = anon.post("/forgot", data={"email": "nobody@example.com"}, follow_redirects=True)
    check("unknown email gets the same answer", "on its way" in r.text)
    with db.get_db() as conn:
        issued = db.query(conn, "SELECT * FROM password_resets ORDER BY id DESC")
    check("only a real account gets a token", len(issued) == 1)
    check("token is stored hashed, not raw", len(issued[0]["token_hash"]) == 64)

    # Re-issue a token we know the plaintext of, the way the route does.
    raw = "test-token-value"
    with db.get_db() as conn:
        aroma = db.one(conn, "SELECT id FROM users WHERE email = 'aroma@example.com'")
        db.execute(
            conn,
            "INSERT INTO password_resets (user_id, token_hash, expires_at) "
            "VALUES (?,?, datetime('now', '+1 hour'))",
            (aroma["id"], auth_mod.hash_token(raw)),
        )
    check("reset form renders for a live token",
          "Choose a new password" in anon.get("/reset/" + raw).text)
    check("garbage token shows the expired page",
          "has expired" in anon.get("/reset/not-a-real-token").text)
    anon.post("/reset/" + raw, data={"password": "brandnew123"}, follow_redirects=False)
    r = TestClient(app).post(
        "/login", data={"email": "aroma@example.com", "password": "brandnew123"},
        follow_redirects=True,
    )
    check("new password works", "Cafe Aroma" in r.text)
    check("token cannot be reused",
          "has expired" in anon.get("/reset/" + raw).text)

    with db.get_db() as conn:
        db.execute(
            conn,
            "INSERT INTO password_resets (user_id, token_hash, expires_at) "
            "VALUES (?,?, datetime('now', '-1 minute'))",
            (aroma["id"], auth_mod.hash_token("stale-token")),
        )
    check("expired token is refused", "has expired" in anon.get("/reset/stale-token").text)

    print("")
    print("--- deployment hygiene ---")
    check("privacy page renders", "How your data is used" in client.get("/privacy").text)
    check("guest page links to privacy", '/privacy' in client.get("/t/" + token).text)
    check("robots.txt disallows guest pages", "Disallow: /t/" in client.get("/robots.txt").text)
    check("guest pages are noindex",
          client.get("/t/" + token).headers.get("x-robots-tag") == "noindex, nofollow")
    health = client.get("/api/health").json()
    check("health endpoint reports counts", health["ok"] and health["businesses"] >= 2)
    check("writer page renders", "Write the stickers" in client.get("/dashboard/tags/write").text)

    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    if FAILED:
        print("failing: " + ", ".join(FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
