def get_random_question(category: str = None):
    """
    Returns a single random question.
    Supports category filtering with strict mapping.
    """

    if category:
        real_key = CATEGORY_MAP.get(category)

        if not real_key:
            raise ValueError(f"Invalid category given: {category}")

        filtered = [q for q in ALL_QUESTIONS if q.get("category") == real_key]

        if not filtered:
            raise ValueError(f"No questions found under category: {real_key}")

        return random.choice(filtered)

    # No category → return from all
    return random.choice(ALL_QUESTIONS)
