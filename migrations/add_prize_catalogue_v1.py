"""
Create the prize_catalogue table.

This table stores public prize listings and is independent of
prize_winners and all payment tables.

The migration follows the project's schema_migrations convention.
"""

import json
import os
from datetime import datetime, timezone

import psycopg2


MIGRATION_NAME = "add_prize_catalogue_v1"


def main():
    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        print("ERROR: DATABASE_URL not found in environment.")
        return

    # psycopg2 requires a synchronous PostgreSQL URL.
    if database_url.startswith("postgresql+asyncpg://"):
        database_url = database_url.replace(
            "postgresql+asyncpg://",
            "postgresql://",
            1,
        )

    conn = None
    cur = None

    try:
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()

        # Ensure the migration tracking table exists.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                meta JSONB DEFAULT '{}'::jsonb
            );
            """
        )

        # Stop if this migration has already been recorded.
        cur.execute(
            """
            SELECT 1
            FROM schema_migrations
            WHERE name = %s
            LIMIT 1;
            """,
            (MIGRATION_NAME,),
        )

        if cur.fetchone():
            print(f"Migration already applied: {MIGRATION_NAME}")
            conn.commit()
            return

        print(f"Starting migration: {MIGRATION_NAME}")

        # Create a separate catalogue table.
        # This does not alter prize_winners or payment tables.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS prize_catalogue (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                conditions TEXT NOT NULL DEFAULT '',
                photo_file_id TEXT,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

        # Index supports listing and filtering catalogue entries.
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS
                idx_prize_catalogue_active_created
            ON prize_catalogue (is_active, created_at, id);
            """
        )

        # Record successful migration within the same transaction.
        cur.execute(
            """
            INSERT INTO schema_migrations (name, meta)
            VALUES (%s, %s::jsonb);
            """,
            (
                MIGRATION_NAME,
                json.dumps(
                    {
                        "applied_by": "render_migration_script",
                        "applied_at": datetime.now(
                            timezone.utc
                        ).isoformat(),
                        "notes": (
                            "Created the independent public "
                            "prize catalogue table."
                        ),
                    }
                ),
            ),
        )

        conn.commit()

        print("Migration applied successfully.")
        print("Created or verified table: prize_catalogue")
        print(
            "Created or verified index: "
            "idx_prize_catalogue_active_created"
        )

    except Exception as exc:
        if conn is not None:
            conn.rollback()

        print("Migration failed; transaction rolled back.")
        print("Error:", exc)
        raise

    finally:
        if cur is not None:
            cur.close()

        if conn is not None:
            conn.close()


if __name__ == "__main__":
    main()