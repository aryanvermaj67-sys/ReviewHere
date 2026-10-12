# ReviewHere — NFC review system for any business

A customer taps an NFC sticker with their phone, the phone opens a rating page for that exact
spot — table, counter, treatment room, service desk — and the software decides what happens next:

- **4–5 stars** → the customer is thanked and offered a one-tap button to post the review on Google.
- **1–3 stars** → the review stays private, goes to the owner's inbox with the customer's contact
  details, and never reaches a public review site.

The owner sees everything live: average rating, 7-day trend, tap-to-review conversion, ratings per
category, and which *spot* is dragging the score down.

## Works for any commercial use

One business type is chosen at signup and decides the wording throughout — what a tag is stuck to
and what the four rating categories are called. The database schema is identical for all of them,
so every type shares one dashboard and one set of statistics.

| Type | Tag is stuck to a | Categories |
|---|---|---|
| Restaurant, café, bar | Table | Food · Service · Ambience · Value |
| Shop or retail store | Counter | Products · Staff · Store · Value |
| Salon, spa, barber | Station | Result · Staff · Cleanliness · Value |
| Clinic, hospital, dentist | Room | Care · Staff · Facilities · Value |
| Hotel or guest house | Room | Room · Staff · Facilities · Value |
| Office or service desk | Desk | Outcome · Staff · Facilities · Value |
| Gym or fitness studio | Station | Equipment · Trainers · Cleanliness · Value |
| Something else | Spot | Quality · Service · Place · Value |

Changing type later relabels everything and leaves existing scores untouched. Add or edit a type in
`app/business_types.py` — one dictionary entry, no schema change.

No hardware is needed to run it. The guest's phone is the NFC reader, and the tag is just a URL.

---

## Run it

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m app.seed
.venv/Scripts/python.exe run.py
```

Open http://127.0.0.1:8000

| What | Where |
|---|---|
| Owner login | `owner@demo.com` / `demo1234` |
| Dashboard | `/dashboard` |
| Guest page | `/t/<tag-token>` (each spot has its own) |
| Business-wide page | `/r/spice-route` |
| API docs | `/api/docs` |

`python -m app.seed --reset` wipes the database and rebuilds the demo business with 8 tables and
about 95 reviews spread over the last three weeks.

Run the end-to-end test suite (123 checks, uses a throwaway database):

```bash
.venv/Scripts/python.exe -m tests.smoke
```

---

## How the pieces fit

```
app/
  main.py           public routes: tap page, review submit, thanks, QR, signup, login, /api/scan
  dashboard.py      owner routes: stats, review inbox, tag CRUD, settings, CSV export
  billing.py        trial/subscription rules, Razorpay client, money formatting
  billing_routes.py billing page, tag orders, checkout callback, webhook
  business_types.py per-industry wording presets
  db.py             SQLite schema + connection helpers (raw SQL, no ORM)
  auth.py           PBKDF2 hashing, HMAC-signed cookies, tag + reset tokens
  config.py         every environment variable, in one place
  limits.py         in-process rate limiting for public endpoints
  mailer.py         SMTP for password resets
  deps.py           template environment, current-user lookup, access gating
  seed.py           demo business + realistic review history
  templates/        Jinja2 pages
  static/           one stylesheet
tests/smoke.py      end-to-end test of every route
data/nfc.db         SQLite database (created on first run)
```

### Data model

- `businesses` — name, business type, branding, Google review link, threshold, plan and trial dates
- `users` — owner logins, one business each
- `tags` — one row per physical sticker: `token` (what the tag encodes), label, zone, chip `uid`
- `scans` — every tap, so conversion can be measured
- `reviews` — rating, four optional sub-ratings, comment, customer contact, routing + status
- `orders` — one-time tag purchases in paise, with shipping details and payment reference
- `billing_events` — every webhook seen, so a replay cannot be processed twice
- `password_resets` — hashed, single-use, one-hour tokens

### The routing rule

`positive_threshold` (default 4) is set per business. A review is marked `routed_public` only when
the rating is at or above the threshold **and** a Google review link is configured. Everything below
it lands in the inbox as `new` until the owner adds a note and marks it resolved. If no Google link
is set, every review stays private.

**`routing`** decides who is invited to post publicly:

- **`open`** (default) — every rater is offered the Google button. An unhappy customer is
  apologised to first, told it reached the manager, and *then* offered the link. This is
  what Google's review policy expects.
- **`gated`** — only ratings at or above the threshold see it. Higher conversion, but this
  is review gating, which Google prohibits and can penalise a business profile for.

Private feedback is captured in full under both.

**`tap_mode`** decides what a tap does at all:

- **`rate_first`** (default) — the tag opens your rating page, and the routing rule below applies.
- **`direct`** — the tag is just a shortcut to your Google review page. Every customer is
  forwarded there, happy or not. Taps are still counted, but no rating, comment or contact
  is collected and nothing reaches your inbox. With no Google link configured it falls back
  to the rating page rather than stranding anyone.

In `rate_first`, sending a happy customer onward is a **choice, not a rule**:

- **Default** — the thanks page offers a *Post this on Google* button. The customer decides.
- **`google_auto` on** (Settings → After a good rating) — the page hands over to Google
  automatically after a moment, with the button still there if the browser blocks it.

With no Google link configured, neither happens and every review stays private.

---

## Adding the hardware later

The software is already shaped for it — nothing here needs rewriting.

**The simplest path (no reader at all).** Buy NTAG213/215 stickers, write the tag's URL from the
Tags page onto each one with a phone app such as NFC Tools, and lock it. Any modern phone reads it
by touch. `Dashboard → Tags → Printable QR sheet` prints table cards with a QR that points at the
same URL, so the flow works today while the stickers are in the post.

**If you build your own reader** (ESP32 + PN532, or a USB desk reader), it posts to:

```
POST /api/scan
{"token": "wyzxefmx", "source": "esp32"}          # token written on the tag
{"uid": "04:A2:B3:C4", "source": "esp32"}          # or the raw chip UID
→ {"ok": true, "url": "http://host/t/wyzxefmx", "tag": {...}, "business": {...}}
```

The UID route works once you paste each chip's UID into the Chip UID field on the Tags page. Every
call is logged as a scan, so the conversion stats stay correct whichever reader is used.

---

## Deploying

See **[DEPLOY.md](DEPLOY.md)** for the full Fly.io walkthrough. In short:

```bash
fly launch --no-deploy --copy-config --name YOUR-APP-NAME
```

```bash
fly volumes create reviewhere_data --size 1 --region bom
```

```bash
fly secrets set NFC_SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
```

```bash
fly deploy
```

Businesses then sign themselves up at `/signup`, which creates their dashboard, their
tags and their 7-day trial. Set `NFC_PUBLIC_BASE_URL` to your real domain **before** writing
any NFC tags.

## Billing

- **7-day free trial** on signup, no card. Then ₹499/month (configurable).
- **Tags are sold separately** at ₹300 each as a one-time order, with a shipping address
  captured. Orderable during the trial.
- When a subscription lapses the dashboard locks, but **guest pages keep working and
  reviews keep arriving** — see `app/billing.py` for why.
- Razorpay over plain HTTPS, no SDK. Webhooks are HMAC-verified and replay-protected.
- Everything is stored in **paise as integers**; no float ever touches money.

Prices, trial length and keys are environment variables — see `.env.example` and
DEPLOY.md step 8.

## Known gaps

- **No alert on a bad review.** A 1-star review waits in the dashboard until someone opens
  it. SMTP is already wired for password resets, so this is a short job — and the most
  valuable thing left to build.
- **No tax invoices.** Orders and payments are recorded, but nothing issues a GST invoice.
- **One machine only.** SQLite sits on one Fly volume; `fly scale count 2` would split
  your data. Move to Postgres before scaling out.
- **Customer contact details are stored in plain text**, and deletion requests are handled
  by hand. Fine at small scale; revisit before taking EU data in volume.
- **No fulfilment tracking.** A paid tag order tells you what to post and where, but you
  mark it shipped in the database yourself.
