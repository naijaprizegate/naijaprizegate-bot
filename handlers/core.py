#=========================================================
# handlers/core.py
#=========================================================
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from helpers import md_escape, get_or_create_user, is_admin

# ---------------------------------------------------------
# /start handler (with optional referral arg)
# ---------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    # Create user in DB (and log referral if ?start=<tg_id>)
    await get_or_create_user(user.id, user.username, referred_by=args[0] if args else None)

    text = (
        f"👋 Hey *{md_escape(user.first_name)}*!\n\n"
        "Welcome to *NaijaPrizeGate * 🎰\n\n"
        "your golden ticket to daily wins 💸🔥\n\n"
        "You can become the *winner* of an *iPhone 16 pro max*\n\n"
        "Here’s how you unlock the gate:\n"
        "✨ `Try Luck` → Spin now & feel the thrill\n"
        "💳 `Buy` → Load up more spins (paid tries)\n"
        "🎁 `Free` → Earn bonus spins (invite & win)\n"
        "📊 `/mytries` → See your balance of chances\n\n"
        "⚡ Every spin counts towards the *Jackpot*\n"
        "…and someone *will* take it home 👑\n\n"
        "Ready? 🎯 Tap *Try Luck* and let’s roll!"
    )

    keyboard = [
        [InlineKeyboardButton("🎰 Try Luck", callback_data="tryluck")],
        [InlineKeyboardButton("💳 Buy Tries", callback_data="buy")],
        [InlineKeyboardButton("🎁 Free Tries", callback_data="free")]
    ]

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="MarkdownV2"
    )


# ---------------------------------------------------------
# /help handler
# ---------------------------------------------------------
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🆘 *Need a quick tour?* \n\n"
        "NaijaPrizeGate 🎰 is your gateway to *daily wins* 💸.\n\n"
        "Here’s your control panel:\n"
        "• `/start` → begin or refresh menu\n"
        "✨ `Try Luck` → Spin the wheel, feel the thrill\n"
        "💳 `Buy` → Load up paid spins & chase the jackpot\n"
        "🎁 `Free` → Earn bonus spins (invite friends = more chances)\n"
        "📊 `/mytries` → Track your spin balance\n"
        "🏆 Jackpot → Every spin moves us closer to the big win 🔥\n\n"
        "👉 Don’t just stand at the gate… *spin your way through!* 🚀"
        "Hit it and be the next winner 🎉"
    )
    await update.message.reply_text(text, parse_mode="MarkdownV2")


# ---------------------------------------------------------
# /mytries handler
# ---------------------------------------------------------
async def mytries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_user = await get_or_create_user(user.id, user.username)

    text = (
        f"🧮 *Your Tries*\n\n"
        f"• Paid: `{db_user.paid_tries}`\n"
        f"• Free: `{db_user.free_tries}`"
    )

    await update.message.reply_text(text, parse_mode="MarkdownV2")


# ---------------------------------------------------------
# Fallback text handler
# ---------------------------------------------------------
async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤔 I didn’t understand that.\n"
        "Use the menu buttons or try `/help`."
    )
    await update.message.reply_text(text, parse_mode="MarkdownV2")


# ---------------------------------------------------------
# Register handlers
# ---------------------------------------------------------
def register_handlers(application):
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("mytries", mytries))
    # fallback = any text message not handled
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback))
