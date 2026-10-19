# =======================================================================
# services/airtime_providers/vtu_ng.py
# VTU.ng airtime provider
# =======================================================================

from __future__ import annotations

import os
import uuid
from typing import Optional

import httpx

from services.airtime_providers.base import AirtimeProvider
from services.airtime_providers.types import AirtimeResult


class VTUNgProvider(AirtimeProvider):
    """
    Airtime provider implementation for VTU.ng.

    The rest of the application does not need to know how VTU.ng works.
    It only receives an AirtimeResult.
    """

    name = "vtu_ng"

    AUTH_URL = os.getenv(
        "VTU_AUTH_URL",
        "https://vtu.ng/wp-json/jwt-auth/v1/token",
    )

    BASE_URL = os.getenv(
        "VTU_BASE_URL",
        "https://vtu.ng/wp-json/api/v2",
    ).rstrip("/")

    def __init__(self) -> None:
        self.username = os.getenv("VTU_USERNAME", "").strip()
        self.password = os.getenv("VTU_PASSWORD", "").strip()

    @staticmethod
    def normalize_phone(phone: str) -> str:
        """
        Convert common Nigerian phone formats to 11-digit format.

        Examples:
            08012345678
            +2348012345678
            2348012345678

        All become:
            08012345678
        """
        value = str(phone or "").strip()

        if value.startswith("+234"):
            value = "0" + value[4:]
        elif value.startswith("234"):
            value = "0" + value[3:]

        digits = "".join(ch for ch in value if ch.isdigit())

        if len(digits) != 11 or not digits.startswith("0"):
            raise ValueError("Invalid Nigerian phone number")

        return digits

    @staticmethod
    def guess_network(phone: str) -> Optional[str]:
        """
        Guess the Nigerian network from the phone prefix.
        """

        prefixes = {
            "mtn": {
                "0703", "0706",
                "0801", "0803", "0806",
                "0810", "0813", "0814", "0816",
                "0903", "0906",
                "0913", "0916",
            },
            "glo": {
                "0705",
                "0805", "0807",
                "0811", "0815",
                "0905", "0915",
            },
            "airtel": {
                "0701", "0708",
                "0802", "0808",
                "0812",
                "0901", "0902", "0904", "0907",
                "0912",
            },
            "9mobile": {
                "0809",
                "0817", "0818", "0819",
                "0908", "0909",
            },
        }

        for network, network_prefixes in prefixes.items():
            if phone[:4] in network_prefixes:
                return network

        return None

    async def _get_token(self) -> str:
        """
        Authenticate with VTU.ng and obtain a JWT access token.

        VTU.ng currently documents JWT authentication for its v2 API.
        """
        if not self.username:
            raise RuntimeError("VTU_USERNAME is not set")

        if not self.password:
            raise RuntimeError("VTU_PASSWORD is not set")

        payload = {
            "username": self.username,
            "password": self.password,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.AUTH_URL,
                    json=payload,
                )

                try:
                    data = response.json()
                except Exception:
                    data = {}

                if response.status_code >= 400:
                    message = (
                        data.get("message")
                        or f"VTU authentication failed (HTTP {response.status_code})"
                    )

                    raise RuntimeError(message)

                token = str(data.get("token") or "").strip()

                if not token:
                    raise RuntimeError(
                        data.get("message")
                        or "VTU.ng authentication did not return a token"
                    )

                return token

        except httpx.TimeoutException as exc:
            raise RuntimeError("VTU.ng authentication timed out") from exc

        except httpx.RequestError as exc:
            raise RuntimeError(
                f"VTU.ng authentication network error: {exc}"
            ) from exc


    @staticmethod
    def _make_request_id(request_id: Optional[str] = None) -> str:
        """Preserve supplied request IDs; generate one only when absent."""
        if request_id is not None:
            value = str(request_id).strip()

            if not value:
                raise ValueError("Supplied VTU.ng request ID cannot be empty")

            if len(value) > 50:
                raise ValueError(
                    "Supplied VTU.ng request ID must not exceed 50 characters"
                )

            return value

        return f"npg_{uuid.uuid4().hex[:32]}"


    @staticmethod
    def _build_result(
        *,
        success: bool,
        message: str,
        data: dict,
        request_id: str,
    ) -> AirtimeResult:
        """
        Convert a VTU.ng response into the application's standard result.
        """

        status = str(data.get("status") or "").lower().strip()
        order_id = data.get("order_id")

        reference = str(
            order_id
            or data.get("request_id")
            or request_id
        )

        raw = {
            "status": status,
            "message": message,
            "request_id": request_id,
            **data,
        }

        return AirtimeResult(
            success=success,
            provider="vtu_ng",
            reference=reference,
            message=message,
            raw=raw,
        )

    async def send_airtime(
        self,
        phone: str,
        amount: int,
        network: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> AirtimeResult:
        """
        Purchase airtime through VTU.ng.
        """

        try:
            normalized_phone = self.normalize_phone(phone)
        except ValueError as exc:
            return AirtimeResult(
                success=False,
                provider=self.name,
                message=str(exc),
                raw={
                    "status": "INVALID_PHONE",
                    "retryable": False,
                },
            )

        try:
            amount = int(amount)
        except (TypeError, ValueError):
            return AirtimeResult(
                success=False,
                provider=self.name,
                message="Invalid airtime amount",
                raw={
                    "status": "INVALID_AMOUNT",
                    "retryable": False,
                },
            )

        if amount <= 0:
            return AirtimeResult(
                success=False,
                provider=self.name,
                message="Airtime amount must be greater than zero",
                raw={
                    "status": "INVALID_AMOUNT",
                    "retryable": False,
                },
            )

        # Use an explicitly supplied network when available.
        # Otherwise fall back to prefix-based detection.
        network = str(network or "").strip().lower()

        if not network:
            network = self.guess_network(normalized_phone)

        # Normalize common aliases.
        network_aliases = {
            "mtn": "mtn",
            "glo": "glo",
            "airtel": "airtel",
            "9mobile": "9mobile",
            "etisalat": "9mobile",
        }

        network = network_aliases.get(network)

        if not network:
            return AirtimeResult(
                success=False,
                provider=self.name,
                message="Could not determine a valid network. Please select your network.",
                raw={
                    "status": "UNKNOWN_NETWORK",
                    "retryable": False,
                    "phone": normalized_phone,
                },
            )


        try:
            vtu_request_id = self._make_request_id(request_id)
        except ValueError as exc:
            return AirtimeResult(
                success=False,
                provider=self.name,
                message=str(exc),
                raw={
                    "status": "INVALID_REQUEST_ID",
                    "retryable": False,
                },
            )


        try:
            token = await self._get_token()

            payload = {
                "request_id": vtu_request_id,
                "phone": normalized_phone,
                "service_id": network,
                "amount": amount,
            }

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    f"{self.BASE_URL}/airtime",
                    json=payload,
                    headers=headers,
                )

                try:
                    body = response.json()
                except Exception:
                    body = {}

            if response.status_code >= 400:
                error_code = str(
                    body.get("code")
                    or body.get("status")
                    or "ERROR"
                ).strip().lower()

                error_message = str(
                    body.get("message")
                    or error_code
                    or f"VTU.ng HTTP {response.status_code}"
                )

                if error_code == "insufficient_funds":
                    normalized_status = "INSUFFICIENT_BALANCE"
                    retryable = False

                elif response.status_code == 429:
                    normalized_status = "RATE_LIMIT"
                    retryable = True

                elif response.status_code >= 500:
                    normalized_status = "PROVIDER_ERROR"
                    retryable = True

                elif error_code in (
                    "duplicate_request_id",
                    "duplicate_request",
                    "duplicate_order",
                ):
                    normalized_status = "DUPLICATE_REQUEST"
                    retryable = False

                else:
                    normalized_status = error_code.upper()
                    retryable = False

                return AirtimeResult(
                    success=False,
                    provider=self.name,
                    reference=vtu_request_id,
                    message=error_message,
                    raw={
                        "http_status": response.status_code,
                        "status": normalized_status,
                        "message": error_message,
                        "retryable": retryable,
                        "request_id": vtu_request_id,
                        "response": body,
                    },
                )

            data = body.get("data") or {}

            message = str(
                body.get("message")
                or data.get("status")
                or ""
            ).strip()

            status = str(
                data.get("status")
                or ""
            ).lower().strip()

            # ------------------------------------------------------------
            # VTU.ng successful / in-progress order states
            # ------------------------------------------------------------
            # These mean VTU.ng accepted the order and it may still be
            # processing. Our notifier will treat these as "sent".
            if status in (
                "processing-api",
                "queued-api",
                "initiated-api",
                "pending",
            ):
                return self._build_result(
                    success=True,
                    message=message or "ORDER PROCESSING",
                    data=data,
                    request_id=vtu_request_id,
                )

            # ------------------------------------------------------------
            # Completed
            # ------------------------------------------------------------
            if status == "completed-api":
                return self._build_result(
                    success=True,
                    message=message or "ORDER COMPLETED",
                    data=data,
                    request_id=vtu_request_id,
                )

            # ------------------------------------------------------------
            # Failed / cancelled / refunded / on-hold
            # ------------------------------------------------------------
            if status in (
                "failed",
                "cancelled",
                "refunded",
                "on-hold",
            ):
                return self._build_result(
                    success=False,
                    message=message or f"ORDER {status.upper()}",
                    data={
                        **data,
                        "retryable": False,
                    },
                    request_id=vtu_request_id,
                )

            # ------------------------------------------------------------
            # Unknown status
            # ------------------------------------------------------------
            # Do NOT assume an unknown response is successful.
            return self._build_result(
                success=False,
                message=message or "Unknown VTU.ng order status",
                data={
                    **data,
                    "retryable": True,
                },
                request_id=vtu_request_id,
            )

        except httpx.TimeoutException:
            return AirtimeResult(
                success=False,
                provider=self.name,
                reference=vtu_request_id,
                message="VTU.ng request timed out",
                raw={
                    "status": "TIMEOUT",
                    "retryable": True,
                    "request_id": vtu_request_id,
                },
            )

        except httpx.RequestError as exc:
            return AirtimeResult(
                success=False,
                provider=self.name,
                reference=vtu_request_id,
                message=f"VTU.ng network error: {exc}",
                raw={
                    "status": "NETWORK_ERROR",
                    "retryable": True,
                    "request_id": vtu_request_id,
                },
            )

        except Exception as exc:
            return AirtimeResult(
                success=False,
                provider=self.name,
                reference=vtu_request_id,
                message=f"VTU.ng error: {exc}",
                raw={
                    "status": "PROVIDER_ERROR",
                    "retryable": False,
                    "request_id": vtu_request_id,
                },
            )

    async def check_status(
        self,
        reference: str,
    ) -> AirtimeResult:
        """
        Requery a VTU.ng order.

        The reference passed by our application is treated as the
        original VTU.ng request_id.
        """

        request_id = str(reference or "").strip()

        if not request_id:
            return AirtimeResult(
                success=False,
                provider=self.name,
                message="Missing VTU.ng request ID",
                raw={
                    "status": "INVALID_REQUEST_ID",
                    "retryable": False,
                },
            )

        try:
            token = await self._get_token()

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.BASE_URL}/requery",
                    json={"request_id": request_id},
                    headers=headers,
                )

                try:
                    body = response.json()
                except Exception:
                    body = {}

            if response.status_code >= 400:
                message = (
                    body.get("message")
                    or body.get("code")
                    or f"VTU.ng HTTP {response.status_code}"
                )

                return AirtimeResult(
                    success=False,
                    provider=self.name,
                    reference=request_id,
                    message=str(message),
                    raw={
                        "http_status": response.status_code,
                        "status": "REQUERY_HTTP_ERROR",
                        "provider_status": str(
                            body.get("code") or "ERROR"
                        ).upper(),
                        "retryable": False,
                        "request_id": request_id,
                        "response": body,
                    },
                )



            data = body.get("data") or {}
            message = str(body.get("message") or "")
            status = str(
                data.get("status")
                or message
                or ""
            ).strip().lower()

            # Confirmed completion.
            if status in ("completed-api", "completed", "order_completed"):
                return self._build_result(
                    success=True,
                    message=message or "ORDER COMPLETED",
                    data={
                        **data,
                        "status": status,
                        "retryable": False,
                    },
                    request_id=request_id,
                )

            # Accepted or still processing: do not send again.
            if status in (
                "processing-api",
                "queued-api",
                "initiated-api",
                "pending",
                "processing",
            ):
                return self._build_result(
                    success=True,
                    message=message or "ORDER PROCESSING",
                    data={
                        **data,
                        "status": status,
                        "retryable": False,
                    },
                    request_id=request_id,
                )

            # Explicitly failed order.
            if status in (
                "failed",
                "failed-api",
                "cancelled",
                "cancelled-api",
                "refunded",
                "refunded-api",
            ):
                return self._build_result(
                    success=False,
                    message=message or status.upper(),
                    data={
                        **data,
                        "status": status,
                        "retryable": False,
                    },
                    request_id=request_id,
                )

            # Unknown response: the purchase outcome is uncertain.
            # Do not treat this as proof that no purchase occurred.
            return AirtimeResult(
                success=False,
                provider=self.name,
                reference=request_id,
                message=message or f"Unrecognized VTU.ng status: {status or 'empty'}",
                raw={
                    **data,
                    "status": "UNKNOWN_REQUERY_STATUS",
                    "provider_status": status,
                    "retryable": False,
                    "request_id": request_id,
                },
            )


        except httpx.TimeoutException:
            return AirtimeResult(
                success=False,
                provider=self.name,
                reference=request_id,
                message="VTU.ng status check timed out",
                raw={
                    "status": "TIMEOUT",
                    "retryable": True,
                    "request_id": request_id,
                },
            )

        except httpx.RequestError as exc:
            return AirtimeResult(
                success=False,
                provider=self.name,
                reference=request_id,
                message=f"VTU.ng status network error: {exc}",
                raw={
                    "status": "NETWORK_ERROR",
                    "retryable": True,
                    "request_id": request_id,
                },
            )

        except Exception as exc:
            return AirtimeResult(
                success=False,
                provider=self.name,
                reference=request_id,
                message=f"VTU.ng status error: {exc}",
                raw={
                    "status": "PROVIDER_ERROR",
                    "retryable": False,
                    "request_id": request_id,
                },
            )
