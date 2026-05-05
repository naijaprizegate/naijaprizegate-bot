# =========================================================
# handlers/university.py
# =========================================================
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from university_loader import get_university_categories
from university_loader import get_university_category_by_code, get_university_subject_by_code
from university_loader import get_university_subject_by_code, get_university_subject_topics


# ------------------------
# Start University Section
# ------------------------
async def university_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    text = (
        "🎓 *University Subjects*\n\n"
        "Welcome to University Tutorials.\n"
        "Learn, practice, and test yourself on Year 1 courses.\n\n"
        "*What would you like to do?*"
    )

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 Tutorials", callback_data="uni_tutorials")],
        [InlineKeyboardButton("⏱ Timed Test", callback_data="uni_timed_test")],
        [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
    ])

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

# ----------------------------------------
# University Tutorials
# ----------------------------------------
async def university_tutorials_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    categories = get_university_categories()

    if not categories:
        return await query.answer("⚠️ No categories available.", show_alert=True)

    rows = []
    for cat in categories:
        rows.append([
            InlineKeyboardButton(
                cat["name"],
                callback_data=f"uni_cat::{cat['code']}"
            )
        ])

    # Add back button
    rows.append([
        InlineKeyboardButton("🔙 Back", callback_data="uni_start")
    ])

    markup = InlineKeyboardMarkup(rows)

    text = "📚 *Tutorial Categories*\n\nSelect a category:"

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

# -----------------------------------
# University Category Handler
# ----------------------------------
async def university_category_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, category_code = query.data.split("::", 1)
    except Exception:
        return await query.answer("⚠️ Invalid category.", show_alert=True)

    category = get_university_category_by_code(category_code)
    if not category:
        return await query.answer("⚠️ Category not found.", show_alert=True)

    subject_codes = category.get("subjects") or []

    rows = []
    for code in subject_codes:
        subject = get_university_subject_by_code(code)
        if subject:
            rows.append([
                InlineKeyboardButton(
                    subject["name"],
                    callback_data=f"uni_sub::{code}"
                )
            ])

    # Back button
    rows.append([
        InlineKeyboardButton("🔙 Back", callback_data="uni_tutorials")
    ])

    markup = InlineKeyboardMarkup(rows)

    text = f"📘 *{category['name']}*\n\nSelect a subject:"

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

# ------------------------------------
# University Subject Handler
# ------------------------------------
async def university_subject_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, subject_code = query.data.split("::", 1)
    except Exception:
        return await query.answer("⚠️ Invalid subject.", show_alert=True)

    subject = get_university_subject_by_code(subject_code)
    if not subject:
        return await query.answer("⚠️ Subject not found.", show_alert=True)

    topics = get_university_subject_topics(subject_code)

    rows = []
    for topic in topics:
        rows.append([
            InlineKeyboardButton(
                topic["title"],
                callback_data=f"uni_topic::{topic['code']}"
            )
        ])

    # Back button → back to category
    rows.append([
        InlineKeyboardButton("🔙 Back", callback_data=f"uni_cat::{subject['category_code']}")
    ])

    markup = InlineKeyboardMarkup(rows)

    text = f"📖 *{subject['name']}*\n\nSelect a topic:"

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

    application.add_handler(
        CallbackQueryHandler(university_tutorials_handler, pattern=r"^uni_tutorials$")
    )

    application.add_handler(
        CallbackQueryHandler(university_category_handler, pattern=r"^uni_cat::")
    )

    application.add_handler(
        CallbackQueryHandler(university_subject_handler, pattern=r"^uni_sub::")
    )

