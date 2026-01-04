# =================================================================
# services/airtime_providers/types.py
# ================================================================
from dataclasses import dataclass
from typing import Optional

@dataclass
class AirtimeResult:
    success: bool
    provider: str
    reference: Optional[str] = None
    message: Optional[str] = None
    raw: Optional[dict] = None
