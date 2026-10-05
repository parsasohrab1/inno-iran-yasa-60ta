"""JWT auth + RBAC (FR-3-8, FR-4-4). LDAP/AD is a later integration point (see authenticate())."""
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import Role, User

_bearer = HTTPBearer(auto_error=False)
_ITER = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt, dk = stored.split("$")
    cand = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITER)
    return hmac.compare_digest(cand.hex(), dk)


def create_token(user: User) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_ttl_minutes)
    return jwt.encode({"sub": user.username, "role": user.role.value, "exp": exp}, settings.jwt_secret, "HS256")


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username))
    if user and user.active and verify_password(password, user.password_hash):
        return user
    return None


def current_user(
    cred: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)
) -> User:
    if not cred:
        raise HTTPException(401, "Not authenticated")
    user = db.scalar(select(User).where(User.username == decode_token(cred.credentials)["sub"]))
    if not user or not user.active:
        raise HTTPException(401, "Unknown or disabled user")
    return user


def require(*roles: Role):
    allowed = set(roles) | {Role.ADMIN}

    def dep(user: User = Depends(current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(403, "Insufficient permissions")
        return user

    return dep


def seed_admin(db: Session):
    if not db.scalar(select(User).where(User.username == settings.admin_username)):
        db.add(User(username=settings.admin_username, role=Role.ADMIN,
                    password_hash=hash_password(settings.admin_password)))
        db.commit()
