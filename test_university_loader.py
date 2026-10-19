
from university_loader import (
    get_university_categories,
    get_university_subjects_by_category,
    get_university_modules,
    get_university_module_topics,
    load_university_topic_questions,
)


def main():
    categories = get_university_categories()
    print("UNIVERSITY CATEGORIES:")
    print(categories)

    category_code = "science_foundation"
    subject_code = "general_biology"

    subjects = get_university_subjects_by_category(category_code)
    print("\nSUBJECTS:")
    print(subjects)

    modules = get_university_modules(category_code, subject_code)
    print("\nMODULES:")
    print(modules)

    if not modules:
        print("\nNo active modules found. Nothing more to inspect.")
        return

    # Use the first active module instead of assuming that
    # "cell_biology" is a module ID.
    module_id = modules[0]["id"]

    topics = get_university_module_topics(
        category_code,
        subject_code,
        module_id,
    )
    print("\nTOPICS:")
    print(topics)

    if not topics:
        print("\nNo active topics found in this module.")
        return

    topic_id = topics[0]["id"]

    questions = load_university_topic_questions(
        category_code,
        subject_code,
        module_id,
        topic_id,
    )
    print("\nTOPIC QUESTIONS:")
    print(questions)


if __name__ == "__main__":
    main()
