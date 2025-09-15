import os
import psycopg2

DATABASE_URL = os.environ.get("DATABASE_URL")

def main():
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    # Check if fw_transaction_id column exists
    cur.execute("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = 'payments' AND column_name = 'fw_transaction_id'
    """)
    exists = cur.fetchone()

    if not exists:
        cur.execute('ALTER TABLE payments ADD COLUMN fw_transaction_id VARCHAR(255)')
        print("✅ Added column: fw_transaction_id")
    else:
        print("ℹ️ Column already exists: fw_transaction_id")

    conn.commit()
    cur.close()
    conn.close()
    print("🎉 Migration completed successfully!")

if __name__ == "__main__":
    main()
