# =======================================================================
# services/airtime_providers/service.py
# Provider-agnostic airtime service
# =======================================================================

from __future__ import annotations

import os
from typing import Optional

from services.airtime_providers.base import AirtimeProvider
from services.airtime_providers.clubkonnect import ClubKonnectProvider
from services.airtime_providers.types import AirtimeResult


def _get_provider() -> AirtimeProvider:
    """
    Return the currently configured airtime provider.

    Default remains ClubKonnect so the existing application behavior
    is preserved until VTU.ng is fully integrated.
    """
    provider_name = os.getenv("AIRTIME_PROVIDER", "clubkonnect").strip().lower()

    if provider_name in ("clubkonnect", "club_konnect", "ck"):
        return ClubKonnectProvider()

    raise ValueError(
        f"Unsupported airtime provider: {provider_name}"
    )


async def send_airtime(
    phone: str,
    amount: int,
    request_id: Optional[str] = None,
) -> AirtimeResult:
    """
    Provider-agnostic entry point for sending airtime.

    The selected provider is responsible for:
      - phone validation
      - network handling
      - provider API communication
      - response normalization
      - retryable/pending/completed classification

    The rest of the application only consumes AirtimeResult.
    """
    provider = _get_provider()

    return await provider.send_airtime(
        phone=phone,
        amount=amount,
        request_id=request_id,
    )


async def check_airtime_status(
    reference: str,
) -> AirtimeResult:
    """
    Provider-agnostic status/requery entry point.
    """
    provider = _get_provider()

    return await provider.check_status(
        reference=reference,
    )