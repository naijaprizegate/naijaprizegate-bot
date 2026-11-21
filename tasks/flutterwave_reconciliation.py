# ==========================================================
# tasks/flutterwave_reconciliation.py
# ==========================================================
"""
Daily Flutterwave settlement reconciliation and Admin reporting.

Schedule:
    12:15 AM Africa/Lagos

Reporting period:
    Previous calendar day.

This task:
    1. Waits until the next scheduled Nigerian time.
    2. Synchronizes Flutterwave settlements.
    3. Reconciles Flutterwave transactions against local payments.
    4. Sends a report to configured Admins.
    5. Sends an alert if reconciliation fails.
"""

import asyncio
import os
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from logger import logger


# ==========================================================
# Configuration
# ==========================================================

LAGOS_TIMEZONE = ZoneInfo("Africa/Lagos")

SCHEDULE_HOUR = 0
SCHEDULE_MINUTE = 15

NOTIFICATION_RETRY_ATTEMPTS = 3
NOTIFICATION_RETRY_DELAY_SECONDS = 5


# ==========================================================
# Admin IDs
# ==========================================================

def get_admin_ids() -> set[int]:
    """
    Load Admin Telegram IDs from ADMIN_IDS.

    Supports multiple IDs separated by commas.
    """

    raw = os.getenv("ADMIN_IDS", "")

    admin_ids: set[int] = set()

    for value in raw.split(","):
        value = value.strip()

        if value.isdigit():
            admin_ids.add(int(value))

    return admin_ids


# ==========================================================
# Schedule Calculation
# ==========================================================

def get_next_reconciliation_time(
    now: datetime | None = None,
) -> datetime:
    """
    Calculate the next 12:15 AM Nigeria time.

    Returns a timezone-aware datetime.
    """

    if now is None:
        now = datetime.now(LAGOS_TIMEZONE)

    elif now.tzinfo is None:
        raise ValueError(
            "now must be timezone-aware"
        )

    now = now.astimezone(LAGOS_TIMEZONE)

    next_run = now.replace(
        hour=SCHEDULE_HOUR,
        minute=SCHEDULE_MINUTE,
        second=0,
        microsecond=0,
    )

    if next_run <= now:
        next_run += timedelta(days=1)

    return next_run


async def has_completed_reconciliation(
    reporting_date,
) -> bool:
    """
    Check whether settlement reconciliation has already
    completed successfully for the reporting date.
    """
    from sqlalchemy import select
    from db import AsyncSessionLocal
    from models import FlutterwaveReconciliationRun

    async with AsyncSessionLocal() as session:
        statement = select(
            FlutterwaveReconciliationRun.id
        ).where(
            FlutterwaveReconciliationRun.run_type
            == "SETTLEMENT_SYNC",
            FlutterwaveReconciliationRun.run_status
            == "COMPLETED",
            FlutterwaveReconciliationRun.reporting_date
            == reporting_date,
        ).limit(1)

        result = await session.execute(statement)

        return result.scalar_one_or_none() is not None


async def get_latest_completed_reconciliation_date():
    """
    Return the most recent reporting date with a
    successfully completed settlement reconciliation.

    Return None if no successful reconciliation exists.
    """
    from sqlalchemy import select
    from db import AsyncSessionLocal
    from models import FlutterwaveReconciliationRun

    async with AsyncSessionLocal() as session:

        statement = (
            select(FlutterwaveReconciliationRun.reporting_date)
            .where(
                FlutterwaveReconciliationRun.run_type
                == "SETTLEMENT_SYNC",
                FlutterwaveReconciliationRun.run_status
                == "COMPLETED",
            )
            .order_by(
                FlutterwaveReconciliationRun.reporting_date.desc()
            )
            .limit(1)
        )

        result = await session.execute(statement)

        return result.scalar_one_or_none()

async def get_earliest_missing_reconciliation_date(
    through_date,
):
    """
    Find the earliest reporting date without a completed
    settlement reconciliation.

    If no reconciliation history exists, return through_date
    as the first reporting date.

    Return None when every date in the known reporting
    sequence through through_date has completed.
    """
    from sqlalchemy import select
    from db import AsyncSessionLocal
    from models import FlutterwaveReconciliationRun

    async with AsyncSessionLocal() as session:

        statement = (
            select(
                FlutterwaveReconciliationRun.reporting_date
            )
            .where(
                FlutterwaveReconciliationRun.run_type
                == "SETTLEMENT_SYNC",
                FlutterwaveReconciliationRun.run_status
                == "COMPLETED",
                FlutterwaveReconciliationRun.reporting_date
                <= through_date,
            )
            .distinct()
            .order_by(
                FlutterwaveReconciliationRun.reporting_date.asc()
            )
        )

        result = await session.execute(statement)

        completed_dates = {
            reporting_date
            for (reporting_date,) in result.all()
        }

    # First reconciliation: start with yesterday.
    if not completed_dates:
        return through_date

    # Begin from the earliest date in the established
    # reconciliation history and find the first gap.
    current_date = min(completed_dates)

    while current_date <= through_date:

        if current_date not in completed_dates:
            return current_date

        current_date += timedelta(days=1)

    return None

async def get_oldest_pending_notification_date(
    through_date,
):
    """
    Find the oldest completed reconciliation whose
    Admin notification has not been fully delivered.

    Returns its reporting date, or None if all reports
    up to through_date have been delivered.
    """
    from sqlalchemy import select
    from db import AsyncSessionLocal
    from models import FlutterwaveReconciliationRun

    async with AsyncSessionLocal() as session:

        statement = (
            select(
                FlutterwaveReconciliationRun.reporting_date,
                FlutterwaveReconciliationRun.summary,
            )
            .where(
                FlutterwaveReconciliationRun.run_type
                == "SETTLEMENT_SYNC",
                FlutterwaveReconciliationRun.run_status
                == "COMPLETED",
                FlutterwaveReconciliationRun.reporting_date
                <= through_date,
            )
            .order_by(
                FlutterwaveReconciliationRun.reporting_date.asc(),
                FlutterwaveReconciliationRun.completed_at.desc(),
            )
        )

        result = await session.execute(statement)

        seen_reporting_dates = set()

        for reporting_date, summary in result.all():

            if reporting_date in seen_reporting_dates:
                continue

            seen_reporting_dates.add(reporting_date)

            summary = summary or {}

            notification_status = summary.get(
                "admin_notification_status",
                "PENDING",
            )

            if notification_status != "DELIVERED":
                return reporting_date

        return None


async def save_admin_notification_state(
    reporting_date,
    delivered_to,
    failed_for,
    status,
) -> bool:
    """
    Save Admin notification delivery progress
    independently of financial reconciliation.
    """

    from sqlalchemy import select
    from db import AsyncSessionLocal
    from models import FlutterwaveReconciliationRun

    allowed_statuses = {
        "PENDING",
        "PARTIAL",
        "DELIVERED",
    }

    if status not in allowed_statuses:
        raise ValueError(
            f"Invalid notification status: {status}"
        )

    async with AsyncSessionLocal() as session:

        async with session.begin():

            statement = (
                select(FlutterwaveReconciliationRun)
                .where(
                    FlutterwaveReconciliationRun.run_type
                    == "SETTLEMENT_SYNC",
                    FlutterwaveReconciliationRun.run_status
                    == "COMPLETED",
                    FlutterwaveReconciliationRun.reporting_date
                    == reporting_date,
                )
                .order_by(
                    FlutterwaveReconciliationRun.completed_at.desc()
                )
                .limit(1)
            )

            result = await session.execute(statement)

            run = result.scalar_one_or_none()

            if run is None:
                return False

            updated_summary = dict(
                run.summary or {}
            )

            updated_summary.update({
                "admin_notification_status": status,
                "admin_notification_delivered_to": sorted(
                    set(delivered_to)
                ),
                "admin_notification_failed_for": sorted(
                    set(failed_for)
                ),
            })

            # Assign a new dictionary so SQLAlchemy
            # detects the JSONB field modification.
            run.summary = updated_summary

    return True


async def get_completed_reconciliation_summary(
    reporting_date,
) -> dict | None:
    """
    Retrieve the saved summary of a successful
    reconciliation for report recovery.
    """

    from sqlalchemy import select
    from db import AsyncSessionLocal
    from models import FlutterwaveReconciliationRun

    async with AsyncSessionLocal() as session:

        statement = (
            select(FlutterwaveReconciliationRun.summary)
            .where(
                FlutterwaveReconciliationRun.run_type
                == "SETTLEMENT_SYNC",
                FlutterwaveReconciliationRun.run_status
                == "COMPLETED",
                FlutterwaveReconciliationRun.reporting_date
                == reporting_date,
            )
            .order_by(
                FlutterwaveReconciliationRun.completed_at.desc()
            )
            .limit(1)
        )

        result = await session.execute(statement)

        summary = result.scalar_one_or_none()

        if summary is None:
            return None

        return dict(summary)


async def deliver_saved_reconciliation_report(
    reporting_date,
    summary: dict,
) -> bool:
    """
    Deliver a saved reconciliation report to Admins
    individually, tracking each successful delivery.
    """

    from bot_instance import bot

    admin_ids = sorted(get_admin_ids())

    if not admin_ids:
        logger.error(
            "Cannot deliver reconciliation report: "
            "ADMIN_IDS is not configured."
        )
        return False

    delivered_to = set(
        summary.get(
            "admin_notification_delivered_to",
            [],
        )
    )

    failed_for = set(
        summary.get(
            "admin_notification_failed_for",
            [],
        )
    )

    report = build_reconciliation_report(
        reporting_date.strftime("%Y-%m-%d"),
        summary,
    )

    for admin_id in admin_ids:

        # Do not resend to Admins who already received it.
        if admin_id in delivered_to:
            continue

        delivered = False

        for attempt in range(
            1,
            NOTIFICATION_RETRY_ATTEMPTS + 1,
        ):

            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=report,
                )

                delivered = True
                delivered_to.add(admin_id)
                failed_for.discard(admin_id)

                logger.info(
                    "Reconciliation report delivered "
                    "to Admin %s",
                    admin_id,
                )

                break

            except Exception:

                logger.exception(
                    "Reconciliation report delivery failed "
                    "for Admin %s (attempt %s/%s)",
                    admin_id,
                    attempt,
                    NOTIFICATION_RETRY_ATTEMPTS,
                )

                if attempt < NOTIFICATION_RETRY_ATTEMPTS:
                    await asyncio.sleep(
                        NOTIFICATION_RETRY_DELAY_SECONDS
                    )

        if not delivered:
            failed_for.add(admin_id)

        # Persist progress after each Admin.
        status = (
            "DELIVERED"
            if set(admin_ids).issubset(delivered_to)
            else "PARTIAL"
        )

        saved = await save_admin_notification_state(
            reporting_date=reporting_date,
            delivered_to=delivered_to,
            failed_for=failed_for,
            status=status,
        )

        if not saved:
            logger.error(
                "Could not save notification delivery "
                "progress for %s",
                reporting_date,
            )
            return False

    return set(admin_ids).issubset(delivered_to)


# ==========================================================
# Formatting
# ==========================================================

def format_money(value) -> str:
    """
    Format monetary values safely using Decimal.
    """

    if value is None:
        value = Decimal("0")

    amount = Decimal(str(value))

    return f"₦{amount:,.2f}"


def format_status_counts(
    status_counts: dict | None,
) -> str:
    """
    Format reconciliation status breakdown.
    """

    if not status_counts:
        return "No status breakdown available."

    lines = []

    for status, count in sorted(
        status_counts.items()
    ):
        lines.append(
            f"• {status}: {count}"
        )

    return "\n".join(lines)


# ==========================================================
# Admin Notifications
# ==========================================================

async def send_admin_notification(
    message: str,
) -> bool:
    """
    Send a notification to all configured Admins.

    Each Admin is handled independently so that one
    delivery failure does not prevent other Admins
    from receiving the report.

    Retries each failed delivery a limited number of times.
    """
    from bot_instance import bot

    admin_ids = get_admin_ids()

    if not admin_ids:
        logger.error(
            "Flutterwave reconciliation notification failed: "
            "ADMIN_IDS is not configured."
        )

        return False

    overall_success = True

    for admin_id in admin_ids:

        delivered = False

        for attempt in range(
            1,
            NOTIFICATION_RETRY_ATTEMPTS + 1,
        ):

            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=message,
                )

                delivered = True

                logger.info(
                    "Flutterwave reconciliation notification "
                    "sent to Admin %s",
                    admin_id,
                )

                break

            except Exception:

                logger.exception(
                    "Failed sending Flutterwave "
                    "reconciliation notification to Admin %s "
                    "(attempt %s/%s)",
                    admin_id,
                    attempt,
                    NOTIFICATION_RETRY_ATTEMPTS,
                )

                if attempt < NOTIFICATION_RETRY_ATTEMPTS:
                    await asyncio.sleep(
                        NOTIFICATION_RETRY_DELAY_SECONDS
                    )

        if not delivered:
            overall_success = False

    return overall_success


# ==========================================================
# Successful Reconciliation Report
# ==========================================================

def build_reconciliation_report(
    reporting_date: str,
    result: dict,
) -> str:
    """
    Build the daily Admin reconciliation report.
    """

    status_counts = (
        result.get(
            "reconciliation_status_counts",
            {},
        )
    )

    report = (
        "📊 FLUTTERWAVE DAILY RECONCILIATION\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"📅 Reporting Date: {reporting_date}\n"
        "🟢 Run Status: COMPLETED\n\n"

        "💰 FINANCIAL SUMMARY\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

        f"Gross Collections: "
        f"{format_money(result.get('gross_collections'))}\n"

        f"Confirmed Net Settlement: "
        f"{format_money(result.get('confirmed_net_amount'))}\n\n"

        "📦 SETTLEMENT ACTIVITY\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

        f"Settlements Processed: "
        f"{result.get('settlements_processed', 0)}\n"

        f"Transactions Retrieved: "
        f"{result.get('transactions_processed', 0)}\n"

        f"Transactions Reconciled: "
        f"{result.get('transactions_reconciled', 0)}\n\n"

        "🔍 LOCAL PAYMENT MATCHING\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

        f"Matched: "
        f"{result.get('matched_transactions', 0)}\n"

        f"Unmatched: "
        f"{result.get('unmatched_transactions', 0)}\n"

        f"Unresolved Discrepancies: "
        f"{result.get('unresolved_discrepancies', 0)}\n"

        f"Skipped (Invalid Identity): "
        f"{result.get('transactions_skipped_invalid_identity', 0)}\n\n"

        "📋 MATCHING STATUS BREAKDOWN\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

        f"{format_status_counts(status_counts)}\n\n"

        "✅ Local Payment Matching: "
        f"{'COMPLETED' if (result.get('local_matching_performed') or result.get('local_payment_matching') == 'COMPLETED') else 'NOT COMPLETED'}\n\n"

        "Automated report generated by "
        "NaijaPrizeGate reconciliation system."
    )

    return report


# ==========================================================
# Failed Reconciliation Alert
# ==========================================================

def build_failure_report(
    reporting_date: str,
) -> str:
    """
    Build a failure notification.

    Avoid exposing exception details or secrets
    in Telegram notifications.
    """

    return (
        "🚨 FLUTTERWAVE RECONCILIATION FAILED\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

        f"📅 Reporting Date: {reporting_date}\n\n"

        "The scheduled Flutterwave settlement "
        "reconciliation did not complete successfully.\n\n"

        "⚠️ No successful reconciliation report "
        "is available for this reporting period.\n\n"

        "Please check the application logs "
        "for the detailed error.\n\n"

        "Automated notification from "
        "NaijaPrizeGate."
    )


# ==========================================================
# Execute Reconciliation
# ==========================================================


async def execute_daily_reconciliation(reporting_date=None):
    """
    Reconcile the specified reporting date, or the previous
    calendar day when no date is supplied.
    Deliver the report using the saved notification state.
    Dates are calculated using Nigeria local time.
    """

    from services.flutterwave_settlement_sync import (
        sync_flutterwave_settlements,
    )

    if reporting_date is None:
        execution_time = datetime.now(LAGOS_TIMEZONE)

        reporting_date = (
            execution_time.date()
            - timedelta(days=1)
        )

    reporting_date_string = reporting_date.strftime("%Y-%m-%d")

    logger.info(
        "Starting scheduled Flutterwave reconciliation for %s",
        reporting_date_string,
    )

    # Step 1: Perform the financial reconciliation.
    try:
        await sync_flutterwave_settlements(
            from_date=reporting_date_string,
            to_date=reporting_date_string,
        )

    except asyncio.CancelledError:
        raise

    except Exception:
        logger.exception(
            "Scheduled Flutterwave reconciliation failed for %s",
            reporting_date_string,
        )

        failure_report = build_failure_report(
            reporting_date_string,
        )

        await send_admin_notification(failure_report)
        return False

    # Step 2: Retrieve the completed summary saved in the database.
    try:
        summary = await get_completed_reconciliation_summary(
            reporting_date,
        )

        if not summary:
            logger.error(
                "Reconciliation completed but no saved summary "
                "was found for %s",
                reporting_date_string,
            )
            return False

        # Step 3: Deliver the saved report and track each Admin.
        notification_success = await deliver_saved_reconciliation_report(
            reporting_date,
            summary,
        )

        if notification_success:
            logger.info(
                "Flutterwave reconciliation completed and "
                "Admin report delivered for %s",
                reporting_date_string,
            )
        else:
            logger.error(
                "Flutterwave reconciliation completed, but one "
                "or more Admin notifications remain undelivered "
                "for %s",
                reporting_date_string,
            )

        return notification_success

    except asyncio.CancelledError:
        raise

    except Exception:
        logger.exception(
            "Could not retrieve or deliver the saved Flutterwave "
            "reconciliation report for %s",
            reporting_date_string,
        )
        return False



# ==========================================================
# Daily Scheduler Loop
# ==========================================================


async def flutterwave_reconciliation_loop():
    """
    Run reconciliation every day at 12:15 AM Nigeria time.

    Handles:
    - Normal daily scheduling
    - Catch-up after bot restarts
    - Avoiding duplicate completed reconciliations
    - Retrying failed reconciliations
    """

    logger.info(
        "Flutterwave daily reconciliation scheduler started. "
        "Schedule: 00:15 Africa/Lagos."
    )

    while True:

        try:

            now = datetime.now(
                LAGOS_TIMEZONE
            )

            today_scheduled_time = now.replace(
                hour=SCHEDULE_HOUR,
                minute=SCHEDULE_MINUTE,
                second=0,
                microsecond=0,
            )

            # --------------------------------------------------
            # 1. Before today's scheduled time: wait
            # --------------------------------------------------

            if now < today_scheduled_time:

                delay_seconds = (
                    today_scheduled_time - now
                ).total_seconds()

                logger.info(
                    "Next Flutterwave reconciliation scheduled "
                    "for %s Africa/Lagos",
                    today_scheduled_time.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                )

                await asyncio.sleep(
                    max(0, delay_seconds)
                )

                continue

            # --------------------------------------------------
            # 2. Scheduled time has passed.
            # Reporting date is yesterday.
            # --------------------------------------------------

            yesterday = now.date() - timedelta(days=1)

            # First, check for an older completed report
            # that still needs Admin notification delivery.
            pending_notification_date = (
                await get_oldest_pending_notification_date(
                    yesterday
                )
            )

            if pending_notification_date is not None:

                reporting_date = pending_notification_date

                logger.info(
                    "Found an outstanding Admin report for %s. "
                    "Prioritising notification delivery.",
                    reporting_date,
                )

            else:

                # No outstanding notification.
                # Find the earliest missing financial reporting date.
                reporting_date = (
                    await get_earliest_missing_reconciliation_date(
                        yesterday
                    )
                )

                if reporting_date is None:

                    # All financial reconciliations and notifications
                    # are up to date. Wait for the next scheduled run.
                    next_run = get_next_reconciliation_time(
                        datetime.now(LAGOS_TIMEZONE)
                    )

                    delay_seconds = (
                        next_run - datetime.now(LAGOS_TIMEZONE)
                    ).total_seconds()

                    logger.info(
                        "All Flutterwave reporting dates are up to date. "
                        "Next run: %s Africa/Lagos",
                        next_run.strftime("%Y-%m-%d %H:%M:%S"),
                    )

                    await asyncio.sleep(max(0, delay_seconds))
                    continue

            logger.info(
                "Checking reconciliation history for "
                "reporting date %s",
                reporting_date,
            )

            # --------------------------------------------------
            # 3. Check whether this date already completed
            # --------------------------------------------------

            already_completed = (
                await has_completed_reconciliation(
                    reporting_date
                )
            )


            if already_completed:

                logger.info(
                    "Flutterwave reconciliation for %s "
                    "has already completed. Checking Admin "
                    "notification delivery.",
                    reporting_date,
                )

                # Retrieve the financial summary already saved.
                summary = await get_completed_reconciliation_summary(
                    reporting_date
                )

                if not summary:
                    logger.error(
                        "Completed reconciliation for %s has "
                        "no saved summary. Will check again.",
                        reporting_date,
                    )

                    await asyncio.sleep(60)
                    continue

                # Retry only outstanding Admin notifications.
                # This does NOT repeat the financial reconciliation.
                notification_success = (
                    await deliver_saved_reconciliation_report(
                        reporting_date,
                        summary,
                    )
                )

                if not notification_success:

                    logger.warning(
                        "One or more Admin notifications for %s "
                        "remain undelivered. Retrying in 60 seconds.",
                        reporting_date,
                    )

                    await asyncio.sleep(60)
                    continue

                logger.info(
                    "All configured Admin notifications for %s "
                    "have been delivered.",
                    reporting_date,
                )

                # Check whether another completed report
                # is still awaiting Admin notification.
                next_pending_notification = (
                    await get_oldest_pending_notification_date(
                        yesterday
                    )
                )

                if next_pending_notification is not None:
                    logger.info(
                        "Another outstanding Admin report exists "
                        "for %s. Continuing recovery.",
                        next_pending_notification,
                    )
                    continue

                # Check whether any financial reporting dates
                # up to yesterday are still missing.
                next_missing_date = (
                    await get_earliest_missing_reconciliation_date(
                        yesterday
                    )
                )

                if next_missing_date is not None:
                    logger.info(
                        "A missing reconciliation date remains: %s. "
                        "Continuing catch-up processing.",
                        next_missing_date,
                    )
                    continue

                # Everything is up to date. Wait for tomorrow.
                next_run = get_next_reconciliation_time(
                    datetime.now(LAGOS_TIMEZONE)
                )

                delay_seconds = (
                    next_run - datetime.now(LAGOS_TIMEZONE)
                ).total_seconds()

                logger.info(
                    "All Flutterwave reconciliations and Admin "
                    "notifications are up to date. "
                    "Next run: %s Africa/Lagos",
                    next_run.strftime("%Y-%m-%d %H:%M:%S"),
                )

                await asyncio.sleep(max(0, delay_seconds))

                continue


            # --------------------------------------------------
            # 4. No successful run exists.
            # Execute now (including catch-up runs).
            # --------------------------------------------------

            logger.warning(
                "No successful Flutterwave reconciliation "
                "found for %s. Starting reconciliation.",
                reporting_date,
            )

            await execute_daily_reconciliation(reporting_date)

            # --------------------------------------------------
            # 5. Check again on the next loop iteration.
            #
            # If successful, history prevents duplication.
            # If failed, retry after 60 seconds.
            # --------------------------------------------------

            await asyncio.sleep(60)

        except asyncio.CancelledError:

            logger.info(
                "Flutterwave reconciliation scheduler "
                "cancelled during shutdown."
            )

            raise

        except Exception:

            logger.exception(
                "Unexpected error in Flutterwave "
                "reconciliation scheduler."
            )

            # Prevent a rapid error loop.
            await asyncio.sleep(60)
