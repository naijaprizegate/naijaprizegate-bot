# ===============================================================
# migrations/init_tables.py  (SAFE MIGRATION: adds cycle system)
# - DOES NOT DROP TABLES
# - Can be re-run safely (idempotent)
# ===============================================================
import os
import json
from datetime import datetime, timezone
import psycopg2

MIGRATION_NAME = "add_cycle_system_v1"

def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not found in env")
        return

    # psycopg2 needs sync URL
    if database_url.startswith("postgresql+asyncpg://"):
        database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    conn = psycopg2.connect(database_url)
    cur = conn.cursor()

    try:
        # -------------------------------------------------------
        # 0) schema_migrations table (ensure exists)
        # -------------------------------------------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            meta JSONB DEFAULT '{}'::jsonb
        );
        """)

        # If migration already applied, stop
        cur.execute("SELECT 1 FROM schema_migrations WHERE name = %s LIMIT 1;", (MIGRATION_NAME,))
        if cur.fetchone():
            print(f"✅ Migration already applied: {MIGRATION_NAME}")
            return

        print(f"🔧 Starting migration: {MIGRATION_NAME}")

        # -------------------------------------------------------
        # 1) Ensure pgcrypto (gen_random_uuid)
        # -------------------------------------------------------
        cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
        print("✅ pgcrypto ensured")

        # -------------------------------------------------------
        # 2) cycles table
        # -------------------------------------------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS cycles (
            id                  INTEGER PRIMARY KEY,
            started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            ended_at            TIMESTAMPTZ,
            paid_tries_target   INTEGER NOT NULL DEFAULT 50000,
            paid_tries_final    INTEGER NOT NULL DEFAULT 0,
            winner_user_id      UUID,
            winner_tg_id        BIGINT,
            winner_points       INTEGER,
            winner_decided_at   TIMESTAMPTZ
        );
        """)
        print("✅ cycles table ensured")

        # Create cycle 1 if missing
        cur.execute("""
        INSERT INTO cycles (id)
        VALUES (1)
        ON CONFLICT (id) DO NOTHING;
        """)
        print("✅ cycles row (id=1) ensured")

        # -------------------------------------------------------
        # 3) user_cycle_stats table (points per cycle)
        # -------------------------------------------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS user_cycle_stats (
            cycle_id    INTEGER NOT NULL,
            user_id     UUID NOT NULL,
            tg_id       BIGINT NOT NULL,
            points      INTEGER NOT NULL DEFAULT 0,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (cycle_id, user_id)
        );
        """)
        print("✅ user_cycle_stats table ensured")

        # Helpful indexes
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_cycle_stats_cycle_points
        ON user_cycle_stats (cycle_id, points DESC);
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_cycle_stats_cycle_tg
        ON user_cycle_stats (cycle_id, tg_id);
        """)
        print("✅ user_cycle_stats indexes ensured")

        # -------------------------------------------------------
        # 4) Add cycle_id columns to existing reward tables
        # -------------------------------------------------------
        cur.execute("ALTER TABLE premium_reward_entries ADD COLUMN IF NOT EXISTS cycle_id INTEGER;")
        cur.execute("ALTER TABLE airtime_payouts ADD COLUMN IF NOT EXISTS cycle_id INTEGER;")
        cur.execute("ALTER TABLE non_airtime_winners ADD COLUMN IF NOT EXISTS cycle_id INTEGER;")
        print("✅ cycle_id columns ensured on premium_reward_entries / airtime_payouts / non_airtime_winners")

        # Index for tie-break (first to reach top score)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_premium_entries_cycle_user_time
        ON premium_reward_entries (cycle_id, user_id, created_at);
        """)
        print("✅ premium_reward_entries tie-break index ensured")

        # Per-cycle uniqueness: same user can win earpod/speaker again in a NEW cycle
        cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_non_airtime_winner_cycle
        ON non_airtime_winners (cycle_id, user_id, reward_type);
        """)
        print("✅ non_airtime_winners per-cycle uniqueness ensured")

        # -------------------------------------------------------
        # 5) Backfill cycle_id for existing rows (optional but helpful)
        # -------------------------------------------------------
        # If you already have data, set cycle_id to current_cycle for old rows that are NULL.
        # We use game_state.id=1 if it exists; otherwise default to cycle 1.
        cur.execute("SELECT current_cycle FROM game_state WHERE id = 1;")
        row = cur.fetchone()
        current_cycle = int(row[0]) if row and row[0] else 1

        cur.execute("UPDATE premium_reward_entries SET cycle_id = %s WHERE cycle_id IS NULL;", (current_cycle,))
        cur.execute("UPDATE airtime_payouts SET cycle_id = %s WHERE cycle_id IS NULL;", (current_cycle,))
        cur.execute("UPDATE non_airtime_winners SET cycle_id = %s WHERE cycle_id IS NULL;", (current_cycle,))
        print(f"✅ Backfilled NULL cycle_id values to cycle {current_cycle}")

        # -------------------------------------------------------
        # 6) Record migration
        # -------------------------------------------------------
        cur.execute(
            "INSERT INTO schema_migrations (name, meta) VALUES (%s, %s::jsonb)",
            (MIGRATION_NAME, json.dumps({
                "applied_by": "render_migration_script",
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "notes": "Added cycles + user_cycle_stats + cycle_id columns for cycle-based rewards"
            }))
        )

        conn.commit()
        print("🎉 Migration applied successfully!")

    except Exception as e:
        conn.rollback()
        print("❌ Migration failed — rolled back")
        print("Error:", e)
        raise

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
