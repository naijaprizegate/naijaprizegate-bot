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
    get_university_subject_by_code,
    get_university_topics,
    load_university_topic_questions,
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

# =========================================
# SUBJECT HANDLER
# =========================================
async def university_subject_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not query:
        return

    await query.answer()

    try:

        _, subject_code = query.data.split("::", 1)

    except Exception:

        return await query.answer(
            "⚠️ Invalid subject.",
            show_alert=True
        )

    subject = get_university_subject_by_code(
        subject_code
    )

    if not subject:

        return await query.answer(
            "⚠️ Subject not found.",
            show_alert=True
        )

    category_code = subject["category_code"]

    topics = get_university_topics(
        category_code,
        subject_code,
    )

    rows = []

    for topic in topics:

        rows.append([
            InlineKeyboardButton(
                topic["title"],
                callback_data=f"uni_topic::{topic['id']}"
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "🔙 Back",
            callback_data=f"uni_cat::{category_code}"
        )
    ])

    markup = InlineKeyboardMarkup(rows)

    text = (
        f"📖 *{subject['name']}*\n\n"
        "Choose a topic below."
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

# =========================================
# SEND QUESTION
# =========================================
async def send_university_question(
    update,
    context,
):

    questions = context.user_data.get(
        "uni_questions",
        []
    )

    current_index = context.user_data.get(
        "uni_current_index",
        0
    )

    # ---------------------------------
    # END OF QUESTIONS
    # ---------------------------------
    if current_index >= len(questions):

        score = context.user_data.get(
            "uni_score",
            0
        )

        total = len(questions)

        text = (
            "🎉 *Practice Completed!*\n\n"
            f"✅ Score: {score}/{total}"
        )

        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Back to Topics",
                    callback_data=(
                        f"uni_sub::"
                        f"{context.user_data['uni_subject_code']}"
                    )
                )
            ]
        ])

        return await update.effective_message.reply_text(
            text=text,
            parse_mode="Markdown",
            reply_markup=markup,
        )

    # ---------------------------------
    # GET QUESTION
    # ---------------------------------
    question = questions[current_index]

    text = (
        f"❓ *Question {current_index + 1}*\n\n"
        f"{question['question']}\n\n"
    )

    for option_letter, option_text in question["options"].items():

        text += (
            f"*{option_letter}.* "
            f"{option_text}\n"
        )

    # ---------------------------------
    # ANSWER BUTTONS
    # ---------------------------------
    rows = []

    for option_letter in question["options"].keys():

        rows.append([
            InlineKeyboardButton(
                option_letter,
                callback_data=(
                    f"uni_ans::{option_letter}"
                )
            )
        ])

    markup = InlineKeyboardMarkup(rows)

    await update.effective_message.reply_text(
        text=text,
        parse_mode="Markdown",
        reply_markup=markup,
    )

# =========================================
# TOPIC HANDLER
# =========================================
async def university_topic_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not query:
        return

    await query.answer()

    try:

        _, topic_id = query.data.split("::", 1)

    except Exception:

        return await query.answer(
            "⚠️ Invalid topic.",
            show_alert=True
        )

    category_code = None
    subject_code = None

    # ---------------------------------
    # FIND SUBJECT + CATEGORY
    # ---------------------------------
    for category in get_university_categories():

        subjects = get_university_subjects_by_category(
            category["code"]
        )

        for subject in subjects:

            topics = get_university_topics(
                category["code"],
                subject["code"],
            )

            if any(
                topic["id"] == topic_id
                for topic in topics
            ):

                category_code = category["code"]
                subject_code = subject["code"]

                break

    if not category_code or not subject_code:

        return await query.answer(
            "⚠️ Topic not found.",
            show_alert=True
        )

    # ---------------------------------
    # LOAD QUESTIONS
    # ---------------------------------
    questions = load_university_topic_questions(
        category_code,
        subject_code,
        topic_id,
    )

    if not questions:

        return await query.answer(
            "⚠️ No questions found.",
            show_alert=True
        )

    # ---------------------------------
    # SAVE SESSION
    # ---------------------------------
    context.user_data["uni_questions"] = questions

    context.user_data["uni_current_index"] = 0

    context.user_data["uni_score"] = 0

    context.user_data["uni_topic_id"] = topic_id

    context.user_data["uni_subject_code"] = (
        subject_code
    )

    context.user_data["uni_category_code"] = (
        category_code
    )

    # ---------------------------------
    # SEND QUESTION 1
    # ---------------------------------
    await send_university_question(
        update,
        context,
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

    application.add_handler(
        CallbackQueryHandler(
            university_subject_handler,
            pattern=r"^uni_sub::"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            university_topic_handler,
            pattern=r"^uni_topic::"
        )
    )
