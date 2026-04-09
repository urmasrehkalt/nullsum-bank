from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select


from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.schemas import UserRegistrationRequest, UserRegistrationResponse, UserProfileResponse, ErrorResponse
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


@router.get(
    "/{userId}",
    response_model=UserProfileResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_user(
    userId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.id != userId:
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": "Cannot view another user's profile"},
        )
    user = await user_service.get_user_by_id(db, userId)
    if not user:
        raise HTTPException(
            status_code=404,
            detail={"code": "USER_NOT_FOUND", "message": "User not found"},
        )
    return UserProfileResponse(
        userId=user.id,
        fullName=user.full_name,
        email=user.email,
        createdAt=user.created_at,
    )
