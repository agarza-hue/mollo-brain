"""
Auth JWT para MolloAI — registro, login, validación de token
"""
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from sqlalchemy import text

from db import get_db

SECRET_KEY = os.getenv("MOLLOAI_JWT_SECRET", "molloai-secret-key-change-in-prod-2026")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 días

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)


# --- Schemas ---

class UserRegister(BaseModel):
    email: str
    name: str
    password: str


class UserLogin(BaseModel):
    email: str
    password: str


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    plan: str
    created_at: datetime
    is_admin: bool = False


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# --- Helpers ---

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def create_access_token(user_id: str, email: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": user_id, "email": email, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


# --- DB helpers ---

def get_user_by_email(db: Session, email: str) -> Optional[dict]:
    row = db.execute(
        text("SELECT id, email, name, hashed_password, plan, is_active, created_at, is_admin FROM users WHERE email = :email"),
        {"email": email}
    ).fetchone()
    return dict(row._mapping) if row else None


def create_user(db: Session, email: str, name: str, password: str) -> dict:
    user_id = str(uuid.uuid4())
    hashed = hash_password(password)
    db.execute(
        text("""
            INSERT INTO users (id, email, name, hashed_password, plan)
            VALUES (:id, :email, :name, :hashed_password, 'free')
        """),
        {"id": user_id, "email": email, "name": name, "hashed_password": hashed}
    )
    db.commit()
    return get_user_by_email(db, email)


def log_usage(db: Session, user_id: str, model: str, input_tokens: int, output_tokens: int, complexity: str):
    db.execute(
        text("""
            INSERT INTO usage_logs (id, user_id, model, input_tokens, output_tokens, complexity)
            VALUES (:id, :user_id, :model, :input_tokens, :output_tokens, :complexity)
        """),
        {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "complexity": complexity,
        }
    )
    db.commit()


# --- FastAPI dependency ---

def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> dict:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token requerido")
    try:
        payload = decode_token(credentials.credentials)
        user_id = payload.get("sub")
        email = payload.get("email")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido o expirado")

    row = db.execute(
        text("SELECT id, email, name, plan, is_active, created_at, is_admin FROM users WHERE id = :id"),
        {"id": user_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Usuario no encontrado")
    user = dict(row._mapping)
    if not user["is_active"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cuenta inactiva")
    return user


def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Optional[dict]:
    """Igual que get_current_user pero no lanza error si no hay token (endpoints públicos)."""
    if not credentials:
        return None
    try:
        payload = decode_token(credentials.credentials)
        user_id = payload.get("sub")
    except JWTError:
        return None
    row = db.execute(
        text("SELECT id, email, name, plan, is_active, created_at, is_admin FROM users WHERE id = :id"),
        {"id": user_id}
    ).fetchone()
    if not row:
        return None
    user = dict(row._mapping)
    return user if user["is_active"] else None
