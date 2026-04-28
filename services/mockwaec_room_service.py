# ==========================================================
# services/mockwaec_room_service.py
# ==========================================================
import json
import random
import string
import logging
from datetime import datetime
from html import escape

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from waec_loader import get_subject_by_code

logger = logging.getLogger("mockwaec_room_service")
logger.setLevel(logging.INFO)


# ======================================================
# Helpers
# ======================================================
def build_mockwaec_room_code(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def build_mockwaec_invite_token(room_code: str) -> str:
    room_code = str(room_code or "").strip().upper()
    suffix = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(12))
    return f"mw_{room_code}_{suffix}"


def build_mockwaec_invite_link(bot_username: str, room_code: str) -> str:
    safe_bot_username = str(bot_username or "").strip()
    safe_room_code = str(room_code or "").strip().upper()

    if not safe_bot_username or not safe_room_code:
        return ""

    return f"https://t.me/{safe_bot_username}?start=wcroom_{safe_room_code}"


def format_mockwaec_player_subjects(subject_codes_raw) -> list[str]:
    subject_codes: list[str] = []

    if isinstance(subject_codes_raw, list):
        subject_codes = [str(x).strip().lower() for x in subject_codes_raw if str(x).strip()]
    elif isinstance(subject_codes_raw, str):
        try:
            parsed = json.loads(subject_codes_raw)
            if isinstance(parsed, list):
                subject_codes = [str(x).strip().lower() for x in parsed if str(x).strip()]
        except Exception:
            return []
    else:
        return []

    formatted_subjects: list[str] = []

    for code in subject_codes:
        subject = get_subject_by_code(code)
        if subject and str(subject.get("name") or "").strip():
            formatted_subjects.append(str(subject["name"]).strip())
        else:
            fallback = code.replace("_", " ").strip()
            if fallback:
                formatted_subjects.append(fallback.title())

    return formatted_subjects


# ======================================================
# Room CRUD
# ======================================================
async def get_mockwaec_room_by_code(
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
            from public.mockwaec_rooms
            where upper(room_code) = :room_code
            limit 1
        """),
        {"room_code": room_code},
    )

    row = result.mappings().first()
    return dict(row) if row else None


async def create_mockwaec_room(
    session: AsyncSession,
    *,
    host_user_id: int,
    duration_minutes: int = 180,
    required_player_count: int = 2,
) -> dict:
    required_player_count = max(2, int(required_player_count or 2))

    for _ in range(10):
        room_code = build_mockwaec_room_code()
        existing = await get_mockwaec_room_by_code(session, room_code=room_code)
        if existing:
            continue

        invite_token = build_mockwaec_invite_token(room_code)

        await session.execute(
            text("""
                insert into public.mockwaec_rooms (
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
                "expected_players": required_player_count,
                "invite_token": invite_token,
            },
        )
        await session.flush()

        return await get_mockwaec_room_by_code(session, room_code=room_code)

    raise ValueError("Could not generate a unique Mock WAEC room code.")


# ======================================================
# Room Players
# ======================================================
async def get_mockwaec_room_player(
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
                first_name,
                last_name,
                username,
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
            from public.mockwaec_room_players
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


async def add_mockwaec_room_player(
    session: AsyncSession,
    *,
    room_code: str,
    user_id: int,
    course_code: str | None = None,
    subject_codes_json: str = "[]",
    is_host: bool = False,
    has_paid: bool = False,
    first_name: str | None = None,
    last_name: str | None = None,
    username: str | None = None,
) -> dict:
    room_code = str(room_code or "").strip().upper()

    clean_first_name = str(first_name or "").strip() or None
    clean_last_name = str(last_name or "").strip() or None
    clean_username = str(username or "").strip() or None

    existing = await get_mockwaec_room_player(
        session,
        room_code=room_code,
        user_id=user_id,
    )
    if existing:
        await session.execute(
            text("""
                update public.mockwaec_room_players
                set
                    first_name = coalesce(:first_name, first_name),
                    last_name = coalesce(:last_name, last_name),
                    username = coalesce(:username, username),
                    updated_at = now()
                where upper(room_code) = :room_code
                  and user_id = :user_id
            """),
            {
                "room_code": room_code,
                "user_id": int(user_id),
                "first_name": clean_first_name,
                "last_name": clean_last_name,
                "username": clean_username,
            },
        )
        await session.flush()

        return await get_mockwaec_room_player(
            session,
            room_code=room_code,
            user_id=user_id,
        )

    payment_status = "successful" if has_paid else "pending"

    await session.execute(
        text("""
            insert into public.mockwaec_room_players (
                room_code,
                user_id,
                first_name,
                last_name,
                username,
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
                :first_name,
                :last_name,
                :username,
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
            "first_name": clean_first_name,
            "last_name": clean_last_name,
            "username": clean_username,
            "course_code": course_code,
            "subject_codes_json": subject_codes_json,
            "is_host": bool(is_host),
            "payment_status": payment_status,
            "paid_at": datetime.utcnow() if has_paid else None,
        },
    )

    await session.flush()

    return await get_mockwaec_room_player(
        session,
        room_code=room_code,
        user_id=user_id,
    )


async def list_mockwaec_room_players(
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
                first_name,
                last_name,
                username,
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
            from public.mockwaec_room_players
            where upper(room_code) = :room_code
            order by joined_at asc, id asc
        """),
        {"room_code": room_code},
    )

    rows = result.mappings().all()
    return [dict(row) for row in rows]
