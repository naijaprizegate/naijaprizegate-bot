# ==============================================================
# migrations/init_tables.py (CLEAN VERSION - removes last_updated_by)
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
        # ======================================================
        # Ensure schema version tracking table
        # ======================================================
        cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            meta JSONB DEFAULT '{}'::jsonb
        );
        """)
        print("✅ schema_migrations table ensured")

        MIGRATION_NAME = "init_tables_v3"

        cur.execute("SELECT 1 FROM schema_migrations WHERE name=%s", (MIGRATION_NAME,))
        if cur.fetchone():
            print(f"⚠️ Migration {MIGRATION_NAME} already applied — skipping ✅")
            conn.close()
            return

        print(f"🔧 Starting migration: {MIGRATION_NAME}")

        cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
        print("✅ pgcrypto ensured")

        # ======================================================
        # 1. Users
        # ======================================================
        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
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
        print("✅ users ensured")

        # ======================================================
        # 2. Prize Winners — DROP + CREATE CLEAN TABLE ✅
        # ======================================================
        cur.execute("DROP TABLE IF EXISTS prize_winners CASCADE;")
        print("🗑️ Dropped old prize_winners (if existed)")

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
        cur.execute("CREATE INDEX IF NOT EXISTS idx_prize_winners_tg_id ON prize_winners(tg_id);")
        print("✅ prize_winners recreated cleanly ✅")

        # ======================================================
        # 3. Global Counter
        # ======================================================
        cur.execute("""
        CREATE TABLE IF NOT EXISTS global_counter (
            id SERIAL PRIMARY KEY,
            paid_tries_total INT DEFAULT 0
        );
        """)
        print("✅ global_counter ensured")

        # ======================================================
        # 4. Plays
        # ======================================================
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

        # ======================================================
        # 5. Payments
        # ======================================================
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
        print("✅ payments ensured")

        # ======================================================
        # 6. Proofs
        # ======================================================
        cur.execute("""
        CREATE TABLE IF NOT EXISTS proofs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            file_id TEXT NOT NULL,
            status TEXT DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        print("✅ proofs ensured")

        # ======================================================
        # 7. Transaction Logs
        # ======================================================
        cur.execute("""
        CREATE TABLE IF NOT EXISTS transaction_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            provider TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        print("✅ transaction_logs ensured")

        # ======================================================
        # 8. Game State
        # ======================================================
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

        cur.execute(
            "INSERT INTO schema_migrations (name, meta) VALUES (%s, %s::jsonb)",
            (MIGRATION_NAME, json.dumps({
                "applied_by": "manual_update",
                "applied_at": datetime.now(timezone.utc).isoformat()
            }))
        )

        conn.commit()
        print("🎉 Migration completed SUCCESSFULLY ✅")

    except Exception as e:
        conn.rollback()
        print("❌ Migration failed — rolled back ❌ Error:", e)
        raise

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
