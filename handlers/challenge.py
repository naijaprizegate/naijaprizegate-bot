# ==========================================================
# handlers/challenge.py
# ==========================================================

import random
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from sqlalchemy import text

from db import AsyncSessionLocal


# ==========================================================
# CONFIG
# ==========================================================

TOTAL_QUESTIONS = 700   # change to your real number


# ==========================================================
# Create Challenge
# ==========================================================

async def create_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    # handle button or message safely
    message = update.message or update.callback_query.message

    # pick random question slice
    start_q = random.randint(1, TOTAL_QUESTIONS - 5)
    end_q = start_q + 4

    try:

        async with AsyncSessionLocal() as session:

            result = await session.execute(
                text("""
                    INSERT INTO challenges
                    (creator_id, question_start, question_end)
                    VALUES (:creator_id, :start_q, :end_q)
                    RETURNING id
                """),
                {
                    "creator_id": int(user.id),
                    "start_q": start_q,
                    "end_q": end_q,
                },
            )

            challenge_id = result.scalar()

            await session.commit()

    except Exception:

        await message.reply_text(
            "❌ Could not create challenge. Please try again."
        )

        return


    # generate invite link
    bot_username = context.bot.username
    invite_link = f"https://t.me/{bot_username}?start=challenge_{challenge_id}"


    await message.reply_text(

        f"⚔️ <b>Friend Challenge Created!</b>\n\n"
        f"Invite your friends to compete with you.\n\n"
        f"<b>Challenge Questions:</b> 5\n\n"
        f"Share this link with friends:\n\n"
        f"{invite_link}",

        parse_mode="HTML",
    )

# ==========================================================
# Join Challenge
# ==========================================================

async def join_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:
        return False

    arg = context.args[0]

    if not arg.startswith("challenge_"):
        return False

    challenge_id = int(arg.split("_")[1])
    user = update.effective_user

    async with AsyncSessionLocal() as session:

        # add player to challenge_players
        await session.execute(
            text("""
                INSERT INTO challenge_players (challenge_id, user_id)
                VALUES (:challenge_id, :user_id)
                ON CONFLICT DO NOTHING
            """),
            {
                "challenge_id": challenge_id,
                "user_id": user.id
            }
        )

        await session.commit()

    await update.message.reply_text(

        "⚔️ *Friend Challenge Joined!*\n\n"
        "You have joined a trivia challenge.\n\n"
        "Both players will answer the same 5 questions.\n\n"
        "Press *Play Trivia Questions* to begin!",

        parse_mode="Markdown"
    )

    return True

# ==========================================================
# Register Handlers
# ==========================================================

def register_handlers(application):

    # /challenge command
    application.add_handler(
        CommandHandler("challenge", create_challenge)
    )

    # menu button press
    application.add_handler(
        CallbackQueryHandler(
            create_challenge,
            pattern="^challenge:start$"
        )
    )

    # text button fallback
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex("^⚔️ Challenge Friends$"),
            create_challenge
        )
    )
