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

        # ✅ Add new winner fields if they don't exist
        cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='users' AND column_name='choice'
            ) THEN
                ALTER TABLE users ADD COLUMN choice TEXT;
                RAISE NOTICE '🆕 Added column choice to users';
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='users' AND column_name='full_name'
            ) THEN
                ALTER TABLE users ADD COLUMN full_name TEXT;
                RAISE NOTICE '🆕 Added column full_name to users';
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='users' AND column_name='phone'
            ) THEN
                ALTER TABLE users ADD COLUMN phone TEXT;
                RAISE NOTICE '🆕 Added column phone to users';
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='users' AND column_name='address'
            ) THEN
                ALTER TABLE users ADD COLUMN address TEXT;
                RAISE NOTICE '🆕 Added column address to users';
            END IF;
        END$$;
        """)
        print("✅ winner fields ensured (choice, full_name, phone, address)")

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

        # ✅ Migration fix: ensure flw_tx_id is TEXT
        cur.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='payments' AND column_name='flw_tx_id'
                  AND data_type IN ('integer', 'bigint')
            ) THEN
                ALTER TABLE payments ALTER COLUMN flw_tx_id TYPE VARCHAR USING flw_tx_id::varchar;
                RAISE NOTICE '🔄 Converted flw_tx_id to VARCHAR';
            END IF;
        END$$;
        """)

        # ✅ Rename 'tries' → 'credited_tries' if needed
        cur.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='payments' AND column_name='tries'
            ) THEN
                ALTER TABLE payments RENAME COLUMN tries TO credited_tries;
            END IF;
        END$$;
        """)
        print("✅ payments table ensured (with VARCHAR flw_tx_id)")

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
            lifetime_paid_tries INT DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        print("✅ game_state table ensured (with lifetime_paid_tries)")

        # ✅ Add lifetime_paid_tries column if missing
        cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='game_state' AND column_name='lifetime_paid_tries'
            ) THEN
                ALTER TABLE game_state ADD COLUMN lifetime_paid_tries INT DEFAULT 0;
                RAISE NOTICE '🆕 Added column lifetime_paid_tries to game_state';
            END IF;
        END$$;
        """)

        # ✅ Ensure at least one GameState row exists
        cur.execute("INSERT INTO game_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;")
        print("✅ ensured default game_state row (id=1)")

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
