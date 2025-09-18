# =========================
# Step 1 — Imports & basic setup
# =========================
import os
import logging
import time
import ipaddress
import uuid
import random
import asyncio
from datetime import datetime, timedelta
from typing import Optional

# FastAPI (used for webhook endpoint)
from fastapi import FastAPI

# telegram helper for MarkdownV2 escaping (we'll use this to keep messages safe)
from telegram.helpers import escape_markdown

# -------------------------
# Logging
# -------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("naijaprizegate")

# -------------------------
# FastAPI app (webhook receiver)
# -------------------------
api = FastAPI()

@api.get("/")
async def root():
    return {"status": "ok", "message": "NaijaPrizeGate bot is running 🚀"}

# -------------------------
# Environment / configuration
# -------------------------
# Note: set these in Render (Environment) or locally before running
BOT_TOKEN = os.getenv("BOT_TOKEN")                        # required to connect to Telegram
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./db.sqlite3")
PUBLIC_CHANNEL = os.getenv("PUBLIC_CHANNEL", "@NaijaPrizeGateWinners")
WIN_THRESHOLD = int(os.getenv("WIN_THRESHOLD", "14600"))
PAYMENT_EXPIRE_HOURS = int(os.getenv("PAYMENT_EXPIRE_HOURS", "2"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")         # path secret for webhook
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL", "")  # e.g. https://my-service.onrender.com

# Friendly warnings (we don't crash here, so you can run linters locally)
if not BOT_TOKEN:
    logger.warning("BOT_TOKEN not set — the bot cannot connect to Telegram until you provide it.")
if not WEBHOOK_SECRET:
    logger.warning("WEBHOOK_SECRET not set — webhook endpoint will be unprotected until you set this.")
if not RENDER_EXTERNAL_URL:
    logger.info("RENDER_EXTERNAL_URL not set. Webhook won't be auto-registered on startup.")

# -------------------------
# MarkdownV2 escaping helper
# -------------------------
def md_escape(value: Optional[str]) -> str:
    """
    Return the given value escaped for MarkdownV2 using telegram.helpers.escape_markdown.
    Accepts None and returns an empty string in that case.
    Use this for any dynamic text inserted into parse_mode=MARKDOWN_V2 messages.
    """
    s = "" if value is None else str(value)
    # escape_markdown handles the heavy lifting; ensure we explicitly pass version=2 where used later
    return escape_markdown(s, version=2)
