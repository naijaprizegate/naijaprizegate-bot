# =========================================================
# handlers/university.py
# =========================================================


from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    CommandHandler,
    CallbackQueryHandler,
)

from university_loader import (
    get_university_categories,
)


# =========================================
# WELCOME TEXT
# =========================================
def build_welcome_text():
    return (
        "🎓 *Welcome to University Subjects Tutorials*\n\n"
        "Learn, practice, and test yourself on Year 1 university courses.\n\n"
        "This section helps you build strong understanding through topic-based practice.\n\n"
        "*Practice Mode*\n"
        "Choose a category → subject → topic → answer questions.\n\n"
        "Choose a category below:"
    )


# =========================================
# CATEGORY KEYBOARD
# =========================================
def make_categories_keyboard():
    rows = []

    categories = get_university_categories()

    for category in categories:
        rows.append([
            InlineKeyboardButton(
                category["name"],
                callback_data=f'us_cat_{category["code"]}'
            )
        ])

    return InlineKeyboardMarkup(rows)


# =========================================
# MAIN HANDLER
# =========================================
async def university_handler(update, context):

    if update.callback_query:
        await update.callback_query.answer()

    await update.effective_message.reply_text(
        build_welcome_text(),
        parse_mode="Markdown",
        reply_markup=make_categories_keyboard(),
    )


# =========================================
# REGISTER HANDLERS
# =========================================
def register_handlers(app):

    app.add_handler(
        CommandHandler(
            "university",
            university_handler
        )
    )
