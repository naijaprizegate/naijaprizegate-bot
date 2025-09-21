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
        # Enable UUID extension (for UUID PKs)
        cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

        # ----------------------
        # 1. Users table
        # ----------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tg_id BIGINT NOT NULL UNIQUE,
            username TEXT,
            tries_paid INT DEFAULT 0,
            tries_bonus INT DEFAULT 0,
            referred_by UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_users_tg_id ON users(tg_id);""")
        print("✅ users table & index created")

        # ----------------------
        # 2. Global Counter table
        # ----------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS global_counter (
            id SERIAL PRIMARY KEY,
            paid_tries_total INT DEFAULT 0
        );
        """)
        print("✅ global_counter table created")

        # ----------------------
        # 3. Plays table
        # ----------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS plays (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            result TEXT NOT NULL CHECK (result IN ('win','lose','pending')),
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_plays_user_id ON plays(user_id);""")
        print("✅ plays table & index created")

        # ----------------------
        # 4. Payments table
        # ----------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            tx_ref TEXT NOT NULL UNIQUE,
            status TEXT DEFAULT 'pending' CHECK (status IN ('pending','successful','failed')),
            amount INT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id);""")
        print("✅ payments table & index created")

        # ----------------------
        # 5. Proofs table
        # ----------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS proofs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            file_id TEXT NOT NULL,
            status TEXT DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cur.execute("""CREATE INDEX IF NOT EXISTS idx_proofs_user_id ON proofs(user_id);""")
        print("✅ proofs table & index created")

        # ----------------------
        # 6. Transaction Logs table
        # ----------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS transaction_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            provider TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        print("✅ transaction_logs table created")

        # Commit changes
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
