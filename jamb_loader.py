# ====================================================================
# jamb_loader.py
# ====================================================================

import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional


BASE_DIR = Path(__file__).resolve().parent
JAMB_DATA_DIR = BASE_DIR / "data" / "jamb"

ENG_EXACT_BLUEPRINT = [
    {"name": "comprehension", "topic_ids": ["eng_01"], "count": 5},
    {"name": "cloze", "topic_ids": ["eng_03"], "count": 10},
    {"name": "reading_text", "topic_ids": ["eng_02"], "count": 10},
    {"name": "sentence_interpretation", "topic_ids": ["eng_04"], "count": 5},
    {"name": "antonyms", "topic_ids": ["eng_06"], "count": 5},
    {"name": "synonyms", "topic_ids": ["eng_05"], "count": 5},
    {
        "name": "sentence_completion",
        "topic_ids": [
            "eng_07",
            "eng_08",
            "eng_09",
            "eng_10",
            "eng_11",
            "eng_12",
            "eng_13",
            "eng_14",
            "eng_15",
        ],
        "count": 10,
    },
    {
        "name": "oral_forms",
        "topic_ids": ["eng_16", "eng_17", "eng_18", "eng_19", "eng_20"],
        "count": 10,
    },
]


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
def get_jamb_subjects() -> List[Dict[str, Any]]:
    """
    Load all active JAMB subjects from data/jamb/subjects.json
    """
    file_path = JAMB_DATA_DIR / "subjects.json"
    subjects = load_json_file(file_path)

    return [subject for subject in subjects if subject.get("active") is True]


def get_subject_by_code(subject_code: str) -> Optional[Dict[str, Any]]:
    """
    Return a single active subject by code.
    Example: 'chem'
    """
    subjects = get_jamb_subjects()
    for subject in subjects:
        if subject.get("code") == subject_code:
            return subject
    return None


def get_subject_folder(subject_code: str) -> Path:
    """
    Resolve the folder path for a subject.
    Example: chem -> data/jamb/chemistry/
    """
    subject = get_subject_by_code(subject_code)
    if not subject:
        raise ValueError(f"Subject not found or inactive: {subject_code}")

    folder_name = subject["folder"]
    return JAMB_DATA_DIR / folder_name


# ====================================================================
# COURSES / RECOMMENDED SUBJECT COMBINATIONS
# ====================================================================
def get_course_subject_map() -> List[Dict[str, Any]]:
    """
    Load all course-to-subject recommendation mappings from
    data/jamb/course_subject_map.json
    """
    file_path = JAMB_DATA_DIR / "course_subject_map.json"
    courses = load_json_file(file_path)

    if not isinstance(courses, list):
        raise ValueError("course_subject_map.json must contain a list of course mappings.")

    return courses


def get_course_by_code(course_code: str) -> Optional[Dict[str, Any]]:
    """
    Return a single course mapping by course_code.
    Example: 'medicine'
    """
    courses = get_course_subject_map()
    for course in courses:
        if course.get("course_code") == course_code:
            return course
    return None


def get_course_subject_codes(course_code: str) -> List[str]:
    """
    Return the recommended subject codes for a course.
    Example: medicine -> ['eng', 'bio', 'chem', 'phys']
    """
    course = get_course_by_code(course_code)
    if not course:
        raise ValueError(f"Course not found: {course_code}")

    subject_codes = course.get("subjects", [])
    if not isinstance(subject_codes, list):
        raise ValueError(f"Invalid subject list for course: {course_code}")

    return [str(code) for code in subject_codes]


def get_course_subjects(course_code: str) -> List[Dict[str, Any]]:
    """
    Return the full active subject objects recommended for a course.
    Useful when you want subject names, folders, etc.
    """
    subject_codes = get_course_subject_codes(course_code)

    subjects: List[Dict[str, Any]] = []
    for code in subject_codes:
        subject = get_subject_by_code(code)
        if not subject:
            raise ValueError(
                f"Course '{course_code}' references subject code '{code}' "
                f"which is missing or inactive in subjects.json"
            )
        subjects.append(subject)

    return subjects


def validate_course_subject_map() -> List[str]:
    """
    Validate that every subject code referenced in course_subject_map.json
    exists in active subjects.json.

    Returns a list of validation error messages.
    Empty list means everything is valid.
    """
    errors: List[str] = []
    courses = get_course_subject_map()

    active_subject_codes = {subject["code"] for subject in get_jamb_subjects()}

    for course in courses:
        course_code = course.get("course_code", "<missing_course_code>")
        course_name = course.get("course_name", "<missing_course_name>")
        subject_codes = course.get("subjects", [])

        if not isinstance(subject_codes, list):
            errors.append(
                f"{course_code} ({course_name}) has invalid 'subjects' format; expected a list."
            )
            continue

        if len(subject_codes) != 4:
            errors.append(
                f"{course_code} ({course_name}) should have exactly 4 subjects, found {len(subject_codes)}."
            )

        if "eng" not in subject_codes:
            errors.append(
                f"{course_code} ({course_name}) is missing compulsory 'eng' in subjects list."
            )

        for code in subject_codes:
            if code not in active_subject_codes:
                errors.append(
                    f"{course_code} ({course_name}) references unknown or inactive subject code: {code}"
                )

    return errors


# ====================================================================
# TOPICS
# ====================================================================
def get_subject_topics(subject_code: str) -> List[Dict[str, Any]]:
    """
    Load all active topics for a subject.
    """
    subject_folder = get_subject_folder(subject_code)
    topics_file = subject_folder / "topics.json"
    topics_data = load_json_file(topics_file)

    topics = topics_data.get("topics", [])
    return [topic for topic in topics if topic.get("active") is True]


def get_topic_by_id(subject_code: str, topic_id: str) -> Optional[Dict[str, Any]]:
    """
    Return a single topic by ID.
    Example: chem_01
    """
    topics = get_subject_topics(subject_code)
    for topic in topics:
        if topic.get("id") == topic_id:
            return topic
    return None


# ====================================================================
# QUESTIONS
# ====================================================================

def get_questions_for_topic(subject_code: str, topic_id: str) -> List[Dict[str, Any]]:
    """
    Load all active questions for a given subject/topic.
    """
    subject_folder = get_subject_folder(subject_code)
    topic = get_topic_by_id(subject_code, topic_id)

    if not topic:
        raise ValueError(f"Topic not found: {topic_id}")

    relative_file = topic.get("file")
    if not relative_file:
        raise ValueError(f"Topic file is missing for topic: {topic_id}")

    question_file = subject_folder / relative_file
    questions = load_json_file(question_file)

    active_questions = [q for q in questions if q.get("active") is True]
    return active_questions


def get_questions_for_topic_ids(
    subject_code: str,
    topic_ids: List[str],
) -> List[Dict[str, Any]]:
    """
    Load all active questions from multiple topic IDs for one subject.

    Example:
    - eng + ['eng_16', 'eng_17', 'eng_18'] -> loads all active questions
      from those oral English topics.
    """
    all_questions: List[Dict[str, Any]] = []

    for topic_id in topic_ids:
        try:
            topic_questions = get_questions_for_topic(subject_code, topic_id)
            all_questions.extend(topic_questions)
        except Exception:
            # Skip broken or missing topic files without crashing
            continue

    return all_questions


def group_questions_by_passage_id(
    questions: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Group questions by passage_id.

    Only questions with a non-empty passage_id are grouped.
    Questions without passage_id are ignored here.

    Example:
    {
        "eng_01_p001": [q1, q2, q3, q4, q5],
        "eng_01_p002": [q6, q7, q8, q9, q10],
    }
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}

    for question in questions:
        passage_id = str(question.get("passage_id") or "").strip()
        if not passage_id:
            continue

        if passage_id not in grouped:
            grouped[passage_id] = []

        grouped[passage_id].append(question)

    return grouped


def get_all_questions_for_subject(subject_code: str) -> List[Dict[str, Any]]:
    """
    Load all active questions across all active topics for a subject.

    Example:
    - biology -> loads questions from all active Biology topic files
    """
    topics = get_subject_topics(subject_code)
    all_questions: List[Dict[str, Any]] = []

    for topic in topics:
        topic_id = topic.get("id")
        if not topic_id:
            continue

        try:
            topic_questions = get_questions_for_topic(subject_code, topic_id)
            all_questions.extend(topic_questions)
        except Exception:
            # Skip broken topic files without crashing the whole subject loader
            continue

    return all_questions


def get_available_subject_questions_excluding_seen(
    subject_code: str,
    seen_question_ids: List[str],
) -> List[Dict[str, Any]]:
    """
    Load all active questions for a subject across all topics
    and exclude questions the user has already seen in that subject.
    """
    all_questions = get_all_questions_for_subject(subject_code)
    remaining_questions = [
        q for q in all_questions if q.get("id") not in seen_question_ids
    ]
    return remaining_questions


def prepare_subject_question_batch(
    subject_code: str,
    requested_count: int,
    seen_question_ids: List[str],
) -> Dict[str, Any]:
    """
    Prepare a subject-wide batch of questions across all topics.

    Logic:
    - load all active questions for the subject across all active topics
    - exclude seen questions
    - if no remaining questions, reset cycle
    - shuffle remaining questions
    - cap requested count to available count
    """
    all_questions = get_all_questions_for_subject(subject_code)
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
        "selected_question_ids": extract_question_ids(selected_questions),
    }



def prepare_use_of_english_batch(
    seen_question_ids: List[str],
) -> Dict[str, Any]:
    """
    Prepare a UTME-structured Use of English paper using the fixed blueprint.

    Logic:
    - use ENG_EXACT_BLUEPRINT
    - load questions from the mapped topic IDs for each section
    - exclude seen questions first
    - if a section does not have enough unseen questions, reset that section
    - shuffle within each section
    - pick the required count for each section
    - combine all selected questions in blueprint order
    """
    selected_questions: List[Dict[str, Any]] = []
    selected_question_ids: List[str] = []
    cycle_reset = False

    for section in ENG_EXACT_BLUEPRINT:
        section_name = str(section["name"])
        topic_ids = section["topic_ids"]
        required_count = int(section["count"])

        section_questions = get_questions_for_topic_ids("eng", topic_ids)

        unseen_section_questions = [
            q for q in section_questions
            if q.get("id") not in seen_question_ids
            and q.get("id") not in selected_question_ids
        ]

        if len(unseen_section_questions) < required_count:
            cycle_reset = True
            unseen_section_questions = [
                q for q in section_questions
                if q.get("id") not in selected_question_ids
            ]

        if section_name in {"comprehension", "reading_text"}:
            grouped_passages = group_questions_by_passage_id(unseen_section_questions)

            eligible_passage_groups = [
                questions
                for _, questions in grouped_passages.items()
                if len(questions) >= required_count
            ]

            if not eligible_passage_groups:
                raise ValueError(
                    f"Use of English section '{section_name}' requires a passage group "
                    f"with at least {required_count} questions, but none was available."
                )

            chosen_group = shuffle_questions(eligible_passage_groups)[0]
            picked_questions = limit_questions(chosen_group, required_count)
        else:
            shuffled_section_questions = shuffle_questions(unseen_section_questions)
            picked_questions = limit_questions(shuffled_section_questions, required_count)

        
        if len(picked_questions) != required_count:
            raise ValueError(
                f"Use of English section '{section_name}' requires {required_count} questions, "
                f"but only {len(picked_questions)} were available."
            )
        
        for question in picked_questions:
            question["_utme_section"] = section_name

        selected_questions.extend(picked_questions)
        selected_question_ids.extend(extract_question_ids(picked_questions))

    return {
        "cycle_reset": cycle_reset,
        "selected_count": len(selected_questions),
        "selected_questions": selected_questions,
        "selected_question_ids": selected_question_ids,
    }


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


def get_available_questions_excluding_seen(
    subject_code: str,
    topic_id: str,
    seen_question_ids: List[str]
) -> List[Dict[str, Any]]:
    """
    Load all active questions for a topic and exclude questions
    the user has already seen in that topic.
    """
    all_questions = get_questions_for_topic(subject_code, topic_id)
    remaining_questions = [
        q for q in all_questions if q.get("id") not in seen_question_ids
    ]
    return remaining_questions


def prepare_topic_question_batch(
    subject_code: str,
    topic_id: str,
    requested_count: int,
    seen_question_ids: List[str]
) -> Dict[str, Any]:
    """
    Prepare a batch of questions for a topic.

    Logic:
    - load all active questions for the topic
    - exclude seen questions
    - if no remaining questions, reset cycle
    - shuffle remaining questions
    - cap requested count to available count
    """
    all_questions = get_questions_for_topic(subject_code, topic_id)
    all_question_ids = extract_question_ids(all_questions)

    unseen_questions = [
        q for q in all_questions if q.get("id") not in seen_question_ids
    ]

    cycle_reset = False

    if not unseen_questions:
        # User has exhausted the topic, so reset cycle
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


# ====================================================================
# MESSAGE HELPERS
# ====================================================================
def format_topic_list_for_message(subject_code: str) -> str:
    """
    Build a simple numbered topic list for sending in a Telegram message.
    Useful if you ever want text fallback.
    """
    topics = get_subject_topics(subject_code)
    if not topics:
        return "No active topics available."

    lines = []
    subject = get_subject_by_code(subject_code)
    subject_name = subject["name"] if subject else subject_code.upper()

    lines.append(f"{subject_name} Topics:\n")

    for idx, topic in enumerate(topics, start=1):
        lines.append(f"{idx}. {topic['title']}")

    return "\n".join(lines)


def format_course_subjects_for_message(course_code: str) -> str:
    """
    Build a simple recommended subject-combination message for a course.
    Useful for Mock JAMB/UTME course recommendation screens.
    """
    course = get_course_by_code(course_code)
    if not course:
        return "Course not found."

    subjects = get_course_subjects(course_code)

    lines = [
        f"🎯 Recommended Subject Combination",
        "",
        f"Course: {course['course_name']}",
        "",
    ]

    for subject in subjects:
        lines.append(f"• {subject['name']}")

    notes = (course.get("notes") or "").strip()
    if notes:
        lines.extend(["", f"Note: {notes}"])

    return "\n".join(lines)
