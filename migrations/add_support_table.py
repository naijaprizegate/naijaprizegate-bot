# migrations/add_support_table.py
# One-time migration for support_tickets table (SYNC engine)
# Works with DATABASE_URL like: postgresql://... (psycopg2)

import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL not found in environment variables")

# Render often provides postgres://... ; SQLAlchemy prefers postgresql://...
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL, echo=True, pool_pre_ping=True)


def column_exists(conn, column_name: str) -> bool:
    res = conn.execute(
        text("""
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'support_tickets'
              AND column_name = :col
            LIMIT 1
        """),
        {"col": column_name},
    )
    return res.scalar() is not None


def run_migration():
    with engine.begin() as conn:
        print("🔍 Checking support_tickets table...")

        if not column_exists(conn, "admin_reply"):
            print("➕ Adding column: admin_reply")
            conn.execute(text("ALTER TABLE support_tickets ADD COLUMN admin_reply TEXT;"))
        else:
            print("✔ admin_reply already exists")

        if not column_exists(conn, "replied_at"):
            print("➕ Adding column: replied_at")
            conn.execute(text("ALTER TABLE support_tickets ADD COLUMN replied_at TIMESTAMPTZ;"))
        else:
            print("✔ replied_at already exists")

        # Optional but useful
        if not column_exists(conn, "updated_at"):
            print("➕ Adding column: updated_at")
            conn.execute(text("ALTER TABLE support_tickets ADD COLUMN updated_at TIMESTAMPTZ DEFAULT NOW();"))
        else:
            print("✔ updated_at already exists")

    print("🎉 Migration completed successfully!")


if __name__ == "__main__":
    run_migration()
