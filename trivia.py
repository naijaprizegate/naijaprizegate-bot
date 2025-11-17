# ===========================================================
# trivia.py (fixed for Render + correct data folder)
# ===========================================================
import json
import random
import os

# Base directory of the project (this file is inside /src)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Correct path to data/questions.json
FILE_PATH = os.path.join(BASE_DIR, "..", "data", "questions.json")

# Normalize path (important for Render)
FILE_PATH = os.path.abspath(FILE_PATH)

# Load questions at import
with open(FILE_PATH, "r", encoding="utf-8") as f:
    TRIVIA_DATA = json.load(f)

def get_random_question():
    """Return a single random trivia question."""
    return random.choice(TRIVIA_DATA)
