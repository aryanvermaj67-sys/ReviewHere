"""Create a demo business with tags and realistic review history.

    python -m app.seed          # add demo data (skips if the demo already exists)
    python -m app.seed --reset  # wipe everything and start over
"""
import random
import sys
from datetime import datetime, timedelta, timezone

from . import auth, db

DEMO_EMAIL = "owner@demo.com"
DEMO_PASSWORD = "demo1234"

POSITIVE = [
    "Butter chicken was superb and the staff were lovely.",
    "Quick service even though the place was packed.",
    "Best biryani we have had in months. Coming back on Friday.",
    "Loved the ambience, perfect for a family dinner.",
    "Portions are generous and the prices are fair.",
    "",
    "",
]
MIXED = [
    "Food was good but we waited 25 minutes for the starters.",
    "Tasty, though the music was far too loud.",
    "Decent meal, table was a bit sticky when we sat down.",
    "",
]
NEGATIVE = [
    "Order came out wrong twice and nobody apologised.",
    "The paneer was cold and the naan was stale.",
    "Waited 40 minutes, then were charged for a dish we never got.",
    "Washroom was not clean at all.",
]


def _reset(conn):
    for table in ("scans", "reviews", "tags", "users", "businesses"):
        conn.execute("DELETE FROM " + table)


def seed(reset: bool = False) -> None:
    db.init_db()
    random.seed(7)
    with db.get_db() as conn:
        if reset:
            _reset(conn)
        if db.one(conn, "SELECT id FROM users WHERE email = ?", (DEMO_EMAIL,)):
            print("Demo data already present. Use --reset to rebuild it.")
            return

        rid = db.execute(
            conn,
            """INSERT INTO businesses (slug, name, business_type, tagline, google_review_url,
                                       accent, positive_threshold, plan, subscription_status)
               VALUES (?,?,?,?,?,?,?,'active','active')""",
            (
                "spice-route",
                "Spice Route",
                "restaurant",
                "North Indian kitchen · since 2011",
                "https://www.google.com/maps",
                "#e0603a",
                4,
            ),
        )
        db.execute(
            conn,
            "INSERT INTO users (business_id, email, name, password_hash) VALUES (?,?,?,?)",
            (rid, DEMO_EMAIL, "Demo Owner", auth.hash_password(DEMO_PASSWORD)),
        )

        tag_ids = []
        for i in range(1, 9):
            zone = "Terrace" if i > 6 else "Main hall"
            tag_ids.append(
                db.execute(
                    conn,
                    "INSERT INTO tags (business_id, token, label, zone) VALUES (?,?,?,?)",
                    (rid, auth.new_token(), "Table %d" % i, zone),
                )
            )

        now = datetime.now(timezone.utc)
        for _ in range(140):
            when = now - timedelta(
                days=random.randint(0, 20), hours=random.randint(0, 23), minutes=random.randint(0, 59)
            )
            stamp = when.strftime("%Y-%m-%d %H:%M:%S")
            tag_id = random.choice(tag_ids)
            db.execute(
                conn,
                "INSERT INTO scans (tag_id, source, created_at) VALUES (?,?,?)",
                (tag_id, "web", stamp),
            )
            if random.random() > 0.62:  # not every tap becomes a review
                continue

            # table 3 is deliberately the weak spot, so the dashboard has a story to tell
            weights = [1, 2, 5, 6, 3] if tag_id == tag_ids[2] else [1, 2, 6, 22, 34]
            rating = random.choices([1, 2, 3, 4, 5], weights=weights)[0]
            if rating >= 4:
                comment = random.choice(POSITIVE)
            elif rating == 3:
                comment = random.choice(MIXED)
            else:
                comment = random.choice(NEGATIVE)

            def near(base):
                return max(1, min(5, base + random.choice([-1, 0, 0, 1])))

            has_contact = rating <= 3 and random.random() < 0.5
            db.execute(
                conn,
                """INSERT INTO reviews (business_id, tag_id, rating, quality, service, environment,
                                        value, comment, customer_name, customer_contact,
                                        routed_public, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    rid,
                    tag_id,
                    rating,
                    near(rating),
                    near(rating),
                    near(rating),
                    near(rating),
                    comment,
                    random.choice(["Aarav", "Priya", "Rohan", "Meera", ""]) if has_contact else "",
                    random.choice(["98765 43210", "guest@example.com"]) if has_contact else "",
                    1 if rating >= 4 else 0,
                    "resolved" if (rating <= 3 and random.random() < 0.4) else "new",
                    stamp,
                ),
            )

        total = db.one(conn, "SELECT COUNT(*) AS n FROM reviews")["n"]
        first_tag = db.one(conn, "SELECT token FROM tags WHERE business_id = ? LIMIT 1", (rid,))

    print("Seeded Spice Route with %d reviews across 8 tables." % total)
    print("  Owner login : %s / %s" % (DEMO_EMAIL, DEMO_PASSWORD))
    print("  Guest page  : http://127.0.0.1:8000/t/%s" % first_tag["token"])


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv)
