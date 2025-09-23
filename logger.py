# ===========================================================
# logger.py
# ===========================================================
import logging
import traceback
from telegram import Update
from telegram.ext import ContextTypes

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Or use LOG_LEVEL from env
handler = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)

# Your Admin Telegram ID
from os import getenv
ADMIN_ID = int(getenv("ADMIN_ID", 0))

async def tg_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles exceptions in Telegram updates safely.
    Logs the error, optionally sends to Sentry, and notifies admin.
    """
    # 1️⃣ Log the exception
    logger.error("Exception in Telegram update:\n%s", "".join(traceback.format_exception(None, context.error, context.error.__traceback__)))

    # 2️⃣ Optionally notify admin on Telegram
    try:
        if ADMIN_ID and context.bot:
            msg = f"⚠️ Exception in bot:\n{type(context.error).__name__}: {context.error}"
            await context.bot.send_message(chat_id=ADMIN_ID, text=msg)
    except Exception as e:
        logger.error("Failed to notify admin: %s", e)

    # 3️⃣ Optional: Sentry capture (if you use it)
    try:
        import sentry_sdk
        sentry_sdk.capture_exception(context.error)
    except ImportError:
        pass
