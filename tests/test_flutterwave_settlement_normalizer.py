
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from services.flutterwave_settlement_normalizer import (
    FlutterwavePayloadError,
    normalize_datetime,
    normalize_decimal,
    normalize_settlement_payload,
    normalize_transaction_payload,
)


def test_normalize_decimal():
    assert normalize_decimal("100.50") == Decimal("100.50")
    assert normalize_decimal(100) == Decimal("100.00")
    assert normalize_decimal(None) is None


def test_normalize_decimal_rejects_invalid_values():
    with pytest.raises(FlutterwavePayloadError):
        normalize_decimal("not-a-number")

    with pytest.raises(FlutterwavePayloadError):
        normalize_decimal(float("inf"))

    with pytest.raises(FlutterwavePayloadError):
        normalize_decimal(True)


def test_normalize_datetime_with_utc_z():
    result = normalize_datetime("2026-08-13T09:52:10Z")

    assert result == datetime(
        2026, 8, 13, 9, 52, 10,
        tzinfo=timezone.utc,
    )


def test_normalize_datetime_preserves_timezone():
    result = normalize_datetime(
        "2026-08-13T10:52:10+01:00"
    )

    assert result.utcoffset().total_seconds() == 3600


def test_normalize_datetime_accepts_date_only():
    result = normalize_datetime("2026-09-24")

    assert result == datetime(
        2026, 9, 24, 0, 0,
        tzinfo=timezone.utc,
    )


def test_normalize_datetime_rejects_invalid_date_only():
    with pytest.raises(FlutterwavePayloadError):
        normalize_datetime("2026-02-30")


def test_normalize_datetime_rejects_naive_datetime():
    with pytest.raises(FlutterwavePayloadError):
        normalize_datetime("2026-08-13T09:52:10")


def test_normalize_settlement_payload():
    payload = {
        "id": 6912585,
        "currency": "ngn",
        "transaction_date": "2026-08-13T00:00:00Z",
        "due_date": "2026-08-14T00:00:00Z",
        "processed_date": "2026-08-14T08:00:00Z",
        "status": "completed",
        "gross_amount": "200.00",
        "app_fee": "4.00",
        "merchant_fee": "0.00",
        "stampduty_charge": "0.00",
        "chargeback": "0.00",
        "refund": "0.00",
        "net_amount": "195.69",
        "transaction_count": 3,
        "processor_ref": "PROCESSOR-001",
        "disburse_ref": "DISBURSE-001",
        "destination": {"type": "bank"},
        "flag_message": None,
    }

    result = normalize_settlement_payload(payload)

    assert result["provider"] == "FLUTTERWAVE"
    assert result["flutterwave_settlement_id"] == "6912585"
    assert result["currency"] == "NGN"
    assert result["settlement_status"] == "completed"
    assert result["gross_amount"] == Decimal("200.00")
    assert result["app_fee"] == Decimal("4.00")
    assert result["net_amount"] == Decimal("195.69")
    assert result["transaction_count"] == 3
    assert result["vat_amount"] is None
    assert result["raw_payload"] is payload


def test_settlement_requires_id():
    with pytest.raises(FlutterwavePayloadError):
        normalize_settlement_payload({
            "currency": "NGN",
        })


def test_settlement_rejects_invalid_transaction_count():
    with pytest.raises(FlutterwavePayloadError):
        normalize_settlement_payload({
            "id": "123",
            "transaction_count": "invalid",
        })


def test_normalize_transaction_payload():
    payload = {
        "id": 2079349690,
        "tx_ref": "TRIVIA-223059B970C2",
        "charged_amount": "50.00",
        "settlement_amount": "48.92",
        "currency": "NGN",
        "status": "successful",
        "payment_entity": "card",
        "customer_email": "customer@example.com",
        "transaction_date": "2026-08-13T09:52:10Z",
        "app_fee": "1.00",
        "merchant_fee": "0.00",
        "stampduty_charge": "0.00",
    }

    result = normalize_transaction_payload(
        payload,
        settlement_id="local-settlement-uuid",
    )

    assert result["provider"] == "FLUTTERWAVE"
    assert result["flutterwave_transaction_id"] == "2079349690"
    assert result["tx_ref"] == "TRIVIA-223059B970C2"
    assert result["charged_amount"] == Decimal("50.00")
    assert result["amount_settled"] == Decimal("48.92")
    assert result["settlement_id"] == "local-settlement-uuid"
    assert result["reconciliation_status"] == "UNMATCHED"
    assert result["is_local_payment_matched"] is False
    assert result["vat_amount"] is None
    assert result["raw_payload"] is payload


def test_transaction_uses_amount_when_charged_amount_missing():
    result = normalize_transaction_payload({
        "id": "123",
        "tx_ref": "TEST-123",
        "amount": "100.00",
        "currency": "NGN",
    })

    assert result["charged_amount"] == Decimal("100.00")


def test_transaction_requires_reference():
    with pytest.raises(FlutterwavePayloadError):
        normalize_transaction_payload({
            "id": "123",
            "currency": "NGN",
        })


def test_missing_optional_transaction_fields_remain_none():
    result = normalize_transaction_payload({
        "id": "123",
        "tx_ref": "TEST-123",
        "currency": "NGN",
    })

    assert result["charged_amount"] is None
    assert result["amount_settled"] is None
    assert result["transaction_date"] is None
    assert result["customer_email"] is None
    assert result["vat_amount"] is None


def test_settlement_rejects_missing_currency():
    with pytest.raises(
        FlutterwavePayloadError,
        match="Settlement currency is missing",
    ):
        normalize_settlement_payload({
            "id": "6912585",
        })


def test_transaction_rejects_missing_currency():
    with pytest.raises(
        FlutterwavePayloadError,
        match="Transaction currency is missing",
    ):
        normalize_transaction_payload({
            "id": "2079349690",
            "tx_ref": "TRIVIA-TEST-001",
        })


def test_normalizer_rejects_invalid_currency_code():
    with pytest.raises(
        FlutterwavePayloadError,
        match="currency code is invalid",
    ):
        normalize_transaction_payload({
            "id": "2079349690",
            "tx_ref": "TRIVIA-TEST-001",
            "currency": "NAIRA",
        })

def test_normalizer_rejects_non_alphabetic_currency_code():
    with pytest.raises(
        FlutterwavePayloadError,
        match="currency code is invalid",
    ):
        normalize_transaction_payload({
            "id": "2079349690",
            "tx_ref": "TRIVIA-TEST-001",
            "currency": "12@",
        })