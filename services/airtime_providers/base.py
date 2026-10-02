# =======================================================================
# services/airtime_providers/base.py
# Common interface for airtime providers
# =======================================================================

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from services.airtime_providers.types import AirtimeResult


class AirtimeProvider(ABC):
    """
    Common interface that every airtime provider must implement.

    The rest of NaijaPrizeGate should depend on this interface,
    not on a specific provider such as ClubKonnect or VTU.ng.
    """

    name: str

    @abstractmethod
    async def send_airtime(
        self,
        phone: str,
        amount: int,
        request_id: Optional[str] = None,
    ) -> AirtimeResult:
        """
        Send airtime to a Nigerian phone number.

        Providers should normalize their own API response into
        AirtimeResult before returning it.
        """
        raise NotImplementedError

    @abstractmethod
    async def check_status(
        self,
        reference: str,
    ) -> AirtimeResult:
        """
        Check the current status of a previously submitted
        airtime transaction.

        Providers that do not support status checking can return
        an appropriate failed/unsupported AirtimeResult.
        """
        raise NotImplementedError