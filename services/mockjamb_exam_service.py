# ======================================================
# services/mockjamb_exam_service.py
# ======================================================
import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from jamb_loader import prepare_subject_question_batch, prepare_use_of_english_batch
from services.mockjamb_payments import get_mockjamb_payment
from services.mockjamb_room_service import (
    get_mockjamb_room_by_code,
    list_mockjamb_room_players,
)
from services.mockjamb_session_service import (
    get_seen_mockjamb_question_ids,
    record_seen_mockjamb_questions,
    start_mockjamb_session_if_needed,
    set_mockjamb_current_subject,
    get_mockjamb_session_by_payment_reference,
    get_or_create_mockjamb_session_from_payment,
    mark_mockjamb_subject_completed,
)


logger = logging.getLogger("mockjamb_exam_service")
logger.setLevel(logging.INFO)


def get_mockjamb_subject_question_count(subject_code: str) -> int:
    subject_code = str(subject_code or "").strip().lower()
    if subject_code == "eng":
        return 60
    return 40


def _extract_correct_option(question: dict[str, Any]) -> str | None:
    for key in ("correct_option", "correct_answer", "answer", "correctAnswer"):
        value = question.get(key)
        if value:
            return str(value).strip()
    return None


async def get_mockjamb_topic_rotation_start(
    session: AsyncSession,
    *,
    user_id: int,
    subject_code: str,
) -> int:
    result = await session.execute(
        text("""
            select next_topic_index
            from public.mockjamb_topic_rotation
            where user_id = :user_id
              and subject_code = :subject_code
            limit 1
        """),
        {
            "user_id": int(user_id),
            "subject_code": str(subject_code).strip().lower(),
        },
    )
    row = result.mappings().first()
    if not row:
        return 0
    return int(row.get("next_topic_index") or 0)


async def save_mockjamb_topic_rotation_start(
    session: AsyncSession,
    *,
    user_id: int,
    subject_code: str,
    next_topic_index: int,
) -> None:
    await session.execute(
        text("""
            insert into public.mockjamb_topic_rotation (
                user_id,
                subject_code,
                next_topic_index,
                created_at,
                updated_at
            )
            values (
                :user_id,
                :subject_code,
                :next_topic_index,
                now(),
                now()
            )
            on conflict (user_id, subject_code)
            do update set
                next_topic_index = excluded.next_topic_index,
                updated_at = now()
        """),
        {
            "user_id": int(user_id),
            "subject_code": str(subject_code).strip().lower(),
            "next_topic_index": int(next_topic_index or 0),
        },
    )

async def get_mockjamb_subject_paper(
    session: AsyncSession,
    *,
    payment_reference: str,
    subject_code: str,
) -> list[dict]:
    result = await session.execute(
        text("""
            select
                id,
                session_id,
                payment_reference,
                user_id,
                subject_code,
                question_id,
                question_order,
                question_json,
                correct_option,
                selected_option,
                is_correct,
                created_at,
                updated_at
            from public.mockjamb_subject_questions
            where payment_reference = :payment_reference
              and subject_code = :subject_code
            order by question_order asc
        """),
        {
            "payment_reference": payment_reference,
            "subject_code": subject_code,
        },
    )
    rows = result.mappings().all()
    return [dict(row) for row in rows]


async def get_mockjamb_subject_question_by_order(
    session: AsyncSession,
    *,
    payment_reference: str,
    subject_code: str,
    question_order: int,
) -> dict | None:
    result = await session.execute(
        text("""
            select
                id,
                session_id,
                payment_reference,
                user_id,
                subject_code,
                question_id,
                question_order,
                question_json,
                correct_option,
                selected_option,
                is_correct,
                created_at,
                updated_at
            from public.mockjamb_subject_questions
            where payment_reference = :payment_reference
              and subject_code = :subject_code
              and question_order = :question_order
            limit 1
        """),
        {
            "payment_reference": payment_reference,
            "subject_code": subject_code,
            "question_order": int(question_order),
        },
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def get_mockjamb_room_subject_paper(
    session: AsyncSession,
    *,
    room_code: str,
    subject_code: str,
) -> list[dict]:
    room_code = str(room_code or "").strip().upper()
    subject_code = str(subject_code or "").strip().lower()

    result = await session.execute(
        text("""
            select
                id,
                room_code,
                subject_code,
                question_id,
                question_order,
                question_json,
                correct_option,
                created_at,
                updated_at
            from public.mockjamb_room_subject_questions
            where upper(room_code) = :room_code
              and lower(subject_code) = :subject_code
            order by question_order asc, id asc
        """),
        {
            "room_code": room_code,
            "subject_code": subject_code,
        },
    )

    rows = result.mappings().all()
    return [dict(row) for row in rows]


async def create_mockjamb_room_subject_paper(
    session: AsyncSession,
    *,
    room_code: str,
    subject_code: str,
    selected_questions: list[dict],
) -> list[dict]:
    room_code = str(room_code or "").strip().upper()
    subject_code = str(subject_code or "").strip().lower()

    for idx, question in enumerate(selected_questions, start=1):
        await session.execute(
            text("""
                insert into public.mockjamb_room_subject_questions (
                    room_code,
                    subject_code,
                    question_id,
                    question_order,
                    question_json,
                    correct_option,
                    created_at,
                    updated_at
                )
                values (
                    :room_code,
                    :subject_code,
                    :question_id,
                    :question_order,
                    :question_json,
                    :correct_option,
                    now(),
                    now()
                )
                on conflict (room_code, subject_code, question_id) do nothing
            """),
            {
                "room_code": room_code,
                "subject_code": subject_code,
                "question_id": str(question.get("id")),
                "question_order": idx,
                "question_json": json.dumps(question),
                "correct_option": _extract_correct_option(question),
            },
        )

    await session.flush()

    return await get_mockjamb_room_subject_paper(
        session,
        room_code=room_code,
        subject_code=subject_code,
    )


async def clone_mockjamb_room_subject_paper_to_player(
    session: AsyncSession,
    *,
    room_code: str,
    subject_code: str,
    payment_reference: str,
    user_id: int,
    session_id: int,
) -> list[dict]:
    room_code = str(room_code or "").strip().upper()
    subject_code = str(subject_code or "").strip().lower()

    room_paper = await get_mockjamb_room_subject_paper(
        session,
        room_code=room_code,
        subject_code=subject_code,
    )

    for row in room_paper:
        await session.execute(
            text("""
                insert into public.mockjamb_subject_questions (
                    session_id,
                    payment_reference,
                    user_id,
                    subject_code,
                    question_id,
                    question_order,
                    question_json,
                    correct_option,
                    selected_option,
                    is_correct,
                    created_at,
                    updated_at
                )
                values (
                    :session_id,
                    :payment_reference,
                    :user_id,
                    :subject_code,
                    :question_id,
                    :question_order,
                    :question_json,
                    :correct_option,
                    null,
                    null,
                    now(),
                    now()
                )
                on conflict (payment_reference, subject_code, question_id) do nothing
            """),
            {
                "session_id": int(session_id),
                "payment_reference": payment_reference,
                "user_id": int(user_id),
                "subject_code": subject_code,
                "question_id": str(row.get("question_id")),
                "question_order": int(row.get("question_order") or 0),
                "question_json": row.get("question_json"),
                "correct_option": row.get("correct_option"),
            },
        )

    await session.flush()

    return await get_mockjamb_subject_paper(
        session,
        payment_reference=payment_reference,
        subject_code=subject_code,
    )


async def create_mockjamb_subject_paper_if_needed(
    session: AsyncSession,
    *,
    payment_reference: str,
    user_id: int,
    subject_code: str,
    requested_count: int | None = None,
) -> dict:
    existing_session = await get_mockjamb_session_by_payment_reference(session, payment_reference)
    if not existing_session:
        raise ValueError(f"Mock JAMB session not found for payment_reference={payment_reference}")

    existing_paper = await get_mockjamb_subject_paper(
        session,
        payment_reference=payment_reference,
        subject_code=subject_code,
    )
    if existing_paper:
        return {
            "created_now": False,
            "cycle_reset": False,
            "selected_count": len(existing_paper),
            "paper_rows": existing_paper,
            "selected_question_ids": [row["question_id"] for row in existing_paper],
        }

    payment = await get_mockjamb_payment(session, payment_reference)
    room_code = str((payment or {}).get("room_code") or "").strip().upper()

    if requested_count is None:
        requested_count = int(get_mockjamb_subject_question_count(subject_code) or 0)

    # ==========================================================
    # ROOM-LED MULTIPLAYER FLOW
    # ==========================================================
    if room_code:
        room = await get_mockjamb_room_by_code(
            session,
            room_code=room_code,
        )

        if room:
            room_paper = await get_mockjamb_room_subject_paper(
                session,
                room_code=room_code,
                subject_code=subject_code,
            )

            # If the official room paper already exists, clone it to this player
            if room_paper:
                cloned_rows = await clone_mockjamb_room_subject_paper_to_player(
                    session,
                    room_code=room_code,
                    subject_code=subject_code,
                    payment_reference=payment_reference,
                    user_id=int(user_id),
                    session_id=int(existing_session["id"]),
                )

                return {
                    "created_now": False,
                    "cycle_reset": False,
                    "selected_count": len(cloned_rows),
                    "paper_rows": cloned_rows,
                    "selected_question_ids": [row["question_id"] for row in cloned_rows],
                }

            players = await list_mockjamb_room_players(
                session,
                room_code=room_code,
            )

            relevant_players: list[dict] = []
            for player in players:
                try:
                    player_subject_codes = json.loads(player.get("subject_codes_json") or "[]")
                except Exception:
                    player_subject_codes = []

                player_payment_status = str(player.get("payment_status") or "").strip().lower()
                if player_payment_status != "successful":
                    continue

                if subject_code in player_subject_codes:
                    relevant_players.append(player)

            # If this subject is not shared by any paid room player for some reason,
            # fall back to solo logic below.
            if relevant_players:
                combined_seen_ids: list[str] = []
                combined_seen_set: set[str] = set()

                for player in relevant_players:
                    player_user_id = int(player.get("user_id") or 0)
                    player_seen_ids = await get_seen_mockjamb_question_ids(
                        session,
                        user_id=player_user_id,
                        subject_code=subject_code,
                    )
                    for qid in player_seen_ids:
                        qid_str = str(qid)
                        if qid_str not in combined_seen_set:
                            combined_seen_set.add(qid_str)
                            combined_seen_ids.append(qid_str)

                # Preserve existing solo subject structure
                if subject_code == "eng":
                    batch = prepare_use_of_english_batch(
                        seen_question_ids=combined_seen_ids,
                    )
                else:
                    rotation_owner_user_id = int(room.get("host_user_id") or user_id)

                    start_topic_index = await get_mockjamb_topic_rotation_start(
                        session,
                        user_id=rotation_owner_user_id,
                        subject_code=subject_code,
                    )

                    batch = prepare_subject_question_batch(
                        subject_code=subject_code,
                        requested_count=requested_count,
                        seen_question_ids=combined_seen_ids,
                        start_topic_index=start_topic_index,
                    )

                    await save_mockjamb_topic_rotation_start(
                        session,
                        user_id=rotation_owner_user_id,
                        subject_code=subject_code,
                        next_topic_index=int(batch.get("next_topic_index") or 0),
                    )

                if subject_code == "eng" and int(batch.get("selected_count") or 0) != 60:
                    raise ValueError(
                        f"Use of English paper must contain exactly 60 questions, got {batch.get('selected_count')}"
                    )

                selected_questions = batch["selected_questions"]
                selected_question_ids = batch["selected_question_ids"]

                # Create the one official room paper
                await create_mockjamb_room_subject_paper(
                    session,
                    room_code=room_code,
                    subject_code=subject_code,
                    selected_questions=selected_questions,
                )

                # Record seen questions for every relevant room player sharing this subject
                for player in relevant_players:
                    target_user_id = int(player.get("user_id") or 0)
                    await record_seen_mockjamb_questions(
                        session,
                        user_id=target_user_id,
                        subject_code=subject_code,
                        question_ids=selected_question_ids,
                    )

                # Clone official room paper into this player's personal paper rows
                paper_rows = await clone_mockjamb_room_subject_paper_to_player(
                    session,
                    room_code=room_code,
                    subject_code=subject_code,
                    payment_reference=payment_reference,
                    user_id=int(user_id),
                    session_id=int(existing_session["id"]),
                )

                return {
                    "created_now": True,
                    "cycle_reset": bool(batch.get("cycle_reset")),
                    "selected_count": len(paper_rows),
                    "paper_rows": paper_rows,
                    "selected_question_ids": selected_question_ids,
                    "start_topic_index_used": batch.get("start_topic_index_used"),
                    "next_topic_index": batch.get("next_topic_index"),
                }

    # ==========================================================
    # SOLO / NON-ROOM FALLBACK
    # ==========================================================
    seen_question_ids = await get_seen_mockjamb_question_ids(
        session,
        user_id=int(user_id),
        subject_code=subject_code,
    )

    if subject_code == "eng":
        batch = prepare_use_of_english_batch(
            seen_question_ids=seen_question_ids,
        )
    else:
        start_topic_index = await get_mockjamb_topic_rotation_start(
            session,
            user_id=int(user_id),
            subject_code=subject_code,
        )

        batch = prepare_subject_question_batch(
            subject_code=subject_code,
            requested_count=requested_count,
            seen_question_ids=seen_question_ids,
            start_topic_index=start_topic_index,
        )

        await save_mockjamb_topic_rotation_start(
            session,
            user_id=int(user_id),
            subject_code=subject_code,
            next_topic_index=int(batch.get("next_topic_index") or 0),
        )

    if subject_code == "eng" and int(batch.get("selected_count") or 0) != 60:
        raise ValueError(
            f"Use of English paper must contain exactly 60 questions, got {batch.get('selected_count')}"
        )

    selected_questions = batch["selected_questions"]
    selected_question_ids = batch["selected_question_ids"]
    session_id = int(existing_session["id"])

    for idx, question in enumerate(selected_questions, start=1):
        await session.execute(
            text("""
                insert into public.mockjamb_subject_questions (
                    session_id,
                    payment_reference,
                    user_id,
                    subject_code,
                    question_id,
                    question_order,
                    question_json,
                    correct_option,
                    selected_option,
                    is_correct,
                    created_at,
                    updated_at
                )
                values (
                    :session_id,
                    :payment_reference,
                    :user_id,
                    :subject_code,
                    :question_id,
                    :question_order,
                    :question_json,
                    :correct_option,
                    null,
                    null,
                    now(),
                    now()
                )
                on conflict (payment_reference, subject_code, question_id) do nothing
            """),
            {
                "session_id": session_id,
                "payment_reference": payment_reference,
                "user_id": int(user_id),
                "subject_code": subject_code,
                "question_id": str(question.get("id")),
                "question_order": idx,
                "question_json": json.dumps(question),
                "correct_option": _extract_correct_option(question),
            },
        )

    await record_seen_mockjamb_questions(
        session,
        user_id=int(user_id),
        subject_code=subject_code,
        question_ids=selected_question_ids,
    )

    await session.flush()

    paper_rows = await get_mockjamb_subject_paper(
        session,
        payment_reference=payment_reference,
        subject_code=subject_code,
    )

    return {
        "created_now": True,
        "cycle_reset": batch["cycle_reset"],
        "selected_count": batch["selected_count"],
        "paper_rows": paper_rows,
        "selected_question_ids": selected_question_ids,
        "start_topic_index_used": batch.get("start_topic_index_used"),
        "next_topic_index": batch.get("next_topic_index"),
    }


async def start_mockjamb_subject(
    session: AsyncSession,
    *,
    payment_reference: str,
    user_id: int,
    subject_code: str,
) -> dict:
    session_row = await start_mockjamb_session_if_needed(
        session,
        payment_reference=payment_reference,
    )
    if not session_row:
        raise ValueError(f"Mock JAMB session not found for payment_reference={payment_reference}")

    session_row = await set_mockjamb_current_subject(
        session,
        payment_reference=payment_reference,
        subject_code=subject_code,
    )

    requested_count = get_mockjamb_subject_question_count(subject_code)

    paper_info = await create_mockjamb_subject_paper_if_needed(
        session,
        payment_reference=payment_reference,
        user_id=int(user_id),
        subject_code=subject_code,
        requested_count=requested_count,
    )

    current_question = await get_mockjamb_subject_question_by_order(
        session,
        payment_reference=payment_reference,
        subject_code=subject_code,
        question_order=1,
    )

    return {
        "session": session_row,
        "paper_info": paper_info,
        "current_question": current_question,
    }


async def answer_mockjamb_question(
    session: AsyncSession,
    *,
    payment_reference: str,
    subject_code: str,
    question_order: int,
    selected_option: str,
) -> dict:
    current_question = await get_mockjamb_subject_question_by_order(
        session,
        payment_reference=payment_reference,
        subject_code=subject_code,
        question_order=question_order,
    )

    if not current_question:
        return {
            "status": "error",
            "reason": "question_not_found",
        }

    selected_option = str(selected_option).strip().upper()
    correct_option = str(current_question.get("correct_option") or "").strip().upper()
    is_correct = selected_option == correct_option if correct_option else False

    await session.execute(
        text("""
            update public.mockjamb_subject_questions
            set
                selected_option = :selected_option,
                is_correct = :is_correct,
                updated_at = now()
            where payment_reference = :payment_reference
              and subject_code = :subject_code
              and question_order = :question_order
        """),
        {
            "payment_reference": payment_reference,
            "subject_code": subject_code,
            "question_order": int(question_order),
            "selected_option": selected_option,
            "is_correct": bool(is_correct),
        },
    )

    paper_rows = await get_mockjamb_subject_paper(
        session,
        payment_reference=payment_reference,
        subject_code=subject_code,
    )
    total_questions = len(paper_rows)

    next_question_order = int(question_order) + 1

    if next_question_order > total_questions:
        return {
            "status": "completed_subject",
            "selected_option": selected_option,
            "is_correct": is_correct,
            "total_questions": total_questions,
        }

    next_question = await get_mockjamb_subject_question_by_order(
        session,
        payment_reference=payment_reference,
        subject_code=subject_code,
        question_order=next_question_order,
    )

    await session.execute(
        text("""
            update public.mockjamb_sessions
            set
                current_question_index = :current_question_index,
                updated_at = now()
            where payment_reference = :payment_reference
        """),
        {
            "payment_reference": payment_reference,
            "current_question_index": int(next_question_order - 1),
        },
    )

    return {
        "status": "next_question",
        "selected_option": selected_option,
        "is_correct": is_correct,
        "next_question": next_question,
        "next_question_order": next_question_order,
        "total_questions": total_questions,
    }


async def calculate_mockjamb_subject_score(
    session: AsyncSession,
    *,
    payment_reference: str,
    subject_code: str,
) -> dict:
    result = await session.execute(
        text("""
            select
                count(*) as total_questions,
                coalesce(sum(case when is_correct = true then 1 else 0 end), 0) as correct_count
            from public.mockjamb_subject_questions
            where payment_reference = :payment_reference
              and subject_code = :subject_code
        """),
        {
            "payment_reference": payment_reference,
            "subject_code": subject_code,
        },
    )
    row = result.mappings().first() or {}

    total_questions = int(row.get("total_questions") or 0)
    correct_count = int(row.get("correct_count") or 0)

    expected_total = get_mockjamb_subject_question_count(subject_code)

    if expected_total <= 0:
        score_100 = 0
    else:
        score_100 = round((correct_count / expected_total) * 100)

    return {
        "total_questions": total_questions,
        "correct_count": correct_count,
        "score_100": int(score_100),
    }


async def get_mockjamb_review_rows(
    session: AsyncSession,
    *,
    payment_reference: str,
    wrong_only: bool = False,
) -> list[dict]:
    if wrong_only:
        result = await session.execute(
            text("""
                select
                    id,
                    session_id,
                    payment_reference,
                    user_id,
                    subject_code,
                    question_id,
                    question_order,
                    question_json,
                    correct_option,
                    selected_option,
                    is_correct,
                    created_at,
                    updated_at
                from public.mockjamb_subject_questions
                where payment_reference = :payment_reference
                  and selected_option is not null
                  and coalesce(is_correct, false) = false
                order by subject_code asc, question_order asc
            """),
            {"payment_reference": payment_reference},
        )
    else:
        result = await session.execute(
            text("""
                select
                    id,
                    session_id,
                    payment_reference,
                    user_id,
                    subject_code,
                    question_id,
                    question_order,
                    question_json,
                    correct_option,
                    selected_option,
                    is_correct,
                    created_at,
                    updated_at
                from public.mockjamb_subject_questions
                where payment_reference = :payment_reference
                  and selected_option is not null
                order by subject_code asc, question_order asc
            """),
            {"payment_reference": payment_reference},
        )

    rows = result.mappings().all()
    return [dict(row) for row in rows]


async def get_mockjamb_subject_result_stats(
    session: AsyncSession,
    *,
    payment_reference: str,
    subject_code: str,
) -> dict:
    result = await session.execute(
        text("""
            select
                count(selected_option) as answered_count,
                coalesce(sum(case when is_correct = true then 1 else 0 end), 0) as correct_count
            from public.mockjamb_subject_questions
            where payment_reference = :payment_reference
              and subject_code = :subject_code
        """),
        {
            "payment_reference": payment_reference,
            "subject_code": subject_code,
        },
    )
    row = result.mappings().first() or {}

    return {
        "answered_count": int(row.get("answered_count") or 0),
        "correct_count": int(row.get("correct_count") or 0),
    }
