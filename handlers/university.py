# =========================================================
# handlers/university.py
# =========================================================
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from university_loader import get_university_categories


# ================================
# Start University Section
# ================================
async def university_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    categories = get_university_categories()

    if not categories:
        return await query.answer("⚠️ No university categories available.", show_alert=True)

    rows = []
    for cat in categories:
        rows.append([
            InlineKeyboardButton(
                cat["name"],
                callback_data=f"uni_cat::{cat['code']}"
            )
        ])

    markup = InlineKeyboardMarkup(rows)

    text = "🎓 *University Tutorials*\n\nSelect a category:"

    try:
        await query.edit_message_text(
            text=text,
            parse_mode="Markdown",
            reply_markup=markup,
        )
    except Exception:
        await query.message.reply_text(
            text=text,
            parse_mode="Markdown",
            reply_markup=markup,
        )


# ====================================================================
# Register Handlers
# ====================================================================
def register_handlers(application):
    application.add_handler(
        CallbackQueryHandler(university_start_handler, pattern=r"^uni_start$")
    )
