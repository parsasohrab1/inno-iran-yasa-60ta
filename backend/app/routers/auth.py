from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import authenticate, create_token, current_user, hash_password, require
from ..db import get_db
from ..models import Role, User
from ..schemas import LoginIn, TokenOut, UserIn, UserOut

router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/auth/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = authenticate(db, body.username, body.password)
    if not user:
        raise HTTPException(401, "Invalid credentials")
    return TokenOut(access_token=create_token(user), role=user.role)


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(require()), db: Session = Depends(get_db)):
    return db.scalars(select(User)).all()


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserIn, _: User = Depends(require()), db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.username == body.username)):
        raise HTTPException(409, "Username taken")
    user = User(username=body.username, role=body.role, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}/active", response_model=UserOut)
def set_active(user_id: int, active: bool, admin: User = Depends(require()), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == admin.id and not active:
        raise HTTPException(400, "Cannot disable yourself")
    user.active = active
    db.commit()
    return user
