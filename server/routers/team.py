"""Team-facing endpoints: observability for assigned profiles, colleague presence."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import re
import time
from typing import Any, Literal
from zoneinfo import available_timezones

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import profile_service
from ..database import get_db
from ..models import Application, Client, Interview, MemberAvatarOverride, Profile, ProfileAssignment, User
from ..schemas import MemberAssignments, MemberProfileSupporters, ProfileAdminUpsert
from ..security import get_current_user, get_user_profile_ids, hash_password, normalize_role

router = APIRouter(prefix="/team", tags=["team"])

_AVATAR_DIR = Path(__file__).resolve().parent.parent / "data" / "avatars"
_AVATAR_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_AVATAR_MAX_BYTES = 2 * 1024 * 1024


class MemberSupporterPassword(BaseModel):
    password: str = Field(min_length=8, max_length=128)


def _require_member(user: User) -> None:
    if normalize_role(user.role) != "member":
        raise HTTPException(status_code=403, detail="Member access required")


def _member_supporter_ids(db: Session, member_id: str) -> set[str]:
    """Active supporters on the member's team (linked by an admin via ``member_id``).

    Archived supporters are hidden from members entirely.
    """
    return profile_service.team_supporter_ids(db, member_id, active_only=True)


def _override_stem(member_id: str, supporter_id: str) -> str:
    safe = lambda value: re.sub(r"[^a-zA-Z0-9_-]", "", value)
    return f"custom-{safe(member_id)}-{safe(supporter_id)}"


def _delete_override_files(member_id: str, supporter_id: str) -> None:
    _AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    for path in _AVATAR_DIR.glob(f"{_override_stem(member_id, supporter_id)}.*"):
        path.unlink(missing_ok=True)


@router.get("/member/supporters")
def member_supporters(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    """The signed-in member's team, with which of their profiles each one is on."""
    _require_member(user)
    ids = _member_supporter_ids(db, user.id)
    if not ids:
        return []
    supporters = db.execute(select(User).where(User.id.in_(ids)).order_by(User.name, User.email)).scalars().all()
    overrides = {row.supporter_id: row.avatar_url for row in db.execute(
        select(MemberAvatarOverride).where(MemberAvatarOverride.member_id == user.id)
    ).scalars().all()}
    my_profiles = {p.id: p.key for p in profile_service.member_profiles(db, user.id)}
    keys_by_supporter: dict[str, list[str]] = {}
    if my_profiles:
        for supporter_id, profile_id in db.execute(
            select(ProfileAssignment.user_id, ProfileAssignment.profile_id).where(
                ProfileAssignment.user_id.in_(ids), ProfileAssignment.profile_id.in_(my_profiles)
            )
        ).all():
            keys_by_supporter.setdefault(supporter_id, []).append(my_profiles[profile_id])
    return [{"id": item.id, "name": item.name, "email": item.email, "role": "supporter",
             "is_active": bool(item.is_active),
             "avatar_url": overrides.get(item.id) or item.avatar_url,
             "custom_avatar_url": overrides.get(item.id),
             "profile_keys": sorted(keys_by_supporter.get(item.id, []))} for item in supporters]


@router.post("/member/supporters/{supporter_id}/avatar")
async def set_supporter_avatar(supporter_id: str, file: UploadFile = File(...),
                               user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    _require_member(user)
    if supporter_id not in _member_supporter_ids(db, user.id):
        raise HTTPException(status_code=404, detail="Supporter is not assigned to your profiles")
    content_type = (file.content_type or "").split(";")[0].lower()
    ext = _AVATAR_TYPES.get(content_type)
    if not ext:
        raise HTTPException(status_code=400, detail="Avatar must be a JPEG, PNG, or WebP image")
    supporter = db.get(User, supporter_id)
    if supporter is None:
        raise HTTPException(status_code=404, detail="Supporter not found")
    from .users import delete_avatar_files, _safe_avatar_stem

    _AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    _delete_override_files(user.id, supporter_id)
    # This is the supporter's shared account avatar, so admin and all assigned
    # members see the same image. Use the normal user-avatar filename scheme.
    delete_avatar_files(supporter_id)
    stem = _safe_avatar_stem(supporter_id)
    dest = _AVATAR_DIR / f"{stem}{ext}"
    written = 0
    try:
        with dest.open("wb") as output:
            while chunk := await file.read(64 * 1024):
                written += len(chunk)
                if written > _AVATAR_MAX_BYTES:
                    raise HTTPException(status_code=413, detail="Avatar exceeds maximum size of 2 MB")
                output.write(chunk)
        if not written:
            raise HTTPException(status_code=400, detail="Empty upload")
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    # The file path stays stable for cleanup, while the query version forces
    # every avatar already rendered in the UI to fetch the replacement image.
    url = f"/uploads/avatars/{stem}{ext}?v={time.time_ns()}"
    row = db.execute(select(MemberAvatarOverride).where(
        MemberAvatarOverride.member_id == user.id, MemberAvatarOverride.supporter_id == supporter_id
    )).scalar_one_or_none()
    if row is None:
        row = MemberAvatarOverride(member_id=user.id, supporter_id=supporter_id, avatar_url=url)
        db.add(row)
    else:
        row.avatar_url = url
    supporter.avatar_url = url
    db.commit()
    return {"avatar_url": url}


@router.delete("/member/supporters/{supporter_id}/avatar")
def clear_supporter_avatar(supporter_id: str, user: User = Depends(get_current_user),
                           db: Session = Depends(get_db)) -> dict:
    _require_member(user)
    if supporter_id not in _member_supporter_ids(db, user.id):
        raise HTTPException(status_code=404, detail="Supporter is not assigned to your profiles")
    row = db.execute(select(MemberAvatarOverride).where(
        MemberAvatarOverride.member_id == user.id, MemberAvatarOverride.supporter_id == supporter_id
    )).scalar_one_or_none()
    supporter = db.get(User, supporter_id)
    if row is not None:
        if supporter is not None and supporter.avatar_url == row.avatar_url:
            supporter.avatar_url = None
            from .users import delete_avatar_files
            delete_avatar_files(supporter_id)
        db.delete(row)
    db.commit()
    _delete_override_files(user.id, supporter_id)
    return {"ok": True}


def _member_profile_out(db: Session, member: User, profiles: list[Profile]) -> list[dict]:
    """Profile fields plus which of the member's own supporters are on each."""
    team = _member_supporter_ids(db, member.id)
    on_profile: dict[str, list[str]] = {p.id: [] for p in profiles}
    if team and profiles:
        for profile_id, user_id in db.execute(
            select(ProfileAssignment.profile_id, ProfileAssignment.user_id).where(
                ProfileAssignment.profile_id.in_(on_profile), ProfileAssignment.user_id.in_(team)
            )
        ).all():
            on_profile[profile_id].append(user_id)
    out = []
    for profile in profiles:
        row = profile_service.profile_dict(profile)
        row.pop("default_save_path", None)
        row["supporter_ids"] = sorted(on_profile.get(profile.id, []))
        out.append(row)
    return out


@router.get("/member/profiles")
def member_profiles(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    """Active profiles the member is on; archived ones are hidden."""
    _require_member(user)
    return _member_profile_out(db, user, profile_service.member_profiles(db, user.id))


@router.post("/member/profiles", status_code=201)
def member_create_profile(
    payload: ProfileAdminUpsert,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Create a profile; the member is assigned to it automatically."""
    _require_member(user)
    profile = profile_service.create_profile(db, payload, creator=user)
    db.commit()
    db.refresh(profile)
    return _member_profile_out(db, user, [profile])[0]


@router.post("/member/profiles/import", status_code=201)
def member_import_profiles(
    data: Any = Body(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Create profiles from an uploaded profile-info JSON (all or nothing)."""
    _require_member(user)
    created = profile_service.import_profiles(db, data, creator=user)
    db.commit()
    return _member_profile_out(db, user, created)


@router.put("/member/profiles/{profile_key}/supporters")
def member_set_profile_supporters(
    profile_key: str,
    payload: MemberProfileSupporters,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Set which of the member's own supporters work on this profile."""
    _require_member(user)
    profile = profile_service.member_profile(db, user, profile_key)
    profile_service.set_member_supporters(db, profile, user, set(payload.supporter_ids))
    db.commit()
    return _member_profile_out(db, user, [profile])[0]


@router.put("/member/assignments")
def member_set_assignments(
    payload: MemberAssignments,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Grid save: supporters per profile for several profiles in one transaction."""
    _require_member(user)
    for key, supporter_ids in payload.assignments.items():
        profile = profile_service.member_profile(db, user, key)
        profile_service.set_member_supporters(db, profile, user, set(supporter_ids))
    db.commit()
    return _member_profile_out(db, user, profile_service.member_profiles(db, user.id))


@router.post("/member/profiles/{profile_key}/archive")
def member_archive_profile(
    profile_key: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Archive: hidden from the desktop app and member pages; data is kept.

    Only an admin or manager can restore it.
    """
    _require_member(user)
    profile = profile_service.member_profile(db, user, profile_key)
    profile_service.archive(profile)
    db.commit()
    return {"ok": True, "key": profile.key}


@router.post("/member/supporters/{supporter_id}/set-password")
def member_set_supporter_password(
    supporter_id: str,
    payload: MemberSupporterPassword,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _require_member(user)
    if supporter_id not in _member_supporter_ids(db, user.id):
        raise HTTPException(status_code=404, detail="Supporter is not assigned to your profiles")
    supporter = db.get(User, supporter_id)
    if supporter is None or normalize_role(supporter.role) != "supporter":
        raise HTTPException(status_code=404, detail="Supporter not found")
    password = payload.password.strip()
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    supporter.password_hash = hash_password(password)
    db.commit()
    return {"ok": True, "id": supporter.id}


@router.put("/member/profiles/{profile_key}")
def member_update_profile(
    profile_key: str,
    payload: ProfileAdminUpsert,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    _require_member(user)
    profile = profile_service.member_profile(db, user, profile_key)
    values = payload.model_dump()
    name = values.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Profile name is required")
    for field, value in values.items():
        setattr(profile, field, value.strip() if isinstance(value, str) else value)
    db.commit()
    db.refresh(profile)
    return _member_profile_out(db, user, [profile])[0]

from ..presence import ONLINE_WINDOW  # noqa: E402

Window = Literal["1d", "2d", "7d", "14d", "30d", "all"]


def _parse_team_iso_dt(value: str | None) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is not None:
            dt = (
                (dt - dt.utcoffset()).replace(tzinfo=None)
                if dt.utcoffset()
                else dt.replace(tzinfo=None)
            )
        return dt
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid datetime: {value}") from None


# window -> (granularity, periods) for fixed windows; "all" is computed.
_WINDOW_SPEC: dict[str, tuple[str, int]] = {
    "1d": ("day", 1),
    "2d": ("day", 2),
    "7d": ("day", 7),
    "14d": ("day", 14),
    "30d": ("day", 30),
}


def _learn_member_timezone(db: Session, user: User, timezone: str | None) -> None:
    """Opportunistically store a member's real IANA zone from her own browser.

    Members use the web app for their own Dashboard/Performance, so this fires
    on essentially every login. Supporters mostly don't use the site, so an
    admin-set ``User.timezone`` (needed for the Colleagues tab) stays
    authoritative for them — this never overwrites it.
    """
    if not timezone or normalize_role(user.role) != "member":
        return
    if timezone == user.timezone or timezone not in available_timezones():
        return
    user.timezone = timezone
    db.commit()


def _clamp_tz_offset(tz_offset: int | None) -> int:
    """Minutes east of UTC (JS: ``-new Date().getTimezoneOffset()``)."""
    if tz_offset is None:
        return 0
    return max(-14 * 60, min(14 * 60, int(tz_offset)))


def _to_local(dt: datetime, tz_offset: int) -> datetime:
    """Shift a naive-UTC datetime into the caller's local wall clock."""
    return dt + timedelta(minutes=tz_offset)


def _local_today(tz_offset: int) -> datetime:
    return _to_local(datetime.utcnow(), tz_offset).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _week_start(dt: datetime) -> datetime:
    """Start (Saturday, midnight) of the Saturday-to-Friday week containing dt."""
    days_since_saturday = (dt.weekday() - 5) % 7
    return dt.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=days_since_saturday
    )


def _bucket_key(dt: datetime, granularity: str) -> str:
    if granularity == "day":
        return dt.strftime("%Y-%m-%d")
    if granularity == "month":
        return dt.strftime("%Y-%m")
    return _week_start(dt).strftime("%Y-%m-%d")


def _month_end(year: int, month: int) -> datetime:
    if month == 12:
        return datetime(year + 1, 1, 1) - timedelta(seconds=1)
    return datetime(year, month + 1, 1) - timedelta(seconds=1)


def _human_day(d: datetime) -> str:
    return d.strftime("%b %d").replace(" 0", " ")


def _bucket_meta(key: str, granularity: str) -> dict:
    # start/end are calendar-day markers (not real UTC instants). Clients must
    # render them with UTC calendar components so local TZ does not shift the day.
    if granularity == "day":
        start = datetime.strptime(key, "%Y-%m-%d")
        end = start.replace(hour=23, minute=59, second=59)
        return {
            "key": key,
            "label": _human_day(start),
            "start": start.isoformat() + "Z",
            "end": end.isoformat() + "Z",
        }
    if granularity == "month":
        year, month = int(key[:4]), int(key[5:7])
        start = datetime(year, month, 1)
        end = _month_end(year, month)
        return {
            "key": key,
            "label": start.strftime("%b %Y"),
            "start": start.isoformat() + "Z",
            "end": end.isoformat() + "Z",
        }
    # week: Saturday start date, YYYY-MM-DD
    start = datetime.strptime(key, "%Y-%m-%d")
    end = start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    label = f"{_human_day(start)}–{_human_day(end)}"
    if start.month != end.month:
        label = f"{_human_day(start)}–{_human_day(end)}"
    return {
        "key": key,
        "label": label,
        "start": start.isoformat() + "Z",
        "end": end.isoformat() + "Z",
    }


def _bucket_range(granularity: str, periods: int, tz_offset: int = 0) -> list[dict]:
    today = _local_today(tz_offset)
    keys: list[str] = []
    if granularity == "day":
        for i in range(periods):
            keys.append((today - timedelta(days=i)).strftime("%Y-%m-%d"))
    elif granularity == "month":
        year, month = today.year, today.month
        for _ in range(periods):
            keys.append(f"{year:04d}-{month:02d}")
            month -= 1
            if month == 0:
                month, year = 12, year - 1
    else:
        cur = today
        for _ in range(periods):
            keys.append(_bucket_key(cur, "week"))
            cur = cur - timedelta(days=7)
    keys.reverse()
    return [_bucket_meta(k, granularity) for k in keys]


def _assigned_profiles(db: Session, user_id: str) -> list[Profile]:
    return list(
        db.execute(
            select(Profile)
            .join(ProfileAssignment, ProfileAssignment.profile_id == Profile.id)
            .where(ProfileAssignment.user_id == user_id)
            .order_by(Profile.key)
        )
        .scalars()
        .all()
    )


def _resolve_assigned_profile(
    db: Session, user_id: str, profile_key: str | None
) -> Profile | None:
    key = (profile_key or "").strip()
    if not key:
        return None
    profiles = {p.key: p for p in _assigned_profiles(db, user_id)}
    profile = profiles.get(key)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not assigned to you")
    return profile


def _window_buckets(
    window: str, earliest: datetime | None, tz_offset: int = 0
) -> tuple[str, list[dict]]:
    """Return (granularity, buckets) for a named window in the caller's local TZ."""
    if window in _WINDOW_SPEC:
        granularity, periods = _WINDOW_SPEC[window]
        return granularity, _bucket_range(granularity, periods, tz_offset)

    # all — span from earliest activity (or 12 months fallback) using month buckets
    today = _local_today(tz_offset)
    if earliest is None:
        return "month", _bucket_range("month", 12, tz_offset)
    start = _to_local(earliest, tz_offset).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    # Prefer day if span is short, else month
    days = max(1, (today - start).days + 1)
    if days <= 45:
        return "day", _bucket_range("day", min(days, 45), tz_offset)
    # months between start and today inclusive
    months = (today.year - start.year) * 12 + (today.month - start.month) + 1
    periods = max(1, min(months, 36))
    return "month", _bucket_range("month", periods, tz_offset)


@router.get("/observability")
def team_observability(
    window: Window = Query("7d"),
    granularity: Literal["day", "week", "month"] | None = None,
    periods: int | None = Query(None, ge=1, le=90),
    period_offset: int = Query(0, ge=0, le=90),
    profile_key: str | None = None,
    contributor_id: str | None = None,
    tz_offset: int | None = Query(
        None,
        description="Client offset minutes east of UTC (−getTimezoneOffset()). "
        "Used so day buckets match desktop tracking local dates.",
    ),
    timezone: str | None = Query(
        None, description="Client IANA zone (Intl.DateTimeFormat().resolvedOptions().timeZone)."
    ),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Assigned-profile activity plus organization-wide contributor rankings.

    ``window``: 1d | 2d | 7d | 14d | 30d | all. Supplying
    ``granularity`` and ``periods`` enables exact calendar ranges; ``period_offset``
    skips the newest buckets for previous-period comparisons.
    Optional ``profile_key`` filters to one assigned profile; default is all assigned.
    Dates are bucketed in the client's local timezone (same as desktop bid_date).
    """
    offset = _clamp_tz_offset(tz_offset)
    _learn_member_timezone(db, user, timezone)
    assigned = _assigned_profiles(db, user.id)
    assigned_ids = {p.id for p in assigned}
    focus = _resolve_assigned_profile(db, user.id, profile_key)
    scope_ids = {focus.id} if focus else assigned_ids
    supporter_ids = (
        _member_supporter_ids(db, user.id) if normalize_role(user.role) == "member" else set()
    )
    if contributor_id and contributor_id not in supporter_ids:
        raise HTTPException(status_code=403, detail="Supporter is not assigned to your profiles")

    app_filters = [Application.deleted_at.is_(None), Application.profile_id.in_(scope_ids)]
    iv_filters = [Interview.deleted_at.is_(None), Interview.profile_id.in_(scope_ids)]
    if contributor_id:
        app_filters.append(Application.user_id == contributor_id)
        iv_filters.append(Interview.user_id == contributor_id)
    app_rows = db.execute(
        select(Application.user_id, Application.profile_id, Application.applied_date).where(
            *app_filters
        )
    ).all()
    iv_rows = db.execute(
        select(Interview.user_id, Interview.profile_id, Interview.start_time_utc).where(*iv_filters)
    ).all()

    dated_apps = [r[2] for r in app_rows if r[2] is not None]
    dated_ivs = [r[2] for r in iv_rows if r[2] is not None]
    earliest = None
    for dt in dated_apps + dated_ivs:
        if earliest is None or dt < earliest:
            earliest = dt

    if granularity and periods:
        buckets = _bucket_range(granularity, periods + period_offset, offset)[:periods]
    else:
        granularity, buckets = _window_buckets(window, earliest, offset)
    bucket_keys = [b["key"] for b in buckets]
    bucket_set = set(bucket_keys)

    app_series = {k: 0 for k in bucket_keys}
    iv_series = {k: 0 for k in bucket_keys}
    undated_apps = 0
    undated_ivs = 0
    profile_app_keys: dict[str, list[str | None]] = {}
    profile_iv_keys: dict[str, list[str | None]] = {}
    editor_app_keys: dict[tuple[str, str], list[str | None]] = {}
    editor_iv_keys: dict[tuple[str, str], list[str | None]] = {}
    last_applied: dict[str, datetime] = {}

    for user_id, profile_id, applied in app_rows:
        if applied is None:
            undated_apps += 1
            key = None
        else:
            if user_id not in last_applied or applied > last_applied[user_id]:
                last_applied[user_id] = applied
            # applied_date already *is* a calendar day: the desktop encodes the
            # local bid_date at UTC noon, so its UTC date is the day the user
            # applied, in their own timezone. Shifting it again double-applies
            # the offset — harmless inside ±12h, but past that it lands the row
            # in the next day's bucket, out of the window, and the KPI drops it
            # while the applications table below still lists it. Bucket the raw
            # value so both read the same day. (Interviews are real UTC instants
            # and do still need the shift.)
            key = _bucket_key(applied, granularity)
            if key in bucket_set:
                app_series[key] += 1
            else:
                key = None
        profile_app_keys.setdefault(profile_id, []).append(key)
        editor_app_keys.setdefault((user_id, profile_id), []).append(key)

    for user_id, profile_id, start in iv_rows:
        if start is None:
            undated_ivs += 1
            key = None
        else:
            key = _bucket_key(_to_local(start, offset), granularity)
            if key in bucket_set:
                iv_series[key] += 1
            else:
                key = None
        profile_iv_keys.setdefault(profile_id, []).append(key)
        editor_iv_keys.setdefault((user_id, profile_id), []).append(key)

    app_counts = [app_series[k] for k in bucket_keys]
    iv_counts = [iv_series[k] for k in bucket_keys]
    assignment_rows = db.execute(
        select(ProfileAssignment.user_id, ProfileAssignment.profile_id)
    ).all()
    assigned_profiles: dict[str, set[str]] = {}
    for assigned_user_id, profile_id in assignment_rows:
        assigned_profiles.setdefault(assigned_user_id, set()).add(profile_id)

    # Rankings are organization-wide, matching the admin dashboard. The activity
    # series above remains limited to profiles assigned to the signed-in user.
    ranking_app_rows = db.execute(
        select(
            Application.id, Application.user_id, Application.profile_id, Application.applied_date
        ).where(Application.deleted_at.is_(None))
    ).all()
    ranking_iv_rows = db.execute(
        select(
            Interview.user_id,
            Interview.profile_id,
            Interview.start_time_utc,
            Interview.application_id,
        ).where(Interview.deleted_at.is_(None))
    ).all()
    ranking_profile_app_keys: dict[str, list[str | None]] = {}
    ranking_profile_iv_keys: dict[str, list[str | None]] = {}
    ranking_editor_app_keys: dict[tuple[str, str | None], list[str | None]] = {}
    ranking_editor_iv_keys: dict[tuple[str, str | None], list[str | None]] = {}
    ranking_last_applied: dict[str, datetime] = {}
    # application_id -> submitter user_id, so an interview can be credited to
    # whoever submitted the application it came from (rather than whoever
    # happened to create the interview record) — this is what makes a
    # supporter's interview count reflect their own submitted applications.
    ranking_app_owner: dict[str, str] = {}

    for app_id, editor_id, profile_id, applied in ranking_app_rows:
        ranking_app_owner[app_id] = editor_id
        # Raw value: applied_date is already a calendar day (see above).
        key = _bucket_key(applied, granularity) if applied else None
        if key not in bucket_set:
            key = None
        if profile_id:
            ranking_profile_app_keys.setdefault(profile_id, []).append(key)
        ranking_editor_app_keys.setdefault((editor_id, profile_id), []).append(key)
        if applied and (
            editor_id not in ranking_last_applied
            or applied > ranking_last_applied[editor_id]
        ):
            ranking_last_applied[editor_id] = applied

    for editor_id, profile_id, start, application_id in ranking_iv_rows:
        key = _bucket_key(_to_local(start, offset), granularity) if start else None
        if key not in bucket_set:
            key = None
        if profile_id:
            ranking_profile_iv_keys.setdefault(profile_id, []).append(key)
        attributed_id = (
            ranking_app_owner.get(application_id, editor_id) if application_id else editor_id
        )
        ranking_editor_iv_keys.setdefault((attributed_id, profile_id), []).append(key)

    # Archived people are left out of rankings; their data still counts in
    # the profile totals above.
    organization_users = db.execute(
        select(User).where(User.is_active.is_(True)).order_by(User.email)
    ).scalars().all()

    def count_in_range(keys: list[str | None]) -> int:
        return sum(1 for key in keys if key is not None and key in bucket_set)

    per_members: list[dict] = []
    per_supporters: list[dict] = []
    for item in organization_users:
        role = normalize_role(item.role)
        user_profiles = assigned_profiles.get(item.id, set())
        if role == "member":
            applications_in_range = sum(
                count_in_range(ranking_profile_app_keys.get(profile_id, []))
                for profile_id in user_profiles
            )
            interviews_in_range = sum(
                count_in_range(ranking_profile_iv_keys.get(profile_id, []))
                for profile_id in user_profiles
            )
            applications_total = sum(
                len(ranking_profile_app_keys.get(profile_id, []))
                for profile_id in user_profiles
            )
        elif role == "supporter":
            applications_in_range = sum(
                count_in_range(
                    ranking_editor_app_keys.get((item.id, profile_id), [])
                )
                for profile_id in user_profiles
            )
            interviews_in_range = sum(
                count_in_range(
                    ranking_editor_iv_keys.get((item.id, profile_id), [])
                )
                for profile_id in user_profiles
            )
            applications_in_range += count_in_range(
                ranking_editor_app_keys.get((item.id, None), [])
            )
            interviews_in_range += count_in_range(
                ranking_editor_iv_keys.get((item.id, None), [])
            )
            applications_total = applications_in_range
        else:
            continue
        row = {
            "user_id": item.id,
            "id": item.id,
            "name": item.name or "",
            "email": item.email,
            "role": role,
            "avatar_url": item.avatar_url,
            "timezone": item.timezone or "UTC",
            "applications_in_range": applications_in_range,
            "interviews_in_range": interviews_in_range,
            "applications_total": applications_total,
            "last_applied": (
                ranking_last_applied[item.id].isoformat() + "Z"
                if item.id in ranking_last_applied
                else None
            ),
        }
        (per_supporters if role == "supporter" else per_members).append(row)

    # Members: interviews first (the stronger performance signal), applications
    # as tiebreaker. Supporters: applications only — interview attribution for
    # supporters isn't reliable enough yet to rank on (see Ranking's
    # showInterviews=false on the frontend).
    per_members.sort(
        key=lambda row: (
            -row["interviews_in_range"],
            -row["applications_in_range"],
            (row["name"] or row["email"]).lower(),
        )
    )
    per_supporters.sort(
        key=lambda row: (-row["applications_in_range"], (row["name"] or row["email"]).lower())
    )

    return {
        "window": window,
        "granularity": granularity,
        "periods": len(buckets),
        "range_start": buckets[0]["start"] if buckets else None,
        "range_end": buckets[-1]["end"] if buckets else None,
        "buckets": buckets,
        "series": {"applications": app_counts, "interviews": iv_counts},
        "max_applications": max(app_counts) if app_counts else 0,
        "max_interviews": max(iv_counts) if iv_counts else 0,
        "undated": {"applications": undated_apps, "interviews": undated_ivs},
        "totals_in_range": {
            "applications": sum(app_counts),
            "interviews": sum(iv_counts),
        },
        "per_members": per_members,
        "per_supporters": per_supporters,
        "profiles": [{"key": p.key, "name": p.name or p.key} for p in assigned],
        "filter_profile_key": focus.key if focus else None,
        "tz_offset": offset,
    }


@router.get("/colleagues")
def team_colleagues(
    tz_offset: int | None = Query(None),
    timezone: str | None = Query(
        None, description="Client IANA zone (Intl.DateTimeFormat().resolvedOptions().timeZone)."
    ),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Co-assignees on shared profiles: presence, weekly progress, recent apps."""
    offset = _clamp_tz_offset(tz_offset)
    _learn_member_timezone(db, user, timezone)
    local_today = _local_today(offset)
    week_start_local = local_today - timedelta(days=(local_today.weekday() - 5) % 7)
    week_start_utc = week_start_local - timedelta(minutes=offset)
    online_mins = int(ONLINE_WINDOW.total_seconds() // 60)

    empty = {
        "colleagues": [],
        "profiles": [],
        "online_window_minutes": online_mins,
        "tz_offset": offset,
        "week_start": week_start_utc.isoformat() + "Z",
    }

    my_profile_ids = get_user_profile_ids(db, user.id)
    if not my_profile_ids:
        return empty

    profiles = {
        p.id: p
        for p in db.execute(select(Profile).where(Profile.id.in_(my_profile_ids)))
        .scalars()
        .all()
    }

    # Other assignees on my profiles
    assignments = db.execute(
        select(ProfileAssignment).where(
            ProfileAssignment.profile_id.in_(my_profile_ids),
            ProfileAssignment.user_id != user.id,
        )
    ).scalars().all()

    by_user: dict[str, set[str]] = {}
    for a in assignments:
        by_user.setdefault(a.user_id, set()).add(a.profile_id)

    profile_list = [
        {"key": p.key, "name": p.name or p.key}
        for p in sorted(profiles.values(), key=lambda p: p.key)
    ]

    if not by_user:
        return {
            **empty,
            "profiles": profile_list,
        }

    users = {
        u.id: u
        for u in db.execute(select(User).where(User.id.in_(by_user.keys())))
        .scalars()
        .all()
    }

    # Active colleagues only
    active_ids = [
        uid
        for uid in by_user
        if (u := users.get(uid)) is not None and u.is_active
    ]
    if not active_ids:
        return {
            **empty,
            "profiles": profile_list,
        }

    # Latest client last_seen per user
    last_seen_rows = db.execute(
        select(Client.user_id, func.max(Client.last_seen))
        .where(Client.user_id.in_(active_ids))
        .group_by(Client.user_id)
    ).all()
    last_seen_map = {uid: ls for uid, ls in last_seen_rows if uid}

    # Weekly app counts: (user_id, profile_id) → count, then filter to shared set
    # applied_date is already a calendar day (local bid_date baked at UTC noon —
    # see the comment in _bucket_key's caller in team_observability). Comparing
    # it against week_start_local (not the tz-shifted week_start_utc) avoids
    # double-applying the offset and landing rows in the wrong week.
    app_week: dict[str, int] = {uid: 0 for uid in active_ids}
    for uid, pid, cnt in db.execute(
        select(Application.user_id, Application.profile_id, func.count())
        .where(
            Application.user_id.in_(active_ids),
            Application.profile_id.in_(my_profile_ids),
            Application.deleted_at.is_(None),
            Application.applied_date.is_not(None),
            Application.applied_date >= week_start_local,
        )
        .group_by(Application.user_id, Application.profile_id)
    ).all():
        if pid in by_user.get(uid, ()):
            app_week[uid] = app_week.get(uid, 0) + int(cnt)

    iv_week: dict[str, int] = {uid: 0 for uid in active_ids}
    for uid, pid, cnt in db.execute(
        select(Interview.user_id, Interview.profile_id, func.count())
        .where(
            Interview.user_id.in_(active_ids),
            Interview.profile_id.in_(my_profile_ids),
            Interview.deleted_at.is_(None),
            Interview.start_time_utc >= week_start_utc,
        )
        .group_by(Interview.user_id, Interview.profile_id)
    ).all():
        if pid in by_user.get(uid, ()):
            iv_week[uid] = iv_week.get(uid, 0) + int(cnt)

    app_total: dict[str, int] = {uid: 0 for uid in active_ids}
    for uid, pid, cnt in db.execute(
        select(Application.user_id, Application.profile_id, func.count())
        .where(
            Application.user_id.in_(active_ids),
            Application.profile_id.in_(my_profile_ids),
            Application.deleted_at.is_(None),
        )
        .group_by(Application.user_id, Application.profile_id)
    ).all():
        if pid in by_user.get(uid, ()):
            app_total[uid] = app_total.get(uid, 0) + int(cnt)

    iv_total: dict[str, int] = {uid: 0 for uid in active_ids}
    for uid, pid, cnt in db.execute(
        select(Interview.user_id, Interview.profile_id, func.count())
        .where(
            Interview.user_id.in_(active_ids),
            Interview.profile_id.in_(my_profile_ids),
            Interview.deleted_at.is_(None),
        )
        .group_by(Interview.user_id, Interview.profile_id)
    ).all():
        if pid in by_user.get(uid, ()):
            iv_total[uid] = iv_total.get(uid, 0) + int(cnt)

    # Recent applications (bucket top 5 per colleague in Python)
    recent_by_user: dict[str, list[dict]] = {uid: [] for uid in active_ids}
    recent_rows = db.execute(
        select(Application, Profile.key)
        .outerjoin(Profile, Profile.id == Application.profile_id)
        .where(
            Application.user_id.in_(active_ids),
            Application.profile_id.in_(my_profile_ids),
            Application.deleted_at.is_(None),
        )
        .order_by(Application.last_modified.desc())
        .limit(max(50, len(active_ids) * 8))
    ).all()
    for app, pkey in recent_rows:
        uid = app.user_id
        if app.profile_id not in by_user.get(uid, ()):
            continue
        bucket = recent_by_user.get(uid)
        if bucket is None or len(bucket) >= 5:
            continue
        when = app.applied_date or app.last_modified
        bucket.append(
            {
                "id": app.id,
                "company_name": app.company_name,
                "position_title": app.position_title,
                "status": app.status,
                "profile_key": pkey,
                "applied_date": (
                    app.applied_date.isoformat() + "Z" if app.applied_date else None
                ),
                "last_modified": (
                    app.last_modified.isoformat() + "Z" if app.last_modified else None
                ),
                "when": when.isoformat() + "Z" if when else None,
            }
        )

    now = datetime.utcnow()
    colleagues = []
    for uid in active_ids:
        u = users[uid]
        pids = by_user[uid]
        ls = last_seen_map.get(uid)
        shared = [
            {"key": profiles[pid].key, "name": profiles[pid].name or profiles[pid].key}
            for pid in sorted(
                pids, key=lambda i: profiles[i].key if i in profiles else ""
            )
            if pid in profiles
        ]
        colleagues.append(
            {
                "user_id": uid,
                "name": u.name or "",
                "email": u.email,
                "role": normalize_role(u.role),
                "avatar_url": u.avatar_url,
                "timezone": u.timezone or "UTC",
                "shared_profiles": shared,
                "last_seen": ls.isoformat() + "Z" if ls else None,
                "online": bool(ls and ls >= now - ONLINE_WINDOW),
                "progress": {
                    "applications_this_week": int(app_week.get(uid, 0)),
                    "interviews_this_week": int(iv_week.get(uid, 0)),
                },
                "totals": {
                    "applications": int(app_total.get(uid, 0)),
                    "interviews": int(iv_total.get(uid, 0)),
                },
                "recent_applications": recent_by_user.get(uid, []),
            }
        )

    colleagues.sort(key=lambda c: (not c["online"], (c["name"] or c["email"]).lower()))

    return {
        "colleagues": colleagues,
        "profiles": profile_list,
        "online_window_minutes": online_mins,
        "tz_offset": offset,
        "week_start": week_start_utc.isoformat() + "Z",
    }


@router.get("/applications")
def team_applications(
    profile_key: str | None = None,
    contributor_id: str | None = None,
    q: str | None = None,
    applied_from: str | None = None,
    applied_to: str | None = None,
    modified_from: str | None = None,
    modified_to: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Browse applications on profiles assigned to the caller (Observability table)."""
    assigned = _assigned_profiles(db, user.id)
    assigned_ids = {p.id for p in assigned}
    if not assigned_ids:
        return {
            "total": 0,
            "limit": limit,
            "offset": offset,
            "applications": [],
            "profiles": [],
            "filter_profile_key": None,
        }

    focus = _resolve_assigned_profile(db, user.id, profile_key)
    scope_ids = {focus.id} if focus else assigned_ids
    if contributor_id and contributor_id not in _member_supporter_ids(db, user.id):
        raise HTTPException(status_code=403, detail="Supporter is not assigned to your profiles")

    filters = [
        Application.deleted_at.is_(None),
        Application.profile_id.in_(scope_ids),
    ]
    if contributor_id:
        filters.append(Application.user_id == contributor_id)
    search = (q or "").strip()
    if search:
        like = f"%{search}%"
        filters.append(
            or_(
                Application.company_name.ilike(like),
                Application.position_title.ilike(like),
                Application.job_link.ilike(like),
            )
        )
    from_dt = _parse_team_iso_dt(applied_from)
    to_dt = _parse_team_iso_dt(applied_to)
    if from_dt is not None:
        filters.append(Application.applied_date.is_not(None))
        filters.append(Application.applied_date >= from_dt)
    if to_dt is not None:
        filters.append(Application.applied_date.is_not(None))
        filters.append(Application.applied_date <= to_dt)
    modified_from_dt = _parse_team_iso_dt(modified_from)
    modified_to_dt = _parse_team_iso_dt(modified_to)
    if modified_from_dt is not None:
        filters.append(Application.last_modified >= modified_from_dt)
    if modified_to_dt is not None:
        filters.append(Application.last_modified <= modified_to_dt)

    total = db.execute(
        select(func.count()).select_from(Application).where(*filters)
    ).scalar_one()

    rows = db.execute(
        select(Application, User.email, User.name, User.avatar_url, Profile.key, Profile.name)
        .join(User, User.id == Application.user_id)
        .outerjoin(Profile, Profile.id == Application.profile_id)
        .where(*filters)
        .order_by(Application.applied_date.desc(), Application.last_modified.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    app_ids = [a.id for a, *_ in rows]
    iv_counts: dict[str, int] = {}
    if app_ids:
        for app_id, cnt in db.execute(
            select(Interview.application_id, func.count())
            .where(
                Interview.application_id.in_(app_ids),
                Interview.deleted_at.is_(None),
            )
            .group_by(Interview.application_id)
        ).all():
            if app_id:
                iv_counts[app_id] = cnt

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "filter_profile_key": focus.key if focus else None,
        "profiles": [{"key": p.key, "name": p.name or p.key} for p in assigned],
        "applications": [
            {
                "id": a.id,
                "user_id": a.user_id,
                "user_email": email,
                "user_name": name or "",
                "user_avatar_url": avatar_url,
                "company_name": a.company_name,
                "position_title": a.position_title,
                "job_link": a.job_link,
                "status": a.status,
                "profile_key": pkey,
                "profile_name": pname,
                "interview_count": iv_counts.get(a.id, 0),
                "applied_date": a.applied_date.isoformat() + "Z" if a.applied_date else None,
                "last_modified": a.last_modified.isoformat() + "Z" if a.last_modified else None,
            }
            for a, email, name, avatar_url, pkey, pname in rows
        ],
    }
