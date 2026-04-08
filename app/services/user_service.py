import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.auth import create_access_token
from app.models import User


async def create_user(db: AsyncSession, full_name: str, email: str | None) -> User:
    user_id = f"user-{uuid.uuid4()}"
    token = create_access_token(user_id)
    now = datetime.now(timezone.utc).isoformat()

    user = User(
        id=user_id,
        full_name=full_name,
        email=email,
        api_key=token,
        created_at=now,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def get_user_by_id(db: AsyncSession, user_id: str) -> User | None:
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()
