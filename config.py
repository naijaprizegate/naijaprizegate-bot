# ======================================
# config.py
# (Loads critical environment variables)
# ======================================
import os
from dotenv import load_dotenv

# Load .env when running locally
load_dotenv()

# ----------------------
# Telegram
# ----------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ Missing BOT_TOKEN env var")

ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")
if not ADMIN_USER_ID:
    raise RuntimeError("❌ Missing ADMIN_USER_ID env var")
ADMIN_USER_ID = int(ADMIN_USER_ID)

# ----------------------
# Flutterwave
# ----------------------
FLW_SECRET_HASH = os.getenv("FLW_SECRET_HASH")
if not FLW_SECRET_HASH:
    raise RuntimeError("❌ Missing FLW_SECRET_HASH env var")

AIRTIME_PROVIDER = os.getenv("AIRTIME_PROVIDER", "flutterwave")
