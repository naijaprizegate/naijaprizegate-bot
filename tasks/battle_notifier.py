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
        [InlineKeyboardButton("🔥 Battle Again", callback_data="battle:menu")],
        [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
    ])


async def process_finished_battles(bot: Bot):
    # --------------------------------------------------------
    # Step 1: Read expired active battles
    # --------------------------------------------------------
    async with get_async_session() as session:
        battles = await get_expired_active_battles(session)

    if not battles:
        return

    logger.info("🏁 Found %s expired active battle(s)", len(battles))

    # --------------------------------------------------------
    # Step 2: Process each battle in its own fresh session
    # --------------------------------------------------------
    for battle in battles:
        room_code = battle["room_code"]
        battle_id = str(battle["id"])

        try:
            async with get_async_session() as session:
                await close_unfinished_players(session, battle_id)
                result = await finalize_battle_result(session, battle_id)
                player_ids = await get_battle_player_ids(session, battle_id)
                await session.commit()

            if not result.get("ok"):
                logger.warning(
                    "⚠️ Could not finalize battle | room_code=%s | battle_id=%s | error=%s",
                    room_code,
                    battle_id,
                    result.get("error"),
                )
                continue

            result_text = build_battle_result_text(result)
            keyboard = _battle_result_keyboard()

            for tg_id in player_ids:
                try:
                    await bot.send_message(
                        chat_id=tg_id,
                        text=result_text,
                        parse_mode="HTML",
                        reply_markup=keyboard,
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
