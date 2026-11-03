from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import mockwaec_payments


@pytest.mark.asyncio
async def test_mockwaec_finalizer_records_verified_at(monkeypatch):
    payment = {
        "payment_reference": "test-mockwaec-001",
        "user_id": 12345,
        "payment_status": "pending",
    }

    latest_payment = {
        **payment,
        "payment_status": "successful",
        "verified_at": "verified-time",
    }

    monkeypatch.setattr(
        mockwaec_payments,
        "get_mockwaec_payment",
        AsyncMock(side_effect=[payment, latest_payment]),
    )

    claim_result = SimpleNamespace(
        first=lambda: ("test-mockwaec-001",)
    )

    session = SimpleNamespace(
        execute=AsyncMock(return_value=claim_result)
    )

    did_finalize, result = (
        await mockwaec_payments.finalize_mockwaec_payment(
            session,
            payment_reference="test-mockwaec-001",
            user_id=12345,
        )
    )

    assert did_finalize is True
    assert result["verified_at"] == "verified-time"
    assert session.execute.await_count == 1

    sql = str(session.execute.await_args.args[0])
    assert "verified_at = COALESCE(verified_at, now())" in sql
    assert "payment_status = 'successful'" in sql


@pytest.mark.asyncio
async def test_mockwaec_provisioning_sets_credited_and_processed_timestamps():
    result = SimpleNamespace(
        first=lambda: ("test-mockwaec-002",)
    )

    session = SimpleNamespace(
        execute=AsyncMock(return_value=result)
    )

    await mockwaec_payments.mark_mockwaec_payment_provisioned(
        session,
        payment_reference="test-mockwaec-002",
        user_id=12345,
    )

    sql = str(session.execute.await_args.args[0])
    params = session.execute.await_args.args[1]

    assert "credited_at = COALESCE(credited_at, now())" in sql
    assert "processed_at = COALESCE(processed_at, now())" in sql
    assert "LOWER(COALESCE(payment_status, '')) = 'successful'" in sql
    assert "payment_reference = :payment_reference" in sql
    assert "user_id = :user_id" in sql

    assert params["payment_reference"] == "test-mockwaec-002"
    assert params["user_id"] == 12345


@pytest.mark.asyncio
async def test_mockwaec_provisioning_raises_if_payment_cannot_be_updated():
    result = SimpleNamespace(first=lambda: None)

    session = SimpleNamespace(
        execute=AsyncMock(return_value=result)
    )

    with pytest.raises(
        RuntimeError,
        match="Mock WAEC payment could not be marked as provisioned",
    ):
        await mockwaec_payments.mark_mockwaec_payment_provisioned(
            session,
            payment_reference="test-mockwaec-003",
            user_id=12345,
        )