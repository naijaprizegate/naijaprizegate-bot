# ==================================================================
# migrations/reset_db.py (FULL RESET with new reward + trivia tables)
# ===================================================================
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
        print("🗑️ Dropping all existing tables...")
        cur.execute("""
        DO $$
        DECLARE r RECORD;
        BEGIN
            FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP
                EXECUTE 'DROP TABLE IF EXISTS ' || quote_ident(r.tablename) || ' CASCADE';
            END LOOP;
        END $$;
        """)
        print("✅ All tables dropped")


        # ======================================================
        # schema_migrations table
        # ======================================================
        cur.execute("""
        CREATE TABLE schema_migrations (
            name TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            meta JSONB DEFAULT '{}'::jsonb
        );
        """)
        print("✅ schema_migrations table created")

        MIGRATION_NAME = "reset_all_tables"
        print(f"🔧 Starting migration: {MIGRATION_NAME}")

        cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
        print("✅ pgcrypto ensured")


        # ======================================================
        # 1. USERS
        # ======================================================
        cur.execute("""
        CREATE TABLE users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tg_id BIGINT NOT NULL UNIQUE,
            username TEXT,
            full_name TEXT,
            tries_paid INT DEFAULT 0,
            tries_bonus INT DEFAULT 0,
            premium_spins INT NOT NULL DEFAULT 0,
            total_premium_spins INT NOT NULL DEFAULT 0,
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
        print("✅ users table created")


        # ======================================================
        # 2. PRIZE WINNERS (existing Top-Tier Campaign Reward winners)
        # ======================================================
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
        cur.execute("CREATE INDEX idx_prize_winners_tg_id ON prize_winners(tg_id);")
        print("✅ prize_winners table created")


        # ======================================================
        # 3. GLOBAL COUNTER
        # ======================================================
        cur.execute("""
        CREATE TABLE global_counter (
            id SERIAL PRIMARY KEY,
            paid_tries_total INT DEFAULT 0
        );
        """)
        print("✅ global_counter table created")


        # ======================================================
        # 4. PLAYS
        # ======================================================
        cur.execute("""
        CREATE TABLE plays (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            result TEXT NOT NULL CHECK (result IN ('win','lose','pending')),
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cur.execute("CREATE INDEX idx_plays_user_id ON plays(user_id);")
        print("✅ plays table created")


        # ======================================================
        # 5. PAYMENTS
        # ======================================================
        cur.execute("""
        CREATE TABLE payments (
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
        print("✅ payments table created")


        # ======================================================
        # 6. PROOFS
        # ======================================================
        cur.execute("""
        CREATE TABLE proofs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            file_id TEXT NOT NULL,
            status TEXT DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        print("✅ proofs table created")


        # ======================================================
        # 7. TRANSACTION LOGS
        # ======================================================
        cur.execute("""
        CREATE TABLE transaction_logs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            provider TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        print("✅ transaction_logs table created")


        # ======================================================
        # 8. GAME STATE
        # ======================================================
        cur.execute("""
        CREATE TABLE game_state (
            id SERIAL PRIMARY KEY,
            current_cycle INT DEFAULT 1,
            paid_tries_this_cycle INT DEFAULT 0,
            lifetime_paid_tries INT DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        cur.execute("INSERT INTO game_state (id) VALUES (1) ON CONFLICT DO NOTHING;")
        print("✅ game_state table created")

        # ======================================================
        # 9. NEW — Trivia Questions
        # ======================================================
        cur.execute("""
        CREATE TABLE trivia_questions (
            id SERIAL PRIMARY KEY,
            category TEXT NOT NULL,
            question TEXT NOT NULL,
            options JSONB NOT NULL,
            answer TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        print("✅ trivia_questions table created")


        # ======================================================
        # 10. NEW — User Answers
        # ======================================================
        cur.execute("""
        CREATE TABLE user_answers (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            question_id INT REFERENCES trivia_questions(id) ON DELETE CASCADE,
            selected TEXT NOT NULL,
            correct BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        print("✅ user_answers table created")


        # ======================================================
        # 11. NEW — Spin Results
        # ======================================================
        cur.execute("""
        CREATE TABLE spin_results (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID REFERENCES users(id) ON DELETE CASCADE,
            tg_id BIGINT,
            spin_type TEXT NOT NULL,           -- basic / premium
            outcome TEXT NOT NULL,             -- lose / Top-Tier Campaign Reward / airtime / earpod / speaker
            extra_data JSONB DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        print("✅ spin_results table created")


        # ======================================================
        # 12. Airtime Payouts (Flutterwave Auto-Credit Live Mode)
        # ======================================================
        cur.execute("""
        CREATE TABLE airtime_payouts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            user_id UUID REFERENCES users(id) ON DELETE CASCADE,
            tg_id BIGINT NOT NULL,

            -- Phone number becomes available ONLY after user enters it
            phone_number TEXT,

            -- Airtime reward amount (system will always insert explicitly)
            amount INT NOT NULL,

            -- Unified status system
            -- pending_claim → waiting for phone
            -- claim_phone_set → phone saved, checkout generated
            -- failed → checkout/webhook failure
            -- completed → webhook success
            status TEXT NOT NULL DEFAULT 'pending_claim',

            -- Flutterwave fields
            flutterwave_tx_ref TEXT,
            provider_response JSONB,

            -- Retry logic (optional future upgrade)
            retry_count INT NOT NULL DEFAULT 0,
            last_retry_at TIMESTAMPTZ,

            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
            sent_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ
        );
        """)
        print("✅ airtime_payouts table created/updated with Flutterwave fields")


        # ======================================================
        # 13. NEW — Non-Airtime Winners (earpods/speakers)
        # ======================================================
        cur.execute("""
        CREATE TABLE non_airtime_winners (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID REFERENCES users(id) ON DELETE CASCADE,
            tg_id BIGINT NOT NULL,
            reward_type TEXT NOT NULL,       -- earpod / speaker
            notified_admin BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        );
        """)
        print("✅ non_airtime_winners table created")

        # =====================================================
        # 14. NEW - PREMIUM SPIN EENTRIES (for Top-Tier Campaign Reward weighted random selection)
        # =====================================================
        cur.execute("""
        CREATE TABLE premium_reward_entries (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE, 
            tg_id BIGINT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        """)
        print("✅ premium_reward_entries table created")
        
        # ======================================================
        # MIGRATION COMPLETED
        # ======================================================
        cur.execute(
            "INSERT INTO schema_migrations (name, meta) VALUES (%s, %s::jsonb)",
            (MIGRATION_NAME, json.dumps({
                "applied_by": "manual_reset_script",
                "applied_at": datetime.now(timezone.utc).isoformat()
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
