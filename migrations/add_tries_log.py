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
        # Ensure tries_log table exists
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tries_log (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                change INT NOT NULL, -- +ve = add tries, -ve = consume tries
                reason TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
            );
        """)
        print("✅ Ensured table exists: tries_log")

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_tries_log_user_id
                ON tries_log (user_id);
        """)
        print("✅ Ensured index exists on user_id")

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
