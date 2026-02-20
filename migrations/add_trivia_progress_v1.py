# ===============================================================
# migrations/add_trivia_progress_v1.py
# SAFE MIGRATION: adds trivia_progress + per-category user indexes
# (Idempotent: creates missing only, does not break existing)
# ===============================================================
import os
import json
from datetime import datetime, timezone
import psycopg2

MIGRATION_NAME = "add_trivia_progress_v1"

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
        # 1) Create trivia_progress table (if not exists)
        # -------------------------------------------------------
        cur.execute("""
        CREATE TABLE IF NOT EXISTS trivia_progress (
          tg_id BIGINT NOT NULL,
          category_key TEXT NOT NULL,
          next_index INTEGER NOT NULL DEFAULT 0,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          PRIMARY KEY (tg_id, category_key)
        );
        """)
        print("✅ trivia_progress ensured")

        # -------------------------------------------------------
        # 2) Add per-category progress columns to users (if missing)
        # NOTE: These are safe no-ops if column already exists.
        # -------------------------------------------------------
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS entertainment_q_index INTEGER DEFAULT 0;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS history_q_index INTEGER DEFAULT 0;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS football_q_index INTEGER DEFAULT 0;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS geography_q_index INTEGER DEFAULT 0;")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS english_q_index INTEGER DEFAULT 0;")

        # ✅ IMPORTANT: use correct spelling: sciences_q_index (not sciencs_q_index)
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS sciences_q_index INTEGER DEFAULT 0;")

        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS mathematics_q_index INTEGER DEFAULT 0;")
        print("✅ users.*_q_index columns ensured")

        # -------------------------------------------------------
        # 3) Record migration
        # -------------------------------------------------------
        cur.execute(
            "INSERT INTO schema_migrations (name, meta) VALUES (%s, %s::jsonb)",
            (MIGRATION_NAME, json.dumps({
                "applied_by": "render_migration_script",
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "notes": "Created trivia_progress table + ensured users category index columns exist"
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
