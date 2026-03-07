# ==================================================
# import_questions.py
# ===================================================

import json
import psycopg2

conn = psycopg2.connect(
    dbname="quizdb",
    user="postgres",
    password="yourpassword",
    host="localhost"
)

cur = conn.cursor()

with open("questions.json", "r") as f:
    data = json.load(f)

for category, questions in data.items():

    for q in questions:

        cur.execute("""
            INSERT INTO questions
            (category, question, option_a, option_b, option_c, option_d, correct_option)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (
            category,
            q["question"],
            q["options"][0],
            q["options"][1],
            q["options"][2],
            q["options"][3],
            q["answer"]
        ))

conn.commit()
cur.close()
conn.close()

print("Questions imported successfully")
