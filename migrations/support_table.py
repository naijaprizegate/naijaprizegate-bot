# support_table.py
# One-time migration for support_tickets table

import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
import os

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL not found in environment variables")

engine = create_async_engine(DATABASE_URL, echo=True)


async def column_exists(conn, column_name: str) -> bool:
    result = await conn.execute(text("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'support_tickets'
        AND column_name = :col
    """), {"col": column_name})
    return result.scalar() is not None


async def run_migration():
    async with engine.begin() as conn:
        print("🔍 Checking support_tickets table...")

        # ---- admin_reply column ----
        if not await column_exists(conn, "admin_reply"):
            print("➕ Adding column: admin_reply")
            await conn.execute(text("""
                ALTER TABLE support_tickets
                ADD COLUMN admin_reply TEXT;
            """))
        else:
            print("✔ admin_reply already exists")

        # ---- replied_at column ----
        if not await column_exists(conn, "replied_at"):
            print("➕ Adding column: replied_at")
            await conn.execute(text("""
                ALTER TABLE support_tickets
                ADD COLUMN replied_at TIMESTAMPTZ;
            """))
        else:
            print("✔ replied_at already exists")

        # Optional but useful
        if not await column_exists(conn, "updated_at"):
            print("➕ Adding column: updated_at")
            await conn.execute(text("""
                ALTER TABLE support_tickets
                ADD COLUMN updated_at TIMESTAMPTZ DEFAULT NOW();
            """))
        else:
            print("✔ updated_at already exists")

    await engine.dispose()
    print("🎉 Migration completed successfully!")


if __name__ == "__main__":
    asyncio.run(run_migration())
