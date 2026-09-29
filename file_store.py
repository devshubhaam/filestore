"""File store: saved files/batches and their shareable deep links.

An admin sends media to the bot (/link for one file, /batch ... /done for
several) and gets back a link `https://t.me/<bot>?start=file_<id>`.
Only Telegram `file_id`s are stored — the files themselves stay on
Telegram's servers.

Collection `files`:
  {_id: link_id, items: [{type, file_id, name, size, caption}], created_by,
   created_at, downloads}
"""

import uuid
import logging
from datetime import datetime, timezone

import db

logger = logging.getLogger(__name__)

MAX_BATCH_ITEMS = 100


def extract_media(message) -> dict | None:
    """Pulls the storable media out of a Telegram message, or None if the
    message carries none (text, sticker, ...)."""
    caption = message.caption or ""
    if message.document:
        m = message.document
        return {"type": "document", "file_id": m.file_id, "name": m.file_name,
                "size": m.file_size, "caption": caption}
    if message.video:
        m = message.video
        return {"type": "video", "file_id": m.file_id, "name": m.file_name,
                "size": m.file_size, "caption": caption}
    if message.audio:
        m = message.audio
        return {"type": "audio", "file_id": m.file_id, "name": m.file_name or m.title,
                "size": m.file_size, "caption": caption}
    if message.animation:
        m = message.animation
        return {"type": "animation", "file_id": m.file_id, "name": m.file_name,
                "size": m.file_size, "caption": caption}
    if message.voice:
        m = message.voice
        return {"type": "voice", "file_id": m.file_id, "name": None,
                "size": m.file_size, "caption": caption}
    if message.photo:
        m = message.photo[-1]
        return {"type": "photo", "file_id": m.file_id, "name": None,
                "size": m.file_size, "caption": caption}
    return None


async def create_link(items: list[dict], created_by: int) -> str:
    link_id = uuid.uuid4().hex[:12]
    await db.get_db().files.insert_one({
        "_id": link_id,
        "items": items[:MAX_BATCH_ITEMS],
        "created_by": created_by,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "downloads": 0,
    })
    return link_id


async def get_link(link_id: str) -> dict | None:
    doc = await db.get_db().files.find_one({"_id": link_id})
    if doc:
        doc["id"] = doc.pop("_id")
    return doc


async def record_download(link_id: str) -> None:
    await db.get_db().files.update_one({"_id": link_id}, {"$inc": {"downloads": 1}})


def format_size(size: int | None) -> str:
    if not size:
        return "?"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return "?"
  
