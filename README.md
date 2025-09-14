# NaijaPrizeGate Bot 🎉

Telegram bot for **NaijaPrizeGate** — a lucky draw campaign where users can try their luck at winning prizes.  
This is the **Stage 1 MVP** of the project.

---

## 🚀 Features

- `/start` — welcome users & explain rules  
- `/tryluck` — lets users pay & try for a chance to win  
- `/stats` or `/stat` — shows overall stats  
- Admin command `/resetcounter` — reset the try counter  
- Secure webhook with **secret path** for Telegram updates  
- PostgreSQL database (via Render) for counters & winners  
- Callback query support (buttons & inline actions)  
- Greeting detection (e.g., "hello", "hi", "good morning")  
- Payment integration (Flutterwave) with redirect + verification  
- Background scheduler for periodic tasks  

---

## 🛠️ Tech Stack

- [FastAPI](https://fastapi.tiangolo.com/) — Webhook server + REST API  
- [python-telegram-bot](https://docs.python-telegram-bot.org/) — Telegram bot framework  
- [SQLAlchemy](https://www.sqlalchemy.org/) — Database ORM  
- [PostgreSQL](https://www.postgresql.org/) — Persistent data store  
- [Render](https://render.com/) — Hosting & deployment platform  

---

## ⚙️ Setup (Local Development)

### 1. Clone the repo
```bash
git clone https://github.com/your-username/naijaprizegate-bot.git
cd naijaprizegate-bot
