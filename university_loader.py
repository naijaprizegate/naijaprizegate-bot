# =========================================================
# university_loader.py
# =========================================================

import json
import random
from pathlib import Path


# =========================================================
# ROOT
# =========================================================

BASE_DIR = Path("data/university")


# =========================================================
# HELPERS
# =========================================================

def prettify_name(name: str) -> str:
    return name.replace("_", " ").title()


def safe_load_json(path: Path):
    if not path.exists():
        return None

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# =========================================================
# CATEGORY LOADERS
# =========================================================

def get_university_categories():
    categories = []

    if not BASE_DIR.exists():
        return categories

    for folder in BASE_DIR.iterdir():
        if folder.is_dir():
            categories.append({
                "code": folder.name,
                "name": prettify_name(folder.name),
            })

    categories.sort(key=lambda x: x["name"])

    return categories


def get_university_category_by_code(category_code: str):
    categories = get_university_categories()

    for category in categories:
        if category["code"] == category_code:
            return category

    return None


# =========================================================
# SUBJECT LOADERS
# =========================================================

def get_university_subjects_by_category(category_code: str):
    category_path = BASE_DIR / category_code

    subjects = []

    if not category_path.exists():
        return subjects

    for folder in category_path.iterdir():
        if folder.is_dir():
            subjects.append({
                "code": folder.name,
                "name": prettify_name(folder.name),
                "category_code": category_code,
            })

    subjects.sort(key=lambda x: x["name"])

    return subjects


def get_university_subject_by_code(category_code: str, subject_code: str):
    subjects = get_university_subjects_by_category(category_code)

    for subject in subjects:
        if subject["code"] == subject_code:
            return subject

    return None


# =========================================================
# COURSE METADATA
# =========================================================

def get_university_course_info(category_code: str, subject_code: str):
    course_path = (
        BASE_DIR
        / category_code
        / subject_code
        / "course.json"
    )

    return safe_load_json(course_path) or {}


# =========================================================
# MODULE LOADERS
# =========================================================

def get_university_modules(category_code: str, subject_code: str):
    modules_path = (
        BASE_DIR
        / category_code
        / subject_code
        / "modules.json"
    )

    modules = safe_load_json(modules_path)

    if not modules:
        return []

    return [
        module
        for module in modules
        if module.get("active", True)
    ]


def get_university_module_by_id(
    category_code: str,
    subject_code: str,
    module_id: str,
):
    modules = get_university_modules(
        category_code,
        subject_code,
    )

    for module in modules:
        if module["id"] == module_id:
            return module

    return None


# =========================================================
# TOPIC LOADERS
# =========================================================

def get_university_module_topics(
    category_code: str,
    subject_code: str,
    module_id: str,
):
    module_topics_path = (
        BASE_DIR
        / category_code
        / subject_code
        / "topics"
        / f"{module_id}.json"
    )

    data = safe_load_json(module_topics_path)

    if not data:
        return []

    topics = data.get("topics", [])

    return [
        topic
        for topic in topics
        if topic.get("active", True)
    ]


def get_university_topic_by_id(
    category_code: str,
    subject_code: str,
    module_id: str,
    topic_id: str,
):
    topics = get_university_module_topics(
        category_code,
        subject_code,
        module_id,
    )

    for topic in topics:
        if topic["id"] == topic_id:
            return topic

    return None


# =========================================================
# QUESTION LOADERS
# =========================================================

def load_university_topic_questions(
    category_code: str,
    subject_code: str,
    module_id: str,
    topic_id: str,
):
    topic = get_university_topic_by_id(
        category_code,
        subject_code,
        module_id,
        topic_id,
    )

    if not topic:
        return []

    question_file = topic.get("file")

    if not question_file:
        return []

    question_path = (
        BASE_DIR
        / category_code
        / subject_code
        / "questions"
        / Path(question_file).name
    )

    questions = safe_load_json(question_path)

    if not questions:
        return []

    return [
        question
        for question in questions
        if question.get("active", True)
    ]


# =========================================================
# TOPIC PRACTICE BATCH
# =========================================================

def prepare_university_topic_question_batch(
    category_code: str,
    subject_code: str,
    module_id: str,
    topic_id: str,
    requested_count: int,
    seen_question_ids: list[str] | None = None,
):
    seen_question_ids = seen_question_ids or []

    questions = load_university_topic_questions(
        category_code=category_code,
        subject_code=subject_code,
        module_id=module_id,
        topic_id=topic_id,
    )

    unseen_questions = [
        q
        for q in questions
        if str(q.get("id")) not in seen_question_ids
    ]

    cycle_reset = False

    if not unseen_questions:
        unseen_questions = questions
        cycle_reset = True

    random.shuffle(unseen_questions)

    selected_questions = unseen_questions[:requested_count]

    selected_question_ids = [
        str(q.get("id"))
        for q in selected_questions
    ]

    return {
        "selected_questions": selected_questions,
        "selected_question_ids": selected_question_ids,
        "cycle_reset": cycle_reset,
    }


# =========================================================
# COURSE MOCK BATCH
# =========================================================

def prepare_university_course_mock_batch(
    category_code: str,
    subject_code: str,
    requested_count: int,
    seen_question_ids: list[str] | None = None,
):
    seen_question_ids = seen_question_ids or []

    modules = get_university_modules(
        category_code,
        subject_code,
    )

    all_questions = []

    for module in modules:
        module_topics = get_university_module_topics(
            category_code,
            subject_code,
            module["id"],
        )

        for topic in module_topics:
            topic_questions = load_university_topic_questions(
                category_code=category_code,
                subject_code=subject_code,
                module_id=module["id"],
                topic_id=topic["id"],
            )

            all_questions.extend(topic_questions)

    unseen_questions = [
        q
        for q in all_questions
        if str(q.get("id")) not in seen_question_ids
    ]

    cycle_reset = False

    if not unseen_questions:
        unseen_questions = all_questions
        cycle_reset = True

    random.shuffle(unseen_questions)

    selected_questions = unseen_questions[:requested_count]

    selected_question_ids = [
        str(q.get("id"))
        for q in selected_questions
    ]

    return {
        "selected_questions": selected_questions,
        "selected_question_ids": selected_question_ids,
        "cycle_reset": cycle_reset,
    }
