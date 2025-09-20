# 📦 Database Migrations for NaijaPrizeGate Bot

This folder contains **migration scripts** for setting up and maintaining the PostgreSQL schema used by the bot.  
Each migration is a standalone Python script that ensures a table exists, creating it if missing.  
They are **idempotent** — safe to re-run.

---

## 📑 Available Migrations

- `add_users.py` → Creates the `users` table (Telegram users + tries_remaining counter).
- `add_tries_log.py` → Creates the `tries_log` table (records each change in user tries).
- `add_plays_log.py` → Creates the `plays_log` table (records every spin outcome).
- `add_transaction_logs.py` → Creates the `transaction_logs` table (payment history).

---

## ⚙️ Running Migrations Locally

### 1. Set up environment
Make sure your `DATABASE_URL` is available as an environment variable:

```bash
export DATABASE_URL="postgresql://username:password@host:port/dbname"
