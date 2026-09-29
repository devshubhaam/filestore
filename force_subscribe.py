"""Force-subscribe: user must join the listed channels/groups first."""



import uuid
import logging

import db

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Force subscribe
# --------------------------------------------------------------------------

async def is_force_sub_enabled() -> bool:
    return (await db.get_settings())["force_subscribe"]["enabled"]


async def set_force_sub_enabled(value: bool) -> None:
    await db.set_settings(**{"force_subscribe.enabled": value})


async def list_force_sub_channels() -> list[dict]:
    return list((await db.get_settings())["force_subscribe"]["channels"])


async def add_force_sub_channel(chat_id: int, title: str, invite_link: str,
                                 username: str | None = None) -> dict:
    entry = {
        "entry_id": uuid.uuid4().hex[:8],
        "id": chat_id,
        "title": title,
        "username": username,
        "invite_link": invite_link,
    }
    await db.get_db().settings.update_one(
        {"_id": db.SETTINGS_ID}, {"$push": {"force_subscribe.channels": entry}}, upsert=True
    )
    return entry


async def remove_force_sub_channel(entry_id: str) -> bool:
    res = await db.get_db().settings.update_one(
        {"_id": db.SETTINGS_ID},
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


