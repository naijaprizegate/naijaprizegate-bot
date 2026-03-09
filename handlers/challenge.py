# ==========================================================
# handlers/challenge.py
# ==========================================================

import asyncio
from urllib.parse import quote

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from sqlalchemy import text
from db import AsyncSessionLocal


# ==========================================================
# CONFIG
# ==========================================================

CHALLENGE_QUESTION_COUNT = 5
CHALLENGE_QUESTION_TIME_LIMIT = 15

CHALLENGE_CATEGORIES = [
    "nigeria_history",
    "geography",
    "nigeria_entertainment",
    "sciences",
    "mathematics",
    "english",
    "football",
]

CHALLENGE_CATEGORY_LABELS = {
    "nigeria_history": "Nigeria History",
    "geography": "Geography",
    "nigeria_entertainment": "Entertainment",
    "sciences": "Sciences",
    "mathematics": "Mathematics",
    "english": "English",
    "football": "Football",
}


# ===================================================
# Upsert Telegram User
# ====================================================
async def upsert_telegram_user(user):
    if not user:
        return

    username = user.username
    full_name = user.full_name

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
                INSERT INTO users (tg_id, username, full_name)
                VALUES (:tg_id, :username, :full_name)
                ON CONFLICT (tg_id)
                DO UPDATE SET
                    username = EXCLUDED.username,
                    full_name = EXCLUDED.full_name
            """),
            {
                "tg_id": int(user.id),
                "username": username,
                "full_name": full_name,
            },
        )
        await session.commit()

# ====================================================
# Handle Challenge Question Timeout
# ====================================================
async def handle_challenge_question_timeout(
    context: ContextTypes.DEFAULT_TYPE,
    challenge_id: int,
    user_id: int,
    question_id: int,
    question_order: int,
):
    await asyncio.sleep(CHALLENGE_QUESTION_TIME_LIMIT)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT answered, timed_out
                FROM challenge_question_delivery
                WHERE challenge_id = :cid
                  AND user_id = :uid
                  AND question_id = :qid
                LIMIT 1
            """),
            {
                "cid": challenge_id,
                "uid": user_id,
                "qid": question_id,
            },
        )

        row = result.fetchone()

        if not row:
            return

        if row.answered or row.timed_out:
            return

        await session.execute(
            text("""
                UPDATE challenge_question_delivery
                SET timed_out = true
                WHERE challenge_id = :cid
                  AND user_id = :uid
                  AND question_id = :qid
            """),
            {
                "cid": challenge_id,
                "uid": user_id,
                "qid": question_id,
            },
        )

        await session.commit()

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text="⏰ Time up! Moving to the next question.",
        )
    except Exception:
        pass

    next_question_order = question_order + 1

    if next_question_order <= CHALLENGE_QUESTION_COUNT:
        await send_challenge_question_to_one_player(
            context=context,
            challenge_id=challenge_id,
            question_order=next_question_order,
            user_id=user_id,
        )
    else:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("""
                    UPDATE challenge_players
                    SET completed = true
                    WHERE challenge_id = :cid
                      AND user_id = :uid
                """),
                {
                    "cid": challenge_id,
                    "uid": user_id,
                },
            )
            await session.commit()

        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="🏁 You have completed the challenge!",
            )
        except Exception:
            pass

        dummy_update = type("DummyUpdate", (), {})()
        dummy_update.effective_chat = None
        await maybe_finish_challenge(dummy_update, context, challenge_id)


# ====================================================
# Create Challenge
# =====================================================

async def create_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    query = update.callback_query
    message = update.message or (query.message if query else None)

    if query:
        await query.answer()

    if not message or not user:
        return
    
    await upsert_telegram_user(user)

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("""
                    INSERT INTO challenges (creator_id, question_start, question_end, status)
                    VALUES (:creator_id, 0, 0, 'waiting')
                    RETURNING id
                """),
                {
                    "creator_id": int(user.id),
                },
            )
            challenge_id = result.scalar()

            await session.execute(
                text("""
                    INSERT INTO challenge_players (challenge_id, user_id, score, completed)
                    VALUES (:challenge_id, :user_id, 0, false)
                    ON CONFLICT DO NOTHING
                """),
                {
                    "challenge_id": challenge_id,
                    "user_id": int(user.id),
                },
            )

            await session.commit()

    except Exception:
        await message.reply_text("❌ Could not create challenge. Please try again.")
        return

    bot_username = context.bot.username
    invite_link = f"https://t.me/{bot_username}?start=challenge_{challenge_id}"

    share_text = (
        "🔥 Can you beat my score on NaijaPrizeGate?\n\n"
        "Join my trivia challenge and let's see who is smarter 🧠\n\n"
    )

    encoded_link = quote(invite_link)
    encoded_text = quote(share_text)

    keyboard = [
        [
            InlineKeyboardButton(
                "📨 Share Challenge",
                url=f"https://t.me/share/url?url={encoded_link}&text={encoded_text}",
            )
        ]
    ]

    await message.reply_text(
        f"⚔️ <b>Friend Challenge Created!</b>\n\n"
        f"Invite your friends to compete with you.\n\n"
        f"<b>Challenge Questions:</b> {CHALLENGE_QUESTION_COUNT}\n\n"
        f"Share this link with friends:\n\n"
        f"{invite_link}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )

    await show_challenge_lobby(update, context, challenge_id)


# ==========================================================
# Generate Challenge Questions
# ==========================================================

async def generate_challenge_questions(
    challenge_id: int,
    category: str,
    question_count: int = CHALLENGE_QUESTION_COUNT,
):
    """
    Select challenge questions in sequence from questions,
    avoiding repeats for players who have not yet exhausted the
    category question cycle.

    Selected questions are stored in challenge_round_questions.
    """
    async with AsyncSessionLocal() as session:
        # 1) Get all players in the challenge
        result = await session.execute(
            text("""
                SELECT user_id
                FROM challenge_players
                WHERE challenge_id = :cid
            """),
            {"cid": challenge_id},
        )
        player_rows = result.fetchall()

        if not player_rows:
            return []

        player_ids = [row.user_id for row in player_rows]

        # 2) Find players who have NOT exhausted this category
        active_players = []

        for player_id in player_ids:
            result = await session.execute(
                text("""
                    SELECT COUNT(DISTINCT ca.question_id) AS answered_count
                    FROM challenge_answers ca
                    JOIN questions q
                      ON q.id = ca.question_id
                    WHERE ca.user_id = :uid
                      AND q.category = :category
                """),
                {
                    "uid": player_id,
                    "category": category,
                },
            )

            row = result.fetchone()
            answered_count = row.answered_count if row else 0

            if answered_count < 100:
                active_players.append(player_id)

        # 3) Build exclusion set from active players only
        excluded_question_ids = set()

        for player_id in active_players:
            result = await session.execute(
                text("""
                    SELECT DISTINCT ca.question_id
                    FROM challenge_answers ca
                    JOIN questions q
                      ON q.id = ca.question_id
                    WHERE ca.user_id = :uid
                      AND q.category = :category
                """),
                {
                    "uid": player_id,
                    "category": category,
                },
            )

            for row in result.fetchall():
                excluded_question_ids.add(row.question_id)

        # 4) Load all category questions in sequence
        result = await session.execute(
            text("""
                SELECT id, question_order
                FROM questions
                WHERE category = :category
                ORDER BY question_order ASC
            """),
            {"category": category},
        )
        all_questions = result.fetchall()

        if not all_questions:
            return []

        # 5) Pick fresh questions first
        fresh_questions = [q for q in all_questions if q.id not in excluded_question_ids]
        selected_questions = fresh_questions[:question_count]

        # 6) If not enough fresh questions remain, reset cycle from beginning
        if len(selected_questions) < question_count:
            selected_questions = all_questions[:question_count]

        if not selected_questions:
            return []

        # 7) Clear any previously stored round questions for this challenge
        await session.execute(
            text("""
                DELETE FROM challenge_round_questions
                WHERE challenge_id = :cid
            """),
            {"cid": challenge_id},
        )

        # 8) Save selected questions
        for i, q in enumerate(selected_questions, start=1):
            await session.execute(
                text("""
                    INSERT INTO challenge_round_questions
                    (challenge_id, question_id, question_order)
                    VALUES (:cid, :qid, :qorder)
                """),
                {
                    "cid": challenge_id,
                    "qid": q.id,
                    "qorder": i,
                },
            )

        await session.commit()

        return selected_questions


# ==========================================================
# Join Challenge
# ==========================================================

async def join_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return False

    arg = context.args[0]

    if not arg.startswith("challenge_"):
        return False

    try:
        challenge_id = int(arg.split("_", 1)[1])
    except (IndexError, ValueError):
        return False

    user = update.effective_user
    if not user:
        return False
    
    await upsert_telegram_user(user)

    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
                INSERT INTO challenge_players (challenge_id, user_id, score, completed)
                VALUES (:challenge_id, :user_id, 0, false)
                ON CONFLICT DO NOTHING
            """),
            {
                "challenge_id": challenge_id,
                "user_id": int(user.id),
            },
        )
        await session.commit()

    await update.effective_chat.send_message("✅ You joined the challenge lobby.")
    await show_challenge_lobby(update, context, challenge_id)
    return True


# ==========================================================
# Send Challenge Question To All Players
# ==========================================================

async def send_challenge_question(
    context: ContextTypes.DEFAULT_TYPE,
    challenge_id: int,
    question_order: int,
):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT crq.question_id,
                       q.category,
                       q.question,
                       q.option_a,
                       q.option_b,
                       q.option_c,
                       q.option_d
                FROM challenge_round_questions crq
                JOIN questions q
                  ON q.id = crq.question_id
                WHERE crq.challenge_id = :cid
                  AND crq.question_order = :qorder
            """),
            {
                "cid": challenge_id,
                "qorder": question_order,
            },
        )
        question = result.fetchone()

        if not question:
            return

        result = await session.execute(
            text("""
                SELECT user_id
                FROM challenge_players
                WHERE challenge_id = :cid
            """),
            {"cid": challenge_id},
        )
        players = result.fetchall()

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "A",
                callback_data=f"challenge_answer_{challenge_id}|{question_order}|A|{question.question_id}",
            ),
            InlineKeyboardButton(
                "B",
                callback_data=f"challenge_answer_{challenge_id}|{question_order}|B|{question.question_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "C",
                callback_data=f"challenge_answer_{challenge_id}|{question_order}|C|{question.question_id}",
            ),
            InlineKeyboardButton(
                "D",
                callback_data=f"challenge_answer_{challenge_id}|{question_order}|D|{question.question_id}",
            ),
        ],
    ])

    text_message = (
        f"🧠 <b>Challenge Question {question_order}/{CHALLENGE_QUESTION_COUNT}</b>\n\n"
        f"<b>Category:</b> {CHALLENGE_CATEGORY_LABELS.get(question.category, question.category)}\n\n"
        f"{question.question}\n\n"
        f"A. {question.option_a}\n"
        f"B. {question.option_b}\n"
        f"C. {question.option_c}\n"
        f"D. {question.option_d}\n\n"
        f"⏳ <b>Time left:</b> {CHALLENGE_QUESTION_TIME_LIMIT}s"
    )

    for player in players:
        try:
            await context.bot.send_message(
                chat_id=player.user_id,
                text=text_message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception:
            pass


# ==========================================================
# Send Challenge Question To One Player
# ==========================================================

async def send_challenge_question_to_one_player(
    context: ContextTypes.DEFAULT_TYPE,
    challenge_id: int,
    question_order: int,
    user_id: int,
):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT crq.question_id,
                       q.category,
                       q.question,
                       q.option_a,
                       q.option_b,
                       q.option_c,
                       q.option_d
                FROM challenge_round_questions crq
                JOIN questions q
                  ON q.id = crq.question_id
                WHERE crq.challenge_id = :cid
                  AND crq.question_order = :qorder
            """),
            {
                "cid": challenge_id,
                "qorder": question_order,
            },
        )

        question = result.fetchone()

        if not question:
            return

        await session.execute(
            text("""
                INSERT INTO challenge_question_delivery
                (challenge_id, user_id, question_id, question_order, answered, timed_out)
                VALUES (:cid, :uid, :qid, :qorder, false, false)
                ON CONFLICT (challenge_id, user_id, question_id)
                DO UPDATE SET
                    question_order = EXCLUDED.question_order,
                    sent_at = now(),
                    answered = false,
                    timed_out = false
            """),
            {
                "cid": challenge_id,
                "uid": user_id,
                "qid": question.question_id,
                "qorder": question_order,
            },
        )

        await session.commit()

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "A",
                callback_data=f"challenge_answer_{challenge_id}|{question_order}|A|{question.question_id}",
            ),
            InlineKeyboardButton(
                "B",
                callback_data=f"challenge_answer_{challenge_id}|{question_order}|B|{question.question_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "C",
                callback_data=f"challenge_answer_{challenge_id}|{question_order}|C|{question.question_id}",
            ),
            InlineKeyboardButton(
                "D",
                callback_data=f"challenge_answer_{challenge_id}|{question_order}|D|{question.question_id}",
            ),
        ],
    ])

    text_message = (
        f"🧠 <b>Challenge Question {question_order}/{CHALLENGE_QUESTION_COUNT}</b>\n\n"
        f"<b>Category:</b> {CHALLENGE_CATEGORY_LABELS.get(question.category, question.category)}\n\n"
        f"{question.question}\n\n"
        f"A. {question.option_a}\n"
        f"B. {question.option_b}\n"
        f"C. {question.option_c}\n"
        f"D. {question.option_d}\n\n"
        f"⏳ <b>Time left:</b> {CHALLENGE_QUESTION_TIME_LIMIT}s"
    )

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=text_message,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

        asyncio.create_task(
            handle_challenge_question_timeout(
                context=context,
                challenge_id=challenge_id,
                user_id=user_id,
                question_id=question.question_id,
                question_order=question_order,
            )
        )

    except Exception:
        pass


# ==========================================================
# Show Challenge Lobby
# ==========================================================

async def show_challenge_lobby(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    challenge_id: int,
):
    async with AsyncSessionLocal() as session:
        # Get players
        result = await session.execute(
            text("""
                SELECT u.tg_id, u.username, u.full_name
                FROM challenge_players cp
                JOIN users u
                  ON u.tg_id = cp.user_id
                WHERE cp.challenge_id = :cid
                ORDER BY cp.joined_at ASC, cp.id ASC
            """),
            {"cid": challenge_id},
        )
        players = result.fetchall()

        # Get challenge info
        result = await session.execute(
            text("""
                SELECT creator_id, lobby_message_id, status
                FROM challenges
                WHERE id = :cid
            """),
            {"cid": challenge_id},
        )
        challenge_row = result.fetchone()

    if not challenge_row:
        return

    player_lines = []
    for idx, player in enumerate(players, start=1):
        name = player.username if player.username else player.full_name
        player_lines.append(f"{idx}. {name}")

    player_text = "\n".join(player_lines) if player_lines else "No players yet."
    player_count = len(players)

    current_user_id = update.effective_user.id if update.effective_user else None
    creator_id = challenge_row.creator_id
    lobby_message_id = challenge_row.lobby_message_id
    status = challenge_row.status

    keyboard = []

    if status == "waiting" and current_user_id == creator_id:
        keyboard.append([
            InlineKeyboardButton(
                "▶ Start Challenge",
                callback_data=f"challenge_start_{challenge_id}",
            )
        ])

    lobby_text = (
        "⚔️ <b>FRIEND CHALLENGE LOBBY</b>\n\n"
        f"<b>Players Joined ({player_count}):</b>\n"
        f"{player_text}\n\n"
        f"<b>Questions:</b> {CHALLENGE_QUESTION_COUNT}\n"
        f"<b>Minimum players:</b> 2\n\n"
        "Invite more friends, then start when ready."
    )

    # Try to edit existing lobby message
    if lobby_message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=lobby_message_id,
                text=lobby_text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
            )
            return
        except Exception:
            pass

    # If no editable lobby exists, create a new one
    sent_message = await update.effective_chat.send_message(
        lobby_text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
    )

    # Save the new lobby message ID
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
                UPDATE challenges
                SET lobby_message_id = :message_id
                WHERE id = :cid
            """),
            {
                "message_id": sent_message.message_id,
                "cid": challenge_id,
            },
        )
        await session.commit()


# ==========================================================
# Start Challenge - Show Category Picker
# ==========================================================

async def start_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    challenge_id = int(query.data.split("_")[2])
    user = query.from_user

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT creator_id, status
                FROM challenges
                WHERE id = :cid
            """),
            {"cid": challenge_id},
        )
        row = result.fetchone()

        result = await session.execute(
            text("""
                SELECT COUNT(*) AS total_players
                FROM challenge_players
                WHERE challenge_id = :cid
            """),
            {"cid": challenge_id},
        )
        count_row = result.fetchone()

    if not row or row.creator_id != user.id:
        await query.answer(
            "Only the challenge creator can start the game.",
            show_alert=True,
        )
        return

    if row.status not in ("waiting", "active", None):
        await query.answer(
            "This challenge has already started or finished.",
            show_alert=True,
        )
        return

    total_players = count_row.total_players if count_row else 0

    if total_players < 2:
        await query.answer(
            "At least 2 players are needed to start this challenge.",
            show_alert=True,
        )
        return

    keyboard = []
    for category in CHALLENGE_CATEGORIES:
        keyboard.append([
            InlineKeyboardButton(
                CHALLENGE_CATEGORY_LABELS.get(category, category),
                callback_data=f"challenge_category_{challenge_id}|{category}",
            )
        ])

    await query.message.edit_text(
        "🧠 <b>Select a category for this challenge</b>\n\n"
        f"<b>Players ready:</b> {total_players}\n\n"
        "Choose one category below to begin.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ==========================================================
# Choose Challenge Category And Start Questions
# ==========================================================

async def choose_challenge_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    payload = query.data.replace("challenge_category_", "", 1)

    try:
        challenge_id_str, category = payload.split("|", 1)
        challenge_id = int(challenge_id_str)
    except (ValueError, IndexError):
        await query.answer("Invalid category selection.", show_alert=True)
        return

    if category not in CHALLENGE_CATEGORIES:
        await query.answer("Invalid category.", show_alert=True)
        return

    user = query.from_user

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT creator_id
                FROM challenges
                WHERE id = :cid
            """),
            {"cid": challenge_id},
        )
        row = result.fetchone()

        if not row or row.creator_id != user.id:
            await query.answer(
                "Only the challenge creator can choose the category.",
                show_alert=True,
            )
            return

        await session.execute(
            text("""
                UPDATE challenges
                SET category = :category,
                    status = 'in_progress'
                WHERE id = :cid
            """),
            {
                "category": category,
                "cid": challenge_id,
            },
        )

        await session.commit()

    selected_questions = await generate_challenge_questions(
        challenge_id=challenge_id,
        category=category,
        question_count=CHALLENGE_QUESTION_COUNT,
    )

    if not selected_questions:
        await query.message.edit_text(
            "❌ Could not generate challenge questions for this category.",
            parse_mode="HTML",
        )
        return

    await query.message.edit_text(
        f"🚀 <b>Challenge Started!</b>\n\n"
        f"<b>Category:</b> {CHALLENGE_CATEGORY_LABELS.get(category, category)}\n"
        f"<b>Questions:</b> {CHALLENGE_QUESTION_COUNT}\n\n"
        f"All players will now receive Question 1.",
        parse_mode="HTML",
    )

    await send_challenge_question(
        context=context,
        challenge_id=challenge_id,
        question_order=1,
    )


# ==========================================================
# Handle Challenge Answer
# ==========================================================

async def handle_challenge_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    payload = query.data.replace("challenge_answer_", "", 1)
    challenge_id_str, question_order_str, selected_option, question_id_str = payload.split("|")

    challenge_id = int(challenge_id_str)
    question_order = int(question_order_str)
    question_id = int(question_id_str)
    user_id = query.from_user.id

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT answered, timed_out
                FROM challenge_question_delivery
                WHERE challenge_id = :cid
                  AND user_id = :uid
                  AND question_id = :qid
                LIMIT 1
            """),
            {
                "cid": challenge_id,
                "uid": user_id,
                "qid": question_id,
            },
        )

        delivery_row = result.fetchone()

        if not delivery_row:
            await query.answer("This question is no longer active.", show_alert=True)
            return

        if delivery_row.timed_out:
            await query.answer("Time is up for this question.", show_alert=True)
            return

        if delivery_row.answered:
            await query.answer("You already answered this question.", show_alert=True)
            return

        result = await session.execute(
            text("""
                SELECT 1
                FROM challenge_answers
                WHERE challenge_id = :cid
                  AND user_id = :uid
                  AND question_id = :qid
                LIMIT 1
            """),
            {
                "cid": challenge_id,
                "uid": user_id,
                "qid": question_id,
            },
        )

        already_answered = result.fetchone()

        if already_answered:
            await query.answer("You already answered this question.", show_alert=True)
            return

        result = await session.execute(
            text("""
                SELECT correct_option
                FROM questions
                WHERE id = :qid
            """),
            {"qid": question_id},
        )

        row = result.fetchone()

        if not row:
            await query.answer("Question not found.", show_alert=True)
            return

        correct_option = row.correct_option
        is_correct = (selected_option == correct_option)

        await session.execute(
            text("""
                INSERT INTO challenge_answers
                (challenge_id, user_id, question_id, selected_option, is_correct)
                VALUES (:cid, :uid, :qid, :selected_option, :is_correct)
            """),
            {
                "cid": challenge_id,
                "uid": user_id,
                "qid": question_id,
                "selected_option": selected_option,
                "is_correct": is_correct,
            },
        )

        await session.execute(
            text("""
                UPDATE challenge_question_delivery
                SET answered = true
                WHERE challenge_id = :cid
                  AND user_id = :uid
                  AND question_id = :qid
            """),
            {
                "cid": challenge_id,
                "uid": user_id,
                "qid": question_id,
            },
        )

        if is_correct:
            await session.execute(
                text("""
                    UPDATE challenge_players
                    SET score = score + 1
                    WHERE challenge_id = :cid
                      AND user_id = :uid
                """),
                {
                    "cid": challenge_id,
                    "uid": user_id,
                },
            )

        await session.commit()

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    if is_correct:
        await query.message.reply_text("✅ Correct!")
    else:
        await query.message.reply_text(f"❌ Wrong! Correct answer: {correct_option}")

    next_question_order = question_order + 1

    if next_question_order <= CHALLENGE_QUESTION_COUNT:
        await send_challenge_question_to_one_player(
            context=context,
            challenge_id=challenge_id,
            question_order=next_question_order,
            user_id=user_id,
        )
    else:
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("""
                    UPDATE challenge_players
                    SET completed = true
                    WHERE challenge_id = :cid
                      AND user_id = :uid
                """),
                {
                    "cid": challenge_id,
                    "uid": user_id,
                },
            )
            await session.commit()

        await query.message.reply_text("🏁 You have completed the challenge!")
        await maybe_finish_challenge(update, context, challenge_id)
        
                
# ==========================================================
# Maybe Finish Challenge
# ==========================================================

async def maybe_finish_challenge(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    challenge_id: int,
):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT COUNT(*) AS remaining
                FROM challenge_players
                WHERE challenge_id = :cid
                  AND completed = false
            """),
            {"cid": challenge_id},
        )

        row = result.fetchone()
        remaining = row.remaining if row else 0

        if remaining == 0:
            await session.execute(
                text("""
                    UPDATE challenges
                    SET status = 'completed'
                    WHERE id = :cid
                """),
                {"cid": challenge_id},
            )
            await session.commit()

            await show_challenge_result(update, context, challenge_id)


# ==========================================================
# Show Challenge Result
# ==========================================================

async def show_challenge_result(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    challenge_id: int,
):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT cp.user_id, u.username, u.full_name, cp.score
                FROM challenge_players cp
                JOIN users u
                  ON u.tg_id = cp.user_id
                WHERE cp.challenge_id = :challenge_id
                ORDER BY cp.score DESC, cp.joined_at ASC
            """),
            {"challenge_id": challenge_id},
        )
        rows = result.fetchall()

    if not rows:
        return

    result_lines = []

    for row in rows:
        name = row.full_name or row.username or f"user_{row.user_id}"
        result_lines.append(f"{name} — {row.score}/{CHALLENGE_QUESTION_COUNT}")

    winner = rows[0].full_name or rows[0].username or f"user_{rows[0].user_id}"

    message = (
        "⚔️ <b>CHALLENGE RESULT</b>\n\n"
        + "\n".join(result_lines)
        + f"\n\n🏆 Winner: <b>{winner}</b>\n\n"
        "🔥 Want to climb the global leaderboard?\n\n"
        "Play Trivia to compete for:\n\n"
        "📱 <b>iPhone 17 Pro Max</b>\n"
        "📱 <b>Samsung Z Flip</b>\n"
        "🎧 <b>AirPods</b>\n"
        "🔊 <b>Bluetooth Speakers</b>\n"
        "📞 Instant <b>Airtime Rewards</b> for Premium Points Milestones"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🧠 Play Trivia Questions",
                callback_data="playtrivia",
            )
        ]
    ])

    # Send result to every player in the challenge
    for row in rows:
        try:
            await context.bot.send_message(
                chat_id=row.user_id,
                text=message,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
        except Exception:
            pass


# ==========================================================
# Register Handlers
# ==========================================================

def register_handlers(application):
    application.add_handler(
        CommandHandler("challenge", create_challenge)
    )

    application.add_handler(
        CallbackQueryHandler(
            create_challenge,
            pattern="^challenge:start$",
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex("^⚔️ Challenge Friends$"),
            create_challenge,
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            start_challenge,
            pattern="^challenge_start_",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            choose_challenge_category,
            pattern="^challenge_category_",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            handle_challenge_answer,
            pattern="^challenge_answer_",
        )
    )
