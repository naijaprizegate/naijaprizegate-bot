# 🇳🇬 NaijaPrizeGate Bot 🎉

**NaijaPrizeGate Bot** is a paid **trivia-and-reward Telegram bot** built for the Nigerian market.
Users pay **₦200 per trivia attempt** to answer Nigerian–themed trivia questions and earn spins that can lead to **instant rewards** and a **cycle-based jackpot prize**.

The system rewards **knowledge, consistency, and competition**.

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

# 📜 Terms, Fair Play & Regulatory Disclosure

## 📌 Terms of Participation

* Each trivia attempt costs **₦200**.
* A chance grants **one trivia question** and one resulting spin.
* Users may purchase and use multiple chances.
* All payments are processed via **Flutterwave** and must be **successfully verified** before a chance is credited.
* Chances are **non-refundable** once a trivia question has been served.

---

## ⚖️ Fair Play Rules

* Trivia questions are randomly selected from predefined categories.
* All answers are validated **server-side**.
* Users cannot influence question selection.
* Any attempt to exploit, automate, or manipulate the system results in **disqualification**.

Admin actions are logged and auditable to ensure fairness.

---

## 🧠 Skill-Based Gameplay Disclosure

NaijaPrizeGate is a **skill-influenced competition**, not a game of chance.

* Correct trivia answers lead to **Premium Spins**
* Premium Spins earn **Premium Points**
* The final jackpot winner is determined **solely by Premium Points**

Users who answer questions correctly **increase their likelihood of winning higher-tier rewards**.

---

## 🔄 Game Cycle & Win Threshold Logic

* Gameplay runs in **cycles**.
* Each cycle has a predefined **win threshold**, which may be based on:

  * Total Premium Points accumulated across all users, or
  * A fixed campaign duration, or
  * A predefined number of total spins

At the end of a cycle:

* 🏆 **The user with the highest Premium Points is declared the Jackpot Winner**
* In the event of a tie, predefined tie-breaking rules (e.g. earliest point attainment) are applied.

Cycle rules may be announced at the start of each campaign.

---

## 🎁 Reward Disclosure

* Rewards are **not guaranteed** on every spin.
* Standard Spins have **lower reward**.
* Premium Spins have **higher reward** and contribute to Premium Points.

Reward distribution is **configurable and adjustable** to ensure system sustainability.

The jackpot prize is awarded **only once per completed cycle**.

---

## 📱 Prize Fulfillment

* Airtime and data rewards are recorded as **pending payouts** and processed after validation.
* Physical prizes (e.g. smartphones, speakers, earpods) may require:

  * Identity verification
  * Delivery coordination
* Failure to provide valid contact details may result in forfeiture of a prize.

---

## 🚫 Abuse & Disqualification

NaijaPrizeGate reserves the right to:

* Disqualify users engaging in abuse or fraud
* Withhold rewards obtained through system manipulation
* Reset points or counters in the event of system misuse

These actions are taken to protect **fair competition**.

---

## 🧾 Regulatory & Compliance Notice

* NaijaPrizeGate is designed as a **knowledge-based promotional competition**.
* Trivia performance directly affects outcomes.
* Rewards are earned through **demonstrated skill and participation**, not random selection.
* No participant is guaranteed a jackpot prize.

Users are encouraged to play responsibly.

---

## 🧠 Transparency Commitment

* Premium Points determine the jackpot winner.
* All critical operations are logged.
* Admin actions are auditable.
* Game mechanics are disclosed publicly.
