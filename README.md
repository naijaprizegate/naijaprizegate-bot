# 🇳🇬 NaijaPrizeGate Bot 🎉

**NaijaPrizeGate Bot** is a paid **trivia-and-reward Telegram bot** built for the Nigerian market.
Users pay **₦200 per chance** to answer Nigerian–themed trivia questions and earn spins that can lead to **instant rewards** and a **cycle-based jackpot prize**.

The system rewards **knowledge, consistency, and competition**, not just luck.

---

## 🎮 How NaijaPrizeGate Works

### 1️⃣ Buy a Chance

* Each trivia attempt costs **₦200**.
* Users purchase chances via **Flutterwave Checkout (NGN)**.
* Each successful payment credits the user with **one trivia chance**.

---

### 2️⃣ Answer Trivia

For every chance used, the user answers **one multiple-choice trivia question** from a selected category:

* 🇳🇬 **History**
* 🎬 **Entertainment**
* ⚽ **Football**
* 🌍 **Geography**

Each question has four options (A–D).

---

### 3️⃣ Spin Allocation (Performance-Based)

After answering the question:

* ✅ **Correct Answer → Premium Spin**
* ❌ **Wrong Answer → Standard Spin**

This ensures **skill directly improves reward quality**.

---

### 4️⃣ Spins, Premium Points & Rewards

* **Standard Spins**

  * Lower-tier rewards or no reward
* **Premium Spins**

  * Higher-value rewards
  * Earn **Premium Points**

Each **Premium Spin adds to the user’s Premium Points balance**.

---

### 5️⃣ Premium Points & Game Cycle

* Premium Points **accumulate across multiple plays**
* A **game cycle** runs until a predefined **win threshold** is reached
* At the end of the cycle:

  * 🏆 **The user with the highest Premium Points wins the Jackpot Prize**

---

### 6️⃣ Reward Structure

| Reward Tier         | Examples                             |
| ------------------- | ------------------------------------ |
| 🎁 Instant Rewards  | Airtime                              |
| 🔊 Mid-Tier Rewards | Bluetooth speakers, earpods          |
| 📱 Jackpot Reward   | **Choice smartphone** (cycle winner) |

Airtime and data rewards are recorded as **pending payouts** and processed after validation.

---

## 🌟 Key Principles

* 🧠 **Knowledge-first gameplay** — correct answers matter
* 🔁 **Repeat play advantage** — consistency builds points
* 🏆 **Transparent competition** — highest Premium Points wins
* 🇳🇬 **Localized experience** — Nigerian questions & NGN payments
* ⚖️ **Fair system** — no guaranteed jackpot without performance

---

## 🧱 System Architecture (High-Level)

```text
User Payment (₦200)
      ↓
Trivia Question
      ↓
Correct? ── Yes → Premium Spin → Premium Points
        └─ No  → Standard Spin
      ↓
Reward / Point Accumulation
      ↓
Cycle Ends → Highest Points Wins Jackpot
```

---

## 🛠 Tech Stack

* **FastAPI** — Webhook server & REST endpoints
* **python-telegram-bot (async)** — Telegram bot framework
* **SQLAlchemy** — ORM
* **PostgreSQL** — Persistent storage
* **Flutterwave** — NGN payments
* **Render** — Hosting & deployment

---

## 🔐 Security & Fair Play

* All payments are **verified via Flutterwave webhooks**
* Trivia answers are **validated server-side**
* Admin operations are **restricted and logged**
* Jackpot winner selection is **point-based and auditable**

---

## 📌 Disclaimer

NaijaPrizeGate is a **skill-influenced reward system**.
Trivia performance affects spin quality and Premium Points accumulation.
Jackpot rewards are awarded **only at the end of a completed game cycle** to the user with the highest Premium Points.


> **Knowledge improves your odds. Consistency wins the jackpot.**
