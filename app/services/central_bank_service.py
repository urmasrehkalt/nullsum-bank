import json
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.models import BankState

logger = logging.getLogger(__name__)

CENTRAL_BANK_URL = settings.CENTRAL_BANK_URL


# ── State helpers ──────────────────────────────────────────────────────────

async def _get_state(db: AsyncSession, key: str) -> str | None:
    result = await db.execute(select(BankState).where(BankState.key == key))
    row = result.scalar_one_or_none()
    return row.value if row else None


async def _set_state(db: AsyncSession, key: str, value: str) -> None:
    result = await db.execute(select(BankState).where(BankState.key == key))
    row = result.scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(BankState(key=key, value=value))
    await db.commit()


# ── Registration ───────────────────────────────────────────────────────────

async def _find_own_bank_in_directory() -> str | None:
    """Look up our own bank in the central bank directory by address."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{CENTRAL_BANK_URL}/api/v1/banks")
        resp.raise_for_status()
        banks = resp.json().get("banks", [])
        for bank in banks:
            if bank.get("address") == settings.BANK_ADDRESS:
                return bank.get("bankId")
    except Exception as exc:
        logger.warning("Could not fetch directory to recover bank_id: %s", exc)
    return None


async def register_with_central_bank(db: AsyncSession, public_key_pem: str) -> str:
    """Register with central bank. Returns assigned bankId. Idempotent."""
    existing_id = await _get_state(db, "bank_id")
    if existing_id:
        logger.info("Already registered with central bank: %s", existing_id)
        return existing_id

    payload = {
        "name": settings.BANK_NAME,
        "address": settings.BANK_ADDRESS,
        "publicKey": public_key_pem,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{CENTRAL_BANK_URL}/api/v1/banks",
            content=json.dumps(payload),
            headers={"Content-Type": "application/json"},
        )

    if resp.status_code in (400, 409):
        logger.warning("Registration returned %s: %s — checking directory for existing registration", resp.status_code, resp.text)
        bank_id = await _find_own_bank_in_directory()
        if bank_id:
            bank_prefix = bank_id[:3]
            await _set_state(db, "bank_id", bank_id)
            await _set_state(db, "bank_prefix", bank_prefix)
            await _set_state(db, "registered_at", datetime.now(timezone.utc).isoformat())
            logger.info("Recovered bank_id from directory: %s (prefix: %s)", bank_id, bank_prefix)
            return bank_id
        raise RuntimeError(f"Registration failed ({resp.status_code}) and bank not found in directory: {resp.text}")

    if not resp.is_success:
        logger.error("Registration failed: %s %s", resp.status_code, resp.text)
    resp.raise_for_status()

    # Central bank may return PHP warnings before the JSON body — find the first '{'
    text = resp.text
    json_start = text.find("{")
    if json_start < 0:
        raise RuntimeError(f"No JSON in registration response: {text[:200]}")
    data = json.loads(text[json_start:])
    bank_id: str = data["bankId"]
    bank_prefix = bank_id[:3]

    await _set_state(db, "bank_id", bank_id)
    await _set_state(db, "bank_prefix", bank_prefix)
    await _set_state(db, "registered_at", datetime.now(timezone.utc).isoformat())

    logger.info("Registered with central bank: %s (prefix: %s)", bank_id, bank_prefix)
    return bank_id


# ── Heartbeat ──────────────────────────────────────────────────────────────

async def send_heartbeat(db: AsyncSession) -> None:
    bank_id = await _get_state(db, "bank_id")
    if not bank_id:
        logger.warning("Cannot send heartbeat: not registered yet")
        return

    payload = {"timestamp": datetime.now(timezone.utc).isoformat()}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{CENTRAL_BANK_URL}/api/v1/banks/{bank_id}/heartbeat",
            json=payload,
        )

    if resp.status_code == 200:
        await _set_state(db, "last_heartbeat_at", datetime.now(timezone.utc).isoformat())
        logger.info("Heartbeat sent successfully for %s", bank_id)
    else:
        logger.error("Heartbeat failed: %s %s", resp.status_code, resp.text)


# ── Bank directory ─────────────────────────────────────────────────────────

async def get_banks_directory(db: AsyncSession, force_refresh: bool = False) -> list[dict]:
    """Fetch bank directory from central bank. Returns cached list on failure."""
    if not force_refresh:
        cached = await _get_state(db, "banks_cache")
        if cached:
            return json.loads(cached)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{CENTRAL_BANK_URL}/api/v1/banks")
        resp.raise_for_status()
        data = resp.json()
        banks = data.get("banks", [])

        await _set_state(db, "banks_cache", json.dumps(banks))
        await _set_state(db, "banks_cache_at", datetime.now(timezone.utc).isoformat())
        return banks
    except Exception as exc:
        logger.warning("Could not refresh bank directory: %s — using cache", exc)
        cached = await _get_state(db, "banks_cache")
        return json.loads(cached) if cached else []


async def get_bank_by_prefix(db: AsyncSession, prefix: str) -> dict | None:
    """Find a bank entry by its 3-letter prefix (first 3 chars of bankId)."""
    banks = await get_banks_directory(db)
    for bank in banks:
        if bank.get("bankId", "")[:3] == prefix:
            return bank
    # Cache miss — try force refresh
    banks = await get_banks_directory(db, force_refresh=True)
    for bank in banks:
        if bank.get("bankId", "")[:3] == prefix:
            return bank
    return None


# ── Exchange rates ─────────────────────────────────────────────────────────

async def get_exchange_rates(db: AsyncSession) -> dict:
    """Returns dict of currency -> rate (EUR base). Caches in DB."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{CENTRAL_BANK_URL}/api/v1/exchange-rates")
        resp.raise_for_status()
        data = resp.json()
        rates = data.get("rates", {})
        rates["EUR"] = "1.000000"

        await _set_state(db, "exchange_rates_cache", json.dumps(rates))
        await _set_state(db, "rates_cache_at", datetime.now(timezone.utc).isoformat())
        return rates
    except Exception as exc:
        logger.warning("Could not fetch exchange rates: %s — using cache", exc)
        cached = await _get_state(db, "exchange_rates_cache")
        if cached:
            return json.loads(cached)
        raise RuntimeError("Exchange rates unavailable and no cache") from exc


# ── Getters ─────────────────────────────────────────────────────────────────

async def get_bank_id(db: AsyncSession) -> str | None:
    return await _get_state(db, "bank_id")


async def get_bank_prefix(db: AsyncSession) -> str | None:
    return await _get_state(db, "bank_prefix")
