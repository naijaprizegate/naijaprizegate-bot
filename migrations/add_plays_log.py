import os
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")

def main():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL not found in env")
        return

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        # Ensure plays_log table exists
        cur.execute("""
            CREATE TABLE IF NOT EXISTS plays_log (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                outcome TEXT NOT NULL,
                prize TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
            );
        """)
        print("✅ Ensured table exists: plays_log")

        # Add useful indexes
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_plays_log_user_id
                ON plays_log (user_id);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_plays_log_outcome
                ON plays_log (outcome);
        """)
        print("✅ Ensured indexes exist on user_id and outcome")

        conn.commit()
        print("🎉 Migration completed successfully!")

    except Exception as e:
        conn.rollback()
        print("❌ Migration failed:", e)
        raise

    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    main()
