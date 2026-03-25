# ====================================================================
# services/question_history_service.py
# ====================================================================
from __future__ import annotations

import hashlib
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def make_json_question_key(category: str, question_text: str) -> str:
    """
    Stable fallback key for JSON questions if they do not have an explicit id.
    Better: if your JSON questions already have 'id', use that instead.
    """
    raw = f"{category.strip().lower()}::{question_text.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def record_question_history(
    session: AsyncSession,
    *,
    tg_id: int,
    source_type: str,
    category: str,
    question_key: str,
) -> None:
    await session.execute(
        text("""
            INSERT INTO user_question_history (
                tg_id,
                source_type,
                category,
                question_key
            )
            VALUES (
                :tg_id,
                :source_type,
                :category,
                :question_key
            )
            ON CONFLICT (tg_id, source_type, category, question_key)
            DO NOTHING
        """),
        {
            "tg_id": int(tg_id),
            "source_type": source_type,
            "category": category,
            "question_key": str(question_key),
        },
    )


async def get_seen_question_keys(
    session: AsyncSession,
    *,
    tg_id: int,
    source_type: str,
    category: str,
) -> set[str]:
    res = await session.execute(
        text("""
            SELECT question_key
            FROM user_question_history
            WHERE tg_id = :tg_id
              AND source_type = :source_type
              AND category = :category
        """),
        {
            "tg_id": int(tg_id),
            "source_type": source_type,
            "category": category,
        },
    )
    return {str(row[0]) for row in res.fetchall()}


async def get_seen_question_keys_for_users(
    session: AsyncSession,
    *,
    tg_ids: Iterable[int],
    source_type: str,
    category: str,
) -> set[str]:
    tg_ids = [int(x) for x in tg_ids]
    if not tg_ids:
        return set()

    res = await session.execute(
        text("""
            SELECT DISTINCT question_key
            FROM user_question_history
            WHERE tg_id = ANY(:tg_ids)
              AND source_type = :source_type
              AND category = :category
        """),
        {
            "tg_ids": tg_ids,
            "source_type": source_type,
            "category": category,
        },
    )
    return {str(row[0]) for row in res.fetchall()}


