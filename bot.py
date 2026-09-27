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


def build_start_text(user_first_name: str) -> str:
    # HTML parse mode: <b> for bold, <blockquote> for the quoted lines
    text = (
        f"<b>Hey {user_first_name},</b>\n\n"
        f"<blockquote><b>My name is Haven 🌺</b></blockquote>\n\n"
        f"<b>I am a permanent file store bot and users can access stored "
        f"messages by using a shareable link given by me.</b>\n\n"
        f"<blockquote><b>To know more click help button.</b></blockquote>"
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
            text="<b>Help Menu</b>\n\nSend me any file and I'll generate a "
                 "shareable permanent link for it. Use that link to retrieve "
                 "the file anytime.",
            parse_mode=ParseMode.HTML,
        )
    elif query.data == "about":
        await query.edit_message_text(
            text="<b>About</b>\n\nI'm Haven 🌺 — a permanent file store bot.",
            parse_mode=ParseMode.HTML,
        )
    elif query.data == "settings":
        await query.edit_message_text(
            text="<b>Settings</b>\n\n(configure your preferences here)",
            parse_mode=ParseMode.HTML,
        )


def main() -> None:
    if BOT_TOKEN == "PUT-YOUR-BOT-TOKEN-HERE":
        raise SystemExit(
            "Set your bot token first: export BOT_TOKEN='123456:ABC-DEF...'"
        )

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    logger.info("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
  
