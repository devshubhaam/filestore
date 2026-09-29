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

import db
import premium
import credits
import referral
import shorteners as shortener_store
import token_verification
import force_subscribe
import file_settings
import famgateway
import file_store

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
        ("credits", "View and earn credits."),
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
        [InlineKeyboardButton("🪙 Credits", callback_data="credits_menu")],
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


# ============================================================
# Credits
# ============================================================

def build_credits_text(balance: int, cost_per_file: int, daily_enabled: bool,
                        daily_amount: int) -> str:
    header = "🪙 <u>" + to_bold_unicode("Credits:") + "</u>"
    desc = (
        "<blockquote>"
        + to_bold_unicode(
            f"1 file access = {cost_per_file} credit(s). Premium users "
            "never spend credits."
        )
        + "</blockquote>"
    )
    balance_line = "💰 " + to_bold_unicode(f"Your balance: {balance} credits")
    daily_line = (
        "🎁 " + to_bold_unicode(f"Daily login: +{daily_amount} credit(s)")
        if daily_enabled else
        "🎁 " + to_bold_unicode("Daily login: currently off")
    )
    return "\n\n".join([header, desc, balance_line, daily_line])


def build_credits_keyboard(daily_enabled: bool, is_admin_user: bool = False) -> InlineKeyboardMarkup:
    keyboard = []
    if daily_enabled:
        keyboard.append([InlineKeyboardButton("🎁 Claim daily credit", callback_data="credits_daily")])
    keyboard += [
        [InlineKeyboardButton("🔗 Earn via shortener", callback_data="credits_earn")],
        [InlineKeyboardButton("🌍 Refer and earn", callback_data="settings_refer")],
        [InlineKeyboardButton("💳 Buy credits", callback_data="credits_buy")],
    ]
    if is_admin_user:
        keyboard.append([InlineKeyboardButton("⚙️ Admin: credit settings", callback_data="credits_admin")])
    keyboard.append([InlineKeyboardButton("◀ Back", callback_data="settings")])
    return InlineKeyboardMarkup(keyboard)


def build_credits_earn_text(shorteners: list[dict]) -> str:
    header = "🔗 <u>" + to_bold_unicode("Earn credits:") + "</u>"
    if not shorteners:
        body = "<blockquote>" + to_bold_unicode("Abhi koi shortener available nahi hai.") + "</blockquote>"
    else:
        body = "<blockquote>" + to_bold_unicode(
            "Neeche diye gaye kisi bhi link ko complete karo — har link "
            "sirf ek baar credit deta hai."
        ) + "</blockquote>"
    return f"{header}\n\n{body}"


def build_credits_earn_keyboard(shorteners: list[dict]) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(
            f"🎯 {s['name']} — +{s.get('reward_credits', 5)} credits",
            callback_data=f"credits_earn_{s['id']}",
        )]
        for s in shorteners if s.get("enabled")
    ]
    keyboard.append([InlineKeyboardButton("◀ Back", callback_data="credits_menu")])
    return InlineKeyboardMarkup(keyboard)


def build_credits_buy_text() -> str:
    header = "💳 <u>" + to_bold_unicode("Buy credits:") + "</u>"
    body = "<blockquote>" + to_bold_unicode(
        "Bigger packs cost less per credit."
    ) + "</blockquote>"
    return f"{header}\n\n{body}"


def build_credits_buy_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(
            f"🪙 {p['credits']} credits - ₹{p['amount']}", callback_data=f"creditpack_{p['id']}"
        )]
        for p in credits.CREDIT_PACKS
    ]
    keyboard.append([InlineKeyboardButton("◀ Back", callback_data="credits_menu")])
    return InlineKeyboardMarkup(keyboard)


def build_credits_admin_text(cost_per_file: int, daily_enabled: bool, daily_amount: int,
                              referral_reward: int) -> str:
    header = "⚙️ <u>" + to_bold_unicode("Credit settings (admin):") + "</u>"
    lines = [
        to_bold_unicode(f"Cost per file: {cost_per_file} credit(s)"),
        to_bold_unicode(f"Daily login: {'ON ✅' if daily_enabled else 'OFF ❌'} — {daily_amount} credit(s)"),
        to_bold_unicode(f"Referral reward: {referral_reward} credit(s)"),
    ]
    return header + "\n\n" + "\n".join(lines)


def build_credits_admin_keyboard(daily_enabled: bool) -> InlineKeyboardMarkup:
    toggle_label = "🔒 Daily credit is on - ✅" if daily_enabled else "🔓 Daily credit is off - ❌"
    keyboard = [
        [InlineKeyboardButton("✏️ Set cost per file", callback_data="credits_set_cost")],
        [InlineKeyboardButton(toggle_label, callback_data="credits_daily_toggle")],
        [InlineKeyboardButton("✏️ Set daily amount", callback_data="credits_set_daily_amount")],
        [InlineKeyboardButton("✏️ Set referral reward", callback_data="credits_set_referral_reward")],
        [InlineKeyboardButton("◀ Back", callback_data="credits_menu")],
    ]
    return InlineKeyboardMarkup(keyboard)


async def start_credit_checkout(bot, user, chat_id, pack_id: str):
    """Creates a FamGateway order for a credit pack and sends the QR
    message. Returns the order dict, None if the gateway isn't configured,
    "invalid_pack", or "error" if the gateway couldn't create the order."""
    if not famgateway.is_configured():
        return None
    pack = credits.get_credit_pack(pack_id)
    if pack is None:
        return "invalid_pack"

    def caption(order):
        price = order.get("payable_amount") or pack["amount"]
        return (
            "🪙 " + to_bold_unicode(f"{pack['credits']} Credits - ₹{price}")
            + "\n\n<blockquote>"
            + "📲 " + to_bold_unicode("Scan this QR with any UPI app, or tap the button below.") + "\n"
            + "⚡ " + to_bold_unicode("Credits are added automatically after payment.") + "\n"
            + "⏳ " + to_bold_unicode("This QR is valid for 5 minutes.")
            + "</blockquote>\n\n"
            + "🧾 " + to_bold_unicode("Order:") + f" <code>{order['order_id']}</code>"
        )

    try:
        return await famgateway.start_checkout(
            bot, user=user, chat_id=chat_id, kind="credits", amount=pack["amount"],
            txn_fields={"credits": pack["credits"], "plan_label": f"{pack['credits']} Credits"},
            caption_fn=caption,
            extra_buttons=[[InlineKeyboardButton("◀ Back", callback_data="credits_buy")]],
        )
    except famgateway.FamGatewayError:
        logger.exception("FamGateway order creation failed (credit pack %s)", pack_id)
        return "error"


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


def build_verification_deep_link(bot_username: str, token: str) -> str:
    return f"https://t.me/{bot_username}?start=verify_{token}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user

    try:
        await db.register_user(user)
    except Exception:
        logger.exception("Could not save user %s to MongoDB", user.id)

    if context.args:
        payload = context.args[0]

        if payload.startswith("ref_"):
            ref_part = payload[len("ref_"):]
            if ref_part.isdigit():
                referrer_id = int(ref_part)
                await referral.record_referral(referred_id=user.id, referrer_id=referrer_id)

        elif payload.startswith("file_"):
            await deliver_link(context, user, update.effective_chat.id, payload[len("file_"):])
            return

        elif payload.startswith("verify_"):
            token = payload[len("verify_"):]
            doc = await token_verification.consume_verify_token(token, user.id)
            if doc is None:
                await update.message.reply_text(
                    "⚠️ " + to_bold_unicode(
                        "Yeh link invalid hai, expire ho gaya hai, ya pehle "
                        "hi use ho chuka hai."
                    )
                )
                return

            if doc["purpose"] == "credit":
                new_balance = await credits.add_credits(user.id, doc["reward_credits"])
                await referral.maybe_reward_referral_credits(user.id, context.bot)
                await update.message.reply_text(
                    "✅ " + to_bold_unicode(
                        f"+{doc['reward_credits']} credits mile! Naya balance: {new_balance}."
                    ),
                )
            else:  # "gate" — free-user file-access verification
                hours = await token_verification.get_verification_validity_hours()
                await token_verification.set_verified(user.id, hours)
                resume = doc.get("payload")
                await update.message.reply_text(
                    "✅ " + to_bold_unicode(
                        f"Verified! Ab agle {hours} ghante tak aap files "
                        "access kar sakte hain."
                    )
                    + ("\n\n" + to_bold_unicode("Aapki file bhej rahe hain…") if resume else ""),
                    parse_mode=ParseMode.HTML,
                )
                if resume:
                    await deliver_link(context, user, update.effective_chat.id, resume)
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
        keyboard.append([
            InlineKeyboardButton(
                f"🪙 Reward: {s.get('reward_credits', 5)} credits — tap to edit",
                callback_data=f"short_reward_{s['id']}",
            )
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


def build_fsub_join_keyboard(unjoined: list[dict], link_id: str | None = None) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(f"📢 Join {c['title']}", url=c["invite_link"])] for c in unjoined
    ]
    recheck = f"fsub_recheck_{link_id}" if link_id else "fsub_recheck"
    keyboard.append([InlineKeyboardButton("✅ I've Joined", callback_data=recheck)])
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
# File delivery
#
# Opening a file link (t.me/<bot>?start=file_<id>) runs these gates, in order:
#   1. Force-subscribe: must have joined every listed channel.
#   2. Access — the FIRST of these that applies lets the user through:
#        - admin or premium user                       (free, unlimited)
#        - token verification on AND user is verified  (free until it expires)
#        - free daily quota left (Free usage limit)    (free, counted per day)
#        - enough credits                              (costs credits/file)
#      Otherwise the user gets a prompt: verify / earn credits / buy premium.
#   3. Files are sent with the caption / thumbnail / button / protect-content
#      settings, then scheduled for auto-delete.
# A link costs ONE access (or one credit) no matter how many files a batch has.
# ============================================================

async def build_final_caption(default_caption: str, **placeholders) -> str:
    if await file_settings.is_caption_enabled():
        template = await file_settings.get_caption_template()
        if template:
            try:
                return template.format(**placeholders)
            except Exception:
                return template
    return default_caption


async def build_extra_button_markup() -> InlineKeyboardMarkup | None:
    if not await file_settings.is_custom_button_enabled():
        return None
    btn = await file_settings.get_custom_button()
    if btn.get("label") and btn.get("url"):
        return InlineKeyboardMarkup([[InlineKeyboardButton(btn["label"], url=btn["url"])]])
    return None


async def schedule_auto_delete(bot, chat_id: int, message_id: int) -> None:
    if not await file_settings.is_auto_delete_enabled():
        return
    seconds = await file_settings.get_auto_delete_seconds()

    async def _delete_later():
        await asyncio.sleep(seconds)
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception:
            pass

    asyncio.create_task(_delete_later())


_thumb_cache: dict = {"file_id": None, "data": None}


async def _get_thumbnail_bytes(bot) -> bytes | None:
    """Downloads (and caches) the admin's thumbnail image, if enabled."""
    if not await file_settings.is_thumbnail_enabled():
        return None
    file_id = await file_settings.get_thumbnail_file_id()
    if not file_id:
        return None
    if _thumb_cache["file_id"] != file_id:
        try:
            tg_file = await bot.get_file(file_id)
            _thumb_cache["data"] = bytes(await tg_file.download_as_bytearray())
            _thumb_cache["file_id"] = file_id
        except Exception:
            logger.exception("Could not download thumbnail")
            return None
    return _thumb_cache["data"]


async def _send_link_items(bot, chat_id: int, link: dict) -> int:
    """Sends every file of a saved link. Returns how many were delivered."""
    protect = await file_settings.is_protect_content_enabled()
    markup = await build_extra_button_markup()
    thumb = await _get_thumbnail_bytes(bot)
    senders = {
        "document": (bot.send_document, "document", True),
        "video": (bot.send_video, "video", True),
        "audio": (bot.send_audio, "audio", True),
        "animation": (bot.send_animation, "animation", True),
        "voice": (bot.send_voice, "voice", False),
        "photo": (bot.send_photo, "photo", False),
    }
    delivered = 0
    for item in link["items"]:
        entry = senders.get(item["type"])
        if entry is None:
            continue
        send, arg, supports_thumb = entry
        caption = await build_final_caption(
            item.get("caption") or "",
            filename=item.get("name") or "",
            filesize=file_store.format_size(item.get("size")),
            caption=item.get("caption") or "",
        )
        kwargs = {
            "chat_id": chat_id, arg: item["file_id"],
            "caption": caption or None,
            "reply_markup": markup, "protect_content": protect,
        }
        try:
            if supports_thumb and thumb:
                try:
                    sent = await send(**kwargs, thumbnail=thumb)
                except Exception:
                    sent = await send(**kwargs)  # thumbnail rejected -> plain send
            else:
                sent = await send(**kwargs)
        except Exception:
            logger.exception("Could not send file %s of link %s", item.get("file_id"), link["id"])
            continue
        delivered += 1
        await schedule_auto_delete(bot, sent.chat_id, sent.message_id)
    return delivered


async def _access_prompt(context, user, link_id: str, balance: int, cost: int) -> tuple[str, InlineKeyboardMarkup]:
    """Message + buttons shown to a user who can't open the file yet."""
    rows = []
    lines = [
        "🔒 " + to_bold_unicode("Is file ko kholne ke liye access chahiye."),
        "",
        "<blockquote>"
        + "🪙 " + to_bold_unicode(f"Aapke credits: {balance} (1 file link = {cost} credit)")
        + "</blockquote>",
    ]
    if await token_verification.is_token_verification_enabled():
        shorteners_on = [s for s in await shortener_store.list_shorteners() if s.get("enabled")]
        if shorteners_on:
            hours = await token_verification.get_verification_validity_hours()
            me = await context.bot.get_me()
            token = await token_verification.create_verify_token(
                user.id, purpose="gate", payload=link_id
            )
            short_link = await shortener_store.shorten_url(
                build_verification_deep_link(me.username, token)
            )
            rows.append([InlineKeyboardButton(f"✅ Verify — {hours}h free access", url=short_link)])
    rows.append([InlineKeyboardButton("🪙 Earn / Buy credits", callback_data="credits_menu")])
    rows.append([InlineKeyboardButton("💎 Buy Premium", callback_data="settings_premium")])
    rows.append([InlineKeyboardButton("🔄 Try again", callback_data=f"file_retry_{link_id}")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def deliver_link(context, user, chat_id: int, link_id: str) -> None:
    """Runs the gates for `link_id` and, if the user passes, sends the files."""
    bot = context.bot
    link = await file_store.get_link(link_id)
    if not link:
        await bot.send_message(chat_id=chat_id, text="⚠️ Yeh link invalid hai ya hata diya gaya hai.")
        return

    # 1. Force subscribe
    unjoined = await force_subscribe.get_unjoined_channels(bot, user.id)
    if unjoined:
        await bot.send_message(
            chat_id=chat_id,
            text="📢 " + to_bold_unicode("File paane ke liye pehle neeche ke channels join karein."),
            reply_markup=build_fsub_join_keyboard(unjoined, link_id),
        )
        return

    # 2. Access. `refund` undoes whatever was spent if sending then fails.
    refund = None
    if is_admin(user.id) or await premium.is_premium(user.id):
        pass
    elif (await token_verification.is_token_verification_enabled()
          and await token_verification.is_verified(user.id)):
        pass
    elif await file_settings.try_use_free_quota(user.id):
        async def refund():
            await file_settings.refund_free_quota(user.id)
    else:
        cost = await credits.get_credit_cost_per_file()
        if cost <= 0:
            pass  # admin set files to cost nothing
        elif await credits.deduct_credit(user.id, cost):
            async def refund():
                await credits.add_credits(user.id, cost)
        else:
            balance = await credits.get_credits(user.id)
            text, keyboard = await _access_prompt(context, user, link_id, balance, cost)
            await bot.send_message(
                chat_id=chat_id, text=text, parse_mode=ParseMode.HTML, reply_markup=keyboard
            )
            return

    # 3. Send
    delivered = 0
    try:
        delivered = await _send_link_items(bot, chat_id, link)
    finally:
        if delivered == 0 and refund is not None:
            try:
                await refund()
            except Exception:
                logger.exception("Refund failed for user %s", user.id)
    if delivered == 0:
        await bot.send_message(
            chat_id=chat_id, text="⚠️ File bhejne mein dikkat aayi. Aapka access wapas kar diya gaya hai."
        )
        return
    await file_store.record_download(link_id)
    if await file_settings.is_auto_delete_enabled():
        minutes = max(1, (await file_settings.get_auto_delete_seconds()) // 60)
        await bot.send_message(
            chat_id=chat_id,
            text="⏳ " + to_bold_unicode(
                f"Yeh file {minutes} minute mein delete ho jayegi. Pehle hi save/forward kar lein."
            ),
        )


# ---------------- Saving files (admin) ----------------

async def _finish_store(update: Update, context: ContextTypes.DEFAULT_TYPE, items: list[dict]) -> None:
    link_id = await file_store.create_link(items, update.effective_user.id)
    me = await context.bot.get_me()
    await update.message.reply_text(
        f"✅ Saved {len(items)} file(s).\n\n"
        f"🔗 https://t.me/{me.username}?start=file_{link_id}",
        disable_web_page_preview=True,
    )


async def link_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admins only.")
        return
    replied = update.message.reply_to_message
    if replied:
        item = file_store.extract_media(replied)
        if item:
            await _finish_store(update, context, [item])
            return
    context.user_data["awaiting"] = "store_single"
    await update.message.reply_text(
        "📤 Ab wo file bhejo jiska link banana hai (document / video / audio / photo).\n"
        "Cancel: /cancel"
    )


async def batch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Admins only.")
        return
    context.user_data["awaiting"] = "store_batch"
    context.user_data["batch_items"] = []
    await update.message.reply_text(
        f"📦 Batch mode on. Files ek ek karke bhejo (max {file_store.MAX_BATCH_ITEMS}).\n"
        "Khatam hone par /done bhejo. Cancel: /cancel"
    )


async def done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        return
    if context.user_data.get("awaiting") != "store_batch":
        await update.message.reply_text("Koi batch chal nahi raha. /batch se shuru karo.")
        return
    items = context.user_data.get("batch_items") or []
    if not items:
        await update.message.reply_text("Abhi tak koi file add nahi hui.")
        return
    context.user_data.pop("awaiting", None)
    context.user_data.pop("batch_items", None)
    await _finish_store(update, context, items)


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
        shorteners = await shortener_store.list_shorteners()
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
            text="➕ " + to_bold_unicode("Send: Name | api-domain.com | API_KEY | reward(optional)")
            + "\n<code>GPLinks | api.gplinks.com | abcd1234 | 5</code>"
            + "\n\n" + to_bold_unicode("Send /cancel to cancel."),
            parse_mode=ParseMode.HTML,
        )
        return

    if data.startswith("short_toggle_"):
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await shortener_store.toggle_shortener(data[len("short_toggle_"):])
        await query.answer()
        shorteners = await shortener_store.list_shorteners()
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
        await shortener_store.remove_shortener(data[len("short_del_"):])
        await query.answer("Removed.")
        shorteners = await shortener_store.list_shorteners()
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
        enabled = await token_verification.is_token_verification_enabled()
        hours = await token_verification.get_verification_validity_hours()
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
        currently_on = await token_verification.is_token_verification_enabled()
        await token_verification.set_token_verification_enabled(not currently_on)
        await query.answer("Token verification turned " + ("OFF ❌" if currently_on else "ON ✅"))
        hours = await token_verification.get_verification_validity_hours()
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
        enabled = await force_subscribe.is_force_sub_enabled()
        channels = await force_subscribe.list_force_sub_channels()
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
        currently_on = await force_subscribe.is_force_sub_enabled()
        await force_subscribe.set_force_sub_enabled(not currently_on)
        await query.answer("Force subscribe turned " + ("OFF ❌" if currently_on else "ON ✅"))
        channels = await force_subscribe.list_force_sub_channels()
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
        await force_subscribe.remove_force_sub_channel(data[len("fsub_del_"):])
        await query.answer("Removed.")
        enabled = await force_subscribe.is_force_sub_enabled()
        channels = await force_subscribe.list_force_sub_channels()
        await query.edit_message_text(
            text=build_fsub_menu_text(enabled, channels, True),
            parse_mode=ParseMode.HTML,
            reply_markup=build_fsub_menu_keyboard(channels, True),
        )
        return

    if data == "fsub_recheck" or data.startswith("fsub_recheck_"):
        unjoined = await force_subscribe.get_unjoined_channels(context.bot, user.id)
        if unjoined:
            await query.answer("Abhi bhi kuch channels baaki hain ❌", show_alert=True)
            return
        await query.answer("✅ Sab channels joined!", show_alert=True)
        if data.startswith("fsub_recheck_"):
            try:
                await query.message.delete()
            except Exception:
                pass
            await deliver_link(context, user, update.effective_chat.id, data[len("fsub_recheck_"):])
        return

    if data.startswith("file_retry_"):
        await query.answer()
        try:
            await query.message.delete()
        except Exception:
            pass
        await deliver_link(context, user, update.effective_chat.id, data[len("file_retry_"):])
        return
    # --------------------------------------------------------------------

    # ---------------- Caption panel ----------------
    if data == "settings_caption":
        await query.answer()
        enabled = await file_settings.is_caption_enabled()
        template = await file_settings.get_caption_template()
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
        currently_on = await file_settings.is_caption_enabled()
        await file_settings.set_caption_enabled(not currently_on)
        await query.answer("Caption turned " + ("OFF ❌" if currently_on else "ON ✅"))
        template = await file_settings.get_caption_template()
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
        enabled = await file_settings.is_thumbnail_enabled()
        has_thumb = bool(await file_settings.get_thumbnail_file_id())
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
        currently_on = await file_settings.is_thumbnail_enabled()
        await file_settings.set_thumbnail_enabled(not currently_on)
        await query.answer("Thumbnail turned " + ("OFF ❌" if currently_on else "ON ✅"))
        has_thumb = bool(await file_settings.get_thumbnail_file_id())
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
        await file_settings.set_thumbnail_file_id(None)
        await query.answer("Thumbnail removed.")
        enabled = await file_settings.is_thumbnail_enabled()
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
        enabled = await file_settings.is_custom_button_enabled()
        btn = await file_settings.get_custom_button()
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
        currently_on = await file_settings.is_custom_button_enabled()
        await file_settings.set_custom_button_enabled(not currently_on)
        await query.answer("Button turned " + ("OFF ❌" if currently_on else "ON ✅"))
        btn = await file_settings.get_custom_button()
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
        enabled = await file_settings.is_auto_delete_enabled()
        seconds = await file_settings.get_auto_delete_seconds()
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
        currently_on = await file_settings.is_auto_delete_enabled()
        await file_settings.set_auto_delete_enabled(not currently_on)
        await query.answer("Auto delete turned " + ("OFF ❌" if currently_on else "ON ✅"))
        seconds = await file_settings.get_auto_delete_seconds()
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
        enabled = await file_settings.is_protect_content_enabled()
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
        currently_on = await file_settings.is_protect_content_enabled()
        await file_settings.set_protect_content_enabled(not currently_on)
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
        await query.answer("⏳ Creating your payment…")
        result = await start_premium_checkout(context.bot, user, update.effective_chat.id, plan_id)
        if result is None:
            await query.edit_message_text(text="⚠️ Payment gateway isn't configured yet.")
        elif result == "disabled":
            await query.edit_message_text(
                text="🚫 Premium plan purchases are currently unavailable. "
                     "Please check back later."
            )
        elif result == "invalid_plan":
            await query.edit_message_text(text="⚠️ Invalid plan.")
        elif result == "error":
            await query.edit_message_text(
                text="⚠️ Couldn't create the payment right now. Please try again in a minute."
            )
        else:
            try:
                await query.message.delete()  # the QR message replaces the menu
            except Exception:
                pass
        return
    # --------------------------------------------------------------------

    # ---------------- Premium admin panel (admin-only) ----------------
    if data == "premium_users_list":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        premium_map = await premium.list_premium()
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
        currently_on = await premium.is_premium_enabled()
        await premium.set_premium_enabled(not currently_on)
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
        enabled = await premium.is_premium_enabled()
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
        currently_on = await file_settings.is_free_limit_enabled()
        await file_settings.set_free_limit_enabled(not currently_on)
        await query.answer("Free usage limit turned " + ("OFF ❌" if currently_on else "ON ✅"))
        count = await file_settings.get_free_limit_count()
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

    # ---------------- Credits ----------------
    if data == "credits_menu":
        await query.answer()
        balance = await credits.get_credits(user.id)
        cost = await credits.get_credit_cost_per_file()
        daily_on = await credits.is_daily_credit_enabled()
        daily_amt = await credits.get_daily_credit_amount()
        await query.edit_message_text(
            text=build_credits_text(balance, cost, daily_on, daily_amt),
            parse_mode=ParseMode.HTML,
            reply_markup=build_credits_keyboard(daily_on, is_admin(user.id)),
        )
        return

    if data == "credits_daily":
        ok, result = await credits.claim_daily_credit(user.id)
        if ok:
            await query.answer(f"✅ +1 credit! Naya balance: {result}", show_alert=True)
        else:
            hours = int(result.total_seconds() // 3600)
            minutes = int((result.total_seconds() % 3600) // 60)
            await query.answer(
                f"⏳ Agla daily credit {hours}h {minutes}m baad milega.", show_alert=True
            )
        balance = await credits.get_credits(user.id)
        cost = await credits.get_credit_cost_per_file()
        daily_on = await credits.is_daily_credit_enabled()
        daily_amt = await credits.get_daily_credit_amount()
        await query.edit_message_text(
            text=build_credits_text(balance, cost, daily_on, daily_amt),
            parse_mode=ParseMode.HTML,
            reply_markup=build_credits_keyboard(daily_on, is_admin(user.id)),
        )
        return

    if data == "credits_earn":
        await query.answer()
        shorteners = await shortener_store.list_shorteners()
        await query.edit_message_text(
            text=build_credits_earn_text(shorteners),
            parse_mode=ParseMode.HTML,
            reply_markup=build_credits_earn_keyboard(shorteners),
        )
        return

    if data.startswith("credits_earn_"):
        shortener_id = data[len("credits_earn_"):]
        shorteners = {s["id"]: s for s in await shortener_store.list_shorteners()}
        shortener = shorteners.get(shortener_id)
        if not shortener or not shortener.get("enabled"):
            await query.answer("⚠️ Yeh shortener ab available nahi hai.", show_alert=True)
            return
        await query.answer()
        me = await context.bot.get_me()
        reward = shortener.get("reward_credits", 5)
        token = await token_verification.create_verify_token(
            user.id, purpose="credit", shortener_id=shortener_id, reward_credits=reward
        )
        long_link = build_verification_deep_link(me.username, token)
        short_link = await shortener_store.shorten_url(long_link, shortener_id=shortener_id)
        await query.edit_message_text(
            text="🔗 " + to_bold_unicode(f"Complete this to earn +{reward} credits:")
            + f"\n{short_link}\n\n"
            + to_bold_unicode("Steps complete karne ke baad aap wapas bot par aa jaayenge "
                               "aur credits mil jaayenge."),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("◀ Back", callback_data="credits_earn")]]
            ),
        )
        return

    if data == "credits_buy":
        await query.answer()
        await query.edit_message_text(
            text=build_credits_buy_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=build_credits_buy_keyboard(),
        )
        return

    if data.startswith("creditpack_"):
        pack_id = data[len("creditpack_"):]
        await query.answer("⏳ Creating your payment…")
        result = await start_credit_checkout(context.bot, user, update.effective_chat.id, pack_id)
        if result is None:
            await query.edit_message_text(text="⚠️ Payment gateway isn't configured yet.")
        elif result == "invalid_pack":
            await query.edit_message_text(text="⚠️ Invalid pack.")
        elif result == "error":
            await query.edit_message_text(
                text="⚠️ Couldn't create the payment right now. Please try again in a minute."
            )
        else:
            try:
                await query.message.delete()  # the QR message replaces the menu
            except Exception:
                pass
        return

    if data == "credits_admin":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await query.answer()
        cost = await credits.get_credit_cost_per_file()
        daily_on = await credits.is_daily_credit_enabled()
        daily_amt = await credits.get_daily_credit_amount()
        referral_reward = await referral.get_referral_reward_credits()
        await query.edit_message_text(
            text=build_credits_admin_text(cost, daily_on, daily_amt, referral_reward),
            parse_mode=ParseMode.HTML,
            reply_markup=build_credits_admin_keyboard(daily_on),
        )
        return

    if data == "credits_set_cost":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await query.answer()
        context.user_data["awaiting"] = "credits_cost_per_file"
        await query.edit_message_text(
            text="✏️ " + to_bold_unicode("Send credit cost per file, e.g.")
            + "\n<code>1</code>\n\n" + to_bold_unicode("Send /cancel to cancel."),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "credits_daily_toggle":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        currently_on = await credits.is_daily_credit_enabled()
        await credits.set_daily_credit_enabled(not currently_on)
        await query.answer("Daily credit turned " + ("OFF ❌" if currently_on else "ON ✅"))
        cost = await credits.get_credit_cost_per_file()
        daily_amt = await credits.get_daily_credit_amount()
        referral_reward = await referral.get_referral_reward_credits()
        await query.edit_message_text(
            text=build_credits_admin_text(cost, not currently_on, daily_amt, referral_reward),
            parse_mode=ParseMode.HTML,
            reply_markup=build_credits_admin_keyboard(not currently_on),
        )
        return

    if data == "credits_set_daily_amount":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await query.answer()
        context.user_data["awaiting"] = "credits_daily_amount"
        await query.edit_message_text(
            text="✏️ " + to_bold_unicode("Send daily credit amount, e.g.")
            + "\n<code>1</code>\n\n" + to_bold_unicode("Send /cancel to cancel."),
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "credits_set_referral_reward":
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await query.answer()
        context.user_data["awaiting"] = "credits_referral_reward"
        await query.edit_message_text(
            text="✏️ " + to_bold_unicode("Send referral reward in credits, e.g.")
            + "\n<code>5</code>\n\n" + to_bold_unicode("Send /cancel to cancel."),
            parse_mode=ParseMode.HTML,
        )
        return

    if data.startswith("short_reward_"):
        if not is_admin(user.id):
            await query.answer("🚫 Admins only.", show_alert=True)
            return
        await query.answer()
        context.user_data["awaiting"] = f"shortener_reward:{data[len('short_reward_'):]}"
        await query.edit_message_text(
            text="✏️ " + to_bold_unicode("Send new reward credits for this shortener, e.g.")
            + "\n<code>5</code>\n\n" + to_bold_unicode("Send /cancel to cancel."),
            parse_mode=ParseMode.HTML,
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
            enabled = await premium.is_premium_enabled()
            await query.edit_message_text(
                text=build_premium_menu_text(),
                parse_mode=ParseMode.HTML,
                reply_markup=build_premium_menu_keyboard(enabled),
            )
            return
        if not famgateway.is_configured():
            await query.edit_message_text(text="⚠️ Payment gateway isn't configured yet.")
            return
        if not await premium.is_premium_enabled():
            await query.edit_message_text(
                text="🚫 Premium plan purchases are currently unavailable. "
                     "Please check back later."
            )
            return
        already_premium = await premium.is_premium(user.id)
        expiry_iso = await premium.get_premium_expiry(user.id) if already_premium else None
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
        enabled = await file_settings.is_free_limit_enabled()
        count = await file_settings.get_free_limit_count()
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
        stats = await referral.get_referral_stats(user.id)
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
        for p in premium.PLANS
    ]
    keyboard.append([InlineKeyboardButton("◀ Back", callback_data="settings")])
    return InlineKeyboardMarkup(keyboard)


async def start_premium_checkout(bot, user, chat_id, plan_id: str):
    """Creates a FamGateway order for the chosen premium plan and sends the
    QR message. Returns the order dict, None if the gateway isn't
    configured, "disabled" if an admin turned premium purchases off,
    "invalid_plan" for an unknown plan id, or "error" if the gateway
    couldn't create the order."""
    if not famgateway.is_configured():
        return None
    if not await premium.is_premium_enabled():
        return "disabled"
    plan = premium.get_plan(plan_id)
    if plan is None:
        return "invalid_plan"

    custom = await premium.get_premium_message()
    extra = []
    if custom.get("button_text") and custom.get("button_url"):
        extra.append([InlineKeyboardButton(custom["button_text"], url=custom["button_url"])])
    extra.append([InlineKeyboardButton("◀ Back", callback_data="settings_premium")])

    def caption(order):
        price = order.get("payable_amount") or plan["amount"]
        order_line = "\n\n🧾 " + to_bold_unicode("Order:") + f" <code>{order['order_id']}</code>"
        if custom.get("text"):
            # Admin's custom message replaces the default text (captions are
            # limited to 1024 chars, so fall back if it wouldn't fit).
            combined = custom["text"] + order_line
            if len(combined) <= 1024:
                return combined
        return (
            "💎 " + to_bold_unicode(f"{plan['label']} - ₹{price}")
            + "\n\n<blockquote>"
            + "⏳ " + to_bold_unicode(f"Valid for {plan['days']} day(s).") + "\n"
            + "📲 " + to_bold_unicode("Scan this QR with any UPI app, or tap the button below.") + "\n"
            + "⚡ " + to_bold_unicode("Premium activates automatically after payment.") + "\n"
            + "⏳ " + to_bold_unicode("This QR is valid for 5 minutes.")
            + "</blockquote>"
            + order_line
        )

    try:
        return await famgateway.start_checkout(
            bot, user=user, chat_id=chat_id, kind="premium", amount=plan["amount"],
            txn_fields={
                "plan_days": plan["days"],
                "plan_label": f"{premium.PLAN_LABEL} - {plan['label']}",
            },
            caption_fn=caption,
            extra_buttons=extra,
        )
    except famgateway.FamGatewayError:
        logger.exception("FamGateway order creation failed (plan %s)", plan_id)
        return "error"


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user

    if not famgateway.is_configured():
        await update.message.reply_text(
            "⚠️ Payment gateway isn't configured yet. Set the "
            "FAMGATEWAY_API_KEY environment variable first."
        )
        return
    if not await premium.is_premium_enabled():
        await update.message.reply_text(
            "🚫 Premium plan purchases are currently unavailable. Please "
            "check back later."
        )
        return

    already_premium = await premium.is_premium(user.id)
    expiry_iso = await premium.get_premium_expiry(user.id) if already_premium else None
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
    expiry_iso = await premium.get_premium_expiry(user.id)
    if expiry_iso and await premium.is_premium(user.id):
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


async def credits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    balance = await credits.get_credits(user.id)
    cost = await credits.get_credit_cost_per_file()
    daily_on = await credits.is_daily_credit_enabled()
    daily_amt = await credits.get_daily_credit_amount()
    await update.message.reply_text(
        text=build_credits_text(balance, cost, daily_on, daily_amt),
        parse_mode=ParseMode.HTML,
        reply_markup=build_credits_keyboard(daily_on, is_admin(user.id)),
    )


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
        await premium.set_premium(target_id, expiry.isoformat())
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
        removed = await premium.remove_premium(target_id)
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
        await file_settings.set_free_limit_count(count)
        context.user_data.pop("awaiting", None)
        await update.message.reply_text(
            "✅ " + to_bold_unicode(f"Free usage limit set to {count} per day.")
        )
        return

    if awaiting == "premium_message_text":
        await premium.set_premium_message(text=update.message.text)
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
        await premium.set_premium_message(button_text=label, button_url=url)
        context.user_data.pop("awaiting", None)
        await update.message.reply_text("✅ Premium plan button updated.")
        return

    if awaiting == "shortener_add":
        raw = update.message.text or ""
        parts = [p.strip() for p in raw.split("|")]
        if len(parts) not in (3, 4) or not all(parts[:3]):
            await update.message.reply_text(
                "⚠️ Format: Name | api-domain.com | API_KEY | reward_credits(optional)  (or /cancel)"
            )
            return
        name, domain, api_key = parts[:3]
        reward = 5
        if len(parts) == 4:
            if not parts[3].isdigit():
                await update.message.reply_text(
                    "⚠️ Reward credits must be a whole number (or /cancel)."
                )
                return
            reward = int(parts[3])
        await shortener_store.add_shortener(name, domain, api_key, reward_credits=reward)
        context.user_data.pop("awaiting", None)
        await update.message.reply_text(
            "✅ " + to_bold_unicode(f"Shortener '{name}' added ({reward} credits reward).")
        )
        return

    if awaiting == "verification_hours":
        raw = (update.message.text or "").strip()
        if not raw.isdigit() or int(raw) <= 0:
            await update.message.reply_text(
                "⚠️ Invalid number. Send a whole number like 24 (or /cancel)."
            )
            return
        hours = int(raw)
        await token_verification.set_verification_validity_hours(hours)
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
            await force_subscribe.add_force_sub_channel(
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
            await force_subscribe.add_force_sub_channel(
                chat_id=int(chat_id_raw), title=title, invite_link=invite_link,
            )
        context.user_data.pop("awaiting", None)
        await update.message.reply_text("✅ " + to_bold_unicode("Channel added to force subscribe."))
        return

    if awaiting == "caption_template":
        await file_settings.set_caption_template(update.message.text or "")
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
        await file_settings.set_custom_button(label, url)
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
        await file_settings.set_auto_delete_seconds(seconds)
        context.user_data.pop("awaiting", None)
        await update.message.reply_text(
            "✅ " + to_bold_unicode(f"Auto delete delay set to {seconds} second(s).")
        )
        return

    if awaiting == "credits_cost_per_file":
        raw = (update.message.text or "").strip()
        if not raw.isdigit() or int(raw) <= 0:
            await update.message.reply_text(
                "⚠️ Invalid number. Send a whole number like 1 (or /cancel)."
            )
            return
        cost = int(raw)
        await credits.set_credit_cost_per_file(cost)
        context.user_data.pop("awaiting", None)
        await update.message.reply_text(
            "✅ " + to_bold_unicode(f"Credit cost per file set to {cost}.")
        )
        return

    if awaiting == "credits_daily_amount":
        raw = (update.message.text or "").strip()
        if not raw.isdigit() or int(raw) <= 0:
            await update.message.reply_text(
                "⚠️ Invalid number. Send a whole number like 1 (or /cancel)."
            )
            return
        amount = int(raw)
        await credits.set_daily_credit_amount(amount)
        context.user_data.pop("awaiting", None)
        await update.message.reply_text(
            "✅ " + to_bold_unicode(f"Daily credit amount set to {amount}.")
        )
        return

    if awaiting == "credits_referral_reward":
        raw = (update.message.text or "").strip()
        if not raw.isdigit() or int(raw) <= 0:
            await update.message.reply_text(
                "⚠️ Invalid number. Send a whole number like 5 (or /cancel)."
            )
            return
        amount = int(raw)
        await referral.set_referral_reward_credits(amount)
        context.user_data.pop("awaiting", None)
        await update.message.reply_text(
            "✅ " + to_bold_unicode(f"Referral reward set to {amount} credits.")
        )
        return

    if awaiting and awaiting.startswith("shortener_reward:"):
        shortener_id = awaiting.split(":", 1)[1]
        raw = (update.message.text or "").strip()
        if not raw.isdigit() or int(raw) <= 0:
            await update.message.reply_text(
                "⚠️ Invalid number. Send a whole number like 5 (or /cancel)."
            )
            return
        reward = int(raw)
        ok = await shortener_store.set_shortener_reward(shortener_id, reward)
        context.user_data.pop("awaiting", None)
        if ok:
            await update.message.reply_text(
                "✅ " + to_bold_unicode(f"Reward updated to {reward} credits.")
            )
        else:
            await update.message.reply_text("⚠️ Shortener not found (was it removed?).")
        return


async def admin_photo_reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Captures the next photo after an admin taps Premium Plan Picture or
    Set Thumbnail."""
    awaiting = context.user_data.get("awaiting")

    # Saving files for /link and /batch (any media type).
    if awaiting in ("store_single", "store_batch"):
        if not is_admin(update.effective_user.id):
            return
        item = file_store.extract_media(update.message)
        if item is None:
            return
        if awaiting == "store_single":
            context.user_data.pop("awaiting", None)
            await _finish_store(update, context, [item])
        else:
            items = context.user_data.setdefault("batch_items", [])
            if len(items) >= file_store.MAX_BATCH_ITEMS:
                await update.message.reply_text("⚠️ Batch full. /done bhejo.")
                return
            items.append(item)
            await update.message.reply_text(f"➕ Added ({len(items)}). Aur bhejo ya /done.")
        return

    if awaiting not in ("premium_message_picture", "thumbnail_photo"):
        return
    if not update.message.photo:
        return
    if not is_admin(update.effective_user.id):
        return
    file_id = update.message.photo[-1].file_id

    if awaiting == "premium_message_picture":
        await premium.set_premium_message(photo_file_id=file_id)
        context.user_data.pop("awaiting", None)
        await update.message.reply_text("✅ Premium plan picture updated.")
        return

    if awaiting == "thumbnail_photo":
        await file_settings.set_thumbnail_file_id(file_id)
        context.user_data.pop("awaiting", None)
        await update.message.reply_text("✅ Thumbnail updated.")
        return


async def main() -> None:
    if BOT_TOKEN == "PUT-YOUR-BOT-TOKEN-HERE":
        raise SystemExit(
            "Set your bot token first: export BOT_TOKEN='123456:ABC-DEF...'"
        )

    # MongoDB must be reachable before anything else starts.
    await db.init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("buy", buy))
    application.add_handler(CommandHandler("id", id_cmd))
    application.add_handler(CommandHandler("myplan", myplan))
    application.add_handler(CommandHandler("credits", credits_cmd))
    application.add_handler(CommandHandler("link", link_cmd))
    application.add_handler(CommandHandler("batch", batch_cmd))
    application.add_handler(CommandHandler("done", done_cmd))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(
        filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO
        | filters.ANIMATION | filters.VOICE,
        admin_photo_reply_handler,
    ))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text_reply_handler))

    # --- Telegram bot: start polling (manual lifecycle, not the blocking
    # run_polling() helper, so it can run alongside the aiohttp server in
    # the same asyncio loop) ---
    await application.initialize()
    await application.start()
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    me = await application.bot.get_me()
    logger.info("Bot @%s started (polling)", me.username)

    # --- FamGateway webhook HTTP server ---
    web_app = famgateway.build_web_app(application.bot)
    web_app["bot_username"] = me.username
    runner = web.AppRunner(web_app)
    await runner.setup()
    port = int(os.environ.get("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("FamGateway webhook server listening on port %s", port)

    # Re-attach pollers to payments that were mid-flight during the last restart.
    await famgateway.resume_pending(application.bot)

    try:
        await asyncio.Event().wait()  # run forever
    finally:
        await runner.cleanup()
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        await famgateway.shutdown()
        await db.close_db()


if __name__ == "__main__":
    asyncio.run(main())
