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
import uuid
import random
import hashlib
import logging
import pathlib
import asyncio
from urllib.parse import quote
from datetime import datetime, timedelta, timezone

from aiohttp import web
import aiohttp

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


async def grant_premium_days(user_id: int, days: int) -> str:
    """Adds `days` on top of a user's current premium (extending an active
    plan, or starting fresh from now if they have none/expired). Returns
    the new expiry ISO string."""
    async with DATA_LOCK:
        data = _load_data()
        now = datetime.now(timezone.utc)
        base = now
        existing = data.get("premium", {}).get(str(user_id))
        if existing:
            try:
                exp_dt = datetime.fromisoformat(existing)
                if exp_dt > now:
                    base = exp_dt
            except Exception:
                pass
        new_expiry = base + timedelta(days=days)
        data.setdefault("premium", {})[str(user_id)] = new_expiry.isoformat()
        _save_data(data)
        return new_expiry.isoformat()


def _get_settings(data: dict) -> dict:
    settings = data.setdefault("settings", {})
    settings.setdefault("premium_enabled", True)
    pm = settings.setdefault("premium_message", {})
    pm.setdefault("text", None)
    pm.setdefault("photo_file_id", None)
    pm.setdefault("button_text", None)
    pm.setdefault("button_url", None)
    settings.setdefault("free_limit_enabled", False)
    settings.setdefault("free_limit_count", 5)

    # Link shorteners (admin-only). Multiple can be added; one is picked
    # at random for every verification link, so if one service is down
    # the others keep working.
    settings.setdefault("shorteners", [])

    # Token verification — required for free (non-premium) users before
    # they can open a file link. Disabled by default.
    tv = settings.setdefault("token_verification", {})
    tv.setdefault("enabled", False)
    tv.setdefault("validity_hours", 24)

    # Force subscribe — user must join every listed channel/group first.
    fs = settings.setdefault("force_subscribe", {})
    fs.setdefault("enabled", False)
    fs.setdefault("channels", [])

    # Custom caption applied to every file post.
    cap = settings.setdefault("caption", {})
    cap.setdefault("enabled", False)
    cap.setdefault("template", None)

    # Custom thumbnail applied to every file post.
    thumb = settings.setdefault("thumbnail", {})
    thumb.setdefault("enabled", False)
    thumb.setdefault("file_id", None)

    # Custom inline button attached under every file post.
    btn = settings.setdefault("button", {})
    btn.setdefault("enabled", False)
    btn.setdefault("label", None)
    btn.setdefault("url", None)

    # Auto delete: remove the sent file message after N seconds.
    ad = settings.setdefault("auto_delete", {})
    ad.setdefault("enabled", False)
    ad.setdefault("seconds", 600)

    # Protect content: stop users from forwarding/saving sent files.
    pc = settings.setdefault("protect_content", {})
    pc.setdefault("enabled", False)

    return settings


async def is_free_limit_enabled() -> bool:
    async with DATA_LOCK:
        data = _load_data()
        return _get_settings(data)["free_limit_enabled"]


async def set_free_limit_enabled(value: bool) -> None:
    async with DATA_LOCK:
        data = _load_data()
        _get_settings(data)["free_limit_enabled"] = value
        _save_data(data)


async def get_free_limit_count() -> int:
    async with DATA_LOCK:
        data = _load_data()
        return _get_settings(data)["free_limit_count"]


async def set_free_limit_count(count: int) -> None:
    async with DATA_LOCK:
        data = _load_data()
        _get_settings(data)["free_limit_count"] = count
        _save_data(data)


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
    """Updates one or more fields of the custom premium-plan message
    (text, photo_file_id, button_text, button_url)."""
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
# Refer & Earn
# data["referrals"] = {referred_user_id_str: {"referrer_id": int, "rewarded": bool}}
# --------------------------------------------------------------------------

async def record_referral(referred_id: int, referrer_id: int) -> bool:
    """Called the first time a referred user starts the bot. Returns True
    if this referral was newly recorded, False if that user already has a
    referrer on file (or referred == referrer)."""
    if referred_id == referrer_id:
        return False
    async with DATA_LOCK:
        data = _load_data()
        referrals = data.setdefault("referrals", {})
        key = str(referred_id)
        if key in referrals:
            return False
        referrals[key] = {"referrer_id": referrer_id, "rewarded": False}
        _save_data(data)
        return True


async def get_referral(referred_id: int) -> dict | None:
    async with DATA_LOCK:
        data = _load_data()
        return data.get("referrals", {}).get(str(referred_id))


async def mark_referral_rewarded(referred_id: int) -> None:
    async with DATA_LOCK:
        data = _load_data()
        rec = data.get("referrals", {}).get(str(referred_id))
        if rec:
            rec["rewarded"] = True
            _save_data(data)


async def get_referral_stats(referrer_id: int) -> dict:
    """Returns {"total": n, "rewarded": n} for everyone a user has
    referred so far."""
    async with DATA_LOCK:
        data = _load_data()
        referrals = data.get("referrals", {})
        total = 0
        rewarded = 0
        for rec in referrals.values():
            if rec.get("referrer_id") == referrer_id:
                total += 1
                if rec.get("rewarded"):
                    rewarded += 1
        return {"total": total, "rewarded": rewarded}


# --------------------------------------------------------------------------
# Link shorteners (admin-only, multiple)
# data["settings"]["shorteners"] = [
#     {"id": "a1b2c3d4", "name": "GPLinks", "api_domain": "api.gplinks.com",
#      "api_key": "...", "enabled": True}, ...
# ]
# --------------------------------------------------------------------------

async def list_shorteners() -> list[dict]:
    async with DATA_LOCK:
        data = _load_data()
        return list(_get_settings(data)["shorteners"])


async def add_shortener(name: str, api_domain: str, api_key: str) -> dict:
    entry = {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "api_domain": api_domain.strip().removeprefix("https://").removeprefix("http://").rstrip("/"),
        "api_key": api_key.strip(),
        "enabled": True,
    }
    async with DATA_LOCK:
        data = _load_data()
        _get_settings(data)["shorteners"].append(entry)
        _save_data(data)
    return entry


async def remove_shortener(shortener_id: str) -> bool:
    async with DATA_LOCK:
        data = _load_data()
        shorteners = _get_settings(data)["shorteners"]
        new_list = [s for s in shorteners if s["id"] != shortener_id]
        if len(new_list) == len(shorteners):
            return False
        _get_settings(data)["shorteners"] = new_list
        _save_data(data)
        return True


async def toggle_shortener(shortener_id: str) -> bool | None:
    """Flips a shortener's enabled flag. Returns the new state, or None
    if no shortener with that id exists."""
    async with DATA_LOCK:
        data = _load_data()
        for s in _get_settings(data)["shorteners"]:
            if s["id"] == shortener_id:
                s["enabled"] = not s["enabled"]
                _save_data(data)
                return s["enabled"]
        return None


async def shorten_url(long_url: str) -> str:
    """Shortens `long_url` with a randomly-picked enabled shortener
    (GPLinks-style API: GET https://<domain>/api?api=<key>&url=<url>
    -> {"status": "success", "shortenedUrl": "..."}). Falls back to the
    original long_url if none are configured or the request fails, so
    the bot degrades gracefully instead of blocking users."""
    shorteners = [s for s in await list_shorteners() if s.get("enabled")]
    if not shorteners:
        return long_url
    chosen = random.choice(shorteners)
    try:
        api_url = (
            f"https://{chosen['api_domain']}/api"
            f"?api={chosen['api_key']}&url={quote(long_url, safe='')}"
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                result = await resp.json(content_type=None)
                short = result.get("shortenedUrl") or result.get("shortened_url")
                if result.get("status") == "success" and short:
                    return short
                logger.warning("Shortener %s returned no link: %s", chosen.get("name"), result)
    except Exception:
        logger.exception("Shortener %s failed", chosen.get("name"))
    return long_url


# --------------------------------------------------------------------------
# Token verification (free users only)
# data["verified"] = {user_id_str: expiry_iso}
# --------------------------------------------------------------------------

async def is_token_verification_enabled() -> bool:
    async with DATA_LOCK:
        data = _load_data()
        return _get_settings(data)["token_verification"]["enabled"]


async def set_token_verification_enabled(value: bool) -> None:
    async with DATA_LOCK:
        data = _load_data()
        _get_settings(data)["token_verification"]["enabled"] = value
        _save_data(data)


async def get_verification_validity_hours() -> int:
    async with DATA_LOCK:
        data = _load_data()
        return _get_settings(data)["token_verification"]["validity_hours"]


async def set_verification_validity_hours(hours: int) -> None:
    async with DATA_LOCK:
        data = _load_data()
        _get_settings(data)["token_verification"]["validity_hours"] = hours
        _save_data(data)


async def is_verified(user_id: int) -> bool:
    async with DATA_LOCK:
        data = _load_data()
        expiry = data.get("verified", {}).get(str(user_id))
    if not expiry:
        return False
    try:
        return datetime.fromisoformat(expiry) > datetime.now(timezone.utc)
    except Exception:
        return False


async def set_verified(user_id: int, hours: int) -> str:
    expiry = datetime.now(timezone.utc) + timedelta(hours=hours)
    async with DATA_LOCK:
        data = _load_data()
        data.setdefault("verified", {})[str(user_id)] = expiry.isoformat()
        _save_data(data)
    return expiry.isoformat()


# --------------------------------------------------------------------------
# Force subscribe
# --------------------------------------------------------------------------

async def is_force_sub_enabled() -> bool:
    async with DATA_LOCK:
        data = _load_data()
        return _get_settings(data)["force_subscribe"]["enabled"]


async def set_force_sub_enabled(value: bool) -> None:
    async with DATA_LOCK:
        data = _load_data()
        _get_settings(data)["force_subscribe"]["enabled"] = value
        _save_data(data)


async def list_force_sub_channels() -> list[dict]:
    async with DATA_LOCK:
        data = _load_data()
        return list(_get_settings(data)["force_subscribe"]["channels"])


async def add_force_sub_channel(chat_id: int, title: str, invite_link: str,
                                 username: str | None = None) -> dict:
    entry = {
        "entry_id": uuid.uuid4().hex[:8],
        "id": chat_id,
        "title": title,
        "username": username,
        "invite_link": invite_link,
    }
    async with DATA_LOCK:
        data = _load_data()
        _get_settings(data)["force_subscribe"]["channels"].append(entry)
        _save_data(data)
    return entry


async def remove_force_sub_channel(entry_id: str) -> bool:
    async with DATA_LOCK:
        data = _load_data()
        channels = _get_settings(data)["force_subscribe"]["channels"]
        new_list = [c for c in channels if c["entry_id"] != entry_id]
        if len(new_list) == len(channels):
            return False
        _get_settings(data)["force_subscribe"]["channels"] = new_list
        _save_data(data)
        return True


async def get_unjoined_channels(bot, user_id: int) -> list[dict]:
    """Returns the force-subscribe channels `user_id` has NOT joined.
    If membership can't be checked (bot isn't admin there, etc.) that
    channel is skipped rather than blocking the user, so a misconfigured
    channel doesn't lock everyone out."""
    if not await is_force_sub_enabled():
        return []
    channels = await list_force_sub_channels()
    unjoined = []
    for ch in channels:
        try:
            member = await bot.get_chat_member(chat_id=ch["id"], user_id=user_id)
            if member.status in ("left", "kicked"):
                unjoined.append(ch)
        except Exception:
            logger.warning("Could not check force-sub membership for channel %s", ch.get("id"))
    return unjoined


# --------------------------------------------------------------------------
# Custom caption / thumbnail / button / auto-delete / protect content
# --------------------------------------------------------------------------

async def is_caption_enabled() -> bool:
    async with DATA_LOCK:
        data = _load_data()
        return _get_settings(data)["caption"]["enabled"]


async def set_caption_enabled(value: bool) -> None:
    async with DATA_LOCK:
        data = _load_data()
        _get_settings(data)["caption"]["enabled"] = value
        _save_data(data)


async def get_caption_template() -> str | None:
    async with DATA_LOCK:
        data = _load_data()
        return _get_settings(data)["caption"]["template"]


async def set_caption_template(template: str) -> None:
    async with DATA_LOCK:
        data = _load_data()
        _get_settings(data)["caption"]["template"] = template
        _save_data(data)


async def is_thumbnail_enabled() -> bool:
    async with DATA_LOCK:
        data = _load_data()
        return _get_settings(data)["thumbnail"]["enabled"]


async def set_thumbnail_enabled(value: bool) -> None:
    async with DATA_LOCK:
        data = _load_data()
        _get_settings(data)["thumbnail"]["enabled"] = value
        _save_data(data)


async def get_thumbnail_file_id() -> str | None:
    async with DATA_LOCK:
        data = _load_data()
        return _get_settings(data)["thumbnail"]["file_id"]


async def set_thumbnail_file_id(file_id: str | None) -> None:
    async with DATA_LOCK:
        data = _load_data()
        _get_settings(data)["thumbnail"]["file_id"] = file_id
        _save_data(data)


async def is_custom_button_enabled() -> bool:
    async with DATA_LOCK:
        data = _load_data()
        return _get_settings(data)["button"]["enabled"]


async def set_custom_button_enabled(value: bool) -> None:
    async with DATA_LOCK:
        data = _load_data()
        _get_settings(data)["button"]["enabled"] = value
        _save_data(data)


async def get_custom_button() -> dict:
    async with DATA_LOCK:
        data = _load_data()
        return dict(_get_settings(data)["button"])


async def set_custom_button(label: str, url: str) -> None:
    async with DATA_LOCK:
        data = _load_data()
        btn = _get_settings(data)["button"]
        btn["label"] = label
        btn["url"] = url
        _save_data(data)


async def is_auto_delete_enabled() -> bool:
    async with DATA_LOCK:
        data = _load_data()
        return _get_settings(data)["auto_delete"]["enabled"]


async def set_auto_delete_enabled(value: bool) -> None:
    async with DATA_LOCK:
        data = _load_data()
        _get_settings(data)["auto_delete"]["enabled"] = value
        _save_data(data)


async def get_auto_delete_seconds() -> int:
    async with DATA_LOCK:
        data = _load_data()
        return _get_settings(data)["auto_delete"]["seconds"]


async def set_auto_delete_seconds(seconds: int) -> None:
    async with DATA_LOCK:
        data = _load_data()
        _get_settings(data)["auto_delete"]["seconds"] = seconds
        _save_data(data)


async def is_protect_content_enabled() -> bool:
    async with DATA_LOCK:
        data = _load_data()
        return _get_settings(data)["protect_content"]["enabled"]


async def set_protect_content_enabled(value: bool) -> None:
    async with DATA_LOCK:
        data = _load_data()
        _get_settings(data)["protect_content"]["enabled"] = value
        _save_data(data)


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

        # Refer & Earn: if this buyer was referred and hasn't triggered a
        # reward yet, give the referrer 1 free day of premium.
        try:
            referral = await get_referral(txn["user_id"])
            if referral and not referral.get("rewarded"):
                referrer_id = referral["referrer_id"]
                new_expiry = await grant_premium_days(referrer_id, 1)
                await mark_referral_rewarded(txn["user_id"])
                try:
                    await bot.send_message(
                        chat_id=referrer_id,
                        text=(
                            "🎁 Your referral just purchased Premium! You've "
                            "earned +1 day of Premium.\n"
                            f"Your premium is now valid until "
                            f"{datetime.fromisoformat(new_expiry).strftime('%d %b %Y')}."
                        ),
                    )
                except Exception:
                    logger.exception("Could not notify referrer %s", referrer_id)
        except Exception:
            logger.exception("Referral reward check failed for user %s", txn["user_id"])

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
