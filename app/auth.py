import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import settings
from app.database import get_db
from app.models import User

bearer_scheme = HTTPBearer()


# ── EC Key Pair ────────────────────────────────────────────────────────────

def ensure_keys() -> tuple[str, str]:
    """Generate EC P-256 key pair if not present. Returns (private_pem, public_pem)."""
    keys_dir = Path(settings.KEYS_DIR)
    keys_dir.mkdir(parents=True, exist_ok=True)
    priv_path = keys_dir / "private_key.pem"
    pub_path = keys_dir / "public_key.pem"

    if not priv_path.exists():
        private_key = ec.generate_private_key(ec.SECP256R1())
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        priv_path.write_bytes(private_pem)
        pub_path.write_bytes(public_pem)

    return priv_path.read_text(), pub_path.read_text()


def get_public_key_pem() -> str:
    pub_path = Path(settings.KEYS_DIR) / "public_key.pem"
    return pub_path.read_text()


def get_private_key_pem() -> str:
    priv_path = Path(settings.KEYS_DIR) / "private_key.pem"
    return priv_path.read_text()


# ── User Bearer Tokens (HS256) ─────────────────────────────────────────────

def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=settings.ACCESS_TOKEN_EXPIRE_DAYS)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")


def decode_access_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        return payload.get("sub")
    except JWTError:
        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = credentials.credentials
    user_id = decode_access_token(token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid or expired token"},
        )
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "User not found"},
        )
    return user


# ── Inter-Bank ES256 JWT ────────────────────────────────────────────────────

def sign_interbank_jwt(payload: dict) -> str:
    private_key_pem = get_private_key_pem()
    return jwt.encode(payload, private_key_pem, algorithm="ES256")


def verify_interbank_jwt(token: str, public_key_pem: str) -> dict:
    return jwt.decode(token, public_key_pem, algorithms=["ES256"])
