import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import httpx
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.auth import sign_interbank_jwt, verify_interbank_jwt
from app.models import Account, Transfer
from app.services.central_bank_service import (
    get_bank_id,
    get_bank_prefix,
    get_bank_by_prefix,
    get_exchange_rates,
)

logger = logging.getLogger(__name__)

TRANSFER_TIMEOUT_HOURS = 4
MAX_RETRY_MINUTES = 60


# ── Helpers ────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid decimal: {value}") from exc


def _format_decimal(value: Decimal, places: int = 2) -> str:
    quantize_str = "0." + "0" * places
    return str(value.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP))


def _retry_delay_minutes(retry_count: int) -> int:
    """Exponential backoff capped at MAX_RETRY_MINUTES."""
    return min(2 ** retry_count, MAX_RETRY_MINUTES)


# ── Internal transfer ──────────────────────────────────────────────────────

async def _do_internal_transfer(
    db: AsyncSession,
    transfer: Transfer,
    source_account: Account,
    dest_account: Account,
) -> Transfer:
    src_balance = _parse_decimal(source_account.balance)
    amount = _parse_decimal(transfer.amount)

    if src_balance < amount:
        transfer.status = "failed"
        transfer.error_message = "Insufficient funds"
        await db.commit()
        return transfer

    source_account.balance = _format_decimal(src_balance - amount)
    dest_account.balance = _format_decimal(_parse_decimal(dest_account.balance) + amount)
    transfer.status = "completed"
    await db.commit()
    await db.refresh(transfer)
    return transfer


# ── External (inter-bank) transfer ─────────────────────────────────────────

async def _do_external_transfer(
    db: AsyncSession,
    transfer: Transfer,
    source_account: Account,
    dest_prefix: str,
) -> Transfer:
    # Fetch destination bank
    dest_bank = await get_bank_by_prefix(db, dest_prefix)
    if not dest_bank:
        transfer.status = "failed"
        transfer.error_message = f"Unknown destination bank prefix: {dest_prefix}"
        await db.commit()
        return transfer

    # Currency conversion
    src_currency = source_account.currency
    amount = _parse_decimal(transfer.amount)
    converted_amount = amount
    exchange_rate = None
    rate_captured_at = None

    dest_account_result = await db.execute(
        select(Account).where(Account.account_number == transfer.destination_account)
    )
    # For external account, we don't know the currency in advance. The receiving bank handles it.
    # We send amount in source currency; receiving bank converts if needed.
    # Per spec: amount field in JWT is source amount. Receiving bank credits in their currency.

    our_bank_id = await get_bank_id(db)
    dest_bank_id = dest_bank["bankId"]

    jwt_payload = {
        "transferId": transfer.transfer_id,
        "sourceAccount": transfer.source_account,
        "destinationAccount": transfer.destination_account,
        "amount": transfer.amount,
        "currency": src_currency,
        "sourceBankId": our_bank_id,
        "destinationBankId": dest_bank_id,
        "timestamp": _now_iso(),
        "nonce": str(uuid.uuid4()),
    }
    signed_jwt = sign_interbank_jwt(jwt_payload)

    dest_address = dest_bank["address"].rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{dest_address}/api/v1/transfers/receive",
                json={"jwt": signed_jwt},
            )

        if resp.status_code == 200:
            resp_data = resp.json()
            # Debit source account
            src_balance = _parse_decimal(source_account.balance)
            source_account.balance = _format_decimal(src_balance - amount)

            transfer.status = "completed"
            if resp_data.get("amount") != transfer.amount:
                transfer.converted_amount = resp_data.get("amount")
            await db.commit()
            await db.refresh(transfer)
            return transfer

        elif resp.status_code in (503, 504, 502, 500):
            # Destination bank unavailable — set pending with retry
            transfer.status = "pending"
            transfer.pending_since = _now_iso()
            transfer.next_retry_at = (
                datetime.now(timezone.utc) + timedelta(minutes=_retry_delay_minutes(0))
            ).isoformat()
            transfer.retry_count = 0
            await db.commit()
            await db.refresh(transfer)
            return transfer
        else:
            error_text = resp.text
            transfer.status = "failed"
            transfer.error_message = f"Destination bank rejected transfer: {resp.status_code} {error_text}"
            await db.commit()
            return transfer

    except httpx.RequestError as exc:
        logger.warning("Network error sending to %s: %s", dest_bank_id, exc)
        transfer.status = "pending"
        transfer.pending_since = _now_iso()
        transfer.next_retry_at = (
            datetime.now(timezone.utc) + timedelta(minutes=_retry_delay_minutes(0))
        ).isoformat()
        transfer.retry_count = 0
        await db.commit()
        await db.refresh(transfer)
        return transfer


# ── Public: initiate transfer ──────────────────────────────────────────────

async def initiate_transfer(
    db: AsyncSession,
    transfer_id: str,
    source_account_number: str,
    destination_account_number: str,
    amount: str,
    current_user_id: str,
) -> Transfer:
    # Idempotency: return existing if already processed
    existing = await db.execute(
        select(Transfer).where(Transfer.transfer_id == transfer_id)
    )
    existing_transfer = existing.scalar_one_or_none()
    if existing_transfer:
        return existing_transfer

    # Validate source account ownership
    src_result = await db.execute(
        select(Account).where(Account.account_number == source_account_number)
    )
    source_account = src_result.scalar_one_or_none()
    if not source_account:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=404,
            detail={"code": "ACCOUNT_NOT_FOUND", "message": "Source account not found"},
        )
    if source_account.owner_id != current_user_id:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": "Source account does not belong to you"},
        )

    # Validate amount
    try:
        amount_decimal = _parse_decimal(amount)
        if amount_decimal <= 0:
            raise ValueError("Amount must be positive")
    except (ValueError, InvalidOperation):
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_AMOUNT", "message": "Invalid amount"},
        )

    # Check sufficient funds (pre-check; actual debit happens in transfer functions)
    if _parse_decimal(source_account.balance) < amount_decimal:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=422,
            detail={"code": "INSUFFICIENT_FUNDS", "message": "Insufficient funds"},
        )

    # Create transfer record (status will be updated by transfer functions)
    transfer = Transfer(
        transfer_id=transfer_id,
        source_account=source_account_number,
        destination_account=destination_account_number,
        amount=amount,
        status="pending",
        timestamp=_now_iso(),
    )
    db.add(transfer)
    await db.commit()
    await db.refresh(transfer)

    # Route: internal vs external
    our_prefix = await get_bank_prefix(db)
    dest_prefix = destination_account_number[:3]

    if dest_prefix == our_prefix:
        # Internal transfer
        dest_result = await db.execute(
            select(Account).where(Account.account_number == destination_account_number)
        )
        dest_account = dest_result.scalar_one_or_none()
        if not dest_account:
            from fastapi import HTTPException
            transfer.status = "failed"
            transfer.error_message = "Destination account not found"
            await db.commit()
            return transfer
        return await _do_internal_transfer(db, transfer, source_account, dest_account)
    else:
        # Debit source account immediately for external transfers that go pending
        # We'll actually debit on confirmed completion to avoid double-debit on retry.
        # For pending case, we reserve balance: debit now, credit back on failed_timeout.
        src_balance = _parse_decimal(source_account.balance)
        source_account.balance = _format_decimal(src_balance - amount_decimal)
        await db.commit()

        return await _do_external_transfer(db, transfer, source_account, dest_prefix)


# ── Public: receive inter-bank transfer ────────────────────────────────────

async def receive_interbank_transfer(db: AsyncSession, jwt_token: str) -> Transfer:
    # Decode header to get sourceBankId without verifying yet
    from jose import jwt as jose_jwt
    try:
        unverified = jose_jwt.get_unverified_claims(jwt_token)
    except JWTError as exc:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=401,
            detail={"code": "INVALID_JWT", "message": "Cannot decode JWT"},
        ) from exc

    source_bank_id = unverified.get("sourceBankId")
    if not source_bank_id:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=401,
            detail={"code": "INVALID_JWT", "message": "Missing sourceBankId in JWT"},
        )

    # Look up source bank's public key
    source_prefix = source_bank_id[:3]
    source_bank = await get_bank_by_prefix(db, source_prefix)
    if not source_bank:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=403,
            detail={"code": "UNKNOWN_BANK", "message": f"Unknown source bank: {source_bank_id}"},
        )

    public_key_pem = source_bank.get("publicKey", "")
    try:
        payload = verify_interbank_jwt(jwt_token, public_key_pem)
    except JWTError as exc:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=401,
            detail={"code": "INVALID_SIGNATURE", "message": "JWT signature verification failed"},
        ) from exc

    transfer_id = payload.get("transferId")
    destination_account_number = payload.get("destinationAccount")
    amount_str = payload.get("amount")
    source_account_number = payload.get("sourceAccount")
    source_currency = payload.get("currency", "EUR")

    if not all([transfer_id, destination_account_number, amount_str]):
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_PAYLOAD", "message": "Missing required JWT fields"},
        )

    # Idempotency
    existing = await db.execute(
        select(Transfer).where(Transfer.transfer_id == transfer_id)
    )
    existing_transfer = existing.scalar_one_or_none()
    if existing_transfer:
        return existing_transfer

    # Find destination account
    dest_result = await db.execute(
        select(Account).where(Account.account_number == destination_account_number)
    )
    dest_account = dest_result.scalar_one_or_none()
    if not dest_account:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=404,
            detail={"code": "ACCOUNT_NOT_FOUND", "message": "Destination account not found"},
        )

    # Currency conversion if needed
    amount = _parse_decimal(amount_str)
    credited_amount = amount
    exchange_rate_str = None
    rate_captured_at = None
    converted_amount_str = None

    if source_currency != dest_account.currency:
        try:
            rates = await get_exchange_rates(db)
            # Convert: source_currency → EUR → dest_currency
            src_rate = _parse_decimal(rates.get(source_currency, "1.000000"))
            dst_rate = _parse_decimal(rates.get(dest_account.currency, "1.000000"))
            amount_in_eur = amount / src_rate
            credited_amount = (amount_in_eur * dst_rate).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            exchange_rate = (dst_rate / src_rate).quantize(
                Decimal("0.000001"), rounding=ROUND_HALF_UP
            )
            exchange_rate_str = str(exchange_rate)
            rate_captured_at = _now_iso()
            converted_amount_str = _format_decimal(credited_amount)
        except Exception as exc:
            logger.error("Currency conversion failed: %s", exc)

    # Credit destination account
    dest_account.balance = _format_decimal(
        _parse_decimal(dest_account.balance) + credited_amount
    )

    now = _now_iso()
    transfer = Transfer(
        transfer_id=transfer_id,
        source_account=source_account_number or "",
        destination_account=destination_account_number,
        amount=amount_str,
        converted_amount=converted_amount_str,
        exchange_rate=exchange_rate_str,
        rate_captured_at=rate_captured_at,
        status="completed",
        timestamp=now,
    )
    db.add(transfer)
    await db.commit()
    await db.refresh(transfer)
    return transfer


# ── Public: get transfer status ────────────────────────────────────────────

async def get_transfer_status(
    db: AsyncSession, transfer_id: str, current_user_id: str
) -> Transfer:
    result = await db.execute(
        select(Transfer).where(Transfer.transfer_id == transfer_id)
    )
    transfer = result.scalar_one_or_none()
    if not transfer:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": "Transfer not found"},
        )

    # Verify ownership via source account
    src_result = await db.execute(
        select(Account).where(Account.account_number == transfer.source_account)
    )
    src_account = src_result.scalar_one_or_none()
    if src_account and src_account.owner_id != current_user_id:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": "Transfer does not belong to you"},
        )

    return transfer


# ── Background: retry pending transfers ────────────────────────────────────

async def retry_pending_transfers(db: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Transfer).where(Transfer.status == "pending")
    )
    pending = result.scalars().all()

    for transfer in pending:
        if not transfer.pending_since or not transfer.next_retry_at:
            continue

        # Check timeout (4 hours)
        pending_since = datetime.fromisoformat(transfer.pending_since)
        if now - pending_since > timedelta(hours=TRANSFER_TIMEOUT_HOURS):
            logger.info("Transfer %s timed out after 4h — refunding", transfer.transfer_id)
            transfer.status = "failed_timeout"
            transfer.error_message = "Transfer timed out after 4 hours"
            # Refund source account
            src_result = await db.execute(
                select(Account).where(Account.account_number == transfer.source_account)
            )
            src_account = src_result.scalar_one_or_none()
            if src_account:
                src_account.balance = _format_decimal(
                    _parse_decimal(src_account.balance) + _parse_decimal(transfer.amount)
                )
            await db.commit()
            continue

        # Check if it's time to retry
        next_retry = datetime.fromisoformat(transfer.next_retry_at)
        if now < next_retry:
            continue

        logger.info(
            "Retrying transfer %s (attempt %d)", transfer.transfer_id, transfer.retry_count + 1
        )

        # Load source account (already debited)
        src_result = await db.execute(
            select(Account).where(Account.account_number == transfer.source_account)
        )
        src_account = src_result.scalar_one_or_none()
        if not src_account:
            transfer.status = "failed"
            transfer.error_message = "Source account no longer exists"
            await db.commit()
            continue

        dest_prefix = transfer.destination_account[:3]
        dest_bank = await get_bank_by_prefix(db, dest_prefix)
        if not dest_bank:
            transfer.retry_count += 1
            delay = _retry_delay_minutes(transfer.retry_count)
            transfer.next_retry_at = (now + timedelta(minutes=delay)).isoformat()
            await db.commit()
            continue

        our_bank_id = await get_bank_id(db)
        dest_bank_id = dest_bank["bankId"]
        jwt_payload = {
            "transferId": transfer.transfer_id,
            "sourceAccount": transfer.source_account,
            "destinationAccount": transfer.destination_account,
            "amount": transfer.amount,
            "sourceBankId": our_bank_id,
            "destinationBankId": dest_bank_id,
            "timestamp": _now_iso(),
            "nonce": str(uuid.uuid4()),
        }
        signed_jwt = sign_interbank_jwt(jwt_payload)
        dest_address = dest_bank["address"].rstrip("/")

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{dest_address}/api/v1/transfers/receive",
                    json={"jwt": signed_jwt},
                )

            if resp.status_code == 200:
                transfer.status = "completed"
                resp_data = resp.json()
                if resp_data.get("amount") != transfer.amount:
                    transfer.converted_amount = resp_data.get("amount")
                await db.commit()
            elif resp.status_code in (503, 504, 502, 500):
                transfer.retry_count += 1
                delay = _retry_delay_minutes(transfer.retry_count)
                transfer.next_retry_at = (now + timedelta(minutes=delay)).isoformat()
                await db.commit()
            else:
                transfer.status = "failed"
                transfer.error_message = f"Rejected: {resp.status_code}"
                # Refund
                src_account.balance = _format_decimal(
                    _parse_decimal(src_account.balance) + _parse_decimal(transfer.amount)
                )
                await db.commit()
        except httpx.RequestError:
            transfer.retry_count += 1
            delay = _retry_delay_minutes(transfer.retry_count)
            transfer.next_retry_at = (now + timedelta(minutes=delay)).isoformat()
            await db.commit()
