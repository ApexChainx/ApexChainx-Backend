from fastapi import APIRouter
from app.api.v1.endpoints import audit


from app.api.v1.endpoints import (
    api_keys,
    auth,
    jobs,
    metrics,
    oauth,
    outages,
    sla,
    sla_dispute,
    payments,
    webhooks,
    wallets,
)

api_router = APIRouter()

api_router.include_router(api_keys.router)
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(audit.router)
api_router.include_router(jobs.router)
api_router.include_router(metrics.router)
api_router.include_router(oauth.router)
api_router.include_router(outages.router, prefix="/outages", tags=["outages"])
api_router.include_router(sla.router, prefix="/sla", tags=["sla"])
api_router.include_router(sla_dispute.router, prefix="/sla", tags=["sla-disputes"])
api_router.include_router(payments.router, prefix="/payments", tags=["payments"])
api_router.include_router(webhooks.router)
api_router.include_router(wallets.router, prefix="/wallets", tags=["wallets"])
