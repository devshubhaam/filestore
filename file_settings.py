"""File-delivery settings: caption, thumbnail, custom button, auto-delete,
protect-content and the free usage limit."""



import logging
from datetime import datetime, timezone

import db

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Custom caption / thumbnail / button / auto-delete / protect content
# --------------------------------------------------------------------------

async def is_caption_enabled() -> bool:
    return (await db.get_settings())["caption"]["enabled"]


async def set_caption_enabled(value: bool) -> None:
    await db.set_settings(**{"caption.enabled": value})


async def get_caption_template() -> str | None:
    return (await db.get_settings())["caption"]["template"]


async def set_caption_template(template: str) -> None:
    await db.set_settings(**{"caption.template": template})


async def is_thumbnail_enabled() -> bool:
    return (await db.get_settings())["thumbnail"]["enabled"]


async def set_thumbnail_enabled(value: bool) -> None:
    await db.set_settings(**{"thumbnail.enabled": value})


async def get_thumbnail_file_id() -> str | None:
    return (await db.get_settings())["thumbnail"]["file_id"]


async def set_thumbnail_file_id(file_id: str | None) -> None:
    await db.set_settings(**{"thumbnail.file_id": file_id})


async def is_custom_button_enabled() -> bool:
    return (await db.get_settings())["button"]["enabled"]


async def set_custom_button_enabled(value: bool) -> None:
    await db.set_settings(**{"button.enabled": value})


async def get_custom_button() -> dict:
    return dict((await db.get_settings())["button"])


async def set_custom_button(label: str, url: str) -> None:
    await db.set_settings(**{"button.label": label, "button.url": url})


async def is_auto_delete_enabled() -> bool:
    return (await db.get_settings())["auto_delete"]["enabled"]


async def set_auto_delete_enabled(value: bool) -> None:
    await db.set_settings(**{"auto_delete.enabled": value})


async def get_auto_delete_seconds() -> int:
    return (await db.get_settings())["auto_delete"]["seconds"]


async def set_auto_delete_seconds(seconds: int) -> None:
    await db.set_settings(**{"auto_delete.seconds": seconds})


async def is_protect_content_enabled() -> bool:
    return (await db.get_settings())["protect_content"]["enabled"]


async def set_protect_content_enabled(value: bool) -> None:
    await db.set_settings(**{"protect_content.enabled": value})



# --------------------------------------------------------------------------
# Free usage limit
# --------------------------------------------------------------------------

async def is_free_limit_enabled() -> bool:
    return (await db.get_settings())["free_limit_enabled"]


async def set_free_limit_enabled(value: bool) -> None:
    await db.set_settings(free_limit_enabled=value)


async def get_free_limit_count() -> int:
    return (await db.get_settings())["free_limit_count"]


async def set_free_limit_count(count: int) -> None:
    await db.set_settings(free_limit_count=count)


async def try_use_free_quota(user_id: int) -> bool:
    """Spends one of the user's free file opens for today (UTC day).
    Returns False if the free limit is off, is 0, or is already used up.
    Every step is one atomic Mongo operation, so concurrent requests can't
    exceed the limit."""
    if not await is_free_limit_enabled():
        return False
    limit = await get_free_limit_count()
    if limit <= 0:
        return False
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    col = db.get_db().free_usage
    # Same day, still under the limit.
    res = await col.update_one(
        {"_id": user_id, "date": today, "count": {"$lt": limit}}, {"$inc": {"count": 1}}
    )
    if res.modified_count == 1:
        return True
    # A previous day's record -> start today's count at 1.
    res = await col.update_one({"_id": user_id, "date": {"$ne": today}},
                               {"$set": {"date": today, "count": 1}})
    if res.modified_count == 1:
        return True
    # Never used before -> create the record (does nothing if it exists).
    res = await col.update_one({"_id": user_id}, {"$setOnInsert": {"date": today, "count": 1}},
                               upsert=True)
    return res.upserted_id is not None


async def refund_free_quota(user_id: int) -> None:
    """Gives back one free open (used if sending the file failed)."""
    await db.get_db().free_usage.update_one(
        {"_id": user_id, "count": {"$gt": 0}}, {"$inc": {"count": -1}}
    )
