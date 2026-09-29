"""Premium plans: plan catalogue, per-user premium expiry, admin toggle and
custom buy message."""



import os
import logging
from datetime import datetime, timedelta, timezone

import db

logger = logging.getLogger(__name__)

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

# --------------------------------------------------------------------------
# Premium
# --------------------------------------------------------------------------

async def set_premium(user_id: int, expiry_iso: str) -> None:
    await db.get_db().premium.update_one(
        {"_id": user_id}, {"$set": {"expiry": expiry_iso}}, upsert=True
    )


async def get_premium_expiry(user_id: int) -> str | None:
    doc = await db.get_db().premium.find_one({"_id": user_id})
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
    async with db.DATA_LOCK:
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
    async for doc in db.get_db().premium.find():
        out[str(doc["_id"])] = doc["expiry"]
    return out


async def remove_premium(user_id: int) -> bool:
    """Removes a user from the premium store. Returns True if they were
    present, False if they weren't premium to begin with."""
    res = await db.get_db().premium.delete_one({"_id": user_id})
    return res.deleted_count > 0



async def is_premium_enabled() -> bool:
    return (await db.get_settings())["premium_enabled"]


async def set_premium_enabled(value: bool) -> None:
    await db.set_settings(premium_enabled=value)


async def get_premium_message() -> dict:
    return dict((await db.get_settings())["premium_message"])


async def set_premium_message(**fields) -> None:
    """Updates one or more fields of the custom premium-plan message
    (text, photo_file_id, button_text, button_url)."""
    await db.set_settings(**{f"premium_message.{k}": v for k, v in fields.items()})

