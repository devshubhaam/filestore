"""Credit system: balance ledger (atomic add/deduct), cost per file, daily
claim and the UPI credit-pack catalogue."""



import logging
from datetime import datetime, timedelta, timezone

import db

logger = logging.getLogger(__name__)

# Credit packs for UPI purchase — bigger packs cost less per credit
# (bulk discount). Edit freely; `id` is used in callback_data, keep it
# short and stable.
CREDIT_PACKS = [
    {"id": "c10", "credits": 10, "amount": "10"},
    {"id": "c30", "credits": 30, "amount": "27"},
    {"id": "c60", "credits": 60, "amount": "48"},
    {"id": "c100", "credits": 100, "amount": "70"},
]


def get_credit_pack(pack_id: str) -> dict | None:
    for p in CREDIT_PACKS:
        if p["id"] == pack_id:
            return p
    return None

# --------------------------------------------------------------------------
# Credits
# credits: {_id: user_id, balance: int}
# daily_claims: {_id: user_id, last_claim: iso}
# --------------------------------------------------------------------------

async def get_credits(user_id: int) -> int:
    doc = await db.get_db().credits.find_one({"_id": user_id})
    return doc["balance"] if doc else 0


async def add_credits(user_id: int, amount: int) -> int:
    """Adds (or subtracts, if amount is negative) credits and returns the
    new balance."""
    doc = await db.get_db().credits.find_one_and_update(
        {"_id": user_id},
        {"$inc": {"balance": amount}},
        upsert=True,
        return_document=True,
    )
    return doc["balance"]


async def deduct_credit(user_id: int, cost: int = 1) -> bool:
    """Atomically spends `cost` credits. Returns False (and changes
    nothing) if the balance is too low — so concurrent requests can never
    push a balance negative."""
    res = await db.get_db().credits.update_one(
        {"_id": user_id, "balance": {"$gte": cost}},
        {"$inc": {"balance": -cost}},
    )
    return res.modified_count == 1


async def get_credit_cost_per_file() -> int:
    return (await db.get_settings())["credit_cost_per_file"]


async def set_credit_cost_per_file(cost: int) -> None:
    await db.set_settings(credit_cost_per_file=cost)


async def is_daily_credit_enabled() -> bool:
    return (await db.get_settings())["daily_credit"]["enabled"]


async def set_daily_credit_enabled(value: bool) -> None:
    await db.set_settings(**{"daily_credit.enabled": value})


async def get_daily_credit_amount() -> int:
    return (await db.get_settings())["daily_credit"]["amount"]


async def set_daily_credit_amount(amount: int) -> None:
    await db.set_settings(**{"daily_credit.amount": amount})


async def claim_daily_credit(user_id: int) -> tuple[bool, int | timedelta]:
    """Grants the daily credit if 24h have passed since the user's last
    claim. Returns (True, new_balance) on success, or
    (False, time_remaining) if they need to wait."""
    async with db.DATA_LOCK:
        now = datetime.now(timezone.utc)
        doc = await db.get_db().daily_claims.find_one({"_id": user_id})
        if doc:
            last = datetime.fromisoformat(doc["last_claim"])
            elapsed = now - last
            if elapsed < timedelta(hours=24):
                return False, timedelta(hours=24) - elapsed
        amount = await get_daily_credit_amount()
        await db.get_db().daily_claims.update_one(
            {"_id": user_id}, {"$set": {"last_claim": now.isoformat()}}, upsert=True
        )
        new_balance = await add_credits(user_id, amount)
        return True, new_balance
