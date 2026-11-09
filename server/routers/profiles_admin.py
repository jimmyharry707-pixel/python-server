"""Admin endpoints to manage resume profiles and assign them to users."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import profile_service
from ..database import get_db
from ..models import Application, Interview, Profile, ProfileAssignment, User
from ..schemas import ProfileAdminOut, ProfileAdminUpsert, ProfileAssignees, ProfileOut
from ..security import get_current_admin

router = APIRouter(prefix="/admin/profiles", tags=["admin:profiles"])


# Fields the desktop client owns locally — never overwritten by admin upsert.
# Resume/cover-letter filenames are now admin/member-editable like the rest of
# profile identity; only the local save path stays desktop-client-only.
_CLIENT_ONLY_FIELDS = frozenset({"default_save_path"})


def _admin_out(db: Session, profiles: list[Profile]) -> list[ProfileAdminOut]:
    if not profiles:
        return []
    profile_ids = [p.id for p in profiles]
    # Archived people are hidden; their assignments are kept for a restore.
    assignments = db.execute(
        select(ProfileAssignment.profile_id, ProfileAssignment.user_id)
        .join(User, User.id == ProfileAssignment.user_id)
        .where(ProfileAssignment.profile_id.in_(profile_ids), User.is_active.is_(True))
    ).all()
    by_profile: dict[str, list[str]] = {pid: [] for pid in profile_ids}
    for profile_id, user_id in assignments:
        by_profile.setdefault(profile_id, []).append(user_id)

    return [
        ProfileAdminOut.model_validate(p).model_copy(
            update={"assigned_user_ids": by_profile.get(p.id, [])}
        )
        for p in profiles
    ]


@router.get("", response_model=list[ProfileAdminOut])
def list_profiles(
    _admin: User = Depends(get_current_admin), db: Session = Depends(get_db)
) -> list[ProfileAdminOut]:
    """Every profile, archived ones included (``archived_at`` set)."""
    profiles = list(db.execute(select(Profile).order_by(Profile.key)).scalars().all())
    return _admin_out(db, profiles)


@router.post("", response_model=ProfileAdminOut, status_code=status.HTTP_201_CREATED)
def create_profile(
    payload: ProfileAdminUpsert,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> ProfileAdminOut:
    """Create a profile with a server-generated key (never overwrites)."""
    profile = profile_service.create_profile(db, payload)
    db.commit()
    db.refresh(profile)
    return _admin_out(db, [profile])[0]


@router.post("/import", response_model=list[ProfileAdminOut], status_code=status.HTTP_201_CREATED)
def import_profiles(
    data: Any = Body(...),
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> list[ProfileAdminOut]:
    """Create profiles from an uploaded profile-info JSON (all or nothing)."""
    created = profile_service.import_profiles(db, data)
    db.commit()
    return _admin_out(db, created)


@router.put("/{key}", response_model=ProfileOut)
def upsert_profile(
    key: str,
    payload: ProfileAdminUpsert,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> Profile:
    key = (key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="Profile key is required")

    profile = db.execute(
        select(Profile).where(Profile.key == key)
    ).scalar_one_or_none()
    if profile is None:
        profile = Profile(key=key)
        db.add(profile)

    for field, value in payload.model_dump().items():
        if field in _CLIENT_ONLY_FIELDS:
            continue
        setattr(profile, field, value)
    db.commit()
    db.refresh(profile)
    return profile


@router.delete("/{key}")
def delete_profile(
    key: str,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Permanently delete a profile and all of its applications/interviews.

    Admin UIs must confirm that tracking data will be wiped. Order:
    interviews → applications → assignments → profile row.
    """
    profile = db.execute(
        select(Profile).where(Profile.key == key)
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    app_n = db.execute(
        select(func.count())
        .select_from(Application)
        .where(Application.profile_id == profile.id)
    ).scalar_one()
    iv_n = db.execute(
        select(func.count())
        .select_from(Interview)
        .where(Interview.profile_id == profile.id)
    ).scalar_one()

    app_ids = list(
        db.execute(
            select(Application.id).where(Application.profile_id == profile.id)
        ).scalars().all()
    )
    # Interviews first (may FK to applications), including linked ones on this profile.
    if app_ids:
        db.execute(delete(Interview).where(Interview.application_id.in_(app_ids)))
    db.execute(delete(Interview).where(Interview.profile_id == profile.id))
    db.execute(delete(Application).where(Application.profile_id == profile.id))
    db.execute(delete(ProfileAssignment).where(ProfileAssignment.profile_id == profile.id))
    db.delete(profile)
    db.commit()
    return {
        "ok": True,
        "profile": key,
        "deleted_applications": int(app_n),
        "deleted_interviews": int(iv_n),
    }


@router.get("/{key}/usage")
def profile_usage(
    key: str,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """How much tracking data a delete would wipe (shown in the confirm)."""
    profile = profile_service.get_profile(db, key)
    apps = db.execute(
        select(func.count()).select_from(Application).where(Application.profile_id == profile.id)
    ).scalar_one()
    ivs = db.execute(
        select(func.count()).select_from(Interview).where(Interview.profile_id == profile.id)
    ).scalar_one()
    return {"applications": int(apps), "interviews": int(ivs)}


@router.put("/{key}/assignees", response_model=ProfileAdminOut)
def set_assignees(
    key: str,
    payload: ProfileAssignees,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> ProfileAdminOut:
    """Replace everyone assigned to the profile in one step."""
    profile = profile_service.get_profile(db, key)
    profile_service.set_assignees(db, profile, set(payload.user_ids))
    db.commit()
    return _admin_out(db, [profile])[0]


@router.post("/{key}/archive", response_model=ProfileAdminOut)
def archive_profile(
    key: str,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> ProfileAdminOut:
    profile = profile_service.get_profile(db, key)
    profile_service.archive(profile)
    db.commit()
    return _admin_out(db, [profile])[0]


@router.post("/{key}/restore", response_model=ProfileAdminOut)
def restore_profile(
    key: str,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> ProfileAdminOut:
    profile = profile_service.get_profile(db, key)
    profile_service.restore(profile)
    db.commit()
    return _admin_out(db, [profile])[0]


@router.post("/{key}/assign/{user_id}", status_code=status.HTTP_201_CREATED)
def assign_profile(
    key: str,
    user_id: str,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    profile = profile_service.get_profile(db, key)
    profile_service.assign_one(db, profile, user_id)
    db.commit()
    return {"ok": True, "profile": key, "user_id": user_id}


@router.delete(
    "/{key}/assign/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def unassign_profile(
    key: str,
    user_id: str,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> Response:
    """Unassign; unassigning a member also unassigns their supporters."""
    profile = profile_service.get_profile(db, key)
    profile_service.unassign_one(db, profile, user_id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
