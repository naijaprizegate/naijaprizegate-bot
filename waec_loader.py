# ====================================================================
# waec_loader.py
# ====================================================================

import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional


BASE_DIR = Path(__file__).resolve().parent
WAEC_DATA_DIR = BASE_DIR / "data" / "waec"


def load_json_file(file_path: Path) -> Any:
    """
    Load and return JSON content from a file.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"JSON file not found: {file_path}")

    with file_path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ====================================================================
# SUBJECTS
# ====================================================================
def get_waec_subjects() -> List[Dict[str, Any]]:
    """
    Load all active WAEC subjects from data/waec/subjects.json
    """
    file_path = WAEC_DATA_DIR / "subjects.json"
    subjects = load_json_file(file_path)

    return [subject for subject in subjects if subject.get("active") is True]


def get_waec_subject_by_code(subject_code: str) -> Optional[Dict[str, Any]]:
    """
    Return a single active WAEC subject by code.
    """
    subjects = get_waec_subjects()
    for subject in subjects:
        if subject.get("code") == subject_code:
            return subject
    return None


def get_waec_subject_folder(subject_code: str) -> Path:
    """
    Resolve the folder path for a WAEC subject.
    Example: bio -> data/waec/biology/
    """
    subject = get_waec_subject_by_code(subject_code)
    if not subject:
        raise ValueError(f"Subject not found or inactive: {subject_code}")

    folder_name = subject["folder"]
    return WAEC_DATA_DIR / folder_name


# ====================================================================
# TOPICS
# ====================================================================
def get_waec_subject_topics(subject_code: str) -> List[Dict[str, Any]]:
    """
    Load all active topics for a WAEC subject.
    """
    subject_folder = get_waec_subject_folder(subject_code)
    topics_file = subject_folder / "topics.json"
    topics_data = load_json_file(topics_file)

    topics = topics_data.get("topics", [])
    return [topic for topic in topics if topic.get("active") is True]


def get_waec_topic_by_id(subject_code: str, topic_id: str) -> Optional[Dict[str, Any]]:
    """
    Return a single topic by ID.
    """
    topics = get_waec_subject_topics(subject_code)
    for topic in topics:
        if topic.get("id") == topic_id:
            return topic
    return None


# ====================================================================
# QUESTIONS
# ====================================================================
def get_waec_questions_for_topic(subject_code: str, topic_id: str) -> List[Dict[str, Any]]:
    """
    Load all active questions for a given subject/topic.
    """
    subject_folder = get_waec_subject_folder(subject_code)
    topic = get_waec_topic_by_id(subject_code, topic_id)

    if not topic:
        raise ValueError(f"Topic not found: {topic_id}")

    relative_file = topic.get("file")
    if not relative_file:
        raise ValueError(f"Topic file is missing for topic: {topic_id}")

    question_file = subject_folder / relative_file
    questions = load_json_file(question_file)

    active_questions = [q for q in questions if q.get("active") is True]
    return active_questions


def shuffle_questions(questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Return a shuffled copy of the questions.
    """
    questions_copy = questions[:]
    random.shuffle(questions_copy)
    return questions_copy


def limit_questions(questions: List[Dict[str, Any]], count: int) -> List[Dict[str, Any]]:
    """
    Return only the first `count` questions.
    """
    return questions[:count]


def extract_question_ids(questions: List[Dict[str, Any]]) -> List[str]:
    """
    Return the list of question IDs from a question list.
    """
    return [q["id"] for q in questions if "id" in q]


def prepare_waec_topic_question_batch(
    subject_code: str,
    topic_id: str,
    requested_count: int,
    seen_question_ids: List[str]
) -> Dict[str, Any]:
    """
    Prepare a batch of questions for a WAEC topic.
    """
    all_questions = get_waec_questions_for_topic(subject_code, topic_id)
    all_question_ids = extract_question_ids(all_questions)

    unseen_questions = [
        q for q in all_questions if q.get("id") not in seen_question_ids
    ]

    cycle_reset = False

    if not unseen_questions:
        unseen_questions = all_questions[:]
        cycle_reset = True

    shuffled = shuffle_questions(unseen_questions)
    selected_questions = limit_questions(shuffled, requested_count)

    return {
        "cycle_reset": cycle_reset,
        "all_question_ids": all_question_ids,
        "available_count": len(unseen_questions),
        "selected_count": len(selected_questions),
        "selected_questions": selected_questions,
        "selected_question_ids": extract_question_ids(selected_questions)
    }

def get_available_questions_excluding_seen(
    subject_code: str,
    topic_id: str,
    seen_question_ids: List[str]
) -> List[Dict[str, Any]]:
    """
    Load all active questions for a topic and exclude questions
    the user has already seen in that topic.
    """
    all_questions = get_waec_questions_for_topic(subject_code, topic_id)
    remaining_questions = [
        q for q in all_questions if q.get("id") not in seen_question_ids
    ]
    return remaining_questions

