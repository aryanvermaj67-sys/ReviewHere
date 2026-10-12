"""Billing pages and the Razorpay callback/webhook endpoints."""
import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import billing, config, db
from .deps import redirect, require_user, templates

router = APIRouter()


def _business(conn, business_id: int):
    return db.one(conn, "SELECT * FROM businesses WHERE id = ?", (business_id,))


# --------------------------------------------------------------------------- #
# the billing page
# --------------------------------------------------------------------------- #
@router.get("/dashboard/billing", response_class=HTMLResponse)
def billing_page(request: Request, user=Depends(require_user), msg: str = "", error: str = ""):
    with db.get_db() as conn:
        billing.expire_lapsed_trials(conn)
        business = _business(conn, user["business_id"])
        orders = db.query(
            conn,
            "SELECT * FROM orders WHERE business_id = ? ORDER BY id DESC LIMIT 20",
            (user["business_id"],),
        )
        tag_count = db.one(
            conn, "SELECT COUNT(*) AS n FROM tags WHERE business_id = ?", (user["business_id"],)
        )["n"]
    return templates.TemplateResponse(
        request, "billing.html",
        {
            "user": user,
            "page": "billing",
            "b": business,
            "s": billing.status(business),
            "orders": orders,
            "tag_count": tag_count,
            "tag_price_paise": config.TAG_PRICE_PAISE,
            "razorpay_key": config.RAZORPAY_KEY_ID,
            "msg": msg,
            "error": error,
            "rupees": billing.rupees,
        },
    )


# --------------------------------------------------------------------------- #
# one-off tag orders
# --------------------------------------------------------------------------- #
@router.post("/dashboard/billing/order")
def create_order(
    user=Depends(require_user),
    quantity: int = Form(...),
    ship_name: str = Form(""),
    ship_phone: str = Form(""),
    ship_address: str = Form(""),
    note: str = Form(""),
):
    quantity = max(1, min(500, quantity))
    amount = quantity * config.TAG_PRICE_PAISE
    with db.get_db() as conn:
        order_id = db.execute(
            conn,
            """INSERT INTO orders (business_id, quantity, unit_paise, amount_paise, currency,
                                   ship_name, ship_phone, ship_address, note)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (user["business_id"], quantity, config.TAG_PRICE_PAISE, amount, config.CURRENCY,
             ship_name.strip()[:80], ship_phone.strip()[:20], ship_address.strip()[:400],
             note.strip()[:300]),
        )
        if config.PAYMENTS_CONFIGURED:
            try:
                remote = billing.create_tag_order(order_id, quantity, amount)
            except billing.PaymentError as exc:
                db.execute(conn, "UPDATE orders SET status = 'failed' WHERE id = ?", (order_id,))
                return redirect("/dashboard/billing?error=" + str(exc)[:120].replace(" ", "+"))
            db.execute(
                conn,
                "UPDATE orders SET provider_order_id = ? WHERE id = ?",
                (remote.get("id", "")[:60], order_id),
            )
    if not config.PAYMENTS_CONFIGURED:
        return redirect("/dashboard/billing?msg=Order+recorded.+Payments+are+not+live+yet,"
                        "+so+it+is+marked+unpaid.")
    return redirect("/dashboard/billing/pay/%d" % order_id)


@router.get("/dashboard/billing/pay/{order_id}", response_class=HTMLResponse)
def pay_page(request: Request, order_id: int, user=Depends(require_user)):
    with db.get_db() as conn:
        order = db.one(
            conn,
            "SELECT * FROM orders WHERE id = ? AND business_id = ?",
            (order_id, user["business_id"]),
        )
        business = _business(conn, user["business_id"])
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return templates.TemplateResponse(
        request, "pay.html",
        {
            "user": user,
            "order": order,
            "b": business,
            "razorpay_key": config.RAZORPAY_KEY_ID,
            "rupees": billing.rupees,
        },
    )


@router.post("/dashboard/billing/confirm")
def confirm_payment(
    user=Depends(require_user),
    razorpay_order_id: str = Form(""),
    razorpay_payment_id: str = Form(""),
    razorpay_signature: str = Form(""),
):
    """Checkout's browser callback. The webhook is the authority; this just gives
    the buyer immediate feedback and marks the order without waiting."""
    if not billing.verify_payment_signature(
        razorpay_order_id, razorpay_payment_id, razorpay_signature
    ):
        return redirect("/dashboard/billing?error=Payment+could+not+be+verified")
    with db.get_db() as conn:
        order = db.one(
            conn,
            "SELECT * FROM orders WHERE provider_order_id = ? AND business_id = ?",
            (razorpay_order_id, user["business_id"]),
        )
        if order is None:
            return redirect("/dashboard/billing?error=Order+not+found")
        billing.mark_order_paid(conn, order["id"], razorpay_payment_id)
    return redirect("/dashboard/billing?msg=Payment+received.+Your+tags+are+on+the+way.")


# --------------------------------------------------------------------------- #
# subscription
# --------------------------------------------------------------------------- #
@router.post("/dashboard/billing/subscribe")
def subscribe(user=Depends(require_user)):
    if not config.PAYMENTS_CONFIGURED:
        return redirect("/dashboard/billing?error=Payments+are+not+configured+yet")
    with db.get_db() as conn:
        business = _business(conn, user["business_id"])
        if business["subscription_id"]:
            return redirect("/dashboard/billing?msg=A+subscription+already+exists")
        try:
            remote = billing.create_subscription(user["business_id"], user["email"])
        except billing.PaymentError as exc:
            return redirect("/dashboard/billing?error=" + str(exc)[:120].replace(" ", "+"))
        db.execute(
            conn,
            "UPDATE businesses SET subscription_id = ?, subscription_status = ? WHERE id = ?",
            (remote.get("id", "")[:60], remote.get("status", "created")[:30], user["business_id"]),
        )
    # Razorpay hosts the mandate/authorisation page for subscriptions.
    short_url = remote.get("short_url")
    if short_url:
        return redirect(short_url)
    return redirect("/dashboard/billing?msg=Subscription+created")


# --------------------------------------------------------------------------- #
# webhook - the authority on what has actually been paid
# --------------------------------------------------------------------------- #
@router.post("/api/razorpay/webhook")
async def razorpay_webhook(request: Request):
    raw = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")
    if not billing.verify_webhook(raw, signature):
        # Never leak whether the secret is merely unset versus wrong.
        return JSONResponse({"ok": False}, status_code=400)

    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return JSONResponse({"ok": False}, status_code=400)

    event_type = event.get("event", "")
    event_id = request.headers.get("x-razorpay-event-id", "") or event_type + str(
        event.get("created_at", "")
    )
    payload = event.get("payload", {})

    with db.get_db() as conn:
        if billing.seen_event(conn, event_id, event_type, raw.decode(errors="replace")):
            return {"ok": True, "duplicate": True}

        if event_type in ("order.paid", "payment.captured"):
            entity = (payload.get("payment") or payload.get("order") or {}).get("entity", {})
            provider_order_id = entity.get("order_id") or entity.get("id") or ""
            payment_id = entity.get("id", "")
            order = db.one(
                conn, "SELECT * FROM orders WHERE provider_order_id = ?", (provider_order_id,)
            )
            if order is not None:
                billing.mark_order_paid(conn, order["id"], payment_id)

        elif event_type.startswith("subscription."):
            entity = (payload.get("subscription") or {}).get("entity", {})
            subscription_id = entity.get("id", "")
            state = entity.get("status", "")
            business_id = int((entity.get("notes") or {}).get("business_id") or 0)
            if not business_id and subscription_id:
                row = db.one(
                    conn, "SELECT id FROM businesses WHERE subscription_id = ?", (subscription_id,)
                )
                business_id = row["id"] if row else 0
            if business_id:
                period_end = str(entity.get("current_end") or "")
                billing.activate_subscription(
                    conn, business_id, subscription_id, state, period_end
                )

    return {"ok": True}
