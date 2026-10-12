# Deploying ReviewHere

Target: **Fly.io**, one machine, SQLite on a persistent volume. Good for the first few
hundred businesses; the section at the bottom says what to change after that.

---

## 1. Install the CLI and sign in

```powershell
iwr https://fly.io/install.ps1 -useb | iex
```

Then, in a new terminal:

```bash
fly auth signup
```

A card is required even on the cheap plans — Fly bills roughly $3–5/month for this machine
plus about $0.15/GB for the volume.

## 2. Check the app name

`fly.toml` is set to `app = "reviewhere"`, region `bom` (Mumbai) — right for a `.in`
domain and Indian users. Fly app names are globally unique, so if `reviewhere` is
already taken, pick something like `reviewhere-app` and edit `fly.toml` to match. This
name only decides your `*.fly.dev` fallback address; guests will see `reviewhere.in`.

## 3. Create the app and its disk

```bash
fly launch --no-deploy --copy-config --name reviewhere
```

```bash
fly volumes create reviewhere_data --size 1 --region bom
```

1 GB holds millions of reviews. The volume name must match `source` in `fly.toml`.

## 4. Set the secret key

Generate one:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set it:

```bash
fly secrets set NFC_SECRET_KEY=paste-the-value-here
```

The app refuses to boot in production without this. It signs login cookies — if it ever
changes, everyone is logged out.

## 5. Deploy

```bash
fly deploy
```

```bash
fly open
```

You should land on the marketing page. Create your first business at `/signup`.

## 6. Point reviewhere.in at it

An apex domain (`reviewhere.in`, no `www`) needs real IP addresses — a `CNAME` is not
allowed at the apex, so allocate the app its own:

```bash
fly ips allocate-v4 --shared
```

```bash
fly ips allocate-v6
```

```bash
fly ips list
```

At your registrar's DNS panel, create two records pointing at what `fly ips list` printed:

| Type | Name | Value |
|---|---|---|
| `A` | `@` | the IPv4 from `fly ips list` |
| `AAAA` | `@` | the IPv6 from `fly ips list` |
| `CNAME` | `www` | `reviewhere.in` |

Then ask Fly for certificates — both names, so `www` doesn't throw a warning:

```bash
fly certs add reviewhere.in
```

```bash
fly certs add www.reviewhere.in
```

`fly certs show reviewhere.in` reports progress. DNS usually propagates in minutes on
`.in` domains but the registrar may claim up to 24 hours; the certificate issues on its
own once the records resolve. Once it goes green, pin the canonical address so generated
links and written tags always use it:

```bash
fly secrets set NFC_PUBLIC_BASE_URL=https://reviewhere.in
```

**Do this before writing any NFC tags.** Rewriting stickers already stuck to tables and counters is
the one genuinely painful mistake available here.

## 7. Turn on password-reset email

Without this, reset links are only written to the server log and you have to fish them
out with `fly logs` — fine while you're the only account, not fine once other businesses
sign up.

Any SMTP provider works. [Resend](https://resend.com) and [Brevo](https://brevo.com) both
have free tiers big enough for this. Once you have credentials:

```bash
fly secrets set NFC_SMTP_HOST=smtp.resend.com NFC_SMTP_PORT=587 NFC_SMTP_USER=resend NFC_SMTP_PASSWORD=your-api-key NFC_MAIL_FROM="ReviewHere <noreply@reviewhere.in>"
```

Set a support address too — it appears on the privacy page and the reset screens:

```bash
fly secrets set NFC_SUPPORT_EMAIL=you@reviewhere.in
```

Sending from your own domain needs SPF and DKIM records at your registrar; your provider
prints the exact records. Without them, resets land in spam.

Verify by using **Forgot your password?** on your own account.

## 8. Switch on payments (Razorpay)

Billing works without this — trials run, orders are recorded, the dashboard locks on
schedule — but no money can be collected until these are set.

**Start the KYC now, it is the long pole.** Razorpay needs your PAN, a bank account and
business proof, and approval typically takes 2–5 working days. You can build and test
against test keys meanwhile.

1. Sign up at [razorpay.com](https://razorpay.com) and complete KYC.
2. **Settings → API Keys** → generate keys. Test keys (`rzp_test_…`) work end to end
   without real money; switch to live keys once KYC clears.
3. **Subscriptions → Plans** → create a monthly plan at your subscription price. Copy the
   plan id (`plan_…`). It must match `NFC_SUBSCRIPTION_PRICE_PAISE`, which is only used
   for what is *displayed* — Razorpay charges what the plan says.
4. **Settings → Webhooks** → add `https://reviewhere.in/api/razorpay/webhook`, set a
   webhook secret, and subscribe to these events:
   `order.paid`, `payment.captured`, `subscription.activated`, `subscription.charged`,
   `subscription.halted`, `subscription.cancelled`.
5. Set everything as secrets:

```bash
fly secrets set NFC_RAZORPAY_KEY_ID=rzp_live_xxx NFC_RAZORPAY_KEY_SECRET=xxx NFC_RAZORPAY_WEBHOOK_SECRET=xxx NFC_RAZORPAY_PLAN_ID=plan_xxx
```

Prices are configurable without touching code:

```bash
fly secrets set NFC_TAG_PRICE_PAISE=30000 NFC_SUBSCRIPTION_PRICE_PAISE=49900 NFC_TRIAL_DAYS=7
```

### How the money flows

- **Tag orders** are one-time payments, priced per tag. A business can order during the
  trial, because that is hardware they are buying from you. Orders record a shipping
  address — you fulfil them yourself; there is no courier integration.
- **The subscription** is free for `NFC_TRIAL_DAYS`, then monthly. Recurring payments in
  India need an e-mandate (UPI Autopay or card mandate); Razorpay Subscriptions handles
  that authorisation flow on its own hosted page.
- **The webhook is the authority.** The browser callback is verified too, but a customer
  closing the tab mid-payment must not lose their purchase, so state is only trusted from
  the signed webhook. Replayed events are ignored by event id.

### What happens when a subscription lapses

The dashboard locks to the billing page. **Review collection never stops** — the tags keep
working and reviews keep arriving. That is deliberate: cutting off a business's customers
punishes the wrong people, and the pile of unread reviews is the reason to subscribe.

### Before you charge anyone

Selling physical tags plus a subscription means invoicing, and in India that means GST
once you cross the registration threshold. Talk to an accountant before the first sale —
this app records orders and payments but does not issue tax invoices.

---

## Backups

The volume is not a backup. Fly snapshots it daily (5-day retention), which you can list
with `fly volumes snapshots list <volume-id>`. For something you can actually hold:

```bash
fly ssh console -C "sqlite3 /data/nfc.db .dump" > backup-$(date +%F).sql
```

Run that weekly. Restoring is `sqlite3 nfc.db < backup.sql` into a fresh volume.

## Day-to-day commands

| Task | Command |
|---|---|
| Watch logs | `fly logs` |
| Open a shell | `fly ssh console` |
| Restart | `fly apps restart YOUR-APP-NAME` |
| Check health | `curl https://your-domain/api/health` |
| Close public signup | `fly secrets set NFC_SIGNUP_CLOSED=1` |

## Keep it to one machine

SQLite lives on one disk attached to one machine. `fly scale count 2` would give the second
machine its own empty volume and split your data in half. Don't scale horizontally without
first moving to Postgres.

Vertical scaling is fine:

```bash
fly scale vm shared-cpu-2x --memory 1024
```

## When to outgrow this setup

This design is comfortable to roughly **500 businesses / 50k reviews a month**. Move to
Postgres when you need any of:

- more than one machine, or machines in several regions
- zero-downtime deploys (right now a deploy is a few seconds of restart)
- a read replica for reporting

The migration is contained: every query lives in `app/db.py`, `app/dashboard.py` and
`app/main.py`, all use `?` placeholders and plain SQL. Swapping in `asyncpg` or SQLAlchemy
means rewriting placeholders to `$1` and replacing the `datetime('now', ...)` calls with
`now() - interval`.

## What is already handled

- **HTTPS** — forced by `force_https` in `fly.toml`; Fly issues the certificate.
- **Real client IPs** — `--proxy-headers` plus the `fly-client-ip` header, so rate limits
  apply per visitor, not per proxy.
- **Rate limits** — 30 reviews/hour per IP, 15/hour per tag, 10 signups/hour per IP,
  10 login attempts per 5 minutes. In-memory, resets on deploy.
- **Spam** — a hidden honeypot field on both the review and signup forms.
- **Search engines** — guest pages and dashboards send `X-Robots-Tag: noindex`, and
  `/robots.txt` disallows them. Customer comments should never be indexed.
- **Timezones** — each business picks its own; stored times stay UTC.
- **Schema changes** — additive migrations run on boot from `MIGRATIONS` in `app/db.py`.

- **Password reset** — self-service, tokens stored hashed, single use, one-hour expiry.
  Needs SMTP (step 7) to actually reach anyone.
- **Privacy page** at `/privacy`, linked from every guest review page.

## Not handled yet

- **Email notification** on a bad review — the dashboard is pull-only, so somebody has to
  open it. The most valuable next feature.
- **Self-service review deletion** — requests are handled by hand today. Fine at small
  scale; revisit if you take EU data in volume.
- **Billing** — no payment or plan logic anywhere.
