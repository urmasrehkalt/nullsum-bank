import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.auth import ensure_keys, get_public_key_pem
from app.config import settings
from app.database import init_db, AsyncSessionLocal
from app.routers import users, accounts, transfers
from app.services.central_bank_service import register_with_central_bank, send_heartbeat, _set_state
from app.services.transfer_service import retry_pending_transfers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 25 * 60   # 25 minutes
RETRY_INTERVAL_SECONDS = 60            # 1 minute


async def _heartbeat_loop():
    await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await send_heartbeat(db)
        except Exception as exc:
            logger.error("Heartbeat error: %s", exc)
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


async def _retry_loop():
    await asyncio.sleep(10)
    while True:
        try:
            async with AsyncSessionLocal() as db:
                await retry_pending_transfers(db)
        except Exception as exc:
            logger.error("Retry loop error: %s", exc)
        await asyncio.sleep(RETRY_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Init database tables
    await init_db()
    logger.info("Database tables ready")

    # 2. Generate EC key pair if needed
    private_pem, public_pem = ensure_keys()
    logger.info("EC key pair ready")

    # 3. Register with central bank (or use BANK_ID from .env for local testing)
    async with AsyncSessionLocal() as db:
        if settings.BANK_ID:
            await _set_state(db, "bank_id", settings.BANK_ID)
            await _set_state(db, "bank_prefix", settings.BANK_ID[:3])
            logger.info("Using configured BANK_ID: %s (prefix: %s)", settings.BANK_ID, settings.BANK_ID[:3])
        else:
            try:
                bank_id = await register_with_central_bank(db, public_pem)
                logger.info("Central bank registration complete: %s", bank_id)
            except Exception as exc:
                logger.error("Central bank registration failed: %s", exc)
                logger.warning("Set BANK_ID=NLS001 in .env to run without central bank")

    # 4. Start background tasks
    heartbeat_task = asyncio.create_task(_heartbeat_loop())
    retry_task = asyncio.create_task(_retry_loop())
    logger.info("Background tasks started")

    yield

    heartbeat_task.cancel()
    retry_task.cancel()


_DESCRIPTION = """
Branch bank API for the TAK25 school project. Integrates with the Central Bank to support
user accounts, intra-bank transfers, and inter-bank payments signed with ES256 JWTs.

## Authentication

All protected endpoints require a **Bearer token** issued at registration.

1. Call `POST /api/v1/users` to register and receive a token.
2. Click **Authorize** (🔒) in the top-right corner of this page.
3. Paste the token value and click **Authorize**.

All subsequent requests will include the token automatically.
"""

_TAGS = [
    {
        "name": "Users",
        "description": "Register users and retrieve user profiles.",
    },
    {
        "name": "Accounts",
        "description": "Create accounts and look up account details.",
    },
    {
        "name": "Transfers",
        "description": (
            "Initiate intra-bank and inter-bank transfers, receive incoming transfers "
            "from other banks, and query transfer status."
        ),
    },
    {
        "name": "Health",
        "description": "Liveness probe — no authentication required.",
    },
]

app = FastAPI(
    title="NullSum Bank API",
    description=_DESCRIPTION,
    version="1.0.0",
    lifespan=lifespan,
    openapi_tags=_TAGS,
)


# ── Global error handler for dict detail ──────────────────────────────────

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"code": "INTERNAL_ERROR", "message": "Internal server error"},
    )


# ── Routes ─────────────────────────────────────────────────────────────────

app.include_router(users.router, prefix="/api/v1")
app.include_router(accounts.router, prefix="/api/v1")
app.include_router(transfers.router, prefix="/api/v1")


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok"}
