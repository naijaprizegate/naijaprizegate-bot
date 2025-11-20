# ===========================================================
# utils/questions_loader.py  (Final Working Category Loader)
# ===========================================================
import json
import os
import random

BASE_DIR = os.path.dirname(os.path.dirname(__file__))  # root of project
QUESTIONS_PATH = os.path.join(BASE_DIR, "questions.json")

with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
    ALL_QUESTIONS = json.load(f)

# ------------------------------
# CATEGORY TRANSLATION MAP
# ------------------------------
CATEGORY_MAP = {
    "History": "nigeria_history",
    "Entertainment": "nigeria_entertainment",
    "Football": "football",
    "Geography": "geography"
}


def get_random_question(category: str = None):
    """
    Returns a single random question.
    Supports category filtering with strict mapping.
    """

    if category:
        real_key = CATEGORY_MAP.get(category)

        if not real_key:
            raise ValueError(f"Invalid category given: {category}")

        filtered = [q for q in ALL_QUESTIONS if q.get("category") == real_key]

        if not filtered:
            raise ValueError(f"No questions found under category: {real_key}")

        return random.choice(filtered)

    # No category → return from all
    return random.choice(ALL_QUESTIONS)
