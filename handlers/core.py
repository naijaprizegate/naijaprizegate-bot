# ==============================================================
# handlers/core.py — Compliance-Safe Version (Polished)
# ==============================================================
import re
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)

from helpers import md_escape, get_or_create_user
from db import get_async_session
from handlers.challenge import join_challenge
from services.mockjamb_room_service import get_mockjamb_room_by_code
from handlers.mockjamb import extract_mockjamb_room_code_from_start_payload

from sqlalchemy import select

from finance_models import ReferralORM
from models import User
from services.finance.referral_finance import create_referral

from services.mockwaec_room_service import get_mockwaec_room_by_code
from handlers.mockwaec import (
    extract_mockwaec_room_code_from_start_payload,
    make_mockwaec_join_room_keyboard,
)

logger = logging.getLogger(__name__)


# ===============================================================
# 📘 /terms COMMAND HANDLER
# ===============================================================

async def terms_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📘 <b>Fair Play &amp; Terms</b>\n\n"
        "NaijaPrizeGate is a trivia competition where "
        "participants compete through their performance.\n\n"

        "🏆 <b>Scoring &amp; Jackpot</b>\n"
        "✔ Correct answers on paid attempts earn one cycle point\n"
        "✔ Incorrect answers earn no cycle point\n"
        "✔ Bonus attempts do not earn cycle points\n"
        "✔ The participant with the highest cycle points at the end "
        "of a game cycle wins the jackpot prize\n"
        "✔ Ties are resolved using the earliest attainment of the "
        "highest score\n\n"

        "⚖️ <b>Fair Play Rules</b>\n"
        "✔ Questions are selected from predefined categories\n"
        "✔ Answers are validated by the platform\n"
        "✔ Cheating, automation, or abuse may lead to disqualification\n\n"

        "💳 <b>Payments &amp; Participation</b>\n"
        "✔ Paid attempts are consumed before bonus attempts\n"
        "✔ Bonus attempts may be earned through approved promotional "
        "activities\n"
        "✔ Paid attempts are counted when used, not merely when credited\n\n"
        "✔ Battle Mode and Challenge Friends are separate free multiplayer games\n"
        "✔ Multiplayer results do not automatically earn Paid Trivia cycle points or prizes\n\n"

        "🎁 <b>Rewards &amp; Fulfilment</b>\n"
        "✔ Rewards are subject to the applicable game and prize rules\n"
        "✔ Prize claims must be made within four days of official "
        "notification\n"
        "✔ Physical prizes require accurate contact and delivery details\n\n"

        "📜 By continuing to use NaijaPrizeGate, you agree to our "
        "full <b>Terms &amp; Conditions</b>.\n\n"

        "🛑 <b>Disclaimer</b>\n"
        "Brand or product names shown as prizes (e.g. iPhone, Samsung "
        "Galaxy) are used only to describe available rewards. "
        "Apple Inc. and Samsung Electronics are not sponsors, partners, "
        "or affiliated with NaijaPrizeGate."
    )

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📄 Read Full Terms & Conditions",
                url="https://naijaprizegate.github.io/naijaprizegate-bot/"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Back to Other Menu",
                callback_data="menu:other"
            )
        ],
        [
            InlineKeyboardButton(
                "🏠 Main Menu",
                callback_data="menu:main"
            )
        ],
    ])

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=markup,
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=markup,
        )



# ===============================================================
# ❓ FAQ HANDLER
# ===============================================================


async def faq_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    page = 2 if query and query.data == "faq:page2" else 1

    if page == 1:
        text = (
            "❓ <b>NaijaPrizeGate — Frequently Asked Questions</b> "
            "<i>(Page 1 of 2)</i>\n\n"

            "🏆 <b>1. How do I win the jackpot?</b>\n"
            "Answer Paid Trivia questions to earn cycle points. The participant "
            "with the highest cycle points at the end of the game cycle wins.\n\n"

            "📊 <b>2. How does the scoring system work?</b>\n"
            "Every correct answer on a paid attempt earns one cycle point. An "
            "incorrect answer earns no point. Bonus attempts do not earn cycle points.\n\n"

            "🔄 <b>3. What is a game cycle?</b>\n"
            "A game cycle is a competition period during which participants "
            "accumulate cycle points. It ends when the configured paid-attempt "
            "play threshold is reached.\n\n"

            "🥇 <b>4. How is the jackpot winner determined?</b>\n"
            "The participant with the highest cycle points at the end of the "
            "cycle wins. If participants are tied, the participant who attained "
            "the highest score first takes priority.\n\n"

            "💳 <b>5. What is the difference between paid and bonus attempts?</b>\n"
            "Paid attempts are purchased through the available payment options. "
            "Bonus attempts are promotional attempts awarded through approved "
            "activities. Paid attempts are always used before bonus attempts.\n\n"

            "🎁 <b>6. How can I get free bonus attempts?</b>\n"
            "You can earn 10 free bonus Trivia attempts by following NaijaPrizeGate "
            "on the listed social media platforms and submitting proof through "
            "the bot. The attempts are credited after admin approval.\n\n"

            "⭐ <b>7. Do bonus attempts earn cycle points?</b>\n"
            "No. Bonus attempts allow you to play Trivia, but correct answers "
            "on bonus attempts do not earn cycle points or contribute to "
            "jackpot ranking.\n\n"

            "👥 <b>8. How does the Refer a Friend programme work?</b>\n"
            "Share your personal referral link. When someone joins through your "
            "link and makes a qualifying payment, your referral relationship "
            "becomes active. You can then earn referral commissions on eligible "
            "payments made by your referrals.\n\n"

            "💰 <b>9. How much can I earn from referrals?</b>\n"
            "Direct referrals earn you 5% commission on qualifying payments. "
            "For qualifying paid Trivia plays, you may also earn 1% from each "
            "of generations 2 to 5 in your referral network. Other qualifying "
            "payment types pay commission to the direct referrer only. "
            "Qualifying payments must be at least ₦100."
        )

    else:
        text = (
            "❓ <b>NaijaPrizeGate — Frequently Asked Questions</b> "
            "<i>(Page 2 of 2)</i>\n\n"

            "🏦 <b>10. Where do my referral earnings go?</b>\n"
            "Your referral commissions are credited to your Referral Wallet. "
            "They are separate from Trivia cycle points, Premium Points and "
            "bonus attempts.\n\n"

            "📤 <b>11. How can I withdraw my referral earnings?</b>\n"
            "Withdrawals are made in multiples of ₦2,000. Every ₦2,000 "
            "withdrawal requires 4 Premium Points. You must also have sufficient "
            "available Referral Wallet balance and a verified bank account. "
            "Follow the withdrawal qualification instructions in the Finance menu.\n\n"

            "🛒 <b>12. What happens when I run out of attempts?</b>\n"
            "You can purchase additional attempts or participate in available "
            "promotional activities to earn bonus attempts.\n\n"

            "🎉 <b>13. What rewards can I win?</b>\n"
            "Paid Trivia rewards may include jackpot and promotional or milestone "
            "rewards, subject to the applicable rules. Winning Battle Mode or "
            "Challenge Friends does not automatically qualify you for those rewards.\n\n"

            "⏰ <b>14. How long do I have to claim a prize?</b>\n"
            "Eligible winners must claim their prizes within four days of official "
            "notification. Follow the instructions provided when notified.\n\n"

            "💳 <b>15. What if my payment succeeds but my attempts are not credited?</b>\n"
            "Contact support at naijaprizegate@gmail.com and provide your "
            "payment reference and relevant Telegram account details for verification.\n\n"

            "↩️ <b>16. Can I request a refund?</b>\n"
            "Refunds are handled in accordance with the payment and refund "
            "provisions in our official Terms &amp; Conditions. Contact support "
            "if you experience a payment problem.\n\n"

            "🛡️ <b>17. Can I use multiple accounts or automated tools?</b>\n"
            "Cheating, automation, manipulation of gameplay and other forms "
            "of abuse are prohibited and may result in disqualification or "
            "account restrictions.\n\n"

            "📩 <b>18. How can I contact NaijaPrizeGate support?</b>\n"
            "Send an email to naijaprizegate@gmail.com. Include your Telegram "
            "account details and any relevant transaction reference.\n\n"

            "🔥 <b>19. What is Battle Mode?</b>\n"
            "Battle Mode is a free multiplayer trivia game. The host creates a "
            "room, chooses a category, 5, 10 or 15 questions, and a total battle "
            "time of 30, 60, 90 or 120 seconds. A room can have 2 to 6 players, "
            "including the host. At least 2 players are needed to start.\n\n"

            "🏆 <b>20. How is the Battle Mode winner decided?</b>\n"
            "Each correct answer earns one point. Wrong and skipped answers earn "
            "no points. The player with the most correct answers wins. If players "
            "tie for the highest score, they are joint winners.\n\n"

            "⚔️ <b>21. How does Challenge Friends work?</b>\n"
            "Challenge Friends is free. The creator invites friends, selects a "
            "category and starts when at least 2 players have joined. Each player "
            "answers 5 multiple-choice questions, with 15 seconds per question. "
            "Each correct answer earns one point; wrong or timed-out answers earn none. "
            "Players tied for the highest score are joint winners.\n\n"

            "🎁 <b>22. Do Battle Mode or Challenge Friends winners receive the jackpot?</b>\n"
            "No. Winning either multiplayer mode does not by itself earn Paid Trivia "
            "cycle points or qualify you for Paid Trivia jackpot or milestone prizes. "
            "A separately announced promotion may have its own conditions.\n\n"

            "📜 For complete rules and additional information, please read "
            "our full Terms &amp; Conditions."
        )

    rows = [
        [
            InlineKeyboardButton(
                "📄 Read Full Terms & Conditions",
                url="https://naijaprizegate.github.io/naijaprizegate-bot/"
            )
        ],
    ]

    if page == 1:
        rows.append([
            InlineKeyboardButton(
                "➡️ More FAQs (Page 2)",
                callback_data="faq:page2"
            )
        ])
    else:
        rows.append([
            InlineKeyboardButton(
                "⬅️ Previous FAQs (Page 1)",
                callback_data="faq"
            )
        ])

    rows.extend([
        [InlineKeyboardButton("⬅️ Back to Other Menu", callback_data="menu:other")],
        [InlineKeyboardButton("🏠 Main Menu", callback_data="menu:main")],
    ])

    markup = InlineKeyboardMarkup(rows)

    if query:
        await query.answer()
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=markup,
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=markup,
        )


# ===============================================================
# MAIN MENU / OTHER MENU KEYBOARDS
# ===============================================================
def build_main_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🎮 Entertainment",
                    callback_data="menu:entertainment",
                )
            ],
            [
                InlineKeyboardButton(
                    "🎓 Education",
                    callback_data="menu:education",
                )
            ],
            [
                InlineKeyboardButton(
                    "💰 Finance (Wallet & Rewards)",
                    callback_data="finance:open",
                )
            ],
            [
                InlineKeyboardButton(
                    "🏆 View Prizes",
                    callback_data="prizes:open",
                )
            ],
            [
                InlineKeyboardButton(
                    "📘 Terms & Fair Play",
                    callback_data="terms"
                )
            ],
        ]
    )


def build_entertainment_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🧠 Play Trivia Questions (Win Phones)", callback_data="playtrivia")],
            [InlineKeyboardButton("⚔️ Challenge Friends (Free)", callback_data="challenge:start")],
            [InlineKeyboardButton("🔥 Battle Mode (Free)", callback_data="battle:menu")],
            [InlineKeyboardButton("📂 Other Menu", callback_data="menu:other")],
            [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="menu:main")],
        ]
    )


def build_education_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(
                    "🎓 JAMB / WAEC / NECO",
                    callback_data="exam:hub",
                )
            ],
            [
                InlineKeyboardButton(
                    "🏛️ University Practice & Tutorials",
                    callback_data="uni_start",
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Back to Main Menu",
                    callback_data="menu:main",
                )
            ],
        ]
    )




def build_other_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💳 Get More Trivia Attempts", callback_data="buy")],
            [InlineKeyboardButton("🎁 Earn Free Trivia Attempts", callback_data="free")],
            [InlineKeyboardButton("📊 My Available Trivia Attempts", callback_data="show_tries")],
            [InlineKeyboardButton("🏆 Leaderboard", callback_data="leaderboard:show")],
            [InlineKeyboardButton("📘 Terms & Fair Play", callback_data="terms")],
            [InlineKeyboardButton("❓ FAQs", callback_data="faq")],
            [InlineKeyboardButton("📩 Contact Support / Admin", callback_data="support:start")],
            [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="menu:main")],
        ]
    )


def build_exam_hub_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎓 JAMB Practice", callback_data="jambpractice")],
            [InlineKeyboardButton("📘 WAEC / NECO Practice", callback_data="waecpractice")],
            [InlineKeyboardButton("📝 Mock JAMB (UTME)", callback_data="mock:jamb")],
            [InlineKeyboardButton("🧪 Mock WAEC / NECO", callback_data="mock:waec")],
            [InlineKeyboardButton("📖 Tutorials", callback_data="tutorials")],
            [InlineKeyboardButton("⬅️ Back to Education", callback_data="menu:education")],
            [InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="menu:main")],
        ]
    )

def make_mockjamb_join_room_keyboard(room_code: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Join This Room", callback_data=f"mjr_join::{room_code}")],
        [InlineKeyboardButton("🏠 Back to Main Menu", callback_data="menu:main")],
    ])


# ===================================================
# Text Builders
# ====================================================

def build_start_text(user_first_name: str) -> str:
    return (
        f"👋 Hello *{md_escape(user_first_name)}*\\!\n\n"
        "🎉 Welcome to *NaijaPrizeGate* — Learn, Compete & Win 🇳🇬\n\n"
        "Whether you're here to have fun or prepare for exams, there's something for everyone\\.\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🎮 *Entertainment*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "• Play Paid Trivia and compete for announced prizes\n"
        "• Challenge friends in a free multiplayer trivia game\n"
        "• Join free Battle Mode rooms and compete with other players\n"
        "• 💰 Earn referral commissions by referring friends\n\n\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🎓 *Education*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "• Practice for JAMB, WAEC & NECO\n"
        "• Practice for IGCSE, SAT & TOEFL\n"
        "• Prepare for University exams\n"
        "• Access tutorials and mock exams\n\n\n\n"
        "🏆 Top performers can compete for amazing prizes including:\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🏆 *GRAND PRIZE*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "📱 *iPhone 18 Pro Max*\n\n"
        "📱 *Samsung Galaxy S26 Ultra*\n\n"
        "🏆 *The winner chooses one of the two phones\\.*\n\n\n\n"
        "*Other Milestone Rewards*\n\n"
        "📞 *Instant Airtime Rewards*\n\n"
        "🎧 *AirPods*\n\n"
        "🔊 *Bluetooth Speakers*\n\n"
        "⌚ *Smart Watch*\n\n"
        "📱 *Android Smartphone*\n\n\n\n"
        "📘 Please read *Terms & Fair Play* for important information about participation\\.\n\n"
        "📘 Continuing with the game means you have read and accepted the Terms\\.\n\n"
        "👇 Choose a category below to begin\\."
    )

def build_entertainment_text() -> str:
    return (
        "━━━━━━━━━━━━━━━━━━\n"
        "🎮 *Entertainment*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Play Paid Trivia to compete for announced prizes, or enjoy the free multiplayer games to test your knowledge against friends.\n\n"
        "Choose how you'd like to play:\n\n"
        "🧠 *Play Trivia* - Answer questions, earn Premium Points and compete for announced prizes, subject to the applicable rules.\n\n"
        "⚔️ *Challenge Friends (Free)* - Invite friends and compete head-to-head in a free multiplayer trivia game.\n\n"
        "🔥 *Battle Mode (Free)* - Create or join multiplayer trivia battles and compete against other players for the highest score.\n\n\n\n"
        "👇 Select an option below."
    )

def build_education_text() -> str:
    return (
        "━━━━━━━━━━━━━━━━━━\n"
        "🎓 *Education*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Build confidence, improve your knowledge and prepare for academic success.\n\n"
        "Choose from a growing collection of study resources, including:\n\n"
        "📚 *JAMB, WAEC & NECO* - Practice questions, mock exams and tutorials.\n\n"
        "🏛️ *University* - Practice questions and learning resources.\n\n"
        "🌍 *IGCSE, SAT & TOEFL* - International exam preparation.\n\n\n\n"
        "👇 Choose where you'd like to begin."
    )

def build_exam_hub_text() -> str:
    return (
        "━━━━━━━━━━━━━━━━━━\n"
        "🎓 *Exam Practice Hub*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Everything you need to prepare with confidence—all in one place.\n\n"
        "📚 *Practice Questions* – Study by subject and topic at your own pace.\n\n"
        "📝 *Mock Examinations* – Simulate the real exam experience and test your readiness.\n\n"
        "📖 *Tutorials* – Strengthen your understanding with guided learning resources.\n\n\n\n"
        "👇 Choose an option below."
    )

def build_other_menu_text() -> str:
    return (
        "━━━━━━━━━━━━━━━━━━\n"
        "📂 *Other Menu*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "Choose any of the options below:"
    )


# ----------------------------------------
# Exam Hub Handler
# ---------------------------------------
async def exam_hub_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    text = build_exam_hub_text()
    markup = build_exam_hub_keyboard()

    try:
        await query.edit_message_text(
            text,
            reply_markup=markup,
            parse_mode="Markdown",
        )
    except Exception:
        await query.message.reply_text(
            text,
            reply_markup=markup,
            parse_mode="Markdown",
        )


# ===============================================================
# OTHER MENU CALLBACK
# ===============================================================
async def other_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    text = build_other_menu_text()
    markup = build_other_menu_keyboard()

    try:
        await query.edit_message_text(
            text,
            reply_markup=markup,
            parse_mode="Markdown",
        )
    except Exception:
        await query.message.reply_text(
            text,
            reply_markup=markup,
            parse_mode="Markdown",
        )

# ===============================================================
# Entertainment Menu
# ===============================================================
async def entertainment_menu_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    text = build_entertainment_text()

    await query.edit_message_text(
        text=text,
        reply_markup=build_entertainment_keyboard(),
        parse_mode="Markdown",
    )

# ===============================================================
# Education Menu
# ===============================================================
async def education_menu_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return

    await query.answer()

    text = build_education_text()

    await query.edit_message_text(
        text=text,
        reply_markup=build_education_keyboard(),
        parse_mode="Markdown",
    )


# ===============================================================
# /start (with optional referral / deep links)
# ===============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_referral_created = False

    joined = await join_challenge(update, context)
    if joined:
        return

    user = update.effective_user

    async with get_async_session() as session:
        db_user = await get_or_create_user(
            session,
            tg_id=user.id,
            username=user.username,
        )

        if context.args:
            referral_arg = context.args[0].strip()

            if referral_arg.isdigit():
                referrer_tg_id = int(referral_arg)

                # Never allow self-referral.
                if referrer_tg_id != user.id:
                    referrer_result = await session.execute(
                        select(User).where(
                            User.tg_id == referrer_tg_id
                        )
                    )
                    referrer_user = (
                        referrer_result.scalar_one_or_none()
                    )

                    if referrer_user:
                        existing_referral_result = await session.execute(
                            select(ReferralORM).where(
                                ReferralORM.referred_user_id
                                == db_user.id
                            )
                        )
                        existing_referral = (
                            existing_referral_result.scalar_one_or_none()
                        )

                        if existing_referral is None:
                            referral_result = await create_referral(
                                session,
                                referrer_user_id=referrer_user.id,
                                referred_user_id=db_user.id,
                                referral_code_used=str(
                                    referrer_tg_id
                                ),
                            )

                            new_referral_created = True

                            logger.info(
                                "🤝 Referral registered | "
                                "referral_id=%s | "
                                "referrer_tg_id=%s | "
                                "referred_tg_id=%s",
                                referral_result.id,
                                referrer_tg_id,
                                user.id,
                            )

        await session.commit()


    # ===========================================================
    # NEW REFERRAL NOTIFICATION
    # ===========================================================
    if new_referral_created:
        referred_name = (
            f"@{user.username}"
            if user.username
            else (user.first_name or "A new user")
        )

        try:
            await context.bot.send_message(
                chat_id=referrer_tg_id,
                text=(
                    "🎉 <b>New Referral!</b>\n\n"
                    "Someone just joined NaijaPrizeGate "
                    "through your referral link.\n\n"
                    f"👤 {referred_name}\n\n"
                    "Your referral has been recorded. 🎉\n\n"
                    "💰 You earn <b>5%</b> whenever your referral "
                    "plays a paid Trivia chance.\n\n\n\n"
                    "👉 Use /start to go to the main menu."
                ),
                parse_mode="HTML",
            )

            logger.info(
                "Referral notification sent | "
                "referrer_tg_id=%s | referred_tg_id=%s",
                referrer_tg_id,
                user.id,
            )

        except Exception as exc:
            # Notification failure must not affect registration.
            logger.warning(
                "Could not send referral notification | "
                "referrer_tg_id=%s | error=%s",
                referrer_tg_id,
                exc,
            )


    # ===========================================================
    # DEEP LINK HANDLERS
    # ===========================================================
    if context.args:
        arg = context.args[0].strip()

        # --------------------------------------
        # Mock JAMB Invite Room Code/Link
        # ------------------------------------
        room_code = extract_mockjamb_room_code_from_start_payload(arg)
        if room_code:
            async with get_async_session() as session:
                room = await get_mockjamb_room_by_code(session, room_code=room_code)

            if not room:
                return await update.effective_message.reply_text(
                    "⚠️ This room invite is invalid or no longer available."
                )

            room_status = str(room.get("status") or "").strip().lower()
            if room_status != "waiting":
                return await update.effective_message.reply_text(
                    "⚠️ This room is no longer open for joining."
                )

            context.user_data["mjr_room_code"] = room_code
            context.user_data["mjr_is_host"] = False

            return await update.effective_message.reply_text(
                "👥 Mock JAMB Room Invite\n\n"
                f"Room Code: {room_code}\n\n"
                "You were invited to join a Mock JAMB multiplayer room.\n"
                "Tap the button below to join this room.",
                reply_markup=make_mockjamb_join_room_keyboard(room_code),
            )


        # --------------------------------------
        # Mock WAEC Invite Room Code/Link
        # --------------------------------------
        room_code = extract_mockwaec_room_code_from_start_payload(arg)
        if room_code:
            async with get_async_session() as session:
                room = await get_mockwaec_room_by_code(session, room_code=room_code)

            if not room:
                return await update.effective_message.reply_text(
                    "⚠️ This room invite is invalid or no longer available."
                )

            room_status = str(room.get("status") or "").strip().lower()
            if room_status != "waiting":
                return await update.effective_message.reply_text(
                    "⚠️ This room is no longer open for joining."
                )

            context.user_data["mw_room_code"] = room_code
            context.user_data["mw_is_host"] = False

            return await update.effective_message.reply_text(
                "👥 Mock WAEC Room Invite\n\n"
                f"Room Code: {room_code}\n\n"
                "You were invited to join a Mock WAEC multiplayer room.\n"
                "Tap the button below to join this room.",
                reply_markup=make_mockwaec_join_room_keyboard(room_code),
            )


        # -------------------------------------------------------
        # BATTLE LINK HANDLER
        # Example: /start battle_A7K92Q
        # -------------------------------------------------------
        if arg.startswith("battle_"):
            room_code = arg.replace("battle_", "", 1).strip()
            from handlers.battle import battle_join_from_payload
            await battle_join_from_payload(update, context, room_code)
            return

        # -------------------------------------------------------
        # PAYMENT SUCCESS DEEP LINKS
        # -------------------------------------------------------
        if arg.startswith("payok_trivia_"):
            if update.message:
                await update.message.reply_text(
                    "✅ *Payment confirmed!*\n\nOpening *Play Trivia* now...",
                    parse_mode="Markdown",
                )

            from handlers.playtrivia import playtrivia_handler
            await playtrivia_handler(update, context)
            return

        if arg.startswith("payok_jamb_"):
            tx_ref = arg.replace("payok_jamb_", "", 1).strip()

            if update.message:
                await update.message.reply_text(
                    "✅ *Payment confirmed!*\n\nOpening your JAMB topic page now...",
                    parse_mode="Markdown",
                )

            from services.jamb_payments import get_jamb_payment
            from handlers.jambpractice import (
                jambpractice_handler,
                send_jamb_topic_access_screen,
            )

            payment = None
            async with get_async_session() as session:
                payment = await get_jamb_payment(session, tx_ref)

            subject_code = (payment or {}).get("subject_code")
            topic_id = (payment or {}).get("topic_id")

            if subject_code and topic_id and update.effective_message:
                await send_jamb_topic_access_screen(
                    update.effective_message,
                    context,
                    subject_code=str(subject_code),
                    topic_id=str(topic_id),
                    user_id=update.effective_user.id,
                )
                return

            await jambpractice_handler(update, context)
            return

        if arg.startswith("payok_waec_"):
            tx_ref = arg.replace("payok_waec_", "", 1).strip()

            if update.message:
                await update.message.reply_text(
                    "✅ *Payment confirmed!*\n\nOpening your WAEC topic page now...",
                    parse_mode="Markdown",
                )

            from services.waec_payment_finalizer import get_waec_payment
            from handlers.waecpractice import (
                waecpractice_handler,
                send_waec_topic_access_screen,
            )

            payment = None
            async with get_async_session() as session:
                payment = await get_waec_payment(session, tx_ref)

            subject_code = (payment or {}).get("subject_code")
            topic_id = (payment or {}).get("topic_id")

            if subject_code and topic_id and update.effective_message:
                await send_waec_topic_access_screen(
                    update.effective_message,
                    context,
                    subject_code=str(subject_code),
                    topic_id=str(topic_id),
                    user_id=update.effective_user.id,
                )
                return

            await waecpractice_handler(update, context)
            return

        if arg.startswith("payok_mockjamb_"):
            tx_ref = arg.replace("payok_mockjamb_", "", 1).strip()

            if update.message:
                await update.message.reply_text(
                    "✅ *Payment confirmed!*\n\nPreparing your *Mock JAMB / UTME* exam...",
                    parse_mode="Markdown",
                )

            from handlers.mockjamb import mockjamb_payment_success_handler
            await mockjamb_payment_success_handler(update, context, tx_ref)
            return

        if arg.startswith("payok_mockwaec_"):
            tx_ref = arg.replace("payok_mockwaec_", "", 1).strip()

            if update.message:
                await update.message.reply_text(
                    "✅ *Payment confirmed!*\n\nPreparing your *Mock WAEC / NECO* exam...",
                    parse_mode="Markdown",
                )

            from handlers.mockwaec import mockwaec_payment_success_handler
            await mockwaec_payment_success_handler(update, context, tx_ref)
            return

        if arg.startswith("payok_jambmocksubject_"):
            payload = arg.replace("payok_jambmocksubject_", "", 1).strip()

            subject_code = ""
            tx_ref = ""

            if "_" in payload:
                subject_code, tx_ref = payload.split("_", 1)
                subject_code = subject_code.strip().lower()
                tx_ref = tx_ref.strip()
            else:
                tx_ref = payload

            if update.message:
                await update.message.reply_text(
                    "✅ *Payment confirmed!*\n\nOpening your *subject mock screen* now...",
                    parse_mode="Markdown",
                )

            if subject_code:
                from handlers.jambpractice import open_jamb_subject_mock_screen
                await open_jamb_subject_mock_screen(update, context, subject_code)
                return

            from handlers.jambpractice import jambpractice_handler
            await jambpractice_handler(update, context)
            return

        if arg.startswith("payok_waecmocksubject_"):
            payload = arg.replace("payok_waecmocksubject_", "", 1).strip()

            subject_code = ""
            tx_ref = ""

            if "_" in payload:
                subject_code, tx_ref = payload.split("_", 1)
                subject_code = subject_code.strip().lower()
                tx_ref = tx_ref.strip()
            else:
                tx_ref = payload

            if update.message:
                await update.message.reply_text(
                    "✅ *Payment confirmed!*\n\nOpening your *WAEC mock subject screen* now.",
                    parse_mode="Markdown",
                )

            if subject_code:
                from handlers.waecpractice import open_waec_subject_mock_screen
                await open_waec_subject_mock_screen(update, context, subject_code)
                return

            from handlers.waecpractice import waecpractice_handler
            await waecpractice_handler(update, context)
            return

        # -------------------------------------------------------
        # PAYMENT FAILED DEEP LINKS
        # -------------------------------------------------------
        if arg.startswith("payfail_trivia_"):
            if update.message:
                await update.message.reply_text(
                    "❌ *Trivia payment was not completed.*\n\nPlease try again.",
                    parse_mode="Markdown",
                )

            from handlers.payments import buy_menu
            await buy_menu(update, context)
            return

        if arg.startswith("payfail_jamb_"):
            if update.message:
                await update.message.reply_text(
                    "❌ *JAMB payment was not completed.*\n\nPlease try again.",
                    parse_mode="Markdown",
                )

            from handlers.jambpractice import jambpractice_handler
            await jambpractice_handler(update, context)
            return

        if arg.startswith("payfail_waec_"):
            if update.message:
                await update.message.reply_text(
                    "❌ *WAEC payment was not completed.*\n\nPlease try again.",
                    parse_mode="Markdown",
                )

            from handlers.waecpractice import waecpractice_handler
            await waecpractice_handler(update, context)
            return

        if arg.startswith("payfail_mockjamb_"):
            if update.message:
                await update.message.reply_text(
                    "❌ *Mock JAMB / UTME payment was not completed.*\n\nPlease try again.",
                    parse_mode="Markdown",
                )

            from handlers.mockjamb import mockjamb_start_handler
            await mockjamb_start_handler(update, context)
            return

        if arg.startswith("payfail_mockwaec_"):
            if update.message:
                await update.message.reply_text(
                    "❌ *Mock WAEC / NECO payment was not completed.*\n\nPlease try again.",
                    parse_mode="Markdown",
                )

            from handlers.mockwaec import mockwaec_start_handler
            await mockwaec_start_handler(update, context)
            return

        if arg.startswith("payfail_jambmocksubject_"):
            payload = arg.replace("payfail_jambmocksubject_", "", 1).strip()

            subject_code = ""
            if "_" in payload:
                subject_code, _ = payload.split("_", 1)
                subject_code = subject_code.strip().lower()

            if update.message:
                await update.message.reply_text(
                    "❌ *Mock UTME \\(By Subject\\) payment was not completed.*\n\nPlease try again.",
                    parse_mode="MarkdownV2",
                )

            if subject_code:
                from handlers.jambpractice import open_jamb_subject_mock_screen
                await open_jamb_subject_mock_screen(update, context, subject_code)
                return

            from handlers.jambpractice import jambpractice_handler
            await jambpractice_handler(update, context)
            return

        if arg.startswith("payfail_waecmocksubject_"):
            payload = arg.replace("payfail_waecmocksubject_", "", 1).strip()

            subject_code = ""
            if "_" in payload:
                subject_code, _ = payload.split("_", 1)
                subject_code = subject_code.strip().lower()

            if update.message:
                await update.message.reply_text(
                    "❌ *Mock WAEC / NECO \\(By Subject\\) payment was not completed.*\n\nPlease try again.",
                    parse_mode="MarkdownV2",
                )

            if subject_code:
                from handlers.waecpractice import open_waec_subject_mock_screen
                await open_waec_subject_mock_screen(update, context, subject_code)
                return

            from handlers.waecpractice import waecpractice_handler
            await waecpractice_handler(update, context)
            return

        # -------------------------------------------------------
        # CHALLENGE LINK HANDLER
        # -------------------------------------------------------
        if arg.startswith("challenge_"):
            challenge_id = arg.split("_", 1)[1]

            if update.message:
                await update.message.reply_text(
                    "⚔️ *Friend Challenge Invitation*\n\n"
                    "You were invited to compete in a trivia challenge!\n\n"
                    f"Challenge ID: `{challenge_id}`\n\n"
                    "Press *Challenge Friends* or *Play Trivia Questions* to continue.",
                    parse_mode="Markdown",
                )
            return

    # ===========================================================
    # SUPPORT FLOW SAFETY GUARD
    # ===========================================================
    if (context.user_data or {}).get("in_support_flow"):
        msg_text = ""
        if update.message and update.message.text:
            msg_text = update.message.text.strip()

        if not msg_text.startswith("/start"):
            return

        context.user_data.pop("in_support_flow", None)

    text = build_start_text(user.first_name or "there")
    markup = build_main_menu_keyboard()

    if update.message:
        await update.message.reply_text(
            text,
            reply_markup=markup,
            parse_mode="MarkdownV2",
        )
        return

    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass

        try:
            await update.callback_query.edit_message_text(
                text,
                reply_markup=markup,
                parse_mode="MarkdownV2",
            )
        except Exception:
            await update.callback_query.message.reply_text(
                text,
                reply_markup=markup,
                parse_mode="MarkdownV2",
            )
        return


# ===============================================================
# GO BACK TO MAIN MENU
# ===============================================================
async def go_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    await start(update, context)


# ===============================================================
# /help
# ===============================================================
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🆘 *How to Play*\n\n"
        "1️⃣ Select *Play Trivia Questions*\n"
        "2️⃣ Choose a category\n"
        "3️⃣ Answer correctly to earn reward points\n"
        "4️⃣ Climb the leaderboard and compete for prizes\n\n"
        "🎁 Top player this cycle can win:\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🏆 *GRAND PRIZE*\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "📱 *iPhone 18 Pro Max*\n\n"
        "📱 *Samsung Galaxy S26 Ultra*\n\n"
        "🏆 *The winner chooses one of the two phones\\.*\n\n\n\n"
        "*Other Milestone Rewards*\n\n"
        "📞 *Instant Airtime Rewards*\n\n"
        "🎧 *AirPods*\n\n"
        "🔊 *Bluetooth Speakers*\n\n"
        "⌚ *Smart Watch*\n\n"
        "📱 *Android Smartphone*\n\n\n\n"
        "Use the buttons below to continue 👇"
    )

    await update.message.reply_text(
        text,
        reply_markup=build_main_menu_keyboard(),
        parse_mode="MarkdownV2",
    )


# ===============================================================
# /mytries
# ===============================================================
async def mytries(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_user = update.effective_user

    async with get_async_session() as session:
        db_user = await get_or_create_user(
            session,
            tg_id=tg_user.id,
            username=tg_user.username,
        )

        text = (
            f"📊 *Your Question Credits*\n\n"
            f"• Paid: `{db_user.tries_paid or 0}`\n"
            f"• Free: `{db_user.tries_bonus or 0}`\n\n"
            "_Questions = Chances to earn more reward points_ 🎯"
        )

    await update.message.reply_text(text, parse_mode="MarkdownV2")


# ===============================================================
# Smart Fallback (LAST handler)
# ===============================================================
async def fallback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("_in_conversation"):
        return

    if not update.message or not update.message.text:
        return

    text_msg = update.message.text.strip()

    if text_msg.startswith("/"):
        return

    if re.fullmatch(r"^[0-9+ ]+$", text_msg):
        return

    intent = detect_user_intent(text_msg)

    if intent:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➡️ Continue", callback_data=intent)]
        ])

        await update.message.reply_text(
            md_escape(
                "🤖 I think I understand what you mean.\n\n"
                "Tap below to continue."
            ),
            reply_markup=keyboard,
            parse_mode="MarkdownV2",
        )


# ===============================================================
# Intent Detector
# ===============================================================
def detect_user_intent(text: str):
    text = text.lower()

    challenge_words = [
        "challenge", "challenge friend", "challenge friends", "friend challenge"
    ]

    battle_words = [
        "battle", "battle mode", "multiplayer"
    ]

    support_words = [
        "support", "contact", "admin", "help desk"
    ]

    trivia_words = [
        "play", "trivia", "question", "questions",
        "quiz", "game", "answer"
    ]

    payment_words = [
        "subscribe", "attempt", "attempts", "buy"
    ]

    faq_words = [
        "faq", "rule", "rules", "terms",
        "guide", "instruction"
    ]

    leaderboard_words = [
        "leaderboard", "rank", "ranking",
        "score", "top", "winner"
    ]

    free_words = [
        "free", "bonus", "earn"
    ]

    menu_words = [
        "menu", "other menu"
    ]

    if any(word in text for word in challenge_words):
        return "challenge:start"

    if any(word in text for word in battle_words):
        return "battle:menu"

    if any(word in text for word in support_words):
        return "support:start"

    if any(word in text for word in trivia_words):
        return "playtrivia"

    if any(word in text for word in payment_words):
        return "buy"

    if any(word in text for word in faq_words):
        return "faq"

    if any(word in text for word in leaderboard_words):
        return "leaderboard:show"

    if any(word in text for word in free_words):
        return "free"

    if any(word in text for word in menu_words):
        return "menu:other"

    return None


# ===============================================================
# Register Handlers
# ===============================================================
def register_handlers(application):
    # ---------------------------------------------------
    # Commands (BLOCK propagation)
    # ---------------------------------------------------
    application.add_handler(CommandHandler("start", start, block=True))
    application.add_handler(CommandHandler("help", help_cmd, block=True))
    application.add_handler(CommandHandler("mytries", mytries, block=True))
    application.add_handler(CommandHandler("terms", terms_handler, block=True))
    application.add_handler(CommandHandler("faq", faq_handler, block=True))

    # ---------------------------------------------------
    # Callback buttons
    # ---------------------------------------------------
    application.add_handler(CallbackQueryHandler(entertainment_menu_handler, pattern=r"^menu:entertainment$"))
    application.add_handler(CallbackQueryHandler(education_menu_handler, pattern=r"^menu:education$"))
    application.add_handler(CallbackQueryHandler(exam_hub_handler, pattern=r"^exam:hub$"))
    application.add_handler(CallbackQueryHandler(other_menu_handler, pattern=r"^menu:other$"))
    application.add_handler(CallbackQueryHandler(go_start_callback, pattern=r"^menu:main$"))
    application.add_handler(CallbackQueryHandler(go_start_callback, pattern=r"^go_start$"))
    application.add_handler(CallbackQueryHandler(terms_handler, pattern=r"^terms$"))
    application.add_handler(CallbackQueryHandler(faq_handler, pattern=r"^faq(?::page2)?$"))

    # ---------------------------------------------------
    # Leaderboard
    # ---------------------------------------------------
    from handlers.leaderboard import register_leaderboard_handlers
    register_leaderboard_handlers(application)

    # ---------------------------------------------------
    # Fallback
    # ---------------------------------------------------
    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND
            & ~filters.Regex(r"^[0-9+ ]+$")
            & ~filters.UpdateType.EDITED_MESSAGE,
            fallback,
        ),
        group=20,
    )

