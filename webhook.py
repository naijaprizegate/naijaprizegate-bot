# ====================================================================
# webhook.py
# ====================================================================
from fastapi import APIRouter

router = APIRouter()

# Keep this file only if you need non-payment webhook endpoints later.
# Payment webhook handling now lives in routes/payments_router.py
# via:
#   - /flw/webhook
#   - /flw/redirect
#   - /flw/redirect/status
