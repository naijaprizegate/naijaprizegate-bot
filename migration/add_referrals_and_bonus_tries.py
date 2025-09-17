# migrations/add_referrals_and_bonus_tries.py
"""
Migration: Add bonus_tries column to users and create referrals table.
Idempotent: safe to run multiple times.
"""

from sqlalchemy import create_engine, text
import os

# Use DATABASE_URL from environment (Render or local)
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set!")

engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    # 1️⃣ Add bonus_tries column to users table if it doesn't exist
    conn.execute(text("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS bonus_tries INT DEFAULT 0;
    """))

    # 2️⃣ Create referrals table if it doesn't exist
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS referrals (
            id SERIAL PRIMARY KEY,
            referrer_id BIGINT NOT NULL,
            new_user_id BIGINT NOT NULL,
            ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_referral_once UNIQUE (referrer_id, new_user_id)
        );
    """))

    conn.commit()

print("✅ Migration applied: bonus_tries column + referrals table")
