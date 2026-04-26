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

from jamb_loader import get_subject_by_code

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
    first_name: str | None = None,
    last_name: str | None = None,
    username: str | None = None,
) -> dict:
    room_code = str(room_code or "").strip().upper()

    clean_first_name = str(first_name or "").strip() or None
    clean_last_name = str(last_name or "").strip() or None
    clean_username = str(username or "").strip() or None

    existing = await get_mockjamb_room_player(
        session,
        room_code=room_code,
        user_id=user_id,
    )
    if existing:
        await session.execute(
            text("""
                update public.mockjamb_room_players
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

        return await get_mockjamb_room_player(
            session,
            room_code=room_code,
            user_id=user_id,
        )

    payment_status = "successful" if has_paid else "pending"

    await session.execute(
        text("""
            insert into public.mockjamb_room_players (
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


def build_mockjamb_waiting_room_text(
    *,
    room_code: str,
    invite_link: str,
    room_status: str,
    players: list[dict],
    host_user_id: int,
    expected_players: int | None = None,
) -> str:
    safe_room_code = str(room_code or "").strip().upper()
    safe_invite_link = str(invite_link or "").strip()
    normalized_status = str(room_status or "waiting").strip().lower()

    status_map = {
        "waiting": "⏳ Waiting for players",
        "ready": "✅ All players ready",
        "locked": "🔒 Locked",
        "in_progress": "📝 Match in progress",
        "completed": "🏁 Completed",
    }
    pretty_status = status_map.get(
        normalized_status,
        normalized_status.replace("_", " ").title(),
    )

    total_players = len(players)
    required_players = int(expected_players or 0) if expected_players else 0
    invited_friends_count = max(0, required_players - 1) if required_players else 0

    lines = []
    lines.append("👥 *Mock JAMB Multiplayer Room*")
    lines.append("")
    lines.append(f"*Room Code:* `{safe_room_code}`")
    lines.append(f"*Status:* {pretty_status}")

    if required_players > 0:
        lines.append(f"*Players Joined:* {total_players} of {required_players}")
        lines.append(
            f"*Total Players Required:* {required_players} \\(1 Host + {invited_friends_count} Friend{'s' if invited_friends_count != 1 else ''}\\)"
        )
    else:
        lines.append(f"*Players Joined:* {total_players}")

    lines.append("")

    if safe_invite_link:
        lines.append("*Invite Link:*")
        lines.append(safe_invite_link)
        lines.append("")

    lines.append("*Players in Room:*")

    if not players:
        lines.append("• No players have joined yet.")
    else:
        for idx, player in enumerate(players, start=1):
            user_id = int(player.get("user_id") or 0)
            is_host = int(user_id) == int(host_user_id)

            first_name = str(player.get("first_name") or "").strip()
            last_name = str(player.get("last_name") or "").strip()
            username = str(player.get("username") or "").strip()

            full_name = " ".join(part for part in [first_name, last_name] if part).strip()

            if full_name and username:
                display_name = f"{full_name} \\(@{username}\\)"
            elif full_name:
                display_name = full_name
            elif username:
                display_name = f"@{username}"
            else:
                display_name = f"User {user_id}"

            course_code = str(player.get("course_code") or "").strip().lower()
            course = get_course_by_code(course_code) if course_code else None
            course_text = str((course or {}).get("course_name") or "").strip() or "Not Set"

            subject_names = format_mockjamb_player_subjects(
                player.get("subject_codes_json") or "[]"
            )
            subject_text = ", ".join(subject_names) if subject_names else "Not Set"

            payment_status = str(player.get("payment_status") or "").strip().lower()
            is_paid = payment_status == "successful"
            is_ready = bool(player.get("is_ready"))

            role_label = "👑 Host" if is_host else "👤 Player"
            payment_label = "💳 Paid" if is_paid else "💰 Not Paid"
            ready_label = "✅ Ready" if is_ready else "⏳ Waiting"

            lines.append(f"{idx}\\. {role_label}: {display_name}")
            lines.append(f"   • Course: {course_text}")
            lines.append(f"   • Subjects: {subject_text}")
            lines.append(f"   • Payment: {payment_label}")
            lines.append(f"   • Readiness: {ready_label}")
            lines.append("")

    if normalized_status in ("waiting", "ready"):
        lines.append("Share the room code or invite link with your friends.")
    elif normalized_status == "in_progress":
        lines.append("The match has started. Players can now continue into the exam.")
    elif normalized_status == "locked":
        lines.append("This room is currently locked.")
    else:
        lines.append("Room status updated.")

    return "\n".join(lines)

