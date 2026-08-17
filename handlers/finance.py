# =========================================
# handlers/finance.py
# ========================================

from __future__ import annotations

import html
import logging
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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


logger = logging.getLogger(__name__)

MENU, AMOUNT, ACCOUNT_NAME, ACCOUNT_NUMBER, BANK_NAME = range(5)

FINANCE_OPEN = "finance:open"
FINANCE_MENU = "finance:menu"
FINANCE_INVITE = "finance:invite"
FINANCE_WALLET = "finance:wallet"
FINANCE_REFERRALS = "finance:referrals"
FINANCE_WITHDRAWALS = "finance:withdrawals"
FINANCE_TRANSACTIONS = "finance:transactions"
FINANCE_WITHDRAW = "finance:withdraw"
FINANCE_PROGRESS = "finance:progress"
FINANCE_SUBMIT = "finance:submit"
FINANCE_CANCEL = "finance:cancel"


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
        [InlineKeyboardButton("🔙 Back", callback_data=FINANCE_CANCEL)],
    ])


def _wallet_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💸 Withdraw", callback_data=FINANCE_WITHDRAW)],
        [InlineKeyboardButton("📜 Transactions", callback_data=FINANCE_TRANSACTIONS)],
        [InlineKeyboardButton("📈 Eligibility / Progress", callback_data=FINANCE_PROGRESS)],
        [InlineKeyboardButton("🔙 Finance Menu", callback_data=FINANCE_MENU)],
    ])


def _progress_keyboard(completed: bool):
    rows = []
    if completed:
        rows.append([
            InlineKeyboardButton("🏦 Enter Bank Details", callback_data=FINANCE_SUBMIT)
        ])
    rows.extend([
        [InlineKeyboardButton("🔄 Refresh Progress", callback_data=FINANCE_PROGRESS)],
        [InlineKeyboardButton("🔙 Finance Menu", callback_data=FINANCE_MENU)],
    ])
    return InlineKeyboardMarkup(rows)


async def _show(update: Update, text: str, markup=None):
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text(
            text,
            reply_markup=markup,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
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
        "🎁 <b>Referral Finance</b>\n\n"
        "Earn referral commissions from qualifying payments "
        "and use Finance Points to qualify for withdrawals.\n\n"
        "Choose an option below:",
        _menu_keyboard(),
    )
    return MENU


async def show_invite_friends(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        from handlers.free import send_referral_link
        await send_referral_link(update, context)
    except Exception:
        logger.exception("Finance referral-link handler failed.")
        await _show(
            update,
            "❌ <b>Unable to load your referral link.</b>\n\nPlease try again.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🔙 Finance Menu", callback_data=FINANCE_MENU
                )
            ]]),
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
        f"Available: <b>{_money(wallet.available_balance)}</b>\n"
        f"Total Earned: <b>{_money(wallet.total_earned)}</b>\n"
        f"Withdrawn: <b>{_money(wallet.total_withdrawn)}</b>\n"
        f"Pending Withdrawals: <b>{_money(wallet.pending_withdrawals)}</b>\n\n"
        f"Eligible Finance Points: <b>{wallet.eligible_points}</b>\n"
        f"Reserved Finance Points: <b>{wallet.reserved_points}</b>\n"
        f"Available Finance Points: <b>{wallet.available_points}</b>",
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
        body = "No wallet transactions yet."
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
            lines.append(f"{sign}{_money(amount)} — {description}")
        body = "\n".join(lines)

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
        f"Total Referrals: <b>{report.total_referrals}</b>\n"
        f"Active: <b>{report.active_referrals}</b>\n"
        f"Pending: <b>{report.pending_referrals}</b>\n"
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
        f"Pending: <b>{_money(report.pending_amount)}</b>\n"
        f"Approved: <b>{_money(report.approved_amount)}</b>\n"
        f"Completed: <b>{_money(report.completed_amount)}</b>\n"
        f"Rejected: <b>{_money(report.rejected_amount)}</b>\n"
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

async def begin_withdrawal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with get_async_session() as session:
        user = await _get_application_user(update, session)
        if user is None:
            return MENU
        wallet = await get_wallet_summary(session, user.id)

    available = Decimal(str(wallet.available_balance))

    if available < Decimal("2000.00"):
        await _show(
            update,
            "💸 <b>Withdrawal</b>\n\n"
            f"Available balance: <b>{_money(available)}</b>\n\n"
            "The minimum withdrawal amount is <b>₦2,000</b>.\n\n"
            "Earn more referral commissions before starting a withdrawal.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🔙 Referral Wallet", callback_data=FINANCE_WALLET
                )
            ]]),
        )
        return MENU

    await _show(
        update,
        "💸 <b>Start Withdrawal</b>\n\n"
        f"Available balance: <b>{_money(available)}</b>\n\n"
        "Enter the withdrawal amount.\n\n"
        "It must be a valid withdrawal amount supported by the Finance "
        "qualification rules.\n\n"
        "Examples: <b>2000</b> or <b>4000</b>",
        InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data=FINANCE_CANCEL)
        ]]),
    )
    return AMOUNT


async def collect_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return AMOUNT

    raw = update.message.text.strip().replace(",", "")

    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        await update.message.reply_text(
            "❌ Please enter a valid withdrawal amount.\n\n"
            "Example: <b>2000</b>",
            parse_mode="HTML",
        )
        return AMOUNT

    try:
        calculate_required_points(amount)
    except ValueError as exc:
        await update.message.reply_text(
            f"❌ {html.escape(str(exc))}",
            parse_mode="HTML",
        )
        return AMOUNT

    try:
        async with get_async_session() as session:
            user = await _get_application_user(update, session)
            if user is None:
                raise ValueError("Unable to identify your account.")

            wallet = await get_wallet_summary(session, user.id)
            available = Decimal(str(wallet.available_balance))

            if amount > available:
                raise ValueError(
                    f"The requested amount is greater than your available "
                    f"balance of {_money(available)}."
                )

            async with session.begin():
                wallet_row = await get_or_create_wallet(session, user.id)
                eligibility = await start_withdrawal_eligibility(
                    session=session,
                    user_id=user.id,
                    wallet_id=wallet_row.id,
                    amount=amount,
                )

    except ValueError as exc:
        await update.message.reply_text(
            f"❌ {html.escape(str(exc))}",
            parse_mode="HTML",
        )
        return AMOUNT
    except Exception:
        logger.exception("Failed to start Finance withdrawal eligibility.")
        await update.message.reply_text(
            "❌ We could not start the withdrawal qualification session.\n\n"
            "Please try again.",
        )
        return AMOUNT

    context.user_data["finance_eligibility_session_id"] = str(eligibility.id)
    context.user_data["finance_withdrawal_amount"] = str(amount)

    await update.message.reply_text(
        "✅ <b>Withdrawal Qualification Started</b>\n\n"
        f"Withdrawal amount: <b>{_money(amount)}</b>\n"
        f"Required Finance Points: <b>{eligibility.required_points}</b>\n"
        "Points earned: <b>0</b>\n\n"
        "You have one hour to complete qualification.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "📈 Check Progress", callback_data=FINANCE_PROGRESS
            )],
            [InlineKeyboardButton(
                "❌ Cancel", callback_data=FINANCE_CANCEL
            )],
        ]),
    )
    return MENU


async def show_progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        eligibility = await _get_current_eligibility(update, context)
    except Exception:
        logger.exception("Failed to validate Finance eligibility session.")
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
            "📈 <b>Finance Eligibility</b>\n\n"
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

    if completed:
        status_text = "✅ <b>QUALIFIED</b>"
        action = "You can now enter your bank details and submit the withdrawal."
    elif status == "EXPIRED":
        status_text = "⏰ <b>EXPIRED</b>"
        action = "This qualification session has expired. Start a new one."
    else:
        status_text = "⏳ <b>IN PROGRESS</b>"
        action = "Continue earning Finance Points, then refresh this screen."

    await _show(
        update,
        "📈 <b>Finance Eligibility</b>\n\n"
        f"Withdrawal amount: <b>{_money(eligibility.requested_amount)}</b>\n"
        f"Required points: <b>{required}</b>\n"
        f"Points earned: <b>{earned}</b>\n"
        f"Status: {status_text}\n\n{action}",
        _progress_keyboard(completed),
    )
    return MENU


# ============================================================
# BANK DETAILS / SUBMISSION
# ============================================================

async def begin_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        eligibility = await _get_current_eligibility(update, context)
    except Exception:
        logger.exception("Failed to validate eligibility before submission.")
        eligibility = None

    if eligibility is None or str(eligibility.status).upper() != "COMPLETED":
        await _show(
            update,
            "❌ <b>Withdrawal Not Ready</b>\n\n"
            "Your Finance Point qualification has not completed yet.",
            InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "📈 Check Progress", callback_data=FINANCE_PROGRESS
                )
            ]]),
        )
        return MENU

    context.user_data["finance_account_name"] = None
    context.user_data["finance_account_number"] = None
    context.user_data["finance_bank_name"] = None

    await _show(
        update,
        "🏦 <b>Bank Details</b>\n\n"
        "Enter the <b>account name</b> exactly as it appears on the bank account.",
        InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "❌ Cancel", callback_data=FINANCE_CANCEL
            )
        ]]),
    )
    return ACCOUNT_NAME


async def collect_account_name(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return ACCOUNT_NAME

    value = update.message.text.strip()
    if len(value) < 2:
        await update.message.reply_text("❌ Please enter a valid account name.")
        return ACCOUNT_NAME

    context.user_data["finance_account_name"] = value

    await update.message.reply_text(
        "Enter the <b>bank account number</b>.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "❌ Cancel", callback_data=FINANCE_CANCEL
            )
        ]]),
    )
    return ACCOUNT_NUMBER


async def collect_account_number(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return ACCOUNT_NUMBER

    value = update.message.text.strip().replace(" ", "")

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
                "❌ Cancel", callback_data=FINANCE_CANCEL
            )
        ]]),
    )
    return BANK_NAME


async def collect_bank_name(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return BANK_NAME

    bank_name = update.message.text.strip()

    if len(bank_name) < 2:
        await update.message.reply_text("❌ Please enter a valid bank name.")
        return BANK_NAME

    stored_session_id = context.user_data.get(
        "finance_eligibility_session_id"
    )

    if not stored_session_id:
        await update.message.reply_text(
            "❌ Your withdrawal qualification session could not be found.\n\n"
            "Please start the withdrawal flow again."
        )
        return MENU

    try:
        session_id = UUID(str(stored_session_id))
    except (ValueError, TypeError):
        await update.message.reply_text(
            "❌ Your withdrawal qualification session is invalid.\n\n"
            "Please start again."
        )
        return MENU

    account_name = context.user_data.get("finance_account_name")
    account_number = context.user_data.get("finance_account_number")

    if not account_name or not account_number:
        await update.message.reply_text(
            "❌ Your bank details are incomplete.\n\n"
            "Please start the withdrawal flow again."
        )
        return MENU

    try:
        async with get_async_session() as session:
            user = await _get_application_user(update, session)
            if user is None:
                raise ValueError("Unable to identify your account.")

            async with session.begin():
                eligibility = await validate_eligibility_session(
                    session=session,
                    user_id=user.id,
                    session_id=session_id,
                )

                if str(eligibility.status).upper() != "COMPLETED":
                    raise ValueError(
                        "Withdrawal eligibility session has not completed "
                        "qualification."
                    )

                referral_wallet = await get_or_create_wallet(
                    session, user.id
                )

                withdrawal = await create_withdrawal_request(
                    session=session,
                    wallet=referral_wallet,
                    amount=Decimal(str(eligibility.requested_amount)),
                    withdrawal_method="bank_transfer",
                    account_name=account_name,
                    account_number=account_number,
                    bank_name=bank_name,
                    session_id=session_id,
                )

    except ValueError as exc:
        await update.message.reply_text(
            f"❌ {html.escape(str(exc))}",
            parse_mode="HTML",
        )
        return MENU
    except Exception:
        logger.exception("Finance withdrawal submission failed.")
        await update.message.reply_text(
            "❌ <b>Withdrawal Submission Failed</b>\n\n"
            "The transaction was not completed. Please try again.",
            parse_mode="HTML",
        )
        return MENU

    amount = Decimal(str(withdrawal.amount))
    withdrawal_id = getattr(withdrawal, "id", None)

    for key in (
        "finance_eligibility_session_id",
        "finance_withdrawal_amount",
        "finance_account_name",
        "finance_account_number",
        "finance_bank_name",
    ):
        context.user_data.pop(key, None)

    await update.message.reply_text(
        "✅ <b>Withdrawal Submitted</b>\n\n"
        f"Amount: <b>{_money(amount)}</b>\n"
        "Status: <b>PENDING</b>\n"
        f"Request ID: <code>{html.escape(str(withdrawal_id))}</code>\n\n"
        "Your withdrawal has been recorded and is awaiting processing.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "📜 Withdrawal History",
                callback_data=FINANCE_WITHDRAWALS,
            )],
            [InlineKeyboardButton(
                "💰 Finance Menu", callback_data=FINANCE_MENU
            )],
        ]),
    )
    return MENU


# ============================================================
# CANCEL / REGISTRATION
# ============================================================

async def cancel_finance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for key in (
        "finance_eligibility_session_id",
        "finance_withdrawal_amount",
        "finance_account_name",
        "finance_account_number",
        "finance_bank_name",
    ):
        context.user_data.pop(key, None)

    await _show(
        update,
        "Finance flow cancelled.",
        InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "🎁 Finance Menu", callback_data=FINANCE_MENU
            )
        ]]),
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
                    begin_submission, pattern=r"^finance:submit$"
                ),
                CallbackQueryHandler(
                    cancel_finance, pattern=r"^finance:cancel$"
                ),
            ],
            AMOUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    collect_amount,
                )
            ],
            ACCOUNT_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    collect_account_name,
                )
            ],
            ACCOUNT_NUMBER: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    collect_account_number,
                )
            ],
            BANK_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    collect_bank_name,
                )
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
