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
  MONGO_URI    - MongoDB connection string, e.g.
                 mongodb+srv://user:pass@cluster0.xxxx.mongodb.net/

Optional:
  MONGO_DB_NAME - default "filestore_bot"
  PLAN_LABEL    - default "Premium Plan" (brand name shown on the buy card)
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
from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

logger = logging.getLogger(__name__)

PAYU_KEY = os.environ.get("PAYU_KEY", "")
PAYU_SALT = os.environ.get("PAYU_SALT", "")
PAYU_MODE = os.environ.get("PAYU_MODE", "test").lower()
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

PLAN_LABEL = os.environ.get("PLAN_LABEL", "Premium Plan")

# Multiple premium plan tiers. `id` is used in callback_data, so keep it
# short and stable — changing an existing id will orphan any pending
# transactions using the old one (harmless, they just expire unused).
PLANS = [
    {"id": "1day", "label": "1 Day", "days": 1, "amount": "5"},
    {"id": "1week", "label": "1 Week", "days": 7, "amount": "19"},
    {"id": "1month", "label": "1 Month", "days": 30, "amount": "49"},
    {"id": "3months", "label": "3 Months", "days": 90, "amount": "99"},
]


def get_plan(plan_id: str) -> dict | None:
    for p in PLANS:
        if p["id"] == plan_id:
            return p
    return None


PAYU_PAYMENT_URL = (
    "https://secure.payu.in/_payment"
    if PAYU_MODE == "live"
    else "https://test.payu.in/_payment"
)


# --------------------------------------------------------------------------
# Storage — MongoDB (PyMongo's native async API)
#
# Collections (database name comes from MONGO_DB_NAME):
#   users         {_id: user_id, first_name, username, joined_at, last_seen}
#   premium       {_id: user_id, expiry: iso}
#   verified      {_id: user_id, expiry: iso}      (token verification)
#   referrals     {_id: referred_user_id, referrer_id, rewarded}
#   transactions  {_id: txnid, user_id, chat_id, amount, plan_days, ...}
#   settings      {_id: "main", ...all bot-wide settings...}
#   meta          {_id: "...", ...internal markers, e.g. legacy import}
# --------------------------------------------------------------------------

MONGO_URI = os.environ.get("MONGO_URI", "")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "filestore_bot")

# Old JSON file — only read once, to import existing data into MongoDB.
DATA_FILE = pathlib.Path(__file__).parent / "data.json"

# Serialises the few read-modify-write operations (extending premium,
# toggling a shortener). Everything else is a single atomic Mongo op.
DATA_LOCK = asyncio.Lock()

_SETTINGS_ID = "main"
_client: AsyncMongoClient | None = None


def _db():
    global _client
    if _client is None:
        if not MONGO_URI:
            raise RuntimeError(
                "MONGO_URI environment variable is not set. Add your MongoDB "
                "connection string (e.g. mongodb+srv://user:pass@cluster/...)."
            )
        _client = AsyncMongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    return _client[MONGO_DB_NAME]


async def init_db() -> None:
    """Call once at startup: checks the connection, creates indexes and
    imports the legacy data.json (if one exists) into MongoDB."""
    db = _db()
    await db.command("ping")
    await db.referrals.create_index("referrer_id")
    await db.transactions.create_index("user_id")
    await _import_legacy_json(db)
    logger.info("MongoDB connected (db=%s)", MONGO_DB_NAME)


async def close_db() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


async def _import_legacy_json(db) -> None:
    """One-time import of the old data.json so existing premium users,
    referrals, transactions and settings aren't lost when switching to
    MongoDB. Runs at most once (tracked by a marker document)."""
    marker_id = "legacy_json_import"
    if await db.meta.find_one({"_id": marker_id}):
        return
    if not DATA_FILE.exists():
        return
    try:
        legacy = json.loads(DATA_FILE.read_text())
    except Exception:
        logger.exception("Could not read legacy data.json — skipping import")
        return

    for uid, expiry in legacy.get("premium", {}).items():
        await db.premium.replace_one({"_id": int(uid)}, {"expiry": expiry}, upsert=True)
    for uid, expiry in legacy.get("verified", {}).items():
        await db.verified.replace_one({"_id": int(uid)}, {"expiry": expiry}, upsert=True)
    for uid, rec in legacy.get("referrals", {}).items():
        await db.referrals.replace_one(
            {"_id": int(uid)},
            {"referrer_id": rec.get("referrer_id"), "rewarded": bool(rec.get("rewarded"))},
            upsert=True,
        )
    for txnid, txn in legacy.get("transactions", {}).items():
        await db.transactions.replace_one({"_id": txnid}, dict(txn), upsert=True)
    if legacy.get("settings"):
        await db.settings.replace_one({"_id": _SETTINGS_ID}, dict(legacy["settings"]), upsert=True)

    await db.meta.insert_one({"_id": marker_id, "imported_at": _now_iso()})
    logger.info("Imported legacy data.json into MongoDB")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------

async def register_user(user) -> bool:
    """Upserts a Telegram user (call on /start). Returns True if this is
    the first time we've seen them."""
    now = _now_iso()
    res = await _db().users.update_one(
        {"_id": user.id},
        {
            "$set": {
                "first_name": user.first_name,
                "username": user.username,
                "last_seen": now,
            },
            "$setOnInsert": {"joined_at": now},
        },
        upsert=True,
    )
    return res.upserted_id is not None


async def count_users() -> int:
    return await _db().users.count_documents({})


async def get_all_user_ids() -> list[int]:
    ids = []
    async for doc in _db().users.find({}, {"_id": 1}):
        ids.append(doc["_id"])
    return ids


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------

async def save_transaction(txnid: str, txn: dict) -> None:
    await _db().transactions.replace_one({"_id": txnid}, dict(txn), upsert=True)


async def get_transaction(txnid: str) -> dict | None:
    doc = await _db().transactions.find_one({"_id": txnid})
    if doc:
        doc.pop("_id", None)
    return doc


async def claim_transaction_success(txnid: str) -> bool:
    """Atomically flips a transaction to "success". Returns True only for
    the single caller that made the flip — PayU sends both a webhook and
    a browser redirect, so this stops premium being granted twice."""
    res = await _db().transactions.update_one(
        {"_id": txnid, "status": {"$ne": "success"}},
        {"$set": {"status": "success"}},
    )
    return res.modified_count == 1


async def unclaim_transaction(txnid: str) -> None:
    """Rolls a claimed transaction back to "pending" (used if granting
    premium fails right after claiming, so a retry can succeed)."""
    await _db().transactions.update_one({"_id": txnid}, {"$set": {"status": "pending"}})


# --------------------------------------------------------------------------
# Premium
# --------------------------------------------------------------------------

async def set_premium(user_id: int, expiry_iso: str) -> None:
    await _db().premium.update_one(
        {"_id": user_id}, {"$set": {"expiry": expiry_iso}}, upsert=True
    )


async def get_premium_expiry(user_id: int) -> str | None:
    doc = await _db().premium.find_one({"_id": user_id})
    return doc["expiry"] if doc else None


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
        now = datetime.now(timezone.utc)
        base = now
        existing = await get_premium_expiry(user_id)
        if existing:
            try:
                exp_dt = datetime.fromisoformat(existing)
                if exp_dt > now:
                    base = exp_dt
            except Exception:
                pass
        new_expiry = (base + timedelta(days=days)).isoformat()
        await set_premium(user_id, new_expiry)
        return new_expiry


async def list_premium() -> dict:
    """Returns {user_id_str: expiry_iso} for every user ever granted
    premium (including expired ones)."""
    out = {}
    async for doc in _db().premium.find():
        out[str(doc["_id"])] = doc["expiry"]
    return out


async def remove_premium(user_id: int) -> bool:
    """Removes a user from the premium store. Returns True if they were
    present, False if they weren't premium to begin with."""
    res = await _db().premium.delete_one({"_id": user_id})
    return res.deleted_count > 0


# --------------------------------------------------------------------------
# Settings (one document, _id "main")
# --------------------------------------------------------------------------

def _apply_defaults(settings: dict) -> dict:
    settings.setdefault("premium_enabled", True)
    pm = settings.setdefault("premium_message", {})
    pm.setdefault("text", None)
    pm.setdefault("photo_file_id", None)
    pm.setdefault("button_text", None)
    pm.setdefault("button_url", None)
    settings.setdefault("free_limit_enabled", False)
    settings.setdefault("free_limit_count", 5)

    # Link shorteners (admin-only). Multiple can be added; one is picked
    # at random for every verification link.
    settings.setdefault("shorteners", [])

    # Token verification — required for free (non-premium) users.
    tv = settings.setdefault("token_verification", {})
    tv.setdefault("enabled", False)
    tv.setdefault("validity_hours", 24)

    # Force subscribe — user must join every listed channel/group first.
    fs = settings.setdefault("force_subscribe", {})
    fs.setdefault("enabled", False)
    fs.setdefault("channels", [])

    cap = settings.setdefault("caption", {})
    cap.setdefault("enabled", False)
    cap.setdefault("template", None)

    thumb = settings.setdefault("thumbnail", {})
    thumb.setdefault("enabled", False)
    thumb.setdefault("file_id", None)

    btn = settings.setdefault("button", {})
    btn.setdefault("enabled", False)
    btn.setdefault("label", None)
    btn.setdefault("url", None)

    ad = settings.setdefault("auto_delete", {})
    ad.setdefault("enabled", False)
    ad.setdefault("seconds", 600)

    pc = settings.setdefault("protect_content", {})
    pc.setdefault("enabled", False)

    return settings


async def _settings() -> dict:
    doc = await _db().settings.find_one({"_id": _SETTINGS_ID}) or {}
    doc.pop("_id", None)
    return _apply_defaults(doc)


async def _set(**fields) -> None:
    """$set one or more settings fields. Use dotted paths via the dict
    form: _set(**{"caption.enabled": True})."""
    if not fields:
        return
    await _db().settings.update_one(
        {"_id": _SETTINGS_ID}, {"$set": fields}, upsert=True
    )


async def is_free_limit_enabled() -> bool:
    return (await _settings())["free_limit_enabled"]


async def set_free_limit_enabled(value: bool) -> None:
    await _set(free_limit_enabled=value)


async def get_free_limit_count() -> int:
    return (await _settings())["free_limit_count"]


async def set_free_limit_count(count: int) -> None:
    await _set(free_limit_count=count)


async def is_premium_enabled() -> bool:
    return (await _settings())["premium_enabled"]


async def set_premium_enabled(value: bool) -> None:
    await _set(premium_enabled=value)


async def get_premium_message() -> dict:
    return dict((await _settings())["premium_message"])


async def set_premium_message(**fields) -> None:
    """Updates one or more fields of the custom premium-plan message
    (text, photo_file_id, button_text, button_url)."""
    await _set(**{f"premium_message.{k}": v for k, v in fields.items()})


# --------------------------------------------------------------------------
# Refer & Earn
# --------------------------------------------------------------------------

async def record_referral(referred_id: int, referrer_id: int) -> bool:
    """Called the first time a referred user starts the bot. Returns True
    if this referral was newly recorded, False if that user already has a
    referrer on file (or referred == referrer)."""
    if referred_id == referrer_id:
        return False
    try:
        await _db().referrals.insert_one(
            {"_id": referred_id, "referrer_id": referrer_id, "rewarded": False}
        )
        return True
    except DuplicateKeyError:
        return False


async def get_referral(referred_id: int) -> dict | None:
    doc = await _db().referrals.find_one({"_id": referred_id})
    if not doc:
        return None
    return {"referrer_id": doc.get("referrer_id"), "rewarded": bool(doc.get("rewarded"))}


async def mark_referral_rewarded(referred_id: int) -> None:
    await _db().referrals.update_one({"_id": referred_id}, {"$set": {"rewarded": True}})


async def get_referral_stats(referrer_id: int) -> dict:
    """Returns {"total": n, "rewarded": n} for everyone a user has
    referred so far."""
    referrals = _db().referrals
    total = await referrals.count_documents({"referrer_id": referrer_id})
    rewarded = await referrals.count_documents({"referrer_id": referrer_id, "rewarded": True})
    return {"total": total, "rewarded": rewarded}


# --------------------------------------------------------------------------
# Link shorteners (admin-only, multiple)
# settings["shorteners"] = [
#     {"id": "a1b2c3d4", "name": "GPLinks", "api_domain": "api.gplinks.com",
#      "api_key": "...", "enabled": True}, ...
# ]
# --------------------------------------------------------------------------

async def list_shorteners() -> list[dict]:
    return list((await _settings())["shorteners"])


async def add_shortener(name: str, api_domain: str, api_key: str) -> dict:
    entry = {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "api_domain": api_domain.strip().removeprefix("https://").removeprefix("http://").rstrip("/"),
        "api_key": api_key.strip(),
        "enabled": True,
    }
    await _db().settings.update_one(
        {"_id": _SETTINGS_ID}, {"$push": {"shorteners": entry}}, upsert=True
    )
    return entry


async def remove_shortener(shortener_id: str) -> bool:
    res = await _db().settings.update_one(
        {"_id": _SETTINGS_ID}, {"$pull": {"shorteners": {"id": shortener_id}}}
    )
    return res.modified_count == 1


async def toggle_shortener(shortener_id: str) -> bool | None:
    """Flips a shortener's enabled flag. Returns the new state, or None
    if no shortener with that id exists."""
    async with DATA_LOCK:
        for s in await list_shorteners():
            if s["id"] == shortener_id:
                new_state = not s["enabled"]
                await _db().settings.update_one(
                    {"_id": _SETTINGS_ID, "shorteners.id": shortener_id},
                    {"$set": {"shorteners.$.enabled": new_state}},
                )
                return new_state
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
# --------------------------------------------------------------------------

async def is_token_verification_enabled() -> bool:
    return (await _settings())["token_verification"]["enabled"]


async def set_token_verification_enabled(value: bool) -> None:
    await _set(**{"token_verification.enabled": value})


async def get_verification_validity_hours() -> int:
    return (await _settings())["token_verification"]["validity_hours"]


async def set_verification_validity_hours(hours: int) -> None:
    await _set(**{"token_verification.validity_hours": hours})


async def is_verified(user_id: int) -> bool:
    doc = await _db().verified.find_one({"_id": user_id})
    if not doc:
        return False
    try:
        return datetime.fromisoformat(doc["expiry"]) > datetime.now(timezone.utc)
    except Exception:
        return False


async def set_verified(user_id: int, hours: int) -> str:
    expiry = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    await _db().verified.update_one(
        {"_id": user_id}, {"$set": {"expiry": expiry}}, upsert=True
    )
    return expiry


# --------------------------------------------------------------------------
# Force subscribe
# --------------------------------------------------------------------------

async def is_force_sub_enabled() -> bool:
    return (await _settings())["force_subscribe"]["enabled"]


async def set_force_sub_enabled(value: bool) -> None:
    await _set(**{"force_subscribe.enabled": value})


async def list_force_sub_channels() -> list[dict]:
    return list((await _settings())["force_subscribe"]["channels"])


async def add_force_sub_channel(chat_id: int, title: str, invite_link: str,
                                 username: str | None = None) -> dict:
    entry = {
        "entry_id": uuid.uuid4().hex[:8],
        "id": chat_id,
        "title": title,
        "username": username,
        "invite_link": invite_link,
    }
    await _db().settings.update_one(
        {"_id": _SETTINGS_ID}, {"$push": {"force_subscribe.channels": entry}}, upsert=True
    )
    return entry


async def remove_force_sub_channel(entry_id: str) -> bool:
    res = await _db().settings.update_one(
        {"_id": _SETTINGS_ID},
        {"$pull": {"force_subscribe.channels": {"entry_id": entry_id}}},
    )
    return res.modified_count == 1


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
    return (await _settings())["caption"]["enabled"]


async def set_caption_enabled(value: bool) -> None:
    await _set(**{"caption.enabled": value})


async def get_caption_template() -> str | None:
    return (await _settings())["caption"]["template"]


async def set_caption_template(template: str) -> None:
    await _set(**{"caption.template": template})


async def is_thumbnail_enabled() -> bool:
    return (await _settings())["thumbnail"]["enabled"]


async def set_thumbnail_enabled(value: bool) -> None:
    await _set(**{"thumbnail.enabled": value})


async def get_thumbnail_file_id() -> str | None:
    return (await _settings())["thumbnail"]["file_id"]


async def set_thumbnail_file_id(file_id: str | None) -> None:
    await _set(**{"thumbnail.file_id": file_id})


async def is_custom_button_enabled() -> bool:
    return (await _settings())["button"]["enabled"]


async def set_custom_button_enabled(value: bool) -> None:
    await _set(**{"button.enabled": value})


async def get_custom_button() -> dict:
    return dict((await _settings())["button"])


async def set_custom_button(label: str, url: str) -> None:
    await _set(**{"button.label": label, "button.url": url})


async def is_auto_delete_enabled() -> bool:
    return (await _settings())["auto_delete"]["enabled"]


async def set_auto_delete_enabled(value: bool) -> None:
    await _set(**{"auto_delete.enabled": value})


async def get_auto_delete_seconds() -> int:
    return (await _settings())["auto_delete"]["seconds"]


async def set_auto_delete_seconds(seconds: int) -> None:
    await _set(**{"auto_delete.seconds": seconds})


async def is_protect_content_enabled() -> bool:
    return (await _settings())["protect_content"]["enabled"]


async def set_protect_content_enabled(value: bool) -> None:
    await _set(**{"protect_content.enabled": value})


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
        # Extend on top of any remaining active premium instead of
        # overwriting it, so buying/extending never shortens a plan.
        # PayU fires both a webhook and a browser redirect; only the caller
        # that atomically claims the transaction may grant premium.
        if not await claim_transaction_success(txnid):
            return True, "already processed"
        try:
            new_expiry_iso = await grant_premium_days(txn["user_id"], txn["plan_days"])
        except Exception:
            await unclaim_transaction(txnid)
            raise
        expiry = datetime.fromisoformat(new_expiry_iso)
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


def _status_page(success: bool, heading: str, subtitle: str, link: str, button_label: str) -> str:
    """Renders a self-contained, animated payment-result page (green
    check-in-circle for success, red cross for failure) with a button
    back to the bot."""
    accent = "#22c55e" if success else "#ef4444"
    accent_dark = "#16a34a" if success else "#dc2626"
    glow = "rgba(34,197,94,0.35)" if success else "rgba(239,68,68,0.35)"
    icon_svg = (
        # Checkmark
        '<path class="icon-path" d="M28 52 L44 68 L76 32" fill="none" '
        'stroke="#ffffff" stroke-width="8" stroke-linecap="round" '
        'stroke-linejoin="round"/>'
        if success else
        # Cross
        '<path class="icon-path" d="M34 34 L66 66 M66 34 L34 66" fill="none" '
        'stroke="#ffffff" stroke-width="8" stroke-linecap="round"/>'
    )
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{html.escape(heading)}</title>
<style>
  :root {{
    --accent: {accent};
    --accent-dark: {accent_dark};
    --glow: {glow};
  }}
  * {{ box-sizing: border-box; }}
  html, body {{
    height: 100%;
    margin: 0;
  }}
  body {{
    min-height: 100dvh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
    padding-top: calc(24px + env(safe-area-inset-top, 0px));
    padding-bottom: calc(24px + env(safe-area-inset-bottom, 0px));
    background: radial-gradient(circle at 50% 20%, #1b2440 0%, #0b0f1f 55%, #05060c 100%);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: #f4f6fb;
  }}
  .card {{
    text-align: center;
    max-width: 420px;
    width: 100%;
    animation: fade-up 0.6s ease-out both;
  }}
  .icon-wrap {{
    position: relative;
    width: 140px;
    height: 140px;
    margin: 0 auto 28px;
  }}
  .icon-glow {{
    position: absolute;
    inset: -20px;
    border-radius: 50%;
    background: radial-gradient(circle, var(--glow) 0%, transparent 70%);
    filter: blur(6px);
    animation: pulse 2.2s ease-in-out infinite;
  }}
  .icon-circle {{
    position: relative;
    width: 140px;
    height: 140px;
    border-radius: 50%;
    background: linear-gradient(145deg, var(--accent) 0%, var(--accent-dark) 100%);
    box-shadow:
      0 10px 30px var(--glow),
      inset 0 -6px 14px rgba(0,0,0,0.25),
      inset 0 6px 10px rgba(255,255,255,0.25);
    display: flex;
    align-items: center;
    justify-content: center;
    animation: pop-in 0.55s cubic-bezier(.34,1.56,.64,1) both;
  }}
  .icon-circle svg {{ width: 78px; height: 78px; }}
  .icon-path {{
    stroke-dasharray: 100;
    stroke-dashoffset: 100;
    animation: draw 0.5s 0.35s ease-out forwards;
  }}
  h1 {{
    font-size: 26px;
    margin: 0 0 10px;
    letter-spacing: -0.02em;
  }}
  p.subtitle {{
    font-size: 15px;
    line-height: 1.5;
    color: #a9b0c6;
    margin: 0 0 32px;
  }}
  .btn {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    text-decoration: none;
    color: #ffffff;
    font-weight: 600;
    font-size: 16px;
    padding: 14px 30px;
    border-radius: 999px;
    background: linear-gradient(135deg, #7c6bff 0%, #5b8dff 100%);
    box-shadow: 0 8px 24px rgba(91,141,255,0.35);
    transition: transform 0.15s ease, box-shadow 0.15s ease;
  }}
  .btn:active {{
    transform: scale(0.96);
    box-shadow: 0 4px 14px rgba(91,141,255,0.35);
  }}
  @keyframes pop-in {{
    0% {{ transform: scale(0.4); opacity: 0; }}
    100% {{ transform: scale(1); opacity: 1; }}
  }}
  @keyframes draw {{
    to {{ stroke-dashoffset: 0; }}
  }}
  @keyframes pulse {{
    0%, 100% {{ opacity: 0.55; transform: scale(1); }}
    50% {{ opacity: 1; transform: scale(1.08); }}
  }}
  @keyframes fade-up {{
    0% {{ opacity: 0; transform: translateY(14px); }}
    100% {{ opacity: 1; transform: translateY(0); }}
  }}
</style>
</head>
<body>
  <div class="card">
    <div class="icon-wrap">
      <div class="icon-glow"></div>
      <div class="icon-circle">
        <svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">{icon_svg}</svg>
      </div>
    </div>
    <h1>{html.escape(heading)}</h1>
    <p class="subtitle">{html.escape(subtitle)}</p>
    <a class="btn" href="{html.escape(link)}">🚀 {html.escape(button_label)}</a>
  </div>
</body>
</html>"""


async def handle_success(request: web.Request) -> web.Response:
    params = await _params_from_request(request)
    bot = request.app["telegram_bot"]
    ok, msg = await process_payu_response(params, bot)
    username = request.app.get("bot_username", "")
    link = f"https://t.me/{username}" if username else "#"
    success = ok and msg in ("success", "already processed")

    subtitle = "Your premium plan is now active." if success else "We couldn't confirm this payment."
    txnid = params.get("txnid")
    if success and txnid:
        txn = await get_transaction(txnid)
        if txn:
            expiry_iso = await get_premium_expiry(txn["user_id"])
            if expiry_iso:
                try:
                    expiry = datetime.fromisoformat(expiry_iso)
                    plan_label = txn.get("plan_label", PLAN_LABEL)
                    subtitle = f"{plan_label} is active until {expiry.strftime('%d %b %Y')}."
                except Exception:
                    pass

    page = _status_page(
        success=success,
        heading="Payment Successful" if success else "Payment Received",
        subtitle=subtitle,
        link=link,
        button_label="Back to Bot",
    )
    return web.Response(text=page, content_type="text/html")


async def handle_failure(request: web.Request) -> web.Response:
    params = await _params_from_request(request)
    bot = request.app["telegram_bot"]
    await process_payu_response(params, bot)
    username = request.app.get("bot_username", "")
    link = f"https://t.me/{username}" if username else "#"
    page = _status_page(
        success=False,
        heading="Payment Failed",
        subtitle="Your payment didn't go through, or was cancelled. No amount was charged.",
        link=link,
        button_label="Back to Bot",
    )
    return web.Response(text=page, content_type="text/html")


def build_web_app(telegram_bot) -> web.Application:
    app = web.Application()
    app["telegram_bot"] = telegram_bot
    app.router.add_get("/", handle_index)
    app.router.add_get("/payu/pay/{txnid}", handle_pay_page)
    app.router.add_route("*", "/payu/webhook", handle_webhook)
    app.router.add_route("*", "/payu/success", handle_success)
    app.router.add_route("*", "/payu/failure", handle_failure)
    return app
