# ===============================================================
# migrations/add_support_tickets_v1.py
# Adds support_tickets table (idempotent)
# ===============================================================
import os
import json
from datetime import datetime, timezone
import psycopg2

MIGRATION_NAME = "add_support_tickets_v1"

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
        # 0) schema_migrations table
        cur.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            meta JSONB DEFAULT '{}'::jsonb
        );
        """)

        # Stop if already applied
        cur.execute("SELECT 1 FROM schema_migrations WHERE name=%s LIMIT 1;", (MIGRATION_NAME,))
        if cur.fetchone():
            print(f"✅ Migration already applied: {MIGRATION_NAME}")
            return

        print(f"🔧 Starting migration: {MIGRATION_NAME}")

        # 1) Create support_tickets
        cur.execute("""
        CREATE TABLE IF NOT EXISTS support_tickets (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tg_id BIGINT NOT NULL,
            username TEXT,
            first_name TEXT,
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """)

        # Helpful indexes
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_support_tickets_tg_id
        ON support_tickets (tg_id);
        """)
        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_support_tickets_status_created
        ON support_tickets (status, created_at DESC);
        """)

        # 2) Record migration
        cur.execute(
            "INSERT INTO schema_migrations (name, meta) VALUES (%s, %s::jsonb)",
            (MIGRATION_NAME, json.dumps({
                "applied_by": "render_migration_script",
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "notes": "Added support_tickets table for Contact Support flow"
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
