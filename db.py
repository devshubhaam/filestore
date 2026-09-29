"""Shared MongoDB core: connection, settings document, users and the
transactions ledger. Every other module builds on this one.

Required env: MONGO_URI      Optional: MONGO_DB_NAME (default "filestore_bot")
"""

import os
import json
import asyncio
import logging
import pathlib
from datetime import datetime, timezone

from pymongo import AsyncMongoClient
from pymongo.errors import DuplicateKeyError

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Storage — MongoDB (PyMongo's native async API)
#
# Collections (database name comes from MONGO_DB_NAME):
#   users          {_id: user_id, first_name, username, joined_at, last_seen}
#   premium        {_id: user_id, expiry: iso}
#   verified       {_id: user_id, expiry: iso}      (token verification)
#   referrals      {_id: referred_user_id, referrer_id, rewarded}
#   credits        {_id: user_id, balance}
#   daily_claims   {_id: user_id, last_claim: iso}
#   verify_tokens  {_id: token, user_id, purpose, ...}   (single-use)
#   transactions   {_id: famgateway order_id, user_id, kind, amount, status, utr, ...}
#   settings       {_id: "main", ...all bot-wide settings...}
#   meta           {_id: "...", ...internal markers, e.g. legacy import}
# --------------------------------------------------------------------------
MONGO_URI = os.environ.get("MONGO_URI", "")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "filestore_bot")

# Old JSON file — only read once, to import existing data into MongoDB.
DATA_FILE = pathlib.Path(__file__).parent / "data.json"

# Serialises the few read-modify-write operations (extending premium,
# toggling a shortener). Everything else is a single atomic Mongo op.
DATA_LOCK = asyncio.Lock()

SETTINGS_ID = "main"
_client: AsyncMongoClient | None = None

def get_db():
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
    db = get_db()
    await db.command("ping")
    await db.referrals.create_index("referrer_id")
    await db.transactions.create_index("user_id")
    await db.verify_tokens.create_index("created_at", expireAfterSeconds=7 * 24 * 3600)
    # A bank UTR can only ever fulfil ONE order (blocks replaying the same
    # payment against several orders). Partial: orders without a UTR yet
    # are not indexed.
    await db.transactions.create_index(
        "utr", unique=True, partialFilterExpression={"utr": {"$type": "string"}}
    )
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
        await db.settings.replace_one({"_id": SETTINGS_ID}, dict(legacy["settings"]), upsert=True)

    await db.meta.insert_one({"_id": marker_id, "imported_at": now_iso()})
    logger.info("Imported legacy data.json into MongoDB")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# --------------------------------------------------------------------------
# Users
# --------------------------------------------------------------------------

async def register_user(user) -> bool:
    """Upserts a Telegram user (call on /start). Returns True if this is
    the first time we've seen them."""
    now = now_iso()
    res = await get_db().users.update_one(
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
    return await get_db().users.count_documents({})


async def get_all_user_ids() -> list[int]:
    ids = []
    async for doc in get_db().users.find({}, {"_id": 1}):
        ids.append(doc["_id"])
    return ids



# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------

async def save_transaction(txnid: str, txn: dict) -> None:
    await get_db().transactions.replace_one({"_id": txnid}, dict(txn), upsert=True)


async def get_transaction(txnid: str) -> dict | None:
    doc = await get_db().transactions.find_one({"_id": txnid})
    if doc:
        doc.pop("_id", None)
    return doc


async def update_transaction(txnid: str, fields: dict) -> None:
    await get_db().transactions.update_one({"_id": txnid}, {"$set": fields})


async def claim_transaction_success(txnid: str, utr: str | None = None) -> bool:
    """Atomically flips a transaction to "success". Returns True only for
    the single caller that made the flip. FamGateway can confirm the same
    payment through the poller AND the webhook, so this guarantees premium/
    credits are granted exactly once. Also stores the bank UTR; if that UTR
    was already used by another order the claim is refused (replay)."""
    fields = {"status": "success", "paid_at": now_iso()}
    if utr:
        fields["utr"] = utr
    try:
        res = await get_db().transactions.update_one(
            {"_id": txnid, "status": {"$ne": "success"}}, {"$set": fields}
        )
    except DuplicateKeyError:
        logger.warning("UTR %s already used by another order — refusing txn %s", utr, txnid)
        return False
    return res.modified_count == 1


async def unclaim_transaction(txnid: str) -> None:
    """Rolls a claimed transaction back to "pending" (used if granting
    premium/credits fails right after claiming, so a retry can succeed)."""
    await get_db().transactions.update_one(
        {"_id": txnid},
        {"$set": {"status": "pending"}, "$unset": {"utr": "", "paid_at": ""}},
    )


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

    # --- Credit system ---
    dc = settings.setdefault("daily_credit", {})
    dc.setdefault("enabled", True)
    dc.setdefault("amount", 1)

    settings.setdefault("referral_reward_credits", 5)
    settings.setdefault("credit_cost_per_file", 1)

    return settings


async def get_settings() -> dict:
    doc = await get_db().settings.find_one({"_id": SETTINGS_ID}) or {}
    doc.pop("_id", None)
    return _apply_defaults(doc)


async def set_settings(**fields) -> None:
    """$set one or more settings fields. Use dotted paths via the dict
    form: set_settings(**{"caption.enabled": True})."""
    if not fields:
        return
    await get_db().settings.update_one(
        {"_id": SETTINGS_ID}, {"$set": fields}, upsert=True
    )
