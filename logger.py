# ===========================================================
# logger.py
# ===========================================================
import traceback
from telegram import Update
from telegram.ext import ContextTypes

# Import central logger from logging_setup
from logging_setup import logger

from os import getenv
ADMIN_USER_ID = int(getenv("ADMIN_USER_ID", 0))

async def tg_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles exceptions in Telegram updates safely.
    Logs the error, optionally sends to Sentry, and notifies admin.
    """
    # 1️⃣ Log the exception
    logger.error(
        "Exception in Telegram update:\n%s",
        "".join(traceback.format_exception(None, context.error, context.error.__traceback__))
    )

    # 2️⃣ Optionally notify admin on Telegram
    try:
        if ADMIN_USER_ID and context.bot:
            msg = f"⚠️ Exception in bot:\n{type(context.error).__name__}: {context.error}"
            await context.bot.send_message(chat_id=ADMIN_USER_ID, text=msg)
    except Exception as e:
        logger.error("Failed to notify admin: %s", e)

    # 3️⃣ Optional: Sentry capture (if installed + configured)
    try:
        import sentry_sdk
        sentry_sdk.capture_exception(context.error)
    except ImportError:
        pass
