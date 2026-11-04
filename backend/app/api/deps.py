"""Request dependencies: DB session, authenticated user, role and tenant-scope checks."""

from __future__ import annotations

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import decode_token

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def current_user(token: str | None = Depends(oauth2), db: Session = Depends(get_db)) -> User:
    if not token:
        raise _unauthorized("Not authenticated")
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Session expired")
    except jwt.InvalidTokenError:
        raise _unauthorized("Invalid token")
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise _unauthorized("Account disabled or removed")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return user


def visible_tenant_ids(user: User) -> list[str] | None:
    """None means every tenant; otherwise the user's single tenant."""
    return None if user.tenant_id is None else [user.tenant_id]


def check_tenant_access(user: User, tenant_id: str) -> None:
    if user.tenant_id is not None and user.tenant_id != tenant_id:
        # 404 rather than 403 so scoped users cannot probe for other tenants' ids.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tenant not found")
