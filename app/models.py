from sqlalchemy import String, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    api_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class Account(Base):
    __tablename__ = "accounts"

    account_number: Mapped[str] = mapped_column(String(8), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    balance: Mapped[str] = mapped_column(String(30), nullable=False, default="0.00")
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class Transfer(Base):
    __tablename__ = "transfers"

    transfer_id: Mapped[str] = mapped_column(String, primary_key=True)
    source_account: Mapped[str] = mapped_column(String(8), nullable=False)
    destination_account: Mapped[str] = mapped_column(String(8), nullable=False)
    amount: Mapped[str] = mapped_column(String(30), nullable=False)
    converted_amount: Mapped[str | None] = mapped_column(String(30), nullable=True)
    exchange_rate: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rate_captured_at: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    timestamp: Mapped[str] = mapped_column(String, nullable=False)
    pending_since: Mapped[str | None] = mapped_column(String, nullable=True)
    next_retry_at: Mapped[str | None] = mapped_column(String, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class BankState(Base):
    __tablename__ = "bank_state"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
