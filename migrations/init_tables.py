import os
import psycopg2


def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not found in env")
        return

    # psycopg2 does not understand "+asyncpg", strip it
    if database_url.startswith("postgresql+asyncpg://"):
        database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    conn = psycopg2.connect(database_url)
    cur = conn.cursor()

    try:
        # Enable UUID extension
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
            is_admin BOOLEAN DEFAULT FALSE NOT NULL,
            referred_by UUID REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_users_tg_id ON users(tg_id);")
        print("✅ users table ensured")

        # ----------------------
        # 2. Global Counter
        # ----------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS global_counter (
            id SERIAL PRIMARY KEY,
            paid_tries_total INT DEFAULT 0
        );
        """)
        print("✅ global_counter table ensured")

        # ----------------------
        # 3. Plays
        # ----------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS plays (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            result TEXT NOT NULL CHECK (result IN ('win','lose','pending')),
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_plays_user_id ON plays(user_id);")
        print("✅ plays table ensured")

        # ----------------------
        # 4. Payments
        # ----------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            tx_ref TEXT NOT NULL UNIQUE,
            status TEXT DEFAULT 'pending' CHECK (status IN ('pending','successful','failed','expired')),
            amount INT NOT NULL,
            tries INT DEFAULT 0,
            flw_tx_id TEXT,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        # Ensure indexes
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id);")

        # ✅ If payments table already exists but is missing flw_tx_id, add it
        cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='payments' AND column_name='flw_tx_id'
            ) THEN
                ALTER TABLE payments ADD COLUMN flw_tx_id TEXT;
                CREATE INDEX IF NOT EXISTS idx_payments_flw_tx_id ON payments(flw_tx_id);
            END IF;
        END$$;
        """)

        print("✅ payments table ensured (with flw_tx_id)")

        # ----------------------
        # 5. Proofs
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
        cur.execute("CREATE INDEX IF NOT EXISTS idx_proofs_user_id ON proofs(user_id);")
        print("✅ proofs table ensured")

        # ----------------------
        # 6. Transaction Logs
        # ----------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS transaction_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            provider TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        print("✅ transaction_logs table ensured")

        # ----------------------
        # 7. Game State
        # ----------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS game_state (
            id SERIAL PRIMARY KEY,
            current_cycle INT DEFAULT 1,
            paid_tries_this_cycle INT DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        print("✅ game_state table ensured")

        # Commit all changes
        conn.commit()
        print("🎉 Non-destructive migration completed successfully!")

    except Exception as e:
        conn.rollback()
        print("❌ Migration failed:", e)
        raise

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
