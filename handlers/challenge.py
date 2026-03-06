# ==========================================================
# handlers/challenge.py
# ==========================================================

import random
from telegram import Update
from telegram.ext import ContextTypes, CommandHandler
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

    except Exception as e:

        await update.message.reply_text(
            "❌ Could not create challenge. Please try again."
        )

        return


    # generate invite link
    bot_username = context.bot.username

    invite_link = f"https://t.me/{bot_username}?start=challenge_{challenge_id}"


    await update.message.reply_text(

        f"🎯 <b>Friend Challenge Created!</b>\n\n"
        f"Invite your friends to compete with you.\n\n"
        f"<b>Challenge Questions:</b> 5\n\n"
        f"Share this link:\n{invite_link}",

        parse_mode="HTML",
    )


# ==========================================================
# Register Handlers
# ==========================================================

def register_challenge_handlers(application):

    application.add_handler(
        CommandHandler("challenge", create_challenge)
    )
