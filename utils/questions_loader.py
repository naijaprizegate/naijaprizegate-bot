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

# ===========================================================
# STEP 1 — SHUFFLE-BAG PER CATEGORY (Prevents repeats)
# ===========================================================
QUESTION_BAGS = {
    "nigeria_history": [],
    "nigeria_entertainment": [],
    "football": [],
    "geography": []
}


def _refill_bag(category_key: str):
    """Refill a category shuffle bag when empty."""
    QUESTION_BAGS[category_key] = [
        q for q in ALL_QUESTIONS if q["category"] == category_key
    ]
    random.shuffle(QUESTION_BAGS[category_key])


# ===========================================================
# STEP 2 — Modified random question system (uses shuffle bags)
# ===========================================================
def get_random_question(category: str = None):
    """
    Returns a single random question.
    Uses shuffle-bags to prevent repeated questions.
    """

    # Category chosen by user
    if category:
        real_key = CATEGORY_MAP.get(category)

        if not real_key:
            raise ValueError(f"Invalid category given: {category}")

        # Refill shuffle bag if empty
        if not QUESTION_BAGS[real_key]:
            _refill_bag(real_key)

        # Pop one question (guarantees no repeat until bag empties)
        return QUESTION_BAGS[real_key].pop()

    # No category → full random (no shuffle bag)
    return random.choice(ALL_QUESTIONS)
