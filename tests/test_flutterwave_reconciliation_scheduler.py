import pytest
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import tasks.flutterwave_reconciliation as scheduler

from tasks.flutterwave_reconciliation import (
    get_next_reconciliation_time,
)


LAGOS = ZoneInfo("Africa/Lagos")


def test_next_run_before_0015():
    now = datetime(
        2026, 11, 18, 0, 10,
        tzinfo=LAGOS,
    )

    result = get_next_reconciliation_time(now)

    assert result == datetime(
        2026, 11, 18, 0, 15,
        tzinfo=LAGOS,
    )


def test_next_run_exactly_at_0015():
    now = datetime(
        2026, 11, 18, 0, 15,
        tzinfo=LAGOS,
    )

    result = get_next_reconciliation_time(now)

    assert result == datetime(
        2026, 11, 19, 0, 15,
        tzinfo=LAGOS,
    )


def test_next_run_during_day():
    now = datetime(
        2026, 11, 18, 8, 30,
        tzinfo=LAGOS,
    )

    result = get_next_reconciliation_time(now)

    assert result == datetime(
        2026, 11, 19, 0, 15,
        tzinfo=LAGOS,
    )


def test_next_run_before_midnight():
    now = datetime(
        2026, 11, 18, 23, 59,
        tzinfo=LAGOS,
    )

    result = get_next_reconciliation_time(now)

    assert result == datetime(
        2026, 11, 19, 0, 15,
        tzinfo=LAGOS,
    )

def test_naive_datetime_is_rejected():
    now = datetime(
        2026, 11, 18, 0, 10,
    )

    with pytest.raises(ValueError):
        get_next_reconciliation_time(now)


@pytest.mark.asyncio
async def test_completed_reconciliation_only_retries_notification(
    monkeypatch,
):
    reporting_date = datetime(
        2026, 11, 20,
    ).date()

    calls = {
        "financial_reconciliation": 0,
        "notification_delivery": 0,
    }

    pending_check_count = 0

    async def fake_get_oldest_pending_notification_date(through_date):
        nonlocal pending_check_count
        pending_check_count += 1

        if pending_check_count == 1:
            return reporting_date

        return None

    async def fake_has_completed_reconciliation(date):
        return True

    async def fake_get_completed_reconciliation_summary(date):
        return {"gross_collections": "500.00"}

    async def fake_deliver_saved_reconciliation_report(date, summary):
        calls["notification_delivery"] += 1
        return True

    async def fake_execute_daily_reconciliation(date):
        calls["financial_reconciliation"] += 1

    async def fake_get_earliest_missing_reconciliation_date(
        through_date,
    ):
        return None

    async def fake_sleep(seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(
        scheduler,
        "get_oldest_pending_notification_date",
        fake_get_oldest_pending_notification_date,
    )
    monkeypatch.setattr(
        scheduler,
        "has_completed_reconciliation",
        fake_has_completed_reconciliation,
    )
    monkeypatch.setattr(
        scheduler,
        "get_completed_reconciliation_summary",
        fake_get_completed_reconciliation_summary,
    )
    monkeypatch.setattr(
        scheduler,
        "deliver_saved_reconciliation_report",
        fake_deliver_saved_reconciliation_report,
    )
    monkeypatch.setattr(
        scheduler,
        "execute_daily_reconciliation",
        fake_execute_daily_reconciliation,
    )
    monkeypatch.setattr(
        scheduler,
        "get_earliest_missing_reconciliation_date",
        fake_get_earliest_missing_reconciliation_date,
    )
    monkeypatch.setattr(
        scheduler.asyncio,
        "sleep",
        fake_sleep,
    )

    class FixedDateTime:
        @staticmethod
        def now(timezone):
            return datetime(
                2026, 11, 21, 8, 0,
                tzinfo=timezone,
            )

    monkeypatch.setattr(
        scheduler,
        "datetime",
        FixedDateTime,
    )

    with pytest.raises(asyncio.CancelledError):
        await scheduler.flutterwave_reconciliation_loop()

    assert calls["notification_delivery"] == 1
    assert calls["financial_reconciliation"] == 0


@pytest.mark.asyncio
async def test_scheduler_processes_earliest_missing_date(
    monkeypatch,
):
    missing_date = datetime(2026, 11, 18).date()
    processed_dates = []

    async def fake_get_oldest_pending_notification_date(through_date):
        return None

    async def fake_get_earliest_missing_reconciliation_date(through_date):
        return missing_date

    async def fake_has_completed_reconciliation(reporting_date):
        return False

    async def fake_execute_daily_reconciliation(reporting_date):
        processed_dates.append(reporting_date)

    async def fake_sleep(seconds):
        raise asyncio.CancelledError

    class FixedDateTime:
        @staticmethod
        def now(timezone):
            return datetime(
                2026, 11, 21, 8, 0,
                tzinfo=timezone,
            )

    monkeypatch.setattr(
        scheduler,
        "datetime",
        FixedDateTime,
    )
    monkeypatch.setattr(
        scheduler,
        "get_oldest_pending_notification_date",
        fake_get_oldest_pending_notification_date,
    )
    monkeypatch.setattr(
        scheduler,
        "get_earliest_missing_reconciliation_date",
        fake_get_earliest_missing_reconciliation_date,
    )
    monkeypatch.setattr(
        scheduler,
        "has_completed_reconciliation",
        fake_has_completed_reconciliation,
    )
    monkeypatch.setattr(
        scheduler,
        "execute_daily_reconciliation",
        fake_execute_daily_reconciliation,
    )
    monkeypatch.setattr(
        scheduler.asyncio,
        "sleep",
        fake_sleep,
    )

    with pytest.raises(asyncio.CancelledError):
        await scheduler.flutterwave_reconciliation_loop()

    assert processed_dates == [missing_date]


@pytest.mark.asyncio
async def test_failed_notification_retries_without_reconciliation(
    monkeypatch,
):
    reporting_date = datetime(2026, 11, 20).date()

    calls = {
        "notification_delivery": 0,
        "financial_reconciliation": 0,
        "sleep_seconds": [],
    }

    async def fake_get_oldest_pending_notification_date(through_date):
        return reporting_date

    async def fake_has_completed_reconciliation(date):
        return True

    async def fake_get_completed_reconciliation_summary(date):
        return {"gross_collections": "500.00"}

    async def fake_deliver_saved_reconciliation_report(date, summary):
        calls["notification_delivery"] += 1
        return False

    async def fake_execute_daily_reconciliation(date):
        calls["financial_reconciliation"] += 1

    async def fake_sleep(seconds):
        calls["sleep_seconds"].append(seconds)
        raise asyncio.CancelledError

    class FixedDateTime:
        @staticmethod
        def now(timezone):
            return datetime(
                2026, 11, 21, 8, 0,
                tzinfo=timezone,
            )

    monkeypatch.setattr(
        scheduler,
        "datetime",
        FixedDateTime,
    )
    monkeypatch.setattr(
        scheduler,
        "get_oldest_pending_notification_date",
        fake_get_oldest_pending_notification_date,
    )
    monkeypatch.setattr(
        scheduler,
        "has_completed_reconciliation",
        fake_has_completed_reconciliation,
    )
    monkeypatch.setattr(
        scheduler,
        "get_completed_reconciliation_summary",
        fake_get_completed_reconciliation_summary,
    )
    monkeypatch.setattr(
        scheduler,
        "deliver_saved_reconciliation_report",
        fake_deliver_saved_reconciliation_report,
    )
    monkeypatch.setattr(
        scheduler,
        "execute_daily_reconciliation",
        fake_execute_daily_reconciliation,
    )
    monkeypatch.setattr(
        scheduler.asyncio,
        "sleep",
        fake_sleep,
    )

    with pytest.raises(asyncio.CancelledError):
        await scheduler.flutterwave_reconciliation_loop()

    assert calls["notification_delivery"] == 1
    assert calls["financial_reconciliation"] == 0
    assert calls["sleep_seconds"] == [60]
