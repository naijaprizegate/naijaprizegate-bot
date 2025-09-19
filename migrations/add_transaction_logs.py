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
        # Ensure transaction_logs table exists
        cur.execute("""
            CREATE TABLE IF NOT EXISTS transaction_logs (
                id SERIAL PRIMARY KEY,
                tx_ref VARCHAR(64) NOT NULL UNIQUE,
                tg_id BIGINT NOT NULL,
                amount NUMERIC(10,2) NOT NULL,
                status VARCHAR(20) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP NOT NULL
            );
        """)
        print("✅ Ensured table exists: transaction_logs")

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
