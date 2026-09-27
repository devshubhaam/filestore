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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from aiohttp import web

import payments

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT-YOUR-BOT-TOKEN-HERE")


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
    await update.message.reply_text(
        text=build_start_text(user.first_name),
        parse_mode=ParseMode.HTML,
        reply_markup=build_start_keyboard(),
    )


SETTINGS_PLACEHOLDER_CALLBACKS = {
    "settings_free_limit",
    "settings_refer",
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

PREMIUM_PLACEHOLDER_CALLBACKS = {
    "premium_plan_message",
    "premium_add_user",
    "premium_remove_user",
    "premium_users_list",
    "premium_toggle",
}


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query

    if (
        query.data in ("help_settings", "help_about")
        or query.data in SETTINGS_PLACEHOLDER_CALLBACKS
        or query.data in PREMIUM_PLACEHOLDER_CALLBACKS
    ):
        # Just display for now — not wired up to real logic yet.
        await query.answer(text="Yeh feature jaldi aa raha hai 🚧", show_alert=True)
        return

    await query.answer()

    if query.data == "help":
        await query.edit_message_text(
            text=build_help_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=build_help_keyboard(),
        )
    elif query.data == "about":
        await query.edit_message_text(
            text=to_bold_unicode("About") + "\n\n"
                 "I'm Haven 🌺 — a permanent file store bot.",
        )
    elif query.data == "settings":
        await query.edit_message_text(
            text=build_settings_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=build_settings_keyboard(),
        )
    elif query.data == "settings_premium":
        await query.edit_message_text(
            text=build_premium_menu_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=build_premium_menu_keyboard(),
        )
    elif query.data == "premium_back":
        await query.edit_message_text(
            text=build_settings_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=build_settings_keyboard(),
        )
    elif query.data == "settings_back":
        user = update.effective_user
        await query.edit_message_text(
            text=build_start_text(user.first_name),
            parse_mode=ParseMode.HTML,
            reply_markup=build_start_keyboard(),
        )
    elif query.data == "help_back":
        user = update.effective_user
        await query.edit_message_text(
            text=build_start_text(user.first_name),
            parse_mode=ParseMode.HTML,
            reply_markup=build_start_keyboard(),
        )


async def build_buy_offer(user, chat_id) -> tuple[str, InlineKeyboardMarkup] | None:
    """Creates a pending PayU transaction and returns (text, keyboard) for
    the buy offer message. Returns None if PayU isn't configured."""
    if not (payments.PAYU_KEY and payments.PAYU_SALT and payments.BASE_URL):
        return None

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
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(f"💳 Pay ₹{payments.PLAN_AMOUNT} now", url=pay_url)]]
    )
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
    return text, keyboard


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

    text, keyboard = offer
    await update.message.reply_text(
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def main() -> None:
    if BOT_TOKEN == "PUT-YOUR-BOT-TOKEN-HERE":
        raise SystemExit(
            "Set your bot token first: export BOT_TOKEN='123456:ABC-DEF...'"
        )

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("buy", buy))
    application.add_handler(CallbackQueryHandler(button_handler))

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
