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
# App branding
# ----------------------
APP_LOGO_URL = os.getenv(
    "APP_LOGO_URL",
    "https://raw.githubusercontent.com/naijaprizegate/naijaprizegate-bot/main/Naijaprizegate%20Logo.png",  # safe fallback
)

# ----------------------
# Flutterwave
# ----------------------
FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY")
if not FLW_SECRET_KEY:
    raise RuntimeError("❌ Missing FLW_SECRET_KEY env var")

FLW_SECRET_HASH = os.getenv("FLW_SECRET_HASH")
if not FLW_SECRET_HASH:
    raise RuntimeError("❌ Missing FLW_SECRET_HASH env var")

WEBHOOK_REDIRECT_URL = os.getenv("WEBHOOK_REDIRECT_URL")
if not WEBHOOK_REDIRECT_URL:
    raise RuntimeError("❌ Missing WEBHOOK_REDIRECT_URL env var")

# ----------------------
# Aliases (DO NOT REMOVE)
# ----------------------
# These prevent import mismatches across services
FLUTTERWAVE_SECRET_KEY = FLW_SECRET_KEY
FLUTTERWAVE_REDIRECT_URL = WEBHOOK_REDIRECT_URL

# ----------------------
# Other
# ----------------------
AIRTIME_PROVIDER = os.getenv("AIRTIME_PROVIDER", "flutterwave")

