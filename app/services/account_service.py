import random
import string
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Account
from app.services.central_bank_service import get_bank_prefix


def _generate_suffix(length: int = 5) -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=length))


async def create_account(db: AsyncSession, owner_id: str, currency: str) -> Account:
    prefix = await get_bank_prefix(db)
    if not prefix:
        raise RuntimeError("Bank not registered — cannot generate account number")

    # Ensure unique account number
    for _ in range(10):
        account_number = prefix + _generate_suffix(5)
        existing = await db.execute(
            select(Account).where(Account.account_number == account_number)
        )
        if not existing.scalar_one_or_none():
            break
    else:
        raise RuntimeError("Could not generate unique account number")

    now = datetime.now(timezone.utc).isoformat()
    account = Account(
        account_number=account_number,
        owner_id=owner_id,
        currency=currency,
        balance="0.00",
        created_at=now,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


async def get_account_by_number(db: AsyncSession, account_number: str) -> Account | None:
    result = await db.execute(
        select(Account).where(Account.account_number == account_number)
    )
    return result.scalar_one_or_none()
