# ======================================================
# university_loader.py
# ======================================================

UNIVERSITY_CATEGORIES = {
    "gst": {
        "name": "General Studies",
        "subjects": ["comm_eng"],
    },
}

UNIVERSITY_SUBJECTS = {
    "comm_eng": {
        "name": "Communication in English",
        "category_code": "gst",
        "topics": ["parts_of_speech", "tenses", "concord"],
    },
}

UNIVERSITY_TOPICS = {
    "parts_of_speech": {
        "title": "Parts of Speech",
        "subject_code": "comm_eng",
    },
    "tenses": {
        "title": "Tenses",
        "subject_code": "comm_eng",
    },
    "concord": {
        "title": "Concord",
        "subject_code": "comm_eng",
    },
}


def get_university_categories() -> list[dict]:
    items = []
    for code, data in UNIVERSITY_CATEGORIES.items():
        items.append({
            "code": code,
            "name": data["name"],
            "subjects": data["subjects"],
        })
    return items


def get_university_category_by_code(category_code: str) -> dict | None:
    category = UNIVERSITY_CATEGORIES.get(str(category_code or "").strip())
    if not category:
        return None

    return {
        "code": str(category_code).strip(),
        "name": category["name"],
        "subjects": category["subjects"],
    }


def get_university_subject_by_code(subject_code: str) -> dict | None:
    subject = UNIVERSITY_SUBJECTS.get(str(subject_code or "").strip())
    if not subject:
        return None

    return {
        "code": str(subject_code).strip(),
        "name": subject["name"],
        "category_code": subject["category_code"],
        "topics": subject["topics"],
    }


def get_university_topic_by_code(topic_code: str) -> dict | None:
    topic = UNIVERSITY_TOPICS.get(str(topic_code or "").strip())
    if not topic:
        return None

    return {
        "code": str(topic_code).strip(),
        "title": topic["title"],
        "subject_code": topic["subject_code"],
    }


def get_university_subject_topics(subject_code: str) -> list[dict]:
    subject = get_university_subject_by_code(subject_code)
    if not subject:
        return []

    items = []
    for topic_code in subject["topics"]:
        topic = get_university_topic_by_code(topic_code)
        if topic:
            items.append(topic)

    return items
