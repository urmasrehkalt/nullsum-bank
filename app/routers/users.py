from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import User
from app.schemas import UserRegistrationRequest, UserRegistrationResponse, ErrorResponse
from app.services import user_service

router = APIRouter(prefix="/users", tags=["Users"])


@router.post(
    "",
    response_model=UserRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def register_user(
    body: UserRegistrationRequest,
    db: AsyncSession = Depends(get_db),
):
    # Check for duplicate email
    if body.email:
        result = await db.execute(select(User).where(User.email == body.email))
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=409,
                detail={"code": "EMAIL_CONFLICT", "message": "Email already registered"},
            )

    user = await user_service.create_user(db, body.fullName, body.email)
    return UserRegistrationResponse(
        userId=user.id,
        fullName=user.full_name,
        email=user.email,
        createdAt=user.created_at,
        token=user.api_key,
    )
