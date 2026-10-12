# Quickstart — testing with your real NFC tags before deploying

Goal of this page: get the app running on your PC and have a real sticker on a real table
open a real review page on your phone. No hosting, no domain, no cost.

The only limitation is that this works **on your wifi only** — the address is your PC's
local one, so it stops working the moment the phone leaves the network or the PC sleeps.
That's fine for testing; it's the reason you deploy afterwards.

---

## 1. Install and run

From the project folder, in PowerShell:

```powershell
python -m venv .venv
```

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

```powershell
.venv\Scripts\python.exe -m app.seed
```

```powershell
.venv\Scripts\python.exe run.py
```

Leave that window open — closing it stops the server. Open http://127.0.0.1:8000 and log
in with `owner@demo.com` / `demo1234`.

## 2. Make your own business

The demo data is there so the dashboard isn't empty. For a real test, create your own at
http://127.0.0.1:8000/signup — pick the real number of spots. You'll get one tag per
spot, each with its own link.

`python -m app.seed --reset` wipes everything and rebuilds the demo, if you want a clean
start.

## 3. Find your PC's address on the wifi

```powershell
ipconfig
```

Look for **IPv4 Address** under your wifi adapter — something like `192.168.1.7`. Your
guest links are then:

```
http://192.168.1.7:8000/t/<token>
```

Take the token from the Tags page in the dashboard. Test it in your phone's browser first,
with the phone on the same wifi. If it doesn't load, it is almost always Windows Firewall:
allow Python on private networks when prompted, or run

```powershell
New-NetFirewallRule -DisplayName "ReviewHere dev" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow -Profile Private
```

## 4. Write a tag

The in-app **Write stickers** page needs HTTPS, so it will not work against this local
address. For local testing, use the free **NFC Tools** app (Android or iPhone):

1. Open NFC Tools → **Write** → **Add a record** → **URL/URI**.
2. Paste `http://192.168.1.7:8000/t/<token>` — use your own IP and token.
3. Tap **Write**, then hold the sticker to the back of the phone.
4. **Do not lock the tag.** Locking is permanent, and you are going to rewrite this one
   with the real address later.

Now tap the sticker with your phone. The review page should open on its own.

## 5. What to actually check

- Does the page open **without unlocking into an app** — just a tap on the lock screen?
- Leave **5 stars**: do you get the "Post this on Google" button? (Add your Google review
  link in Settings first, or nothing public happens.)
- Leave **2 stars**: does it stay private and ask how to reach you?
- Does the review appear in the dashboard with the right table name?
- Try it through the acrylic table tent, not just bare in your hand — the read distance
  through plastic is what matters in a real restaurant.
- Try it on a second phone, ideally an iPhone, to see the difference in tap behaviour.

## 6. Then deploy

Once the flow feels right, follow [DEPLOY.md](DEPLOY.md). After the domain is live you
rewrite each sticker with `https://reviewhere.in/t/<token>` — same tags, same tokens, one
rewrite. **That** is when you lock them, if you want to.

---

## Useful commands

| Task | Command |
|---|---|
| Run the server | `.venv\Scripts\python.exe run.py` |
| Rebuild demo data | `.venv\Scripts\python.exe -m app.seed --reset` |
| Run all 69 tests | `.venv\Scripts\python.exe -m tests.smoke` |
| Reset a forgotten password | see `/forgot` — the link is printed in the server window |
