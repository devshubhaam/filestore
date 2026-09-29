"""Token verification: free-user access gate + single-use verify tokens
(also used when earning credits through a shortener)."""



import uuid
import logging
from datetime import datetime, timedelta, timezone

import db

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Token verification (free users only)
# --------------------------------------------------------------------------

async def is_token_verification_enabled() -> bool:
    return (await db.get_settings())["token_verification"]["enabled"]


async def set_token_verification_enabled(value: bool) -> None:
    await db.set_settings(**{"token_verification.enabled": value})


async def get_verification_validity_hours() -> int:
    return (await db.get_settings())["token_verification"]["validity_hours"]


async def set_verification_validity_hours(hours: int) -> None:
    await db.set_settings(**{"token_verification.validity_hours": hours})


async def is_verified(user_id: int) -> bool:
    doc = await db.get_db().verified.find_one({"_id": user_id})
    if not doc:
        return False
    try:
        return datetime.fromisoformat(doc["expiry"]) > datetime.now(timezone.utc)
    except Exception:
        return False


async def set_verified(user_id: int, hours: int) -> str:
    expiry = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    await db.get_db().verified.update_one(
        {"_id": user_id}, {"$set": {"expiry": expiry}}, upsert=True
    )
    return expiry


# --------------------------------------------------------------------------
# Single-use verification tokens
#
# Used for BOTH the free-user file-access gate ("token verification") and
# earning credits via a shortener. A token is only ever redeemable once,
# and only by the Telegram user it was created for — this replaces an
# earlier scheme that just embedded the user id in the token itself
# (guessable/reusable by anyone, not a real security check).
# --------------------------------------------------------------------------

async def create_verify_token(user_id: int, purpose: str,
                               shortener_id: str | None = None,
                               reward_credits: int = 0,
                               payload: str | None = None) -> str:
    token = uuid.uuid4().hex
    await db.get_db().verify_tokens.insert_one({
        "_id": token,
        "user_id": user_id,
        "purpose": purpose,  # "gate" or "credit"
        "shortener_id": shortener_id,
        "reward_credits": reward_credits,
        "payload": payload,  # e.g. the file link to resume after verifying
        "created_at": datetime.now(timezone.utc),
        "used": False,
    })
    return token


async def consume_verify_token(token: str, clicking_user_id: int) -> dict | None:
    """Atomically marks a token used — only if it exists, is unused, and
    belongs to the user clicking it. Returns the token doc on success
    (with "_id" popped), or None if invalid/already used/not theirs."""
    res = await db.get_db().verify_tokens.find_one_and_update(
        {"_id": token, "user_id": clicking_user_id, "used": False},
        {"$set": {"used": True, "used_at": db.now_iso()}},
    )
    if res:
        res.pop("_id", None)
    return res


