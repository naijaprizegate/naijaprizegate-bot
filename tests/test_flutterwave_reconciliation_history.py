
import asyncio
import sys
from datetime import date
from types import ModuleType
from uuid import uuid4

from tasks.flutterwave_reconciliation import (
    get_earliest_missing_reconciliation_date,
    get_oldest_pending_notification_date,
    has_completed_reconciliation,
)


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def all(self):
        return self.value

class FakeSession:
    def __init__(self, result_value):
        self.result_value = result_value
        self.execute_called = False

    async def __aenter__(self):
        return self

    async def __aexit__(
        self,
        exc_type,
        exc_value,
        traceback,
    ):
        return False

    async def execute(self, statement):
        self.execute_called = True
        return FakeResult(self.result_value)


def run_history_check(monkeypatch, result_value):
    fake_session = FakeSession(result_value)

    fake_db = ModuleType("db")
    fake_db.AsyncSessionLocal = lambda: fake_session

    monkeypatch.setitem(
        sys.modules,
        "db",
        fake_db,
    )

    result = asyncio.run(
        has_completed_reconciliation(
            date(2026, 11, 17)
        )
    )

    return result, fake_session


def test_completed_reconciliation_returns_true(
    monkeypatch,
):
    result, fake_session = run_history_check(
        monkeypatch,
        uuid4(),
    )

    assert result is True
    assert fake_session.execute_called


def test_missing_completed_reconciliation_returns_false(
    monkeypatch,
):
    result, fake_session = run_history_check(
        monkeypatch,
        None,
    )

    assert result is False
    assert fake_session.execute_called



def test_completed_summary_is_retrieved(
    monkeypatch,
):
    expected_summary = {
        "gross_collections": "2500.00",
        "confirmed_net_amount": "2446.25",
        "matched_transactions": 5,
        "admin_notification_status": "PENDING",
        "admin_notification_delivered_to": [],
        "admin_notification_failed_for": [],
    }

    fake_session = FakeSession(
        expected_summary
    )

    fake_db = ModuleType("db")
    fake_db.AsyncSessionLocal = lambda: fake_session

    monkeypatch.setitem(
        sys.modules,
        "db",
        fake_db,
    )

    from tasks.flutterwave_reconciliation import (
        get_completed_reconciliation_summary,
    )

    result = asyncio.run(
        get_completed_reconciliation_summary(
            date(2026, 11, 17)
        )
    )

    assert result == expected_summary
    assert fake_session.execute_called


def test_missing_completed_summary_returns_none(
    monkeypatch,
):
    fake_session = FakeSession(None)

    fake_db = ModuleType("db")
    fake_db.AsyncSessionLocal = lambda: fake_session

    monkeypatch.setitem(
        sys.modules,
        "db",
        fake_db,
    )

    from tasks.flutterwave_reconciliation import (
        get_completed_reconciliation_summary,
    )

    result = asyncio.run(
        get_completed_reconciliation_summary(
            date(2026, 11, 17)
        )
    )

    assert result is None
    assert fake_session.execute_called

def test_oldest_pending_notification_date_is_returned(
    monkeypatch,
):
    pending_date = date(2026, 11, 16)

    fake_session = FakeSession([
        (
            date(2026, 11, 16),
            {
                "admin_notification_status": "PENDING",
            },
        ),
        (
            date(2026, 11, 17),
            {
                "admin_notification_status": "DELIVERED",
            },
        ),
    ])

    fake_db = ModuleType("db")
    fake_db.AsyncSessionLocal = lambda: fake_session

    monkeypatch.setitem(
        sys.modules,
        "db",
        fake_db,
    )

    result = asyncio.run(
        get_oldest_pending_notification_date(
            date(2026, 11, 17)
        )
    )

    assert result == pending_date
    assert fake_session.execute_called


def test_no_pending_notification_returns_none(
    monkeypatch,
):
    fake_session = FakeSession([
        (
            date(2026, 11, 16),
            {
                "admin_notification_status": "DELIVERED",
            },
        ),
        (
            date(2026, 11, 17),
            {
                "admin_notification_status": "DELIVERED",
            },
        ),
    ])

    fake_db = ModuleType("db")
    fake_db.AsyncSessionLocal = lambda: fake_session

    monkeypatch.setitem(
        sys.modules,
        "db",
        fake_db,
    )

    result = asyncio.run(
        get_oldest_pending_notification_date(
            date(2026, 11, 17)
        )
    )

    assert result is None
    assert fake_session.execute_called

def test_latest_run_status_is_used_for_duplicate_reporting_dates(
    monkeypatch,
):
    fake_session = FakeSession([
        # Latest run for November 16: already delivered.
        (
            date(2026, 11, 16),
            {
                "admin_notification_status": "DELIVERED",
            },
        ),
        # Older run for November 16: still pending.
        (
            date(2026, 11, 16),
            {
                "admin_notification_status": "PENDING",
            },
        ),
        # November 17: pending.
        (
            date(2026, 11, 17),
            {
                "admin_notification_status": "PENDING",
            },
        ),
    ])

    fake_db = ModuleType("db")
    fake_db.AsyncSessionLocal = lambda: fake_session

    monkeypatch.setitem(
        sys.modules,
        "db",
        fake_db,
    )

    result = asyncio.run(
        get_oldest_pending_notification_date(
            date(2026, 11, 17)
        )
    )

    assert result == date(2026, 11, 17)
    assert fake_session.execute_called

def test_earliest_missing_reconciliation_date_is_found(
    monkeypatch,
):
    fake_session = FakeSession([
        (date(2026, 11, 16),),
        (date(2026, 11, 18),),
    ])

    fake_db = ModuleType("db")
    fake_db.AsyncSessionLocal = lambda: fake_session

    monkeypatch.setitem(
        sys.modules,
        "db",
        fake_db,
    )

    result = asyncio.run(
        get_earliest_missing_reconciliation_date(
            date(2026, 11, 19)
        )
    )

    assert result == date(2026, 11, 17)
    assert fake_session.execute_called

def test_no_history_starts_with_through_date(
    monkeypatch,
):
    through_date = date(2026, 11, 19)

    fake_session = FakeSession([])

    fake_db = ModuleType("db")
    fake_db.AsyncSessionLocal = lambda: fake_session

    monkeypatch.setitem(
        sys.modules,
        "db",
        fake_db,
    )

    result = asyncio.run(
        get_earliest_missing_reconciliation_date(
            through_date
        )
    )

    assert result == through_date
    assert fake_session.execute_called

def test_all_reconciliation_dates_completed_returns_none(
    monkeypatch,
):
    fake_session = FakeSession([
        (date(2026, 11, 16),),
        (date(2026, 11, 17),),
        (date(2026, 11, 18),),
        (date(2026, 11, 19),),
    ])

    fake_db = ModuleType("db")
    fake_db.AsyncSessionLocal = lambda: fake_session

    monkeypatch.setitem(
        sys.modules,
        "db",
        fake_db,
    )

    result = asyncio.run(
        get_earliest_missing_reconciliation_date(
            date(2026, 11, 19)
        )
    )

    assert result is None
    assert fake_session.execute_called