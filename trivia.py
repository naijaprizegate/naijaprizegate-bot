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
# Get a random question (optional category)
# -----------------------------------------------------------
def get_random_question(category: Optional[str] = None) -> Dict:
    questions = load_questions()

    if category:
        filtered = [
            q for q in questions
            if q.get("category", "").lower() == category.lower()
        ]

        if filtered:
            return random.choice(filtered)

    return random.choice(questions)
