"""
PayU payment integration
-------------------------
Handles the 9-rupee premium plan: creating a PayU transaction, hosting the
auto-submit checkout page, and receiving PayU's webhook / redirect callbacks.

PayU's own hosted checkout page (the one the user is redirected to) already
shows every payment method on its own -- UPI intent (opens PhonePe / GPay /
Paytm etc. on the phone), UPI QR code, cards, netbanking and wallets -- as
long as you don't restrict it to a single mode. Nothing extra is needed on
our side to "enable" UPI or QR; that's PayU's default checkout behaviour.

Required environment variables:
  PAYU_KEY     - PayU merchant key (from your PayU dashboard)
  PAYU_SALT    - PayU salt (v1 salt, matching the key)
  PAYU_MODE    - "test" or "live" (default: "test")
  BASE_URL     - Public https URL of this deployed service,
                 e.g. https://your-app.onrender.com
                 (used to build the checkout page link and PayU's
                 success/failure/webhook callback URLs)

Optional:
  PLAN_AMOUNT  - default "9"
  PLAN_DAYS    - default "30"
  PLAN_LABEL   - default "Premium Plan"
"""

import os
import json
import html
import hashlib
import logging
import pathlib
import asyncio
from datetime import datetime, timedelta, timezone

from aiohttp import web

logger = logging.getLogger(__name__)

PAYU_KEY = os.environ.get("PAYU_KEY", "")
PAYU_SALT = os.environ.get("PAYU_SALT", "")
PAYU_MODE = os.environ.get("PAYU_MODE", "test").lower()
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

PLAN_AMOUNT = os.environ.get("PLAN_AMOUNT", "10")
PLAN_DAYS = int(os.environ.get("PLAN_DAYS", "30"))
PLAN_LABEL = os.environ.get("PLAN_LABEL", "Premium Plan")

PAYU_PAYMENT_URL = (
    "https://secure.payu.in/_payment"
    if PAYU_MODE == "live"
    else "https://test.payu.in/_payment"
)

DATA_FILE = pathlib.Path(__file__).parent / "data.json"
DATA_LOCK = asyncio.Lock()


# --------------------------------------------------------------------------
# Storage (simple JSON file: {"premium": {user_id: expiry_iso},
#                              "transactions": {txnid: {...}}})
# NOTE: on most free hosting (e.g. Render's free tier) local disk is
# ephemeral and can be wiped on redeploy/restart. For real production use,
# swap this out for a proper database (Postgres, Redis, etc.).
# --------------------------------------------------------------------------

def _load_data() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except Exception:
            logger.exception("Failed to read data.json, starting fresh")
    return {"premium": {}, "transactions": {}}


def _save_data(data: dict) -> None:
    DATA_FILE.write_text(json.dumps(data, indent=2))


async def save_transaction(txnid: str, txn: dict) -> None:
    async with DATA_LOCK:
        data = _load_data()
        data.setdefault("transactions", {})[txnid] = txn
        _save_data(data)


async def get_transaction(txnid: str) -> dict | None:
    async with DATA_LOCK:
        data = _load_data()
        return data.get("transactions", {}).get(txnid)


async def set_premium(user_id: int, expiry_iso: str) -> None:
    async with DATA_LOCK:
        data = _load_data()
        data.setdefault("premium", {})[str(user_id)] = expiry_iso
        _save_data(data)


async def get_premium_expiry(user_id: int) -> str | None:
    async with DATA_LOCK:
        data = _load_data()
        return data.get("premium", {}).get(str(user_id))


async def is_premium(user_id: int) -> bool:
    expiry = await get_premium_expiry(user_id)
    if not expiry:
        return False
    try:
        return datetime.fromisoformat(expiry) > datetime.now(timezone.utc)
    except Exception:
        return False


def _get_settings(data: dict) -> dict:
    settings = data.setdefault("settings", {})
    settings.setdefault("premium_enabled", True)
    pm = settings.setdefault("premium_message", {})
    pm.setdefault("text", None)
    pm.setdefault("photo_file_id", None)
    pm.setdefault("button_text", None)
    pm.setdefault("button_url", None)
    return settings


async def is_premium_enabled() -> bool:
    async with DATA_LOCK:
        data = _load_data()
        return _get_settings(data)["premium_enabled"]


async def set_premium_enabled(value: bool) -> None:
    async with DATA_LOCK:
        data = _load_data()
        _get_settings(data)["premium_enabled"] = value
        _save_data(data)


async def get_premium_message() -> dict:
    async with DATA_LOCK:
        data = _load_data()
        return dict(_get_settings(data)["premium_message"])


async def set_premium_message(**fields) -> None:
    """Update one or more fields of the custom premium-plan message
    (text, photo_file_id, button_text, button_url). Only the fields
    passed in are changed."""
    async with DATA_LOCK:
        data = _load_data()
        pm = _get_settings(data)["premium_message"]
        pm.update(fields)
        _save_data(data)


async def list_premium() -> dict:
    """Returns {user_id_str: expiry_iso} for every user ever granted
    premium (including expired ones)."""
    async with DATA_LOCK:
        data = _load_data()
        return dict(data.get("premium", {}))


async def remove_premium(user_id: int) -> bool:
    """Removes a user from the premium store. Returns True if they were
    present, False if they weren't premium to begin with."""
    async with DATA_LOCK:
        data = _load_data()
        key = str(user_id)
        if key in data.get("premium", {}):
            del data["premium"][key]
            _save_data(data)
            return True
        return False


# --------------------------------------------------------------------------
# PayU hashing
# --------------------------------------------------------------------------

def make_request_hash(txnid: str, amount: str, productinfo: str,
                       firstname: str, email: str) -> str:
    """Hash PayU requires when INITIATING a payment."""
    raw = (
        f"{PAYU_KEY}|{txnid}|{amount}|{productinfo}|{firstname}|{email}"
        f"|||||||||||{PAYU_SALT}"
    )
    return hashlib.sha512(raw.encode("utf-8")).hexdigest().lower()


def verify_response_hash(params: dict) -> bool:
    """Hash PayU sends back on the webhook / success / failure callback.
    Recomputed in reverse order and compared to what PayU sent us, so we
    know the callback genuinely came from PayU and wasn't tampered with."""
    status = params.get("status", "")
    firstname = params.get("firstname", "")
    productinfo = params.get("productinfo", "")
    amount = params.get("amount", "")
    txnid = params.get("txnid", "")
    email = params.get("email", "")
    received_hash = params.get("hash", "")

    raw = (
        f"{PAYU_SALT}|{status}|||||||||||{email}|{firstname}"
        f"|{productinfo}|{amount}|{txnid}|{PAYU_KEY}"
    )
    calculated = hashlib.sha512(raw.encode("utf-8")).hexdigest().lower()
    return calculated == received_hash.lower()


# --------------------------------------------------------------------------
# Core: process a PayU callback (used by both the server-to-server webhook
# and the browser success/failure redirects, since PayU sends the same
# fields to all three).
# --------------------------------------------------------------------------

async def process_payu_response(params: dict, bot) -> tuple[bool, str]:
    txnid = params.get("txnid")
    status = params.get("status")

    if not txnid:
        return False, "missing txnid"

    txn = await get_transaction(txnid)
    if not txn:
        return False, "unknown transaction"

    if not verify_response_hash(params):
        logger.warning("PayU hash mismatch for txn %s", txnid)
        return False, "hash mismatch"

    if txn.get("status") == "success":
        return True, "already processed"

    if status == "success":
        expiry = datetime.now(timezone.utc) + timedelta(days=txn["plan_days"])
        await set_premium(txn["user_id"], expiry.isoformat())
        txn["status"] = "success"
        await save_transaction(txnid, txn)
        try:
            await bot.send_message(
                chat_id=txn["chat_id"],
                text=(
                    "✅ Payment successful!\n\n"
                    f"Your {txn.get('plan_label', PLAN_LABEL)} is active "
                    f"until {expiry.strftime('%d %b %Y')}."
                ),
            )
        except Exception:
            logger.exception("Could not notify user %s", txn["user_id"])
        return True, "success"
    else:
        txn["status"] = "failed"
        await save_transaction(txnid, txn)
        try:
            await bot.send_message(
                chat_id=txn["chat_id"],
                text="❌ Payment failed or was cancelled. Try again with /buy.",
            )
        except Exception:
            logger.exception("Could not notify user %s", txn["user_id"])
        return True, "failed"


# --------------------------------------------------------------------------
# HTTP routes
# --------------------------------------------------------------------------

async def handle_index(request: web.Request) -> web.Response:
    return web.Response(text="Bot is running.")


async def handle_pay_page(request: web.Request) -> web.Response:
    txnid = request.match_info["txnid"]
    txn = await get_transaction(txnid)
    if not txn or txn.get("status") != "pending":
        return web.Response(text="Invalid or expired payment link.", status=404)

    if not (PAYU_KEY and PAYU_SALT and BASE_URL):
        return web.Response(
            text="Payment gateway is not configured yet (missing PAYU_KEY / "
                 "PAYU_SALT / BASE_URL).",
            status=500,
        )

    amount = f'{float(txn["amount"]):.2f}'
    productinfo = txn.get("plan_label", PLAN_LABEL)
    firstname = txn.get("firstname", "User")
    email = txn.get("email", f'user{txn["user_id"]}@telegram.local')
    phone = txn.get("phone", "9999999999")
    surl = f"{BASE_URL}/payu/success"
    furl = f"{BASE_URL}/payu/failure"

    hashh = make_request_hash(txnid, amount, productinfo, firstname, email)

    fields = {
        "key": PAYU_KEY,
        "txnid": txnid,
        "amount": amount,
        "productinfo": productinfo,
        "firstname": firstname,
        "email": email,
        "phone": phone,
        "surl": surl,
        "furl": furl,
        "hash": hashh,
        # Leave "pg" / bank codes unset so PayU shows every payment method:
        # UPI intent (PhonePe/GPay/Paytm app open), UPI QR, cards,
        # netbanking and wallets all appear on PayU's own checkout page.
    }
    inputs_html = "\n".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
        for k, v in fields.items()
    )

    page = f"""<!doctype html>
<html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Redirecting to payment...</title></head>
<body onload="document.forms[0].submit()">
<form method="post" action="{PAYU_PAYMENT_URL}">
{inputs_html}
</form>
<p>Redirecting you to the secure payment page...</p>
</body></html>"""
    return web.Response(text=page, content_type="text/html")


async def _params_from_request(request: web.Request) -> dict:
    if request.method == "POST":
        data = await request.post()
        return dict(data)
    return dict(request.query)


async def handle_webhook(request: web.Request) -> web.Response:
    params = await _params_from_request(request)
    bot = request.app["telegram_bot"]
    ok, msg = await process_payu_response(params, bot)
    return web.Response(text="OK" if ok else f"ERR: {msg}")


async def handle_success(request: web.Request) -> web.Response:
    params = await _params_from_request(request)
    bot = request.app["telegram_bot"]
    ok, msg = await process_payu_response(params, bot)
    username = request.app.get("bot_username", "")
    link = f"https://t.me/{username}" if username else "#"
    title = "Payment successful 🎉" if ok and msg in ("success", "already processed") else "Payment received"
    return web.Response(
        text=f"<html><body><h2>{title}</h2>"
             f"<p><a href='{link}'>Return to Telegram</a></p></body></html>",
        content_type="text/html",
    )


async def handle_failure(request: web.Request) -> web.Response:
    params = await _params_from_request(request)
    bot = request.app["telegram_bot"]
    await process_payu_response(params, bot)
    username = request.app.get("bot_username", "")
    link = f"https://t.me/{username}" if username else "#"
    return web.Response(
        text="<html><body><h2>Payment failed or cancelled</h2>"
             f"<p><a href='{link}'>Return to Telegram</a></p></body></html>",
        content_type="text/html",
    )


def build_web_app(telegram_bot) -> web.Application:
    app = web.Application()
    app["telegram_bot"] = telegram_bot
    app.router.add_get("/", handle_index)
    app.router.add_get("/payu/pay/{txnid}", handle_pay_page)
    app.router.add_route("*", "/payu/webhook", handle_webhook)
    app.router.add_route("*", "/payu/success", handle_success)
    app.router.add_route("*", "/payu/failure", handle_failure)
    return app
