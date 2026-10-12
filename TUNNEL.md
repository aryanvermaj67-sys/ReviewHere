# Going live for free with a Cloudflare Tunnel

This puts `https://reviewhere.in` on the real internet, with a real certificate, served
from your own PC. It costs nothing.

**The trade-off, stated plainly:** your PC is the server. If it sleeps, loses wifi, or
shuts down, the site is down and a customer tapping a tag sees an error. That is fine for
testing your tags and showing a demo. It is not fine for a restaurant paying you ₹499 a
month. Move to Fly.io (see DEPLOY.md) when you have a real customer — **you will not have
to rewrite the tags**, because the address stays `reviewhere.in` either way.

---

## Step 1 — Create a free Cloudflare account

Sign up at [dash.cloudflare.com/sign-up](https://dash.cloudflare.com/sign-up). No card needed.

## Step 2 — Add reviewhere.in to Cloudflare

1. **Add a site** → type `reviewhere.in` → **Continue**
2. Choose the **Free** plan
3. Cloudflare scans your existing DNS records and shows them. Fine — continue.
4. Cloudflare gives you **two nameservers**, something like:
   `xxx.ns.cloudflare.com` and `yyy.ns.cloudflare.com`

Copy both.

## Step 3 — Point GoDaddy at Cloudflare

1. Sign in to GoDaddy → **My Products** → reviewhere.in → **DNS** → **Nameservers**
2. **Change** → **I'll use my own nameservers**
3. Paste the two Cloudflare nameservers, replacing GoDaddy's
4. Save

This hands DNS control to Cloudflare. It usually takes 10 minutes to a few hours;
Cloudflare emails you when the domain is active. **Wait for that email before Step 6.**

While you wait, do steps 4 and 5.

## Step 4 — Install cloudflared

In PowerShell:

```powershell
winget install --id Cloudflare.cloudflared
```

Close and reopen the terminal, then check:

```powershell
cloudflared --version
```

## Step 5 — Log cloudflared in

```powershell
cloudflared tunnel login
```

A browser opens. Pick **reviewhere.in** and authorise. This saves a certificate to
`C:\Users\OMEN\.cloudflared\cert.pem`.

## Step 6 — Create the tunnel

```powershell
cloudflared tunnel create reviewhere
```

It prints a tunnel **ID** (a long uuid) and writes a credentials file next to the cert.
Then attach your domain to it:

```powershell
cloudflared tunnel route dns reviewhere reviewhere.in
```

```powershell
cloudflared tunnel route dns reviewhere www.reviewhere.in
```

Each of those creates a DNS record in Cloudflare automatically. HTTPS is issued by
Cloudflare — there is nothing to configure.

## Step 7 — Tell the tunnel where your app is

Create `C:\Users\OMEN\.cloudflared\config.yml` with this content, replacing
`YOUR-TUNNEL-ID` with the id from step 6:

```yaml
tunnel: YOUR-TUNNEL-ID
credentials-file: C:\Users\OMEN\.cloudflared\YOUR-TUNNEL-ID.json

ingress:
  - hostname: reviewhere.in
    service: http://127.0.0.1:8000
  - hostname: www.reviewhere.in
    service: http://127.0.0.1:8000
  - service: http_status:404
```

A copy of this template is in `cloudflared-config.example.yml` in this folder.

## Step 8 — Run both halves

Two windows, both must stay open.

**Window 1 — the app:**

```powershell
.\serve_public.ps1
```

**Window 2 — the tunnel:**

```powershell
cloudflared tunnel run reviewhere
```

Now open `https://reviewhere.in` on your phone, on mobile data with wifi off. That proves
it is genuinely on the internet and not just on your network.

---

## Then, before writing tags

1. Log in at `https://reviewhere.in/login`
2. **Settings** → paste your real Google review link (`https://g.page/r/.../review`)
3. **Settings** → choose how a tap behaves (straight to Google, or ask for a rating first)
4. **Tags → Write stickers** on an Android phone — Web NFC now works, because the site is
   finally on HTTPS
5. Tap one sticker, confirm where it lands, and only then write the rest

## Keeping it running

By default the tunnel stops when you close its window. To keep it alive across reboots,
install it as a Windows service (run PowerShell **as Administrator**):

```powershell
cloudflared service install
```

The app itself still needs `serve_public.ps1` running. Also set your PC to never sleep:
**Settings → System → Power → Screen and sleep → Sleep: Never**.

## When you outgrow this

Follow DEPLOY.md to move onto Fly.io. The only change is where `reviewhere.in` points —
your tags, tokens, links and QR codes all keep working untouched. Copy `data\nfc.db` up to
the new server and nothing is lost.
