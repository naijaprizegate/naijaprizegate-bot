# ===========================================================
# trivia.py — Advanced Trivia Loader (FINAL, correct filepath)
# ===========================================================
import json
import random
import os
import time
from typing import Optional, Dict, List
from logger import logger

# -----------------------------------------------------------
# Correct file path (questions.json is in SAME directory)
# -----------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FILE_PATH = os.path.join(SCRIPT_DIR, "questions.json")

# Cache system
TRIVIA_CACHE: List[Dict] = []
LAST_LOAD_TIME: float = 0
AUTO_RELOAD_SECONDS = 45

# Default fallback
FALLBACK_QUESTIONS = [
    {
        "id": 1,
        "category": "General",
        "question": "What country is known as the Giant of Africa?",
        "options": {"A": "Kenya", "B": "South Africa", "C": "Nigeria", "D": "Egypt"},
        "answer": "C"
    }
]


# -----------------------------------------------------------
# Validate question structure
# -----------------------------------------------------------
def validate_question(q) -> bool:
    try:
        if not isinstance(q["id"], int):
            return False
        if not isinstance(q["question"], str):
            return False
        if not isinstance(q["options"], dict):
            return False
        if q["answer"] not in ["A", "B", "C", "D"]:
            return False
        return True
    except Exception:
        return False


# -----------------------------------------------------------
# Load questions.json safely + caching
# -----------------------------------------------------------
def load_questions(force_reload: bool = False) -> List[Dict]:
    global TRIVIA_CACHE, LAST_LOAD_TIME

    # cache still valid?
    if (
        TRIVIA_CACHE
        and not force_reload
        and (time.time() - LAST_LOAD_TIME < AUTO_RELOAD_SECONDS)
    ):
        return TRIVIA_CACHE

    try:
        with open(FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        valid_questions = [q for q in data if validate_question(q)]

        if not valid_questions:
            logger.warning("⚠️ No valid trivia questions found → fallback used.")
            TRIVIA_CACHE = FALLBACK_QUESTIONS
        else:
            TRIVIA_CACHE = valid_questions

        LAST_LOAD_TIME = time.time()
        logger.info(f"Trivia loaded: {len(TRIVIA_CACHE)} questions.")
        return TRIVIA_CACHE

    except FileNotFoundError:
        logger.warning("⚠️ questions.json missing → Using fallback questions.")
        TRIVIA_CACHE = FALLBACK_QUESTIONS
        return TRIVIA_CACHE

    except json.JSONDecodeError:
        logger.error("❌ Invalid JSON format in questions.json → fallback.")
        TRIVIA_CACHE = FALLBACK_QUESTIONS
        return TRIVIA_CACHE

    except Exception as e:
        logger.error(f"❌ Error loading trivia: {e}")
        TRIVIA_CACHE = FALLBACK_QUESTIONS
        return TRIVIA_CACHE
    
# -----------------------------------------------------------
# Category helpers (SEQUENTIAL, not random)
# -----------------------------------------------------------

def get_questions_for_category(category: str, force_reload: bool = False) -> List[Dict]:
    """
    Returns questions filtered by category, sorted in order (by id).
    """
    all_qs = load_questions(force_reload=force_reload)
    cat = (category or "").strip()

    qs = [q for q in all_qs if str(q.get("category", "")).strip() == cat]
    qs.sort(key=lambda x: int(x.get("id", 0)))
    return qs


def get_next_question_in_category(category: str, next_index: int = 0, force_reload: bool = False):
    """
    Sequential question picker.
    - category: category string
    - next_index: the index to serve now (0-based)
    Returns: (question_dict_or_None, new_next_index_int, total_in_category_int)
    """
    qs = get_questions_for_category(category, force_reload=force_reload)

    if not qs:
        return None, 0, 0

    # safety clamp + wrap
    idx = int(next_index or 0) % len(qs)

    question = qs[idx]
    new_next_index = (idx + 1) % len(qs)

    return question, new_next_index, len(qs)
