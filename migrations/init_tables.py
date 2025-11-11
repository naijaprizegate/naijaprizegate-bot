# ==============================================================  
# migrations/reset_db.py (FULL RESET VERSION)  
# ==============================================================  
import os
import json
from datetime import datetime, timezone
import psycopg2

def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not found in env")
        return

    if database_url.startswith("postgresql+asyncpg://"):
        database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    conn = psycopg2.connect(database_url)
    cur = conn.cursor()

    try:
        print("🗑️ Dropping all existing tables...")
        cur.execute("""
        DO $$
        DECLARE r RECORD;
        BEGIN
            FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP
                EXECUTE 'DROP TABLE IF EXISTS ' || quote_ident(r.tablename) || ' CASCADE';
            END LOOP;
        END $$;
        """)
        print("✅ All tables dropped")

        # ======================================================
        # Ensure schema version tracking table
        # ======================================================
        cur.execute("""
        CREATE TABLE schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            meta JSONB DEFAULT '{}'::jsonb
        );
        """)
        print("✅ schema_migrations table created")

        MIGRATION_NAME = "reset_all_tables"

        print(f"🔧 Starting migration: {MIGRATION_NAME}")

        cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
        print("✅ pgcrypto ensured")

        # ======================================================
        # 1. Users
        # ======================================================
        cur.execute("""
        CREATE TABLE users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tg_id BIGINT NOT NULL UNIQUE,
            username TEXT,
            tries_paid INT DEFAULT 0,
            tries_bonus INT DEFAULT 0,
            is_admin BOOLEAN DEFAULT FALSE NOT NULL,
            referred_by UUID REFERENCES users(id) ON DELETE SET NULL,
            choice TEXT,
            phone TEXT,
            address TEXT,
            delivery_status TEXT DEFAULT 'Pending',
            winner_stage TEXT,
            winner_data JSONB DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        print("✅ users table created")

        # ======================================================
        # 2. Prize Winners
        # ======================================================
        cur.execute("""
        CREATE TABLE prize_winners (
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
            delivery_data JSONB DEFAULT '{}'::jsonb
        );
        """)
        cur.execute("CREATE INDEX idx_prize_winners_tg_id ON prize_winners(tg_id);")
        print("✅ prize_winners table created")

        # ======================================================
        # 3. Global Counter
        # ======================================================
        cur.execute("""
        CREATE TABLE global_counter (
            id SERIAL PRIMARY KEY,
            paid_tries_total INT DEFAULT 0
        );
        """)
        print("✅ global_counter table created")

        # ======================================================
        # 4. Plays
        # ======================================================
        cur.execute("""
        CREATE TABLE plays (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            result TEXT NOT NULL CHECK (result IN ('win','lose','pending')),
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cur.execute("CREATE INDEX idx_plays_user_id ON plays(user_id);")
        print("✅ plays table created")

        # ======================================================
        # 5. Payments
        # ======================================================
        cur.execute("""
        CREATE TABLE payments (
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
        print("✅ payments table created")

        # ======================================================
        # 6. Proofs
        # ======================================================
        cur.execute("""
        CREATE TABLE proofs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            file_id TEXT NOT NULL,
            status TEXT DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        print("✅ proofs table created")

        # ======================================================
        # 7. Transaction Logs
        # ======================================================
        cur.execute("""
        CREATE TABLE transaction_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            provider TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        print("✅ transaction_logs table created")

        # ======================================================
        # 8. Game State
        # ======================================================
        cur.execute("""
        CREATE TABLE game_state (
            id SERIAL PRIMARY KEY,
            current_cycle INT DEFAULT 1,
            paid_tries_this_cycle INT DEFAULT 0,
            lifetime_paid_tries INT DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cur.execute("INSERT INTO game_state (id) VALUES (1) ON CONFLICT DO NOTHING;")
        print("✅ game_state table created")

        # ======================================================
        # Mark migration as applied
        # ======================================================
        cur.execute(
            "INSERT INTO schema_migrations (name, meta) VALUES (%s, %s::jsonb)",
            (MIGRATION_NAME, json.dumps({
                "applied_by": "manual_reset_script",
                "applied_at": datetime.now(timezone.utc).isoformat()
            }))
        )

        conn.commit()
        print("🎉 Database fully reset and migration applied successfully ✅")

    except Exception as e:
        conn.rollback()
        print("❌ Migration failed — rolled back ❌ Error:", e)
        raise

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
