# ===============================================================
# logging_setup.py
# ===============================================================
import logging
import os
import sys
import re
import html
import traceback

import sentry_sdk
from telegram.ext import ContextTypes
from telegram import Update

ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))

# ------------------------------------------------
# Environment & log level
# ------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
numeric_level = getattr(logging, LOG_LEVEL, logging.INFO)
SENTRY_DSN = os.getenv("SENTRY_DSN")  # optional, leave empty if not using
ENVIRONMENT = os.getenv("ENVIRONMENT", "production")


# ------------------------------------------------
# 🔒 Secret Filter to hide tokens / API keys
# ------------------------------------------------
class SecretFilter(logging.Filter):
    TOKEN_PATTERN = re.compile(r"\b\d{9,10}:[A-Za-z0-9_-]{35,}\b")
    KEY_PATTERN = re.compile(
        r"(?:secret|token|key|password|api)[^\s=:'\"]*['\"]?[:=]['\"]?([\w-]+)['\"]?",
        re.IGNORECASE,
    )

    def filter(self, record):
        msg = str(record.msg)
        msg = self.TOKEN_PATTERN.sub("[SECRET]", msg)
        msg = self.KEY_PATTERN.sub("[REDACTED]", msg)
        record.msg = msg

        if record.args:
            record.args = tuple(
                self.TOKEN_PATTERN.sub("[SECRET]", str(a))
                for a in record.args
            )

        return True


# ------------------------------------------------
# Configure root logger
# ------------------------------------------------
formatter = logging.Formatter(
    "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    "%Y-%m-%d %H:%M:%S",
)

handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(formatter)
handler.addFilter(SecretFilter())

logger = logging.getLogger("NaijaPrizeGateBot")
logger.setLevel(numeric_level)

# Prevent duplicate handlers if module is imported more than once
if not logger.handlers:
    logger.addHandler(handler)

logger.propagate = False

# Apply SecretFilter globally to all other loggers
for name in logging.root.manager.loggerDict:
    logging.getLogger(name).addFilter(SecretFilter())


# ------------------------------------------------
# Ensure uvicorn/gunicorn logs flow through this formatter
# ------------------------------------------------
for noisy in (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "gunicorn",
    "gunicorn.error",
    "gunicorn.access",
):
    logging.getLogger(noisy).handlers = []
    logging.getLogger(noisy).propagate = True


# ------------------------------------------------
# Optional: Initialize Sentry
# ------------------------------------------------
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        traces_sample_rate=1.0,
        environment=ENVIRONMENT,
    )

logger.info("✅ Secure logger initialized (tokens masked from output).")


# ------------------------------------------------
# Telegram error handler
# ------------------------------------------------
async def tg_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles exceptions in Telegram updates safely:
    - Logs the error locally
    - Sends to Sentry (if configured)
    - Notifies admin safely on Telegram
    """
    error = context.error

    # 1) Log locally
    if error:
        logger.error(
            "Telegram update failed: %s",
            update,
            exc_info=(type(error), error, error.__traceback__),
        )
    else:
        logger.error("Telegram update failed: %s", update)

    # 2) Send to Sentry if configured
    if SENTRY_DSN and error:
        sentry_sdk.capture_exception(error)

    # 3) Notify admin on Telegram
    if ADMIN_USER_ID and isinstance(update, Update):
        try:
            update_summary = html.escape(repr(update))[:900]

            if error:
                error_text = "".join(
                    traceback.format_exception(type(error), error, error.__traceback__)
                )
            else:
                error_text = "Unknown error"

            safe_error_text = html.escape(error_text)[:2500]

            message = (
                "⚠️ <b>Telegram Exception Occurred</b>\n\n"
                f"<b>Update:</b>\n<pre>{update_summary}</pre>\n\n"
                f"<b>Error:</b>\n<pre>{safe_error_text}</pre>"
            )

            await context.bot.send_message(
                chat_id=ADMIN_USER_ID,
                text=message,
                parse_mode="HTML",
            )

        except Exception as inner_exc:
            logger.warning("Failed to notify admin: %s", inner_exc)


