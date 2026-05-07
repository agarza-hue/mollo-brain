"""
Endpoints de autenticación — /auth/register, /auth/login, /auth/me
"""
from fastapi import APIRouter, HTTPException, Depends, status
from sqlalchemy.orm import Session

from db import get_db
from auth import (
    UserRegister, UserLogin, TokenResponse, UserOut,
    get_user_by_email, create_user,
    verify_password, create_access_token,
    get_current_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(body: UserRegister, db: Session = Depends(get_db)):
    if get_user_by_email(db, body.email):
        raise HTTPException(status_code=400, detail="Email ya registrado")
    if len(body.password) < 6:
        raise HTTPException(status_code=400, detail="Password mínimo 6 caracteres")
    user = create_user(db, body.email, body.name, body.password)
    token = create_access_token(str(user["id"]), user["email"])
    return TokenResponse(
        access_token=token,
        user=UserOut(**{k: str(v) if k == "id" else v for k, v in user.items() if k != "hashed_password" and k != "is_active"}),
    )


@router.post("/login", response_model=TokenResponse)
def login(body: UserLogin, db: Session = Depends(get_db)):
    user = get_user_by_email(db, body.email)
    if not user or not verify_password(body.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="Cuenta inactiva")
    token = create_access_token(str(user["id"]), user["email"])
    return TokenResponse(
        access_token=token,
        user=UserOut(**{k: str(v) if k == "id" else v for k, v in user.items() if k != "hashed_password" and k != "is_active"}),
    )


@router.get("/me", response_model=UserOut)
def me(current_user: dict = Depends(get_current_user)):
    return UserOut(**{k: str(v) if k == "id" else v for k, v in current_user.items()})
