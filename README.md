# 🇳🇬 NaijaPrizeGate Bot 🎉

**NaijaPrizeGate Bot** is a Telegram-based trivia and multiplayer gaming platform built for the Nigerian market.

The platform offers three ways to play:

- **Paid Trivia:** Answer multiple-choice questions, earn Cycle Points and compete for announced rewards and cycle-based jackpot prizes.
- **Battle Mode (Free):** Create or join timed multiplayer trivia battles and compete for the highest score.
- **Challenge Friends (Free):** Invite friends to compete head-to-head in a free trivia challenge.

NaijaPrizeGate combines knowledge, friendly competition and exciting rewards through distinct gameplay experiences.

---

## 🎮 How NaijaPrizeGate Works

NaijaPrizeGate offers three distinct gameplay experiences.

### 1. Paid Trivia

Paid Trivia allows participants to purchase attempt packages and answer multiple-choice questions while competing for applicable rewards.

Participants can purchase the following attempt packages:

| Package Price | Attempts Credited |
|---|---:|
| ₦50 | 1 |
| ₦500 | 15 |
| ₦1,000 | 35 |

- Payments are processed through Flutterwave.
- Successfully verified payments are credited with the corresponding number of attempts.
- Participants can use their available paid attempts to play trivia.
- Bonus attempts may also be credited to eligible participants under the applicable platform rules.

### 2. Answer Trivia Questions

For each eligible Paid Trivia attempt used, the participant answers one multiple-choice question from an available category.

Each question has four options (A–D).

### 3. Points Allocation

After answering an eligible Paid Trivia question:

- **Correct Answer → 1 Cycle Point**
- **Wrong Answer → 0 Cycle Points**

Bonus attempts do not earn Cycle Points or qualify for cycle-based rewards.

### 4. Game Cycles

- Cycle Points accumulate during the applicable game cycle.
- Each game cycle operates according to its announced rules and winning threshold.
- The jackpot winner is determined in accordance with the applicable cycle rules.
- Bonus attempts do not qualify for cycle-based rewards.

### 5. Free Multiplayer Games

NaijaPrizeGate also offers two free multiplayer experiences.

**🔥 Battle Mode (Free)**

- Create a battle room and invite friends using a room link or room code.
- Choose a question category.
- Select 5, 10 or 15 questions.
- Choose a battle duration of 30, 60, 90 or 120 seconds.
- Set the maximum number of players, from 2 to 6, including the host.
- Compete for the highest score.

**⚔️ Challenge Friends (Free)**

- Create a challenge and invite friends.
- Compete head-to-head by answering five multiple-choice questions.
- Each question has a 15-second answering period.
- Earn one point for each correct answer.
- The participant with the highest score wins; tied highest scores are joint winners.

### 6. Rewards and Prizes

Paid Trivia rewards may include:

| Reward Category | Examples |
|---|---|
| Instant Rewards | Airtime |
| Mid-Tier Rewards | Bluetooth speakers, earpods |
| Jackpot Reward | Choice smartphone, subject to the applicable campaign |

Airtime and data rewards are recorded as pending payouts and processed after validation.

**Important:** Battle Mode and Challenge Friends are free multiplayer games. Their results do not automatically qualify participants for Paid Trivia Cycle Points, jackpot prizes or milestone rewards.

---

## 🌟 Key Principles

- 🧠 **Knowledge-based gameplay** — correct answers earn points in eligible Paid Trivia games.
- 🔁 **Consistent participation** — eligible Paid Trivia attempts contribute to a participant's progress within the game cycle.
- 🏆 **Performance-based competition** — jackpot winners are determined by eligible points and the applicable cycle rules.
- 🇳🇬 **Localized experience** — Nigerian-themed questions and payments in Nigerian Naira (NGN).
- ⚖️ **Fair play and transparency** — rewards are subject to the announced rules, eligibility requirements and performance.
- 🎮 **Different ways to play** — Paid Trivia, free Battle Mode and free Challenge Friends offer distinct gaming experiences.

---

## 🧱 System Architecture (High-Level)

```text
                 PAID TRIVIA
                      │
                      ↓
          Purchase Attempt Package
                      │
                      ↓
             Payment Verification
                      │
                      ↓
              Trivia Question
                      │
                ┌─────┴─────┐
                ↓           ↓
             Correct      Incorrect
                │           │
                ↓           ↓
          1 Cycle Point   0 Cycle Points
                │           │
                └─────┬─────┘
                      ↓
            Points Accumulate
             During Game Cycle
                      │
                      ↓
                Cycle Ends
                      │
                      ↓
          Jackpot Winner Determined
          Under Applicable Cycle Rules


              FREE MULTIPLAYER
                      │
             ┌────────┴────────┐
             ↓                 ↓
        Battle Mode      Challenge Friends
             │                 │
             ↓                 ↓
        Highest Score     Highest Score
             Wins              Wins
```

---

## 🛠 Tech Stack

- **FastAPI** — Webhook server and REST endpoints
- **python-telegram-bot (async)** — Telegram bot framework
- **SQLAlchemy** — ORM and database interaction
- **PostgreSQL** — Persistent data storage
- **Flutterwave** — NGN payment processing
- **Fly.io** — Application hosting and deployment

---

## 🔐 Security & Fair Play

- All payments are processed through **Flutterwave** and must be successfully verified before attempts are credited.
- Trivia answers are validated **server-side**.
- Administrative operations are restricted to authorized personnel.
- Critical administrative and payment operations are logged for accountability.
- Gameplay and reward eligibility are governed by the published Terms and applicable game-cycle rules.
- Attempts to exploit, automate or manipulate the platform may result in disqualification and other appropriate action.

---

## 📌 Disclaimer

NaijaPrizeGate offers different gaming experiences, including **Paid Trivia**, **Battle Mode (Free)** and **Challenge Friends (Free)**.

Paid Trivia participants compete based on their trivia performance and eligible points accumulated during the applicable game cycle.

Battle Mode and Challenge Friends are separate free multiplayer games. Their results do not automatically qualify participants for Paid Trivia Cycle Points, jackpot prizes or milestone rewards.

Rewards are subject to the applicable game rules, eligibility requirements and validation. Participation does not guarantee a prize or jackpot win.

**Play. Learn. Compete. Enjoy the experience.**

---

## 📜 Terms, Fair Play & Regulatory Disclosure

Participation in NaijaPrizeGate is subject to the platform's Terms of Participation, Fair Play Rules and applicable game-specific conditions.

Please read the full Terms before participating.

👉 [Read the Terms & Fair Play Rules](TERMS.md)
