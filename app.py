import os
import random
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Enable logging (for debugging)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID")
PAY_LINK = os.getenv("PAY_LINK")

# Internal counter
try_counter = 0
WIN_THRESHOLD = 14600  # One winner every 14,600 tries

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎉 Welcome to *NaijaPrizeGate!* 🎉\n\n"
        "Pay ₦500 to try your luck for an iPhone 16 Pro Max!\n\n"
        "👉 Use /pay to get payment link\n"
        "👉 Use /tryluck after payment\n\n"
        "Good luck! 🍀",
        parse_mode="Markdown"
    )

# Pay command
async def pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"💳 Click below to pay ₦500:\n{PAY_LINK}\n\n"
        "After payment, return and type /tryluck 🎯"
    )

# Try luck command
async def tryluck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global try_counter
    try_counter += 1

    # 🎰 Spinning animation (just text effect)
    await update.message.reply_text("🎰 Spinning...")
    
    if try_counter >= WIN_THRESHOLD:
        # Winner!
        try_counter = 0
        code = f"{random.randint(1000,9999)}-{random.randint(1000,9999)}"
        await update.message.reply_text(
            f"🎉 CONGRATULATIONS! You WON the iPhone 16 Pro Max! 🎉\n\n"
            f"Your Winner Code: *{code}*\n\n"
            "📦 Please send your *Name, Phone, and Address* to the admin.",
            parse_mode="Markdown"
        )
        # Notify Admin
        if ADMIN_USER_ID:
            await context.bot.send_message(
                chat_id=ADMIN_USER_ID,
                text=f"✅ WINNER ALERT!\n\nUser: @{update.effective_user.username}\nID: {update.effective_user.id}"
            )
    else:
        await update.message.reply_text("❌ Sorry, not a winner this time.\nTry again or share our page for a bonus! 🎁")

# Admin check counter
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) == str(ADMIN_USER_ID):
        await update.message.reply_text(f"📊 Current Counter: {try_counter}/{WIN_THRESHOLD}")
    else:
        await update.message.reply_text("⛔ You are not authorized.")

# Run bot
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pay", pay))
    app.add_handler(CommandHandler("tryluck", tryluck))
    app.add_handler(CommandHandler("stats", stats))

    app.run_polling()

if __name__ == "__main__":
    main()
