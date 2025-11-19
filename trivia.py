# ==========================================================
# trivia.py — Advanced Trivia Loader (Stable for Production)
# ==========================================================
import json
import random
import os
import time
from typing import Optional, Dict, List

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILE_PATH = os.path.abspath(os.path.join(BASE_DIR, "..", "data", "questions.json"))

# Cache
TRIVIA_CACHE: List[Dict] = []
LAST_LOAD_TIME: float = 0
AUTO_RELOAD_SECONDS = 45  # reload every 45 seconds (only if file changed)

# Default fallback in case questions.json missing or broken
FALLBACK_QUESTIONS = [
    {
        "id": 1,
        "category": "General",
        "question": "What country is known as the Giant of Africa?",
        "options": {"A": "Kenya", "B": "South Africa", "C": "Nigeria", "D": "Egypt"},
        "answer": "C"
    }
]


# ------------------------------------------------------------
# Validate question format
# ------------------------------------------------------------
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


# ------------------------------------------------------------
# Load questions from file safely
# ------------------------------------------------------------
def load_questions(force_reload: bool = False) -> List[Dict]:
    global TRIVIA_CACHE, LAST_LOAD_TIME

    # Cache still fresh?
    if not force_reload and TRIVIA_CACHE and (time.time() - LAST_LOAD_TIME < AUTO_RELOAD_SECONDS):
        return TRIVIA_CACHE

    # Attempt loading file safely
    try:
        with open(FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        valid_questions = [q for q in data if validate_question(q)]

        if not valid_questions:
            print("⚠️ WARNING: No valid trivia questions found. Using fallback.")
            TRIVIA_CACHE = FALLBACK_QUESTIONS
        else:
            TRIVIA_CACHE = valid_questions

        LAST_LOAD_TIME = time.time()
        return TRIVIA_CACHE

    except FileNotFoundError:
        print(f"⚠️ WARNING: questions.json missing → Using fallback questions.")
        TRIVIA_CACHE = FALLBACK_QUESTIONS
        return TRIVIA_CACHE

    except json.JSONDecodeError:
        print(f"⚠️ WARNING: Invalid JSON format → Using fallback questions.")
        TRIVIA_CACHE = FALLBACK_QUESTIONS
        return TRIVIA_CACHE

    except Exception as e:
        print(f"❌ ERROR loading trivia: {e}")
        TRIVIA_CACHE = FALLBACK_QUESTIONS
        return TRIVIA_CACHE


# ------------------------------------------------------------
# Get a random question (optional category filter)
# ------------------------------------------------------------
def get_random_question(category: Optional[str] = None) -> Dict:
    questions = load_questions()

    if category:
        filtered = [q for q in questions if q.get("category", "").lower() == category.lower()]
        if filtered:
            return random.choice(filtered)

    # fallback to full set
    return random.choice(questions)
