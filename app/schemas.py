from typing import Optional
from pydantic import BaseModel, EmailStr, field_validator
import re


# ── Users ──────────────────────────────────────────────────────────────────

class UserRegistrationRequest(BaseModel):
    fullName: str
    email: Optional[str] = None

    @field_validator("fullName")
    @classmethod
    def validate_full_name(cls, v: str) -> str:
        if len(v) < 2 or len(v) > 200:
            raise ValueError("fullName must be between 2 and 200 characters")
        return v


class UserRegistrationResponse(BaseModel):
    userId: str
    fullName: str
    email: Optional[str] = None
    createdAt: str
    token: str  # Extension: Bearer token returned at registration


# ── Accounts ───────────────────────────────────────────────────────────────

class AccountCreationRequest(BaseModel):
    currency: str

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        if not re.match(r"^[A-Z]{3}$", v):
            raise ValueError("currency must be a 3-letter ISO 4217 code")
        return v


class AccountCreationResponse(BaseModel):
    accountNumber: str
    ownerId: str
    currency: str
    balance: str
    createdAt: str


class AccountLookupResponse(BaseModel):
    accountNumber: str
    ownerName: str
    currency: str


# ── Transfers ──────────────────────────────────────────────────────────────

class TransferRequest(BaseModel):
    transferId: str
    sourceAccount: str
    destinationAccount: str
    amount: str

    @field_validator("sourceAccount", "destinationAccount")
    @classmethod
    def validate_account_number(cls, v: str) -> str:
        if not re.match(r"^[A-Z0-9]{8}$", v):
            raise ValueError("account number must be 8 uppercase alphanumeric characters")
        return v

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: str) -> str:
        if not re.match(r"^\d+\.\d{2}$", v):
            raise ValueError("amount must be a decimal with exactly 2 decimal places")
        return v


class TransferResponse(BaseModel):
    transferId: str
    status: str
    sourceAccount: str
    destinationAccount: str
    amount: str
    convertedAmount: Optional[str] = None
    exchangeRate: Optional[str] = None
    rateCapturedAt: Optional[str] = None
    timestamp: str
    errorMessage: Optional[str] = None


class InterBankTransferRequest(BaseModel):
    jwt: str


class InterBankTransferResponse(BaseModel):
    transferId: str
    status: str
    destinationAccount: str
    amount: str
    timestamp: str


class TransferStatusResponse(BaseModel):
    transferId: str
    status: str
    sourceAccount: str
    destinationAccount: str
    amount: str
    convertedAmount: Optional[str] = None
    exchangeRate: Optional[str] = None
    rateCapturedAt: Optional[str] = None
    timestamp: str
    pendingSince: Optional[str] = None
    nextRetryAt: Optional[str] = None
    retryCount: Optional[int] = None
    errorMessage: Optional[str] = None


# ── Error ──────────────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    code: str
    message: str
