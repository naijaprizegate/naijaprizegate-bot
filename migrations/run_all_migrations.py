import subprocess

scripts = [
    "add_users.py",
    "add_tries_log.py",
    "add_plays_log.py",
    "add_transaction_logs.py",
    "add_fw_transaction_id.py",
    "add_proofs_and_bonus_tries.py",
    "add_referrals_and_bonus_tries.py",
    "fix_payments_table.py",
]

for script in scripts:
    print(f"▶️ Running {script}...")
    subprocess.run(["python", f"migrations/{script}"], check=True)

print("✅ All migrations completed!")
