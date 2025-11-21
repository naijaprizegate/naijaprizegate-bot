# NaijaPrizeGate Bot 🇳🇬🎉

**NaijaPrizeGate Bot** is a Telegram bot that lets users **buy tries**, answer fun **Nigerian–themed trivia**, and spin a **lucky wheel** to win real prizes like **airtime**, **data bundles**, and even **smartphones**.  

It is built for the Nigerian market, with **NGN payments via Flutterwave** and a PostgreSQL backend for tracking users, tries, and payouts.

---

## 🎮 What the Bot Does

### Core User Flow

1. **Start the bot**
   - `/start` — greets the user, explains how NaijaPrizeGate works, and shows the main menu.

2. **Buy tries via Flutterwave**
   - Users choose a package (e.g. 1, 5, 15 tries — prices configurable in code).
   - Payment is processed via **Flutterwave Checkout**.
   - A verified payment automatically credits the user with the appropriate number of tries.

3. **Play “Try Your Luck”**
   - The user taps the **Try Luck** button.
   - Selects a trivia category:
     - 🇳🇬 **History** (`nigeria_history`)
     - 🎬 **Entertainment** (`nigeria_entertainment`)
     - ⚽ **Football** (`football`)
     - 🌍 **Geography** (`geography`)
   - A Nigerian–themed multiple–choice question is shown with four options (A–D).
   - The answer is evaluated:
     - **Correct answer → Premium Spin** 🎯  
     - **Wrong answer → Basic Spin** 😅  
   - The bot then runs the spin logic and records the outcome.

4. **Win Real Prizes**
   Depending on the spin outcome, users can win:

   - 📱 **Airtime recharges**
   - 📶 **Data bundles**
   - 📞 **Smartphones / phones** (top–tier prizes)
   - 🎟️ Or other configurable reward types

   Airtime & data payouts are stored in the database as **pending payouts** for processing, with each record tied to:
   - User
   - Phone number
   - Amount
   - Status (`pending`, `completed`, etc.)

5. **Stats & Counters**
   - `/stats` or `/stat` — shows basic statistics (e.g. total tries, winners, etc. depending on what you expose).
   - Admin command `/resetcounter` — resets try counters (e.g. daily/weekly campaign resets).

---

## 🌟 Key Features

- ✅ **Trivia before spin** — Users must answer a question before spinning, making it fun and knowledge-based.
- ✅ **Multiple categories** — History, Entertainment, Football, Geography (mapped cleanly to internal JSON categories).
- ✅ **Smart spin logic** — Premium vs Basic spins based on trivia result.
- ✅ **Real rewards** — Airtime, data bundles, and **phones** as prizes.
- ✅ **Payment Integration** — Flutterwave Standard Checkout with webhook verification.
- ✅ **Try balance tracking** — Users have a stored number of tries in the database.
- ✅ **Admin tools** — Safe admin-only operations like resetting counters.
- ✅ **Background tasks** — Periodic jobs for maintenance / payout follow-up (via a background scheduler).
- ✅ **PostgreSQL storage** — Persistent records of users, payments, tries, and payouts.

---

## 🛠 Tech Stack

- **[FastAPI](https://fastapi.tiangolo.com/)** — Webhook server + REST endpoints  
- **[python-telegram-bot](https://docs.python-telegram-bot.org/)** — Telegram bot framework (async)  
- **[SQLAlchemy](https://www.sqlalchemy.org/)** — ORM for PostgreSQL  
- **[PostgreSQL](https://www.postgresql.org/)** — Main database (tries, payouts, users, etc.)  
- **[Render](https://render.com/)** — Hosting & deployment  
- **[Flutterwave](https://flutterwave.com/)** — Payment processing in NGN  

---

## 📦 Project Structure (Simplified)

```text
src/
  app.py                  # FastAPI app & webhook entrypoint
  handlers/
    core.py               # /start, basic commands & menus
    payments.py           # Buy tries, handle Flutterwave initiation
    tryluck.py            # Trivia + spin logic integration
    admin.py              # Admin-only commands (e.g. reset counter)
  services/
    payments.py           # Payment verification & tries calculation
    tryluck.py            # Core spin logic & prize selection
  utils/
    questions_loader.py   # Loads and filters trivia questions
    logger.py             # Centralized structured logging
  questions.json          # Nigerian trivia questions (160 total)
