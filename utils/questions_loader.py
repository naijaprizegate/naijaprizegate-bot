# ===========================================================
# utils/questions_loader.py  (FINAL PRODUCTION VERSION)
# Sequential per-user per-category using trivia_progress table
# ===========================================================
import json
import os
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from db import get_async_session

# -----------------------------------------------------------
# FILE PATHS
# -----------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
QUESTIONS_PATH = os.path.join(BASE_DIR, "questions.json")

# -----------------------------------------------------------
# CATEGORY MAP  (Telegram button → JSON category key)
# -----------------------------------------------------------
CATEGORY_MAP = {
    "History": "nigeria_history",
    "Entertainment": "nigeria_entertainment",
    "Football": "football",
    "Geography": "geography",
    "English": "english",
    "Sciences": "sciences",
    "Mathematics": "mathematics",
}

# -----------------------------------------------------------
# GLOBAL CACHE
# -----------------------------------------------------------
_ALL_QUESTIONS: Optional[List[Dict[str, Any]]] = None
_CATEGORY_CACHE: Dict[str, List[Dict[str, Any]]] = {}

# ===========================================================
# LOAD QUESTIONS (cached)
# ===========================================================
def _load_questions() -> List[Dict[str, Any]]:
    global _ALL_QUESTIONS

    if _ALL_QUESTIONS is None:
        if not os.path.exists(QUESTIONS_PATH):
            raise RuntimeError(f"questions.json not found at {QUESTIONS_PATH}")

        with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
            _ALL_QUESTIONS = json.load(f)

    return _ALL_QUESTIONS


# ===========================================================
# GET CATEGORY QUESTIONS (sorted + cached)
# ===========================================================
def _get_category_questions_sorted(category_key: str) -> List[Dict[str, Any]]:
    """
    Returns questions sorted by numeric ID.
    Cached after first call for performance.
    """
    if category_key in _CATEGORY_CACHE:
        return _CATEGORY_CACHE[category_key]

    all_q = _load_questions()
    cat_q = [q for q in all_q if q.get("category") == category_key]

    # Sort by numeric id to guarantee deterministic order
    cat_q.sort(key=lambda x: int(x.get("id") or 0))

    _CATEGORY_CACHE[category_key] = cat_q
    return cat_q


# ===========================================================
# CORE: GET NEXT QUESTION FOR USER
# ===========================================================
async def get_next_question_for_user(tg_id: int, category: str) -> Dict[str, Any]:
    """
    Sequential question engine per-user per-category.

    Flow:
    1) Read user's next_index from trivia_progress
    2) Pick question in sorted order
    3) Increment index and wrap
    4) Upsert new progress
    """

    category_key = CATEGORY_MAP.get(category)
    if not category_key:
        raise ValueError(f"Invalid category given: {category}")

    questions = _get_category_questions_sorted(category_key)
    if not questions:
        raise ValueError(f"No questions found for category {category}")

    async with get_async_session() as session:

        # Lock row if exists to prevent double-question race conditions
        res = await session.execute(
            text("""
                SELECT next_index
                FROM trivia_progress
                WHERE tg_id = :tg_id AND category_key = :ck
                FOR UPDATE
            """),
            {"tg_id": int(tg_id), "ck": category_key},
        )

        row = res.fetchone()
        next_index = int(row[0]) if row else 0

        # Pick question + compute next pointer
        idx = next_index % len(questions)
        question = questions[idx]
        new_next = (idx + 1) % len(questions)

        # Upsert progress
        await session.execute(
            text("""
                INSERT INTO trivia_progress (tg_id, category_key, next_index, updated_at)
                VALUES (:tg_id, :ck, :ni, NOW())
                ON CONFLICT (tg_id, category_key)
                DO UPDATE SET next_index = EXCLUDED.next_index, updated_at = NOW()
            """),
            {"tg_id": int(tg_id), "ck": category_key, "ni": int(new_next)},
        )

        await session.commit()

    return question


# ===========================================================
# RESET USER PROGRESS (single category)
# ===========================================================
async def reset_user_category_progress(tg_id: int, category: str) -> None:
    """Reset a user back to the first question for a category."""
    category_key = CATEGORY_MAP.get(category)
    if not category_key:
        raise ValueError(f"Invalid category given: {category}")

    async with get_async_session() as session:
        await session.execute(
            text("""
                INSERT INTO trivia_progress (tg_id, category_key, next_index, updated_at)
                VALUES (:tg_id, :ck, 0, NOW())
                ON CONFLICT (tg_id, category_key)
                DO UPDATE SET next_index = 0, updated_at = NOW()
            """),
            {"tg_id": int(tg_id), "ck": category_key},
        )
        await session.commit()


# ===========================================================
# RESET USER PROGRESS (ALL CATEGORIES)
# ===========================================================
async def reset_user_all_categories(tg_id: int) -> None:
    """Reset a user to the first question in ALL categories."""
    async with get_async_session() as session:
        await session.execute(
            text("DELETE FROM trivia_progress WHERE tg_id = :tg_id"),
            {"tg_id": int(tg_id)},
        )
        await session.commit()


# ===========================================================
# OPTIONAL: PEEK NEXT QUESTION (does not advance progress)
# ===========================================================
async def peek_next_question_for_user(tg_id: int, category: str) -> Dict[str, Any]:
    """Returns next question without incrementing progress."""
    category_key = CATEGORY_MAP.get(category)
    if not category_key:
        raise ValueError(f"Invalid category given: {category}")

    questions = _get_category_questions_sorted(category_key)
    if not questions:
        raise ValueError(f"No questions found for category {category}")

    async with get_async_session() as session:
        res = await session.execute(
            text("""
                SELECT next_index
                FROM trivia_progress
                WHERE tg_id = :tg_id AND category_key = :ck
                LIMIT 1
            """),
            {"tg_id": int(tg_id), "ck": category_key},
        )
        row = res.fetchone()
        next_index = int(row[0]) if row else 0

    idx = next_index % len(questions)
    return questions[idx]
