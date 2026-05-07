from university_loader import (
    get_university_categories,
    get_university_subjects,
    get_university_topics,
    load_university_topic_questions,
)

print(get_university_categories())

print(get_university_subjects("science_foundation"))

print(get_university_topics(
    "science_foundation",
    "biology"
))

print(load_university_topic_questions(
    "science_foundation",
    "biology",
    "cell_biology"
))
