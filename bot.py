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
            text=to_bold_unicode("Help Menu") + "\n\n"
                 "Send me any file and I'll generate a shareable permanent "
                 "link for it. Use that link to retrieve the file anytime.",
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
