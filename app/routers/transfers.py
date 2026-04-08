from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.schemas import (
    TransferRequest,
    TransferResponse,
    InterBankTransferRequest,
    InterBankTransferResponse,
    TransferStatusResponse,
    ErrorResponse,
)
from app.services import transfer_service

router = APIRouter(prefix="/transfers", tags=["Transfers"])


def _transfer_to_response(transfer) -> TransferResponse:
    return TransferResponse(
        transferId=transfer.transfer_id,
        status=transfer.status,
        sourceAccount=transfer.source_account,
        destinationAccount=transfer.destination_account,
        amount=transfer.amount,
        convertedAmount=transfer.converted_amount,
        exchangeRate=transfer.exchange_rate,
        rateCapturedAt=transfer.rate_captured_at,
        timestamp=transfer.timestamp,
        errorMessage=transfer.error_message,
    )


def _transfer_to_status_response(transfer) -> TransferStatusResponse:
    return TransferStatusResponse(
        transferId=transfer.transfer_id,
        status=transfer.status,
        sourceAccount=transfer.source_account,
        destinationAccount=transfer.destination_account,
        amount=transfer.amount,
        convertedAmount=transfer.converted_amount,
        exchangeRate=transfer.exchange_rate,
        rateCapturedAt=transfer.rate_captured_at,
        timestamp=transfer.timestamp,
        pendingSince=transfer.pending_since,
        nextRetryAt=transfer.next_retry_at,
        retryCount=transfer.retry_count if transfer.status == "pending" else None,
        errorMessage=transfer.error_message,
    )


@router.post(
    "",
    response_model=TransferResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def initiate_transfer(
    body: TransferRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    transfer = await transfer_service.initiate_transfer(
        db=db,
        transfer_id=body.transferId,
        source_account_number=body.sourceAccount,
        destination_account_number=body.destinationAccount,
        amount=body.amount,
        current_user_id=current_user.id,
    )
    return _transfer_to_response(transfer)


@router.post(
    "/receive",
    response_model=InterBankTransferResponse,
    status_code=status.HTTP_200_OK,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def receive_transfer(
    body: InterBankTransferRequest,
    db: AsyncSession = Depends(get_db),
):
    transfer = await transfer_service.receive_interbank_transfer(db, body.jwt)
    credited_amount = transfer.converted_amount or transfer.amount
    return InterBankTransferResponse(
        transferId=transfer.transfer_id,
        status=transfer.status,
        destinationAccount=transfer.destination_account,
        amount=credited_amount,
        timestamp=transfer.timestamp,
    )


@router.get(
    "/{transferId}",
    response_model=TransferStatusResponse,
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_transfer_status(
    transferId: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    transfer = await transfer_service.get_transfer_status(db, transferId, current_user.id)
    return _transfer_to_status_response(transfer)
