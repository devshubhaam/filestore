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


def build_settings_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("💎 Premium plan", callback_data="settings_premium")],
        [InlineKeyboardButton("🆓 Free usage limit", callback_data="settings_free_limit")],
        [InlineKeyboardButton("🌍 Refer and earn", callback_data="settings_refer")],
        [InlineKeyboardButton("🔗 Link shortner", callback_data="settings_link_shortener")],
        [InlineKeyboardButton("⏰ Token verification", callback_data="settings_token_verification")],
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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user

    if context.args:
        payload = context.args[0]
        if payload.startswith("ref_"):
            ref_part = payload[len("ref_"):]
            if ref_part.isdigit():
                referrer_id = int(ref_part)
                await payments.record_referral(referred_id=user.id, referrer_id=referrer_id)

    await update.message.reply_text(
        text=build_start_text(user.first_name),
        parse_mode=ParseMode.HTML,
        reply_markup=build_start_keyboard(),
    )


SETTINGS_PLACEHOLDER_CALLBACKS = {
    "settings_free_limit",
    "settings_link_shortener",
    "settings_token_verification",
    "settings_force_subscribe",
    "settings_caption",
    "settings_thumbnail",
    "settings_button",
    "settings_auto_delete",
    "settings_permanent_link",
    "settings_protect_content",
}


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data
    user = update.effective_user

    if data in ("help_settings", "help_about") or data in SETTINGS_PLACEHOLDER_CALLBACKS:
        # Just display for now — not wired up to real logic yet.
        await query.answer(text="Yeh feature jaldi aa raha hai 🚧", show_alert=True)
        return

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
            reply_markup=build_settings_keyboard(),
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
        offer = await build_buy_offer(user, update.effective_chat.id)
        if offer is None:
            await query.edit_message_text(text="⚠️ Payment gateway isn't configured yet.")
            return
        if offer == "disabled":
            await query.edit_message_text(
                text="🚫 Premium plan purchases are currently unavailable. "
                     "Please check back later."
            )
            return
        # Editing a text message can't turn it into a photo message, so a
        # custom picture (if set) is shown for /buy but not from here.
        await query.edit_message_text(
            text=offer["text"],
            parse_mode=ParseMode.HTML,
            reply_markup=offer["keyboard"],
        )
    elif data == "premium_back":
        await query.edit_message_text(
            text=build_settings_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=build_settings_keyboard(),
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


async def build_buy_offer(user, chat_id):
    """Creates a pending PayU transaction and returns a dict describing
    the buy offer message: {"text", "keyboard", "photo_file_id"}.
    Returns None if PayU isn't configured, or the string "disabled" if
    an admin has turned premium purchases off."""
    if not (payments.PAYU_KEY and payments.PAYU_SALT and payments.BASE_URL):
        return None

    if not await payments.is_premium_enabled():
        return "disabled"

    txnid = uuid.uuid4().hex[:20]
    txn = {
        "user_id": user.id,
        "chat_id": chat_id,
        "amount": payments.PLAN_AMOUNT,
        "plan_days": payments.PLAN_DAYS,
        "plan_label": payments.PLAN_LABEL,
        "firstname": user.first_name or "User",
        "email": f"user{user.id}@telegram.local",
        "phone": "9999999999",
        "status": "pending",
    }
    await payments.save_transaction(txnid, txn)

    pay_url = f"{payments.BASE_URL}/payu/pay/{txnid}"
    buttons = [[InlineKeyboardButton(f"💳 Pay ₹{payments.PLAN_AMOUNT} now", url=pay_url)]]

    custom = await payments.get_premium_message()
    if custom.get("button_text") and custom.get("button_url"):
        buttons.append(
            [InlineKeyboardButton(custom["button_text"], url=custom["button_url"])]
        )
    keyboard = InlineKeyboardMarkup(buttons)

    if custom.get("text"):
        text = custom["text"]
    else:
        text = (
            "💎 " + to_bold_unicode(f"{payments.PLAN_LABEL} - ₹{payments.PLAN_AMOUNT}")
            + "\n\n"
            + "<blockquote>"
            + "⏳ " + to_bold_unicode(f"Valid for {payments.PLAN_DAYS} days.") + "\n"
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
    chat_id = update.effective_chat.id

    offer = await build_buy_offer(user, chat_id)
    if offer is None:
        await update.message.reply_text(
            "⚠️ Payment gateway isn't configured yet. Set PAYU_KEY, "
            "PAYU_SALT and BASE_URL environment variables first."
        )
        return
    if offer == "disabled":
        await update.message.reply_text(
            "🚫 Premium plan purchases are currently unavailable. Please "
            "check back later."
        )
        return

    if offer["photo_file_id"]:
        await update.message.reply_photo(
            photo=offer["photo_file_id"],
            caption=offer["text"],
            parse_mode=ParseMode.HTML,
            reply_markup=offer["keyboard"],
        )
    else:
        await update.message.reply_text(
            text=offer["text"],
            parse_mode=ParseMode.HTML,
            reply_markup=offer["keyboard"],
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
            + "⏳ " + to_bold_unicode(f"Expires on: {expiry.strftime('%d %b %Y, %H:%M UTC')}")
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


async def admin_photo_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Captures the next photo after an admin taps Premium Plan Picture."""
    if context.user_data.get("awaiting") != "premium_message_picture":
        return
    if not is_admin(update.effective_user.id):
        return
    file_id = update.message.photo[-1].file_id
    await payments.set_premium_message(photo_file_id=file_id)
    context.user_data.pop("awaiting", None)
    await update.message.reply_text("✅ Premium plan picture updated.")


async def main() -> None:
    if BOT_TOKEN == "PUT-YOUR-BOT-TOKEN-HERE":
        raise SystemExit(
            "Set your bot token first: export BOT_TOKEN='123456:ABC-DEF...'"
        )

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


if __name__ == "__main__":
    asyncio.run(main())
