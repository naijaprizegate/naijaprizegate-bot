# ==============================================================
# migrations/init_tables.py
# ==============================================================
import os
import psycopg2

def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not found in env")
        return

    # psycopg2 does not understand "+asyncpg", fix it
    if database_url.startswith("postgresql+asyncpg://"):
        database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    conn = psycopg2.connect(database_url)
    cur = conn.cursor()

    try:
        # Enable UUID generation extension
        cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")

        # ----------------------
        # 1. Users
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

        # Add winner-related columns if missing
        cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='choice') THEN
                ALTER TABLE users ADD COLUMN choice TEXT;
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='winner_stage') THEN
                ALTER TABLE users ADD COLUMN winner_stage TEXT;
            END IF;

            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='winner_data') THEN
                ALTER TABLE users ADD COLUMN winner_data JSON DEFAULT '{}'::json;
            END IF;
        END$$;
        """)
        print("✅ winner fields ensured on users")

        # ----------------------
        # 2. Prize Winners ✅ FINAL CORRECT VERSION
        # ----------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS prize_winners (
            id SERIAL PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            tg_id BIGINT NOT NULL,
            choice TEXT NOT NULL,
            delivery_status TEXT DEFAULT 'Pending',
            submitted_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
            pending_at TIMESTAMPTZ,
            in_transit_at TIMESTAMPTZ,
            delivered_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            delivery_data JSONB DEFAULT '{}'::jsonb,
            last_updated_by UUID REFERENCES users(id) ON DELETE SET NULL
        );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_prize_winners_tg_id ON prize_winners(tg_id);")
        print("✅ prize_winners table ensured")

        # ----------------------
        # 3. Global Counter
        # ----------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS global_counter (
            id SERIAL PRIMARY KEY,
            paid_tries_total INT DEFAULT 0
        );
        """)
        print("✅ global_counter ensured")

        # ----------------------
        # 4. Plays
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
        print("✅ plays ensured")

        # ----------------------
        # 5. Payments
        # ----------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID REFERENCES users(id) ON DELETE CASCADE,
            tx_ref TEXT NOT NULL UNIQUE,
            status TEXT DEFAULT 'pending' CHECK (status IN ('pending','successful','failed','expired')),
            amount INT NOT NULL,
            credited_tries INT DEFAULT 0,
            flw_tx_id TEXT,
            tg_id BIGINT,
            username TEXT,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_flw_tx_id ON payments(flw_tx_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_tg_id ON payments(tg_id);")
        print("✅ payments ensured")

        # ----------------------
        # 6. Proofs
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
        print("✅ proofs ensured")

        # ----------------------
        # 7. Transaction Logs
        # ----------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS transaction_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            provider TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        print("✅ transaction_logs ensured")

        # ----------------------
        # 8. Game State
        # ----------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS game_state (
            id SERIAL PRIMARY KEY,
            current_cycle INT DEFAULT 1,
            paid_tries_this_cycle INT DEFAULT 0,
            lifetime_paid_tries INT DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cur.execute("INSERT INTO game_state (id) VALUES (1) ON CONFLICT DO NOTHING;")
        print("✅ game_state ensured")

        conn.commit()
        print("🎉 FULL migration completed successfully!")

    except Exception as e:
        conn.rollback()
        print("❌ Migration failed:", e)
        raise

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
