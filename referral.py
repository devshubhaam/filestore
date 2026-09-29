"""Refer & Earn: who referred whom, and the credit reward for the referrer."""



import logging

from pymongo.errors import DuplicateKeyError

import db
import credits

logger = logging.getLogger(__name__)

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
        await db.get_db().referrals.insert_one(
            {"_id": referred_id, "referrer_id": referrer_id, "rewarded": False}
        )
        return True
    except DuplicateKeyError:
        return False


async def get_referral(referred_id: int) -> dict | None:
    doc = await db.get_db().referrals.find_one({"_id": referred_id})
    if not doc:
        return None
    return {"referrer_id": doc.get("referrer_id"), "rewarded": bool(doc.get("rewarded"))}


async def mark_referral_rewarded(referred_id: int) -> None:
    await db.get_db().referrals.update_one({"_id": referred_id}, {"$set": {"rewarded": True}})


async def get_referral_stats(referrer_id: int) -> dict:
    """Returns {"total": n, "rewarded": n} for everyone a user has
    referred so far."""
    referrals = db.get_db().referrals
    total = await referrals.count_documents({"referrer_id": referrer_id})
    rewarded = await referrals.count_documents({"referrer_id": referrer_id, "rewarded": True})
    return {"total": total, "rewarded": rewarded}



# --------------------------------------------------------------------------
# Referral reward (paid in credits)
# --------------------------------------------------------------------------

async def get_referral_reward_credits() -> int:
    return (await db.get_settings())["referral_reward_credits"]


async def set_referral_reward_credits(amount: int) -> None:
    await db.set_settings(referral_reward_credits=amount)


async def maybe_reward_referral_credits(referred_id: int, bot=None) -> None:
    """Call after a referred user's first credit-earning action (shortener
    completion or a purchase). Rewards their referrer once, in credits,
    and (if `bot` is given) tells them about it."""
    referral = await get_referral(referred_id)
    if not referral or referral.get("rewarded"):
        return
    # Flip "rewarded" first (atomically) so two simultaneous triggers can't
    # both pay out.
    res = await db.get_db().referrals.update_one(
        {"_id": referred_id, "rewarded": {"$ne": True}}, {"$set": {"rewarded": True}}
    )
    if res.modified_count != 1:
        return
    referrer_id = referral["referrer_id"]
    reward = await get_referral_reward_credits()
    try:
        new_balance = await credits.add_credits(referrer_id, reward)
    except Exception:
        await db.get_db().referrals.update_one({"_id": referred_id}, {"$set": {"rewarded": False}})
        raise
    if bot is not None:
        try:
            await bot.send_message(
                chat_id=referrer_id,
                text=(
                    f"🎁 Your referral just completed their first action! You've earned "
                    f"+{reward} credits.\nYour credit balance is now {new_balance}."
                ),
            )
        except Exception:
            logger.exception("Could not notify referrer %s", referrer_id)
