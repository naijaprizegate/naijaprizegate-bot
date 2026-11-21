
import asyncio

import pytest

import httpx

from services import flutterwave_settlement_client as settlement_module
from services.flutterwave_settlement_client import (
    FlutterwaveSettlementClient,
    FlutterwaveSettlementError,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeAsyncClient:
    def __init__(self, responses, **kwargs):
        self.responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None, params=None):
        self.calls.append({
            "url": url,
            "headers": headers,
            "params": params,
        })

        if not self.responses:
            raise AssertionError("Unexpected HTTP request")

        response = self.responses.pop(0)

        if isinstance(response, Exception):
            raise response

        return response


def make_client():
    return FlutterwaveSettlementClient(
        secret_key="test-secret-key",
        base_url="https://example.test/v3",
    )


def test_get_settlements_page(monkeypatch):
    fake = FakeAsyncClient([
        FakeResponse({
            "status": "success",
            "data": [
                {"id": "SETTLEMENT-001", "net_amount": 9500}
            ],
        })
    ])

    monkeypatch.setattr(
        settlement_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake,
    )

    async def scenario():
        client = make_client()

        result = await client.get_settlements_page(
            page=1,
            from_date="2026-11-01",
            to_date="2026-11-10",
        )

        assert result["status"] == "success"
        assert result["data"][0]["id"] == "SETTLEMENT-001"

        assert fake.calls[0]["url"] == (
            "https://example.test/v3/settlements"
        )

        assert fake.calls[0]["params"] == {
            "page": 1,
            "from": "2026-11-01",
            "to": "2026-11-10",
        }

    asyncio.run(scenario())


def test_get_all_settlements_handles_pagination(monkeypatch):
    fake = FakeAsyncClient([
        FakeResponse({
            "status": "success",
            "data": [{"id": "SETTLEMENT-001"}],
            "meta": {
                "page_info": {"total_pages": 2}
            },
        }),
        FakeResponse({
            "status": "success",
            "data": [{"id": "SETTLEMENT-002"}],
            "meta": {
                "page_info": {"total_pages": 2}
            },
        }),
    ])

    monkeypatch.setattr(
        settlement_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake,
    )

    async def scenario():
        client = make_client()

        result = await client.get_all_settlements(
            from_date="2026-11-01",
            to_date="2026-11-10",
        )

        assert len(result) == 2
        assert result[0]["id"] == "SETTLEMENT-001"
        assert result[1]["id"] == "SETTLEMENT-002"

        assert len(fake.calls) == 2
        assert fake.calls[0]["params"]["page"] == 1
        assert fake.calls[1]["params"]["page"] == 2

    asyncio.run(scenario())


def test_get_settlement_details(monkeypatch):
    fake = FakeAsyncClient([
        FakeResponse({
            "status": "success",
            "data": {
                "id": "SETTLEMENT-001",
                "gross_amount": 10000,
                "net_amount": 9750,
                "transactions": [],
            },
        })
    ])

    monkeypatch.setattr(
        settlement_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake,
    )

    async def scenario():
        client = make_client()

        result = await client.get_settlement(
            "SETTLEMENT-001"
        )

        assert result["id"] == "SETTLEMENT-001"
        assert result["gross_amount"] == 10000
        assert result["net_amount"] == 9750

        assert fake.calls[0]["url"].endswith(
            "/settlements/SETTLEMENT-001"
        )

    asyncio.run(scenario())



def test_get_settlement_details_handles_transaction_pagination(monkeypatch):
    fake = FakeAsyncClient([
        FakeResponse({
            "status": "success",
            "data": {
                "id": "SETTLEMENT-002",
                "gross_amount": 300,
                "net_amount": 293.55,
                "transactions": [
                    {"id": 101, "tx_ref": "TRIVIA-001"},
                    {"id": 102, "tx_ref": "JAMB-001"},
                ],
            },
            "meta": {
                "page_info": {
                    "total_pages": 2,
                }
            },
        }),
        FakeResponse({
            "status": "success",
            "data": {
                "id": "SETTLEMENT-002",
                "transactions": [
                    {"id": 103, "tx_ref": "WAEC-001"},
                ],
            },
            "meta": {
                "page_info": {
                    "total_pages": 2,
                }
            },
        }),
    ])

    monkeypatch.setattr(
        settlement_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake,
    )

    async def scenario():
        client = make_client()

        result = await client.get_settlement("SETTLEMENT-002")

        assert result["id"] == "SETTLEMENT-002"
        assert result["gross_amount"] == 300
        assert result["net_amount"] == 293.55

        assert len(result["transactions"]) == 3

        assert [
            transaction["id"]
            for transaction in result["transactions"]
        ] == [101, 102, 103]

        assert fake.calls[0]["url"].endswith(
            "/settlements/SETTLEMENT-002"
        )
        assert fake.calls[0]["params"] is None

        assert fake.calls[1]["url"].endswith(
            "/settlements/SETTLEMENT-002"
        )
        assert fake.calls[1]["params"] == {"page": 2}

    asyncio.run(scenario())



def test_get_settlement_details_without_pagination_metadata(monkeypatch):
    fake = FakeAsyncClient([
        FakeResponse({
            "status": "success",
            "data": {
                "id": "SETTLEMENT-003",
                "gross_amount": 200,
                "net_amount": 195.70,
                "transactions": [
                    {"id": 201, "tx_ref": "TRIVIA-002"},
                ],
            },
        }),
        FakeResponse({
            "status": "success",
            "data": {
                "id": "SETTLEMENT-003",
                "transactions": [
                    {"id": 202, "tx_ref": "JAMB-002"},
                ],
            },
        }),
        FakeResponse({
            "status": "success",
            "data": {
                "id": "SETTLEMENT-003",
                "transactions": [],
            },
        }),
    ])

    monkeypatch.setattr(
        settlement_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake,
    )

    async def scenario():
        client = make_client()

        result = await client.get_settlement("SETTLEMENT-003")

        assert result["gross_amount"] == 200
        assert result["net_amount"] == 195.70

        assert [
            transaction["id"]
            for transaction in result["transactions"]
        ] == [201, 202]

        assert len(fake.calls) == 3
        assert fake.calls[0]["params"] is None
        assert fake.calls[1]["params"] == {"page": 2}
        assert fake.calls[2]["params"] == {"page": 3}

    asyncio.run(scenario())


def test_empty_settlement_list(monkeypatch):
    fake = FakeAsyncClient([
        FakeResponse({
            "status": "success",
            "data": [],
        })
    ])

    monkeypatch.setattr(
        settlement_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake,
    )

    async def scenario():
        client = make_client()

        result = await client.get_all_settlements()

        assert result == []

    asyncio.run(scenario())


def test_missing_secret_key_is_rejected(monkeypatch):
    monkeypatch.setattr(
        settlement_module,
        "FLW_SECRET_KEY",
        None,
    )

    with pytest.raises(FlutterwaveSettlementError):
        FlutterwaveSettlementClient()


@pytest.mark.parametrize(
    "invalid_date",
    [
        "2026/11/01",
        "11-01-2026",
        "not-a-date",
    ],
)
def test_invalid_date_is_rejected(invalid_date):
    client = make_client()

    with pytest.raises(ValueError):
        asyncio.run(
            client.get_settlements_page(
                from_date=invalid_date
            )
        )


def test_reversed_date_range_is_rejected():
    client = make_client()

    with pytest.raises(ValueError):
        asyncio.run(
            client.get_settlements_page(
                from_date="2026-11-10",
                to_date="2026-11-01",
            )
        )


def test_invalid_page_number_is_rejected():
    client = make_client()

    with pytest.raises(ValueError):
        asyncio.run(
            client.get_settlements_page(page=0)
        )


def test_empty_settlement_id_is_rejected():
    client = make_client()

    with pytest.raises(ValueError):
        asyncio.run(
            client.get_settlement(" ")
        )


def test_http_error_is_reported(monkeypatch):
    request = httpx.Request(
        "GET",
        "https://example.test/v3/settlements",
    )
    response = httpx.Response(
        401,
        request=request,
    )

    error = httpx.HTTPStatusError(
        "Unauthorized",
        request=request,
        response=response,
    )

    fake = FakeAsyncClient([error])

    monkeypatch.setattr(
        settlement_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake,
    )

    async def scenario():
        client = make_client()

        with pytest.raises(FlutterwaveSettlementError):
            await client.get_settlements_page()

        assert len(fake.calls) == 1

    asyncio.run(scenario())



def test_connection_error_is_reported(monkeypatch):
    request = httpx.Request(
        "GET",
        "https://example.test/v3/settlements",
    )

    fake = FakeAsyncClient([
        httpx.ConnectError("Connection failed", request=request),
        httpx.ConnectError("Connection failed", request=request),
        httpx.ConnectError("Connection failed", request=request),
    ])

    async def no_sleep(delay):
        pass

    monkeypatch.setattr(
        settlement_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake,
    )

    monkeypatch.setattr(
        settlement_module.asyncio,
        "sleep",
        no_sleep,
    )

    async def scenario():
        client = make_client()

        with pytest.raises(FlutterwaveSettlementError):
            await client.get_settlements_page()

        assert len(fake.calls) == 3

    asyncio.run(scenario())


def test_connection_error_recovers_on_retry(monkeypatch):
    request = httpx.Request(
        "GET",
        "https://example.test/v3/settlements",
    )

    fake = FakeAsyncClient([
        httpx.ConnectError("Connection failed", request=request),
        FakeResponse({
            "status": "success",
            "data": [
                {"id": "SETTLEMENT-RETRY-001"}
            ],
        }),
    ])

    delays = []

    async def no_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(
        settlement_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake,
    )

    monkeypatch.setattr(
        settlement_module.asyncio,
        "sleep",
        no_sleep,
    )

    async def scenario():
        client = make_client()

        result = await client.get_settlements_page()

        assert result["status"] == "success"
        assert result["data"][0]["id"] == "SETTLEMENT-RETRY-001"

        assert len(fake.calls) == 2
        assert delays == [0.5]

    asyncio.run(scenario())



def test_http_500_recovers_on_retry(monkeypatch):
    request = httpx.Request(
        "GET",
        "https://example.test/v3/settlements",
    )

    error_response = httpx.Response(
        500,
        request=request,
    )

    fake = FakeAsyncClient([
        httpx.HTTPStatusError(
            "Server error",
            request=request,
            response=error_response,
        ),
        FakeResponse({
            "status": "success",
            "data": [
                {"id": "SETTLEMENT-RETRY-002"}
            ],
        }),
    ])

    delays = []

    async def no_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(
        settlement_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake,
    )

    monkeypatch.setattr(
        settlement_module.asyncio,
        "sleep",
        no_sleep,
    )

    async def scenario():
        client = make_client()

        result = await client.get_settlements_page()

        assert result["status"] == "success"
        assert len(fake.calls) == 2
        assert delays == [0.5]

    asyncio.run(scenario())


def test_http_500_retry_exhaustion(monkeypatch):
    request = httpx.Request(
        "GET",
        "https://example.test/v3/settlements",
    )

    errors = []

    for _ in range(3):
        response = httpx.Response(
            500,
            request=request,
        )

        errors.append(
            httpx.HTTPStatusError(
                "Server error",
                request=request,
                response=response,
            )
        )

    fake = FakeAsyncClient(errors)

    async def no_sleep(delay):
        pass

    monkeypatch.setattr(
        settlement_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake,
    )

    monkeypatch.setattr(
        settlement_module.asyncio,
        "sleep",
        no_sleep,
    )

    async def scenario():
        client = make_client()

        with pytest.raises(FlutterwaveSettlementError):
            await client.get_settlements_page()

        assert len(fake.calls) == 3

    asyncio.run(scenario())


def test_flutterwave_unsuccessful_status_is_rejected(monkeypatch):
    fake = FakeAsyncClient([
        FakeResponse({
            "status": "error",
            "message": "Invalid credentials",
            "data": [],
        })
    ])

    monkeypatch.setattr(
        settlement_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake,
    )

    async def scenario():
        client = make_client()

        with pytest.raises(FlutterwaveSettlementError):
            await client.get_settlements_page()

    asyncio.run(scenario())


def test_malformed_settlement_list_is_rejected(monkeypatch):
    fake = FakeAsyncClient([
        FakeResponse({
            "status": "success",
            "data": None,
        })
    ])

    monkeypatch.setattr(
        settlement_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake,
    )

    async def scenario():
        client = make_client()

        with pytest.raises(FlutterwaveSettlementError):
            await client.get_settlements_page()

    asyncio.run(scenario())


def test_pagination_max_pages_is_enforced(monkeypatch):
    fake = FakeAsyncClient([
        FakeResponse({
            "status": "success",
            "data": [{"id": "SETTLEMENT-001"}],
        }),
        FakeResponse({
            "status": "success",
            "data": [{"id": "SETTLEMENT-002"}],
        }),
    ])

    monkeypatch.setattr(
        settlement_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake,
    )

    async def scenario():
        client = make_client()

        with pytest.raises(FlutterwaveSettlementError):
            await client.get_all_settlements(
                max_pages=1
            )

    asyncio.run(scenario())


def test_get_transactions_page_with_filters(monkeypatch):
    fake = FakeAsyncClient([
        FakeResponse({
            "status": "success",
            "data": [
                {
                    "id": 12345,
                    "tx_ref": "NPG-TEST-001",
                    "status": "successful",
                    "charged_amount": 1000,
                }
            ],
        })
    ])

    monkeypatch.setattr(
        settlement_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake,
    )

    async def scenario():
        client = make_client()

        result = await client.get_transactions_page(
            from_date="2026-11-01",
            to_date="2026-11-10",
            page=2,
            status="successful",
            tx_ref="NPG-TEST-001",
        )

        assert result["data"][0]["tx_ref"] == "NPG-TEST-001"

        assert fake.calls[0]["url"].endswith("/transactions")

        assert fake.calls[0]["params"] == {
            "from": "2026-11-01",
            "to": "2026-11-10",
            "page": 2,
            "status": "successful",
            "tx_ref": "NPG-TEST-001",
        }

    asyncio.run(scenario())


def test_transaction_dates_are_required():
    client = make_client()

    with pytest.raises(ValueError):
        asyncio.run(
            client.get_transactions_page(
                from_date="2026-11-01",
                to_date=None,
            )
        )


def test_get_all_transactions_handles_pagination(monkeypatch):
    fake = FakeAsyncClient([
        FakeResponse({
            "status": "success",
            "data": [
                {"id": 101, "tx_ref": "NPG-001"}
            ],
            "meta": {
                "page_info": {"total_pages": 2}
            },
        }),
        FakeResponse({
            "status": "success",
            "data": [
                {"id": 102, "tx_ref": "NPG-002"}
            ],
            "meta": {
                "page_info": {"total_pages": 2}
            },
        }),
    ])

    monkeypatch.setattr(
        settlement_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake,
    )

    async def scenario():
        client = make_client()

        result = await client.get_all_transactions(
            from_date="2026-11-01",
            to_date="2026-11-10",
            status="successful",
        )

        assert len(result) == 2
        assert result[0]["id"] == 101
        assert result[1]["id"] == 102

        assert len(fake.calls) == 2
        assert fake.calls[0]["params"]["page"] == 1
        assert fake.calls[1]["params"]["page"] == 2

    asyncio.run(scenario())


def test_empty_transaction_list(monkeypatch):
    fake = FakeAsyncClient([
        FakeResponse({
            "status": "success",
            "data": [],
        })
    ])

    monkeypatch.setattr(
        settlement_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake,
    )

    async def scenario():
        client = make_client()

        result = await client.get_all_transactions(
            from_date="2026-11-01",
            to_date="2026-11-10",
        )

        assert result == []

    asyncio.run(scenario())


def test_malformed_transaction_list_is_rejected(monkeypatch):
    fake = FakeAsyncClient([
        FakeResponse({
            "status": "success",
            "data": None,
        })
    ])

    monkeypatch.setattr(
        settlement_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake,
    )

    async def scenario():
        client = make_client()

        with pytest.raises(FlutterwaveSettlementError):
            await client.get_transactions_page(
                from_date="2026-11-01",
                to_date="2026-11-10",
            )

    asyncio.run(scenario())


def test_transaction_pagination_max_pages_is_enforced(monkeypatch):
    fake = FakeAsyncClient([
        FakeResponse({
            "status": "success",
            "data": [{"id": 101}],
        }),
        FakeResponse({
            "status": "success",
            "data": [{"id": 102}],
        }),
    ])

    monkeypatch.setattr(
        settlement_module.httpx,
        "AsyncClient",
        lambda **kwargs: fake,
    )

    async def scenario():
        client = make_client()

        with pytest.raises(FlutterwaveSettlementError):
            await client.get_all_transactions(
                from_date="2026-11-01",
                to_date="2026-11-10",
                max_pages=1,
            )

    asyncio.run(scenario())
