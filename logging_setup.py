# ===============================================================
# logging_setup.py
# ===============================================================
import logging
import os
import sys
import re
import sentry_sdk

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
        re.IGNORECASE
    )

    def filter(self, record):
        msg = str(record.msg)
        msg = self.TOKEN_PATTERN.sub("[SECRET]", msg)
        msg = self.KEY_PATTERN.sub("[REDACTED]", msg)
        record.msg = msg
        if record.args:
            record.args = tuple(self.TOKEN_PATTERN.sub("[SECRET]", str(a)) for a in record.args)
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
logger.addHandler(handler)
logger.propagate = False

# Apply SecretFilter globally to all other loggers
for name in logging.root.manager.loggerDict:
    logging.getLogger(name).addFilter(SecretFilter())

# ------------------------------------------------
# Ensure uvicorn/gunicorn logs flow through this formatter
# ------------------------------------------------
for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access",
              "gunicorn", "gunicorn.error", "gunicorn.access"):
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
from telegram.ext import ContextTypes, Application
from telegram import Update

ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", 0))

async def tg_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles exceptions in Telegram updates safely:
    - Logs the error locally (with secrets masked)
    - Sends to Sentry (if configured)
    - Notifies admin (if ADMIN_USER_ID set)
    """
    try:
        raise context.error
    except Exception as e:
        # 1️⃣ Log locally
        logger.exception(f"Telegram update failed: {update}")

        # 2️⃣ Send to Sentry if configured
        if SENTRY_DSN:
            sentry_sdk.capture_exception(e)

        # 3️⃣ Notify admin on Telegram
        if ADMIN_USER_ID and isinstance(update, Update):
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_USER_ID,
                    text=f"⚠️ Exception occurred:\n<pre>{e}</pre>",
                    parse_mode="HTML",
                )
            except Exception as inner_exc:
                logger.warning(f"Failed to notify admin: {inner_exc}")
