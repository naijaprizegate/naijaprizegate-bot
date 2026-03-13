# ====================================================================
# services/battle_service.py
# ====================================================================
from __future__ import annotations

import random
import string
import json
import html
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
                      question_count, duration_seconds, status,
                      created_at, host_chat_id, host_lobby_message_id
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
# Get room by code FOR UPDATE
# ------------------------------------------------------------
async def get_battle_room_for_update(session: AsyncSession, room_code: str) -> Optional[dict]:
    res = await session.execute(
        text("""
            SELECT id, room_code, host_tg_id, category, max_players,
                   question_count, duration_seconds, status,
                   question_ids, created_at, started_at, ends_at,
                   finished_at, winner_tg_id,
                   host_chat_id, host_lobby_message_id
            FROM battle_rooms
            WHERE room_code = :room_code
            LIMIT 1
            FOR UPDATE
        """),
        {"room_code": room_code},
    )
    row = res.mappings().first()
    return dict(row) if row else None


# ------------------------------------------------------------
# Get room players full list
# ------------------------------------------------------------
async def get_battle_players_full(session: AsyncSession, battle_id: str) -> list[dict]:
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
# Pick battle questions from questions table
# Same source used by Challenge Mode
# ------------------------------------------------------------
async def pick_battle_questions(
    session: AsyncSession,
    *,
    category: str,
    question_count: int,
) -> list[int]:
    res = await session.execute(
        text("""
            SELECT id
            FROM questions
            WHERE category = :category
            ORDER BY question_order ASC
            LIMIT :question_count
        """),
        {
            "category": category,
            "question_count": question_count,
        },
    )
    return [int(row[0]) for row in res.fetchall()]


# ------------------------------------------------------------
# Get one battle question by id from questions table
# ------------------------------------------------------------
async def get_trivia_question_by_id(session: AsyncSession, question_id: int) -> Optional[dict]:
    res = await session.execute(
        text("""
            SELECT
                id,
                category,
                question,
                option_a,
                option_b,
                option_c,
                option_d,
                correct_option
            FROM questions
            WHERE id = :question_id
            LIMIT 1
        """),
        {"question_id": question_id},
    )
    row = res.mappings().first()
    if not row:
        return None

    return {
        "id": int(row["id"]),
        "category": row["category"],
        "question": row["question"],
        "options": {
            "A": row["option_a"],
            "B": row["option_b"],
            "C": row["option_c"],
            "D": row["option_d"],
        },
        "answer": row["correct_option"],
    }

# ------------------------------------------------------------
# Start battle room
# ------------------------------------------------------------
async def start_battle_room(
    session: AsyncSession,
    *,
    room_code: str,
    requester_tg_id: int,
) -> dict:
    room = await get_battle_room_for_update(session, room_code)
    if not room:
        return {"ok": False, "error": "Battle room not found."}

    if int(room["host_tg_id"]) != int(requester_tg_id):
        return {"ok": False, "error": "Only the host can start this battle."}

    if room["status"] != "waiting":
        return {"ok": False, "error": "This battle has already started or ended."}

    players = await get_battle_players_full(session, str(room["id"]))
    if len(players) < 2:
        return {"ok": False, "error": "At least 2 players are needed to start the battle."}

    question_ids = await pick_battle_questions(
        session,
        category=room["category"],
        question_count=int(room["question_count"]),
    )

    if len(question_ids) < int(room["question_count"]):
        return {
            "ok": False,
            "error": f"Not enough questions found in category '{room['category']}'.",
        }

    await session.execute(
        text("""
            UPDATE battle_rooms
            SET status = 'active',
                question_ids = CAST(:question_ids AS jsonb),
                started_at = NOW(),
                ends_at = NOW() + (:duration_seconds * INTERVAL '1 second')
            WHERE id = :battle_id
        """),
        {
            "battle_id": room["id"],
            "question_ids": json.dumps(question_ids),
            "duration_seconds": int(room["duration_seconds"]),
        },
    )

    logger.info(
        "🚀 Battle started | room_code=%s | battle_id=%s | host_tg_id=%s | players=%s",
        room_code,
        room["id"],
        requester_tg_id,
        len(players),
    )

    return {
        "ok": True,
        "room_id": str(room["id"]),
        "room_code": room_code,
        "question_ids": question_ids,
        "players": players,
    }


# ------------------------------------------------------------
# Save host lobby message metadata
# ------------------------------------------------------------
async def save_host_lobby_message(
    session: AsyncSession,
    *,
    room_code: str,
    host_chat_id: int,
    host_lobby_message_id: int,
) -> None:
    await session.execute(
        text("""
            UPDATE battle_rooms
            SET host_chat_id = :host_chat_id,
                host_lobby_message_id = :host_lobby_message_id
            WHERE room_code = :room_code
        """),
        {
            "room_code": room_code,
            "host_chat_id": host_chat_id,
            "host_lobby_message_id": host_lobby_message_id,
        },
    )


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
    room_res = await session.execute(
        text("""
            SELECT id, room_code, host_tg_id, category, max_players,
                   question_count, duration_seconds, status,
                   host_chat_id, host_lobby_message_id
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
                   created_at, started_at, ends_at, finished_at, winner_tg_id,
                   host_chat_id, host_lobby_message_id
            FROM battle_rooms
            WHERE room_code = :room_code
            LIMIT 1
        """),
        {"room_code": room_code},
    )
    row = res.mappings().first()
    return dict(row) if row else None


# ------------------------------------------------------------
# Get battle room by id
# ------------------------------------------------------------
async def get_battle_room_by_id(session: AsyncSession, battle_id: str) -> Optional[dict]:
    res = await session.execute(
        text("""
            SELECT id, room_code, host_tg_id, category, max_players,
                   question_count, duration_seconds, status,
                   created_at, started_at, ends_at, finished_at, winner_tg_id,
                   host_chat_id, host_lobby_message_id
            FROM battle_rooms
            WHERE id = :battle_id
            LIMIT 1
        """),
        {"battle_id": battle_id},
    )
    row = res.mappings().first()
    return dict(row) if row else None


# ------------------------------------------------------------
# Get battle players
# Prefer real names from users table
# ------------------------------------------------------------
async def get_battle_players(session: AsyncSession, battle_id: str) -> list[dict]:
    res = await session.execute(
        text("""
            SELECT
                bp.tg_id,
                COALESCE(NULLIF(u.full_name, ''), NULLIF(u.username, ''), NULLIF(bp.display_name, ''), CAST(bp.tg_id AS text)) AS display_name,
                bp.joined_at,
                bp.current_question_index,
                bp.correct_count,
                bp.wrong_count,
                bp.skipped_count,
                bp.answered_count,
                bp.is_finished
            FROM battle_players bp
            LEFT JOIN users u
              ON u.tg_id = bp.tg_id
            WHERE bp.battle_id = :battle_id
            ORDER BY bp.joined_at ASC
        """),
        {"battle_id": battle_id},
    )
    return [dict(row) for row in res.mappings().all()]


# ------------------------------------------------------------
# Format lobby text
# ------------------------------------------------------------
def build_battle_lobby_text(room: dict, players: list[dict], bot_username: str) -> str:
    import html

    category_labels = {
        "nigeria_history": "Nigeria History",
        "geography": "Geography",
        "nigeria_entertainment": "Entertainment",
        "sciences": "Sciences",
        "mathematics": "Mathematics",
        "english": "English",
        "football": "Football",
    }

    joined_count = len(players)
    lines = []

    for i, p in enumerate(players, start=1):
        name = p.get("display_name") or str(p.get("tg_id"))
        lines.append(f"{i}. {html.escape(str(name))}")

    joined_text = "\n".join(lines) if lines else "No players yet."
    category = category_labels.get(room.get("category"), str(room.get("category") or ""))
    category = html.escape(category)

    room_code = html.escape(str(room["room_code"]))
    invite_link = f"https://t.me/{bot_username}?start=battle_{room['room_code']}"

    return (
        "🔥 <b>BATTLE ROOM CREATED</b>\n\n"
        f"<b>Room Code:</b> <code>{room_code}</code>\n"
        f"<b>Category:</b> {category}\n"
        f"<b>Questions:</b> {room['question_count']}\n"
        f"<b>Time:</b> {room['duration_seconds']} seconds\n"
        f"<b>Players Joined ({joined_count}/{room['max_players']}):</b>\n"
        f"{joined_text}\n\n"
        "<b>Invite friends with this link:</b>\n"
        f"{invite_link}"
    )

# ------------------------------------------------------------
# Get active battle state for a player
# ------------------------------------------------------------
async def get_player_battle_state(
    session: AsyncSession,
    *,
    room_code: str,
    tg_id: int,
) -> Optional[dict]:
    res = await session.execute(
        text("""
            SELECT
                br.id AS battle_id,
                br.room_code,
                br.category,
                br.question_count,
                br.duration_seconds,
                br.status,
                br.question_ids,
                br.started_at,
                br.ends_at,
                bp.tg_id,
                bp.current_question_index,
                bp.correct_count,
                bp.wrong_count,
                bp.skipped_count,
                bp.answered_count,
                bp.is_finished
            FROM battle_rooms br
            JOIN battle_players bp
              ON bp.battle_id = br.id
            WHERE br.room_code = :room_code
              AND bp.tg_id = :tg_id
            LIMIT 1
        """),
        {
            "room_code": room_code,
            "tg_id": tg_id,
        },
    )
    row = res.mappings().first()
    return dict(row) if row else None


# ------------------------------------------------------------
# Get question ids list from room row
# ------------------------------------------------------------
def parse_question_ids(raw_question_ids) -> list[int]:
    if not raw_question_ids:
        return []

    if isinstance(raw_question_ids, list):
        return [int(x) for x in raw_question_ids]

    if isinstance(raw_question_ids, str):
        data = json.loads(raw_question_ids)
        return [int(x) for x in data]

    return []


# ------------------------------------------------------------
# Has player already answered this question?
# ------------------------------------------------------------
async def has_player_answered_question(
    session: AsyncSession,
    *,
    battle_id: str,
    tg_id: int,
    question_id: int,
) -> bool:
    res = await session.execute(
        text("""
            SELECT 1
            FROM battle_answers
            WHERE battle_id = :battle_id
              AND tg_id = :tg_id
              AND question_id = :question_id
            LIMIT 1
        """),
        {
            "battle_id": battle_id,
            "tg_id": tg_id,
            "question_id": question_id,
        },
    )
    return res.first() is not None


# ------------------------------------------------------------
# Record answer and update player summary
# ------------------------------------------------------------
async def record_battle_answer(
    session: AsyncSession,
    *,
    battle_id: str,
    tg_id: int,
    question_id: int,
    question_index: int,
    selected_option: str | None,
    is_correct: bool,
    was_skipped: bool,
) -> None:
    await session.execute(
        text("""
            INSERT INTO battle_answers (
                battle_id,
                tg_id,
                question_id,
                question_index,
                selected_option,
                is_correct,
                was_skipped
            )
            VALUES (
                :battle_id,
                :tg_id,
                :question_id,
                :question_index,
                :selected_option,
                :is_correct,
                :was_skipped
            )
        """),
        {
            "battle_id": battle_id,
            "tg_id": tg_id,
            "question_id": question_id,
            "question_index": question_index,
            "selected_option": selected_option,
            "is_correct": is_correct,
            "was_skipped": was_skipped,
        },
    )

    await session.execute(
        text("""
            UPDATE battle_players
            SET
                current_question_index = current_question_index + 1,
                answered_count = answered_count + 1,
                correct_count = correct_count + CASE WHEN :is_correct THEN 1 ELSE 0 END,
                wrong_count = wrong_count + CASE
                    WHEN :was_skipped THEN 0
                    WHEN :is_correct THEN 0
                    ELSE 1
                END,
                skipped_count = skipped_count + CASE WHEN :was_skipped THEN 1 ELSE 0 END
            WHERE battle_id = :battle_id
              AND tg_id = :tg_id
        """),
        {
            "battle_id": battle_id,
            "tg_id": tg_id,
            "is_correct": is_correct,
            "was_skipped": was_skipped,
        },
    )


# ------------------------------------------------------------
# Mark player finished if done
# ------------------------------------------------------------
async def mark_player_finished_if_done(
    session: AsyncSession,
    *,
    battle_id: str,
    tg_id: int,
    question_count: int,
) -> bool:
    res = await session.execute(
        text("""
            SELECT current_question_index, is_finished
            FROM battle_players
            WHERE battle_id = :battle_id
              AND tg_id = :tg_id
            LIMIT 1
        """),
        {
            "battle_id": battle_id,
            "tg_id": tg_id,
        },
    )
    row = res.first()
    if not row:
        return False

    current_index, is_finished = row
    if is_finished:
        return True

    if int(current_index) >= int(question_count):
        await session.execute(
            text("""
                UPDATE battle_players
                SET is_finished = TRUE,
                    finished_at = NOW()
                WHERE battle_id = :battle_id
                  AND tg_id = :tg_id
            """),
            {
                "battle_id": battle_id,
                "tg_id": tg_id,
            },
        )
        return True

    return False


# ------------------------------------------------------------
# Get current question for player
# ------------------------------------------------------------
async def get_current_battle_question_for_player(
    session: AsyncSession,
    *,
    room_code: str,
    tg_id: int,
) -> Optional[dict]:
    state = await get_player_battle_state(
        session,
        room_code=room_code,
        tg_id=tg_id,
    )
    if not state:
        return None

    question_ids = parse_question_ids(state.get("question_ids"))
    current_index = int(state.get("current_question_index") or 0)

    if current_index >= len(question_ids):
        return {
            "done": True,
            "state": state,
        }

    question_id = int(question_ids[current_index])
    q = await get_trivia_question_by_id(session, question_id)
    if not q:
        return None

    return {
        "done": False,
        "state": state,
        "question_index": current_index,
        "question_id": question_id,
        "question": q,
    }


# ------------------------------------------------------------
# Find active rooms that have expired
# ------------------------------------------------------------
async def get_expired_active_battles(session: AsyncSession) -> list[dict]:
    res = await session.execute(
        text("""
            SELECT id, room_code, host_tg_id, category, max_players,
                   question_count, duration_seconds, status,
                   question_ids, created_at, started_at, ends_at,
                   finished_at, winner_tg_id,
                   host_chat_id, host_lobby_message_id
            FROM battle_rooms
            WHERE status = 'active'
              AND ends_at IS NOT NULL
              AND ends_at <= NOW()
            ORDER BY ends_at ASC
        """)
    )
    return [dict(row) for row in res.mappings().all()]


# ------------------------------------------------------------
# Get final ranking for a battle
# ------------------------------------------------------------
async def get_battle_rankings(session: AsyncSession, battle_id: str) -> list[dict]:
    res = await session.execute(
        text("""
            SELECT
                tg_id,
                display_name,
                correct_count,
                wrong_count,
                skipped_count,
                answered_count,
                is_finished,
                finished_at
            FROM battle_players
            WHERE battle_id = :battle_id
            ORDER BY correct_count DESC,
                     wrong_count ASC,
                     joined_at ASC
        """),
        {"battle_id": battle_id},
    )
    return [dict(row) for row in res.mappings().all()]


# ------------------------------------------------------------
# Mark unfinished players as finished when battle ends
# ------------------------------------------------------------
async def close_unfinished_players(session: AsyncSession, battle_id: str) -> None:
    await session.execute(
        text("""
            UPDATE battle_players
            SET is_finished = TRUE,
                finished_at = COALESCE(finished_at, NOW())
            WHERE battle_id = :battle_id
              AND is_finished = FALSE
        """),
        {"battle_id": battle_id},
    )


# ------------------------------------------------------------
# Finalize battle result
# winner = highest correct_count
# tie = no winner_tg_id
# ------------------------------------------------------------
async def finalize_battle_result(session: AsyncSession, battle_id: str) -> dict:
    room = await get_battle_room_by_id(session, battle_id)
    if not room:
        return {"ok": False, "error": "Battle room not found."}

    rankings = await get_battle_rankings(session, battle_id)

    if not rankings:
        await session.execute(
            text("""
                UPDATE battle_rooms
                SET status = 'completed',
                    finished_at = NOW(),
                    winner_tg_id = NULL
                WHERE id = :battle_id
            """),
            {"battle_id": battle_id},
        )
        return {
            "ok": True,
            "room": room,
            "rankings": [],
            "winner_tg_id": None,
            "is_draw": True,
        }

    winner_tg_id = None
    is_draw = False

    if len(rankings) == 1:
        winner_tg_id = rankings[0]["tg_id"]
    else:
        top_score = int(rankings[0]["correct_count"] or 0)
        second_score = int(rankings[1]["correct_count"] or 0)

        if top_score == second_score:
            is_draw = True
            winner_tg_id = None
        else:
            winner_tg_id = rankings[0]["tg_id"]

    await session.execute(
        text("""
            UPDATE battle_rooms
            SET status = 'completed',
                finished_at = NOW(),
                winner_tg_id = :winner_tg_id
            WHERE id = :battle_id
        """),
        {
            "battle_id": battle_id,
            "winner_tg_id": winner_tg_id,
        },
    )

    return {
        "ok": True,
        "room": room,
        "rankings": rankings,
        "winner_tg_id": winner_tg_id,
        "is_draw": is_draw,
    }


# ------------------------------------------------------------
# Get all tg_ids in battle
# ------------------------------------------------------------
async def get_battle_player_ids(session: AsyncSession, battle_id: str) -> list[int]:
    res = await session.execute(
        text("""
            SELECT tg_id
            FROM battle_players
            WHERE battle_id = :battle_id
            ORDER BY joined_at ASC
        """),
        {"battle_id": battle_id},
    )
    return [int(row[0]) for row in res.fetchall()]


# ------------------------------------------------------------
# Build final result text
# ------------------------------------------------------------
def build_battle_result_text(result: dict) -> str:
    rankings = result["rankings"]
    is_draw = result["is_draw"]

    if not rankings:
        return (
            "🏁 *Battle Over!*\n\n"
            "No valid player results were found."
        )

    lines = []
    medals = ["🥇", "🥈", "🥉"]

    for i, row in enumerate(rankings, start=1):
        icon = medals[i - 1] if i <= 3 else f"{i}."
        name = row.get("display_name") or str(row.get("tg_id"))
        correct = int(row.get("correct_count") or 0)
        wrong = int(row.get("wrong_count") or 0)
        skipped = int(row.get("skipped_count") or 0)

        lines.append(
            f"{icon} {name} — ✅ {correct} | ❌ {wrong} | ⏭ {skipped}"
        )

    board = "\n".join(lines)

    if is_draw:
        winner_line = "🤝 *Result:* It's a draw!"
    else:
        winner_name = rankings[0].get("display_name") or str(rankings[0].get("tg_id"))
        winner_line = f"🏆 *Winner:* {winner_name}"

    return (
        "🏁 *Battle Over!*\n\n"
        f"{winner_line}\n\n"
        "*Final Ranking:*\n"
        f"{board}\n\n"
        "🎁 Want bigger rewards?\n"
        "Play *Paid Trivia Questions* to compete for iPhone, Samsung, AirPods, Bluetooth speaker and airtime milestones."
    )


# ------------------------------------------------------------
# Cancel battle room
# ------------------------------------------------------------
async def cancel_battle_room(
    session: AsyncSession,
    *,
    room_code: str,
    requester_tg_id: int,
) -> dict:
    room = await get_battle_room_for_update(session, room_code)
    if not room:
        return {"ok": False, "error": "Battle room not found."}

    if int(room["host_tg_id"]) != int(requester_tg_id):
        return {"ok": False, "error": "Only the host can cancel this battle."}

    if room["status"] != "waiting":
        return {"ok": False, "error": "Only waiting rooms can be cancelled."}

    await session.execute(
        text("""
            UPDATE battle_rooms
            SET status = 'cancelled',
                finished_at = NOW()
            WHERE id = :battle_id
        """),
        {"battle_id": room["id"]},
    )

    logger.info(
        "❌ Battle room cancelled | room_code=%s | host_tg_id=%s",
        room_code,
        requester_tg_id,
    )

    return {"ok": True, "room": room}



# ------------------------------------------------------------
# Create or reset battle draft for host
# ------------------------------------------------------------
async def create_or_reset_battle_draft(session: AsyncSession, host_tg_id: int) -> None:
    await session.execute(
        text("""
            INSERT INTO battle_room_drafts (
                host_tg_id,
                category,
                question_count,
                duration_seconds,
                max_players,
                created_at,
                updated_at
            )
            VALUES (
                :host_tg_id,
                NULL,
                NULL,
                NULL,
                NULL,
                NOW(),
                NOW()
            )
            ON CONFLICT (host_tg_id)
            DO UPDATE SET
                category = NULL,
                question_count = NULL,
                duration_seconds = NULL,
                max_players = NULL,
                updated_at = NOW()
        """),
        {"host_tg_id": host_tg_id},
    )


# ------------------------------------------------------------
# Update draft category
# ------------------------------------------------------------
async def set_battle_draft_category(
    session: AsyncSession,
    *,
    host_tg_id: int,
    category: str,
) -> None:
    await session.execute(
        text("""
            UPDATE battle_room_drafts
            SET category = :category,
                updated_at = NOW()
            WHERE host_tg_id = :host_tg_id
        """),
        {
            "host_tg_id": host_tg_id,
            "category": category,
        },
    )


# ------------------------------------------------------------
# Update draft question count
# ------------------------------------------------------------
async def set_battle_draft_question_count(
    session: AsyncSession,
    *,
    host_tg_id: int,
    question_count: int,
) -> None:
    await session.execute(
        text("""
            UPDATE battle_room_drafts
            SET question_count = :question_count,
                updated_at = NOW()
            WHERE host_tg_id = :host_tg_id
        """),
        {
            "host_tg_id": host_tg_id,
            "question_count": question_count,
        },
    )


# ------------------------------------------------------------
# Update draft duration
# ------------------------------------------------------------
async def set_battle_draft_duration(
    session: AsyncSession,
    *,
    host_tg_id: int,
    duration_seconds: int,
) -> None:
    await session.execute(
        text("""
            UPDATE battle_room_drafts
            SET duration_seconds = :duration_seconds,
                updated_at = NOW()
            WHERE host_tg_id = :host_tg_id
        """),
        {
            "host_tg_id": host_tg_id,
            "duration_seconds": duration_seconds,
        },
    )


# ------------------------------------------------------------
# Update draft max players
# ------------------------------------------------------------
async def set_battle_draft_max_players(
    session: AsyncSession,
    *,
    host_tg_id: int,
    max_players: int,
) -> None:
    await session.execute(
        text("""
            UPDATE battle_room_drafts
            SET max_players = :max_players,
                updated_at = NOW()
            WHERE host_tg_id = :host_tg_id
        """),
        {
            "host_tg_id": host_tg_id,
            "max_players": max_players,
        },
    )


# ------------------------------------------------------------
# Get draft
# ------------------------------------------------------------
async def get_battle_draft(session: AsyncSession, host_tg_id: int) -> Optional[dict]:
    res = await session.execute(
        text("""
            SELECT id, host_tg_id, category, question_count,
                   duration_seconds, max_players, created_at, updated_at
            FROM battle_room_drafts
            WHERE host_tg_id = :host_tg_id
            LIMIT 1
        """),
        {"host_tg_id": host_tg_id},
    )
    row = res.mappings().first()
    return dict(row) if row else None


# ------------------------------------------------------------
# Delete draft
# ------------------------------------------------------------
async def delete_battle_draft(session: AsyncSession, host_tg_id: int) -> None:
    await session.execute(
        text("""
            DELETE FROM battle_room_drafts
            WHERE host_tg_id = :host_tg_id
        """),
        {"host_tg_id": host_tg_id},
    )

