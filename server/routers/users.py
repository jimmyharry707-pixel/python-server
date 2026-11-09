"""User endpoints: current user + assigned (admin-provisioned) profiles."""

from __future__ import annotations

import re
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth_sessions
from ..database import get_db
from ..models import LicenseSeat, Profile, ProfileAssignment, User
from ..schemas import (
    ProfileOut,
    UserChangePassword,
    UserOut,
    UserUpdateMe,
)
from ..security import (
    get_current_session_id,
    get_current_user,
    hash_password,
    verify_password,
)
from .license import require_active_license

router = APIRouter(prefix="/users", tags=["users"])

_AVATAR_DIR = Path(__file__).resolve().parent.parent / "data" / "avatars"
_AVATAR_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB
_AVATAR_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_AVATAR_URL_PREFIX = "/uploads/avatars"


def _avatar_dir() -> Path:
    _AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    return _AVATAR_DIR


def _safe_avatar_stem(user_id: str) -> str:
    # UUIDs only; still sanitize for path safety.
    return re.sub(r"[^a-zA-Z0-9_-]", "", user_id) or "user"


def delete_avatar_files(user_id: str) -> None:
    """Remove on-disk avatar image(s) for a user (best-effort)."""
    stem = _safe_avatar_stem(user_id)
    directory = _avatar_dir()
    for path in directory.glob(f"{stem}.*"):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _delete_avatar_files(user_id: str) -> None:
    delete_avatar_files(user_id)


@router.get("/me", response_model=UserOut)
def read_me(user: User = Depends(get_current_user)) -> User:
    return user


@router.patch("/me", response_model=UserOut)
def update_me(
    payload: UserUpdateMe,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Update display name and/or email. Email change requires current password."""
    changed = False

    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Display name cannot be empty")
        if len(name) > 255:
            raise HTTPException(status_code=400, detail="Display name is too long")
        if name != user.name:
            user.name = name
            changed = True

    if payload.email is not None:
        new_email = str(payload.email).strip().lower()
        current = (user.email or "").strip().lower()
        if new_email != current:
            if not payload.current_password:
                raise HTTPException(
                    status_code=400,
                    detail="Current password is required to change email",
                )
            if not verify_password(payload.current_password, user.password_hash):
                raise HTTPException(status_code=400, detail="Current password is incorrect")
            existing = db.execute(
                select(User).where(User.email == new_email)
            ).scalar_one_or_none()
            if existing is not None and existing.id != user.id:
                raise HTTPException(status_code=400, detail="Email is already in use")
            user.email = new_email
            changed = True

    if not changed and payload.name is None and payload.email is None:
        raise HTTPException(status_code=400, detail="No changes provided")

    db.commit()
    db.refresh(user)
    return user


@router.post("/me/password")
def change_my_password(
    payload: UserChangePassword,
    user: User = Depends(get_current_user),
    session_id: str | None = Depends(get_current_session_id),
    db: Session = Depends(get_db),
) -> dict:
    """Change password after verifying the current one. Signs out everywhere
    else; this browser or app stays signed in."""
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    new_password = (payload.new_password or "").strip()
    if len(new_password) < 8:
        raise HTTPException(
            status_code=400, detail="Password must be at least 8 characters"
        )
    if verify_password(new_password, user.password_hash):
        raise HTTPException(
            status_code=400, detail="New password must be different from the current one"
        )
    user.password_hash = hash_password(new_password)
    auth_sessions.end_user_sessions(db, user.id, reason="password_changed", keep=session_id)
    db.commit()
    return {"ok": True}


@router.post("/me/avatar", response_model=UserOut)
async def upload_my_avatar(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Upload a profile photo (jpeg/png/webp, max 2 MiB)."""
    content_type = (file.content_type or "").split(";")[0].strip().lower()
    ext = _AVATAR_TYPES.get(content_type)
    if not ext:
        raise HTTPException(
            status_code=400,
            detail="Avatar must be a JPEG, PNG, or WebP image",
        )

    directory = _avatar_dir()
    stem = _safe_avatar_stem(user.id)
    dest = directory / f"{stem}{ext}"

    written = 0
    try:
        # Remove any previous extension for this user before writing.
        _delete_avatar_files(user.id)
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(64 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > _AVATAR_MAX_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Avatar exceeds maximum size of 2 MB",
                    )
                out.write(chunk)
        if written == 0:
            dest.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="Empty upload")
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    # Keep one predictable file on disk, but version its public URL so React and
    # browser caches cannot reuse the image from a previous upload.
    user.avatar_url = f"{_AVATAR_URL_PREFIX}/{stem}{ext}?v={time.time_ns()}"
    db.commit()
    db.refresh(user)
    return user


@router.delete("/me/avatar", response_model=UserOut)
def delete_my_avatar(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """Remove the profile photo and fall back to initials."""
    _delete_avatar_files(user.id)
    user.avatar_url = None
    db.commit()
    db.refresh(user)
    return user


@router.get("/me/profiles", response_model=list[ProfileOut])
def my_profiles(
    user: User = Depends(get_current_user),
    _seat: LicenseSeat = Depends(require_active_license),
    db: Session = Depends(get_db),
) -> list[Profile]:
    """Return the read-only profiles an admin has assigned to this user.

    The desktop client wipes its local ``config/profiles`` and rewrites it from
    this list on sign-in. Requires a valid license lease. Archived profiles
    are left out, so they drop off the desktop app; their tracking data stays
    on the server and keeps syncing.
    """
    rows = db.execute(
        select(Profile)
        .join(ProfileAssignment, ProfileAssignment.profile_id == Profile.id)
        .where(ProfileAssignment.user_id == user.id, Profile.archived_at.is_(None))
        .order_by(Profile.key)
    ).scalars().all()
    return list(rows)
