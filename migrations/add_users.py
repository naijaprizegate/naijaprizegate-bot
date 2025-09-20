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
        # Ensure users table exists
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                tg_id BIGINT NOT NULL UNIQUE,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                tries_remaining INT DEFAULT 0,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
            );
        """)
        print("✅ Ensured table exists: users")

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_users_tg_id
                ON users (tg_id);
        """)
        print("✅ Ensured index exists on tg_id")

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
