# ======================================================
# services/mockjamb_room_service.py
# ======================================================
import json
import random
import string
import logging
from datetime import datetime

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
    room_code = str(room_code or "").strip().upper()
    if not room_code:
        return None

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
                expected_players,
                all_players_ready,
                started_by_host,
                host_waiting_message_id,
                created_at,
                updated_at
            from public.mockjamb_rooms
            where upper(room_code) = :room_code
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
    required_player_count: int = 2,
) -> dict:
    expected_players = max(2, int(required_player_count or 2))

    for _ in range(10):
        room_code = build_mockjamb_room_code()

        existing = await get_mockjamb_room_by_code(
            session,
            room_code=room_code,
        )
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
                    expected_players,
                    invite_token,
                    created_at,
                    updated_at
                )
                values (
                    :room_code,
                    :host_user_id,
                    'waiting',
                    :duration_minutes,
                    :expected_players,
                    :invite_token,
                    now(),
                    now()
                )
            """),
            {
                "room_code": room_code,
                "host_user_id": int(host_user_id),
                "duration_minutes": int(duration_minutes),
                "expected_players": expected_players,
                "invite_token": invite_token,
            },
        )

        await session.flush()

        room = await get_mockjamb_room_by_code(
            session,
            room_code=room_code,
        )
        if room:
            return room

    raise ValueError("Could not generate a unique Mock JAMB room code.")


async def set_mockjamb_room_expected_players(
    session,
    *,
    room_code: str,
    expected_players: int,
) -> dict | None:
    expected_players = max(2, int(expected_players))

    result = await session.execute(
        text("""
            update public.mockjamb_rooms
            set
                expected_players = :expected_players,
                updated_at = now()
            where upper(room_code) = upper(:room_code)
            returning *
        """),
        {
            "room_code": room_code,
            "expected_players": expected_players,
        },
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def count_mockjamb_room_ready_players(
    session,
    *,
    room_code: str,
) -> int:
    result = await session.execute(
        text("""
            select count(*) as total
            from public.mockjamb_room_players
            where upper(room_code) = upper(:room_code)
              and coalesce(payment_status, 'pending') = 'paid'
              and coalesce(is_ready, false) = true
        """),
        {
            "room_code": room_code,
        },
    )
    row = result.mappings().first()
    return int((row or {}).get("total") or 0)

async def get_mockjamb_room_player(
    session: AsyncSession,
    *,
    room_code: str,
    user_id: int,
) -> dict | None:
    room_code = str(room_code or "").strip().upper()

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
                is_host,
                payment_status,
                paid_at,
                is_ready,
                ready_at,
                joined_at,
                updated_at
            from public.mockjamb_room_players
            where upper(room_code) = :room_code
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

async def set_mockjamb_room_all_players_ready(
    session,
    *,
    room_code: str,
    all_players_ready: bool,
) -> dict | None:
    result = await session.execute(
        text("""
            update public.mockjamb_rooms
            set
                all_players_ready = :all_players_ready,
                updated_at = now()
            where upper(room_code) = upper(:room_code)
            returning *
        """),
        {
            "room_code": room_code,
            "all_players_ready": bool(all_players_ready),
        },
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def count_mockjamb_room_ready_players(
    session,
    *,
    room_code: str,
) -> int:
    result = await session.execute(
        text("""
            select count(*) as total
            from public.mockjamb_room_players
            where upper(room_code) = upper(:room_code)
              and coalesce(has_paid, false) = true
              and coalesce(is_ready, false) = true
        """),
        {
            "room_code": room_code,
        },
    )
    row = result.mappings().first()
    return int((row or {}).get("total") or 0)


async def count_mockjamb_room_paid_players(
    session,
    *,
    room_code: str,
) -> int:
    result = await session.execute(
        text("""
            select count(*) as total
            from public.mockjamb_room_players
            where upper(room_code) = upper(:room_code)
              and coalesce(has_paid, false) = true
        """),
        {
            "room_code": room_code,
        },
    )
    row = result.mappings().first()
    return int((row or {}).get("total") or 0)


async def add_mockjamb_room_player(
    session: AsyncSession,
    *,
    room_code: str,
    user_id: int,
    course_code: str | None = None,
    subject_codes_json: str = "[]",
    is_host: bool = False,
    has_paid: bool = False,
) -> dict:
    room_code = str(room_code or "").strip().upper()

    existing = await get_mockjamb_room_player(
        session,
        room_code=room_code,
        user_id=user_id,
    )
    if existing:
        return existing

    payment_status = "successful" if has_paid else "pending"

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
                is_host,
                payment_status,
                paid_at,
                is_ready,
                ready_at,
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
                :is_host,
                :payment_status,
                :paid_at,
                false,
                null,
                now(),
                now()
            )
        """),
        {
            "room_code": room_code,
            "user_id": int(user_id),
            "course_code": course_code,
            "subject_codes_json": subject_codes_json,
            "is_host": bool(is_host),
            "payment_status": payment_status,
            "paid_at": datetime.utcnow() if has_paid else None,
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
    room_code = str(room_code or "").strip().upper()

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
                updated_at,
                is_host,
                payment_status,
                is_ready,
                paid_at,
                ready_at,
                case
                    when lower(coalesce(payment_status, '')) = 'successful' then true
                    else false
                end as has_paid
            from public.mockjamb_room_players
            where upper(room_code) = :room_code
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
    is_host: bool | None = None,
    has_paid: bool | None = None,
) -> dict | None:
    room_code = str(room_code or "").strip().upper()

    payment_status = None
    if has_paid is not None:
        payment_status = "successful" if has_paid else "pending"

    await session.execute(
        text("""
            update public.mockjamb_room_players
            set
                course_code = :course_code,
                subject_codes_json = :subject_codes_json,
                is_host = case
                    when :is_host_is_null then is_host
                    else :is_host
                end,
                payment_status = case
                    when :has_paid_is_null then payment_status
                    else :payment_status
                end,
                paid_at = case
                    when :has_paid_is_null then paid_at
                    when :has_paid then coalesce(paid_at, now())
                    else null
                end,
                updated_at = now()
            where upper(room_code) = :room_code
              and user_id = :user_id
        """),
        {
            "room_code": room_code,
            "user_id": int(user_id),
            "course_code": course_code,
            "subject_codes_json": subject_codes_json,
            "is_host": is_host,
            "is_host_is_null": is_host is None,
            "has_paid": has_paid,
            "has_paid_is_null": has_paid is None,
            "payment_status": payment_status,
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
    lines.append("👥 Mock JAMB Multiplayer Room")
    lines.append("")
    lines.append(f"Room Code: {room_code}")
    lines.append(f"Invite Link: {invite_link}")
    lines.append(f"Status: {room_status}")
    lines.append("")
    lines.append("Players:")

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
                f"{idx}. {user_id}{host_tag}\n"
                f"   Course: {course_code}\n"
                f"   Subjects: {subject_text}\n"
                f"   Status: {player_status}"
            )

    lines.append("")
    lines.append("Share the room code or invite link with your friends.")

    return "\n".join(lines)


