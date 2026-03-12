# ============================================================
# tasks/battle_notifier.py
# ============================================================
from __future__ import annotations

import asyncio

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from db import get_async_session
from logger import logger
from services.battle_service import (
    get_expired_active_battles,
    close_unfinished_players,
    finalize_battle_result,
    get_battle_player_ids,
    build_battle_result_text,
)

BATTLE_LOOP_SECONDS = 5


def _battle_result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 Play Paid Trivia", callback_data="buy")],
        [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard:show")],
    ])


async def process_finished_battles(bot: Bot):
    async with get_async_session() as session:
        try:
            rooms = await get_expired_active_battles(session)
        except Exception:
            logger.exception("❌ Failed to fetch expired active battles")
            return

        if not rooms:
            return

        logger.info("🏁 Found %s expired active battle(s)", len(rooms))

        for room in rooms:
            battle_id = str(room["id"])
            room_code = room["room_code"]

            try:
                async with session.begin():
                    await close_unfinished_players(session, battle_id)
                    result = await finalize_battle_result(session, battle_id)

                if not result["ok"]:
                    logger.warning(
                        "⚠️ Could not finalize battle | room_code=%s | battle_id=%s",
                        room_code,
                        battle_id,
                    )
                    continue

                player_ids = await get_battle_player_ids(session, battle_id)
                result_text = build_battle_result_text(result)

                for tg_id in player_ids:
                    try:
                        await bot.send_message(
                            chat_id=tg_id,
                            text=result_text,
                            parse_mode="Markdown",
                            reply_markup=_battle_result_keyboard(),
                        )
                    except Exception:
                        logger.exception(
                            "❌ Failed to send battle result | room_code=%s | tg_id=%s",
                            room_code,
                            tg_id,
                        )

                logger.info(
                    "✅ Battle finalized and announced | room_code=%s | battle_id=%s",
                    room_code,
                    battle_id,
                )

            except Exception:
                logger.exception(
                    "❌ Failed to process finished battle | room_code=%s | battle_id=%s",
                    room_code,
                    battle_id,
                )


async def battle_notifier_loop(bot: Bot):
    logger.info("🚀 Battle notifier started...")
    while True:
        try:
            await process_finished_battles(bot)
        except Exception as e:
            logger.exception("Battle notifier loop error: %s", e)

        await asyncio.sleep(BATTLE_LOOP_SECONDS)
