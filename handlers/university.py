# =========================================================
# handlers/university.py
# =========================================================
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from university_loader import (
    get_university_categories,
    get_university_category_by_code,
    get_university_subject_by_code,
    get_university_subject_topics,
    get_university_topic_by_code,
    load_university_topic_content,
)



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

# ---------------------------------------
# University Topic Handler
# --------------------------------------
async def university_topic_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, topic_code = query.data.split("::", 1)
    except Exception:
        return await query.answer("⚠️ Invalid topic.", show_alert=True)

    topic = get_university_topic_by_code(topic_code)
    if not topic:
        return await query.answer("⚠️ Topic not found.", show_alert=True)

    subject_code = topic["subject_code"]

    content = load_university_topic_content(subject_code, topic_code)
    if not content:
        return await query.answer("⚠️ Lesson content not found.", show_alert=True)

    intro = content.get("intro", "No introduction available.")

    # Save for next step
    context.user_data["uni_subject_code"] = subject_code
    context.user_data["uni_topic_code"] = topic_code
    context.user_data["uni_step_index"] = 0

    text = (
        f"📖 *{topic['title']}*\n\n"
        f"{intro}\n\n"
        "Tap below to begin the lesson."
    )

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Start Lesson", callback_data="uni_start_lesson")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"uni_sub::{subject_code}")],
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


#-------------------------------------
# University Start Lesson Handler
# ------------------------------------
async def university_start_lesson_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    subject_code = context.user_data.get("uni_subject_code")
    topic_code = context.user_data.get("uni_topic_code")

    if not subject_code or not topic_code:
        return await query.answer("⚠️ Lesson data missing.", show_alert=True)

    content = load_university_topic_content(subject_code, topic_code)
    if not content:
        return await query.answer("⚠️ Lesson not found.", show_alert=True)

    sections = content.get("sections", [])
    if not sections:
        return await query.answer("⚠️ No lesson sections found.", show_alert=True)

    # 🔥 Convert sections → steps
    steps = []
    for sec in sections:
        text_parts = []

        for line in sec.get("explanation", []):
            text_parts.append(f"• {line}")

        if sec.get("examples"):
            text_parts.append("\n*Examples:*")
            for ex in sec["examples"]:
                text_parts.append(f"• {ex}")

        steps.append({
            "title": sec.get("title", "Lesson"),
            "content": "\n".join(text_parts)
        })

    context.user_data["uni_steps"] = steps
    context.user_data["uni_step_index"] = 0
    context.user_data["uni_phase"] = "lesson"

    step = steps[0]

    text = f"📘 *{step['title']}*\n\n{step['content']}"

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ Next", callback_data="uni_next_step")],
        [InlineKeyboardButton("❌ Exit", callback_data=f"uni_topic::{topic_code}")]
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


# -----------------------------------
# University Next Step Handler
# ----------------------------------
async def university_next_step_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    phase = context.user_data.get("uni_phase", "lesson")

    # -------------
    # LESSON PHASE
    # -------------
    if phase == "lesson":
        steps = context.user_data.get("uni_steps", [])
        index = context.user_data.get("uni_step_index", 0)

        index += 1

        if index >= len(steps):
            # 🔥 MOVE TO QUIZ
            context.user_data["uni_phase"] = "quiz"
            context.user_data["uni_quiz_index"] = 0

            subject_code = context.user_data.get("uni_subject_code")
            topic_code = context.user_data.get("uni_topic_code")

            content = load_university_topic_content(subject_code, topic_code)
            questions = content.get("check_questions", [])

            if not questions:
                # Skip to summary if no quiz
                context.user_data["uni_phase"] = "summary"
                return await show_summary(query, context, content)

            q = questions[0]

            text = f"❓ *{q['question']}*"

            rows = []
            for i, opt in enumerate(q["options"]):
                rows.append([
                    InlineKeyboardButton(opt, callback_data=f"uni_quiz_answer::{i}")
                ])

            markup = InlineKeyboardMarkup(rows)

            return await query.edit_message_text(
                text=text,
                parse_mode="Markdown",
                reply_markup=markup,
            )

        context.user_data["uni_step_index"] = index
        step = steps[index]

        text = f"📘 *{step['title']}*\n\n{step['content']}"

        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("⏭ Next", callback_data="uni_next_step")],
            [InlineKeyboardButton("❌ Exit", callback_data=f"uni_topic::{context.user_data.get('uni_topic_code')}")]
        ])

        return await query.edit_message_text(
            text=text,
            parse_mode="Markdown",
            reply_markup=markup,
        )

    # ------------------------
    # SUMMARY PHASE (fallback)
    # ------------------------
    if phase == "summary":
        subject_code = context.user_data.get("uni_subject_code")

        return await query.edit_message_text(
            text="🎉 *Lesson Completed!*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to Topics", callback_data=f"uni_sub::{subject_code}")]
            ])
        )

# -----------------------------------
# University Quiz Answer Handler
# ----------------------------------
async def university_quiz_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    try:
        _, selected_index = query.data.split("::", 1)
        selected_index = int(selected_index)
    except Exception:
        return

    subject_code = context.user_data.get("uni_subject_code")
    topic_code = context.user_data.get("uni_topic_code")

    content = load_university_topic_content(subject_code, topic_code)
    questions = content.get("check_questions", [])

    q_index = context.user_data.get("uni_quiz_index", 0)
    question = questions[q_index]

    correct_index = question["answer_index"]

    correct_option = question["options"][correct_index]

    if selected_index == correct_index:
        # ✅ POPUP
        await query.answer("✅ Correct!", show_alert=True)

        result_text = "✅ *Correct!*\n\n"
    else:
        # ❌ POPUP WITH CORRECT ANSWER
        await query.answer(
            f"❌ Wrong!\nCorrect answer:\n{correct_option}",
            show_alert=True,
        )

        result_text = (
            "❌ *Incorrect*\n\n"
            f"*Correct Answer:* {correct_option}\n\n"
        )

    result_text += f"*Explanation:*\n{question['explanation']}"

    # Move to next question
    q_index += 1
    context.user_data["uni_quiz_index"] = q_index

    if q_index >= len(questions):
        # 🔥 MOVE TO SUMMARY
        context.user_data["uni_phase"] = "summary"
        return await show_summary(query, context, content)

    next_q = questions[q_index]

    rows = []
    for i, opt in enumerate(next_q["options"]):
        rows.append([
            InlineKeyboardButton(opt, callback_data=f"uni_quiz_answer::{i}")
        ])

    markup = InlineKeyboardMarkup(rows)

    return await query.edit_message_text(
        text=result_text + f"\n\n❓ *{next_q['question']}*",
        parse_mode="Markdown",
        reply_markup=markup,
    )


# --------------------------
# Show Summary
# --------------------------
async def show_summary(query, context, content):
    summary = content.get("summary", [])

    text = "📌 *Summary*\n\n"
    for line in summary:
        text += f"• {line}\n"

    subject_code = context.user_data.get("uni_subject_code")

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Topics", callback_data=f"uni_sub::{subject_code}")]
    ])

    return await query.edit_message_text(
        text=text,
        parse_mode="Markdown",
        reply_markup=markup,
    )

# -----------------------------------------------------
# Register Handlers
# ----------------------------------------------------
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

    application.add_handler(
        CallbackQueryHandler(university_topic_handler, pattern=r"^uni_topic::")
    )

    application.add_handler(
        CallbackQueryHandler(university_start_lesson_handler, pattern=r"^uni_start_lesson$")
    )

    application.add_handler(
        CallbackQueryHandler(university_next_step_handler, pattern=r"^uni_next_step$")
    )

    application.add_handler(
        CallbackQueryHandler(university_quiz_answer_handler, pattern=r"^uni_quiz_answer::")
    )


