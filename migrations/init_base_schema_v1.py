# ===============================================================
# migrations/init_base_schema_v1.py
# Creates ALL core tables from models.py (idempotent)
# Safe to run on a fresh Supabase DB.
# ===============================================================
import os
import json
from datetime import datetime, timezone
import psycopg2

MIGRATION_NAME = "init_base_schema_v1"


def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not found in env")
        return

    # psycopg2 needs sync URL
    if database_url.startswith("postgresql+asyncpg://"):
        database_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    conn = psycopg2.connect(database_url, sslmode="require")
    cur = conn.cursor()

    try:
        # -------------------------------------------------------
        # 0) schema_migrations table
        # -------------------------------------------------------
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                meta JSONB DEFAULT '{}'::jsonb
            );
            """
        )

        # Stop if already applied
        cur.execute("SELECT 1 FROM schema_migrations WHERE name=%s LIMIT 1;", (MIGRATION_NAME,))
        if cur.fetchone():
            print(f"✅ Migration already applied: {MIGRATION_NAME}")
            return

        print(f"🔧 Starting migration: {MIGRATION_NAME}")

        # -------------------------------------------------------
        # 1) Extensions
        # -------------------------------------------------------
        cur.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")  # gen_random_uuid()
        print("✅ pgcrypto ensured")

        # -------------------------------------------------------
        # 2) Core tables
        # -------------------------------------------------------

        # USERS
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tg_id BIGINT NOT NULL UNIQUE,
                username TEXT,
                full_name TEXT,
                tries_paid INTEGER NOT NULL DEFAULT 0,
                tries_bonus INTEGER NOT NULL DEFAULT 0,
                premium_spins INTEGER NOT NULL DEFAULT 0,
                total_premium_spins INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW(),

                entertainment_q_index INTEGER DEFAULT 0,
                history_q_index INTEGER DEFAULT 0,
                football_q_index INTEGER DEFAULT 0,
                geography_q_index INTEGER DEFAULT 0,
                english_q_index INTEGER DEFAULT 0,
                sciences_q_index INTEGER DEFAULT 0,
                mathematics_q_index INTEGER DEFAULT 0
            );
            """
        )
        print("✅ users ensured")

        # TRIVIA PROGRESS
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trivia_progress (
                tg_id BIGINT NOT NULL,
                category_key TEXT NOT NULL,
                next_index INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (tg_id, category_key)
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_trivia_progress_category
            ON trivia_progress (category_key);
            """
        )
        print("✅ trivia_progress ensured")

        # GLOBAL COUNTER
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS global_counter (
                id INTEGER PRIMARY KEY,
                paid_tries_total INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        # Seed row
        cur.execute("INSERT INTO global_counter (id) VALUES (1) ON CONFLICT (id) DO NOTHING;")
        print("✅ global_counter ensured")

        # GAME STATE
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS game_state (
                id INTEGER PRIMARY KEY,
                current_cycle INTEGER NOT NULL DEFAULT 1,
                paid_tries_this_cycle INTEGER NOT NULL DEFAULT 0,
                lifetime_paid_tries INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        cur.execute("INSERT INTO game_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;")
        print("✅ game_state ensured")

        # CYCLES (needed for FKs)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cycles (
                id INTEGER PRIMARY KEY,
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ended_at TIMESTAMPTZ,
                paid_tries_target INTEGER NOT NULL DEFAULT 50000,
                paid_tries_final INTEGER NOT NULL DEFAULT 0,
                winner_user_id UUID,
                winner_tg_id BIGINT,
                winner_points INTEGER,
                winner_decided_at TIMESTAMPTZ
            );
            """
        )
        cur.execute("INSERT INTO cycles (id) VALUES (1) ON CONFLICT (id) DO NOTHING;")
        print("✅ cycles ensured")

        # USER CYCLE STATS
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_cycle_stats (
                cycle_id INTEGER NOT NULL REFERENCES cycles(id),
                user_id UUID NOT NULL REFERENCES users(id),
                tg_id BIGINT NOT NULL,
                points INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (cycle_id, user_id)
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_user_cycle_stats_cycle_points
            ON user_cycle_stats (cycle_id, points DESC);
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_user_cycle_stats_cycle_tg
            ON user_cycle_stats (cycle_id, tg_id);
            """
        )
        print("✅ user_cycle_stats ensured")

        # PLAYS
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS plays (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id),
                result TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_plays_user_time ON plays (user_id, created_at DESC);")
        print("✅ plays ensured")

        # PAYMENTS
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID REFERENCES users(id),
                tx_ref TEXT NOT NULL UNIQUE,
                status TEXT,
                amount INTEGER NOT NULL,
                credited_tries INTEGER,
                flw_tx_id TEXT,
                tg_id BIGINT,
                username TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_user_time ON payments (user_id, created_at DESC);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_payments_tg_time ON payments (tg_id, created_at DESC);")
        print("✅ payments ensured")

        # PROOFS
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS proofs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id),
                file_id TEXT NOT NULL,
                status TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_proofs_user_time ON proofs (user_id, created_at DESC);")
        print("✅ proofs ensured")

        # TRANSACTION LOGS
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS transaction_logs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                provider TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_txlogs_time ON transaction_logs (created_at DESC);")
        print("✅ transaction_logs ensured")

        # PRIZE WINNERS
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS prize_winners (
                id INTEGER PRIMARY KEY,
                user_id UUID NOT NULL REFERENCES users(id),
                tg_id BIGINT NOT NULL,
                choice TEXT NOT NULL,
                delivery_status TEXT,
                submitted_at TIMESTAMPTZ DEFAULT NOW(),
                pending_at TIMESTAMPTZ,
                in_transit_at TIMESTAMPTZ,
                delivered_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ DEFAULT NOW(),
                delivery_data JSONB
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_prize_winners_tg ON prize_winners (tg_id);")
        print("✅ prize_winners ensured")

        # TRIVIA QUESTIONS
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trivia_questions (
                id INTEGER PRIMARY KEY,
                category TEXT NOT NULL,
                question TEXT NOT NULL,
                options JSONB NOT NULL,
                answer TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_trivia_questions_cat ON trivia_questions (category);")
        print("✅ trivia_questions ensured")

        # USER ANSWERS
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_answers (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id),
                question_id INTEGER REFERENCES trivia_questions(id),
                selected TEXT NOT NULL,
                correct BOOLEAN,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_user_answers_user_time ON user_answers (user_id, created_at DESC);")
        print("✅ user_answers ensured")

        # SPIN RESULTS
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS spin_results (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID REFERENCES users(id),
                tg_id BIGINT,
                spin_type TEXT NOT NULL,
                outcome TEXT NOT NULL,
                extra_data JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_spin_results_tg_time ON spin_results (tg_id, created_at DESC);")
        print("✅ spin_results ensured")

        # AIRTIME PAYOUTS (includes cycle_id)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS airtime_payouts (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID REFERENCES users(id),
                tg_id BIGINT NOT NULL,
                cycle_id INTEGER REFERENCES cycles(id),

                phone_number TEXT,
                amount INTEGER NOT NULL,
                status TEXT NOT NULL,

                flutterwave_tx_ref TEXT,

                provider VARCHAR,
                provider_reference VARCHAR,
                provider_ref TEXT,
                provider_payload TEXT,
                provider_response JSONB,

                retry_count INTEGER NOT NULL DEFAULT 0,
                last_retry_at TIMESTAMPTZ,

                created_at TIMESTAMPTZ DEFAULT NOW(),
                sent_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_airtime_payouts_status ON airtime_payouts (status);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_airtime_payouts_tg ON airtime_payouts (tg_id);")
        print("✅ airtime_payouts ensured")

        # NON-AIRTIME WINNERS (includes cycle_id)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS non_airtime_winners (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID REFERENCES users(id),
                tg_id BIGINT NOT NULL,
                cycle_id INTEGER REFERENCES cycles(id),
                reward_type TEXT NOT NULL,
                notified_admin BOOLEAN,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_non_airtime_winners_tg ON non_airtime_winners (tg_id);")
        print("✅ non_airtime_winners ensured")

        # PREMIUM REWARD ENTRIES (includes cycle_id)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS premium_reward_entries (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id),
                tg_id BIGINT NOT NULL,
                cycle_id INTEGER REFERENCES cycles(id),
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_premium_entries_cycle_user_time
            ON premium_reward_entries (cycle_id, user_id, created_at);
            """
        )
        print("✅ premium_reward_entries ensured")

        # -------------------------------------------------------
        # 3) Record migration
        # -------------------------------------------------------
        cur.execute(
            "INSERT INTO schema_migrations (name, meta) VALUES (%s, %s::jsonb)",
            (
                MIGRATION_NAME,
                json.dumps(
                    {
                        "applied_by": "render_migration_script",
                        "applied_at": datetime.now(timezone.utc).isoformat(),
                        "notes": "Created all base tables from models.py for fresh Supabase DB",
                    }
                ),
            ),
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
