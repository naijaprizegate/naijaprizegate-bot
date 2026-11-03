
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import jamb_payments


@pytest.mark.asyncio
async def test_jamb_finalizer_records_timestamps_after_crediting(monkeypatch):
    payment = {
        "payment_reference": "test-jamb-001",
        "user_id": 12345,
        "amount_paid": 100,
        "question_credits_added": 10,
        "mock_sessions_added": 0,
        "payment_status": "pending",
    }

    latest_payment = {
        **payment,
        "payment_status": "successful",
        "verified_at": "verified-time",
        "credited_at": "credited-time",
        "processed_at": "processed-time",
    }

    monkeypatch.setattr(
        jamb_payments,
        "get_jamb_payment",
        AsyncMock(side_effect=[payment, latest_payment]),
    )

    claim_result = SimpleNamespace(first=lambda: ("test-jamb-001",))
    insert_result = SimpleNamespace()
    benefit_result = SimpleNamespace(rowcount=1)
    timestamp_result = SimpleNamespace()

    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                claim_result,
                insert_result,
                benefit_result,
                timestamp_result,
            ]
        )
    )

    did_credit, result, credits, mock_sessions = (
        await jamb_payments.finalize_jamb_payment(
            session,
            payment_reference="test-jamb-001",
            user_id=12345,
            amount_paid=100,
        )
    )

    assert did_credit is True
    assert result["verified_at"] == "verified-time"
    assert result["credited_at"] == "credited-time"
    assert result["processed_at"] == "processed-time"
    assert credits == 10
    assert mock_sessions == 0

    assert session.execute.await_count == 4

    timestamp_sql = str(session.execute.await_args_list[3].args[0])
    assert "credited_at = COALESCE(credited_at, now())" in timestamp_sql
    assert "processed_at = COALESCE(processed_at, now())" in timestamp_sql


@pytest.mark.asyncio
async def test_jamb_finalizer_does_not_mark_processed_if_benefit_update_fails(
    monkeypatch,
):
    payment = {
        "payment_reference": "test-jamb-002",
        "user_id": 12345,
        "amount_paid": 100,
        "question_credits_added": 10,
        "mock_sessions_added": 0,
        "payment_status": "pending",
    }

    monkeypatch.setattr(
        jamb_payments,
        "get_jamb_payment",
        AsyncMock(return_value=payment),
    )

    claim_result = SimpleNamespace(first=lambda: ("test-jamb-002",))
    insert_result = SimpleNamespace()
    benefit_result = SimpleNamespace(rowcount=0)

    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                claim_result,
                insert_result,
                benefit_result,
            ]
        )
    )

    with pytest.raises(
        RuntimeError,
        match="JAMB payment benefit update failed",
    ):
        await jamb_payments.finalize_jamb_payment(
            session,
            payment_reference="test-jamb-002",
            user_id=12345,
            amount_paid=100,
        )

    # The timestamp update must not run after a failed benefit update.
    assert session.execute.await_count == 3


@pytest.mark.asyncio
async def test_jamb_finalizer_does_not_credit_an_already_successful_payment(
    monkeypatch,
):
    payment = {
        "payment_reference": "test-jamb-003",
        "user_id": 12345,
        "amount_paid": 100,
        "question_credits_added": 10,
        "mock_sessions_added": 0,
        "payment_status": "successful",
    }

    monkeypatch.setattr(
        jamb_payments,
        "get_jamb_payment",
        AsyncMock(return_value=payment),
    )

    claim_result = SimpleNamespace(first=lambda: None)

    session = SimpleNamespace(
        execute=AsyncMock(return_value=claim_result)
    )

    did_credit, result, credits, mock_sessions = (
        await jamb_payments.finalize_jamb_payment(
            session,
            payment_reference="test-jamb-003",
            user_id=12345,
            amount_paid=100,
        )
    )

    assert did_credit is False
    assert result["payment_status"] == "successful"
    assert credits == 10
    assert mock_sessions == 0

    # Only the status-claim query runs; no benefit is credited again.
    assert session.execute.await_count == 1
