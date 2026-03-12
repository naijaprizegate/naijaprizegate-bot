# ====================================================================
# services/battle_service.py
# ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
from __future__ import annotations

import json
import random
import string
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from logger import logger


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
def generate_room_code(length: int = 6) -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))


async def _room_code_exists(session: AsyncSession, room_code: str) -> bool:
    res = await session.execute(
        text("""
            SELECT 1
            FROM battle_rooms
            WHERE room_code = :room_code
            LIMIT 1
        """),
        {"room_code": room_code},
    )
    return res.first() is not None


async def generate_unique_room_code(session: AsyncSession, length: int = 6) -> str:
    for _ in range(20):
        code = generate_room_code(length)
        if not await _room_code_exists(session, code):
            return code
    raise RuntimeError("Could not generate unique room code")


# ------------------------------------------------------------
# Create battle room
# ------------------------------------------------------------
async def create_battle_room(
    session: AsyncSession,
    *,
    host_tg_id: int,
    host_display_name: str,
    category: str,
    max_players: int,
    question_count: int,
    duration_seconds: int,
) -> dict:
    """
    Creates a new battle room and automatically adds the host
    as the first player.
    """
    room_code = await generate_unique_room_code(session)

    room_res = await session.execute(
        text("""
            INSERT INTO battle_rooms (
                room_code,
                host_tg_id,
                category,
                max_players,
                question_count,
                duration_seconds,
                status
            )
            VALUES (
                :room_code,
                :host_tg_id,
                :category,
                :max_players,
                :question_count,
                :duration_seconds,
                'waiting'
            )
            RETURNING id, room_code, host_tg_id, category, max_players,
                      question_count, duration_seconds, status, created_at
        """),
        {
            "room_code": room_code,
            "host_tg_id": host_tg_id,
            "category": category,
            "max_players": max_players,
            "question_count": question_count,
            "duration_seconds": duration_seconds,
        },
    )
    room = room_res.mappings().first()

    if not room:
        raise RuntimeError("Failed to create battle room")

    await session.execute(
        text("""
            INSERT INTO battle_players (
                battle_id,
                tg_id,
                display_name
            )
            VALUES (
                :battle_id,
                :tg_id,
                :display_name
            )
        """),
        {
            "battle_id": room["id"],
            "tg_id": host_tg_id,
            "display_name": host_display_name,
        },
    )

    logger.info(
        "🔥 Battle room created | battle_id=%s | room_code=%s | host_tg_id=%s",
        room["id"],
        room["room_code"],
        host_tg_id,
    )

    return dict(room)


# ------------------------------------------------------------
# Join battle room
# ------------------------------------------------------------
async def join_battle_room(
    session: AsyncSession,
    *,
    room_code: str,
    tg_id: int,
    display_name: str,
) -> dict:
    """
    Lets a player join an existing waiting room.
    """
    room_res = await session.execute(
        text("""
            SELECT id, room_code, host_tg_id, category, max_players,
                   question_count, duration_seconds, status
            FROM battle_rooms
            WHERE room_code = :room_code
            LIMIT 1
            FOR UPDATE
        """),
        {"room_code": room_code},
    )
    room = room_res.mappings().first()

    if not room:
        return {"ok": False, "error": "Room not found."}

    if room["status"] != "waiting":
        return {"ok": False, "error": "This battle has already started or ended."}

    existing_res = await session.execute(
        text("""
            SELECT 1
            FROM battle_players
            WHERE battle_id = :battle_id
              AND tg_id = :tg_id
            LIMIT 1
        """),
        {
            "battle_id": room["id"],
            "tg_id": tg_id,
        },
    )
    already_joined = existing_res.first() is not None

    if already_joined:
        return {"ok": True, "room": dict(room), "message": "You already joined this room."}

    count_res = await session.execute(
        text("""
            SELECT COUNT(*) AS total
            FROM battle_players
            WHERE battle_id = :battle_id
        """),
        {"battle_id": room["id"]},
    )
    total_players = count_res.scalar_one()

    if total_players >= room["max_players"]:
        return {"ok": False, "error": "This battle room is already full."}

    await session.execute(
        text("""
            INSERT INTO battle_players (
                battle_id,
                tg_id,
                display_name
            )
            VALUES (
                :battle_id,
                :tg_id,
                :display_name
            )
        """),
        {
            "battle_id": room["id"],
            "tg_id": tg_id,
            "display_name": display_name,
        },
    )

    logger.info(
        "👥 Battle room joined | battle_id=%s | room_code=%s | tg_id=%s",
        room["id"],
        room["room_code"],
        tg_id,
    )

    return {"ok": True, "room": dict(room), "message": "Joined successfully."}


# ------------------------------------------------------------
# Get battle room details
# ------------------------------------------------------------
async def get_battle_room(session: AsyncSession, room_code: str) -> Optional[dict]:
    res = await session.execute(
        text("""
            SELECT id, room_code, host_tg_id, category, max_players,
                   question_count, duration_seconds, status,
                   created_at, started_at, ends_at, finished_at, winner_tg_id
            FROM battle_rooms
            WHERE room_code = :room_code
            LIMIT 1
        """),
        {"room_code": room_code},
    )
    row = res.mappings().first()
    return dict(row) if row else None


# ------------------------------------------------------------
# Get battle players
# ------------------------------------------------------------
async def get_battle_players(session: AsyncSession, battle_id: str) -> list[dict]:
    res = await session.execute(
        text("""
            SELECT tg_id, display_name, joined_at,
                   current_question_index, correct_count,
                   wrong_count, skipped_count, answered_count, is_finished
            FROM battle_players
            WHERE battle_id = :battle_id
            ORDER BY joined_at ASC
        """),
        {"battle_id": battle_id},
    )
    return [dict(row) for row in res.mappings().all()]


# ------------------------------------------------------------
# Format lobby text
# ------------------------------------------------------------
def build_battle_lobby_text(room: dict, players: list[dict], bot_username: str) -> str:
    joined_count = len(players)
    lines = []

    for i, p in enumerate(players, start=1):
        name = p.get("display_name") or str(p.get("tg_id"))
        lines.append(f"{i}. {name}")

    joined_text = "\n".join(lines) if lines else "No players yet."

    invite_link = f"https://t.me/{bot_username}?start=battle_{room['room_code']}"

    return (
        "🔥 *Battle Room Created*\n\n"
        f"*Room Code:* `{room['room_code']}`\n"
        f"*Category:* {room['category']}\n"
        f"*Questions:* {room['question_count']}\n"
        f"*Time:* {room['duration_seconds']} seconds\n"
        f"*Players:* {joined_count}/{room['max_players']}\n\n"
        "*Joined Players:*\n"
        f"{joined_text}\n\n"
        "*Invite friends with this link:*\n"
        f"{invite_link}"
    )
