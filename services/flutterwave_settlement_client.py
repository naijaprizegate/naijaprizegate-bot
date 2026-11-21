# ================================================
# services/flutterwave_settlement_client.py
# ================================================


"""
Flutterwave V3 Settlement API client.

Read-only operations:
- Retrieve settlement records.
- Retrieve individual settlement details.
- Handle pagination and API errors.

This module does not initiate charges, transfers, or payouts.
"""

import asyncio
import logging
import os
from datetime import date
from typing import Any, Optional

import httpx


logger = logging.getLogger("flutterwave_settlement_client")


FLW_BASE_URL = os.getenv(
    "FLW_BASE_URL",
    "https://api.flutterwave.com/v3",
).rstrip("/")

FLW_SECRET_KEY = os.getenv("FLW_SECRET_KEY")


TIMEOUT = httpx.Timeout(
    connect=10.0,
    read=30.0,
    write=20.0,
    pool=20.0,
)

MAX_RETRIES = 2
RETRY_BASE_DELAY = 0.5

class FlutterwaveSettlementError(Exception):
    """Raised when a Flutterwave settlement request fails."""


def _validate_date(value: Optional[str], field_name: str) -> None:
    if value is None:
        return

    if not isinstance(value, str):
        raise ValueError(
            f"{field_name} must use YYYY-MM-DD format"
        )

    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"{field_name} must use YYYY-MM-DD format"
        ) from exc

    if parsed.isoformat() != value:
        raise ValueError(
            f"{field_name} must use YYYY-MM-DD format"
        )


class FlutterwaveSettlementClient:

    def __init__(
        self,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.secret_key = secret_key or FLW_SECRET_KEY
        self.base_url = (
            base_url or FLW_BASE_URL
        ).rstrip("/")

        if not self.secret_key:
            raise FlutterwaveSettlementError(
                "Missing FLW_SECRET_KEY"
            )

        self.headers = {
            "Authorization": f"Bearer {self.secret_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }


    async def _get(
        self,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:

        url = f"{self.base_url}{endpoint}"

        for attempt in range(MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=TIMEOUT
                ) as client:

                    response = await client.get(
                        url,
                        headers=self.headers,
                        params=params,
                    )

                    response.raise_for_status()
                    payload = response.json()

                break

            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code

                retryable = (
                    status_code == 429
                    or 500 <= status_code <= 599
                    or (
                        status_code == 400
                        and endpoint.startswith("/settlements/")
                    )
                )

                if retryable and attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)

                    logger.warning(
                        f"Temporary Flutterwave HTTP {status_code} error; "
                        f"retrying request in {delay:.1f} seconds "
                        f"(attempt {attempt + 1} of {MAX_RETRIES})"
                    )

                    await asyncio.sleep(delay)
                    continue

                logger.error(
                    "Flutterwave settlement API returned HTTP %s",
                    status_code,
                )

                raise FlutterwaveSettlementError(
                    f"Flutterwave API HTTP error: {status_code}"
                ) from exc

            except httpx.RequestError as exc:
                if attempt < MAX_RETRIES:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)

                    logger.warning(
                        f"Flutterwave connection error; "
                        f"retrying in {delay:.1f} seconds "
                        f"(attempt {attempt + 1} of {MAX_RETRIES})"
                    )

                    await asyncio.sleep(delay)
                    continue

                logger.error(
                    "Flutterwave settlement API connection failed "
                    "after %s attempts",
                    MAX_RETRIES + 1,
                )

                raise FlutterwaveSettlementError(
                    "Unable to connect to Flutterwave settlement API"
                ) from exc

            except ValueError as exc:
                logger.exception(
                    "Invalid JSON returned by Flutterwave"
                )

                raise FlutterwaveSettlementError(
                    "Flutterwave returned an invalid response"
                ) from exc

        if not isinstance(payload, dict):
            raise FlutterwaveSettlementError(
                "Unexpected Flutterwave response format"
            )

        if payload.get("status") != "success":
            raise FlutterwaveSettlementError(
                payload.get("message")
                or "Flutterwave settlement request was unsuccessful"
            )

        return payload


    async def get_settlements_page(
        self,
        page: int = 1,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Retrieve one page of settlement records.

        Flutterwave V3 supports:
        - page
        - from (YYYY-MM-DD)
        - to (YYYY-MM-DD)
        """

        if page < 1:
            raise ValueError("page must be at least 1")

        _validate_date(from_date, "from_date")
        _validate_date(to_date, "to_date")

        if from_date and to_date and from_date > to_date:
            raise ValueError(
                "from_date cannot be later than to_date"
            )

        params: dict[str, Any] = {
            "page": page,
        }

        if from_date:
            params["from"] = from_date

        if to_date:
            params["to"] = to_date

        payload = await self._get(
            "/settlements",
            params=params,
        )

        if not isinstance(payload.get("data"), list):
            raise FlutterwaveSettlementError(
                "Settlement list response has no valid data array"
            )

        return payload

    async def get_all_settlements(
        self,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        max_pages: int = 1000,
    ) -> list[dict[str, Any]]:
        """
        Retrieve all available settlement pages.

        Uses Flutterwave's pagination metadata when available.
        If total_pages is unavailable, continues until an
        empty page is returned.

        max_pages prevents an endless pagination loop.
        """

        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")

        settlements: list[dict[str, Any]] = []

        page = 1
        total_pages: Optional[int] = None

        while page <= max_pages:

            payload = await self.get_settlements_page(
                page=page,
                from_date=from_date,
                to_date=to_date,
            )

            page_data = payload["data"]

            if not page_data:
                break

            settlements.extend(page_data)

            page_info = (
                (payload.get("meta") or {}).get("page_info")
                or {}
            )

            reported_total_pages = page_info.get("total_pages")

            if reported_total_pages is not None:
                try:
                    total_pages = int(reported_total_pages)
                except (TypeError, ValueError) as exc:
                    raise FlutterwaveSettlementError(
                        "Invalid settlement pagination metadata"
                    ) from exc

                if total_pages < 1:
                    raise FlutterwaveSettlementError(
                        "Invalid settlement total_pages value"
                    )

                if page >= total_pages:
                    break

            page += 1

        else:
            raise FlutterwaveSettlementError(
                f"Settlement pagination exceeded {max_pages} pages"
            )

        return settlements

    async def get_transactions_page(
        self,
        from_date: str,
        to_date: str,
        page: int = 1,
        status: Optional[str] = None,
        tx_ref: Optional[str] = None,
        customer_email: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Retrieve one page of Flutterwave V3 transactions.

        Both from_date and to_date are required by Flutterwave.

        Supported optional filters:
        - status
        - tx_ref
        - customer_email
        """

        _validate_date(from_date, "from_date")
        _validate_date(to_date, "to_date")

        if not from_date or not to_date:
            raise ValueError(
                "Both from_date and to_date are required"
            )

        if from_date > to_date:
            raise ValueError(
                "from_date cannot be later than to_date"
            )

        if page < 1:
            raise ValueError("page must be at least 1")

        params: dict[str, Any] = {
            "from": from_date,
            "to": to_date,
            "page": page,
        }

        if status:
            params["status"] = status

        if tx_ref:
            params["tx_ref"] = tx_ref

        if customer_email:
            params["customer_email"] = customer_email

        payload = await self._get(
            "/transactions",
            params=params,
        )

        if not isinstance(payload.get("data"), list):
            raise FlutterwaveSettlementError(
                "Transaction list response has no valid data array"
            )

        return payload

    async def get_all_transactions(
        self,
        from_date: str,
        to_date: str,
        status: Optional[str] = None,
        tx_ref: Optional[str] = None,
        customer_email: Optional[str] = None,
        max_pages: int = 1000,
    ) -> list[dict[str, Any]]:
        """
        Retrieve all transaction pages for a date range.

        Stops using pagination metadata when available.
        Otherwise, continues until an empty page is returned.
        """

        if max_pages < 1:
            raise ValueError(
                "max_pages must be at least 1"
            )

        transactions: list[dict[str, Any]] = []

        page = 1
        total_pages: Optional[int] = None

        while page <= max_pages:

            payload = await self.get_transactions_page(
                from_date=from_date,
                to_date=to_date,
                page=page,
                status=status,
                tx_ref=tx_ref,
                customer_email=customer_email,
            )

            page_data = payload["data"]

            if not page_data:
                break

            transactions.extend(page_data)

            page_info = (
                (payload.get("meta") or {}).get("page_info")
                or {}
            )

            reported_total_pages = page_info.get(
                "total_pages"
            )

            if reported_total_pages is not None:
                try:
                    total_pages = int(
                        reported_total_pages
                    )
                except (TypeError, ValueError) as exc:
                    raise FlutterwaveSettlementError(
                        "Invalid transaction pagination metadata"
                    ) from exc

                if total_pages < 1:
                    raise FlutterwaveSettlementError(
                        "Invalid transaction total_pages value"
                    )

                if page >= total_pages:
                    break

            page += 1

        else:
            raise FlutterwaveSettlementError(
                f"Transaction pagination exceeded {max_pages} pages"
            )

        return transactions

    async def get_settlement(
        self,
        settlement_id: str | int,
        max_pages: int = 1000,
    ) -> dict[str, Any]:
        """
        Retrieve an individual settlement and all its
        associated transaction pages.
        """
        if settlement_id is None or not str(settlement_id).strip():
            raise ValueError("settlement_id is required")

        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")

        endpoint = f"/settlements/{settlement_id}"

        # Page 1 is requested without an explicit page parameter.
        payload = await self._get(endpoint)

        data = payload.get("data")

        if not isinstance(data, dict):
            raise FlutterwaveSettlementError(
                "Settlement detail response has no valid data object"
            )

        transactions = data.get("transactions")

        if not isinstance(transactions, list):
            raise FlutterwaveSettlementError(
                "Settlement detail response has no valid transactions list"
            )

        all_transactions = list(transactions)

        if not all_transactions:
            return data

        page_info = (
            (payload.get("meta") or {}).get("page_info") or {}
        )

        reported_total_pages = page_info.get("total_pages")

        total_pages: Optional[int] = None

        if reported_total_pages is not None:
            try:
                total_pages = int(reported_total_pages)
            except (TypeError, ValueError) as exc:
                raise FlutterwaveSettlementError(
                    "Invalid settlement detail pagination metadata"
                ) from exc

            if total_pages < 1:
                raise FlutterwaveSettlementError(
                    "Invalid settlement detail total_pages value"
                )

            if total_pages > max_pages:
                raise FlutterwaveSettlementError(
                    f"Settlement detail pagination exceeded {max_pages} pages"
                )

        page = 2

        while page <= max_pages:
            # When metadata exists, stop after the reported last page.
            if total_pages is not None and page > total_pages:
                break

            # Without metadata, continue until an empty page is returned.
            page_payload = await self._get(
                endpoint,
                params={"page": page},
            )

            page_data = page_payload.get("data")

            if not isinstance(page_data, dict):
                raise FlutterwaveSettlementError(
                    "Settlement detail page has no valid data object"
                )

            page_transactions = page_data.get("transactions")

            if not isinstance(page_transactions, list):
                raise FlutterwaveSettlementError(
                    "Settlement detail page has no valid transactions list"
                )

            if not page_transactions:
                if total_pages is not None:
                    raise FlutterwaveSettlementError(
                        "Settlement detail returned an unexpected empty page"
                    )
                break

            all_transactions.extend(page_transactions)
            page += 1

        else:
            raise FlutterwaveSettlementError(
                f"Settlement detail pagination exceeded {max_pages} pages"
            )

        # Preserve the original settlement summary and replace only
        # its transactions with the complete combined transaction list.
        data["transactions"] = all_transactions

        return data
