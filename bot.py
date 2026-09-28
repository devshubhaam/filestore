"""
Telegram File Store Bot - /start command
-----------------------------------------
Replicates the welcome message:
  "Hey <name>," bold
  "My name is Haven 🌺" as a blockquote
  Description text
  "To know more click help button." as a blockquote
  Inline buttons: 🕵️ Help | 📜 About
                  ‼️ Settings ‼️

Requires: pip install python-telegram-bot --upgrade
Run: BOT_TOKEN="your-token-here" python bot.py
"""

import os
import html
import uuid
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from aiohttp import web

import payments

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT-YOUR-BOT-TOKEN-HERE")

# Comma-separated Telegram user IDs allowed to manage the Premium Plan
# admin panel (add/remove premium users, toggle premium, edit the buy
# message). e.g. ADMIN_IDS="111111111,222222222"
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()
}


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def to_bold_unicode(text: str) -> str:
    """Convert A-Z, a-z, 0-9 in `text` to Mathematical Sans-Serif Bold
    Unicode codepoints. Everything else (spaces, punctuation, emoji) is
    left untouched, so this needs no parse_mode to render bold."""
    result = []
    for ch in text:
        code = ord(ch)
        if 0x41 <= code <= 0x5A:          # A-Z
            result.append(chr(0x1D5D4 + (code - 0x41)))
        elif 0x61 <= code <= 0x7A:        # a-z
            result.append(chr(0x1D5EE + (code - 0x61)))
        elif 0x30 <= code <= 0x39:        # 0-9
            result.append(chr(0x1D7EC + (code - 0x30)))
        else:
            result.append(ch)
    return "".join(result)


def build_help_text() -> str:
    intro = (
        "<blockquote>"
        "🤖 " + to_bold_unicode("I am a permanent file store bot") + "\n"
        "📧 " + to_bold_unicode(
            "My name is file store bot, but I can store any type of "
            "content, including messages, photos, videos, and files."
        ) + "\n"
        "📁 " + to_bold_unicode(
            "You can store files from your public channel without adding "
            "me as an admin."
        ) + "\n"
        "🔒 " + to_bold_unicode(
            "If your channel or group is private, make me an admin first."
        ) + "\n"
        "🔗 " + to_bold_unicode(
            "After storing, I will generate a shareable link for instant "
            "access to your files."
        )
        + "</blockquote>"
    )

    def section(emoji: str, title: str, commands: list[tuple[str, str]]) -> str:
        head = f"<blockquote>{emoji} " + to_bold_unicode(title) + "</blockquote>"
        body = "\n".join(
            f"➤ /{cmd} - " + to_bold_unicode(desc) for cmd, desc in commands
        )
        return f"{head}\n{body}"

    basic = section("📚", "Basic commands", [
        ("start", "Check if I'm alive."),
        ("id", "Get your telegram ID."),
    ])

    buy_plan = section("💰", "Buy", [
        ("buy", "Buy the premium plan."),
    ])

    file_storage = section("📦", "File storage commands", [
        ("link", "Store a single file or message."),
        ("batch", "Store multiple consecutive messages from a channel."),
        ("custom_batch", "Store multiple random messages."),
        ("multiple_batch", "Create multiple batch links at once."),
        ("special_link", "Create special links with custom settings."),
    ])

    premium = section("💎", "Premium commands", [
        ("createcode", "Create premium redeem codes."),
        ("redeem", "Redeem a premium code."),
        ("myplan", "View your premium plan details."),
    ])

    user_settings = section("⚙️", "User settings", [
        ("settings", "Customize your personal settings."),
    ])

    admin = section("👑", "Admin commands", [
        ("customize", "Customize clone bot settings."),
        ("broadcast", "Broadcast a message to all users."),
        ("ban", "Ban a user from the bot."),
        ("unban", "Unban a previously banned user."),
    ])

    header = "🎓 <u>" + to_bold_unicode("Help menu:") + "</u>"

    return "\n\n".join(
        [header, intro, basic, buy_plan, file_storage, premium, user_settings, admin]
    )


def build_help_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("‼️ Settings ‼️", callback_data="help_settings"),
            InlineKeyboardButton("📜 About", callback_data="help_about"),
        ],
        [
            InlineKeyboardButton("◀ Back", callback_data="help_back"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_settings_text() -> str:
    header = "⚙️ <u>" + to_bold_unicode("Settings:") + "</u>"
    note1 = (
        "<blockquote>" + to_bold_unicode("Customize your settings as per your need.") + "</blockquote>"
    )
    note2 = (
        "<blockquote>"
        + to_bold_unicode(
            "Note: The settings below will only work for links created by "
            "this Telegram account. They will not affect links created by "
            "other accounts."
        )
        + "</blockquote>"
    )
    return "\n\n".join([header, note1, note2])


def build_settings_keyboard(is_admin_user: bool = False) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("💎 Premium plan", callback_data="settings_premium")],
        [InlineKeyboardButton("🆓 Free usage limit", callback_data="settings_free_limit")],
        [InlineKeyboardButton("🌍 Refer and earn", callback_data="settings_refer")],
    ]
    # Link shortener panel is admin-only, per the owner's setup — it never
    # shows up in a regular user's Settings menu at all.
    if is_admin_user:
        keyboard.append([InlineKeyboardButton("🔗 Link shortner", callback_data="settings_link_shortener")])
    keyboard.append(
        [InlineKeyboardButton("⏰ Token verification", callback_data="settings_token_verification")]
    )
    keyboard += [
        [InlineKeyboardButton("📢 Force subscribe", callback_data="settings_force_subscribe")],
        [
            InlineKeyboardButton("🍿 Caption", callback_data="settings_caption"),
            InlineKeyboardButton("🖼 Thumbnail", callback_data="settings_thumbnail"),
        ],
        [
            InlineKeyboardButton("⚪ Button", callback_data="settings_button"),
            InlineKeyboardButton("♻️ Auto delete", callback_data="settings_auto_delete"),
        ],
        [InlineKeyboardButton("♾️ Permanent link", callback_data="settings_permanent_link")],
        [InlineKeyboardButton("🔒 Protect content", callback_data="settings_protect_content")],
        [InlineKeyboardButton("◀ Back", callback_data="settings_back")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_premium_menu_text() -> str:
    header = "💸 <u>" + to_bold_unicode("Premium Plan:") + "</u>"
    desc = (
        "<blockquote>"
        + to_bold_unicode(
            "Premium Plan: A paid subscription that gives users ad-free "
            "access, faster downloads, and exclusive entry to restricted "
            "files or groups."
        )
        + "</blockquote>"
    )
    return f"{header}\n\n{desc}"


def build_premium_menu_keyboard(premium_on: bool = True) -> InlineKeyboardMarkup:
    toggle_label = "🔒 Premium is on - ✅" if premium_on else "🔓 Premium is off - ❌"
    keyboard = [
        [InlineKeyboardButton("🧵 Premium plan message 🧵", callback_data="premium_plan_message")],
        [InlineKeyboardButton("➕ Add premium user ➕", callback_data="premium_add_user")],
        [InlineKeyboardButton("➖ Remove premium user ➖", callback_data="premium_remove_user")],
        [InlineKeyboardButton("🚦 Premium users list 🚦", callback_data="premium_users_list")],
        [InlineKeyboardButton(toggle_label, callback_data="premium_toggle")],
        [InlineKeyboardButton("◀ Back", callback_data="premium_back")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_premium_message_menu_text() -> str:
    header = "📝 <u>" + to_bold_unicode("Premium plan message:") + "</u>"
    desc = (
        "<blockquote>"
        + to_bold_unicode(
            "Premium plan message: when a user clicks the Buy Premium "
            "Plan button, the bot replies with this premium plan "
            "message. Here the bot owner can set the premium plan "
            "message text, picture and button."
        )
        + "</blockquote>"
    )
    return f"{header}\n\n{desc}"


def build_premium_message_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("Premium plan text", callback_data="premium_message_text")],
        [InlineKeyboardButton("Premium plan picture", callback_data="premium_message_picture")],
        [InlineKeyboardButton("Premium plan button", callback_data="premium_message_button")],
        [InlineKeyboardButton("◀ Back", callback_data="premium_message_back")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_refer_text(bot_username: str, user_id: int, stats: dict) -> str:
    link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    header = "🌍 <u>" + to_bold_unicode("Refer and earn:") + "</u>"
    desc = (
        "<blockquote>"
        + to_bold_unicode(
            "Share your referral link. When someone you referred "
            "purchases the Premium Plan, you get +1 day of Premium, free."
        )
        + "</blockquote>"
    )
    link_line = "🔗 " + to_bold_unicode("Your referral link:") + f"\n{link}"
    stats_line = (
        "📊 " + to_bold_unicode(
            f"Referred: {stats['total']}  |  Rewarded: {stats['rewarded']}"
        )
    )
    return "\n\n".join([header, desc, link_line, stats_line])


def build_free_limit_text(enabled: bool, count: int) -> str:
    header = "🆓 <u>" + to_bold_unicode("Free usage limit:") + "</u>"
    status = to_bold_unicode(f"Status: {'ON ✅' if enabled else 'OFF ❌'}")
    count_line = to_bold_unicode(f"Free uses per day: {count}")
    desc = (
        "<blockquote>"
        + to_bold_unicode(
            "When enabled, non-premium users will be limited to this many "
            "free uses per day. Premium users are never limited."
        )
        + "</blockquote>"
    )
    return "\n\n".join([header, desc, status, count_line])


def build_free_limit_keyboard(enabled: bool) -> InlineKeyboardMarkup:
    toggle_label = "🔒 Limit is on - ✅" if enabled else "🔓 Limit is off - ❌"
    keyboard = [
        [InlineKeyboardButton("✏️ Set daily limit", callback_data="free_limit_set")],
        [InlineKeyboardButton(toggle_label, callback_data="free_limit_toggle")],
        [InlineKeyboardButton("◀ Back", callback_data="free_limit_back")],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_start_text(user_first_name: str) -> str:
    # Text itself is already bold via Unicode Mathematical Sans-Serif Bold
    # characters, so only <blockquote> (structural, not styling) needs HTML.
    line1 = to_bold_unicode(f"Hey {user_first_name},")
    line2 = to_bold_unicode("My name is Haven 🌺")
    line3 = to_bold_unicode(
        "I am a permanent file store bot and users can access stored "
        "messages by using a shareable link given by me."
    )
    line4 = to_bold_unicode("To know more click help button.")

    text = (
        f"{line1}\n\n"
        f"<blockquote>{line2}</blockquote>\n\n"
        f"{line3}\n\n"
        f"<blockquote>{line4}</blockquote>"
    )
    return text


def build_start_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton("🕵️ Help", callback_data="help"),
            InlineKeyboardButton("📜 About", callback_data="about"),
        ],
        [
            InlineKeyboardButton("‼️ Settings ‼️", callback_data="settings"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_verification_deep_link(bot_username: str, user_id: int) -> str:
    """A one-time-looking deep link tied to `user_id`. Whoever clicks
    it after completing the shortener steps is verified for
    `token_verification.validity_hours`."""
    token = f"{user_id}-{uuid.uuid4().hex[:6]}"
    return f"https://t.me/{bot_username}?start=verify_{token}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user

    try:
        await payments.register_user(user)
    except Exception:
        logger.exception("Could not save user %s to MongoDB", user.id)

    if context.args:
        payload = context.args[0]

        if payload.startswith("ref_"):
            ref_part = payload[len("ref_"):]
            if ref_part.isdigit():
                referrer_id = int(ref_part)
                await payments.record_referral(referred_id=user.id, referrer_id=referrer_id)

        elif payload.startswith("verify_"):
            token = payload[len("verify_"):]
            owner_id_str = token.split("-", 1)[0]
            if not owner_id_str.isdigit() or int(owner_id_str) != user.id:
                await update.message.reply_text(
                    "⚠️ " + to_bold_unicode(
                        "Yeh verification link aapke liye nahi hai. Apna "
                        "khud ka link use karein."
                    )
                )
                return
            hours = await payments.get_verification_validity_hours()
            await payments.set_verified(user.id, hours)
            await update.message.reply_text(
                "✅ " + to_bold_unicode(
                    f"Verified! Ab agle {hours} ghante tak aap files "
                    "access kar sakte hain."
                )
                + "\n\n" + to_bold_unicode(
                    "Jo file link khola tha, wapas wahi link se try karein."
                ),
                parse_mode=ParseMode.HTML,
            )
            return

    await update.message.reply_text(
        text=build_start_text(user.first_name),
        parse_mode=ParseMode.HTML,
        reply_markup=build_start_keyboard(),
    )


# ============================================================
# Link shortener (admin-only)
# ============================================================

def build_shortener_menu_text(shorteners: list[dict]) -> str:
    header = "🔗 <u>" + to_bold_unicode("Link Shortener:") + "</u>"
    desc = (
        "<blockquote>" + to_bold_unicode(
            "Add multiple shorteners. Every verification link is "
            "shortened using one of the enabled ones, picked at random."
        ) + "</blockquote>"
    )
    if not shorteners:
        body = "<blockquote>" + to_bold_unicode("Koi shortener add nahi hai abhi.") + "</blockquote>"
    else:
        lines = []
        for s in shorteners:
            status = "✅" if s.get("enabled") else "❌"
            lines.append(f"• {status} " + to_bold_unicode(f"{s['name']} — {s['api_domain']}"))
        body = "\n".join(lines)
    return "\n\n".join([header, desc, body])


def build_shortener_menu_keyboard(shorteners: list[dict]) -> InlineKeyboardMarkup:
    keyboard = []
    for s in shorteners:
        toggle = "✅" if s.get("enabled") else "❌"
        keyboard.append([
            InlineKeyboardButton(f"{toggle} {s['name']}", callback_data=f"short_toggle_{s['id']}"),
            InlineKeyboardButton("🗑 Remove", callback_data=f"short_del_{s['id']}"),
        ])
    keyboard.append([InlineKeyboardButton("➕ Add Shortener", callback_data="short_add")])
    keyboard.append([InlineKeyboardButton("◀ Back", callback_data="settings")])
    return InlineKeyboardMarkup(keyboard)


# ============================================================
# Token verification (free users only)
# ============================================================

def build_verification_menu_text(enabled: bool, hours: int, admin_view: bool) -> str:
    header = "⏰ <u>" + to_bold_unicode("Token Verification:") + "</u>"
    desc = (
        "<blockquote>" + to_bold_unicode(
            "Free users get a shortener link to complete before they can "
            "open a file. Premium users always skip this."
        ) + "</blockquote>"
    )
    status = to_bold_unicode(f"Status: {'ON ✅' if enabled else 'OFF ❌'}")
    validity = to_bold_unicode(f"Valid for: {hours} hour(s) after verifying")
    return "\n\n".join([header, desc, status, validity])


def build_verification_menu_keyboard(enabled: bool, admin_view: bool) -> InlineKeyboardMarkup:
    if not admin_view:
        return InlineKeyboardMarkup([[InlineKeyboardButton("◀ Back", callback_data="settings")]])
    toggle_label = "🔒 Verification is on - ✅" if enabled else "🔓 Verification is off - ❌"
    keyboard = [
        [InlineKeyboardButton(toggle_label, callback_data="verify_toggle")],
        [InlineKeyboardButton("✏️ Set validity (hours)", callback_data="verify_set_hours")],
        [InlineKeyboardButton("◀ Back", callback_data="settings")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ============================================================
# Force subscribe
# ============================================================

def build_fsub_menu_text(enabled: bool, channels: list[dict], admin_view: bool) -> str:
    header = "📢 <u>" + to_bold_unicode("Force Subscribe:") + "</u>"
    desc = (
        "<blockquote>" + to_bold_unicode(
            "Users must join every channel/group listed below before "
            "they can open a file."
        ) + "</blockquote>"
    )
    status = to_bold_unicode(f"Status: {'ON ✅' if enabled else 'OFF ❌'}")
    if not channels:
        chan_lines = "<blockquote>" + to_bold_unicode("Koi channel/group add nahi hai abhi.") + "</blockquote>"
    else:
        chan_lines = "\n".join(
            "• " + to_bold_unicode(c["title"]) for c in channels
        )
    return "\n\n".join([header, desc, status, chan_lines])


def build_fsub_menu_keyboard(channels: list[dict], admin_view: bool) -> InlineKeyboardMarkup:
    keyboard = []
    if admin_view:
        for c in channels:
            keyboard.append([
                InlineKeyboardButton(f"🗑 {c['title']}", callback_data=f"fsub_del_{c['entry_id']}")
            ])
        keyboard.append([InlineKeyboardButton("♻️ Toggle on/off", callback_data="fsub_toggle")])
        keyboard.append([InlineKeyboardButton("➕ Add channel/group", callback_data="fsub_add")])
    keyboard.append([InlineKeyboardButton("◀ Back", callback_data="settings")])
    return InlineKeyboardMarkup(keyboard)


def build_fsub_join_keyboard(unjoined: list[dict]) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(f"📢 Join {c['title']}", url=c["invite_link"])] for c in unjoined
    ]
    keyboard.append([InlineKeyboardButton("✅ I've Joined", callback_data="fsub_recheck")])
    return InlineKeyboardMarkup(keyboard)


# ============================================================
# Caption
# ============================================================

def build_caption_menu_text(enabled: bool, template: str | None, admin_view: bool) -> str:
    header = "🍿 <u>" + to_bold_unicode("Custom Caption:") + "</u>"
    desc = (
        "<blockquote>" + to_bold_unicode(
            "This caption is applied to every file post. Placeholders: "
            "{filename} {filesize} {caption}"
        ) + "</blockquote>"
    )
    status = to_bold_unicode(f"Status: {'ON ✅' if enabled else 'OFF ❌'}")
    preview = (
        "<blockquote>" + html.escape(template) + "</blockquote>"
        if template else "<blockquote>" + to_bold_unicode("Abhi koi custom caption set nahi hai.") + "</blockquote>"
    )
    return "\n\n".join([header, desc, status, preview])


def build_caption_menu_keyboard(enabled: bool, admin_view: bool) -> InlineKeyboardMarkup:
    if not admin_view:
        return InlineKeyboardMarkup([[InlineKeyboardButton("◀ Back", callback_data="settings")]])
    toggle_label = "🔒 Caption is on - ✅" if enabled else "🔓 Caption is off - ❌"
    keyboard = [
        [InlineKeyboardButton(toggle_label, callback_data="caption_toggle")],
        [InlineKeyboardButton("✏️ Set caption", callback_data="caption_set")],
        [InlineKeyboardButton("◀ Back", callback_data="settings")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ============================================================
# Thumbnail
# ============================================================

def build_thumbnail_menu_text(enabled: bool, has_thumb: bool, admin_view: bool) -> str:
    header = "🖼 <u>" + to_bold_unicode("Custom Thumbnail:") + "</u>"
    desc = (
        "<blockquote>" + to_bold_unicode(
            "This thumbnail is applied to every file post that supports one."
        ) + "</blockquote>"
    )
    status = to_bold_unicode(f"Status: {'ON ✅' if enabled else 'OFF ❌'}")
    thumb_line = to_bold_unicode(f"Thumbnail set: {'Yes ✅' if has_thumb else 'No ❌'}")
    return "\n\n".join([header, desc, status, thumb_line])


def build_thumbnail_menu_keyboard(enabled: bool, admin_view: bool) -> InlineKeyboardMarkup:
    if not admin_view:
        return InlineKeyboardMarkup([[InlineKeyboardButton("◀ Back", callback_data="settings")]])
    toggle_label = "🔒 Thumbnail is on - ✅" if enabled else "🔓 Thumbnail is off - ❌"
    keyboard = [
        [InlineKeyboardButton(toggle_label, callback_data="thumb_toggle")],
        [InlineKeyboardButton("🖼 Set thumbnail", callback_data="thumb_set")],
        [InlineKeyboardButton("🗑 Remove thumbnail", callback_data="thumb_remove")],
        [InlineKeyboardButton("◀ Back", callback_data="settings")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ============================================================
# Button
# ============================================================

def build_button_menu_text(enabled: bool, btn: dict, admin_view: bool) -> str:
    header = "⚪ <u>" + to_bold_unicode("Custom Button:") + "</u>"
    desc = (
        "<blockquote>" + to_bold_unicode(
            "This extra inline button is attached under every file post."
        ) + "</blockquote>"
    )
    status = to_bold_unicode(f"Status: {'ON ✅' if enabled else 'OFF ❌'}")
    if btn.get("label") and btn.get("url"):
        preview = "<blockquote>" + html.escape(f"{btn['label']} → {btn['url']}") + "</blockquote>"
    else:
        preview = "<blockquote>" + to_bold_unicode("Abhi koi button set nahi hai.") + "</blockquote>"
    return "\n\n".join([header, desc, status, preview])


def build_button_menu_keyboard(enabled: bool, admin_view: bool) -> InlineKeyboardMarkup:
    if not admin_view:
        return InlineKeyboardMarkup([[InlineKeyboardButton("◀ Back", callback_data="settings")]])
    toggle_label = "🔒 Button is on - ✅" if enabled else "🔓 Button is off - ❌"
    keyboard = [
        [InlineKeyboardButton(toggle_label, callback_data="btn_toggle")],
        [InlineKeyboardButton("✏️ Set button", callback_data="btn_set")],
        [InlineKeyboardButton("◀ Back", callback_data="settings")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ============================================================
# Auto delete
# ============================================================

def build_auto_delete_menu_text(enabled: bool, seconds: int, admin_view: bool) -> str:
    header = "♻️ <u>" + to_bold_unicode("Auto Delete:") + "</u>"
    desc = (
        "<blockquote>" + to_bold_unicode(
            "The file message sent to a user is auto-deleted after this "
            "many seconds."
        ) + "</blockquote>"
    )
    status = to_bold_unicode(f"Status: {'ON ✅' if enabled else 'OFF ❌'}")
    delay = to_bold_unicode(f"Delay: {seconds} second(s)")
    return "\n\n".join([header, desc, status, delay])


def build_auto_delete_menu_keyboard(enabled: bool, admin_view: bool) -> InlineKeyboardMarkup:
    if not admin_view:
        return InlineKeyboardMarkup([[InlineKeyboardButton("◀ Back", callback_data="settings")]])
    toggle_label = "🔒 Auto delete is on - ✅" if enabled else "🔓 Auto delete is off - ❌"
    keyboard = [
        [InlineKeyboardButton(toggle_label, callback_data="ad_toggle")],
        [InlineKeyboardButton("✏️ Set delay (seconds)", callback_data="ad_set")],
        [InlineKeyboardButton("◀ Back", callback_data="settings")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ============================================================
# Protect content
# ============================================================

def build_protect_content_menu_text(enabled: bool, admin_view: bool) -> str:
    header = "🔒 <u>" + to_bold_unicode("Protect Content:") + "</u>"
    desc = (
        "<blockquote>" + to_bold_unicode(
            "When on, users can't forward or save the files sent by "
            "this bot."
        ) + "</blockquote>"
    )
    status = to_bold_unicode(f"Status: {'ON ✅' if enabled else 'OFF ❌'}")
    return "\n\n".join([header, desc, status])


def build_protect_content_menu_keyboard(enabled: bool, admin_view: bool) -> InlineKeyboardMarkup:
    if not admin_view:
        return InlineKeyboardMarkup([[InlineKeyboardButton("◀ Back", callback_data="settings")]])
    toggle_label = "🔒 Protect content is on - ✅" if enabled else "🔓 Protect content is off - ❌"
    keyboard = [
        [InlineKeyboardButton(toggle_label, callback_data="pc_toggle")],
        [InlineKeyboardButton("◀ Back", callback_data="settings")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ============================================================
# Integration helpers — for the file-storage/delivery system
# (currently under maintenance, so not part of this file). Once it's
# back, wire it up like this before/around sending a file:
#
#   1. Force Subscribe gate (skip if disabled):
#        unjoined = await payments.get_unjoined_channels(bot, user.id)
#        if unjoined:
#            await message.reply_text(text, reply_markup=build_fsub_join_keyboard(unjoined))
#            return
#
#   2. Token verification gate (skip for premium users):
#        if not await payments.is_premium(user.id) \
#           and await payments.is_token_verification_enabled() \
#           and not await payments.is_verified(user.id):
#                me = await context.bot.get_me()
#                long_link = build_verification_deep_link(me.username, user.id)
#                short_link = await payments.shorten_url(long_link)
#                await message.reply_text(f"Verify here: {short_link}")
#                return
#
#   3. Caption / thumbnail / button / protect content when sending:
#        caption = await build_final_caption(original_caption, filename=.., filesize=..)
#        thumb   = await payments.get_thumbnail_file_id() if await payments.is_thumbnail_enabled() else None
#        markup  = await build_extra_button_markup()
#        protect = await payments.is_protect_content_enabled()
#        sent = await bot.send_document(..., caption=caption, thumbnail=thumb,
#                                        reply_markup=markup, protect_content=protect)
#
#   4. Auto delete after sending:
#        await schedule_auto_delete(context.bot, sent.chat_id, sent.message_id)
# ============================================================

async def build_final_caption(default_caption: str, **placeholders) -> str:
    if await payments.is_caption_enabled():
        template = await payments.get_caption_template()
        if template:
            try:
                return template.format(**placeholders)
            except Exception:
                return template
    return default_caption


async def build_extra_button_markup() -> InlineKeyboardMarkup | None:
    if not await payments.is_custom_button_enabled():
        return None
    btn = await payments.get_custom_button()
    if btn.get("label") and btn.get("url"):
        return InlineKeyboardMarkup([[InlineKeyboardButton(btn["label"], url=btn["url"])]])
    return None


async def schedule_auto_delete(bot, chat_id: int, message_id: int) -> None:
    if not await payments.is_auto_delete_enabled():
        return
    seconds = await payments.get_auto_delete_seconds()

    async def _delete_later():
        await asyncio.sleep(seconds)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass

    asyncio.create_task(_delete_later())


SETTINGS_PLACEHOLDER_CALLBACKS = {
    "settings_permanent_link",
}


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data
    user = update.effective_user

    if data in ("help_settings", "help_about") or data in SETTINGS_PLACEHOLDER_CALLBACKS:
        # Just display for now — not wired up to real logic yet.
        await query.answer(text="Yeh feature jaldi aa raha hai 🚧", show_alert=True)
        return

    # ---------------- Link shortener panel (admin-only) ----------------
    if data == "settings_link_shortener":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await query.answer()
        shorteners = await payments.list_shorteners()
        await query.edit_message_text(
            text=build_shortener_menu_text(shorteners),
            parse_mode=ParseMode.HTML,
            reply_markup=build_shortener_menu_keyboard(shorteners),
        )
        return

    if data == "short_add":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await query.answer()
        context.user_data["awaiting"] = "shortener_add"
        await query.edit_message_text(
            text="➕ " + to_bold_unicode("Send: Name | api-domain.com | API_KEY")
            + "\n<code>GPLinks | api.gplinks.com | abcd1234</code>"
            + "\n\n" + to_bold_unicode("Send /cancel to cancel."),
            parse_mode=ParseMode.HTML,
        )
        return

    if data.startswith("short_toggle_"):
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await payments.toggle_shortener(data[len("short_toggle_"):])
        await query.answer()
        shorteners = await payments.list_shorteners()
        await query.edit_message_text(
            text=build_shortener_menu_text(shorteners),
            parse_mode=ParseMode.HTML,
            reply_markup=build_shortener_menu_keyboard(shorteners),
        )
        return

    if data.startswith("short_del_"):
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await payments.remove_shortener(data[len("short_del_"):])
        await query.answer("Removed.")
        shorteners = await payments.list_shorteners()
        await query.edit_message_text(
            text=build_shortener_menu_text(shorteners),
            parse_mode=ParseMode.HTML,
            reply_markup=build_shortener_menu_keyboard(shorteners),
        )
        return
    # --------------------------------------------------------------------

    # ---------------- Token verification panel ----------------
    if data == "settings_token_verification":
        await query.answer()
        enabled = await payments.is_token_verification_enabled()
        hours = await payments.get_verification_validity_hours()
        admin_view = is_admin(user.id)
        await query.edit_message_text(
            text=build_verification_menu_text(enabled, hours, admin_view),
            parse_mode=ParseMode.HTML,
            reply_markup=build_verification_menu_keyboard(enabled, admin_view),
        )
        return

    if data == "verify_toggle":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        currently_on = await payments.is_token_verification_enabled()
        await payments.set_token_verification_enabled(not currently_on)
        await query.answer("Token verification turned " + ("OFF ❌" if currently_on else "ON ✅"))
        hours = await payments.get_verification_validity_hours()
        await query.edit_message_text(
            text=build_verification_menu_text(not currently_on, hours, True),
            parse_mode=ParseMode.HTML,
            reply_markup=build_verification_menu_keyboard(not currently_on, True),
        )
        return

    if data == "verify_set_hours":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await query.answer()
        context.user_data["awaiting"] = "verification_hours"
        await query.edit_message_text(
            text="✏️ " + to_bold_unicode("Send validity in hours, e.g.")
            + "\n<code>24</code>\n\n" + to_bold_unicode("Send /cancel to cancel."),
            parse_mode=ParseMode.HTML,
        )
        return
    # --------------------------------------------------------------------

    # ---------------- Force subscribe panel ----------------
    if data == "settings_force_subscribe":
        await query.answer()
        enabled = await payments.is_force_sub_enabled()
        channels = await payments.list_force_sub_channels()
        admin_view = is_admin(user.id)
        await query.edit_message_text(
            text=build_fsub_menu_text(enabled, channels, admin_view),
            parse_mode=ParseMode.HTML,
            reply_markup=build_fsub_menu_keyboard(channels, admin_view),
        )
        return

    if data == "fsub_toggle":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        currently_on = await payments.is_force_sub_enabled()
        await payments.set_force_sub_enabled(not currently_on)
        await query.answer("Force subscribe turned " + ("OFF ❌" if currently_on else "ON ✅"))
        channels = await payments.list_force_sub_channels()
        await query.edit_message_text(
            text=build_fsub_menu_text(not currently_on, channels, True),
            parse_mode=ParseMode.HTML,
            reply_markup=build_fsub_menu_keyboard(channels, True),
        )
        return

    if data == "fsub_add":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await query.answer()
        context.user_data["awaiting"] = "fsub_add_channel"
        await query.edit_message_text(
            text="➕ " + to_bold_unicode("Public channel ho to bhejo:")
            + "\n<code>@channelusername</code>\n\n"
            + to_bold_unicode("Private ho to bhejo (id | invite link | title):")
            + "\n<code>-1001234567890 | https://t.me/+abc123 | My Channel</code>"
            + "\n\n" + to_bold_unicode("Send /cancel to cancel."),
            parse_mode=ParseMode.HTML,
        )
        return

    if data.startswith("fsub_del_"):
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await payments.remove_force_sub_channel(data[len("fsub_del_"):])
        await query.answer("Removed.")
        enabled = await payments.is_force_sub_enabled()
        channels = await payments.list_force_sub_channels()
        await query.edit_message_text(
            text=build_fsub_menu_text(enabled, channels, True),
            parse_mode=ParseMode.HTML,
            reply_markup=build_fsub_menu_keyboard(channels, True),
        )
        return

    if data == "fsub_recheck":
        unjoined = await payments.get_unjoined_channels(context.bot, user.id)
        if unjoined:
            await query.answer("Abhi bhi kuch channels baaki hain ❌", show_alert=True)
        else:
            await query.answer("✅ Sab channels joined! Ab file access kar sakte hain.", show_alert=True)
        return
    # --------------------------------------------------------------------

    # ---------------- Caption panel ----------------
    if data == "settings_caption":
        await query.answer()
        enabled = await payments.is_caption_enabled()
        template = await payments.get_caption_template()
        admin_view = is_admin(user.id)
        await query.edit_message_text(
            text=build_caption_menu_text(enabled, template, admin_view),
            parse_mode=ParseMode.HTML,
            reply_markup=build_caption_menu_keyboard(enabled, admin_view),
        )
        return

    if data == "caption_toggle":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        currently_on = await payments.is_caption_enabled()
        await payments.set_caption_enabled(not currently_on)
        await query.answer("Caption turned " + ("OFF ❌" if currently_on else "ON ✅"))
        template = await payments.get_caption_template()
        await query.edit_message_text(
            text=build_caption_menu_text(not currently_on, template, True),
            parse_mode=ParseMode.HTML,
            reply_markup=build_caption_menu_keyboard(not currently_on, True),
        )
        return

    if data == "caption_set":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await query.answer()
        context.user_data["awaiting"] = "caption_template"
        await query.edit_message_text(
            text="✏️ " + to_bold_unicode("Send the new caption. Placeholders:")
            + " <code>{filename}</code> <code>{filesize}</code> <code>{caption}</code>"
            + "\n\n" + to_bold_unicode("Send /cancel to cancel."),
            parse_mode=ParseMode.HTML,
        )
        return
    # --------------------------------------------------------------------

    # ---------------- Thumbnail panel ----------------
    if data == "settings_thumbnail":
        await query.answer()
        enabled = await payments.is_thumbnail_enabled()
        has_thumb = bool(await payments.get_thumbnail_file_id())
        admin_view = is_admin(user.id)
        await query.edit_message_text(
            text=build_thumbnail_menu_text(enabled, has_thumb, admin_view),
            parse_mode=ParseMode.HTML,
            reply_markup=build_thumbnail_menu_keyboard(enabled, admin_view),
        )
        return

    if data == "thumb_toggle":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        currently_on = await payments.is_thumbnail_enabled()
        await payments.set_thumbnail_enabled(not currently_on)
        await query.answer("Thumbnail turned " + ("OFF ❌" if currently_on else "ON ✅"))
        has_thumb = bool(await payments.get_thumbnail_file_id())
        await query.edit_message_text(
            text=build_thumbnail_menu_text(not currently_on, has_thumb, True),
            parse_mode=ParseMode.HTML,
            reply_markup=build_thumbnail_menu_keyboard(not currently_on, True),
        )
        return

    if data == "thumb_set":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await query.answer()
        context.user_data["awaiting"] = "thumbnail_photo"
        await query.edit_message_text(
            text="🖼 " + to_bold_unicode("Send the new thumbnail (as a photo).")
            + "\n\n" + to_bold_unicode("Send /cancel to cancel."),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "thumb_remove":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await payments.set_thumbnail_file_id(None)
        await query.answer("Thumbnail removed.")
        enabled = await payments.is_thumbnail_enabled()
        await query.edit_message_text(
            text=build_thumbnail_menu_text(enabled, False, True),
            parse_mode=ParseMode.HTML,
            reply_markup=build_thumbnail_menu_keyboard(enabled, True),
        )
        return
    # --------------------------------------------------------------------

    # ---------------- Button panel ----------------
    if data == "settings_button":
        await query.answer()
        enabled = await payments.is_custom_button_enabled()
        btn = await payments.get_custom_button()
        admin_view = is_admin(user.id)
        await query.edit_message_text(
            text=build_button_menu_text(enabled, btn, admin_view),
            parse_mode=ParseMode.HTML,
            reply_markup=build_button_menu_keyboard(enabled, admin_view),
        )
        return

    if data == "btn_toggle":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        currently_on = await payments.is_custom_button_enabled()
        await payments.set_custom_button_enabled(not currently_on)
        await query.answer("Button turned " + ("OFF ❌" if currently_on else "ON ✅"))
        btn = await payments.get_custom_button()
        await query.edit_message_text(
            text=build_button_menu_text(not currently_on, btn, True),
            parse_mode=ParseMode.HTML,
            reply_markup=build_button_menu_keyboard(not currently_on, True),
        )
        return

    if data == "btn_set":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await query.answer()
        context.user_data["awaiting"] = "button_set"
        await query.edit_message_text(
            text="✏️ " + to_bold_unicode("Send the button as: Label - https://example.com")
            + "\n\n" + to_bold_unicode("Send /cancel to cancel."),
            parse_mode=ParseMode.HTML,
        )
        return
    # --------------------------------------------------------------------

    # ---------------- Auto delete panel ----------------
    if data == "settings_auto_delete":
        await query.answer()
        enabled = await payments.is_auto_delete_enabled()
        seconds = await payments.get_auto_delete_seconds()
        admin_view = is_admin(user.id)
        await query.edit_message_text(
            text=build_auto_delete_menu_text(enabled, seconds, admin_view),
            parse_mode=ParseMode.HTML,
            reply_markup=build_auto_delete_menu_keyboard(enabled, admin_view),
        )
        return

    if data == "ad_toggle":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        currently_on = await payments.is_auto_delete_enabled()
        await payments.set_auto_delete_enabled(not currently_on)
        await query.answer("Auto delete turned " + ("OFF ❌" if currently_on else "ON ✅"))
        seconds = await payments.get_auto_delete_seconds()
        await query.edit_message_text(
            text=build_auto_delete_menu_text(not currently_on, seconds, True),
            parse_mode=ParseMode.HTML,
            reply_markup=build_auto_delete_menu_keyboard(not currently_on, True),
        )
        return

    if data == "ad_set":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await query.answer()
        context.user_data["awaiting"] = "auto_delete_seconds"
        await query.edit_message_text(
            text="✏️ " + to_bold_unicode("Send delay in seconds, e.g.")
            + "\n<code>600</code>\n\n" + to_bold_unicode("Send /cancel to cancel."),
            parse_mode=ParseMode.HTML,
        )
        return
    # --------------------------------------------------------------------

    # ---------------- Protect content panel ----------------
    if data == "settings_protect_content":
        await query.answer()
        enabled = await payments.is_protect_content_enabled()
        admin_view = is_admin(user.id)
        await query.edit_message_text(
            text=build_protect_content_menu_text(enabled, admin_view),
            parse_mode=ParseMode.HTML,
            reply_markup=build_protect_content_menu_keyboard(enabled, admin_view),
        )
        return

    if data == "pc_toggle":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        currently_on = await payments.is_protect_content_enabled()
        await payments.set_protect_content_enabled(not currently_on)
        await query.answer("Protect content turned " + ("OFF ❌" if currently_on else "ON ✅"))
        await query.edit_message_text(
            text=build_protect_content_menu_text(not currently_on, True),
            parse_mode=ParseMode.HTML,
            reply_markup=build_protect_content_menu_keyboard(not currently_on, True),
        )
        return
    # --------------------------------------------------------------------

    # ---------------- Buy / extend premium plan ----------------
    if data.startswith("buyplan_"):
        plan_id = data[len("buyplan_"):]
        offer = await build_buy_offer(user, update.effective_chat.id, plan_id)
        if offer is None:
            await query.answer()
            await query.edit_message_text(text="⚠️ Payment gateway isn't configured yet.")
            return
        if offer == "disabled":
            await query.answer()
            await query.edit_message_text(
                text="🚫 Premium plan purchases are currently unavailable. "
                     "Please check back later."
            )
            return
        if offer == "invalid_plan":
            await query.answer("⚠️ Invalid plan.", show_alert=True)
            return
        await query.answer()
        if offer["photo_file_id"]:
            # Editing text -> photo isn't possible, so send a fresh
            # message when a custom picture is configured.
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=offer["photo_file_id"],
                caption=offer["text"],
                parse_mode=ParseMode.HTML,
                reply_markup=offer["keyboard"],
            )
        else:
            await query.edit_message_text(
                text=offer["text"],
                parse_mode=ParseMode.HTML,
                reply_markup=offer["keyboard"],
            )
        return
    # --------------------------------------------------------------------

    # ---------------- Premium admin panel (admin-only) ----------------
    if data == "premium_users_list":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        premium_map = await payments.list_premium()
        if not premium_map:
            await query.answer("No premium users found.", show_alert=True)
            return
        await query.answer()
        now = datetime.now(timezone.utc)
        lines = []
        for uid, expiry_iso in premium_map.items():
            try:
                exp = datetime.fromisoformat(expiry_iso)
                status = "✅ active" if exp > now else "❌ expired"
                lines.append(f"• <code>{uid}</code> — {exp.strftime('%d %b %Y')} ({status})")
            except Exception:
                lines.append(f"• <code>{uid}</code> — invalid expiry")
        text = "🚦 <u>" + to_bold_unicode("Premium users:") + "</u>\n\n" + "\n".join(lines)
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("◀ Back", callback_data="settings_premium")]])
        await query.edit_message_text(text=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        return

    if data == "premium_toggle":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        currently_on = await payments.is_premium_enabled()
        await payments.set_premium_enabled(not currently_on)
        await query.answer("Premium turned " + ("OFF ❌" if currently_on else "ON ✅"))
        await query.edit_message_text(
            text=build_premium_menu_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=build_premium_menu_keyboard(not currently_on),
        )
        return

    if data == "premium_add_user":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await query.answer()
        context.user_data["awaiting"] = "add_premium_user"
        await query.edit_message_text(
            text="➕ " + to_bold_unicode("Send the user ID and number of days, e.g.")
            + "\n<code>123456789 30</code>\n\n"
            + to_bold_unicode("Send /cancel to cancel."),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "premium_remove_user":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await query.answer()
        context.user_data["awaiting"] = "remove_premium_user"
        await query.edit_message_text(
            text="➖ " + to_bold_unicode("Send the user ID to remove from premium.")
            + "\n\n" + to_bold_unicode("Send /cancel to cancel."),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "premium_plan_message":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await query.answer()
        await query.edit_message_text(
            text=build_premium_message_menu_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=build_premium_message_menu_keyboard(),
        )
        return

    if data == "premium_message_text":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await query.answer()
        context.user_data["awaiting"] = "premium_message_text"
        await query.edit_message_text(
            text="📝 " + to_bold_unicode("Send the new premium plan message text.")
            + "\n\n" + to_bold_unicode("Send /cancel to cancel."),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "premium_message_picture":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await query.answer()
        context.user_data["awaiting"] = "premium_message_picture"
        await query.edit_message_text(
            text="🖼 " + to_bold_unicode("Send the new premium plan picture (as a photo).")
            + "\n\n" + to_bold_unicode("Send /cancel to cancel."),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "premium_message_button":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await query.answer()
        context.user_data["awaiting"] = "premium_message_button"
        await query.edit_message_text(
            text="⚪ " + to_bold_unicode("Send the button as: Label - https://example.com")
            + "\n\n" + to_bold_unicode("Send /cancel to cancel."),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "premium_message_back":
        await query.answer()
        enabled = await payments.is_premium_enabled()
        await query.edit_message_text(
            text=build_premium_menu_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=build_premium_menu_keyboard(enabled),
        )
        return
    # --------------------------------------------------------------------

    # ---------------- Free usage limit panel (admin-only) ----------------
    if data == "free_limit_set":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await query.answer()
        context.user_data["awaiting"] = "free_limit_count"
        await query.edit_message_text(
            text="✏️ " + to_bold_unicode("Send the number of free uses allowed per day, e.g.")
            + "\n<code>5</code>\n\n"
            + to_bold_unicode("Send /cancel to cancel."),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "free_limit_toggle":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        currently_on = await payments.is_free_limit_enabled()
        await payments.set_free_limit_enabled(not currently_on)
        await query.answer("Free usage limit turned " + ("OFF ❌" if currently_on else "ON ✅"))
        count = await payments.get_free_limit_count()
        await query.edit_message_text(
            text=build_free_limit_text(not currently_on, count),
            parse_mode=ParseMode.HTML,
            reply_markup=build_free_limit_keyboard(not currently_on),
        )
        return

    if data == "free_limit_back":
        await query.answer()
        await query.edit_message_text(
            text=build_settings_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=build_settings_keyboard(is_admin(user.id)),
        )
        return
    # --------------------------------------------------------------------

    await query.answer()

    if data == "help":
        await query.edit_message_text(
            text=build_help_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=build_help_keyboard(),
        )
    elif data == "about":
        await query.edit_message_text(
            text=to_bold_unicode("About") + "\n\n"
                 "I'm Haven 🌺 — a permanent file store bot.",
        )
    elif data == "settings":
        await query.edit_message_text(
            text=build_settings_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=build_settings_keyboard(is_admin(user.id)),
        )
    elif data == "settings_premium":
        if is_admin(user.id):
            enabled = await payments.is_premium_enabled()
            await query.edit_message_text(
                text=build_premium_menu_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=build_premium_menu_keyboard(enabled),
            )
            return
        if not (payments.PAYU_KEY and payments.PAYU_SALT and payments.BASE_URL):
            await query.edit_message_text(text="⚠️ Payment gateway isn't configured yet.")
            return
        if not await payments.is_premium_enabled():
            await query.edit_message_text(
                text="🚫 Premium plan purchases are currently unavailable. "
                     "Please check back later."
            )
            return
        already_premium = await payments.is_premium(user.id)
        expiry_iso = await payments.get_premium_expiry(user.id) if already_premium else None
        await query.edit_message_text(
            text=build_plan_choice_text(already_premium, expiry_iso),
            parse_mode=ParseMode.HTML,
            reply_markup=build_plan_choice_keyboard(),
        )
    elif data == "premium_back":
        await query.edit_message_text(
            text=build_settings_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=build_settings_keyboard(is_admin(user.id)),
        )
    elif data == "settings_free_limit":
        enabled = await payments.is_free_limit_enabled()
        count = await payments.get_free_limit_count()
        if is_admin(user.id):
            await query.edit_message_text(
                text=build_free_limit_text(enabled, count),
                parse_mode=ParseMode.HTML,
                reply_markup=build_free_limit_keyboard(enabled),
            )
        else:
            info = build_free_limit_text(enabled, count)
            await query.edit_message_text(
                text=info,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("◀ Back", callback_data="settings")]]
                ),
            )
    elif data == "settings_refer":
        me = await context.bot.get_me()
        stats = await payments.get_referral_stats(user.id)
        await query.edit_message_text(
            text=build_refer_text(me.username, user.id, stats),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("◀ Back", callback_data="settings")]]
            ),
        )
    elif data == "settings_back":
        await query.edit_message_text(
            text=build_start_text(user.first_name),
            parse_mode=ParseMode.HTML,
            reply_markup=build_start_keyboard(),
        )
    elif data == "help_back":
        await query.edit_message_text(
            text=build_start_text(user.first_name),
            parse_mode=ParseMode.HTML,
            reply_markup=build_start_keyboard(),
        )


def build_plan_choice_text(already_premium: bool, expiry_iso: str | None) -> str:
    header = "💎 <u>" + to_bold_unicode("Premium Plan:") + "</u>"
    if already_premium and expiry_iso:
        expiry = datetime.fromisoformat(expiry_iso)
        notice = (
            "<blockquote>"
            + "✅ " + to_bold_unicode("Aap already Premium user hain!") + "\n"
            + "⏳ " + to_bold_unicode(f"Expires on: {expiry.strftime('%d %b %Y, %H:%M UTC')}") + "\n"
            + "🔄 " + to_bold_unicode("Chahen to niche se plan lekar Extend kar sakte hain — naya time abhi wali expiry ke upar jud jayega.")
            + "</blockquote>"
        )
    else:
        notice = (
            "<blockquote>"
            + to_bold_unicode(
                "Premium Plan: ad-free access, faster downloads, and "
                "exclusive entry to restricted files or groups."
            )
            + "</blockquote>"
        )
    return f"{header}\n\n{notice}"


def build_plan_choice_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(f"💳 {p['label']} - ₹{p['amount']}", callback_data=f"buyplan_{p['id']}")]
        for p in payments.PLANS
    ]
    keyboard.append([InlineKeyboardButton("◀ Back", callback_data="settings")])
    return InlineKeyboardMarkup(keyboard)


async def build_buy_offer(user, chat_id, plan_id: str):
    """Creates a pending PayU transaction for the chosen plan and returns
    a dict describing the buy offer message: {"text", "keyboard",
    "photo_file_id"}. Returns None if PayU isn't configured, "disabled"
    if an admin has turned premium purchases off, or "invalid_plan" if
    plan_id doesn't match any configured plan."""
    if not (payments.PAYU_KEY and payments.PAYU_SALT and payments.BASE_URL):
        return None

    if not await payments.is_premium_enabled():
        return "disabled"

    plan = payments.get_plan(plan_id)
    if plan is None:
        return "invalid_plan"

    txnid = uuid.uuid4().hex[:20]
    txn = {
        "user_id": user.id,
        "chat_id": chat_id,
        "amount": plan["amount"],
        "plan_days": plan["days"],
        "plan_label": f"{payments.PLAN_LABEL} - {plan['label']}",
        "firstname": user.first_name or "User",
        "email": f"user{user.id}@telegram.local",
        "phone": "9999999999",
        "status": "pending",
    }
    await payments.save_transaction(txnid, txn)

    pay_url = f"{payments.BASE_URL}/payu/pay/{txnid}"
    buttons = [[InlineKeyboardButton(f"💳 Pay ₹{plan['amount']} now", url=pay_url)]]

    custom = await payments.get_premium_message()
    if custom.get("button_text") and custom.get("button_url"):
        buttons.append(
            [InlineKeyboardButton(custom["button_text"], url=custom["button_url"])]
        )
    buttons.append([InlineKeyboardButton("◀ Back", callback_data="settings_premium")])
    keyboard = InlineKeyboardMarkup(buttons)

    if custom.get("text"):
        text = custom["text"]
    else:
        text = (
            "💎 " + to_bold_unicode(f"{plan['label']} - ₹{plan['amount']}")
            + "\n\n"
            + "<blockquote>"
            + "⏳ " + to_bold_unicode(f"Valid for {plan['days']} day(s).") + "\n"
            + "💳 " + to_bold_unicode(
                "Pay via UPI, UPI QR, cards, netbanking or wallet — all shown "
                "on the payment page."
            )
            + "</blockquote>\n\n"
            + to_bold_unicode("Tap the button below to pay.")
        )

    return {
        "text": text,
        "keyboard": keyboard,
        "photo_file_id": custom.get("photo_file_id"),
    }


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user

    if not (payments.PAYU_KEY and payments.PAYU_SALT and payments.BASE_URL):
        await update.message.reply_text(
            "⚠️ Payment gateway isn't configured yet. Set PAYU_KEY, "
            "PAYU_SALT and BASE_URL environment variables first."
        )
        return
    if not await payments.is_premium_enabled():
        await update.message.reply_text(
            "🚫 Premium plan purchases are currently unavailable. Please "
            "check back later."
        )
        return

    already_premium = await payments.is_premium(user.id)
    expiry_iso = await payments.get_premium_expiry(user.id) if already_premium else None
    await update.message.reply_text(
        text=build_plan_choice_text(already_premium, expiry_iso),
        parse_mode=ParseMode.HTML,
        reply_markup=build_plan_choice_keyboard(),
    )


async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    text = (
        "🆔 " + to_bold_unicode(f"Your Telegram ID: {user.id}") + "\n"
        + "💬 " + to_bold_unicode(f"This chat's ID: {chat.id}")
    )
    await update.message.reply_text(text)


async def myplan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    expiry_iso = await payments.get_premium_expiry(user.id)
    if expiry_iso and await payments.is_premium(user.id):
        expiry = datetime.fromisoformat(expiry_iso)
        text = (
            "💎 " + to_bold_unicode("You have an active premium plan.") + "\n"
            + "⏳ " + to_bold_unicode(f"Expires on: {expiry.strftime('%d %b %Y, %H:%M UTC')}") + "\n"
            + "🔄 " + to_bold_unicode("Use /buy to extend it further.")
        )
    else:
        text = (
            "🚫 " + to_bold_unicode("You don't have an active premium plan.") + "\n"
            + "💳 " + to_bold_unicode("Use /buy to purchase one.")
        )
    await update.message.reply_text(text)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.pop("awaiting", None) is not None:
        await update.message.reply_text("Process Cancelled by User ❌")
    else:
        await update.message.reply_text("Nothing to cancel.")


async def admin_text_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Captures the next text message after an admin taps Add/Remove
    Premium User or one of the Premium Plan Message buttons."""
    awaiting = context.user_data.get("awaiting")
    if not awaiting:
        return
    user = update.effective_user
    if not is_admin(user.id):
        return

    if awaiting == "add_premium_user":
        parts = (update.message.text or "").split()
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            await update.message.reply_text(
                "⚠️ Invalid format. Send: <user_id> <days>  (or /cancel)"
            )
            return
        target_id, days = int(parts[0]), int(parts[1])
        expiry = datetime.now(timezone.utc) + timedelta(days=days)
        await payments.set_premium(target_id, expiry.isoformat())
        context.user_data.pop("awaiting", None)
        await update.message.reply_text(
            "✅ " + to_bold_unicode(
                f"User {target_id} is now premium until {expiry.strftime('%d %b %Y')}."
            )
        )
        return

    if awaiting == "remove_premium_user":
        raw = (update.message.text or "").strip()
        if not raw.isdigit():
            await update.message.reply_text(
                "⚠️ Invalid user ID. Send a numeric ID (or /cancel)."
            )
            return
        target_id = int(raw)
        removed = await payments.remove_premium(target_id)
        context.user_data.pop("awaiting", None)
        if removed:
            await update.message.reply_text(
                "✅ " + to_bold_unicode(f"User {target_id} removed from premium.")
            )
        else:
            await update.message.reply_text(
                "⚠️ " + to_bold_unicode(f"User {target_id} was not a premium user.")
            )
        return

    if awaiting == "free_limit_count":
        raw = (update.message.text or "").strip()
        if not raw.isdigit():
            await update.message.reply_text(
                "⚠️ Invalid number. Send a whole number like 5 (or /cancel)."
            )
            return
        count = int(raw)
        await payments.set_free_limit_count(count)
        context.user_data.pop("awaiting", None)
        await update.message.reply_text(
            "✅ " + to_bold_unicode(f"Free usage limit set to {count} per day.")
        )
        return

    if awaiting == "premium_message_text":
        await payments.set_premium_message(text=update.message.text)
        context.user_data.pop("awaiting", None)
        await update.message.reply_text("✅ Premium plan message text updated.")
        return

    if awaiting == "premium_message_button":
        raw = update.message.text or ""
        if " - " not in raw:
            await update.message.reply_text(
                "⚠️ Format: Label - https://example.com  (or /cancel)"
            )
            return
        label, url = raw.split(" - ", 1)
        label, url = label.strip(), url.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            await update.message.reply_text(
                "⚠️ The URL must start with http:// or https:// (or /cancel)."
            )
            return
        await payments.set_premium_message(button_text=label, button_url=url)
        context.user_data.pop("awaiting", None)
        await update.message.reply_text("✅ Premium plan button updated.")
        return

    if awaiting == "shortener_add":
        raw = update.message.text or ""
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) != 3 or not all(parts):
            await update.message.reply_text(
                "⚠️ Format: Name | api-domain.com | API_KEY  (or /cancel)"
            )
            return
        name, domain, api_key = parts
        await payments.add_shortener(name, domain, api_key)
        context.user_data.pop("awaiting", None)
        await update.message.reply_text("✅ " + to_bold_unicode(f"Shortener '{name}' added."))
        return

    if awaiting == "verification_hours":
        raw = (update.message.text or "").strip()
        if not raw.isdigit() or int(raw) <= 0:
            await update.message.reply_text(
                "⚠️ Invalid number. Send a whole number like 24 (or /cancel)."
            )
            return
        hours = int(raw)
        await payments.set_verification_validity_hours(hours)
        context.user_data.pop("awaiting", None)
        await update.message.reply_text(
            "✅ " + to_bold_unicode(f"Verification validity set to {hours} hour(s).")
        )
        return

    if awaiting == "fsub_add_channel":
        raw = (update.message.text or "").strip()
        if raw.startswith("@") and "|" not in raw:
            try:
                chat = await context.bot.get_chat(raw)
            except Exception:
                await update.message.reply_text(
                    "⚠️ Channel nahi mila. Check username ya bot ko admin banao (or /cancel)."
                )
                return
            await payments.add_force_sub_channel(
                chat_id=chat.id,
                title=chat.title or raw,
                invite_link=f"https://t.me/{raw.lstrip('@')}",
                username=raw,
            )
        else:
            parts = [p.strip() for p in raw.split("|")]
            if len(parts) != 3 or not parts[0].lstrip("-").isdigit():
                await update.message.reply_text(
                    "⚠️ Format: chat_id | invite_link | title  (or /cancel)"
                )
                return
            chat_id_raw, invite_link, title = parts
            if not (invite_link.startswith("http://") or invite_link.startswith("https://")):
                await update.message.reply_text(
                    "⚠️ Invite link http:// ya https:// se start honi chahiye (or /cancel)."
                )
                return
            await payments.add_force_sub_channel(
                chat_id=int(chat_id_raw), title=title, invite_link=invite_link,
            )
        context.user_data.pop("awaiting", None)
        await update.message.reply_text("✅ " + to_bold_unicode("Channel added to force subscribe."))
        return

    if awaiting == "caption_template":
        await payments.set_caption_template(update.message.text or "")
        context.user_data.pop("awaiting", None)
        await update.message.reply_text("✅ Caption updated.")
        return

    if awaiting == "button_set":
        raw = update.message.text or ""
        if " - " not in raw:
            await update.message.reply_text(
                "⚠️ Format: Label - https://example.com  (or /cancel)"
            )
            return
        label, url = raw.split(" - ", 1)
        label, url = label.strip(), url.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            await update.message.reply_text(
                "⚠️ The URL must start with http:// or https:// (or /cancel)."
            )
            return
        await payments.set_custom_button(label, url)
        context.user_data.pop("awaiting", None)
        await update.message.reply_text("✅ Custom button updated.")
        return

    if awaiting == "auto_delete_seconds":
        raw = (update.message.text or "").strip()
        if not raw.isdigit() or int(raw) <= 0:
            await update.message.reply_text(
                "⚠️ Invalid number. Send a whole number like 600 (or /cancel)."
            )
            return
        seconds = int(raw)
        await payments.set_auto_delete_seconds(seconds)
        context.user_data.pop("awaiting", None)
        await update.message.reply_text(
            "✅ " + to_bold_unicode(f"Auto delete delay set to {seconds} second(s).")
        )
        return


async def admin_photo_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Captures the next photo after an admin taps Premium Plan Picture or
    Set Thumbnail."""
    awaiting = context.user_data.get("awaiting")
    if awaiting not in ("premium_message_picture", "thumbnail_photo"):
        return
    if not is_admin(update.effective_user.id):
        return
    file_id = update.message.photo[-1].file_id

    if awaiting == "premium_message_picture":
        await payments.set_premium_message(photo_file_id=file_id)
        context.user_data.pop("awaiting", None)
        await update.message.reply_text("✅ Premium plan picture updated.")
        return

    if awaiting == "thumbnail_photo":
        await payments.set_thumbnail_file_id(file_id)
        context.user_data.pop("awaiting", None)
        await update.message.reply_text("✅ Thumbnail updated.")
        return


async def main() -> None:
    if BOT_TOKEN == "PUT-YOUR-BOT-TOKEN-HERE":
        raise SystemExit(
            "Set your bot token first: export BOT_TOKEN='123456:ABC-DEF...'"
        )

    # MongoDB must be reachable before anything else starts.
    await payments.init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("buy", buy))
    application.add_handler(CommandHandler("id", id_cmd))
    application.add_handler(CommandHandler("myplan", myplan))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.PHOTO, admin_photo_reply_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text_reply_handler))

    # --- Telegram bot: start polling (manual lifecycle, not the blocking
    # run_polling() helper, so it can run alongside the aiohttp server in
    # the same asyncio loop) ---
    await application.initialize()
    await application.start()
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    me = await application.bot.get_me()
    logger.info("Bot @%s started (polling)", me.username)

    # --- PayU webhook / checkout HTTP server ---
    web_app = payments.build_web_app(application.bot)
    web_app["bot_username"] = me.username
    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.environ.get("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("PayU webhook server listening on port %s", port)

    try:
        await asyncio.Event().wait()  # run forever
    finally:
        await runner.cleanup()
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        await payments.close_db()


if __name__ == "__main__":
    asyncio.run(main())
