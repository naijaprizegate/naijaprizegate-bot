
# ================================================
# services/flutterwave_settlement_normalizer.py
# ================================================

"""
Normalize Flutterwave V3 settlement and transaction
payloads into database-ready dictionaries.

This module:
- Does not make API requests.
- Does not write to the database.
- Does not modify local customer payment records.
- Preserves original API payloads for auditing.
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


class FlutterwavePayloadError(Exception):
    """Raised when a Flutterwave payload cannot be normalized."""


def normalize_text(value: Any) -> str | None:
    """Normalize optional text without inventing missing values."""
    if value is None:
        return None

    result = str(value).strip()
    return result or None


def normalize_currency(value: Any) -> str | None:
    """Normalize an optional currency code."""
    result = normalize_text(value)
    return result.upper() if result else None


def require_currency(value: Any, label: str) -> str:
    """Require a valid currency code."""
    currency = normalize_currency(value)

    if not currency:
        raise FlutterwavePayloadError(
            f"{label} currency is missing"
        )

    if (
        len(currency) != 3
        or not currency.isascii()
        or not currency.isalpha()
    ):
        raise FlutterwavePayloadError(
            f"{label} currency code is invalid"
        )

    return currency


def normalize_decimal(value: Any) -> Decimal | None:
    """
    Convert an optional monetary value to Decimal.

    Missing values remain None. Invalid or non-finite values
    raise FlutterwavePayloadError.
    """
    if value is None:
        return None

    if isinstance(value, bool):
        raise FlutterwavePayloadError(
            "Boolean values are not valid monetary amounts"
        )

    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise FlutterwavePayloadError(
            f"Invalid monetary amount: {value!r}"
        ) from None

    if not amount.is_finite():
        raise FlutterwavePayloadError(
            "Monetary amount must be finite"
        )

    return amount.quantize(Decimal("0.01"))


def normalize_datetime(value: Any) -> datetime | None:
    """
    Convert an ISO 8601 timestamp or date-only value into
    an aware datetime.

    Date-only values are interpreted as midnight UTC.
    Naive timestamps are rejected because their timezone
    cannot safely be inferred.
    """
    if value is None:
        return None

    if isinstance(value, datetime):
        parsed = value
    else:
        text_value = normalize_text(value)

        if not text_value:
            return None

        # Flutterwave may return date-only values such as:
        # 2026-09-24
        if len(text_value) == 10:
            try:
                parsed = datetime.strptime(
                    text_value,
                    "%Y-%m-%d",
                ).replace(tzinfo=timezone.utc)
            except ValueError:
                raise FlutterwavePayloadError(
                    f"Invalid ISO 8601 date: {value!r}"
                ) from None

            return parsed

        try:
            parsed = datetime.fromisoformat(
                text_value.replace("Z", "+00:00")
            )
        except ValueError:
            raise FlutterwavePayloadError(
                f"Invalid ISO 8601 datetime: {value!r}"
            ) from None

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FlutterwavePayloadError(
            f"Datetime must include a timezone: {value!r}"
        )

    return parsed


def _require_identifier(
    payload: dict,
    field: str,
    label: str,
) -> str:
    value = normalize_text(payload.get(field))

    if not value:
        raise FlutterwavePayloadError(
            f"Flutterwave {label} is missing"
        )

    return value


def normalize_settlement_payload(
    payload: dict,
) -> dict:
    """
    Convert one Flutterwave settlement summary into
    FlutterwaveSettlement-compatible fields.
    """
    if not isinstance(payload, dict):
        raise FlutterwavePayloadError(
            "Settlement payload must be a dictionary"
        )

    settlement_id = _require_identifier(
        payload,
        "id",
        "settlement ID",
    )

    transaction_count = payload.get("transaction_count")

    if transaction_count is not None:
        if isinstance(transaction_count, bool):
            raise FlutterwavePayloadError(
                "Settlement transaction_count is invalid"
            )

        try:
            transaction_count = int(transaction_count)
        except (TypeError, ValueError):
            raise FlutterwavePayloadError(
                "Settlement transaction_count is invalid"
            ) from None

        if transaction_count < 0:
            raise FlutterwavePayloadError(
                "Settlement transaction_count cannot be negative"
            )

    destination = payload.get("destination")

    if destination is not None and not isinstance(destination, dict):
        raise FlutterwavePayloadError(
            "Settlement destination must be an object"
        )

    return {
        "provider": "FLUTTERWAVE",
        "flutterwave_settlement_id": settlement_id,
        "currency": require_currency(
            payload.get("currency"),
            "Settlement",
        ),

        "transaction_date": normalize_datetime(
            payload.get("transaction_date")
        ),
        "due_date": normalize_datetime(
            payload.get("due_date")
        ),
        "processed_date": normalize_datetime(
            payload.get("processed_date")
        ),

        "settlement_status": normalize_text(
            payload.get("status")
        ),

        "gross_amount": normalize_decimal(
            payload.get("gross_amount")
        ),
        "app_fee": normalize_decimal(
            payload.get("app_fee")
        ),
        "merchant_fee": normalize_decimal(
            payload.get("merchant_fee")
        ),
        "stamp_duty": normalize_decimal(
            payload.get("stampduty_charge")
        ),
        "chargeback_amount": normalize_decimal(
            payload.get("chargeback")
        ),
        "refund_amount": normalize_decimal(
            payload.get("refund")
        ),

        # Only use VAT when Flutterwave explicitly supplies it.
        "vat_amount": normalize_decimal(
            payload.get("vat_amount")
        ),

        "net_amount": normalize_decimal(
            payload.get("net_amount")
        ),

        "transaction_count": transaction_count,
        "processor_ref": normalize_text(
            payload.get("processor_ref")
        ),
        "disburse_ref": normalize_text(
            payload.get("disburse_ref")
        ),
        "destination": destination,
        "flag_message": normalize_text(
            payload.get("flag_message")
        ),

        "raw_payload": payload,
    }


def normalize_transaction_payload(
    payload: dict,
    settlement_id: Any = None,
) -> dict:
    """
    Convert one Flutterwave settlement transaction into
    FlutterwaveCollectionTransaction-compatible fields.

    settlement_id is the local UUID of the parent settlement
    database record, not Flutterwave's settlement ID.
    """
    if not isinstance(payload, dict):
        raise FlutterwavePayloadError(
            "Transaction payload must be a dictionary"
        )

    transaction_id = normalize_text(payload.get("id"))
    reference = normalize_text(payload.get("tx_ref"))

    if not reference:
        raise FlutterwavePayloadError(
            "Flutterwave transaction reference (tx_ref) is missing"
        )

    # Flutterwave's nested settlement transaction payload may
    # provide charged_amount or amount. Prefer charged_amount.
    charged_amount_value = payload.get("charged_amount")

    if charged_amount_value is None:
        charged_amount_value = payload.get("amount")

    return {
        "provider": "FLUTTERWAVE",
        "flutterwave_transaction_id": transaction_id,
        "tx_ref": reference,

        "local_payment_source": None,
        "local_payment_id": None,
        "settlement_id": settlement_id,

        "currency": require_currency(
            payload.get("currency"),
            "Transaction",
        ),

        "transaction_status": normalize_text(
            payload.get("status")
        ),
        "payment_entity": normalize_text(
            payload.get("payment_entity")
        ),
        "customer_email": normalize_text(
            payload.get("customer_email")
        ),

        "transaction_date": normalize_datetime(
            payload.get("transaction_date")
        ),
        "settlement_date": normalize_datetime(
            payload.get("settlement_date")
        ),

        "charged_amount": normalize_decimal(
            charged_amount_value
        ),
        "amount_settled": normalize_decimal(
            payload.get("settlement_amount")
        ),

        "app_fee": normalize_decimal(
            payload.get("app_fee")
        ),
        "merchant_fee": normalize_decimal(
            payload.get("merchant_fee")
        ),
        "stamp_duty": normalize_decimal(
            payload.get("stampduty_charge")
        ),

        # Do not estimate VAT from fees.
        "vat_amount": normalize_decimal(
            payload.get("vat_amount")
        ),

        "is_local_payment_matched": False,
        "reconciliation_status": "UNMATCHED",

        "raw_payload": payload,
    }
