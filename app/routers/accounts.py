from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.schemas import (
    AccountCreationRequest,
    AccountCreationResponse,
    AccountLookupResponse,
    ErrorResponse,
)
from app.services import account_service, user_service

router = APIRouter(tags=["Accounts"])


@router.post(
    "/users/{userId}/accounts",
    response_model=AccountCreationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def create_account(
    userId: str,
    body: AccountCreationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.id != userId:
        raise HTTPException(
            status_code=403,
            detail={"code": "FORBIDDEN", "message": "Cannot create account for another user"},
        )

    user = await user_service.get_user_by_id(db, userId)
    if not user:
        raise HTTPException(
            status_code=404,
            detail={"code": "USER_NOT_FOUND", "message": "User not found"},
        )

    account = await account_service.create_account(db, userId, body.currency)
    return AccountCreationResponse(
        accountNumber=account.account_number,
        ownerId=account.owner_id,
        currency=account.currency,
        balance=account.balance,
        createdAt=account.created_at,
    )


@router.get(
    "/accounts/{accountNumber}",
    response_model=AccountLookupResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def lookup_account(
    accountNumber: str,
    db: AsyncSession = Depends(get_db),
):
    account = await account_service.get_account_by_number(db, accountNumber)
    if not account:
        raise HTTPException(
            status_code=404,
            detail={"code": "ACCOUNT_NOT_FOUND", "message": "Account not found"},
        )

    # Fetch owner name for privacy-protected response
    from app.services import user_service as us
    owner = await us.get_user_by_id(db, account.owner_id)
    owner_name = owner.full_name if owner else "Unknown"

    return AccountLookupResponse(
        accountNumber=account.account_number,
        ownerName=owner_name,
        currency=account.currency,
    )
