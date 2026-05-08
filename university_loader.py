# ======================================================
# university_loader.py
# ======================================================

import json
import random
from pathlib import Path


# --------
# BASE PATH
# ---------

BASE_PATH = Path("data/university")


# ---------------------
# UNIVERSITY CATEGORIES
# ---------------------

UNIVERSITY_CATEGORIES = [

    # --------------------------------------------------
    # GENERAL STUDIES
    # --------------------------------------------------
    {
        "code": "general_studies",
        "name": "General Studies",
    },

    # --------------------------------------------------
    # SCIENCE FOUNDATION
    # --------------------------------------------------
    {
        "code": "science_foundation",
        "name": "Science Foundation",
    },

    # --------------------------------------------------
    # ENGINEERING FOUNDATION
    # --------------------------------------------------
    {
        "code": "engineering_foundation",
        "name": "Engineering Foundation",
    },

    # --------------------------------------------------
    # SOCIAL & MANAGEMENT
    # --------------------------------------------------
    {
        "code": "social_management",
        "name": "Social and Management Sciences Foundation",
    },

    # --------------------------------------------------
    # ARTS & HUMANITIES
    # --------------------------------------------------
    {
        "code": "arts_humanities",
        "name": "Arts and Humanities Foundation",
    },

]


# -------------------------
# UNIVERSITY SUBJECTS
# -----------------------

UNIVERSITY_SUBJECTS = [

    # --------------------------------------------------
    # GENERAL STUDIES
    # --------------------------------------------------
    {
        "code": "use_of_english",
        "name": "Use of English / Communication in English",
        "category_code": "general_studies",
    },

    {
        "code": "logic_philosophy",
        "name": "Logic, Philosophy, and Human Existence",
        "category_code": "general_studies",
    },

    {
        "code": "nigerian_culture",
        "name": "Nigerian Peoples and Culture",
        "category_code": "general_studies",
    },

    {
        "code": "entrepreneurship",
        "name": "Entrepreneurship",
        "category_code": "general_studies",
    },

    {
        "code": "ict",
        "name": "ICT / Computer Appreciation",
        "category_code": "general_studies",
    },

    {
        "code": "library_study_skills",
        "name": "Library Use / Study Skills",
        "category_code": "general_studies",
    },

    {
        "code": "citizenship",
        "name": "Citizenship / Civic-related Courses",
        "category_code": "general_studies",
    },

    # --------------------------------------------------
    # SCIENCE FOUNDATION
    # --------------------------------------------------
    {
        "code": "general_biology",
        "name": "General Biology",
        "category_code": "science_foundation",
    },

    {
        "code": "general_chemistry",
        "name": "General Chemistry",
        "category_code": "science_foundation",
    },

    {
        "code": "general_physics",
        "name": "General Physics",
        "category_code": "science_foundation",
    },

    {
        "code": "practical_biology",
        "name": "Practical Biology",
        "category_code": "science_foundation",
    },

    {
        "code": "practical_chemistry",
        "name": "Practical Chemistry",
        "category_code": "science_foundation",
    },

    {
        "code": "practical_physics",
        "name": "Practical Physics",
        "category_code": "science_foundation",
    },

    {
        "code": "basic_mathematics",
        "name": "Basic Mathematics / Statistics",
        "category_code": "science_foundation",
    },

    # --------------------------------------------------
    # ENGINEERING FOUNDATION
    # --------------------------------------------------
    {
        "code": "algebra",
        "name": "Algebra",
        "category_code": "engineering_foundation",
    },

    {
        "code": "calculus",
        "name": "Calculus",
        "category_code": "engineering_foundation",
    },

    {
        "code": "engineering_physics",
        "name": "General Physics",
        "category_code": "engineering_foundation",
    },

    {
        "code": "engineering_chemistry",
        "name": "General Chemistry",
        "category_code": "engineering_foundation",
    },

    {
        "code": "engineering_drawing",
        "name": "Engineering Drawing",
        "category_code": "engineering_foundation",
    },

    {
        "code": "basic_computing",
        "name": "Basic Computing",
        "category_code": "engineering_foundation",
    },

    # --------------------------------------------------
    # SOCIAL & MANAGEMENT
    # --------------------------------------------------
    {
        "code": "economics",
        "name": "Economics",
        "category_code": "social_management",
    },

    {
        "code": "accounting",
        "name": "Accounting",
        "category_code": "social_management",
    },

    {
        "code": "social_science_mathematics",
        "name": "Mathematics for Social Sciences",
        "category_code": "social_management",
    },

    {
        "code": "statistics",
        "name": "Statistics",
        "category_code": "social_management",
    },

    {
        "code": "business_management",
        "name": "Business Studies / Principles of Management",
        "category_code": "social_management",
    },

    # --------------------------------------------------
    # ARTS & HUMANITIES
    # --------------------------------------------------
    {
        "code": "communication_english",
        "name": "Communication in English",
        "category_code": "arts_humanities",
    },

    {
        "code": "history",
        "name": "History",
        "category_code": "arts_humanities",
    },

    {
        "code": "literature",
        "name": "Literature",
        "category_code": "arts_humanities",
    },

    {
        "code": "government",
        "name": "Government-related Courses",
        "category_code": "arts_humanities",
    },

]


# -----------------------------
# GET ALL CATEGORIES
# ----------------------------

def get_university_categories():

    return UNIVERSITY_CATEGORIES


# -------------------------------------------
# GET CATEGORY BY CODE
# ------------------------------------------

def get_university_category_by_code(category_code: str):

    return next(

        (
            category

            for category in UNIVERSITY_CATEGORIES

            if category["code"] == category_code
        ),

        None
    )


# --------------------------------
# GET SUBJECTS BY CATEGORY
# --------------------------------

def get_university_subjects_by_category(
    category_code: str
):

    return [

        subject

        for subject in UNIVERSITY_SUBJECTS

        if subject["category_code"] == category_code
    ]


# ------------------------------
# GET SUBJECT BY CODE
# -----------------------------

def get_university_subject_by_code(
    subject_code: str
):

    return next(

        (
            subject

            for subject in UNIVERSITY_SUBJECTS

            if subject["code"] == subject_code
        ),

        None
    )


# --------------------------------
# GET TOPICS
# -------------------------------

def get_university_topics(
    category_code: str,
    subject_code: str,
):

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


# -------------------------------
# LOAD TOPIC QUESTIONS
# -----------------------------

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


# ======================================================
# PREPARE TOPIC QUESTION BATCH
# ======================================================

def prepare_university_topic_question_batch(
    category_code: str,
    subject_code: str,
    topic_id: str,
    requested_count: int,
    seen_question_ids: list[str] | None = None,
):

    seen_question_ids = seen_question_ids or []

    all_questions = load_university_topic_questions(
        category_code=category_code,
        subject_code=subject_code,
        topic_id=topic_id,
    )

    # -----------------------------------------
    # FILTER ACTIVE QUESTIONS ONLY
    # -----------------------------------------

    active_questions = [

        q

        for q in all_questions

        if q.get("is_active", True) is True
    ]

    # -----------------------------------------
    # REMOVE ALREADY-SEEN QUESTIONS
    # -----------------------------------------

    unseen_questions = [

        q

        for q in active_questions

        if str(q.get("id")) not in seen_question_ids
    ]

    cycle_reset = False

    # -----------------------------------------
    # IF USER HAS EXHAUSTED TOPIC
    # RESET CYCLE AUTOMATICALLY
    # -----------------------------------------

    if not unseen_questions:

        unseen_questions = active_questions.copy()

        cycle_reset = True

    # -----------------------------------------
    # SHUFFLE QUESTIONS
    # -----------------------------------------

    random.shuffle(unseen_questions)

    # -----------------------------------------
    # LIMIT TO REQUESTED COUNT
    # -----------------------------------------

    selected_questions = unseen_questions[:requested_count]

    # -----------------------------------------
    # EXTRACT QUESTION IDS
    # -----------------------------------------

    selected_question_ids = [

        str(q.get("id"))

        for q in selected_questions
    ]

    return {

        "selected_questions": selected_questions,

        "selected_question_ids": selected_question_ids,

        "cycle_reset": cycle_reset,
    }

# ======================================================
# LOAD ALL SUBJECT QUESTIONS
# ======================================================

def load_all_subject_questions(
    category_code: str,
    subject_code: str,
):

    topics = get_university_topics(
        category_code,
        subject_code,
    )

    all_questions = []

    for topic in topics:

        topic_id = topic.get("id")

        if not topic_id:
            continue

        topic_questions = load_university_topic_questions(
            category_code=category_code,
            subject_code=subject_code,
            topic_id=topic_id,
        )

        for question in topic_questions:

            # Attach topic_id to each question
            if "topic_id" not in question:
                question["topic_id"] = topic_id

            all_questions.append(question)

    return all_questions


# ======================================================
# FILTER ACTIVE QUESTIONS
# ======================================================

def filter_active_questions(
    questions: list[dict]
):

    return [

        q

        for q in questions

        if q.get("is_active", True) is True
    ]


# ======================================================
# FILTER UNSEEN QUESTIONS
# ======================================================

def filter_unseen_questions(
    questions: list[dict],
    seen_question_ids: list[str] | None = None,
):

    seen_question_ids = seen_question_ids or []

    return [

        q

        for q in questions

        if str(q.get("id")) not in seen_question_ids
    ]


# ======================================================
# PREPARE UNIVERSITY COURSE MOCK BATCH
# ======================================================

def prepare_university_course_mock_batch(
    category_code: str,
    subject_code: str,
    requested_count: int,
    seen_question_ids: list[str] | None = None,
):

    seen_question_ids = seen_question_ids or []

    # -----------------------------------------
    # LOAD ALL QUESTIONS ACROSS ALL TOPICS
    # -----------------------------------------

    all_questions = load_all_subject_questions(
        category_code=category_code,
        subject_code=subject_code,
    )

    # -----------------------------------------
    # FILTER ACTIVE QUESTIONS
    # -----------------------------------------

    active_questions = filter_active_questions(
        all_questions
    )

    # -----------------------------------------
    # FILTER UNSEEN QUESTIONS
    # -----------------------------------------

    unseen_questions = filter_unseen_questions(
        active_questions,
        seen_question_ids,
    )

    cycle_reset = False

    # -----------------------------------------
    # RESET IF USER EXHAUSTED QUESTION POOL
    # -----------------------------------------

    if not unseen_questions:

        unseen_questions = active_questions.copy()

        cycle_reset = True

    # -----------------------------------------
    # RANDOMIZE QUESTIONS
    # -----------------------------------------

    random.shuffle(unseen_questions)

    # -----------------------------------------
    # LIMIT TO REQUESTED COUNT
    # -----------------------------------------

    selected_questions = unseen_questions[:requested_count]

    # -----------------------------------------
    # EXTRACT IDS
    # -----------------------------------------

    selected_question_ids = [

        str(q.get("id"))

        for q in selected_questions
    ]

    return {

        "selected_questions": selected_questions,

        "selected_question_ids": selected_question_ids,

        "cycle_reset": cycle_reset,
    }

