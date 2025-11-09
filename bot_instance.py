# ===================================================
# bot_instance.py
# ===================================================
import os
from telegram import Bot

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

bot = Bot(token=BOT_TOKEN)

BOT_USERNAME = os.getenv("BOT_USERNAME", "NaijaPrizeGateBot") 
