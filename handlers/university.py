# =========================================
# handlers/university.py
# =========================================

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)

from telegram.ext import (
    CallbackQueryHandler,
    ContextTypes,
)

from university_loader import (
    get_university_categories,
    get_university_category_by_code,
    get_university_subjects_by_category,
)


# =========================================
# WELCOME TEXT
# =========================================
def build_welcome_text():

    return (
        "🎓 *University Tutorials*\n\n"
        "Learn, practice, and test yourself on Year 1 university courses.\n\n"
        "Choose a category below."
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
                callback_data=f"uni_cat::{category['code']}"
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "🏠 Back to Main Menu",
            callback_data="menu:main"
        )
    ])

    return InlineKeyboardMarkup(rows)


# =========================================
# UNIVERSITY START HANDLER
# =========================================
async def university_start_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not query:
        return

    await query.answer()

    text = build_welcome_text()

    markup = make_categories_keyboard()

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

# =========================================
# CATEGORY HANDLER
# =========================================
async def university_category_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not query:
        return

    await query.answer()

    try:

        _, category_code = query.data.split("::", 1)

    except Exception:

        return await query.answer(
            "⚠️ Invalid category.",
            show_alert=True
        )

    category = get_university_category_by_code(
        category_code
    )

    if not category:

        return await query.answer(
            "⚠️ Category not found.",
            show_alert=True
        )

    subjects = get_university_subjects_by_category(
        category_code
    )

    rows = []

    for subject in subjects:

        rows.append([
            InlineKeyboardButton(
                subject["name"],
                callback_data=f"uni_sub::{subject['code']}"
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "🔙 Back",
            callback_data="uni_start"
        )
    ])

    markup = InlineKeyboardMarkup(rows)

    text = (
        f"📘 *{category['name']}*\n\n"
        "Choose a subject below."
    )

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

# -----------------------------------------
# REGISTER HANDLERS
# -----------------------------------------
def register_handlers(application):

    application.add_handler(
        CallbackQueryHandler(
            university_start_handler,
            pattern=r"^uni_start$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            university_category_handler,
            pattern=r"^uni_cat::"
        )
    )
