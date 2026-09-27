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
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

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
        [header, intro, basic, file_storage, premium, user_settings, admin]
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


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
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
            text=to_bold_unicode("Settings") + "\n\n"
                 "(configure your preferences here)",
        )
    elif query.data == "help_back":
        user = update.effective_user
        await query.edit_message_text(
            text=build_start_text(user.first_name),
            parse_mode=ParseMode.HTML,
            reply_markup=build_start_keyboard(),
        )
    elif query.data in ("help_settings", "help_about"):
        # Just display for now — not wired up to real logic yet.
        await query.answer(text="Yeh feature jaldi aa raha hai 🚧", show_alert=True)


def main() -> None:
    if BOT_TOKEN == "PUT-YOUR-BOT-TOKEN-HERE":
        raise SystemExit(
            "Set your bot token first: export BOT_TOKEN='123456:ABC-DEF...'"
        )

    # Python 3.14 removed the implicit auto-creation of an event loop in the
    # main thread. python-telegram-bot's run_polling() still calls
    # asyncio.get_event_loop() internally, so we create and set one explicitly
    # here to stay compatible across Python versions.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
