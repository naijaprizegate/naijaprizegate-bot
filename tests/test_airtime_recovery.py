
import asyncio
import importlib
import sys
from pathlib import Path
import types
from contextlib import asynccontextmanager
from types import SimpleNamespace



class FakeResult:
    def __init__(self, rows=None, rowcount=1):
        self.rows = rows or []
        self.rowcount = rowcount

    def fetchall(self):
        return self.rows




class FakeSession:
    def __init__(self, rows, final_update_rowcount=1):
        self.rows = rows
        self.statements = []
        self.final_update_rowcount = final_update_rowcount

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append((sql, params or {}))

        if "WITH picked AS" in sql:
            return FakeResult(self.rows)

        if (
            "SET status = :status" in sql
            and "provider_request_id = :request_id" in sql
        ):
            return FakeResult(
                rowcount=self.final_update_rowcount
            )

        return FakeResult()

    async def commit(self):
        pass

    async def rollback(self):
        pass



def load_isolated_notifier(
    monkeypatch,
    rows,
    calls,
    response,
    final_update_rowcount=1,
):
    # Fake database session: no real database is imported or contacted.
    session = FakeSession(
        rows,
        final_update_rowcount=final_update_rowcount,
    )
    db_stub = types.ModuleType("db")

    @asynccontextmanager
    async def fake_get_async_session():
        yield session

    db_stub.get_async_session = fake_get_async_session
    monkeypatch.setitem(sys.modules, "db", db_stub)

    # Fake provider functions: no network calls or airtime purchases.
    service_stub = types.ModuleType(
        "services.airtime_providers.service"
    )

    async def fake_send_airtime(**kwargs):
        calls["send"].append(kwargs)
        return response

    async def fake_check_airtime_status(**kwargs):
        calls["check"].append(kwargs)
        return response

    service_stub.send_airtime = fake_send_airtime
    service_stub.check_airtime_status = fake_check_airtime_status

    monkeypatch.setitem(
        sys.modules,
        "services.airtime_providers.service",
        service_stub,
    )

    # Fake Telegram so the test cannot contact Telegram either.
    telegram_stub = types.ModuleType("telegram")
    constants_stub = types.ModuleType("telegram.constants")


    class FakeBot:
        def __init__(self, token):
            self.token = token
            self.messages = calls.setdefault("messages", [])

        async def send_message(self, *args, **kwargs):
            self.messages.append({
                "args": args,
                "kwargs": kwargs,
            })


    class FakeTelegramObject:
        def __init__(self, *args, **kwargs):
            pass

    telegram_stub.Bot = FakeBot
    telegram_stub.InlineKeyboardButton = FakeTelegramObject
    telegram_stub.InlineKeyboardMarkup = FakeTelegramObject
    constants_stub.ParseMode = SimpleNamespace(HTML="HTML")
    telegram_stub.constants = constants_stub

    monkeypatch.setitem(sys.modules, "telegram", telegram_stub)
    monkeypatch.setitem(
        sys.modules,
        "telegram.constants",
        constants_stub,
    )

    monkeypatch.setenv(
        "BOT_TOKEN",
        "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
    )
    monkeypatch.setenv("ADMIN_USER_ID", "0")
    monkeypatch.setenv("AIRTIME_PROVIDER", "vtu_ng")

    # Prevent importing the real logger during isolated tests.
    import logging
    logger_stub = types.ModuleType("logger")
    logger_stub.logger = logging.getLogger("test_airtime_notifier")
    monkeypatch.setitem(sys.modules, "logger", logger_stub)

    # Bypass tasks/__init__.py during this isolated test.
    tasks_stub = types.ModuleType("tasks")
    tasks_stub.__path__ = [
        str(Path(__file__).resolve().parents[1] / "tasks")
    ]
    monkeypatch.setitem(sys.modules, "tasks", tasks_stub)

    # Force a fresh import using the fake dependencies above.
    sys.modules.pop("tasks.notifier", None)
    notifier = importlib.import_module("tasks.notifier")

    return notifier, session


def fake_processing_response():
    return SimpleNamespace(
        success=True,
        provider="vtu_ng",
        reference="NPG-test-001",
        message="Transaction is processing",
        raw={"status": "processing-api"},
    )


def fake_duplicate_response():
    return SimpleNamespace(
        success=False,
        provider="vtu_ng",
        reference="NPG-new-test-001",
        message="Duplicate request ID",
        raw={
            "status": "DUPLICATE_REQUEST",
            "retryable": False,
            "request_id": "NPG-new-test-001",
        },
    )


def make_payout(
    *,
    payout_id,
    status,
    provider,
    request_id,
):
    return SimpleNamespace(
        id=payout_id,
        status=status,
        tg_id=12345,
        phone_number="08012345678",
        network="mtn",
        amount=50,
        provider=provider,
        provider_request_id=request_id,
        retry_count=0,
    )


def test_existing_request_checks_status_without_new_purchase(monkeypatch):
    calls = {"send": [], "check": []}
    request_id = "NPG-existing-test-001"

    payout = make_payout(
        payout_id="existing-test-001",
        status="processing",
        provider="vtu_ng",
        request_id=request_id,
    )

    notifier, session = load_isolated_notifier(
        monkeypatch,
        [payout],
        calls,
        fake_processing_response(),
    )

    try:
        asyncio.run(notifier.process_pending_airtime())

        assert calls["check"] == [
            {
                "reference": request_id,
                "provider_name": "vtu_ng",
            }
        ]
        assert calls["send"] == []

    finally:
        sys.modules.pop("tasks.notifier", None)


def test_new_payout_submits_once_with_stable_request_id(monkeypatch):
    calls = {"send": [], "check": []}
    payout_id = "new-test-001"
    expected_request_id = f"NPG-{payout_id}"

    payout = make_payout(
        payout_id=payout_id,
        status="pending_claim",
        provider=None,
        request_id=None,
    )

    notifier, session = load_isolated_notifier(
        monkeypatch,
        [payout],
        calls,
        fake_processing_response(),
    )

    try:
        asyncio.run(notifier.process_pending_airtime())

        assert calls["check"] == []
        assert len(calls["send"]) == 1

        assert calls["send"][0] == {
            "phone": "08012345678",
            "amount": 50,
            "network": "mtn",
            "request_id": expected_request_id,
        }

    finally:
        sys.modules.pop("tasks.notifier", None)


def test_duplicate_response_is_held_for_review(monkeypatch):
    calls = {"send": [], "check": []}
    payout_id = "new-test-001"
    expected_request_id = f"NPG-{payout_id}"

    payout = make_payout(
        payout_id=payout_id,
        status="pending_claim",
        provider=None,
        request_id=None,
    )

    response = fake_duplicate_response()
    response.reference = expected_request_id
    response.raw["request_id"] = expected_request_id

    notifier, session = load_isolated_notifier(
        monkeypatch,
        [payout],
        calls,
        response,
    )

    try:
        asyncio.run(notifier.process_pending_airtime())

        # The original purchase is attempted only once.
        assert len(calls["send"]) == 1
        assert calls["send"][0]["request_id"] == expected_request_id

        # A duplicate response must not trigger an immediate requery
        # or another purchase during the same processing run.
        assert calls["check"] == []

        # The final database update must hold the payout for review.
        status_updates = [
            params
            for sql, params in session.statements
            if "SET status = :status" in sql
        ]
        assert status_updates
        assert status_updates[-1]["status"] == "needs_review"

    finally:
        sys.modules.pop("tasks.notifier", None)


def test_zero_row_final_update_skips_notifications(monkeypatch):
    calls = {"send": [], "check": []}
    payout_id = "zero-row-test-001"

    payout = make_payout(
        payout_id=payout_id,
        status="pending_claim",
        provider=None,
        request_id=None,
    )

    notifier, session = load_isolated_notifier(
        monkeypatch,
        [payout],
        calls,
        SimpleNamespace(
            success=True,
            provider="vtu_ng",
            reference="NPG-zero-row-test-001",
            message="Order completed",
            raw={"status": "completed-api"},
        ),
        final_update_rowcount=0,
    )

    try:
        asyncio.run(notifier.process_pending_airtime())

        # The provider is called once for the new payout.
        assert len(calls["send"]) == 1

        # The final guarded update was attempted.
        final_updates = [
            (sql, params)
            for sql, params in session.statements
            if (
                "SET status = :status" in sql
                and "provider_request_id = :request_id" in sql
            )
        ]
        assert len(final_updates) == 1

        assert calls["messages"] == []

    finally:
        sys.modules.pop("tasks.notifier", None)


def test_invalid_network_does_not_send_airtime(monkeypatch):
    calls = {"send": [], "check": []}

    payout = make_payout(
        payout_id="invalid-network-test-001",
        status="pending_claim",
        provider=None,
        request_id=None,
    )
    payout.network = "unknown_network"

    notifier, session = load_isolated_notifier(
        monkeypatch,
        [payout],
        calls,
        fake_processing_response(),
    )

    try:
        asyncio.run(notifier.process_pending_airtime())

        # Invalid network data must never trigger an airtime purchase.
        assert calls["send"] == []
        assert calls["check"] == []

    finally:
        sys.modules.pop("tasks.notifier", None)


def test_missing_phone_with_existing_request_needs_review(monkeypatch):
    calls = {"send": [], "check": []}
    request_id = "NPG-missing-phone-test-001"

    payout = make_payout(
        payout_id="missing-phone-test-001",
        status="processing",
        provider="vtu_ng",
        request_id=request_id,
    )
    payout.phone_number = None

    notifier, session = load_isolated_notifier(
        monkeypatch,
        [payout],
        calls,
        fake_processing_response(),
    )

    try:
        asyncio.run(notifier.process_pending_airtime())

        assert calls["send"] == []
        assert calls["check"] == []

        updates = [
            params
            for sql, params in session.statements
            if "INVALID_PHONE" in str(params.get("response", ""))
        ]

        assert len(updates) == 1
        assert updates[0]["status"] == "needs_review"
        assert request_id in updates[0]["response"]
        assert '"outcome": "uncertain"' in updates[0]["response"]

    finally:
        sys.modules.pop("tasks.notifier", None)


def test_invalid_amount_with_existing_request_needs_review(monkeypatch):
    calls = {"send": [], "check": []}
    request_id = "NPG-invalid-amount-test-001"

    payout = make_payout(
        payout_id="invalid-amount-test-001",
        status="processing",
        provider="vtu_ng",
        request_id=request_id,
    )
    payout.amount = 0

    notifier, session = load_isolated_notifier(
        monkeypatch,
        [payout],
        calls,
        fake_processing_response(),
    )

    try:
        asyncio.run(notifier.process_pending_airtime())

        assert calls["send"] == []
        assert calls["check"] == []

        updates = [
            params
            for sql, params in session.statements
            if "INVALID_AMOUNT" in str(params.get("response", ""))
        ]

        assert len(updates) == 1
        assert updates[0]["status"] == "needs_review"
        assert request_id in updates[0]["response"]
        assert '"outcome": "uncertain"' in updates[0]["response"]

    finally:
        sys.modules.pop("tasks.notifier", None)


def test_missing_phone_without_request_fails_permanently(monkeypatch):
    calls = {"send": [], "check": []}

    payout = make_payout(
        payout_id="missing-phone-no-request-test-001",
        status="pending_claim",
        provider="vtu_ng",
        request_id=None,
    )
    payout.phone_number = None

    notifier, session = load_isolated_notifier(
        monkeypatch,
        [payout],
        calls,
        fake_processing_response(),
    )

    try:
        asyncio.run(notifier.process_pending_airtime())

        assert calls["send"] == []
        assert calls["check"] == []

        updates = [
            params
            for sql, params in session.statements
            if "INVALID_PHONE" in str(params.get("response", ""))
        ]

        assert len(updates) == 1
        assert updates[0]["status"] == "failed_permanent"
        assert '"outcome": "not_submitted_by_this_attempt"' in updates[0]["response"]

    finally:
        sys.modules.pop("tasks.notifier", None)

def test_invalid_amount_without_request_fails_permanently(monkeypatch):
    calls = {"send": [], "check": []}

    payout = make_payout(
        payout_id="invalid-amount-no-request-test-001",
        status="pending_claim",
        provider="vtu_ng",
        request_id=None,
    )
    payout.amount = 0

    notifier, session = load_isolated_notifier(
        monkeypatch,
        [payout],
        calls,
        fake_processing_response(),
    )

    try:
        asyncio.run(notifier.process_pending_airtime())

        assert calls["send"] == []
        assert calls["check"] == []

        updates = [
            params
            for sql, params in session.statements
            if "INVALID_AMOUNT" in str(params.get("response", ""))
        ]

        assert len(updates) == 1
        assert updates[0]["status"] == "failed_permanent"
        assert (
            '"outcome": "not_submitted_by_this_attempt"'
            in updates[0]["response"]
        )
    finally:
        sys.modules.pop("tasks.notifier", None)
