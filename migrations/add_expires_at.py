#!/usr/bin/env python3
"""
One-time migration: add expires_at column to payments table.
Safe: uses existing DATABASE_URL environment variable.
"""

import os
import sys
from sqlalchemy import create_engine, text

def main():
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("RENDER_DATABASE_URL")
    if not db_url:
        print("ERROR: No database URL found in environment. Please set DATABASE_URL in your Render service.")
        return 1

    alter_sql = "ALTER TABLE IF EXISTS payments ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP;"

    try:
        engine = create_engine(db_url)
        with engine.begin() as conn:
            conn.execute(text(alter_sql))
        print("SUCCESS: 'expires_at' column added (or already existed).")
    except Exception as e:
        print("ERROR: Failed to run ALTER TABLE:", e)
        return 1

    return 0

if __name__ == '__main__':
    sys.exit(main())
