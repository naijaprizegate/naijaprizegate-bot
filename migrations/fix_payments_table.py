import os
import psycopg2

# Get DB connection details from Render environment variables
DATABASE_URL = os.environ.get("DATABASE_URL")

def ensure_column(cur, table, column, col_type, default=None):
    """
    Add column if missing.
    """
    cur.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = %s AND column_name = %s
    """, (table, column))
    exists = cur.fetchone()
    if not exists:
        sql = f'ALTER TABLE {table} ADD COLUMN "{column}" {col_type}'
        if default is not None:
            sql += f' DEFAULT {default}'
        cur.execute(sql)
        print(f"✅ Added column: {column}")
    else:
        print(f"ℹ️ Column already exists: {column}")

def main():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # Ensure all required columns exist
    ensure_column(cur, "payments", "id", "SERIAL PRIMARY KEY")
    ensure_column(cur, "payments", "tg_id", "BIGINT NOT NULL")
    ensure_column(cur, "payments", "tx_ref", "VARCHAR(255) NOT NULL")
    ensure_column(cur, "payments", "amount", "NUMERIC(10,2) NOT NULL DEFAULT 0")
    ensure_column(cur, "payments", "tries", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(cur, "payments", "status", "VARCHAR(50) NOT NULL DEFAULT 'pending'")
    ensure_column(cur, "payments", "created_at", "TIMESTAMP NOT NULL DEFAULT NOW()")
    ensure_column(cur, "payments", "expires_at", "TIMESTAMP")

    conn.commit()
    cur.close()
    conn.close()
    print("🎉 Migration completed successfully!")

if __name__ == "__main__":
    main()
