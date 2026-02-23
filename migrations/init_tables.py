# =================================================================
# migrations/init_tables.py  (SAFE MIGRATION: adds cycle system)
# Fixes duplicate non_airtime_winners before creating unique index
# =================================================================
import os
import json
from datetime import datetime, timezone
import psycopg2
from urllib.parse import urlparse

MIGRATION_NAME = "add_cycle_system_v1"

def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not found in env")
        return

    # psycopg2 needs sync URL
    if database_url.startswith("postgresql+asyncpg://"):
        database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    # =============================================================
    # 🔎 DEBUG — SHOW WHAT RENDER IS ACTUALLY USING
    # =============================================================
    parsed = urlparse(database_url)
    print("========================================")
    print("🔎 DATABASE DEBUG INFO")
    print("DB USER:", parsed.username)
    print("DB HOST:", parsed.hostname)
    print("DB PORT:", parsed.port)
    print("DB NAME:", (parsed.path or "").lstrip("/"))
    print("========================================")

    # =============================================================
    # ✅ IMPORTANT: psycopg2 does NOT accept "pgbouncer=true" param.
    # Force SSL via connect kwargs (works reliably with Supabase).
    # =============================================================
    conn = psycopg2.connect(database_url, sslmode="require")
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
        cur.execute("""
        INSERT INTO cycles (id)
        VALUES (1)
        ON CONFLICT (id) DO NOTHING;
        """)
        print("✅ cycles ensured")

        # -------------------------------------------------------
        # 3) user_cycle_stats table
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
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_cycle_stats_cycle_points
        ON user_cycle_stats (cycle_id, points DESC);
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_cycle_stats_cycle_tg
        ON user_cycle_stats (cycle_id, tg_id);
        """)
        print("✅ user_cycle_stats ensured")

        # -------------------------------------------------------
        # 4) Add cycle_id columns (idempotent)
        # -------------------------------------------------------
        cur.execute("ALTER TABLE premium_reward_entries ADD COLUMN IF NOT EXISTS cycle_id INTEGER;")
        cur.execute("ALTER TABLE airtime_payouts ADD COLUMN IF NOT EXISTS cycle_id INTEGER;")
        cur.execute("ALTER TABLE non_airtime_winners ADD COLUMN IF NOT EXISTS cycle_id INTEGER;")
        print("✅ cycle_id columns ensured")

        # Helpful index for tie-break
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_premium_entries_cycle_user_time
        ON premium_reward_entries (cycle_id, user_id, created_at);
        """)
        print("✅ premium_reward_entries index ensured")

        # -------------------------------------------------------
        # 5) Determine current cycle
        # -------------------------------------------------------
        cur.execute("SELECT current_cycle FROM game_state WHERE id = 1;")
        row = cur.fetchone()
        current_cycle = int(row[0]) if row and row[0] else 1
        print(f"ℹ️ Using current_cycle={current_cycle} for backfill")

        # -------------------------------------------------------
        # 6) CLEANUP duplicates in non_airtime_winners BEFORE backfill
        # -------------------------------------------------------
        print("🧹 Cleaning duplicates in non_airtime_winners (same user_id + reward_type)...")
        cur.execute("""
        DELETE FROM non_airtime_winners a
        USING non_airtime_winners b
        WHERE a.user_id = b.user_id
          AND a.reward_type = b.reward_type
          AND a.id <> b.id
          AND COALESCE(a.created_at, NOW()) > COALESCE(b.created_at, NOW());
        """)
        print("✅ Duplicate cleanup done")

        # -------------------------------------------------------
        # 7) Backfill cycle_id safely
        # -------------------------------------------------------
        cur.execute("UPDATE premium_reward_entries SET cycle_id = %s WHERE cycle_id IS NULL;", (current_cycle,))
        cur.execute("UPDATE airtime_payouts SET cycle_id = %s WHERE cycle_id IS NULL;", (current_cycle,))
        cur.execute("UPDATE non_airtime_winners SET cycle_id = %s WHERE cycle_id IS NULL;", (current_cycle,))
        print("✅ Backfilled NULL cycle_id values")

        # -------------------------------------------------------
        # 8) Create per-cycle uniqueness AFTER data is clean
        # -------------------------------------------------------
        cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_non_airtime_winner_cycle
        ON non_airtime_winners (cycle_id, user_id, reward_type);
        """)
        print("✅ non_airtime_winners per-cycle uniqueness ensured")

        # -------------------------------------------------------
        # 9) Record migration
        # -------------------------------------------------------
        cur.execute(
            "INSERT INTO schema_migrations (name, meta) VALUES (%s, %s::jsonb)",
            (MIGRATION_NAME, json.dumps({
                "applied_by": "render_migration_script",
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "notes": "Added cycles + user_cycle_stats + cycle_id columns; deduped non_airtime_winners before unique index"
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
