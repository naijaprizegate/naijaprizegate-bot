# ===============================================================
# logging_setup.py
# ===============================================================
import logging
import os
import sentry_sdk

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
SENTRY_DSN = os.getenv("SENTRY_DSN")  # optional, leave empty if not using

# 1️⃣ Configure Python logging
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("tryluck_bot")

# 2️⃣ Optional: Initialize Sentry
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=1.0,  # adjust sampling as needed
        environment=os.getenv("ENVIRONMENT", "production"),
    )

# 3️⃣ Telegram error handler
from telegram.ext import Application, ContextTypes
from telegram import Update

async def tg_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    try:
        raise context.error
    except Exception as e:
        # Log locally
        logger.exception(f"Telegram update failed: {update}")
        
        # Send to Sentry if configured
        if SENTRY_DSN:
            sentry_sdk.capture_exception(e)
        
        # Notify admin if ADMIN_ID is set
        ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
        if ADMIN_ID and isinstance(update, Update):
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"⚠️ Exception occurred:\n<pre>{e}</pre>",
                    parse_mode="HTML",
                )
            except Exception as inner_exc:
                logger.warning(f"Failed to notify admin: {inner_exc}")
