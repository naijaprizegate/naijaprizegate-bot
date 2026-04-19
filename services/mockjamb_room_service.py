# ======================================================
# services/mockjamb_room_service.py
# ======================================================
import json
import random
import string
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("mockjamb_room_service")
logger.setLevel(logging.INFO)


def build_mockjamb_room_code(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choices(alphabet, k=length))


def build_mockjamb_invite_token(room_code: str) -> str:
    return f"jmroom_{room_code}"


async def get_mockjamb_room_by_code(
    session: AsyncSession,
    *,
    room_code: str,
) -> dict | None:
    result = await session.execute(
        text("""
            select
                id,
                room_code,
                host_user_id,
                status,
                scheduled_start_at,
                started_at,
                ends_at,
                duration_minutes,
                invite_token,
                created_at,
                updated_at
            from public.mockjamb_rooms
            where room_code = :room_code
            limit 1
        """),
        {"room_code": room_code},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def create_mockjamb_room(
    session: AsyncSession,
    *,
    host_user_id: int,
    duration_minutes: int = 120,
) -> dict:
    for _ in range(10):
        room_code = build_mockjamb_room_code()
        existing = await get_mockjamb_room_by_code(session, room_code=room_code)
        if existing:
            continue

        invite_token = build_mockjamb_invite_token(room_code)

        await session.execute(
            text("""
                insert into public.mockjamb_rooms (
                    room_code,
                    host_user_id,
                    status,
                    duration_minutes,
                    invite_token,
                    created_at,
                    updated_at
                )
                values (
                    :room_code,
                    :host_user_id,
                    'waiting',
                    :duration_minutes,
                    :invite_token,
                    now(),
                    now()
                )
            """),
            {
                "room_code": room_code,
                "host_user_id": int(host_user_id),
                "duration_minutes": int(duration_minutes),
                "invite_token": invite_token,
            },
        )
        await session.flush()
        return await get_mockjamb_room_by_code(session, room_code=room_code)

    raise ValueError("Could not generate a unique Mock JAMB room code.")


async def get_mockjamb_room_player(
    session: AsyncSession,
    *,
    room_code: str,
    user_id: int,
) -> dict | None:
    result = await session.execute(
        text("""
            select
                id,
                room_code,
                user_id,
                course_code,
                subject_codes_json,
                player_status,
                score_json,
                current_subject_code,
                current_question_index,
                joined_at,
                updated_at
            from public.mockjamb_room_players
            where room_code = :room_code
              and user_id = :user_id
            limit 1
        """),
        {
            "room_code": room_code,
            "user_id": int(user_id),
        },
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def add_mockjamb_room_player(
    session: AsyncSession,
    *,
    room_code: str,
    user_id: int,
    course_code: str | None = None,
    subject_codes_json: str = "[]",
) -> dict:
    existing = await get_mockjamb_room_player(
        session,
        room_code=room_code,
        user_id=user_id,
    )
    if existing:
        return existing

    await session.execute(
        text("""
            insert into public.mockjamb_room_players (
                room_code,
                user_id,
                course_code,
                subject_codes_json,
                player_status,
                score_json,
                current_subject_code,
                current_question_index,
                joined_at,
                updated_at
            )
            values (
                :room_code,
                :user_id,
                :course_code,
                :subject_codes_json,
                'joined',
                '{}',
                null,
                0,
                now(),
                now()
            )
        """),
        {
            "room_code": room_code,
            "user_id": int(user_id),
            "course_code": course_code,
            "subject_codes_json": subject_codes_json,
        },
    )
    await session.flush()
    return await get_mockjamb_room_player(
        session,
        room_code=room_code,
        user_id=user_id,
    )


async def list_mockjamb_room_players(
    session: AsyncSession,
    *,
    room_code: str,
) -> list[dict]:
    result = await session.execute(
        text("""
            select
                id,
                room_code,
                user_id,
                course_code,
                subject_codes_json,
                player_status,
                score_json,
                current_subject_code,
                current_question_index,
                joined_at,
                updated_at
            from public.mockjamb_room_players
            where room_code = :room_code
            order by joined_at asc, id asc
        """),
        {"room_code": room_code},
    )
    rows = result.mappings().all()
    return [dict(row) for row in rows]


async def update_mockjamb_room_player_setup(
    session: AsyncSession,
    *,
    room_code: str,
    user_id: int,
    course_code: str,
    subject_codes_json: str,
) -> dict | None:
    await session.execute(
        text("""
            update public.mockjamb_room_players
            set
                course_code = :course_code,
                subject_codes_json = :subject_codes_json,
                updated_at = now()
            where room_code = :room_code
              and user_id = :user_id
        """),
        {
            "room_code": room_code,
            "user_id": int(user_id),
            "course_code": course_code,
            "subject_codes_json": subject_codes_json,
        },
    )
    await session.flush()
    return await get_mockjamb_room_player(
        session,
        room_code=room_code,
        user_id=user_id,
    )


async def set_mockjamb_room_player_status(
    session: AsyncSession,
    *,
    room_code: str,
    user_id: int,
    player_status: str,
) -> dict | None:
    await session.execute(
        text("""
            update public.mockjamb_room_players
            set
                player_status = :player_status,
                updated_at = now()
            where room_code = :room_code
              and user_id = :user_id
        """),
        {
            "room_code": room_code,
            "user_id": int(user_id),
            "player_status": player_status,
        },
    )
    await session.flush()
    return await get_mockjamb_room_player(
        session,
        room_code=room_code,
        user_id=user_id,
    )


async def update_mockjamb_room_status(
    session: AsyncSession,
    *,
    room_code: str,
    status: str,
) -> dict | None:
    await session.execute(
        text("""
            update public.mockjamb_rooms
            set
                status = :status,
                updated_at = now()
            where room_code = :room_code
        """),
        {
            "room_code": room_code,
            "status": status,
        },
    )
    await session.flush()
    return await get_mockjamb_room_by_code(session, room_code=room_code)


async def save_mockjamb_room_subject_paper(
    session: AsyncSession,
    *,
    room_code: str,
    subject_code: str,
    question_ids_json: str,
) -> None:
    await session.execute(
        text("""
            insert into public.mockjamb_room_subject_papers (
                room_code,
                subject_code,
                question_ids_json,
                created_at,
                updated_at
            )
            values (
                :room_code,
                :subject_code,
                :question_ids_json,
                now(),
                now()
            )
            on conflict (room_code, subject_code)
            do update set
                question_ids_json = excluded.question_ids_json,
                updated_at = now()
        """),
        {
            "room_code": room_code,
            "subject_code": subject_code,
            "question_ids_json": question_ids_json,
        },
    )
    await session.flush()


async def get_mockjamb_room_subject_paper(
    session: AsyncSession,
    *,
    room_code: str,
    subject_code: str,
) -> dict | None:
    result = await session.execute(
        text("""
            select
                id,
                room_code,
                subject_code,
                question_ids_json,
                created_at,
                updated_at
            from public.mockjamb_room_subject_papers
            where room_code = :room_code
              and subject_code = :subject_code
            limit 1
        """),
        {
            "room_code": room_code,
            "subject_code": subject_code,
        },
    )
    row = result.mappings().first()
    return dict(row) if row else None

async def get_mockjamb_room_by_invite_token(
    session: AsyncSession,
    *,
    invite_token: str,
) -> dict | None:
    result = await session.execute(
        text("""
            select
                id,
                room_code,
                host_user_id,
                status,
                scheduled_start_at,
                started_at,
                ends_at,
                duration_minutes,
                invite_token,
                created_at,
                updated_at
            from public.mockjamb_rooms
            where invite_token = :invite_token
            limit 1
        """),
        {"invite_token": invite_token},
    )
    row = result.mappings().first()
    return dict(row) if row else None


def build_mockjamb_invite_link(bot_username: str, room_code: str) -> str:
    clean_username = str(bot_username or "").replace("@", "").strip()
    return f"https://t.me/{clean_username}?start=jmroom_{room_code}"


def format_mockjamb_player_subjects(subject_codes_raw) -> list[str]:
    if isinstance(subject_codes_raw, list):
        return [str(x).strip() for x in subject_codes_raw if str(x).strip()]

    if isinstance(subject_codes_raw, str):
        try:
            parsed = json.loads(subject_codes_raw)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except Exception:
            return []

    return []


def build_mockjamb_waiting_room_text(
    *,
    room_code: str,
    invite_link: str,
    room_status: str,
    players: list[dict],
    host_user_id: int,
) -> str:
    lines = []
    lines.append("👥 *Mock JAMB Multiplayer Room*")
    lines.append("")
    lines.append(f"*Room Code:* `{room_code}`")
    lines.append(f"*Invite Link:* {invite_link}")
    lines.append(f"*Status:* {room_status}")
    lines.append("")
    lines.append("*Players:*")

    if not players:
        lines.append("• No players yet")
    else:
        for idx, player in enumerate(players, start=1):
            user_id = int(player.get("user_id") or 0)
            course_code = str(player.get("course_code") or "Not set").strip()
            player_status = str(player.get("player_status") or "joined").strip()

            subject_codes = format_mockjamb_player_subjects(
                player.get("subject_codes_json") or "[]"
            )
            subject_text = ", ".join(subject_codes) if subject_codes else "No subjects yet"

            host_tag = " 👑 Host" if user_id == int(host_user_id) else ""

            lines.append(
                f"{idx}. `{user_id}`{host_tag}\n"
                f"   Course: {course_code}\n"
                f"   Subjects: {subject_text}\n"
                f"   Status: {player_status}"
            )

    lines.append("")
    lines.append("_Share the room code or invite link with your friends._")

    return "\n".join(lines)

