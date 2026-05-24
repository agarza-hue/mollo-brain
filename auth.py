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

from db import get_db, SessionLocal
from config import ENFORCE_PLAN_LIMITS, MOLLOIA_PLAN_LIMITS, OWNER_USER_ID

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


# ── Paywall: límites mensuales por plan (MolloIA) ─────────────────────────────
def plan_monthly_limit(plan: Optional[str]) -> int:
    return MOLLOIA_PLAN_LIMITS.get(plan or "free", MOLLOIA_PLAN_LIMITS["free"])


def monthly_request_count(db: Session, user_id: str) -> int:
    """Mensajes del usuario en el mes calendario actual (ventana auto-reseteable)."""
    row = db.execute(
        text("SELECT count(*) FROM usage_logs WHERE user_id = :uid "
             "AND created_at >= date_trunc('month', now())"),
        {"uid": user_id},
    ).fetchone()
    return int(row[0]) if row else 0


def quota_guard(user: Optional[dict] = Depends(get_optional_user),
                db: Session = Depends(get_db)) -> Optional[dict]:
    """Dependency: enforcea el límite mensual del plan para usuarios MolloIA.
    Devuelve el user sin cambios (la resolución de colección sigue igual).
    No-op si el flag está OFF, no hay user (anónimo/tenant), o es owner/admin."""
    if not ENFORCE_PLAN_LIMITS or not user:
        return user
    if str(user.get("id")) == OWNER_USER_ID or user.get("is_admin"):
        return user
    limit = plan_monthly_limit(user.get("plan"))
    if limit >= 999_999:
        return user
    used = monthly_request_count(db, user["id"])
    if used >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(f"Límite del plan {user.get('plan', 'free')} alcanzado "
                    f"({limit} mensajes/mes, usados {used}). "
                    f"Mejora tu plan para seguir usando MolloIA."),
        )
    return user


def record_request(user_id: Optional[str], model: str = "", complexity: str = "",
                   input_tokens: int = 0, output_tokens: int = 0) -> None:
    """Registra un request en usage_logs (alimenta el contador de cuota).
    Abre su propia sesión — pensado para background tasks. Silencioso ante error."""
    if not user_id:
        return
    try:
        db = SessionLocal()
        try:
            log_usage(db, user_id, model or "unknown", input_tokens, output_tokens, complexity or "")
        finally:
            db.close()
    except Exception:
        pass
