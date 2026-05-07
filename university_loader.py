# ======================================================
# university_loader.py
# ======================================================
import json
from pathlib import Path

BASE_PATH = Path("data/university")


# ===================================
# CATEGORIES
# ===================================
UNIVERSITY_CATEGORIES = [
    {
        "code": "science_foundation",
        "name": "Science Foundation",
    },
]


def get_university_categories():
    return UNIVERSITY_CATEGORIES


# ===================================
# SUBJECTS
# ===================================
def get_university_subjects(category_code: str):
    category_path = BASE_PATH / category_code

    if not category_path.exists():
        return []

    subjects = []

    for subject_dir in category_path.iterdir():
        if subject_dir.is_dir():
            subjects.append({
                "code": subject_dir.name,
                "name": subject_dir.name.replace("_", " ").title(),
            })

    return sorted(subjects, key=lambda x: x["name"])


# ===================================
# GET SUBJECT BY CODE
# ===================================
def get_university_subject_by_code(category_code: str, subject_code: str):
    subjects = get_university_subjects(category_code)

    return next(
        (s for s in subjects if s["code"] == subject_code),
        None
    )


# ===================================
# TOPICS
# ===================================
def get_university_topics(category_code: str, subject_code: str):
    topics_path = (
        BASE_PATH
        / category_code
        / subject_code
        / "topics.json"
    )

    if not topics_path.exists():
        return []

    with open(topics_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ===================================
# LOAD QUESTIONS
# ===================================
def load_university_topic_questions(
    category_code: str,
    subject_code: str,
    topic_id: str,
):
    question_file = (
        BASE_PATH
        / category_code
        / subject_code
        / "questions"
        / f"{topic_id}.json"
    )

    if not question_file.exists():
        return []

    with open(question_file, "r", encoding="utf-8") as f:
        return json.load(f)
