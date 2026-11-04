"""Login, current user, password change, and admin user management."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_admin
from app.db import get_db
from app.models import AuditLog, Tenant, User, utcnow
from app.schemas import PasswordChange, Token, UserCreate, UserOut, UserUpdate
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api", tags=["auth"])

# Same message for unknown email and wrong password: no account enumeration.
BAD_LOGIN = "Incorrect email or password"


@router.post("/auth/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(func.lower(User.email) == form.username.strip().lower()))
    if user is None or not verify_password(form.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=BAD_LOGIN)
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This account has been disabled")
    user.last_login_at = utcnow()
    db.add(AuditLog(actor=user.email, action="login"))
    db.commit()
    token, expires_in = create_access_token(user.id, user.role)
    return Token(access_token=token, expires_in=expires_in)


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.post("/auth/password", status_code=204)
def change_password(body: PasswordChange, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    if body.current_password == body.new_password:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="New password must differ from the current one")
    user.password_hash = hash_password(body.new_password)
    db.add(AuditLog(actor=user.email, action="password_changed"))
    db.commit()


@router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return db.scalars(select(User).order_by(User.created_at)).all()


def _check_tenant(db: Session, tenant_id: str | None) -> None:
    if tenant_id is not None and db.get(Tenant, tenant_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown tenant '{tenant_id}'")


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="A user with this email already exists")
    _check_tenant(db, body.tenant_id)
    if body.role == "admin" and body.tenant_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Admins cannot be scoped to a tenant")
    user = User(email=email, full_name=body.full_name.strip(), password_hash=hash_password(body.password),
                role=body.role, tenant_id=body.tenant_id)
    db.add(user)
    db.add(AuditLog(actor=admin.email, action="user_created", target=email, details={"role": body.role}))
    db.commit()
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
    removing_admin = user.role == "admin" and (body.role == "viewer" or body.is_active is False)
    if removing_admin:
        if user.id == admin.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="You cannot demote or disable your own account")
        active_admins = db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.is_active))
        if active_admins <= 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="At least one active admin is required")
    if body.full_name is not None:
        user.full_name = body.full_name.strip()
    if body.role is not None:
        user.role = body.role
    if body.clear_tenant:
        user.tenant_id = None
    elif body.tenant_id is not None:
        _check_tenant(db, body.tenant_id)
        user.tenant_id = body.tenant_id
    if user.role == "admin" and user.tenant_id is not None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Admins cannot be scoped to a tenant")
    if body.is_active is not None:
        user.is_active = body.is_active
    db.add(AuditLog(actor=admin.email, action="user_updated", target=user.email,
                    details=body.model_dump(exclude_none=True)))
    db.commit()
    return user
