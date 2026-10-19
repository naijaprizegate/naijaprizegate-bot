# =======================================================================
# services/airtime_providers/service.py
# Provider-agnostic airtime service
# =======================================================================


from __future__ import annotations

import os
from typing import Optional

from services.airtime_providers.base import AirtimeProvider
from services.airtime_providers.clubkonnect import ClubKonnectProvider
from services.airtime_providers.vtu_ng import VTUNgProvider
from services.airtime_providers.types import AirtimeResult


def _get_provider(
    provider_name: Optional[str] = None,
) -> AirtimeProvider:
    """
    Return the requested provider.

    If provider_name is supplied, use that provider.
    Otherwise, use AIRTIME_PROVIDER from the environment.
    """
    if provider_name is None:
        provider_name = os.getenv(
            "AIRTIME_PROVIDER",
            "vtu_ng",
        )

    provider_name = provider_name.strip().lower()

    if provider_name in ("clubkonnect", "club_konnect", "ck"):
        return ClubKonnectProvider()

    if provider_name in ("vtu_ng", "vtu.ng", "vtung", "vtu"):
        return VTUNgProvider()

    raise ValueError(
        f"Unsupported airtime provider: {provider_name}"
    )


async def send_airtime(
    phone: str,
    amount: int,
    network: Optional[str] = None,
    request_id: Optional[str] = None,
) -> AirtimeResult:
    """
    Send airtime using the currently configured provider.
    """
    provider = _get_provider()

    return await provider.send_airtime(
        phone=phone,
        amount=amount,
        network=network,
        request_id=request_id,
    )


async def check_airtime_status(
    reference: str,
    provider_name: Optional[str] = None,
) -> AirtimeResult:
    """
    Check a transaction using its original provider.

    Pass provider_name from the payout's database record.
    If omitted, use the currently configured provider.
    """
    provider = _get_provider(provider_name)

    return await provider.check_status(
        reference=reference,
    )
