# ===========================================================
# utils/questions_loader.py  (Render-safe Lazy Loader)
# ===========================================================
import json
import os
import random
from typing import Any, Dict, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(__file__))  # root of project
QUESTIONS_PATH = os.path.join(BASE_DIR, "questions.json")

# ------------------------------
# CATEGORY TRANSLATION MAP
# ------------------------------
CATEGORY_MAP = {
    "History": "nigeria_history",
    "Entertainment": "nigeria_entertainment",
    "Football": "football",
    "Geography": "geography",
}

# In-memory cache (lazy-loaded)
_ALL_QUESTIONS: Optional[List[Dict[str, Any]]] = None

# Shuffle bags (lazy-refilled)
QUESTION_BAGS: Dict[str, List[Dict[str, Any]]] = {
    "nigeria_history": [],
    "nigeria_entertainment": [],
    "football": [],
    "geography": [],
}


def _load_questions() -> List[Dict[str, Any]]:
    """
    Load questions.json once (lazy).
    This prevents slow imports during Render startup.
    """
    global _ALL_QUESTIONS
    if _ALL_QUESTIONS is None:
        with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
            _ALL_QUESTIONS = json.load(f)
    return _ALL_QUESTIONS


def _refill_bag(category_key: str) -> None:
    """Refill a category shuffle bag when empty."""
    all_q = _load_questions()
    QUESTION_BAGS[category_key] = [q for q in all_q if q.get("category") == category_key]
    random.shuffle(QUESTION_BAGS[category_key])


def get_random_question(category: str = None) -> Dict[str, Any]:
    """
    Returns a single random question.
    Uses shuffle-bags to prevent repeated questions per category.
    """
    all_q = _load_questions()

    # Category chosen by user
    if category:
        real_key = CATEGORY_MAP.get(category)
        if not real_key:
            raise ValueError(f"Invalid category given: {category}")

        if not QUESTION_BAGS[real_key]:
            _refill_bag(real_key)

        return QUESTION_BAGS[real_key].pop()

    # No category → full random
    return random.choice(all_q)
