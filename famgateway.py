"""FamGateway (UPI) payments
===========================
Docs: https://famgateway.in/docs.php

How a purchase works
--------------------
1. The user taps a plan / credit pack in the bot.
2. `start_checkout()` creates an order (POST /api/create-order), sends the
   user the UPI QR image + a "pay in UPI app" button, and saves a pending
   transaction whose `_id` is the FamGateway `order_id`.
3. Two independent things confirm the payment (whichever is first wins):
     a. a background poller (`_poll_order`) that asks the gateway every
        few seconds (GET /api/verify-order.php, authenticated), and
     b. the FamGateway webhook (POST /famgateway/webhook), which is
        HMAC-verified AND re-confirmed with the gateway before anything
        is granted.
4. `fulfill_order()` atomically claims the transaction (so it can only ever
   run once), then grants premium days or credits, notifies the user and
   pays out the referral reward.

This module only handles the *money* side. Premium, credits and referral
logic live in premium.py / credits.py / referral.py.

Required environment variables:
  FAMGATEWAY_API_KEY  - your FamGateway API key (dashboard -> API Keys)
  MONGO_URI           - MongoDB connection string (see db.py)

Recommended:
  BASE_URL            - public https URL of this service, e.g.
                        https://your-app.onrender.com
                        Enables the webhook (payments still confirm by
                        polling without it, but the webhook is a safety net
                        if the bot restarts mid-payment).

Optional:
  FAMGATEWAY_BASE_URL - default https://famgateway.in
"""

import os
import hmac
import time
import json
import hashlib
import asyncio
import logging
from datetime import datetime

import aiohttp
from aiohttp import web
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

import db
import premium
import credits
import referral

logger = logging.getLogger(__name__)

FAMGATEWAY_API_KEY = os.environ.get("FAMGATEWAY_API_KEY", "").strip()
FAMGATEWAY_BASE_URL = os.environ.get("FAMGATEWAY_BASE_URL", "https://famgateway.in").rstrip("/")
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

ORDER_TTL_SECONDS = 300        # FamGateway orders expire after 5 minutes
POLL_INTERVAL_SECONDS = 4      # docs: poll every 3-5 s (faster hits a lock)
POLL_GRACE_SECONDS = 30        # keep polling a little past expiry


def is_configured() -> bool:
    return bool(FAMGATEWAY_API_KEY)


class FamGatewayError(Exception):
    """Gateway rejected the request or could not be reached."""


# --------------------------------------------------------------------------
# Low-level API client
# --------------------------------------------------------------------------

_session: aiohttp.ClientSession | None = None


def _http() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session


async def _call(method: str, path: str, *, json_body: dict | None = None,
                params: dict | None = None) -> tuple[int, dict]:
    """Returns (http_status, parsed_json_dict). Never raises for HTTP error
    codes (the gateway answers 404/408 with a JSON body we want to read);
    raises FamGatewayError only for network problems."""
    if not FAMGATEWAY_API_KEY:
        raise FamGatewayError("FAMGATEWAY_API_KEY is not set")
    # Header auth is the recommended way (keeps the key out of URLs/logs).
    headers = {"X-Api-Key": FAMGATEWAY_API_KEY, "Accept": "application/json"}
    try:
        async with _http().request(
            method, f"{FAMGATEWAY_BASE_URL}{path}",
            headers=headers, json=json_body, params=params,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            try:
                data = await resp.json(content_type=None)
            except Exception:
                data = {}
            return resp.status, (data if isinstance(data, dict) else {})
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise FamGatewayError(f"network error: {exc}") from exc


async def create_order(amount, customer_name: str | None = None,
                       redirect_url: str | None = None,
                       webhook_url: str | None = None) -> dict:
    """POST /api/create-order. Returns the gateway's `data` object:
    order_id, amount, payable_amount, upi_id, qr_url, checkout_url,
    upi_intent, created_at_ist, expires_at_ist."""
    body: dict = {"amount": round(float(amount), 2)}
    if customer_name:
        body["customer_name"] = customer_name
    if redirect_url:
        body["redirect_url"] = redirect_url
    if webhook_url:
        body["webhook_url"] = webhook_url
    status, data = await _call("POST", "/api/create-order", json_body=body)
    order = data.get("data")
    if status != 200 or data.get("status") != "success" or not order or not order.get("order_id"):
        raise FamGatewayError(data.get("message") or f"HTTP {status}")
    return order


async def get_order_status(order_id: str) -> dict:
    """GET /api/verify-order.php (server-to-server, authoritative).

    Returns {"state": "success" | "pending" | "expired" | "error", ...}
    and, for "success": utr, amount, sender_name."""
    status, data = await _call("GET", "/api/verify-order.php", params={"order_id": order_id})
    if status == 401:
        # Header auth was refused — retry once with the query-param form
        # the docs also support.
        status, data = await _call(
            "GET", "/api/verify-order.php",
            params={"order_id": order_id, "api_key": FAMGATEWAY_API_KEY},
        )
    st = data.get("status")
    if st == "success":
        d = data.get("data") if isinstance(data.get("data"), dict) else data
        return {
            "state": "success",
            "utr": str(d.get("utr") or ""),
            "amount": d.get("amount"),
            "sender_name": d.get("sender_name"),
        }
    if st == "pending":
        return {"state": "pending"}
    if st == "expired" or status == 408:
        return {"state": "expired"}
    return {"state": "error", "message": data.get("message") or f"HTTP {status}"}


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """Webhook is signed with HMAC-SHA256(raw body) using the API key as
    the secret (header X-FamGateway-Signature). Missing signature = reject."""
    if not signature or not FAMGATEWAY_API_KEY:
        return False
    expected = hmac.new(FAMGATEWAY_API_KEY.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


# --------------------------------------------------------------------------
# Fulfilment (runs exactly once per paid order)
# --------------------------------------------------------------------------

async def _delete_qr_message(bot, txn: dict) -> None:
    msg_id = txn.get("qr_message_id")
    if not msg_id:
        return
    try:
        await bot.delete_message(chat_id=txn["chat_id"], message_id=msg_id)
    except Exception:
        pass  # already deleted / too old — not important


async def fulfill_order(order_id: str, bot, utr: str = "", paid_amount=None) -> str:
    """Grants what the order was for. Safe to call from both the poller and
    the webhook; only the first caller does anything.

    Returns "fulfilled", "already", "unknown" or "amount_mismatch"."""
    txn = await db.get_transaction(order_id)
    if not txn:
        return "unknown"
    if txn.get("status") == "success":
        return "already"

    # Never grant more than was paid.
    if paid_amount is not None:
        try:
            if float(paid_amount) + 0.001 < float(txn["amount"]):
                logger.error("Order %s paid %s but expected %s", order_id, paid_amount, txn["amount"])
                return "amount_mismatch"
        except (TypeError, ValueError):
            pass

    if not await db.claim_transaction_success(order_id, utr or None):
        return "already"

    kind = txn.get("kind", "premium")
    try:
        if kind == "credits":
            credits_bought = int(txn.get("credits", 0))
            new_balance = await credits.add_credits(txn["user_id"], credits_bought)
            message = (
                "✅ Payment successful!\n\n"
                f"🪙 {credits_bought} credits added. New balance: {new_balance}."
            )
        else:
            # Extends on top of any remaining premium, never shortens it.
            new_expiry_iso = await premium.grant_premium_days(txn["user_id"], int(txn["plan_days"]))
            expiry = datetime.fromisoformat(new_expiry_iso).strftime("%d %b %Y")
            message = (
                "✅ Payment successful!\n\n"
                f"Your {txn.get('plan_label', premium.PLAN_LABEL)} is active until {expiry}."
            )
    except Exception:
        await db.unclaim_transaction(order_id)  # let a retry succeed
        raise

    await _delete_qr_message(bot, txn)
    try:
        await bot.send_message(chat_id=txn["chat_id"], text=message)
    except Exception:
        logger.exception("Could not notify user %s", txn["user_id"])

    # Refer & Earn: a referred user's first purchase pays their referrer.
    try:
        await referral.maybe_reward_referral_credits(txn["user_id"], bot)
    except Exception:
        logger.exception("Referral reward failed for user %s", txn["user_id"])

    return "fulfilled"


async def _expire_order(bot, order_id: str) -> None:
    """Marks a still-pending order expired and tidies the chat. (If the
    user pays late, the webhook can still fulfil it — see fulfill_order.)"""
    res = await db.get_db().transactions.update_one(
        {"_id": order_id, "status": "pending"}, {"$set": {"status": "expired"}}
    )
    if res.modified_count != 1:
        return
    txn = await db.get_transaction(order_id)
    if not txn:
        return
    await _delete_qr_message(bot, txn)
    try:
        await bot.send_message(
            chat_id=txn["chat_id"],
            text="⌛ Payment window expired (5 min). No money was taken — tap /buy to try again.\n\n"
                 "Already paid? It will be credited automatically within a minute or two.",
        )
    except Exception:
        logger.exception("Could not send expiry notice to %s", txn.get("user_id"))


# --------------------------------------------------------------------------
# Background polling (one task per pending order)
# --------------------------------------------------------------------------

_poll_tasks: dict[str, asyncio.Task] = {}


def start_polling(bot, order_id: str, deadline_ts: float | None = None) -> None:
    existing = _poll_tasks.get(order_id)
    if existing and not existing.done():
        return
    if deadline_ts is None:
        deadline_ts = time.time() + ORDER_TTL_SECONDS + POLL_GRACE_SECONDS
    task = asyncio.create_task(_poll_order(bot, order_id, deadline_ts))
    _poll_tasks[order_id] = task
    task.add_done_callback(lambda _t, oid=order_id: _poll_tasks.pop(oid, None))


async def _poll_order(bot, order_id: str, deadline_ts: float) -> None:
    try:
        while time.time() < deadline_ts:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            txn = await db.get_transaction(order_id)
            if not txn or txn.get("status") != "pending":
                return  # webhook (or something else) already handled it
            try:
                st = await get_order_status(order_id)
            except FamGatewayError as exc:
                logger.warning("Poll %s: %s", order_id, exc)
                continue
            if st["state"] == "success":
                logger.info("Order %s confirmed by gateway (utr=%s)", order_id, st.get("utr"))
                try:
                    result = await fulfill_order(order_id, bot, st.get("utr", ""), st.get("amount"))
                except Exception:
                    logger.exception("Fulfilment of %s failed — will retry", order_id)
                    continue
                logger.info("Order %s fulfilment result: %s", order_id, result)
                return
            if st["state"] == "expired":
                await _expire_order(bot, order_id)
                return
            if st["state"] == "error":
                logger.warning("Poll %s: gateway error: %s", order_id, st.get("message"))
        # Ran out of time without a definite answer.
        await _expire_order(bot, order_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Polling task for order %s crashed", order_id)


async def resume_pending(bot) -> None:
    """Call once at startup: re-attach pollers to orders that were still
    pending when the bot last stopped (so a redeploy mid-payment doesn't
    lose the payment)."""
    cutoff = time.time() - (ORDER_TTL_SECONDS + POLL_GRACE_SECONDS)
    count = 0
    async for doc in db.get_db().transactions.find(
        {"gateway": "famgateway", "status": "pending", "created_ts": {"$gt": cutoff}}
    ):
        start_polling(bot, doc["_id"], doc["created_ts"] + ORDER_TTL_SECONDS + POLL_GRACE_SECONDS)
        count += 1
    if count:
        logger.info("Resumed polling for %d pending FamGateway order(s)", count)


# --------------------------------------------------------------------------
# Checkout: create order -> send QR -> start polling
# --------------------------------------------------------------------------

async def start_checkout(bot, *, user, chat_id: int, kind: str, amount, txn_fields: dict,
                         caption_fn, extra_buttons: list | None = None,
                         redirect_url: str | None = None) -> dict:
    """Creates the order, sends the QR message and starts confirming it.

    kind         "premium" or "credits" (stored on the transaction)
    txn_fields   extra fields saved on the transaction, e.g.
                 {"plan_days": 30, "plan_label": "..."} or {"credits": 60}
    caption_fn   callable(order_dict) -> HTML caption for the QR photo
    extra_buttons  extra InlineKeyboard rows shown under the pay button
    Raises FamGatewayError if the gateway can't create the order."""
    order = await create_order(
        amount,
        customer_name=f"{user.first_name or 'User'} ({user.id})",
        redirect_url=redirect_url,
        webhook_url=f"{BASE_URL}/famgateway/webhook" if BASE_URL else None,
    )
    order_id = order["order_id"]
    payable = order.get("payable_amount") or order.get("amount") or amount

    await db.save_transaction(order_id, {
        "gateway": "famgateway",
        "user_id": user.id,
        "chat_id": chat_id,
        "kind": kind,
        "amount": str(amount),
        "payable_amount": str(payable),
        "status": "pending",
        "created_ts": time.time(),
        "checkout_url": order.get("checkout_url"),
        **txn_fields,
    })

    rows = [[InlineKeyboardButton(f"💳 Pay ₹{payable} in UPI app", url=order["checkout_url"])]]
    rows += extra_buttons or []
    markup = InlineKeyboardMarkup(rows)
    caption = caption_fn(order)

    try:
        msg = await bot.send_photo(
            chat_id=chat_id, photo=order["qr_url"], caption=caption,
            parse_mode=ParseMode.HTML, reply_markup=markup,
        )
    except Exception:
        # Telegram couldn't fetch the QR image — the checkout link still works.
        logger.warning("send_photo failed for order %s, falling back to text", order_id)
        msg = await bot.send_message(
            chat_id=chat_id, text=caption, parse_mode=ParseMode.HTML, reply_markup=markup,
        )

    await db.update_transaction(order_id, {"qr_message_id": msg.message_id})
    start_polling(bot, order_id)
    return order


# --------------------------------------------------------------------------
# Webhook + HTTP server
# --------------------------------------------------------------------------

async def handle_index(request: web.Request) -> web.Response:
    return web.Response(text="Bot is running.")


async def handle_webhook(request: web.Request) -> web.Response:
    raw = await request.read()
    if not verify_webhook_signature(raw, request.headers.get("X-FamGateway-Signature", "")):
        logger.warning("FamGateway webhook with bad/missing signature rejected")
        return web.Response(status=401, text="invalid signature")

    try:
        payload = json.loads(raw)
    except ValueError:
        return web.Response(status=400, text="bad json")

    if payload.get("status") != "success" and payload.get("event") != "payment.success":
        return web.json_response({"status": "ignored"})

    order_id = payload.get("order_id")
    if not order_id or not await db.get_transaction(order_id):
        return web.json_response({"status": "ignored"})  # 200 so it isn't retried

    bot = request.app["telegram_bot"]
    try:
        # Don't trust the payload alone — confirm with the gateway.
        st = await get_order_status(order_id)
        if st["state"] != "success":
            return web.json_response({"status": "ignored"})
        result = await fulfill_order(order_id, bot, st.get("utr", ""), st.get("amount"))
    except Exception:
        logger.exception("Webhook fulfilment failed for %s", order_id)
        return web.Response(status=500, text="retry")  # gateway retries on non-2xx
    return web.json_response({"status": "ok", "result": result})


def build_web_app(telegram_bot) -> web.Application:
    app = web.Application()
    app["telegram_bot"] = telegram_bot
    app.router.add_get("/", handle_index)
    app.router.add_post("/famgateway/webhook", handle_webhook)
    return app


async def shutdown() -> None:
    """Cancel pollers and close the HTTP session (call on bot shutdown)."""
    for task in list(_poll_tasks.values()):
        task.cancel()
    if _session is not None and not _session.closed:
        await _session.close()
