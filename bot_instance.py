# ===================================================
# bot_instance.py
# ===================================================
import os
from telegram import Bot
from telegram.ext import CallbackQueryHandler, MessageHandler, filters
from telegram.ext import Application

from services.airtime_service import (
    handle_claim_airtime_button,
    handle_airtime_claim_phone,
)


BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

bot = Bot(token=BOT_TOKEN)

BOT_USERNAME = os.getenv("BOT_USERNAME", "NaijaPrizeGateBot") 

application = Application.builder().token(BOT_TOKEN).build()

# Airtime Claim Handlers
application.add_handler(
    CallbackQueryHandler(handle_claim_airtime_button, pattern=r"^claim_airtime:")
)

# Phone entry handler (must always be below button handler)
application.add_handler(
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_airtime_claim_phone)
)
