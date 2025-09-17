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
        # Add bonus_tries column if missing
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'users' AND column_name = 'bonus_tries'
        """)
        if not cur.fetchone():
            cur.execute("ALTER TABLE users ADD COLUMN bonus_tries INTEGER DEFAULT 0;")
            print("✅ Added column: users.bonus_tries")
        else:
            print("ℹ️ Column already exists: users.bonus_tries")

        # Ensure proofs table exists
        cur.execute("""
            CREATE TABLE IF NOT EXISTS proofs (
                id SERIAL PRIMARY KEY,
                tg_id BIGINT NOT NULL,
                file_id TEXT NOT NULL,
                status VARCHAR(32) DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        print("✅ Ensured table exists: proofs")

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
