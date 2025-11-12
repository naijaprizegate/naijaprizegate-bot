# =============================================================== 
# handlers/core.py
# ================================================================
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters
from helpers import md_escape, get_or_create_user, is_admin
from db import get_async_session
import re
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# /start handler (with optional referral arg)
# ---------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args

    async with get_async_session() as session:
        await get_or_create_user(
            session,
            tg_id=user.id,
            username=user.username,
        )
        # TODO: handle referral (your get_or_create_user doesn’t take referred_by currently)

    text = (
        f"👋 Hey *{md_escape(user.first_name)}*\\!\n\n"
        "Welcome to *NaijaPrizeGate* 🎰\n\n"
        "Your golden ticket to daily wins 💸🔥\n\n"
        "You can become the *winner* of:\n\n"
        "*📱 iPhone 16 Pro Max*\n\n"
        "*📱 iPhone 17 Pro Max*\n\n"
        "*📱 Samsung Galaxy Z flip 7*\n\n"
        "*📱 Samsung Galaxy S25 Ultra*\n\n"
        "⚡ Every spin counts towards the *Jackpot*\n"
        "…and someone *will* take it home 👑\n\n"
        "Ready\\? 🎯 Tap *Try Luck* and let’s roll\\!"
    )


    keyboard = [
        [InlineKeyboardButton("🎰 Try Luck", callback_data="tryluck")],
        [InlineKeyboardButton("💳 Buy Tries", callback_data="buy")],
        [InlineKeyboardButton("🎁 Free Tries", callback_data="free")],
        [InlineKeyboardButton("📊 Available Tries", callback_data="show_tries")]
    ]

    # 🧠 Check if called via a normal message (/start) or a callback (like "Cancel")
    if update.message:
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="MarkdownV2"
        )
    elif update.callback_query:
        query = update.callback_query
        await query.answer()
        await query.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="MarkdownV2"
        )


# ---------------------------------------------------------
# Callback: Return to Start (from Cancel button)
# ---------------------------------------------------------
from telegram.ext import CallbackQueryHandler

async def go_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles Cancel button — returns user to start screen"""
    query = update.callback_query
    await query.answer()

    # Try to delete the previous message to keep chat tidy
    try:
        await query.message.delete()
    except Exception as e:
        logger.warning(f"⚠️ Could not delete message: {e}")

    # Reuse your existing start() function to show the start menu again
    await start(update, context)

# ---------------------------------------------------------
# /help handler (auto-updates message if possible)
# ---------------------------------------------------------
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    text = (
        "🆘 *Need a quick tour?*\n\n"
        "*NaijaPrizeGate* 🎰 is your gateway to *daily wins* 💸\n\n"
        "Here’s your control panel:\n\n"
        "• `/start` → Begin or refresh menu\n\n"
        "• 🎰 *Try Luck* → Spin the wheel, feel the thrill\n\n"
        "• 💳 *Buy Tries* → Load up paid spins & chase the jackpot\n\n"
        "• 🎁 *Free Tries* → Earn bonus spins \\(invite friends \\= more chances\\)\n\n"
        "• 📊 *Available Tries* → Track your spin balance\n\n"
        "🏆 *Jackpot* → Every spin moves us closer to the big win 🔥\n\n"
        "👉 Don’t just stand at the gate… *spin your way through* 🚀\n\n"
        "⚡Hit it and be the next winner 🎉"
    )

    keyboard = [
        [InlineKeyboardButton("🎰 Try Luck", callback_data="tryluck")],
        [InlineKeyboardButton("💳 Buy Tries", callback_data="buy")],
        [InlineKeyboardButton("🎁 Free Tries", callback_data="free")],
        [InlineKeyboardButton("📊 Available Tries", callback_data="show_tries")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # 🧠 Detect whether it came from a message or a callback query
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        try:
            # 🧹 Edit existing message for a cleaner UX
            await query.message.edit_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode="MarkdownV2"
            )
        except Exception:
            # fallback if message can’t be edited (e.g., old message)
            await query.message.reply_text(
                text,
                reply_markup=reply_markup,
                parse_mode="MarkdownV2"
            )
    elif update.message:
        await update.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode="MarkdownV2"
        )

# ---------------------------------------------------------
# /mytries handler
# ---------------------------------------------------------
async def mytries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user
    logger.info(f"🔔 /mytries called by tg_id={tg_user.id}, username={tg_user.username}")

    async with get_async_session() as session:
        db_user = await get_or_create_user(session, tg_id=tg_user.id, username=tg_user.username)

        # 📊 Log what we actually fetched from DB
        logger.info(
            f"📊 /mytries fetched: db_user.id={db_user.id}, tg_id={db_user.tg_id}, "
            f"paid={db_user.tries_paid}, bonus={db_user.tries_bonus}"
        )

        # 🚨 Extra summary log if user has no tries
        if (db_user.tries_paid or 0) == 0 and (db_user.tries_bonus or 0) == 0:
            logger.warning(f"🚨 User {db_user.id} (tg_id={db_user.tg_id}) has NO tries left!")

        # Construct response text
        text = (
            f"🧮 *Your Tries*\n\n"
            f"• Paid: `{db_user.tries_paid or 0}`\n"
            f"• Free: `{db_user.tries_bonus or 0}`"
        )

    # Send reply (escape to avoid markdown issues)
    await update.message.reply_text(md_escape(text), parse_mode="MarkdownV2")

# ---------------------------------------------------------
# Fallback text handler
# ---------------------------------------------------------
async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤔 Sorry, I didn’t understand that\\.\n\n"
        "Use the menu buttons or try /help"
    )
    safe_text = md_escape(text)

    keyboard = [
        [InlineKeyboardButton("🎰 Try Luck", callback_data="tryluck")],
        [InlineKeyboardButton("💳 Buy Tries", callback_data="buy")],
        [InlineKeyboardButton("🎁 Free Tries", callback_data="free")],
        [InlineKeyboardButton("📊 Available Tries", callback_data="show_tries")]
    ]

    if update.message:  # User typed something
        await update.message.reply_text(
            safe_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="MarkdownV2",
        )
    elif update.callback_query:  # User pressed an inline button
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            safe_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="MarkdownV2",
        )

# ---------------------------------------------------------
# Register handlers (unified)
# ---------------------------------------------------------
def register_handlers(application):
    # /start command
    application.add_handler(CommandHandler("start", start))

    # 👋 greetings like hi, hello, hey
    greetings = filters.Regex(re.compile(
        r"^(hi|hello|hey|howdy|sup|good\s?(morning|afternoon|evening))",
        re.IGNORECASE
    ))
    application.add_handler(MessageHandler(greetings, start))

    # core commands
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("mytries", mytries))

    # fallback for unrecognized text
    application.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.Regex(r"^[0-9+ ]+$"),
        fallback
    )
)
