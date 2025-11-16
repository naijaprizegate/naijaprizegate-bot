# ===========================================================
# utils/questions_loader.py
# ===========================================================
import json
import os
import random

BASE_DIR = os.path.dirname(os.path.dirname(__file__))  # root of project
QUESTIONS_PATH = os.path.join(BASE_DIR, "data", "questions.json")

with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
    ALL_QUESTIONS = json.load(f)


def get_random_question(category: str = None):
    """
    Returns a single random question.
    If category is None → pick from all categories.
    """

    if category:
        filtered = [q for q in ALL_QUESTIONS if q["category"] == category]
        if not filtered:
            raise ValueError(f"No questions found in category: {category}")
        return random.choice(filtered)

    return random.choice(ALL_QUESTIONS)
