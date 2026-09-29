"""Link shorteners (admin-managed, multiple). Each has its own credit reward."""



import uuid
import random
import logging
from urllib.parse import quote

import aiohttp

import db

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Link shorteners (admin-only, multiple)
# settings["shorteners"] = [
#     {"id": "a1b2c3d4", "name": "GPLinks", "api_domain": "api.gplinks.com",
#      "api_key": "...", "enabled": True}, ...
# ]
# --------------------------------------------------------------------------

async def list_shorteners() -> list[dict]:
    shorteners = list((await db.get_settings())["shorteners"])
    for s in shorteners:
        s.setdefault("reward_credits", 5)
    return shorteners


async def add_shortener(name: str, api_domain: str, api_key: str, reward_credits: int = 5) -> dict:
    entry = {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "api_domain": api_domain.strip().removeprefix("https://").removeprefix("http://").rstrip("/"),
        "api_key": api_key.strip(),
        "enabled": True,
        "reward_credits": reward_credits,
    }
    await db.get_db().settings.update_one(
        {"_id": db.SETTINGS_ID}, {"$push": {"shorteners": entry}}, upsert=True
    )
    return entry


async def set_shortener_reward(shortener_id: str, reward_credits: int) -> bool:
    res = await db.get_db().settings.update_one(
        {"_id": db.SETTINGS_ID, "shorteners.id": shortener_id},
        {"$set": {"shorteners.$.reward_credits": reward_credits}},
    )
    return res.modified_count == 1


async def remove_shortener(shortener_id: str) -> bool:
    res = await db.get_db().settings.update_one(
        {"_id": db.SETTINGS_ID}, {"$pull": {"shorteners": {"id": shortener_id}}}
    )
    return res.modified_count == 1


async def toggle_shortener(shortener_id: str) -> bool | None:
    """Flips a shortener's enabled flag. Returns the new state, or None
    if no shortener with that id exists."""
    async with db.DATA_LOCK:
        for s in await list_shorteners():
            if s["id"] == shortener_id:
                new_state = not s["enabled"]
                await db.get_db().settings.update_one(
                    {"_id": db.SETTINGS_ID, "shorteners.id": shortener_id},
                    {"$set": {"shorteners.$.enabled": new_state}},
                )
                return new_state
        return None


async def shorten_url(long_url: str, shortener_id: str | None = None) -> str:
    """Shortens `long_url`. If `shortener_id` is given, uses that specific
    shortener; otherwise picks randomly among enabled ones (GPLinks-style
    API: GET https://<domain>/api?api=<key>&url=<url> ->
    {"status": "success", "shortenedUrl": "..."}). Falls back to the
    original long_url if none are configured/found or the request fails,
    so the bot degrades gracefully instead of blocking users."""
    shorteners = [s for s in await list_shorteners() if s.get("enabled")]
    if shortener_id:
        shorteners = [s for s in shorteners if s["id"] == shortener_id]
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


