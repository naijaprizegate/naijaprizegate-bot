# =========================================
# handlers/finance.py
# ========================================

from __future__ import annotations

import html
import logging
import os
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from db import get_async_session
from helpers import get_or_create_user
from finance_models import WithdrawalEligibilitySessionORM
from services.finance.reporting_service import (
    get_wallet_summary,
    get_wallet_transactions,
    get_referral_report,
    get_withdrawal_report,
)
from services.finance.wallet_service import get_or_create_wallet
from services.finance.premium_points import (
    calculate_required_points,
    start_withdrawal_eligibility,
    validate_eligibility_session,
)
from services.finance.withdrawal_service import create_withdrawal_request
from services.finance.bank_account_service import (
    get_user_bank_accounts,
    get_verified_bank_account,
    create_bank_account,
)
from services.flutterwave_client import (
    get_ng_banks,
    resolve_bank_account,
)

logger = logging.getLogger(__name__)

MENU, AMOUNT, ACCOUNT_NAME, ACCOUNT_NUMBER, BANK_NAME, BANK_SELECT, BANK_SEARCH = range(7)

FINANCE_OPEN = "finance:open"
FINANCE_MENU = "finance:menu"
FINANCE_INVITE = "finance:invite"
FINANCE_WALLET = "finance:wallet"
FINANCE_REFERRALS = "finance:referrals"
FINANCE_WITHDRAWALS = "finance:withdrawals"
FINANCE_TRANSACTIONS = "finance:transactions"
FINANCE_WITHDRAW = "finance:withdraw"
FINANCE_PROGRESS = "finance:progress"
FINANCE_BANK_ACCOUNT = "finance:bank_account"
FINANCE_BANK_ADD = "finance:bank:add"
FINANCE_USE_SAVED_BANK = "finance:bank:use_saved"
FINANCE_BANK_SEARCH = "finance:bank:search"
FINANCE_BANK_SELECT = "finance:bank:select"
FINANCE_BANK_PAGE = "finance:bank:page"
FINANCE_SUBMIT = "finance:submit"
FINANCE_CANCEL = "finance:cancel"

WITHDRAWAL_UNIT = Decimal("2000.00")


# ============================================================
# IDENTITY / DISPLAY HELPERS
# ============================================================

async def _get_application_user(update: Update, session):
    """
    Resolve the Telegram identity to the application's User row.

    NEVER pass update.effective_user.id directly to Finance services.
    """
    telegram_user = update.effective_user
    if telegram_user is None:
        return None

    return await get_or_create_user(
        session=session,
        tg_id=telegram_user.id,
        username=telegram_user.username,
        full_name=telegram_user.full_name,
    )


def _money(value) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0")
    return f"₦{amount:,.2f}"


def _menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Invite Friends", callback_data=FINANCE_INVITE)],
        [InlineKeyboardButton("💰 Referral Wallet", callback_data=FINANCE_WALLET)],
        [InlineKeyboardButton("👥 My Referrals", callback_data=FINANCE_REFERRALS)],
        [InlineKeyboardButton("📜 Withdrawal History", callback_data=FINANCE_WITHDRAWALS)],
        [InlineKeyboardButton("📈 Eligibility / Progress", callback_data=FINANCE_PROGRESS)],
        [InlineKeyboardButton("🏦 Bank Account", callback_data=FINANCE_BANK_ACCOUNT)],
        [InlineKeyboardButton("🔙 Back", callback_data=FINANCE_CANCEL)],
    ])


def _wallet_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💸 Withdraw", callback_data=FINANCE_WITHDRAW)],
        [InlineKeyboardButton("📜 Transactions", callback_data=FINANCE_TRANSACTIONS)],
        [InlineKeyboardButton("📈 Eligibility / Progress", callback_data=FINANCE_PROGRESS)],
        [InlineKeyboardButton("🔙 Finance Menu", callback_data=FINANCE_MENU)],
    ])


def _progress_keyboard(
    completed: bool,
    expired: bool,
):
    rows = []

    if completed:
        rows.append([
            InlineKeyboardButton(
                "🏦 Enter Bank Details",
                callback_data=FINANCE_SUBMIT,
            )
        ])
    elif not expired:
        rows.append([
            InlineKeyboardButton(
                "🎯 Play Trivia & Earn Points",
                callback_data="playtrivia",
            )
        ])

    rows.extend([
        [
            InlineKeyboardButton(
                "🔄 Refresh Progress",
                callback_data=FINANCE_PROGRESS,
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Finance Menu",
                callback_data=FINANCE_MENU,
            )
        ],
    ])

    return InlineKeyboardMarkup(rows)


def _withdrawal_amount_keyboard(
    available_balance: Decimal,
) -> InlineKeyboardMarkup:
    buttons = []

    max_units = int(available_balance // WITHDRAWAL_UNIT)

    for unit in range(1, max_units + 1):
        amount = WITHDRAWAL_UNIT * unit

        buttons.append(
            InlineKeyboardButton(
                f"💰 {_money(amount)}",
                callback_data=f"finance:amount:{int(amount)}",
            )
        )

    rows = [
        buttons[index:index + 2]
        for index in range(0, len(buttons), 2)
    ]

    rows.append([
        InlineKeyboardButton(
            "❌ Cancel",
            callback_data=FINANCE_CANCEL,
        )
    ])

    return InlineKeyboardMarkup(rows)


async def _show(update: Update, text: str, markup=None):
    query = update.callback_query
    if query:
        await query.answer()

        try:
            await query.edit_message_text(
                text,
                reply_markup=markup,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except BadRequest as exc:
            if "Message is not modified" not in str(exc):
                raise

        return

    message = update.effective_message
    if message:
        await message.reply_text(
            text,
            reply_markup=markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


async def _get_current_eligibility(update: Update, context):
    """
    Resolve the application's UUID first, then locate/validate the
    eligibility session using that UUID.
    """
    stored_id = context.user_data.get("finance_eligibility_session_id")

    async with get_async_session() as session:
        user = await _get_application_user(update, session)
        if user is None:
            return None

        if stored_id:
            try:
                session_id = UUID(str(stored_id))
                return await validate_eligibility_session(
                    session=session,
                    user_id=user.id,
                    session_id=session_id,
                )
            except (ValueError, TypeError):
                context.user_data.pop("finance_eligibility_session_id", None)

        result = await session.execute(
            select(WithdrawalEligibilitySessionORM)
            .where(WithdrawalEligibilitySessionORM.user_id == user.id)
            .order_by(WithdrawalEligibilitySessionORM.started_at.desc())
            .limit(1)
        )
        eligibility = result.scalar_one_or_none()

        if eligibility is not None:
            context.user_data["finance_eligibility_session_id"] = str(
                eligibility.id
            )

        return eligibility


# ============================================================
# FINANCE MENU
# ============================================================

async def show_finance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _show(
        update,
        "💰 <b>Finance &amp; Rewards</b>\n\n"
        "💰 <b>Refer &amp; Earn</b>\n\n"
        "Earn <b>₦5 for every ₦100</b> spent by your "
        "qualifying referrals.\n\n\n\n"
        "🎯 <b>Play &amp; Win</b>\n\n"
        "Answer trivia questions correctly, earn Premium Points "
        "and climb the leaderboard to unlock exciting rewards.\n\n"
        "🎁 <b>Rewards include:</b>\n\n"
        "• Airtime\n\n"
        "• AirPods\n\n"
        "• Bluetooth Speakers\n\n"
        "• Latest iPhone &amp; Samsung Phones\n\n"
        "💸 <b>Withdrawals start from ₦2,000.</b>\n\n"
        "Every ₦2,000 withdrawal requires <b>4 Premium Points</b> "
        "for qualification.\n\n\n\n"
        "Choose an option below:",
        _menu_keyboard(),
    )
    return MENU


async def show_invite_friends(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if query:
        await query.answer()

    tg_user = update.effective_user
    if tg_user is None:
        return MENU

    async with get_async_session() as session:
        user = await _get_application_user(update, session)

        if user is None:
            return MENU

    # Use the application's user UUID as the referral identifier,
    # matching the existing referral-link format.
    ref_link = (
        f"https://t.me/{os.getenv('BOT_USERNAME', 'NaijaPrizeGateBot')}"
        f"?start={user.id}"
    )

    display_name = html.escape(
        tg_user.first_name or tg_user.username or "Friend"
    )

    # Plain-text share message because Telegram does not parse
    # HTML inside switch_inline_query.
    share_name = (
        tg_user.first_name or tg_user.username or "Friend"
    ).strip() or "Friend"

    share_message = (
        f"🔥 Hey, it’s {share_name}!\n\n"
        "Join me on NaijaPrizeGate — play, earn rewards "
        "and enjoy exciting trivia challenges! 🎯\n\n"
        "🏆 Play, earn Premium Points and compete for exciting "
        "rewards including airtime, AirPods, Bluetooth Speakers "
        "and the latest iPhone & Samsung Phones.\n\n"
        "Invite friends, build your network and unlock "
        "Finance & Rewards opportunities. 💰\n\n"
        f"Join me now 👇\n{ref_link}"
    )

    text = (
        f"🔗 <b>Invite Friends</b>\n\n"
        f"Hey {display_name}! 👋\n\n"
        "💰 <b>Refer &amp; Earn</b>\n\n"
        "For every <b>₦100</b> spent by your qualifying "
        "referrals, you earn <b>₦5</b>.\n\n"
        "💳 Your earnings accumulate in your "
        "<b>Referral Wallet</b> and can be withdrawn "
        "from <b>₦2,000</b>.\n\n"
        "🎁 Keep playing and earning Premium Points to "
        "unlock exciting rewards such as airtime, AirPods, "
        "Bluetooth Speakers, and the latest <b>iPhone & Samsung Phones</b>.\n\n"
        "🔗 <b>Your personal referral link:</b>\n"
        f"{html.escape(ref_link)}\n\n"
        "Share your link and start earning. 🚀"
    )

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "👥 Share Referral",
                switch_inline_query=share_message,
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Back",
                callback_data=FINANCE_MENU,
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data=FINANCE_CANCEL,
            )
        ],
    ])

    if query:
        await query.edit_message_text(
            text,
            reply_markup=markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    return MENU


async def show_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with get_async_session() as session:
        user = await _get_application_user(update, session)
        if user is None:
            return MENU

        wallet = await get_wallet_summary(session, user.id)

    await _show(
        update,
        "💰 <b>Referral Wallet</b>\n\n"
        f"Balance: <b>{_money(wallet.balance)}</b>\n"
        "-------------\n\n"
        f"Available: <b>{_money(wallet.available_balance)}</b>\n"
        "---------------\n\n"
        f"Total Earned: <b>{_money(wallet.total_earned)}</b>\n"
        "--------------------\n\n"
        f"Withdrawn: <b>{_money(wallet.total_withdrawn)}</b>\n"
        "-----------------\n\n"
        f"Pending Withdrawals: <b>{_money(wallet.pending_withdrawals)}</b>\n"
        "--------------------------\n\n"
        f"Eligible Premium Points: <b>{wallet.eligible_points}</b>\n"
        "-------------------------\n\n"
        f"Reserved Premium Points: <b>{wallet.reserved_points}</b>\n"
        "--------------------------\n\n"
        f"Available Premium Points: <b>{wallet.available_points}</b>\n\n"
        "💡 <b>Withdrawal Guide</b>\n"
        "Every ₦2,000 you withdraw requires "
        "<b>4 Premium Points</b>.",
        _wallet_keyboard(),
    )
    return MENU


async def show_transactions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with get_async_session() as session:
        user = await _get_application_user(update, session)
        if user is None:
            return MENU
        transactions = await get_wallet_transactions(
            session, user.id, limit=20
        )

    if not transactions:
        body = (
            "No wallet transactions yet.\n\n"
            "💰 Start referring friends to begin earning "
            "₦5 for every ₦100 they spend."
        )
    else:
        lines = []
        for tx in transactions:
            amount = Decimal(str(tx.amount))
            sign = "+" if amount >= 0 else ""
            description = html.escape(
                getattr(tx, "description", None)
                or getattr(tx, "transaction_type", None)
                or "Finance transaction"
            )

            lines.append(
                f"{sign}{_money(amount)} — {description}\n"
                "------------------------------"
            )

        body = "\n\n".join(lines)

    await _show(
        update,
        f"📜 <b>Wallet Transactions</b>\n\n{body}",
        InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🔙 Referral Wallet", callback_data=FINANCE_WALLET
            )],
            [InlineKeyboardButton(
                "🔙 Finance Menu", callback_data=FINANCE_MENU
            )],
        ]),
    )
    return MENU


async def show_referrals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with get_async_session() as session:
        user = await _get_application_user(update, session)
        if user is None:
            return MENU
        report = await get_referral_report(session, user.id)

    await _show(
        update,
        "👥 <b>My Referrals</b>\n\n"
        "Your referrals are the people who joined "
        "NaijaPrizeGate through your referral link.\n\n"
        "💰 When qualifying referrals spend, you earn "
        "<b>₦5 for every ₦100</b>.\n\n"
        f"Total Referrals: <b>{report.total_referrals}</b>\n"
        "-------------------\n\n"
        f"Active: <b>{report.active_referrals}</b>\n"
        "-------\n\n"
        f"Pending: <b>{report.pending_referrals}</b>\n"
        "---------\n\n"
        f"Inactive: <b>{report.inactive_referrals}</b>",
        InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "🔙 Finance Menu", callback_data=FINANCE_MENU
            )
        ]]),
    )
    return MENU


async def show_withdrawals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with get_async_session() as session:
        user = await _get_application_user(update, session)
        if user is None:
            return MENU
        report = await get_withdrawal_report(session, user.id)

    await _show(
        update,
        "📜 <b>Withdrawal History</b>\n\n"
        f"Requests: <b>{report.total_requests}</b>\n"
        "---------\n\n"
        f"Pending: <b>{_money(report.pending_amount)}</b>\n"
        "---------\n\n"
        f"Approved: <b>{_money(report.approved_amount)}</b>\n"
        "----------\n\n"
        f"Completed: <b>{_money(report.completed_amount)}</b>\n"
        "-----------\n\n"
        f"Rejected: <b>{_money(report.rejected_amount)}</b>\n"
        "----------\n\n"
        f"Cancelled: <b>{_money(report.cancelled_amount)}</b>",
        InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🔙 Referral Wallet", callback_data=FINANCE_WALLET
            )],
            [InlineKeyboardButton(
                "🔙 Finance Menu", callback_data=FINANCE_MENU
            )],
        ]),
    )
    return MENU


# ============================================================
# WITHDRAWAL / ELIGIBILITY FLOW
# ============================================================

async def begin_withdrawal(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    async with get_async_session() as session:
        user = await _get_application_user(update, session)
        if user is None:
            return MENU

        wallet = await get_wallet_summary(session, user.id)

    available = Decimal(str(wallet.available_balance))

    if available < WITHDRAWAL_UNIT:
        await _show(
            update,
            "💸 <b>Withdrawal</b>\n\n"
            f"Available Balance: <b>{_money(available)}</b>\n"
            "----------------------------\n\n"
            "Minimum withdrawal: <b>₦2,000</b>\n\n"
            "💰 Keep referring friends to grow your wallet.\n"
            "Earn <b>₦5 for every ₦100</b> spent by your "
            "qualifying referrals.\n\n"
            "📈 Once you reach ₦2,000, you can start a "
            "withdrawal qualification.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🔙 Referral Wallet",
                    callback_data=FINANCE_WALLET,
                )
            ]]),
        )
        return MENU

    await _show(
        update,
        "💸 <b>Start Withdrawal</b>\n\n"
        f"Available Balance: <b>{_money(available)}</b>\n"
        "----------------------------\n\n"
        "💡 <b>Withdrawal Rule</b>\n\n"
        "Withdrawals are available from"
        "<b>₦2,000</b> and above.\n\n"
        "📈 Every <b>₦2,000</b> withdrawn requires "
        "<b>4 Premium Points</b> for qualification.\n\n"
        "Select the amount you want to withdraw:",
        _withdrawal_amount_keyboard(available),
    )

    return AMOUNT


async def select_withdrawal_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return AMOUNT

    await query.answer()

    callback_data = query.data or ""

    try:
        amount_text = callback_data.split(":", 2)[2]
        amount = Decimal(amount_text)
    except (IndexError, InvalidOperation, ValueError):
        await _show(
            update,
            "❌ <b>Invalid Withdrawal Amount</b>\n\n"
            "Please select a withdrawal amount from the available options.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🔙 Referral Wallet",
                    callback_data=FINANCE_WALLET,
                )
            ]]),
        )
        return MENU

    # ---------------------------------------------------------
    # SECURITY: Re-read the current wallet balance.
    # Never trust the amount simply because it came from a
    # previously displayed button.
    # ---------------------------------------------------------
    async with get_async_session() as session:
        user = await _get_application_user(update, session)

        if user is None:
            return MENU

        wallet = await get_wallet_summary(session, user.id)

    available = Decimal(str(wallet.available_balance))

    # ---------------------------------------------------------
    # Validate the selected amount against the current rules.
    # ---------------------------------------------------------
    if amount < WITHDRAWAL_UNIT:
        await _show(
            update,
            "❌ <b>Invalid Withdrawal Amount</b>\n\n"
            "The minimum withdrawal amount is <b>₦2,000</b>.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "💸 Select Withdrawal Amount",
                    callback_data=FINANCE_WITHDRAW,
                )
            ]]),
        )
        return MENU

    if amount % WITHDRAWAL_UNIT != 0:
        await _show(
            update,
            "❌ <b>Invalid Withdrawal Amount</b>\n\n"
            "Withdrawal amounts must be in multiples of <b>₦2,000</b>.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "💸 Select Withdrawal Amount",
                    callback_data=FINANCE_WITHDRAW,
                )
            ]]),
        )
        return MENU

    if amount > available:
        await _show(
            update,
            "❌ <b>Withdrawal Amount No Longer Available</b>\n\n"
            f"Selected Amount: <b>{_money(amount)}</b>\n"
            "-----------------\n\n"
            f"Current Available Balance: <b>{_money(available)}</b>\n"
            "----------------------------\n\n"
            "Your available balance has changed. "
            "Please select a new withdrawal amount.",
            _withdrawal_amount_keyboard(available)
            if available >= WITHDRAWAL_UNIT
            else InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🔙 Referral Wallet",
                    callback_data=FINANCE_WALLET,
                )
            ]]),
        )
        return AMOUNT if available >= WITHDRAWAL_UNIT else MENU

    # ---------------------------------------------------------
    # Existing Finance qualification calculation remains
    # authoritative.
    # ---------------------------------------------------------
    try:
        required_points = calculate_required_points(amount)
    except ValueError as exc:
        await _show(
            update,
            f"❌ {html.escape(str(exc))}",
            InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "💸 Select Withdrawal Amount",
                    callback_data=FINANCE_WITHDRAW,
                )
            ]]),
        )
        return MENU

    try:
        # -----------------------------------------------------
        # SECURITY: Final balance verification.
        #
        # This read intentionally happens in its own session.
        # get_wallet_summary() performs a SELECT, which causes
        # SQLAlchemy to begin a transaction automatically.
        # We therefore must NOT call session.begin() on this
        # same session afterward.
        # -----------------------------------------------------
        async with get_async_session() as session:
            user = await _get_application_user(update, session)

            if user is None:
                raise ValueError(
                    "Unable to identify your account."
                )

            wallet = await get_wallet_summary(
                session,
                user.id,
            )

            current_available = Decimal(
                str(wallet.available_balance)
            )

            if amount > current_available:
                raise ValueError(
                    "Your available balance has changed. "
                    "Please select a new withdrawal amount."
                )

        # -----------------------------------------------------
        # ATOMIC WRITE TRANSACTION
        #
        # Use a completely fresh session so that session.begin()
        # starts a clean transaction.
        # -----------------------------------------------------
        async with get_async_session() as session:
            async with session.begin():
                user = await _get_application_user(
                    update,
                    session,
                )

                if user is None:
                    raise ValueError(
                        "Unable to identify your account."
                    )

                wallet_row = await get_or_create_wallet(
                    session,
                    user.id,
                )

                # Final authoritative balance check against the
                # wallet row used by the write transaction.
                current_available = (
                    wallet_row.balance
                    - wallet_row.total_pending_withdrawals
                )

                if amount > current_available:
                    raise ValueError(
                        "Your available balance has changed. "
                        "Please select a new withdrawal amount."
                    )

                eligibility = await start_withdrawal_eligibility(
                    session=session,
                    user_id=user.id,
                    wallet_id=wallet_row.id,
                    amount=amount,
                )

    except ValueError as exc:
        await _show(
            update,
            f"❌ {html.escape(str(exc))}",
            InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "💸 Select Withdrawal Amount",
                    callback_data=FINANCE_WITHDRAW,
                )
            ]]),
        )
        return MENU

    except Exception:
        logger.exception(
            "Failed to start Finance withdrawal eligibility."
        )
        await _show(
            update,
            "❌ <b>We could not start the withdrawal "
            "qualification session.</b>\n\n"
            "Please try again.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "💰 Finance Menu",
                    callback_data=FINANCE_MENU,
                )
            ]]),
        )
        return MENU

    context.user_data["finance_eligibility_session_id"] = str(
        eligibility.id
    )
    context.user_data["finance_withdrawal_amount"] = str(amount)

    await _show(
        update,
        "✅ <b>Withdrawal Qualification Started</b>\n\n"
        f"Withdrawal Amount: <b>{_money(amount)}</b>\n"
        "----------------------------\n\n"
        f"Required Premium Points: <b>{required_points}</b>\n"
        "---------------------------\n\n"
        "Points Earned: <b>0</b>\n"
        "----------------\n\n"
        "💡 You need <b>4 Premium Points for every "
        "₦2,000</b> you want to withdraw.\n\n"
        "⏱️ You have one hour to complete your "
        "withdrawal qualification session.\n\n"
        "📈 Earn Premium Points by answering trivia questions ",
        InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🎯 Play Trivia & Earn Points",
                callback_data="playtrivia",
            )],
            [InlineKeyboardButton(
                "📈 Check Progress",
                callback_data=FINANCE_PROGRESS,
            )],
            [InlineKeyboardButton(
                "❌ Cancel",
                callback_data=FINANCE_CANCEL,
            )],
        ]),
    )

    return MENU


async def show_progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        eligibility = await _get_current_eligibility(update, context)
    except Exception:
        logger.exception("Failed to validate Withdrawal eligibility session.")
        await _show(
            update,
            "❌ <b>Unable to check eligibility.</b>\n\nPlease try again.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🔙 Finance Menu", callback_data=FINANCE_MENU
                )
            ]]),
        )
        return MENU

    if eligibility is None:
        await _show(
            update,
            "📈 <b>Withdrawal Eligibility</b>\n\n"
            "No withdrawal qualification session is currently available.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "💸 Start Withdrawal", callback_data=FINANCE_WITHDRAW
                )
            ]]),
        )
        return MENU

    status = str(eligibility.status).upper()
    required = int(eligibility.required_points)
    earned = int(eligibility.points_earned)
    completed = status == "COMPLETED"
    expired = status == "EXPIRED"

    if completed:
        status_text = "✅ <b>QUALIFIED</b>"
        action = (
            "You can now enter your bank details "
            "and submit the withdrawal."
        )
    elif status == "EXPIRED":
        status_text = "⏰ <b>EXPIRED</b>"
        action = (
            "This qualification session has expired. "
            "Start a new one."
        )
    else:
        status_text = "⏳ <b>IN PROGRESS</b>"
        action = (
            "Keep earning Premium Points to complete your "
            "withdrawal qualification, then refresh this screen."

        )

    await _show(
        update,
        "📈 <b>Withdrawal Eligibility</b>\n\n"
        f"Withdrawal Amount: <b>{_money(eligibility.requested_amount)}</b>\n"
        "----------------------------\n\n"
        f"Required Points: <b>{required}</b>\n"
        "-------------------\n\n"
        f"Points Earned: <b>{earned}</b>\n"
        "------------------\n\n"
        f"Status: {status_text}\n"
        "---------------------\n\n"
        f"{action}",
        _progress_keyboard(
            completed=completed,
            expired=expired,
        ),
    )
    return MENU


# ============================================================
# BANK DETAILS / SUBMISSION
# ============================================================

async def begin_submission(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    try:
        eligibility = await _get_current_eligibility(update, context)
    except Exception:
        logger.exception(
            "Failed to validate eligibility before submission."
        )
        eligibility = None

    if eligibility is None or str(eligibility.status).upper() != "COMPLETED":
        await _show(
            update,
            "❌ <b>Withdrawal Not Ready</b>\n\n"
            "Your Premium Point qualification has not completed yet.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "📈 Check Progress",
                    callback_data=FINANCE_PROGRESS,
                )
            ]]),
        )
        return MENU

    async with get_async_session() as session:
        user = await _get_application_user(
            update,
            session,
        )

        if user is None:
            await _show(
                update,
                "❌ <b>Account Not Found</b>\n\n"
                "We could not identify your Finance account.\n\n"
                "Please try again.",
                InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "🔙 Finance Menu",
                        callback_data=FINANCE_MENU,
                    )
                ]]),
            )
            return MENU

        accounts = await get_user_bank_accounts(
            session=session,
            user_id=user.id,
        )

    verified_accounts = [
        account
        for account in accounts
        if account.is_verified and account.is_active
    ]

    if not verified_accounts:
        await _show(
            update,
            "🏦 <b>Bank Account Required</b>\n\n"
            "You need a verified bank account before "
            "you can submit a withdrawal.\n\n"
            "Please add and verify your bank account first.",
            InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🏦 Add Bank Account",
                        callback_data=FINANCE_BANK_ADD,
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Finance Menu",
                        callback_data=FINANCE_MENU,
                    )
                ],
            ]),
        )
        return MENU

    # ---------------------------------------------------------
    # Use the user's default verified account when available.
    # Otherwise use the first verified active account.
    # ---------------------------------------------------------

    account = next(
        (
            item
            for item in verified_accounts
            if item.is_default
        ),
        verified_accounts[0],
    )

    masked_account = (
        f"••••{account.account_number[-4:]}"
        if len(account.account_number) >= 4
        else "••••"
    )

    context.user_data["finance_bank_account_id"] = str(account.id)

    await _show(
        update,
        "🏦 <b>Withdrawal Bank Account</b>\n\n"
        f"Bank: <b>{html.escape(account.bank_name)}</b>\n"
        f"Account Name: <b>{html.escape(account.account_name)}</b>\n"
        f"Account: <b>{html.escape(masked_account)}</b>\n\n"
        "Use this verified bank account for your withdrawal?",
        InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Use This Account",
                    callback_data=FINANCE_USE_SAVED_BANK,
                )
            ],
            [
                InlineKeyboardButton(
                    "🏦 Use Another Bank Account",
                    callback_data=FINANCE_BANK_ADD,
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data=FINANCE_CANCEL,
                )
            ],
        ]),
    )

    return MENU


async def use_saved_bank_account(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return MENU

    await query.answer()

    stored_account_id = context.user_data.get(
        "finance_bank_account_id"
    )

    if not stored_account_id:
        await query.edit_message_text(
            "❌ <b>Bank Account Selection Expired</b>\n\n"
            "Please start the withdrawal flow again.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🔙 Finance Menu",
                    callback_data=FINANCE_MENU,
                )
            ]]),
        )
        return MENU

    try:
        account_id = UUID(str(stored_account_id))
    except (ValueError, TypeError):
        await query.edit_message_text(
            "❌ <b>Invalid Bank Account</b>\n\n"
            "Please start the withdrawal flow again.",
            parse_mode="HTML",
        )
        return MENU

    async with get_async_session() as session:
        user = await _get_application_user(
            update,
            session,
        )

        if user is None:
            await query.edit_message_text(
                "❌ <b>Account Not Found</b>\n\n"
                "We could not identify your Finance account.",
                parse_mode="HTML",
            )
            return MENU

        account = await get_verified_bank_account(
            session=session,
            user_id=user.id,
            account_id=account_id,
        )

    if account is None:
        await query.edit_message_text(
            "❌ <b>Bank Account Unavailable</b>\n\n"
            "This bank account is no longer available "
            "or is no longer verified.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🏦 Add Bank Account",
                        callback_data=FINANCE_BANK_ADD,
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Finance Menu",
                        callback_data=FINANCE_MENU,
                    )
                ],
            ]),
        )
        return MENU

    context.user_data["finance_bank_account_id"] = str(account.id)

    await query.edit_message_text(
        "🏦 <b>Bank Account Selected</b>\n\n"
        f"Bank: <b>{html.escape(account.bank_name)}</b>\n"
        f"Account Name: <b>{html.escape(account.account_name)}</b>\n"
        f"Account: <b>••••{html.escape(account.account_number[-4:])}</b>\n\n"
        "Submitting your withdrawal...",
        parse_mode="HTML",
    )

    return await submit_with_saved_bank_account(
        update,
        context,
    )


async def submit_with_saved_bank_account(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    stored_session_id = context.user_data.get(
        "finance_eligibility_session_id"
    )
    stored_account_id = context.user_data.get(
        "finance_bank_account_id"
    )

    if not stored_session_id or not stored_account_id:
        await _show(
            update,
            "❌ <b>Withdrawal Session Expired</b>\n\n"
            "Please start the withdrawal flow again.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🔙 Finance Menu",
                    callback_data=FINANCE_MENU,
                )
            ]]),
        )
        return MENU

    try:
        session_id = UUID(str(stored_session_id))
        bank_account_id = UUID(str(stored_account_id))
    except (ValueError, TypeError):
        await _show(
            update,
            "❌ <b>Invalid Withdrawal Session</b>\n\n"
            "Please start the withdrawal flow again.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🔙 Finance Menu",
                    callback_data=FINANCE_MENU,
                )
            ]]),
        )
        return MENU

    try:
        async with get_async_session() as session:
            user = await _get_application_user(
                update,
                session,
            )

            if user is None:
                raise ValueError(
                    "Unable to identify your account."
                )

            async with session.begin():
                eligibility = await validate_eligibility_session(
                    session=session,
                    user_id=user.id,
                    session_id=session_id,
                )

                if str(eligibility.status).upper() != "COMPLETED":
                    raise ValueError(
                        "Withdrawal eligibility session has not "
                        "completed qualification."
                    )

                account = await get_verified_bank_account(
                    session=session,
                    user_id=user.id,
                    account_id=bank_account_id,
                )

                if account is None:
                    raise ValueError(
                        "The selected bank account is no longer "
                        "available or verified."
                    )

                referral_wallet = await get_or_create_wallet(
                    session,
                    user.id,
                )

                withdrawal = await create_withdrawal_request(
                    session=session,
                    wallet=referral_wallet,
                    amount=Decimal(
                        str(eligibility.requested_amount)
                    ),
                    withdrawal_method="bank_transfer",
                    account_name=account.account_name,
                    account_number=account.account_number,
                    bank_name=account.bank_name,
                    bank_account_id=account.id,
                    session_id=session_id,
                )

    except ValueError as exc:
        await _show(
            update,
            f"❌ {html.escape(str(exc))}",
            InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🔙 Finance Menu",
                    callback_data=FINANCE_MENU,
                )
            ]]),
        )
        return MENU

    except Exception:
        logger.exception(
            "Finance withdrawal submission failed."
        )

        await _show(
            update,
            "❌ <b>Withdrawal Submission Failed</b>\n\n"
            "The transaction was not completed. "
            "Please try again.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🔙 Finance Menu",
                    callback_data=FINANCE_MENU,
                )
            ]]),
        )
        return MENU

    amount = Decimal(str(withdrawal.amount))
    withdrawal_id = getattr(withdrawal, "id", None)

    for key in (
        "finance_eligibility_session_id",
        "finance_withdrawal_amount",
        "finance_bank_account_id",
    ):
        context.user_data.pop(key, None)

    await _show(
        update,
        "✅ <b>Withdrawal Submitted</b>\n\n"
        f"Amount: <b>{_money(amount)}</b>\n"
        "-----------------\n\n"
        "Status: <b>PENDING</b>\n"
        "---------------\n\n"
        f"Request ID: <code>{html.escape(str(withdrawal_id))}</code>\n"
        "-----------------\n\n"
        "Your withdrawal has been recorded "
        "and is awaiting processing.",
        InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "📜 Withdrawal History",
                    callback_data=FINANCE_WITHDRAWALS,
                )
            ],
            [
                InlineKeyboardButton(
                    "💰 Finance Menu",
                    callback_data=FINANCE_MENU,
                )
            ],
        ]),
    )

    return MENU



BANKS_PER_PAGE = 8


def _bank_selection_keyboard(
    banks: list[dict],
    page: int,
) -> InlineKeyboardMarkup:
    """
    Build a paginated bank-selection keyboard.

    Each button carries the Flutterwave bank code.
    """

    start = page * BANKS_PER_PAGE
    end = start + BANKS_PER_PAGE

    page_banks = banks[start:end]

    rows = []

    # ---------------------------------------------------------
    # Bank Search
    # ---------------------------------------------------------

    rows.append([
        InlineKeyboardButton(
            "🔎 Search Bank",
            callback_data=FINANCE_BANK_SEARCH,
        )
    ])

    for bank in page_banks:
        bank_code = str(bank.get("code") or "").strip()
        bank_name = str(bank.get("name") or "").strip()

        if not bank_code or not bank_name:
            continue

        rows.append([
            InlineKeyboardButton(
                f"🏦 {bank_name}",
                callback_data=f"{FINANCE_BANK_SELECT}:{bank_code}",
            )
        ])

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬅️ Previous",
                callback_data=f"{FINANCE_BANK_PAGE}:{page - 1}",
            )
        )

    if end < len(banks):
        navigation.append(
            InlineKeyboardButton(
                "Next ➡️",
                callback_data=f"{FINANCE_BANK_PAGE}:{page + 1}",
            )
        )

    if navigation:
        rows.append(navigation)

    rows.append([
        InlineKeyboardButton(
            "🔙 Bank Account",
            callback_data=FINANCE_BANK_ACCOUNT,
        )
    ])

    rows.append([
        InlineKeyboardButton(
            "❌ Cancel",
            callback_data=FINANCE_CANCEL,
        )
    ])

    return InlineKeyboardMarkup(rows)


async def start_bank_account_add(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Retrieve the current Flutterwave Nigerian bank list
    and display the first page.

    No database write occurs here.
    """

    query = update.callback_query

    if query:
        await query.answer()

    # ---------------------------------------------------------
    # Starting a fresh bank-selection cycle.
    #
    # Clear only temporary account/verification data from
    # the previous registration attempt.
    #
    # Keep finance_bank_list because it will be refreshed below.
    # ---------------------------------------------------------

    for key in (
        "finance_account_number",
        "finance_verified_account_name",
    ):
        context.user_data.pop(key, None)

    result = await get_ng_banks()

    if not result.get("success"):
        logger.error(
            "Unable to retrieve Flutterwave bank list: %s",
            result.get("error") or result.get("message"),
        )

        text = (
            "🏦 <b>Add Bank Account</b>\n\n"
            "❌ We couldn't load the available banks right now.\n\n"
            "Please try again shortly."
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔄 Try Again",
                    callback_data=FINANCE_BANK_ADD,
                )
            ],
            [
                InlineKeyboardButton(
                    "🔙 Bank Account",
                    callback_data=FINANCE_BANK_ACCOUNT,
                )
            ],
        ])

        if query:
            await query.edit_message_text(
                text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )

        return MENU

    banks = result.get("banks") or []

    # ---------------------------------------------------------
    # Keep only valid Nigerian bank entries.
    # ---------------------------------------------------------

    cleaned_banks = []

    for bank in banks:
        code = str(bank.get("code") or "").strip()
        name = str(bank.get("name") or "").strip()

        if code and name:
            cleaned_banks.append({
                "code": code,
                "name": name,
            })

    # Sort by name so the UI is predictable.
    cleaned_banks.sort(
        key=lambda item: item["name"].lower()
    )

    if not cleaned_banks:
        logger.error(
            "Flutterwave returned an empty Nigerian bank list."
        )

        text = (
            "🏦 <b>Add Bank Account</b>\n\n"
            "❌ No banks are currently available.\n\n"
            "Please try again later."
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔙 Bank Account",
                    callback_data=FINANCE_BANK_ACCOUNT,
                )
            ],
        ])

        if query:
            await query.edit_message_text(
                text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )

        return MENU

    # ---------------------------------------------------------
    # Store the server-provided bank list temporarily.
    #
    # No database write.
    # ---------------------------------------------------------

    context.user_data["finance_bank_list"] = cleaned_banks
    context.user_data["finance_bank_page"] = 0

    text = (
        "🏦 <b>Select Your Bank</b>\n\n"
        "Choose the bank account you want to use "
        "for your Finance & Rewards withdrawals.\n\n"
        "👇 Select your bank below:"
    )

    markup = _bank_selection_keyboard(
        cleaned_banks,
        page=0,
    )

    if query:
        await query.edit_message_text(
            text,
            reply_markup=markup,
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=markup,
            parse_mode="HTML",
        )

    return BANK_SELECT


# ====================
# BANK SEARCH
# ====================

async def start_bank_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if query:
        await query.answer()

    # ---------------------------------------------------------
    # Make sure the Flutterwave bank list still exists.
    # ---------------------------------------------------------

    banks = context.user_data.get("finance_bank_list")

    if not banks:
        await _show(
            update,
            "❌ <b>Bank List Expired</b>\n\n"
            "Please open the bank selection again.",
            InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🏦 Bank Account",
                        callback_data=FINANCE_BANK_ACCOUNT,
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data=FINANCE_CANCEL,
                    )
                ],
            ]),
        )
        return MENU

    # ---------------------------------------------------------
    # Ask the user to enter a search term.
    # ---------------------------------------------------------

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data=FINANCE_CANCEL,
            )
        ],
    ])

    text = (
        "🔎 <b>Search Bank</b>\n\n"
        "Type the bank name or at least "
        "<b>3 letters</b>.\n\n"
        "Examples:\n"
        "• GTB\n"
        "• Access\n"
        "• Zenith\n"
        "• UBA\n"
        "• First Bank\n\n"
        "I'll show the matching banks for you to select."
    )

    if query:
        await query.edit_message_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    return BANK_SEARCH


async def search_bank(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    """
    Search the server-fetched Flutterwave bank list.

    No database write is performed here.
    """

    if not update.message:
        return BANK_SEARCH

    search_term = (update.message.text or "").strip()

    if len(search_term) < 3:
        await update.message.reply_text(
            "❌ <b>Search Too Short</b>\n\n"
            "Please enter at least <b>3 letters</b> "
            "of the bank name.\n\n"
            "For example: <b>GTB</b>, <b>UBA</b>, "
            "<b>Access</b> or <b>Zenith</b>.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data=FINANCE_CANCEL,
                    )
                ],
            ]),
        )
        return BANK_SEARCH

    banks = context.user_data.get(
        "finance_bank_list"
    ) or []

    if not banks:
        await update.message.reply_text(
            "❌ <b>Bank List Expired</b>\n\n"
            "Please open bank selection again.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🏦 Bank Account",
                        callback_data=FINANCE_BANK_ACCOUNT,
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data=FINANCE_CANCEL,
                    )
                ],
            ]),
        )
        return MENU

    normalized_search = search_term.casefold()

    matches = [
        bank
        for bank in banks
        if normalized_search
        in str(bank.get("name") or "").casefold()
    ]

    if not matches:
        await update.message.reply_text(
            "❌ <b>No Matching Bank Found</b>\n\n"
            f'No bank matched "<b>{html.escape(search_term)}</b>".\n\n'
            "Try the first 3 letters or another part "
            "of the bank name.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔎 Search Again",
                        callback_data=FINANCE_BANK_SEARCH,
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📄 Browse Banks",
                        callback_data=f"{FINANCE_BANK_PAGE}:0",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data=FINANCE_CANCEL,
                    )
                ],
            ]),
        )
        return BANK_SELECT

    rows = []

    for bank in matches[:20]:
        bank_code = str(
            bank.get("code") or ""
        ).strip()

        bank_name = str(
            bank.get("name") or ""
        ).strip()

        if not bank_code or not bank_name:
            continue

        rows.append([
            InlineKeyboardButton(
                f"🏦 {bank_name}",
                callback_data=(
                    f"{FINANCE_BANK_SELECT}:{bank_code}"
                ),
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "🔎 Search Again",
            callback_data=FINANCE_BANK_SEARCH,
        )
    ])

    rows.append([
        InlineKeyboardButton(
            "📄 Browse Banks",
            callback_data=f"{FINANCE_BANK_PAGE}:0",
        )
    ])

    rows.append([
        InlineKeyboardButton(
            "❌ Cancel",
            callback_data=FINANCE_CANCEL,
        )
    ])

    await update.message.reply_text(
        "🔎 <b>Bank Search Results</b>\n\n"
        f'Found <b>{len(matches)}</b> matching bank(s) '
        f'for "<b>{html.escape(search_term)}</b>".\n\n'
        "👇 Select your bank:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="HTML",
    )

    return BANK_SELECT



async def show_bank_page(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if query:
        await query.answer()

    banks = context.user_data.get("finance_bank_list")

    if not banks:
        return await start_bank_account_add(
            update,
            context,
        )

    try:
        page = int(
            query.data.split(":")[-1]
        )
    except (ValueError, AttributeError):
        return BANK_SELECT

    max_page = (
        len(banks) - 1
    ) // BANKS_PER_PAGE

    if page < 0 or page > max_page:
        return BANK_SELECT

    context.user_data["finance_bank_page"] = page

    text = (
        "🏦 <b>Select Your Bank</b>\n\n"
        "Choose the bank account you want to use "
        "for your Finance & Rewards withdrawals.\n\n"
        "👇 Select your bank below:"
    )

    await query.edit_message_text(
        text,
        reply_markup=_bank_selection_keyboard(
            banks,
            page,
        ),
        parse_mode="HTML",
    )

    return BANK_SELECT


async def select_bank(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if query:
        await query.answer()

    banks = context.user_data.get("finance_bank_list") or []

    if not query or not query.data:
        return BANK_SELECT

    try:
        bank_code = query.data.split(":")[-1].strip()
    except Exception:
        return BANK_SELECT

    # ---------------------------------------------------------
    # Validate the callback against the bank list that THIS
    # user received from Flutterwave.
    # ---------------------------------------------------------

    selected_bank = next(
        (
            bank
            for bank in banks
            if str(bank.get("code")) == bank_code
        ),
        None,
    )

    if selected_bank is None:
        await query.answer(
            "This bank selection is no longer valid.",
            show_alert=True,
        )
        return BANK_SELECT

    bank_name = selected_bank["name"]

    # ---------------------------------------------------------
    # Store only temporarily.
    # Nothing is written to PostgreSQL yet.
    # ---------------------------------------------------------

    context.user_data["finance_selected_bank_code"] = bank_code
    context.user_data["finance_selected_bank_name"] = bank_name
    context.user_data["finance_bank_account_flow"] = True

    text = (
        "🏦 <b>Bank Selected</b>\n\n"
        f"Bank: <b>{html.escape(bank_name)}</b>\n\n"
        "🔢 Please enter your <b>10-digit bank account number</b>.\n\n"
        "We will verify the account with Flutterwave "
        "before saving it."
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔙 Change Bank",
                callback_data=FINANCE_BANK_ADD,
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data=FINANCE_CANCEL,
            )
        ],
    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )

    return ACCOUNT_NUMBER


async def show_bank_account(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    async with get_async_session() as session:
        user = await _get_application_user(update, session)

        if user is None:
            return MENU

        accounts = await get_user_bank_accounts(
            session=session,
            user_id=user.id,
        )

    if not accounts:
        text = (
            "🏦 <b>Bank Account</b>\n\n"
            "No bank account has been added yet.\n\n"
            "A verified bank account will be required "
            "to receive your referral rewards.\n\n"
            "We will guide you through adding and verifying "
            "your bank account."
        )
    else:
        lines = [
            "🏦 <b>Bank Account</b>",
            "",
        ]

        for account in accounts:
            masked_number = (
                f"••••{account.account_number[-4:]}"
                if len(account.account_number) >= 4
                else "••••"
            )

            verification = (
                "✅ Verified"
                if account.is_verified
                else "⏳ Verification Pending"
            )

            default_marker = (
                " ⭐ <b>Default</b>"
                if account.is_default
                else ""
            )

            lines.extend([
                f"<b>{html.escape(account.bank_name)}</b>{default_marker}",
                f"{html.escape(account.account_name)}",
                f"{masked_number}",
                f"{verification}",
                "",
            ])

        text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "➕ Add Bank Account",
                callback_data=FINANCE_BANK_ADD,
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Finance Menu",
                callback_data=FINANCE_MENU,
            )
        ],
    ])

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    return MENU


async def collect_account_number(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return ACCOUNT_NUMBER

    value = update.message.text.strip().replace(" ", "")

    # =========================================================
    # BANK ACCOUNT REGISTRATION FLOW
    # =========================================================

    if context.user_data.get("finance_bank_account_flow"):

        if not value.isdigit() or len(value) != 10:
            await update.message.reply_text(
                "❌ <b>Invalid Account Number</b>\n\n"
                "Please enter a valid <b>10-digit Nigerian bank "
                "account number</b>.",
                parse_mode="HTML",
            )
            return ACCOUNT_NUMBER

        bank_code = context.user_data.get(
            "finance_selected_bank_code"
        )
        bank_name = context.user_data.get(
            "finance_selected_bank_name"
        )

        if not bank_code or not bank_name:
            logger.warning(
                "Bank account flow missing selected bank."
            )

            context.user_data.pop(
                "finance_bank_account_flow",
                None,
            )

            await update.message.reply_text(
                "❌ <b>Bank Selection Expired</b>\n\n"
                "Please select your bank again.",
                parse_mode="HTML",
            )

            return MENU

        # -----------------------------------------------------
        # Store account number temporarily.
        # Nothing is written to the database yet.
        # -----------------------------------------------------

        context.user_data["finance_account_number"] = value

        await update.message.reply_text(
            "🔎 <b>Verifying Bank Account...</b>\n\n"
            f"🏦 Bank: <b>{html.escape(bank_name)}</b>\n"
            f"🔢 Account: <b>••••{html.escape(value[-4:])}</b>\n\n"
            "Please wait while we confirm the account details.",
            parse_mode="HTML",
        )

        # -----------------------------------------------------
        # Flutterwave account resolution
        # -----------------------------------------------------

        try:
            result = await resolve_bank_account(
                account_number=value,
                account_bank=bank_code,
            )

        except Exception:
            logger.exception(
                "Flutterwave account resolution failed."
            )

            await update.message.reply_text(
                "❌ <b>Verification Failed</b>\n\n"
                "We couldn't verify this bank account right now.\n\n"
                "Please check the account number and try again.",
                parse_mode="HTML",
            )

            return ACCOUNT_NUMBER

        if not result.get("success"):
            logger.warning(
                "Flutterwave account resolution unsuccessful: %s",
                result,
            )

            await update.message.reply_text(
                "❌ <b>Account Could Not Be Verified</b>\n\n"
                "Flutterwave could not confirm this account.\n\n"
                "Please check the bank and account number "
                "and try again.",
                parse_mode="HTML",
            )

            return ACCOUNT_NUMBER

        # -----------------------------------------------------
        # Extract Flutterwave's returned beneficiary name.
        # -----------------------------------------------------

        data = result.get("data") or {}

        account_name = (
            result.get("account_name")
            or data.get("account_name")
        )

        if not account_name:
            logger.error(
                "Flutterwave resolution succeeded but "
                "returned no account name: %s",
                result,
            )

            await update.message.reply_text(
                "❌ <b>Verification Incomplete</b>\n\n"
                "The account could not be confirmed because "
                "no beneficiary name was returned.\n\n"
                "Please try again.",
                parse_mode="HTML",
            )

            return ACCOUNT_NUMBER

        # -----------------------------------------------------
        # Store verified information temporarily.
        # Still NO database write.
        # -----------------------------------------------------

        context.user_data["finance_verified_account_name"] = (
            str(account_name)
        )

        masked_account = "••••" + value[-4:]

        text = (
            "🏦 <b>Bank Account Verification</b>\n\n"
            f"Bank: <b>{html.escape(bank_name)}</b>\n"
            f"Account: <b>{html.escape(masked_account)}</b>\n\n"
            "👤 <b>Account Name:</b>\n"
            f"{html.escape(str(account_name))}\n\n"
            "Is this your account?"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "✅ Confirm & Save",
                    callback_data="finance:bank:confirm",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data=FINANCE_CANCEL,
                )
            ],
        ])

        await update.message.reply_text(
            text,
            reply_markup=keyboard,
            parse_mode="HTML",
        )

        return ACCOUNT_NUMBER

    # =========================================================
    # EXISTING WITHDRAWAL FLOW
    # =========================================================

    if not value.isdigit() or len(value) < 10:
        await update.message.reply_text(
            "❌ Please enter a valid bank account number."
        )
        return ACCOUNT_NUMBER

    context.user_data["finance_account_number"] = value

    await update.message.reply_text(
        "Enter the <b>bank name</b>.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "❌ Cancel",
                callback_data=FINANCE_CANCEL,
            )
        ]]),
    )

    return BANK_NAME


async def confirm_bank_account(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if query:
        await query.answer()

    # ---------------------------------------------------------
    # Only allow confirmation inside the bank-registration flow.
    # ---------------------------------------------------------

    if not context.user_data.get("finance_bank_account_flow"):
        if query:
            await query.answer(
                "This bank-account verification has expired.",
                show_alert=True,
            )
        return MENU

    bank_code = context.user_data.get(
        "finance_selected_bank_code"
    )
    bank_name = context.user_data.get(
        "finance_selected_bank_name"
    )
    account_number = context.user_data.get(
        "finance_account_number"
    )
    account_name = context.user_data.get(
        "finance_verified_account_name"
    )

    # ---------------------------------------------------------
    # Make sure the complete verified record exists.
    # ---------------------------------------------------------

    if not all([
        bank_code,
        bank_name,
        account_number,
        account_name,
    ]):
        logger.warning(
            "Incomplete bank-account confirmation data."
        )

        await query.edit_message_text(
            "❌ <b>Verification Session Expired</b>\n\n"
            "Your bank-account verification could not be "
            "completed.\n\n"
            "Please start again.",
            parse_mode="HTML",
        )

        return MENU

    # ---------------------------------------------------------
    # Resolve the application user.
    # ---------------------------------------------------------

    try:
        async with get_async_session() as session:
            user = await _get_application_user(
                update,
                session,
            )

            if user is None:
                raise ValueError(
                    "Unable to identify your account."
                )

            account = await create_bank_account(
                session=session,
                user_id=user.id,
                bank_code=bank_code,
                bank_name=bank_name,
                account_number=account_number,
                account_name=account_name,
                is_verified=True,
            )

            await session.commit()

    except ValueError as exc:
        logger.warning(
            "Bank account save rejected: %s",
            exc,
        )

        await query.edit_message_text(
            "❌ <b>Could Not Save Bank Account</b>\n\n"
            f"{html.escape(str(exc))}",
            parse_mode="HTML",
        )

        return MENU

    except Exception:
        logger.exception(
            "Failed to save verified bank account."
        )

        await query.edit_message_text(
            "❌ <b>Could Not Save Bank Account</b>\n\n"
            "Your bank account was not saved.\n\n"
            "Please try again.",
            parse_mode="HTML",
        )

        return MENU

    # ---------------------------------------------------------
    # Determine whether this is the default account.
    # ---------------------------------------------------------

    is_default = bool(
        getattr(account, "is_default", False)
    )

    masked_account = (
        "••••" + account_number[-4:]
    )

    # ---------------------------------------------------------
    # Clear temporary registration data.
    # ---------------------------------------------------------

    for key in (
        "finance_bank_account_flow",
        "finance_bank_list",
        "finance_bank_page",
        "finance_selected_bank_code",
        "finance_selected_bank_name",
        "finance_account_number",
        "finance_verified_account_name",
    ):
        context.user_data.pop(key, None)

    # ---------------------------------------------------------
    # Success message
    # ---------------------------------------------------------

    default_text = (
        "\n⭐ <b>This is now your default withdrawal account.</b>"
        if is_default
        else ""
    )

    text = (
        "✅ <b>Bank Account Saved</b>\n\n"
        f"🏦 Bank: <b>{html.escape(bank_name)}</b>\n"
        f"🔢 Account: <b>{html.escape(masked_account)}</b>\n"
        f"👤 Account Name: <b>{html.escape(str(account_name))}</b>\n"
        f"{default_text}\n\n"
        "Your verified bank account is now available "
        "for future withdrawals."
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🏦 Bank Account",
                callback_data=FINANCE_BANK_ACCOUNT,
            )
        ],
        [
            InlineKeyboardButton(
                "🔙 Finance Menu",
                callback_data=FINANCE_MENU,
            )
        ],
    ])

    await query.edit_message_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML",
    )

    return MENU


# ============================================================
# CANCEL / REGISTRATION
# ============================================================

async def cancel_finance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for key in (
        "finance_eligibility_session_id",
        "finance_withdrawal_amount",

        # Bank-account registration flow
        "finance_bank_account_flow",
        "finance_bank_list",
        "finance_bank_page",
        "finance_selected_bank_code",
    ):
        context.user_data.pop(key, None)

    await _show(
        update,
        "Finance flow cancelled.",
        InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "💰 Finance Menu",
                    callback_data=FINANCE_MENU,
                )
            ],
            [
                InlineKeyboardButton(
                    "🏠 Back to Main Menu",
                    callback_data="menu:main",
                )
            ],
        ]),
    )
    return MENU


def build_finance_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("finance", show_finance_menu),
            CallbackQueryHandler(
                show_finance_menu,
                pattern=r"^finance:open$",
            ),
        ],
        states={
            MENU: [
                CallbackQueryHandler(
                    show_finance_menu, pattern=r"^finance:menu$"
                ),
                CallbackQueryHandler(
                    show_invite_friends, pattern=r"^finance:invite$"
                ),
                CallbackQueryHandler(
                    show_wallet, pattern=r"^finance:wallet$"
                ),
                CallbackQueryHandler(
                    show_referrals, pattern=r"^finance:referrals$"
                ),
                CallbackQueryHandler(
                    show_withdrawals, pattern=r"^finance:withdrawals$"
                ),
                CallbackQueryHandler(
                    show_transactions, pattern=r"^finance:transactions$"
                ),
                CallbackQueryHandler(
                    begin_withdrawal, pattern=r"^finance:withdraw$"
                ),
                CallbackQueryHandler(
                    show_progress, pattern=r"^finance:progress$"
                ),
                CallbackQueryHandler(
                    show_bank_account,
                    pattern=rf"^{FINANCE_BANK_ACCOUNT}$",
                ),
                CallbackQueryHandler(
                    start_bank_account_add,
                    pattern=rf"^{FINANCE_BANK_ADD}$",
                ),
                CallbackQueryHandler(
                    begin_submission, pattern=r"^finance:submit$"
                ),
                CallbackQueryHandler(
                    use_saved_bank_account, pattern=rf"^{FINANCE_USE_SAVED_BANK}$",
                ),
                CallbackQueryHandler(
                    cancel_finance, pattern=r"^finance:cancel$"
                ),
            ],
            AMOUNT: [
                CallbackQueryHandler(
                    select_withdrawal_amount,
                    pattern=r"^finance:amount:\d+$",
                )
            ],
            ACCOUNT_NUMBER: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    collect_account_number,
                ),
                CallbackQueryHandler(
                    confirm_bank_account,
                    pattern=r"^finance:bank:confirm$",
                ),
                CallbackQueryHandler(
                    start_bank_account_add,
                    pattern=rf"^{FINANCE_BANK_ADD}$",
                ),
            ],
            BANK_SEARCH: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    search_bank,
                )
            ],
            BANK_SELECT: [
                CallbackQueryHandler(
                    start_bank_search,
                    pattern=rf"^{FINANCE_BANK_SEARCH}$",
                ),
                CallbackQueryHandler(
                    show_bank_page,
                    pattern=rf"^{FINANCE_BANK_PAGE}:\d+$",
                ),
                CallbackQueryHandler(
                    select_bank,
                    pattern=rf"^{FINANCE_BANK_SELECT}:.+$",
                ),
            ],
        },
        
        fallbacks=[
            CommandHandler("cancel", cancel_finance),
            CallbackQueryHandler(
                cancel_finance, pattern=r"^finance:cancel$"
            ),
            CallbackQueryHandler(
                show_finance_menu, pattern=r"^finance:menu$"
            ),
        ],
        allow_reentry=True,
        per_user=True,
        per_chat=True,
        name="finance_conversation",
        persistent=False,
    )


def register_handlers(application: Application) -> None:
    application.add_handler(build_finance_conversation())
    logger.info("Finance handlers registered.")
 
