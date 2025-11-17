# =========================================================
# trivia.py
# =========================================================
import json
import random
import os

# Path → navigate from helpers/ to root folder where questions.json is located
FILE_PATH = os.path.join(os.path.dirname(__file__), "..", "questions.json")

# Load questions at import time
with open(FILE_PATH, "r", encoding="utf-8") as f:
    TRIVIA_DATA = json.load(f)

def get_random_question():
    """Return a single random trivia question."""
    return random.choice(TRIVIA_DATA)
