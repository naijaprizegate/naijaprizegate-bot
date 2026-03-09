import csv
import json
from pathlib import Path

INPUT_FILE = Path("questions.json")
OUTPUT_FILE = Path("questions.csv")

REQUIRED_TOP_LEVEL_KEYS = {"category", "question", "options", "answer"}
REQUIRED_OPTION_KEYS = {"A", "B", "C", "D"}

def validate_item(item: dict, index: int) -> None:
    missing = REQUIRED_TOP_LEVEL_KEYS - set(item.keys())
    if missing:
        raise ValueError(f"Item #{index} is missing keys: {sorted(missing)}")

    if not isinstance(item["options"], dict):
        raise ValueError(f"Item #{index} has invalid 'options' format; expected object.")

    missing_options = REQUIRED_OPTION_KEYS - set(item["options"].keys())
    if missing_options:
        raise ValueError(f"Item #{index} is missing option keys: {sorted(missing_options)}")

    answer = str(item["answer"]).strip().upper()
    if answer not in REQUIRED_OPTION_KEYS:
        raise ValueError(f"Item #{index} has invalid answer: {item['answer']}")

    category = str(item["category"]).strip()
    question = str(item["question"]).strip()

    if not category:
        raise ValueError(f"Item #{index} has empty category.")
    if not question:
        raise ValueError(f"Item #{index} has empty question.")

def main() -> None:
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Input file not found: {INPUT_FILE}")

    with INPUT_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("questions.json must contain a top-level array/list.")

    category_counters: dict[str, int] = {}
    rows: list[dict[str, str | int]] = []

    for idx, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Item #{idx} is not an object.")

        validate_item(item, idx)

        category = str(item["category"]).strip()
        category_counters[category] = category_counters.get(category, 0) + 1
        question_order = category_counters[category]

        options = item["options"]
        row = {
            "question": str(item["question"]).strip(),
            "option_a": str(options["A"]).strip(),
            "option_b": str(options["B"]).strip(),
            "option_c": str(options["C"]).strip(),
            "option_d": str(options["D"]).strip(),
            "correct_option": str(item["answer"]).strip().upper(),
            "category": category,
            "question_order": question_order,
        }
        rows.append(row)

    with OUTPUT_FILE.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "question",
                "option_a",
                "option_b",
                "option_c",
                "option_d",
                "correct_option",
                "category",
                "question_order",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Done. Wrote {len(rows)} rows to {OUTPUT_FILE}")
    print("\nCategory counts / question_order max:")
    for category, count in sorted(category_counters.items()):
        print(f"  {category}: {count}")

if __name__ == "__main__":
    main()
