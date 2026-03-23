# ====================================================================
# handlers/jambpractice.py
# ====================================================================

import math
import uuid
import logging
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CommandHandler, CallbackQueryHandler, ContextTypes
from sqlalchemy import text

from services.payments import create_checkout
from db import get_async_session
from jamb_loader import (
    get_jamb_subjects,
    get_subject_topics,
    get_subject_by_code,
    prepare_topic_question_batch,
)

logger = logging.getLogger(__name__)

TOPICS_PER_PAGE = 6


# =============================
# DB helpers
# =============================
async def ensure_jamb_user_access(user_id: int):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    insert into jamb_user_access (user_id)
                    values (:user_id)
                    on conflict (user_id) do nothing
                """),
                {"user_id": user_id},
            )


async def get_jamb_user_access(user_id: int) -> Optional[dict]:
    async with get_async_session() as session:
        result = await session.execute(
            text("""
                select
                    free_questions_remaining,
                    paid_question_credits,
                    total_questions_used
                from jamb_user_access
                where user_id = :user_id
            """),
            {"user_id": user_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None


async def create_jamb_session(
    user_id: int,
    subject_code: str,
    topic_id: str,
    question_target: int,
    mode: str = "topic_practice",
) -> int:
    async with get_async_session() as session:
        async with session.begin():
            result = await session.execute(
                text("""
                    insert into jamb_sessions (
                        user_id,
                        subject_code,
                        topic_id,
                        mode,
                        question_target,
                        status
                    )
                    values (
                        :user_id,
                        :subject_code,
                        :topic_id,
                        :mode,
                        :question_target,
                        'active'
                    )
                    returning id
                """),
                {
                    "user_id": user_id,
                    "subject_code": subject_code,
                    "topic_id": topic_id,
                    "mode": mode,
                    "question_target": question_target,
                },
            )
            session_id = result.scalar_one()
            return int(session_id)


async def get_seen_question_ids_for_topic(user_id: int, subject_code: str, topic_id: str) -> list[str]:
    async with get_async_session() as session:
        result = await session.execute(
            text("""
                select question_id
                from jamb_user_topic_history
                where user_id = :user_id
                  and subject_code = :subject_code
                  and topic_id = :topic_id
            """),
            {
                "user_id": user_id,
                "subject_code": subject_code,
                "topic_id": topic_id,
            },
        )
        rows = result.fetchall()
        return [str(row[0]) for row in rows]


async def reset_topic_history(user_id: int, subject_code: str, topic_id: str):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    delete from jamb_user_topic_history
                    where user_id = :user_id
                      and subject_code = :subject_code
                      and topic_id = :topic_id
                """),
                {
                    "user_id": user_id,
                    "subject_code": subject_code,
                    "topic_id": topic_id,
                },
            )


async def create_pending_jamb_payment(
    user_id: int,
    amount_paid: int,
    question_credits_added: int,
    payment_reference: str,
):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    insert into jamb_payments (
                        user_id,
                        amount_paid,
                        question_credits_added,
                        payment_reference,
                        payment_status,
                        provider
                    )
                    values (
                        :user_id,
                        :amount_paid,
                        :question_credits_added,
                        :payment_reference,
                        'pending',
                        'flutterwave'
                    )
                """),
                {
                    "user_id": user_id,
                    "amount_paid": amount_paid,
                    "question_credits_added": question_credits_added,
                    "payment_reference": payment_reference,
                },
            )


async def mark_jamb_payment_failed(payment_reference: str):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    update jamb_payments
                    set
                        payment_status = 'failed',
                        updated_at = now()
                    where payment_reference = :payment_reference
                """),
                {"payment_reference": payment_reference},
            )


async def get_paid_question_credits(user_id: int) -> int:
    access = await get_jamb_user_access(user_id)
    return int((access or {}).get("paid_question_credits", 0))


async def deduct_one_free_question(user_id: int) -> bool:
    """
    Deduct 1 free question if available.
    Returns True if deducted, False otherwise.
    """
    async with get_async_session() as session:
        async with session.begin():
            result = await session.execute(
                text("""
                    update jamb_user_access
                    set
                        free_questions_remaining = free_questions_remaining - 1,
                        total_questions_used = total_questions_used + 1,
                        updated_at = now()
                    where user_id = :user_id
                      and free_questions_remaining > 0
                    returning free_questions_remaining
                """),
                {"user_id": user_id},
            )
            row = result.first()
            return row is not None


async def deduct_one_paid_question(user_id: int) -> bool:
    """
    Deduct 1 paid question credit if available.
    Returns True if deducted, False otherwise.
    """
    async with get_async_session() as session:
        async with session.begin():
            result = await session.execute(
                text("""
                    update jamb_user_access
                    set
                        paid_question_credits = paid_question_credits - 1,
                        total_questions_used = total_questions_used + 1,
                        updated_at = now()
                    where user_id = :user_id
                      and paid_question_credits > 0
                    returning paid_question_credits
                """),
                {"user_id": user_id},
            )
            row = result.first()
            return row is not None


async def add_question_to_topic_history(
    user_id: int,
    subject_code: str,
    topic_id: str,
    question_id: str,
):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    insert into jamb_user_topic_history (
                        user_id,
                        subject_code,
                        topic_id,
                        question_id
                    )
                    values (
                        :user_id,
                        :subject_code,
                        :topic_id,
                        :question_id
                    )
                    on conflict (user_id, subject_code, topic_id, question_id) do nothing
                """),
                {
                    "user_id": user_id,
                    "subject_code": subject_code,
                    "topic_id": topic_id,
                    "question_id": question_id,
                },
            )


async def record_jamb_attempt(
    session_id: int,
    user_id: int,
    subject_code: str,
    topic_id: str,
    question_id: str,
    selected_option: str,
    correct_option: str,
    is_correct: bool,
):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    insert into jamb_attempts (
                        session_id,
                        user_id,
                        subject_code,
                        topic_id,
                        question_id,
                        selected_option,
                        correct_option,
                        is_correct
                    )
                    values (
                        :session_id,
                        :user_id,
                        :subject_code,
                        :topic_id,
                        :question_id,
                        :selected_option,
                        :correct_option,
                        :is_correct
                    )
                """),
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "subject_code": subject_code,
                    "topic_id": topic_id,
                    "question_id": question_id,
                    "selected_option": selected_option,
                    "correct_option": correct_option,
                    "is_correct": is_correct,
                },
            )


async def increment_jamb_session_served(session_id: int):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    update jamb_sessions
                    set
                        questions_served = questions_served + 1
                    where id = :session_id
                """),
                {"session_id": session_id},
            )


async def increment_jamb_session_result(session_id: int, is_correct: bool):
    async with get_async_session() as session:
        async with session.begin():
            if is_correct:
                await session.execute(
                    text("""
                        update jamb_sessions
                        set
                            correct_count = correct_count + 1
                        where id = :session_id
                    """),
                    {"session_id": session_id},
                )
            else:
                await session.execute(
                    text("""
                        update jamb_sessions
                        set
                            wrong_count = wrong_count + 1
                        where id = :session_id
                    """),
                    {"session_id": session_id},
                )


async def complete_jamb_session(session_id: int):
    async with get_async_session() as session:
        async with session.begin():
            await session.execute(
                text("""
                    update jamb_sessions
                    set
                        status = 'completed',
                        ended_at = now()
                    where id = :session_id
                """),
                {"session_id": session_id},
            )


# =============================
# Keyboards
# =============================
def make_subject_keyboard():
    subjects = get_jamb_subjects()
    rows = []
    row = []

    for subject in subjects:
        row.append(
            InlineKeyboardButton(
                subject["name"],
                callback_data=f"jp_subj_{subject['code']}"
            )
        )
        if len(row) == 2:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def make_mode_keyboard(subject_code: str):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📚 By Topics", callback_data=f"jp_mode_topics_{subject_code}")],
            [InlineKeyboardButton("📝 Mock UTME", callback_data=f"jp_mode_mock_{subject_code}")],
            [InlineKeyboardButton("⬅️ Back to Subjects", callback_data="jambpractice")],
            [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
        ]
    )


def make_topics_keyboard(subject_code: str, page: int = 1):
    topics = get_subject_topics(subject_code)
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
                callback_data=f"jp_topic::{subject_code}::{topic['id']}"
            )
        ])

    nav_row = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton("◀ Prev", callback_data=f"jp_topicpage_{subject_code}_{page-1}")
        )
    if page < total_pages:
        nav_row.append(
            InlineKeyboardButton("Next ▶", callback_data=f"jp_topicpage_{subject_code}_{page+1}")
        )
    if nav_row:
        rows.append(nav_row)

    rows.append([InlineKeyboardButton("⬅️ Back to Mode", callback_data=f"jp_back_mode_{subject_code}")])
    rows.append([InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")])

    return InlineKeyboardMarkup(rows), page, total_pages


def make_topic_access_keyboard_for_subject(subject_code: str, has_free_trial: bool):
    rows = []

    if has_free_trial:
        rows.append([InlineKeyboardButton("🎁 Use Free Trial (5 Questions)", callback_data="jp_start_free")])

    rows.extend([
        [InlineKeyboardButton("💳 Get 50 Questions — ₦100", callback_data="jp_buy_50")],
        [InlineKeyboardButton("💳 Get 100 Questions — ₦200", callback_data="jp_buy_100")],
        [InlineKeyboardButton("💳 Get 150 Questions — ₦300", callback_data="jp_buy_150")],
        [InlineKeyboardButton("💳 Get 200 Questions — ₦400", callback_data="jp_buy_200")],
        [InlineKeyboardButton("⬅️ Back to Topics", callback_data=f"jp_topicpage_{subject_code}_1")],
        [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
    ])

    return InlineKeyboardMarkup(rows)


def make_after_answer_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➡️ Next", callback_data="jp_next")],
            [InlineKeyboardButton("📖 Answer Details", callback_data="jp_details")],
            [InlineKeyboardButton("🏠 End Practice", callback_data="menu:main")],
        ]
    )


def make_after_details_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("➡️ Next", callback_data="jp_next")],
            [InlineKeyboardButton("🏠 End Practice", callback_data="menu:main")],
        ]
    )


# =============================
# Message builders
# =============================
def build_welcome_text(free_remaining: int, paid_credits: int) -> str:
    return (
        "🎓 *Welcome to JAMB Practice*\n\n"
        "Practice original UTME-style questions by subject and topic.\n"
        "You can study with detailed answer explanations after each question.\n\n"
        "*How it works:*\n"
        "• First-time users get *5 free questions*\n"
        "• After that, it costs *₦100 per 50 questions*\n"
        "• Questions are served topic by topic\n"
        "• Repeats are avoided until you exhaust the topic bank\n\n"
        "*Disclaimer:*\n"
        "This is an independent study tool and not an official JAMB platform.\n\n"
        f"🎁 Free questions left: *{free_remaining}*\n"
        f"💳 Paid question credits: *{paid_credits}*\n\n"
        "Please choose a subject below."
    )


# =============================
# Entry point
# =============================
async def jambpractice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg = update.effective_user
    if not tg:
        return

    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass

    await ensure_jamb_user_access(tg.id)
    access = await get_jamb_user_access(tg.id)

    free_remaining = int((access or {}).get("free_questions_remaining", 5))
    paid_credits = int((access or {}).get("paid_question_credits", 0))

    context.user_data["jp_subject_code"] = None
    context.user_data["jp_mode"] = None
    context.user_data["jp_topic_id"] = None
    context.user_data["jp_topic_page"] = 1

    text_msg = build_welcome_text(free_remaining, paid_credits)

    await update.effective_message.reply_text(
        text_msg,
        parse_mode="Markdown",
        reply_markup=make_subject_keyboard(),
    )


# =============================
# Subject selected
# =============================
async def jamb_subject_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, _, subject_code = query.data.split("_", 2)
    except Exception:
        return await query.message.reply_text("⚠️ Invalid subject selection.")

    subject = get_subject_by_code(subject_code)
    if not subject:
        return await query.message.reply_text("⚠️ Subject not found or inactive.")

    context.user_data["jp_subject_code"] = subject_code
    context.user_data["jp_mode"] = None
    context.user_data["jp_topic_id"] = None
    context.user_data["jp_topic_page"] = 1

    await query.message.reply_text(
        f"📘 *You selected:* {subject['name']}\n\n"
        "How would you like to practice?",
        parse_mode="Markdown",
        reply_markup=make_mode_keyboard(subject_code),
    )


# =============================
# Mode selected
# =============================
async def jamb_mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data = query.data

    if data.startswith("jp_mode_topics_"):
        subject_code = data.replace("jp_mode_topics_", "", 1)
        context.user_data["jp_subject_code"] = subject_code
        context.user_data["jp_mode"] = "topic_practice"
        context.user_data["jp_topic_page"] = 1

        subject = get_subject_by_code(subject_code)
        kb, page, total_pages = make_topics_keyboard(subject_code, 1)

        return await query.message.reply_text(
            f"📚 *{subject['name']} Topics*\n\n"
            f"Choose a topic below.\n"
            f"_Page {page} of {total_pages}_",
            parse_mode="Markdown",
            reply_markup=kb,
        )

    if data.startswith("jp_mode_mock_"):
        subject_code = data.replace("jp_mode_mock_", "", 1)
        context.user_data["jp_subject_code"] = subject_code
        context.user_data["jp_mode"] = "mock_utme"

        subject = get_subject_by_code(subject_code)

        return await query.message.reply_text(
            f"📝 *Mock UTME for {subject['name']}*\n\n"
            "Mock UTME mode is coming next.\n"
            "For now, please use *By Topics* while we complete the full flow.",
            parse_mode="Markdown",
            reply_markup=make_mode_keyboard(subject_code),
        )


# =============================
# Topic pagination
# =============================
async def jamb_topic_page_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, _, subject_code, page_str = query.data.split("_", 3)
        page = int(page_str)
    except Exception:
        return await query.message.reply_text("⚠️ Invalid topic page.")

    context.user_data["jp_subject_code"] = subject_code
    context.user_data["jp_topic_page"] = page

    subject = get_subject_by_code(subject_code)
    kb, page, total_pages = make_topics_keyboard(subject_code, page)

    await query.message.reply_text(
        f"📚 *{subject['name']} Topics*\n\n"
        f"Choose a topic below.\n"
        f"_Page {page} of {total_pages}_",
        parse_mode="Markdown",
        reply_markup=kb,
    )


# =============================
# Topic selected
# =============================
async def jamb_topic_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    try:
        _, subject_code, topic_id = query.data.split("::")
    except Exception:
        return await query.message.reply_text("⚠️ Invalid topic selection.")

    topics = get_subject_topics(subject_code)
    selected_topic = next((t for t in topics if t["id"] == topic_id), None)

    if not selected_topic:
        return await query.message.reply_text("⚠️ Topic not found.")

    context.user_data["jp_subject_code"] = subject_code
    context.user_data["jp_topic_id"] = topic_id

    tg = update.effective_user
    await ensure_jamb_user_access(tg.id)
    access = await get_jamb_user_access(tg.id)

    free_remaining = int((access or {}).get("free_questions_remaining", 0))
    paid_credits = int((access or {}).get("paid_question_credits", 0))
    has_free_trial = free_remaining > 0

    await query.message.reply_text(
        f"✅ *Topic selected:* {selected_topic['title']}\n\n"
        f"🎁 Free questions left: *{free_remaining}*\n"
        f"💳 Paid question credits: *{paid_credits}*\n\n"
        "Choose how you want to continue:",
        parse_mode="Markdown",
        reply_markup=make_topic_access_keyboard_for_subject(subject_code, has_free_trial),
    )


# =============================
# Free trial start
# =============================
async def jamb_start_free_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    tg = update.effective_user
    user_id = tg.id

    subject_code = context.user_data.get("jp_subject_code")
    topic_id = context.user_data.get("jp_topic_id")

    if not subject_code or not topic_id:
        return await query.message.reply_text(
            "⚠️ Topic session data missing. Please choose your subject and topic again.",
            reply_markup=make_subject_keyboard(),
        )

    access = await get_jamb_user_access(user_id)
    free_remaining = int((access or {}).get("free_questions_remaining", 0))

    if free_remaining <= 0:
        return await query.message.reply_text(
            "⚠️ You have no free JAMB questions left.\n\nPlease buy a question pack to continue."
        )

    requested_count = min(5, free_remaining)

    seen_question_ids = await get_seen_question_ids_for_topic(
        user_id=user_id,
        subject_code=subject_code,
        topic_id=topic_id,
    )

    batch = prepare_topic_question_batch(
        subject_code=subject_code,
        topic_id=topic_id,
        requested_count=requested_count,
        seen_question_ids=seen_question_ids,
    )

    if batch["cycle_reset"]:
        await reset_topic_history(user_id, subject_code, topic_id)

    selected_questions = batch["selected_questions"]
    selected_question_ids = batch["selected_question_ids"]

    if not selected_questions:
        return await query.message.reply_text(
            "⚠️ No active questions found for this topic yet."
        )

    session_id = await create_jamb_session(
        user_id=user_id,
        subject_code=subject_code,
        topic_id=topic_id,
        question_target=len(selected_questions),
        mode="topic_practice",
    )

    context.user_data["jp_session_id"] = session_id
    context.user_data["jp_session_mode"] = "free_trial"
    context.user_data["jp_question_batch"] = selected_questions
    context.user_data["jp_question_ids"] = selected_question_ids
    context.user_data["jp_current_index"] = 0
    context.user_data["jp_session_target"] = len(selected_questions)
    context.user_data["jp_correct_count"] = 0
    context.user_data["jp_wrong_count"] = 0
    context.user_data["jp_current_question"] = None
    context.user_data["jp_answered_current"] = False
    context.user_data["jp_served_question_ids"] = []

    topic = next((t for t in get_subject_topics(subject_code) if t["id"] == topic_id), None)
    topic_title = topic["title"] if topic else topic_id

    reset_note = "\n♻️ Topic cycle reset because you already exhausted this topic before." if batch["cycle_reset"] else ""

    await query.message.reply_text(
        f"🎉 *Free Trial Started*\n\n"
        f"📘 Subject: *{get_subject_by_code(subject_code)['name']}*\n"
        f"🧪 Topic: *{topic_title}*\n"
        f"📚 Questions in this session: *{len(selected_questions)}*"
        f"{reset_note}\n\n"
        "Next step: we will now start serving Question 1.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("▶ Start Questions", callback_data="jp_serve_first")],
                [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
            ]
        ),
    )


# =============================
# Buy question pack
# =============================
async def jamb_buy_pack_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    data = query.data
    pack_size = data.replace("jp_buy_", "", 1)

    pricing_map = {
        "50": 100,
        "100": 200,
        "150": 300,
        "200": 400,
    }

    credits_map = {
        "50": 50,
        "100": 100,
        "150": 150,
        "200": 200,
    }

    if pack_size not in pricing_map:
        return await query.message.reply_text("⚠️ Invalid JAMB package selected.")

    amount = pricing_map[pack_size]
    credits = credits_map[pack_size]

    user = query.from_user
    tg_id = user.id
    username = user.username or f"user_{tg_id}"
    email = f"{username}@naijaprizegate.ng"

    tx_ref = f"JAMB-{uuid.uuid4().hex[:12].upper()}"

    await create_pending_jamb_payment(
        user_id=tg_id,
        amount_paid=amount,
        question_credits_added=credits,
        payment_reference=tx_ref,
    )

    try:
        checkout_url = await create_checkout(
            user_id=tg_id,
            amount=amount,
            username=username,
            email=email,
            tx_ref=tx_ref,
            meta={
                "tg_id": tg_id,
                "username": username,
                "purpose": "JAMB",
                "product": "jamb_practice",
                "question_credits": credits,
            },
            product_type="JAMB",
        )
    except Exception:
        logger.exception("❌ Failed to create JAMB checkout | tx_ref=%s", tx_ref)
        await mark_jamb_payment_failed(tx_ref)
        return await query.message.reply_text(
            "⚠️ Payment service unavailable. Please try again shortly."
        )

    await query.message.reply_text(
        f"💳 *JAMB Question Pack Selected*\n\n"
        f"📚 Questions: *{credits}*\n"
        f"💰 Amount: *₦{amount}*\n\n"
        "After successful payment, your JAMB question credits will be added automatically.\n\n"
        "Tap below to complete payment.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("💳 Pay Securely", url=checkout_url)],
                [InlineKeyboardButton("⬅️ Back to JAMB Practice", callback_data="jambpractice")],
                [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
            ]
        ),
    )

# =============================
# Question serving
# =============================
async def send_current_jamb_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    batch = context.user_data.get("jp_question_batch") or []
    current_index = int(context.user_data.get("jp_current_index", 0))

    if not batch:
        return await update.effective_message.reply_text(
            "⚠️ No active JAMB question session found."
        )

    if current_index >= len(batch):
        session_id = context.user_data.get("jp_session_id")
        if session_id:
            await complete_jamb_session(int(session_id))

        correct_count = int(context.user_data.get("jp_correct_count", 0))
        wrong_count = int(context.user_data.get("jp_wrong_count", 0))
        total = len(batch)

        return await update.effective_message.reply_text(
            f"✅ *Practice Completed*\n\n"
            f"📚 Total Questions: *{total}*\n"
            f"✅ Correct: *{correct_count}*\n"
            f"❌ Wrong: *{wrong_count}*\n\n"
            "Great job. You can return to JAMB Practice for another topic.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🎓 JAMB Practice", callback_data="jambpractice")],
                    [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
                ]
            ),
        )

    question = batch[current_index]
    question_id = str(question["id"])
    user_id = update.effective_user.id
    subject_code = context.user_data.get("jp_subject_code")
    topic_id = context.user_data.get("jp_topic_id")
    session_id = context.user_data.get("jp_session_id")
    session_mode = context.user_data.get("jp_session_mode")
    served_question_ids = context.user_data.get("jp_served_question_ids", [])

    # Charge and record history when question is SERVED, not when answered
    if question_id not in served_question_ids:
        if session_mode == "free_trial":
            deducted = await deduct_one_free_question(user_id)
            if not deducted:
                return await update.effective_message.reply_text(
                    "⚠️ You have no free question balance left.\n\nPlease buy a question pack to continue."
                )
        elif session_mode == "paid_session":
            deducted = await deduct_one_paid_question(user_id)
            if not deducted:
                return await update.effective_message.reply_text(
                    "⚠️ You have no paid JAMB question credits left.\n\nPlease buy another question pack to continue."
                )

        await add_question_to_topic_history(
            user_id=user_id,
            subject_code=subject_code,
            topic_id=topic_id,
            question_id=question_id,
        )

        if session_id:
            await increment_jamb_session_served(int(session_id))

        served_question_ids.append(question_id)
        context.user_data["jp_served_question_ids"] = served_question_ids

    context.user_data["jp_current_question"] = question
    context.user_data["jp_answered_current"] = False

    options = question.get("options", {})

    option_lines = []
    for key in ["A", "B", "C", "D", "E"]:
        if key in options:
            option_lines.append(f"{key}. {options[key]}")

    text_msg = (
        f"📘 *JAMB Practice*\n"
        f"Question {current_index + 1} of {len(batch)}\n\n"
        f"{question['question']}\n\n"
        + "\n".join(option_lines)
    )

    rows = []
    answer_row = []
    for key in ["A", "B", "C", "D", "E"]:
        if key in options:
            answer_row.append(
                InlineKeyboardButton(key, callback_data=f"jp_ans::{key}")
            )
            if len(answer_row) == 2:
                rows.append(answer_row)
                answer_row = []

    if answer_row:
        rows.append(answer_row)

    rows.append([InlineKeyboardButton("🏠 End Practice", callback_data="menu:main")])

    await update.effective_message.reply_text(
        text_msg,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def jamb_serve_first_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    await send_current_jamb_question(update, context)


# =============================
# Answer handling
# =============================
async def jamb_answer_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    if context.user_data.get("jp_answered_current", False):
        return await query.answer("You already answered this question.", show_alert=False)

    try:
        _, selected_option = query.data.split("::", 1)
    except Exception:
        return await query.message.reply_text("⚠️ Invalid answer selection.")

    question = context.user_data.get("jp_current_question")
    if not question:
        return await query.message.reply_text("⚠️ No active question found.")

    user_id = update.effective_user.id
    session_id = int(context.user_data.get("jp_session_id"))
    subject_code = context.user_data.get("jp_subject_code")
    topic_id = context.user_data.get("jp_topic_id")
    question_id = str(question["id"])
    correct_option = str(question["answer"])
    is_correct = selected_option == correct_option

    await record_jamb_attempt(
        session_id=session_id,
        user_id=user_id,
        subject_code=subject_code,
        topic_id=topic_id,
        question_id=question_id,
        selected_option=selected_option,
        correct_option=correct_option,
        is_correct=is_correct,
    )

    await increment_jamb_session_result(session_id, is_correct)

    context.user_data["jp_answered_current"] = True
    context.user_data["jp_last_selected_option"] = selected_option
    context.user_data["jp_last_correct_option"] = correct_option

    if is_correct:
        context.user_data["jp_correct_count"] = int(context.user_data.get("jp_correct_count", 0)) + 1
        result_text = "✅ *Correct!*"
    else:
        context.user_data["jp_wrong_count"] = int(context.user_data.get("jp_wrong_count", 0)) + 1
        result_text = (
            f"❌ *Wrong!*\n\n"
            f"Correct answer: *{correct_option}* — {question['options'][correct_option]}"
        )

    await query.message.reply_text(
        result_text,
        parse_mode="Markdown",
        reply_markup=make_after_answer_keyboard(),
    )


async def jamb_answer_details_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    question = context.user_data.get("jp_current_question")
    if not question:
        return await query.message.reply_text("⚠️ No answered question found.")

    explanation = question.get("explanation", {})

    question_restate = explanation.get("question_restate", "")
    principle = explanation.get("principle", "")
    steps = explanation.get("steps", [])
    final_answer = explanation.get("final_answer", "")
    simple_explanation = explanation.get("simple_explanation", "")

    lines = ["📖 *Answer Details*\n"]

    if question_restate:
        lines.append(f"*Question Restated*\n{question_restate}\n")

    if principle:
        lines.append(f"*Principle*\n{principle}\n")

    if steps:
        lines.append("*Step-by-step Solution*")
        for i, step in enumerate(steps, start=1):
            lines.append(f"{i}. {step}")
        lines.append("")

    if final_answer:
        lines.append(f"*Final Answer*\n{final_answer}\n")

    if simple_explanation:
        lines.append(f"*Simple Explanation*\n{simple_explanation}")

    await query.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=make_after_details_keyboard(),
    )


async def jamb_next_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    current_index = int(context.user_data.get("jp_current_index", 0))
    context.user_data["jp_current_index"] = current_index + 1

    await send_current_jamb_question(update, context)


# =============================
# Back to mode
# =============================
async def jamb_back_mode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    subject_code = query.data.replace("jp_back_mode_", "", 1)
    subject = get_subject_by_code(subject_code)

    context.user_data["jp_subject_code"] = subject_code
    context.user_data["jp_mode"] = None
    context.user_data["jp_topic_id"] = None

    await query.message.reply_text(
        f"📘 *You selected:* {subject['name']}\n\n"
        "How would you like to practice?",
        parse_mode="Markdown",
        reply_markup=make_mode_keyboard(subject_code),
    )


# =============================
# Register handlers
# =============================
def register_handlers(application):
    application.add_handler(CommandHandler("jambpractice", jambpractice_handler))
    application.add_handler(CallbackQueryHandler(jambpractice_handler, pattern=r"^jambpractice$"))
    application.add_handler(CallbackQueryHandler(jamb_subject_handler, pattern=r"^jp_subj_"))
    application.add_handler(CallbackQueryHandler(jamb_mode_handler, pattern=r"^jp_mode_"))
    application.add_handler(CallbackQueryHandler(jamb_topic_page_handler, pattern=r"^jp_topicpage_"))
    application.add_handler(CallbackQueryHandler(jamb_topic_handler, pattern=r"^jp_topic::"))
    application.add_handler(CallbackQueryHandler(jamb_back_mode_handler, pattern=r"^jp_back_mode_"))
    application.add_handler(CallbackQueryHandler(jamb_start_free_handler, pattern=r"^jp_start_free$"))
    application.add_handler(CallbackQueryHandler(jamb_buy_pack_handler, pattern=r"^jp_buy_"))
    application.add_handler(CallbackQueryHandler(jamb_serve_first_handler, pattern=r"^jp_serve_first$"))
    application.add_handler(CallbackQueryHandler(jamb_answer_handler, pattern=r"^jp_ans::"))
    application.add_handler(CallbackQueryHandler(jamb_answer_details_handler, pattern=r"^jp_details$"))
    application.add_handler(CallbackQueryHandler(jamb_next_handler, pattern=r"^jp_next$"))

