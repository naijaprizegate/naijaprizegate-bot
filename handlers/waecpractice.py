# ====================================================
# handlers/waecpractice.py
# ===================================================
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from helpers import md_escape
from waec_loader import (
    get_waec_subjects,
    get_waec_subject_by_code,
    get_waec_subject_topics,
    prepare_waec_topic_question_batch,
)

import math

TOPICS_PER_PAGE = 7


def make_waec_subject_keyboard():
    subjects = get_waec_subjects()
    rows = []
    row = []

    for subject in subjects:
        row.append(
            InlineKeyboardButton(
                subject["name"],
                callback_data=f"wp_subj_{subject['code']}"
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def make_waec_mode_keyboard(subject_code: str):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📚 By Topics", callback_data=f"wp_mode_topics_{subject_code}")],
            [InlineKeyboardButton("📝 Mock WAEC / NECO", callback_data=f"wp_mode_mock_{subject_code}")],
            [InlineKeyboardButton("⬅️ Back to Subjects", callback_data="waecneco:practice")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def make_waec_topics_keyboard(subject_code: str, page: int = 1):
    topics = get_waec_subject_topics(subject_code)
    total_topics = len(topics)
    total_pages = max(1, math.ceil(total_topics / TOPICS_PER_PAGE))

    page = max(1, min(page, total_pages))

    start = (page - 1) * TOPICS_PER_PAGE
    end = start + TOPICS_PER_PAGE
    page_topics = topics[start:end]

    rows = []

    for topic in page_topics:
        rows.append([
            InlineKeyboardButton(
                f"{topic['number']}. {topic['title']}",
                callback_data=f"wp_topic::{subject_code}::{topic['id']}"
            )
        ])

    nav_row = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton("◀ Prev", callback_data=f"wp_topicpage_{subject_code}_{page-1}")
        )
    if page < total_pages:
        nav_row.append(
            InlineKeyboardButton("Next ▶", callback_data=f"wp_topicpage_{subject_code}_{page+1}")
        )
    if nav_row:
        rows.append(nav_row)

    rows.append([InlineKeyboardButton("⬅️ Back to Mode", callback_data=f"wp_back_mode_{subject_code}")])
    rows.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")])

    return InlineKeyboardMarkup(rows), page, total_pages


def make_waec_topic_access_keyboard_for_subject(
    subject_code: str,
    has_free_trial: bool,
    has_paid_credits: bool,
):
    rows = []

    if has_free_trial:
        rows.append([InlineKeyboardButton("🎁 Use Free Trial (5 Questions)", callback_data="wp_start_free")])

    if has_paid_credits:
        rows.append([InlineKeyboardButton("✅ Use Paid Credits", callback_data="wp_use_paid")])

    rows.extend([
        [InlineKeyboardButton("💳 Get 50 Questions — ₦100", callback_data="wp_buy_50")],
        [InlineKeyboardButton("💳 Get 100 Questions — ₦200", callback_data="wp_buy_100")],
        [InlineKeyboardButton("💳 Get 150 Questions — ₦300", callback_data="wp_buy_150")],
        [InlineKeyboardButton("💳 Get 200 Questions — ₦400", callback_data="wp_buy_200")],
        [InlineKeyboardButton("⬅️ Back to Topics", callback_data=f"wp_topicpage_{subject_code}_1")],
        [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
    ])

    return InlineKeyboardMarkup(rows)


def make_after_answer_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➡️ Next", callback_data="wp_next")],
            [InlineKeyboardButton("📖 Answer Details", callback_data="wp_details")],
            [InlineKeyboardButton("🏠 End Practice", callback_data="wp_end_session")],
        ]
    )


def make_after_details_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➡️ Next", callback_data="wp_next")],
            [InlineKeyboardButton("🏠 End Practice", callback_data="wp_end_session")],
        ]
    )


def build_waec_welcome_text() -> str:
    return (
        "📘 *Welcome to WAEC / NECO Practice*\n\n"
        "This section helps you practise for WAEC and NECO just like JAMB Practice\\.\n\n"
        "Please choose a subject below\\."
    )


def clear_waec_session_state(context: ContextTypes.DEFAULT_TYPE):
    keys_to_clear = [
        "wp_subject_code",
        "wp_mode",
        "wp_topic_id",
        "wp_topic_page",
        "wp_question_batch",
        "wp_question_ids",
        "wp_current_index",
        "wp_session_target",
        "wp_correct_count",
        "wp_wrong_count",
        "wp_current_question",
        "wp_answered_current",
        "wp_served_question_ids",
        "wp_last_selected_option",
        "wp_last_correct_option",
    ]

    for key in keys_to_clear:
        context.user_data.pop(key, None)


async def waecpractice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass

    context.user_data["wp_subject_code"] = None
    context.user_data["wp_mode"] = None
    context.user_data["wp_topic_id"] = None
    context.user_data["wp_topic_page"] = 1

    await update.effective_message.reply_text(
        build_waec_welcome_text(),
        parse_mode="MarkdownV2",
        reply_markup=make_waec_subject_keyboard(),
    )


async def waec_subject_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, _, subject_code = query.data.split("_", 2)
    except Exception:
        return await query.message.reply_text("⚠️ Invalid subject selection.")

    subject = get_waec_subject_by_code(subject_code)
    if not subject:
        return await query.message.reply_text("⚠️ Subject not found or inactive.")

    context.user_data["wp_subject_code"] = subject_code
    context.user_data["wp_mode"] = None
    context.user_data["wp_topic_id"] = None
    context.user_data["wp_topic_page"] = 1

    safe_subject_name = md_escape(str(subject["name"]))

    await query.message.reply_text(
        f"📘 *You selected:* {safe_subject_name}\n\n"
        "How would you like to practice\\?",
        parse_mode="MarkdownV2",
        reply_markup=make_waec_mode_keyboard(subject_code),
    )


async def waec_mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data = query.data

    if data.startswith("wp_mode_topics_"):
        subject_code = data.replace("wp_mode_topics_", "", 1)
        context.user_data["wp_subject_code"] = subject_code
        context.user_data["wp_mode"] = "topic_practice"
        context.user_data["wp_topic_page"] = 1

        subject = get_waec_subject_by_code(subject_code)
        kb, page, total_pages = make_waec_topics_keyboard(subject_code, 1)

        safe_subject_name = md_escape(str(subject["name"]))
        safe_page = md_escape(str(page))
        safe_total_pages = md_escape(str(total_pages))

        return await query.message.reply_text(
            f"📚 *{safe_subject_name} Topics*\n\n"
            f"Choose a topic below\\.\n"
            f"_Page {safe_page} of {safe_total_pages}_",
            parse_mode="MarkdownV2",
            reply_markup=kb,
        )

    if data.startswith("wp_mode_mock_"):
        subject_code = data.replace("wp_mode_mock_", "", 1)
        subject = get_waec_subject_by_code(subject_code)
        safe_subject_name = md_escape(str(subject["name"])) if subject else md_escape(subject_code)

        return await query.message.reply_text(
            f"📝 *Mock WAEC / NECO*\n\n"
            f"Subject: *{safe_subject_name}*\n\n"
            "This part will be connected after normal topic practice is finished\\.",
            parse_mode="MarkdownV2",
        )


async def waec_topic_page_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, _, subject_code, page_str = query.data.split("_", 3)
        page = int(page_str)
    except Exception:
        return await query.message.reply_text(
            "⚠️ Invalid topic page\\.",
            parse_mode="MarkdownV2",
        )

    context.user_data["wp_subject_code"] = subject_code
    context.user_data["wp_topic_page"] = page

    subject = get_waec_subject_by_code(subject_code)
    kb, page, total_pages = make_waec_topics_keyboard(subject_code, page)

    safe_subject_name = md_escape(str(subject["name"]))
    safe_page = md_escape(str(page))
    safe_total_pages = md_escape(str(total_pages))

    await query.message.reply_text(
        f"📚 *{safe_subject_name} Topics*\n\n"
        f"Choose a topic below\\.\n"
        f"_Page {safe_page} of {safe_total_pages}_",
        parse_mode="MarkdownV2",
        reply_markup=kb,
    )


async def waec_topic_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, subject_code, topic_id = query.data.split("::")
    except Exception:
        return await query.message.reply_text(
            "⚠️ Invalid topic selection\\.",
            parse_mode="MarkdownV2",
        )

    topics = get_waec_subject_topics(subject_code)
    selected_topic = next((t for t in topics if t["id"] == topic_id), None)

    if not selected_topic:
        return await query.message.reply_text(
            "⚠️ Topic not found\\.",
            parse_mode="MarkdownV2",
        )

    context.user_data["wp_subject_code"] = subject_code
    context.user_data["wp_topic_id"] = topic_id

    free_remaining = 5
    paid_credits = 0
    has_free_trial = free_remaining > 0
    has_paid_credits = paid_credits > 0

    safe_topic_title = md_escape(str(selected_topic["title"]))
    safe_free_remaining = md_escape(str(free_remaining))
    safe_paid_credits = md_escape(str(paid_credits))

    await query.message.reply_text(
        f"✅ *Topic selected:* {safe_topic_title}\n\n"
        f"🎁 Free questions left: *{safe_free_remaining}*\n"
        f"💳 Paid question credits: *{safe_paid_credits}*\n\n"
        "Choose how you want to continue:",
        parse_mode="MarkdownV2",
        reply_markup=make_waec_topic_access_keyboard_for_subject(
            subject_code,
            has_free_trial,
            has_paid_credits,
        ),
    )


async def waec_start_free_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    subject_code = context.user_data.get("wp_subject_code")
    topic_id = context.user_data.get("wp_topic_id")

    if not subject_code or not topic_id:
        return await query.message.reply_text(
            "⚠️ Topic session data missing\\. Please choose your subject and topic again\\.",
            parse_mode="MarkdownV2",
            reply_markup=make_waec_subject_keyboard(),
        )

    requested_count = 5
    seen_question_ids = context.user_data.get("wp_served_question_ids", [])

    batch = prepare_waec_topic_question_batch(
        subject_code=subject_code,
        topic_id=topic_id,
        requested_count=requested_count,
        seen_question_ids=seen_question_ids,
    )

    selected_questions = batch["selected_questions"]
    selected_question_ids = batch["selected_question_ids"]

    if not selected_questions:
        return await query.message.reply_text(
            "⚠️ No active questions found for this topic yet\\.",
            parse_mode="MarkdownV2",
        )

    context.user_data["wp_session_mode"] = "free_trial"
    context.user_data["wp_question_batch"] = selected_questions
    context.user_data["wp_question_ids"] = selected_question_ids
    context.user_data["wp_current_index"] = 0
    context.user_data["wp_session_target"] = len(selected_questions)
    context.user_data["wp_correct_count"] = 0
    context.user_data["wp_wrong_count"] = 0
    context.user_data["wp_current_question"] = None
    context.user_data["wp_answered_current"] = False
    context.user_data["wp_served_question_ids"] = []

    topic = next((t for t in get_waec_subject_topics(subject_code) if t["id"] == topic_id), None)
    topic_title = topic["title"] if topic else topic_id

    subject = get_waec_subject_by_code(subject_code)
    subject_name = subject["name"] if subject else subject_code

    safe_subject_name = md_escape(str(subject_name))
    safe_topic_title = md_escape(str(topic_title))
    safe_question_count = md_escape(str(len(selected_questions)))

    reset_note = (
        "\n♻️ Topic cycle reset because you already exhausted this topic before\\."
        if batch["cycle_reset"]
        else ""
    )

    await query.message.reply_text(
        f"🎉 *Free Trial Started*\n\n"
        f"📘 Subject: *{safe_subject_name}*\n"
        f"🧪 Topic: *{safe_topic_title}*\n"
        f"📚 Questions in this session: *{safe_question_count}*"
        f"{reset_note}\n\n"
        "Next step: we will now start serving Question 1\\.",
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("▶ Start Questions", callback_data="wp_serve_first")],
                [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
            ]
        ),
    )


async def send_current_waec_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    batch = context.user_data.get("wp_question_batch") or []
    current_index = int(context.user_data.get("wp_current_index", 0))

    if not batch:
        return await update.effective_message.reply_text(
            "⚠️ No active WAEC question session found\\.",
            parse_mode="MarkdownV2",
        )

    if current_index >= len(batch):
        correct_count = int(context.user_data.get("wp_correct_count", 0))
        wrong_count = int(context.user_data.get("wp_wrong_count", 0))
        total = len(batch)

        safe_total = md_escape(str(total))
        safe_correct_count = md_escape(str(correct_count))
        safe_wrong_count = md_escape(str(wrong_count))

        return await update.effective_message.reply_text(
            f"✅ *Practice Completed*\n\n"
            f"📚 Total Questions: *{safe_total}*\n"
            f"✅ Correct: *{safe_correct_count}*\n"
            f"❌ Wrong: *{safe_wrong_count}*\n\n"
            "Great job\\. You can return to WAEC / NECO Practice for another topic\\.",
            parse_mode="MarkdownV2",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("📘 WAEC / NECO Practice", callback_data="waecneco:practice")],
                    [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
                ]
            ),
        )

    question = batch[current_index]
    context.user_data["wp_current_question"] = question
    context.user_data["wp_answered_current"] = False

    options = question.get("options", {})

    safe_question_text = md_escape(str(question.get("question") or "Question unavailable."))
    safe_question_no = md_escape(str(current_index + 1))
    safe_total = md_escape(str(len(batch)))

    option_lines = []
    for key in ["A", "B", "C", "D", "E"]:
        if key in options:
            safe_option_text = md_escape(str(options[key]))
            option_lines.append(f"{key}\\. {safe_option_text}")

    text_msg = (
        "📘 *WAEC / NECO Practice*\n\n"
        f"Question {safe_question_no} of {safe_total}\n\n"
        f"{safe_question_text}\n\n"
        + "\n".join(option_lines)
    )

    rows = []
    answer_row = []

    for key in ["A", "B", "C", "D", "E"]:
        if key in options:
            answer_row.append(
                InlineKeyboardButton(key, callback_data=f"wp_ans::{key}")
            )
            if len(answer_row) == 2:
                rows.append(answer_row)
                answer_row = []

    if answer_row:
        rows.append(answer_row)

    rows.append([InlineKeyboardButton("🏠 End Practice", callback_data="wp_end_session")])

    await update.effective_message.reply_text(
        text_msg,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def waec_serve_first_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    await send_current_waec_question(update, context)


async def waec_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    if context.user_data.get("wp_answered_current", False):
        return await query.answer("You already answered this question\\.", show_alert=False)

    try:
        _, selected_option = query.data.split("::", 1)
    except Exception:
        return await query.message.reply_text(
            "⚠️ Invalid answer selection\\.",
            parse_mode="MarkdownV2",
        )

    question = context.user_data.get("wp_current_question")
    if not question:
        return await query.message.reply_text(
            "⚠️ No active question found\\.",
            parse_mode="MarkdownV2",
        )

    correct_option = str(question["answer"]).strip().upper()
    selected_option = str(selected_option).strip().upper()
    is_correct = selected_option == correct_option

    context.user_data["wp_answered_current"] = True
    context.user_data["wp_last_selected_option"] = selected_option
    context.user_data["wp_last_correct_option"] = correct_option

    if is_correct:
        context.user_data["wp_correct_count"] = int(context.user_data.get("wp_correct_count", 0)) + 1
        result_text = "✅ *Correct\\!*"
    else:
        context.user_data["wp_wrong_count"] = int(context.user_data.get("wp_wrong_count", 0)) + 1

        safe_correct_option = md_escape(str(correct_option))
        safe_correct_option_text = md_escape(str(question["options"].get(correct_option, "---")))

        result_text = (
            f"❌ *Wrong\\!*\n\n"
            f"Correct answer: *{safe_correct_option}* \\- {safe_correct_option_text}"
        )

    await query.message.reply_text(
        result_text,
        parse_mode="MarkdownV2",
        reply_markup=make_after_answer_keyboard(),
    )


async def waec_answer_details_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    question = context.user_data.get("wp_current_question")
    if not question:
        return await query.message.reply_text(
            "⚠️ No answered question found\\.",
            parse_mode="MarkdownV2",
        )

    explanation = question.get("explanation", {})

    question_restate = explanation.get("question_restate", "")
    principle = explanation.get("principle", "")
    steps = explanation.get("steps", [])
    if not isinstance(steps, list):
        steps = []

    final_answer = explanation.get("final_answer", "")
    simple_explanation = explanation.get("simple_explanation", "")

    lines = ["📖 *Answer Details*\n"]

    if question_restate:
        lines.append(f"*Question Restated*\n{md_escape(str(question_restate))}\n")

    if principle:
        lines.append(f"*Principle*\n{md_escape(str(principle))}\n")

    if steps:
        lines.append("*Step\\-by\\-step Solution*")
        for i, step in enumerate(steps, start=1):
            lines.append(f"{i}\\. {md_escape(str(step))}")
        lines.append("")

    if final_answer:
        lines.append(f"*Final Answer*\n{md_escape(str(final_answer))}\n")

    if simple_explanation:
        lines.append(f"*Simple Explanation*\n{md_escape(str(simple_explanation))}")

    await query.message.reply_text(
        "\n".join(lines),
        parse_mode="MarkdownV2",
        reply_markup=make_after_details_keyboard(),
    )


async def waec_next_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    current_index = int(context.user_data.get("wp_current_index", 0))
    context.user_data["wp_current_index"] = current_index + 1

    await send_current_waec_question(update, context)


async def waec_end_session_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    clear_waec_session_state(context)

    from handlers.core import go_start_callback
    await go_start_callback(update, context)


async def waec_back_mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    subject_code = query.data.replace("wp_back_mode_", "", 1)
    subject = get_waec_subject_by_code(subject_code)

    context.user_data["wp_subject_code"] = subject_code
    context.user_data["wp_mode"] = None
    context.user_data["wp_topic_id"] = None

    safe_subject_name = md_escape(str(subject["name"]))

    await query.message.reply_text(
        f"📘 *You selected:* {safe_subject_name}\n\n"
        "How would you like to practice\\?",
        parse_mode="MarkdownV2",
        reply_markup=make_waec_mode_keyboard(subject_code),
    )

def register_handlers(application):
    application.add_handler(CommandHandler("waecpractice", waecpractice_handler))
    application.add_handler(CallbackQueryHandler(waecpractice_handler, pattern=r"^waecneco:practice$"))

