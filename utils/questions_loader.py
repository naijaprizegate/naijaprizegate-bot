# ===========================================================
# utils/questions_loader.py
# Shared-history version for Paid Trivia (questions.json)
# No repeat per user per category until JSON bank is exhausted
# ===========================================================
import json
import os
from typing import Any, Dict, List, Optional

from db import get_async_session
from services.question_history_service import (
    get_seen_question_keys,
    make_json_question_key,
)

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
# NORMALIZE ONE QUESTION
# Ensures every question has a stable id and uniform option shape
# ===========================================================
def _normalize_question(category_key: str, q: Dict[str, Any]) -> Dict[str, Any]:
    q2 = dict(q)

    # Stable question key:
    # Prefer explicit JSON id, otherwise generate hash from category + question text
    qid = q2.get("id")
    if qid is None or str(qid).strip() == "":
        qid = make_json_question_key(category_key, str(q2.get("question", "")))
    q2["id"] = str(qid)

    # Normalize options to {"A":..., "B":..., "C":..., "D":...}
    options = q2.get("options")
    if isinstance(options, dict):
        q2["options"] = {
            "A": options.get("A", ""),
            "B": options.get("B", ""),
            "C": options.get("C", ""),
            "D": options.get("D", ""),
        }
    else:
        q2["options"] = {
            "A": q2.get("option_a", ""),
            "B": q2.get("option_b", ""),
            "C": q2.get("option_c", ""),
            "D": q2.get("option_d", ""),
        }

    # Normalize answer key
    if "answer" not in q2 and "correct_option" in q2:
        q2["answer"] = q2.get("correct_option")

    return q2


# ===========================================================
# GET CATEGORY QUESTIONS (sorted + cached)
# ===========================================================
def _get_category_questions_sorted(category_key: str) -> List[Dict[str, Any]]:
    """
    Returns normalized questions for a category.
    Sorted deterministically:
    - by numeric id if possible
    - otherwise by question text
    Cached after first call.
    """
    if category_key in _CATEGORY_CACHE:
        return _CATEGORY_CACHE[category_key]

    all_q = _load_questions()
    cat_q = [
        _normalize_question(category_key, q)
        for q in all_q
        if q.get("category") == category_key
    ]

    def _sort_key(q: Dict[str, Any]):
        qid = str(q.get("id") or "")
        if qid.isdigit():
            return (0, int(qid))
        return (1, str(q.get("question") or "").lower())

    cat_q.sort(key=_sort_key)
    _CATEGORY_CACHE[category_key] = cat_q
    return cat_q


# ===========================================================
# CORE: GET NEXT QUESTION FOR USER
# ===========================================================
async def get_next_question_for_user(tg_id: int, category: str) -> Dict[str, Any]:
    """
    Paid Trivia question picker using shared history table.

    Rules:
    1) Load all JSON questions in the category
    2) Exclude questions this user has already seen in source_type='json_paid'
    3) Return the first fresh question in deterministic order
    4) If category is exhausted, restart from beginning
    """
    category_key = CATEGORY_MAP.get(category)
    if not category_key:
        raise ValueError(f"Invalid category given: {category}")

    questions = _get_category_questions_sorted(category_key)
    if not questions:
        raise ValueError(f"No questions found for category {category}")

    async with get_async_session() as session:
        seen_keys = await get_seen_question_keys(
            session,
            tg_id=int(tg_id),
            source_type="json_paid",
            category=category_key,
        )

    fresh_questions = [q for q in questions if str(q["id"]) not in seen_keys]

    if fresh_questions:
        return fresh_questions[0]

    # Category exhausted → restart cycle from beginning
    return questions[0]


# ===========================================================
# OPTIONAL: PEEK NEXT QUESTION (does not change history)
# ===========================================================
async def peek_next_question_for_user(tg_id: int, category: str) -> Dict[str, Any]:
    """
    Returns what get_next_question_for_user would currently return,
    but does not record anything.
    """
    return await get_next_question_for_user(tg_id, category)


# ===========================================================
# RESET HELPERS
# These do not delete shared history anymore.
# They are retained for compatibility only.
# ===========================================================
async def reset_user_category_progress(tg_id: int, category: str) -> None:
    """
    Deprecated in shared-history mode.
    Intentionally does nothing.
    """
    return None


async def reset_user_all_categories(tg_id: int) -> None:
    """
    Deprecated in shared-history mode.
    Intentionally does nothing.
    """
    return None
