# ======================================================
# university_loader.py
# ======================================================
import json
from pathlib import Path

BASE_PATH = Path("data/university")


# ===================================
# UNIVERSITY DATA
# ===================================

UNIVERSITY_CATEGORIES = [

    {
        "code": "general_studies",
        "name": "General Studies",
        "subjects": [
            "use_of_english",
            "logic_philosophy",
            "nigerian_culture",
            "entrepreneurship",
            "ict",
            "library_study_skills",
            "citizenship",
        ]
    },

    {
        "code": "science_foundation",
        "name": "Science Foundation",
        "subjects": [
            "general_biology",
            "general_chemistry",
            "general_physics",
            "practical_biology",
            "practical_chemistry",
            "practical_physics",
            "basic_mathematics",
        ]
    },

    {
        "code": "engineering_foundation",
        "name": "Engineering Foundation",
        "subjects": [
            "algebra",
            "calculus",
            "engineering_physics",
            "engineering_chemistry",
            "engineering_drawing",
            "basic_computing",
        ]
    },

    {
        "code": "social_management",
        "name": "Social and Management Sciences Foundation",
        "subjects": [
            "economics",
            "accounting",
            "social_science_mathematics",
            "statistics",
            "business_management",
        ]
    },

    {
        "code": "arts_humanities",
        "name": "Arts and Humanities Foundation",
        "subjects": [
            "communication_english",
            "history",
            "literature",
            "government",
        ]
    },

]

# ===================================
# SUBJECTS
# ===================================

UNIVERSITY_SUBJECTS = [

    # --------------------------------
    # GENERAL STUDIES
    # --------------------------------
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

    # --------------------------------
    # SCIENCE FOUNDATION
    # --------------------------------
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

    # --------------------------------
    # ENGINEERING FOUNDATION
    # --------------------------------
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

    # --------------------------------
    # SOCIAL & MANAGEMENT
    # --------------------------------
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

    # --------------------------------
    # ARTS & HUMANITIES
    # --------------------------------
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

def get_university_categories():
    return UNIVERSITY_CATEGORIES


# ===================================
# GET SUBJECTS BY CATEGORY
# ===================================
def get_university_subjects_by_category(
    category_code: str
):

    return [

        subject

        for subject in UNIVERSITY_SUBJECTS

        if subject["category_code"] == category_code
    ]


# ===================================
# GET SUBJECT BY CODE
# ===================================
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
