"""Profile create/import/assign/archive rules shared by the admin and member routes.

Team rules:

* A supporter works for exactly one member (``User.member_id``), set by an
  admin or manager.
* A supporter may only be assigned to a profile their member is also on.
  Every path that changes assignments or team links keeps that true.
* Members create, import, edit and archive profiles and assign their own
  supporters; only admins/managers add or remove members, restore or delete.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .models import Profile, ProfileAssignment, User
from .schemas import ProfileAdminUpsert
from .security import normalize_role

MAX_IMPORT = 200


def profile_dict(profile: Profile) -> dict:
    out = {column.name: getattr(profile, column.name) for column in Profile.__table__.columns}
    for field in ("archived_at", "created_at", "updated_at"):
        value = out.get(field)
        out[field] = value.isoformat() + "Z" if value else None
    return out


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug[:64].strip("-") or "profile"


def new_profile_key(db: Session, name: str, taken: set[str] | None = None) -> str:
    """Unique key derived from ``name``; never collides with an existing profile."""
    base = _slug(name)
    used = set(
        db.execute(
            select(Profile.key).where(
                (Profile.key == base) | Profile.key.like(f"{base}-%")
            )
        ).scalars().all()
    ) | (taken or set())
    if base not in used:
        return base
    suffix = 2
    while f"{base}-{suffix}" in used:
        suffix += 1
    return f"{base}-{suffix}"


def _clean_values(payload: ProfileAdminUpsert) -> dict[str, Any]:
    values = {
        field: value.strip() if isinstance(value, str) else value
        for field, value in payload.model_dump().items()
    }
    if not values.get("name"):
        raise HTTPException(status_code=400, detail="Profile name is required")
    return values


def create_profile(
    db: Session,
    payload: ProfileAdminUpsert,
    creator: User | None = None,
    taken: set[str] | None = None,
) -> Profile:
    """Add a new profile (never overwrites). A member creator is auto-assigned."""
    values = _clean_values(payload)
    profile = Profile(key=new_profile_key(db, values["name"], taken), **values)
    db.add(profile)
    db.flush()
    if creator is not None and normalize_role(creator.role) == "member":
        db.add(ProfileAssignment(profile_id=profile.id, user_id=creator.id))
    return profile


def import_profiles(db: Session, data: Any, creator: User | None = None) -> list[Profile]:
    """Create one profile per entry of a profile-info JSON upload.

    Accepts a single object, a list, or ``{"profiles": [...]}``. Unknown keys
    are ignored; ``key`` in the file is ignored too so an import can never
    overwrite an existing profile.
    """
    items = data.get("profiles") if isinstance(data, dict) and "profiles" in data else data
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="The file has no profiles")
    if len(items) > MAX_IMPORT:
        raise HTTPException(status_code=400, detail=f"Import at most {MAX_IMPORT} profiles at once")
    created: list[Profile] = []
    taken: set[str] = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise HTTPException(status_code=400, detail=f"Profile #{index} is not an object")
        item = dict(item)
        if not item.get("name") and item.get("display_name"):
            item["name"] = item["display_name"]
        fields = {k: v for k, v in item.items() if k in ProfileAdminUpsert.model_fields}
        fields = {k: "" if v is None else str(v) for k, v in fields.items()}
        try:
            payload = ProfileAdminUpsert(**fields)
        except ValidationError as error:
            raise HTTPException(status_code=400, detail=f"Profile #{index}: {error.errors()[0]['msg']}")
        if not payload.name.strip():
            raise HTTPException(status_code=400, detail=f"Profile #{index} has no name")
        profile = create_profile(db, payload, creator, taken)
        taken.add(profile.key)
        created.append(profile)
    return created


def get_profile(db: Session, key: str, *, include_archived: bool = True) -> Profile:
    profile = db.execute(select(Profile).where(Profile.key == key)).scalar_one_or_none()
    if profile is None or (profile.archived_at is not None and not include_archived):
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


def assignee_ids(db: Session, profile_id: str, *, active_only: bool = False) -> set[str]:
    query = select(ProfileAssignment.user_id).where(ProfileAssignment.profile_id == profile_id)
    if active_only:
        query = query.join(User, User.id == ProfileAssignment.user_id).where(
            User.is_active.is_(True)
        )
    return set(db.execute(query).scalars().all())


def team_supporter_ids(db: Session, member_id: str, *, active_only: bool = False) -> set[str]:
    query = select(User.id).where(User.member_id == member_id, User.role == "supporter")
    if active_only:
        query = query.where(User.is_active.is_(True))
    return set(db.execute(query).scalars().all())


def _display(user: User) -> str:
    return user.name or user.email


def set_assignees(db: Session, profile: Profile, user_ids: set[str]) -> None:
    """Admin: replace who is assigned to ``profile`` in one step.

    Rejects a supporter whose member is not in the new set. Unlinked
    supporters may stay where they already are (legacy data) but can't be
    newly added until an admin links them to a member. Archived people are
    hidden from the admin UI, so their assignments are left as they are.
    """
    current = assignee_ids(db, profile.id, active_only=True)
    users = {
        u.id: u
        for u in db.execute(select(User).where(User.id.in_(user_ids | current))).scalars().all()
    }
    missing = user_ids - users.keys()
    if missing:
        raise HTTPException(status_code=404, detail="User not found")
    for uid in user_ids:
        user = users[uid]
        if not user.is_active and uid not in current:
            raise HTTPException(status_code=400, detail=f"{_display(user)} is archived")
        role = normalize_role(user.role)
        if role == "admin":
            raise HTTPException(status_code=400, detail="Admins can't be assigned to profiles")
        if role != "supporter":
            continue
        if user.member_id is None:
            if uid not in current:
                raise HTTPException(
                    status_code=400,
                    detail=f"Link {_display(user)} to a member on the Users page first",
                )
        elif user.member_id not in user_ids:
            member = db.get(User, user.member_id)
            raise HTTPException(
                status_code=400,
                detail=f"{_display(user)} works for {_display(member) if member else 'a member'}, "
                "who must be on this profile too",
            )
    remove = current - user_ids
    if remove:
        db.execute(
            delete(ProfileAssignment).where(
                ProfileAssignment.profile_id == profile.id,
                ProfileAssignment.user_id.in_(remove),
            )
        )
    for uid in user_ids - current:
        db.add(ProfileAssignment(profile_id=profile.id, user_id=uid))


def assign_one(db: Session, profile: Profile, user_id: str) -> None:
    set_assignees(db, profile, assignee_ids(db, profile.id) | {user_id})


def unassign_one(db: Session, profile: Profile, user_id: str) -> None:
    """Remove one assignee; removing a member also removes their supporters."""
    remove = {user_id} | team_supporter_ids(db, user_id)
    db.execute(
        delete(ProfileAssignment).where(
            ProfileAssignment.profile_id == profile.id,
            ProfileAssignment.user_id.in_(remove),
        )
    )


def member_profiles(db: Session, member_id: str) -> list[Profile]:
    """Active profiles the member is assigned to."""
    return list(
        db.execute(
            select(Profile)
            .join(ProfileAssignment, ProfileAssignment.profile_id == Profile.id)
            .where(ProfileAssignment.user_id == member_id, Profile.archived_at.is_(None))
            .order_by(Profile.name, Profile.key)
        ).scalars().all()
    )


def member_profile(db: Session, member: User, key: str) -> Profile:
    for profile in member_profiles(db, member.id):
        if profile.key == key:
            return profile
    raise HTTPException(status_code=404, detail="Profile not assigned to you")


def set_member_supporters(
    db: Session, profile: Profile, member: User, supporter_ids: set[str]
) -> None:
    """Member: set which of their own supporters work on ``profile``.

    Other members' supporters and co-members on the profile are untouched.
    """
    team = team_supporter_ids(db, member.id, active_only=True)
    foreign = supporter_ids - team
    if foreign:
        raise HTTPException(status_code=403, detail="You can only assign supporters on your team")
    # Archived supporters are hidden from members, so leave their rows alone.
    current = assignee_ids(db, profile.id) & team
    remove = current - supporter_ids
    if remove:
        db.execute(
            delete(ProfileAssignment).where(
                ProfileAssignment.profile_id == profile.id,
                ProfileAssignment.user_id.in_(remove),
            )
        )
    for uid in supporter_ids - current:
        db.add(ProfileAssignment(profile_id=profile.id, user_id=uid))


def link_supporter(db: Session, supporter: User, member_id: str | None) -> None:
    """Admin: move a supporter onto ``member_id``'s team (or unlink).

    Drops the supporter's assignments on profiles the new member isn't on.
    """
    if normalize_role(supporter.role) != "supporter":
        raise HTTPException(status_code=400, detail="Only supporters can be linked to a member")
    if member_id is not None:
        member = db.get(User, member_id)
        if member is None or normalize_role(member.role) != "member":
            raise HTTPException(status_code=400, detail="Pick an existing member")
    if supporter.member_id == member_id:
        return
    supporter.member_id = member_id
    keep = (
        select(ProfileAssignment.profile_id).where(ProfileAssignment.user_id == member_id)
        if member_id
        else None
    )
    query = delete(ProfileAssignment).where(ProfileAssignment.user_id == supporter.id)
    if keep is not None:
        query = query.where(ProfileAssignment.profile_id.notin_(keep))
    db.execute(query)


def on_role_change(db: Session, user: User, old_role: str, new_role: str) -> None:
    """Keep team links valid when an account changes role."""
    if old_role == "supporter" and new_role != "supporter":
        user.member_id = None
    if old_role == "member" and new_role != "member":
        unlink_team(db, user.id)
        # Work plan reviewer is a member-only grant; don't let it come back later.
        user.can_review_work_plans = False


def unlink_team(db: Session, member_id: str) -> None:
    """A member is going away: their supporters become unlinked.

    Supporters keep their profile assignments; an admin re-links them.
    """
    db.execute(update(User).where(User.member_id == member_id).values(member_id=None))


def archive(profile: Profile) -> None:
    if profile.archived_at is None:
        profile.archived_at = datetime.utcnow()


def restore(profile: Profile) -> None:
    profile.archived_at = None
