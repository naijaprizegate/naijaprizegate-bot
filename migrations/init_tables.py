# ==============================================================
# migrations/init_tables.py
# ==============================================================
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

MIGRATION_NAME = "init_tables_from_models_py_v1"

def run_sql(cur, sql, params=None):
    cur.execute(sql, params)

def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not found in env")
        return

    if database_url.startswith("postgresql+asyncpg://"):
        database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    conn = psycopg2.connect(database_url)
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=RealDictCursor)

    try:
        # 0) create migration tracking table
        run_sql(cur, """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            meta JSONB
        );
        """)
        conn.commit()
        print("✅ schema_migrations table ensured")

        # if migration already applied -> exit
        run_sql(cur, "SELECT 1 FROM schema_migrations WHERE name = %s LIMIT 1;", (MIGRATION_NAME,))
        if cur.fetchone():
            print(f"ℹ️ Migration '{MIGRATION_NAME}' already applied. Exiting.")
            return

        print("🔧 Starting migration:", MIGRATION_NAME)

        # enable uuid extension
        run_sql(cur, "CREATE EXTENSION IF NOT EXISTS pgcrypto;")
        print("✅ pgcrypto extension ensured")

        # ----------------------
        # 1) users table (matches models.py)
        # ----------------------
        run_sql(cur, """
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
        run_sql(cur, "CREATE INDEX IF NOT EXISTS idx_users_tg_id ON users(tg_id);")
        print("✅ users table ensured")

        # add optional winner fields on users if missing (non-destructive)
        run_sql(cur, """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='choice') THEN
                ALTER TABLE users ADD COLUMN choice TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='full_name') THEN
                ALTER TABLE users ADD COLUMN full_name TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='phone') THEN
                ALTER TABLE users ADD COLUMN phone TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='address') THEN
                ALTER TABLE users ADD COLUMN address TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='delivery_status') THEN
                ALTER TABLE users ADD COLUMN delivery_status TEXT DEFAULT 'Pending';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='winner_stage') THEN
                ALTER TABLE users ADD COLUMN winner_stage TEXT;
            END IF;
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='users' AND column_name='winner_data') THEN
                ALTER TABLE users ADD COLUMN winner_data JSON DEFAULT '{}'::json;
            END IF;
        END$$;
        """)
        print("✅ ensured optional winner fields on users (if missing)")

        # ----------------------
        # 2) prize_winners table (as per your models.py)
        # ----------------------
        # If the table does not exist, create it matching your models.py
        run_sql(cur, "SELECT to_regclass('public.prize_winners') AS exists;")
        exists = cur.fetchone()["exists"]
        if not exists:
            run_sql(cur, """
            CREATE TABLE prize_winners (
                id SERIAL PRIMARY KEY,
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                tg_id BIGINT NOT NULL,
                choice TEXT NOT NULL,
                delivery_status TEXT,
                submitted_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP NOT NULL,
                pending_at TIMESTAMPTZ,
                in_transit_at TIMESTAMPTZ,
                delivered_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                delivery_data JSONB DEFAULT '{}'::jsonb,
                last_updated_by INT
            );
            """)
            run_sql(cur, "CREATE INDEX IF NOT EXISTS idx_prize_winners_tg_id ON prize_winners(tg_id);")
            print("✅ prize_winners created (fresh, matches models.py)")
        else:
            print("⚠ prize_winners already exists — performing safe, non-destructive sync to match models.py")

            # Add any missing columns present in models.py
            run_sql(cur, """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='prize_winners' AND column_name='user_id') THEN
                    ALTER TABLE prize_winners ADD COLUMN user_id UUID;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='prize_winners' AND column_name='tg_id') THEN
                    ALTER TABLE prize_winners ADD COLUMN tg_id BIGINT;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='prize_winners' AND column_name='choice') THEN
                    ALTER TABLE prize_winners ADD COLUMN choice TEXT;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='prize_winners' AND column_name='delivery_status') THEN
                    ALTER TABLE prize_winners ADD COLUMN delivery_status TEXT;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='prize_winners' AND column_name='submitted_at') THEN
                    ALTER TABLE prize_winners ADD COLUMN submitted_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='prize_winners' AND column_name='pending_at') THEN
                    ALTER TABLE prize_winners ADD COLUMN pending_at TIMESTAMPTZ;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='prize_winners' AND column_name='in_transit_at') THEN
                    ALTER TABLE prize_winners ADD COLUMN in_transit_at TIMESTAMPTZ;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='prize_winners' AND column_name='delivered_at') THEN
                    ALTER TABLE prize_winners ADD COLUMN delivered_at TIMESTAMPTZ;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='prize_winners' AND column_name='updated_at') THEN
                    ALTER TABLE prize_winners ADD COLUMN updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='prize_winners' AND column_name='delivery_data') THEN
                    ALTER TABLE prize_winners ADD COLUMN delivery_data JSONB DEFAULT '{}'::jsonb;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='prize_winners' AND column_name='last_updated_by') THEN
                    ALTER TABLE prize_winners ADD COLUMN last_updated_by INT;
                END IF;
            END$$;
            """)
            print("✅ ensured prize_winners columns exist (added any missing columns)")

            # Attempt to populate user_id by matching on tg_id if present
            # This helps migrate older tables where user_id was integer or missing.
            try:
                run_sql(cur, """
                UPDATE prize_winners pw
                SET user_id = u.id
                FROM users u
                WHERE pw.user_id IS NULL
                  AND pw.tg_id IS NOT NULL
                  AND u.tg_id = pw.tg_id;
                """)
                run_sql(cur, "SELECT count(*) AS mapped FROM prize_winners WHERE user_id IS NOT NULL;")
                mapped = cur.fetchone()["mapped"]
                print(f"ℹ️ After attempt, prize_winners rows with user_id populated: {mapped}")
            except Exception as e:
                print("⚠ Could not auto-populate user_id from users.tg_id automatically:", e)

            # Create index on tg_id if missing
            run_sql(cur, "CREATE INDEX IF NOT EXISTS idx_prize_winners_tg_id ON prize_winners(tg_id);")
            print("✅ ensured index on prize_winners.tg_id (if possible)")

            # Provide guidance if there are mismatched types or NULL user_id
            run_sql(cur, "SELECT count(*) AS null_userid FROM prize_winners WHERE user_id IS NULL;")
            null_userid = cur.fetchone()["null_userid"]
            if null_userid:
                print(f"⚠ There are {null_userid} prize_winners rows with NULL user_id. If these should link to users, run manual mapping or fix tg_id values.")
                run_sql(cur, "SELECT id, tg_id, user_id FROM prize_winners WHERE user_id IS NULL LIMIT 5;")
                for row in cur.fetchall():
                    print("  sample row needing attention:", row)

        # ----------------------
        # 3) global_counter
        # ----------------------
        run_sql(cur, """
        CREATE TABLE IF NOT EXISTS global_counter (
            id SERIAL PRIMARY KEY,
            paid_tries_total INT DEFAULT 0
        );
        """)
        print("✅ global_counter ensured")

        # ----------------------
        # 4) plays
        # ----------------------
        run_sql(cur, """
        CREATE TABLE IF NOT EXISTS plays (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            result TEXT NOT NULL CHECK (result IN ('win','lose','pending')),
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        run_sql(cur, "CREATE INDEX IF NOT EXISTS idx_plays_user_id ON plays(user_id);")
        print("✅ plays ensured")

        # ----------------------
        # 5) payments
        # ----------------------
        run_sql(cur, """
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
        run_sql(cur, "CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id);")
        run_sql(cur, "CREATE INDEX IF NOT EXISTS idx_payments_flw_tx_id ON payments(flw_tx_id);")
        run_sql(cur, "CREATE INDEX IF NOT EXISTS idx_payments_tg_id ON payments(tg_id);")
        print("✅ payments ensured")

        # ----------------------
        # 6) proofs
        # ----------------------
        run_sql(cur, """
        CREATE TABLE IF NOT EXISTS proofs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            file_id TEXT NOT NULL,
            status TEXT DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        run_sql(cur, "CREATE INDEX IF NOT EXISTS idx_proofs_user_id ON proofs(user_id);")
        print("✅ proofs ensured")

        # ----------------------
        # 7) transaction_logs
        # ----------------------
        run_sql(cur, """
        CREATE TABLE IF NOT EXISTS transaction_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            provider TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        print("✅ transaction_logs ensured")

        # ----------------------
        # 8) game_state
        # ----------------------
        run_sql(cur, """
        CREATE TABLE IF NOT EXISTS game_state (
            id SERIAL PRIMARY KEY,
            current_cycle INT DEFAULT 1,
            paid_tries_this_cycle INT DEFAULT 0,
            lifetime_paid_tries INT DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        run_sql(cur, "INSERT INTO game_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;")
        print("✅ game_state ensured and seeded")

        # All good -> commit, record migration
        conn.commit()
        run_sql(cur, "INSERT INTO schema_migrations (name, applied_at, meta) VALUES (%s, CURRENT_TIMESTAMP, %s);",
                (MIGRATION_NAME, {"applied_by": os.getenv("USER", "migration_script"), "applied_at": str(datetime.utcnow())}))
        conn.commit()
        print(f"🎉 Migration '{MIGRATION_NAME}' applied and recorded in schema_migrations.")

    except Exception as exc:
        conn.rollback()
        print("❌ Migration failed — rolled back. Error:", exc)
        raise

    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    main()
