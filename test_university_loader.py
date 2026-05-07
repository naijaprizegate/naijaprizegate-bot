from university_loader import (
    get_university_categories,
    get_university_subjects_by_category,
    get_university_topics,
    load_university_topic_questions,
)

print(get_university_categories())

print(
    get_university_subjects_by_category(
        "science_foundation"
    )
)

print(
    get_university_topics(
        "science_foundation",
        "general_biology"
    )
)

print(
    load_university_topic_questions(
        "science_foundation",
        "general_biology",
        "cell_biology"
    )
)
