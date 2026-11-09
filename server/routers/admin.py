"""Admin API: user management, fleet status, per-user data, error/telemetry views."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Literal
from pathlib import Path
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import case, extract, func, or_, select
from sqlalchemy.orm import Session

from .. import auth_sessions, profile_service, user_archive
from ..config import get_settings
from ..database import get_db
from ..geo_location import resolve_profile_state, state_code_to_region
from ..models import (
    Application,
    AuthSession,
    Client,
    ConsentRecord,
    ErrorReport,
    Interview,
    LicenseAudit,
    LicenseSeat,
    LicenseSeatUser,
    MemberAvatarOverride,
    Profile,
    ProfileAssignment,
    Release,
    SignupRequest,
    SyncAudit,
    TelemetryEvent,
    User,
    WorkPlanComment,
    WorkPlanItem,
)
from ..release_versioning import latest_for_channel, parse_version_tuple
from ..security import (
    get_current_admin,
    get_current_session_id,
    hash_password,
    is_true_admin,
    normalize_role,
    ROLES,
)
from .auth import OTP_TTL_MINUTES, OPEN_SIGNUP_STATUSES, generate_otp, hash_otp

router = APIRouter(prefix="/admin/api", tags=["admin"])

from ..presence import ONLINE_WINDOW  # noqa: E402


class UserCreate(BaseModel):
    email: EmailStr
    name: str = ""
    password: str
    role: str = "member"


class UserSetActive(BaseModel):
    is_active: bool


class UserSetRole(BaseModel):
    role: str


class UserSetTimezone(BaseModel):
    timezone: str


class UserSetMember(BaseModel):
    member_id: str | None = None


class UserSetWorkPlanReviewer(BaseModel):
    enabled: bool


class UserSetPassword(BaseModel):
    password: str = Field(min_length=8)


class DuplicateResolve(BaseModel):
    ids: list[str] = []


def _resolve_profile(db: Session, profile_key: str | None) -> Profile | None:
    key = (profile_key or "").strip()
    if not key:
        return None
    return db.execute(select(Profile).where(Profile.key == key)).scalar_one_or_none()


def _parse_iso_dt(value: str | None) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is not None:
            # Store/compare as naive UTC to match Application.applied_date.
            dt = (dt - dt.utcoffset()).replace(tzinfo=None) if dt.utcoffset() else dt.replace(tzinfo=None)
        return dt
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid datetime: {value}") from None


@router.get("/applications")
def list_applications(
    user_id: str | None = None,
    account: str | None = None,
    profile_key: str | None = None,
    q: str | None = None,
    applied_from: str | None = None,
    applied_to: str | None = None,
    include_deleted: bool = False,
    limit: int = 50,
    offset: int = 0,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Browse synced application tracking data filtered by user and/or profile.

    ``include_deleted`` widens the search to soft-deleted rows too - a support
    tool for the case where a debugging session traces a "missing from
    history" report back to a tombstoned record rather than a sync bug.
    """
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    filters = [] if include_deleted else [Application.deleted_at.is_(None)]
    if user_id:
        filters.append(Application.user_id == user_id)
    profile = _resolve_profile(db, profile_key)
    if (profile_key or "").strip() and profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    if profile is not None:
        filters.append(Application.profile_id == profile.id)
    account_filter = (account or "").strip()
    if account_filter and profile is None:
        filters.append(Application.account == account_filter)
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
    from_dt = _parse_iso_dt(applied_from)
    to_dt = _parse_iso_dt(applied_to)
    if from_dt is not None:
        filters.append(Application.applied_date.is_not(None))
        filters.append(Application.applied_date >= from_dt)
    if to_dt is not None:
        filters.append(Application.applied_date.is_not(None))
        filters.append(Application.applied_date <= to_dt)

    total = db.execute(
        select(func.count()).select_from(Application).where(*filters)
    ).scalar_one()

    rows = db.execute(
        select(Application, User.email, User.name, User.avatar_url, Profile.key, Profile.name)
        .join(User, User.id == Application.user_id)
        .outerjoin(Profile, Profile.id == Application.profile_id)
        .where(*filters)
        .order_by(Application.last_modified.desc(), Application.id)
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

    # Distinct accounts for the filter dropdown (scoped to user/profile when provided)
    acct_filters = [Application.deleted_at.is_(None)]
    if user_id:
        acct_filters.append(Application.user_id == user_id)
    if profile is not None:
        acct_filters.append(Application.profile_id == profile.id)
    accounts = sorted(
        {
            (a or "").strip()
            for a in db.execute(
                select(Application.account).where(*acct_filters)
            ).scalars().all()
            if (a or "").strip()
        },
        key=str.lower,
    )

    users = [
        {"id": u.id, "email": u.email, "name": u.name, "avatar_url": u.avatar_url, "role": u.role}
        for u in db.execute(select(User).order_by(User.email)).scalars().all()
    ]
    profiles = [
        {"key": p.key, "name": p.name}
        for p in db.execute(select(Profile).order_by(Profile.key)).scalars().all()
    ]

    def _notes_preview(notes: str | None) -> str | None:
        if not notes:
            return None
        text = notes.strip()
        if len(text) <= 120:
            return text
        return text[:117] + "..."

    return {
        "filter_user_id": user_id,
        "filter_account": account_filter or None,
        "filter_profile_key": profile.key if profile else None,
        "total": total,
        "limit": limit,
        "offset": offset,
        "accounts": accounts,
        "users": users,
        "profiles": profiles,
        "applications": [
            {
                "id": a.id,
                "user_id": a.user_id,
                "user_email": email,
                "user_name": name,
                "user_avatar_url": avatar_url,
                "company_name": a.company_name,
                "position_title": a.position_title,
                "job_link": a.job_link,
                "status": a.status,
                "account": a.account,
                "profile_key": pkey,
                "profile_name": pname,
                "notes_preview": _notes_preview(a.notes),
                "interview_count": iv_counts.get(a.id, 0),
                "applied_date": a.applied_date.isoformat() + "Z" if a.applied_date else None,
                "last_modified": a.last_modified.isoformat() + "Z" if a.last_modified else None,
                "deleted_at": a.deleted_at.isoformat() + "Z" if a.deleted_at else None,
            }
            for a, email, name, avatar_url, pkey, pname in rows
        ],
    }


@router.get("/applications/{application_id}")
def application_detail(
    application_id: str,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Full application record with nested interviews for Observability detail drawer."""
    row = db.execute(
        select(Application, User.email, User.name, User.avatar_url, Profile.key, Profile.name)
        .join(User, User.id == Application.user_id)
        .outerjoin(Profile, Profile.id == Application.profile_id)
        .where(Application.id == application_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Application not found")
    a, email, name, avatar_url, pkey, pname = row

    interviews = db.execute(
        select(Interview)
        .where(
            Interview.application_id == application_id,
            Interview.deleted_at.is_(None),
        )
        .order_by(Interview.start_time_utc.desc())
    ).scalars().all()

    return {
        "application": {
            "id": a.id,
            "user_id": a.user_id,
            "user_email": email,
            "user_name": name,
            "user_avatar_url": avatar_url,
            "company_name": a.company_name,
            "position_title": a.position_title,
            "job_link": a.job_link,
            "status": a.status,
            "account": a.account,
            "profile_key": pkey,
            "profile_name": pname,
            "notes": a.notes,
            "applied_date": a.applied_date.isoformat() + "Z" if a.applied_date else None,
            "last_modified": a.last_modified.isoformat() + "Z" if a.last_modified else None,
            "deleted_at": a.deleted_at.isoformat() + "Z" if a.deleted_at else None,
            "resume_file_path": a.resume_file_path,
            "resume_version": a.resume_version,
            "cover_letter_file_path": a.cover_letter_file_path,
        },
        "interviews": [
            {
                "id": iv.id,
                "title": iv.title,
                "start_time_utc": iv.start_time_utc.isoformat() + "Z" if iv.start_time_utc else None,
                "end_time_utc": iv.end_time_utc.isoformat() + "Z" if iv.end_time_utc else None,
                # Needed to render the time. Despite the field name, start/end are
                # naive wall clock in *this* zone, not UTC instants; the web reads
                # them with interviewDate(value, timezone). Omitting it makes the
                # UI fall back to new Date(), which treats the trailing Z as real
                # UTC and shifts the time by the viewer's offset.
                "timezone": iv.timezone,
                "status": iv.status,
                "interview_type": iv.interview_type,
                "notes": iv.notes,
                "location": iv.location,
                "description": iv.description,
            }
            for iv in interviews
        ],
    }


def _clamp_tz_offset(tz_offset: int | None) -> int:
    """Minutes east of UTC (JS: ``-new Date().getTimezoneOffset()``)."""
    if tz_offset is None:
        return 0
    return max(-14 * 60, min(14 * 60, int(tz_offset)))


def _learn_manager_timezone(db: Session, user: User, timezone: str | None) -> None:
    """Opportunistically store a manager's real IANA zone from her own browser.

    Mirrors ``_learn_member_timezone`` in team.py: managers use the Staff
    dashboard/Performance view for themselves, so this fires on essentially
    every login. Scoped to "manager" only — true admins and supporters keep
    an admin-set ``User.timezone`` (supporters' stays authoritative for the
    Colleagues tab, since their web usage is too sparse to self-correct).
    """
    if not timezone or normalize_role(user.role) != "manager":
        return
    if timezone == user.timezone or timezone not in available_timezones():
        return
    user.timezone = timezone
    db.commit()


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
    """Stable bucket key for a datetime (day / Saturday-start week / month)."""
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
    """Build {key, label, start, end} for a bucket key.

    start/end are calendar-day markers (not real UTC instants). Clients must
    render them with UTC calendar components so local TZ does not shift the day.
    """
    if granularity == "day":
        start = datetime.strptime(key, "%Y-%m-%d")
        end = start.replace(hour=23, minute=59, second=59)
        return {
            "key": key,
            "label": start.strftime("%Y-%m-%d"),
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
    # week key: Saturday start date, YYYY-MM-DD
    start = datetime.strptime(key, "%Y-%m-%d")
    end = start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    label = f"{_human_day(start)}–{_human_day(end)}"
    if start.year != end.year:
        label = f"{start.strftime('%b %d %Y')}–{end.strftime('%b %d %Y')}"
    elif start.month != end.month:
        label = f"{_human_day(start)}–{_human_day(end)}"
    return {
        "key": key,
        "label": label,
        "start": start.isoformat() + "Z",
        "end": end.isoformat() + "Z",
    }


def _bucket_range(granularity: str, periods: int, tz_offset: int = 0) -> list[dict]:
    """Chronological list of the most recent ``periods`` bucket metadata dicts."""
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


@router.get("/tracking/metrics")
def tracking_metrics(
    granularity: str = "week",
    user_id: str | None = None,
    account: str | None = None,
    profile_key: str | None = None,
    periods: int = 12,
    period_offset: int = Query(
        0,
        ge=0,
        le=90,
        description="Skip this many newest buckets (0=current period, 1=previous, …).",
    ),
    tz_offset: int | None = Query(
        None,
        description="Client offset minutes east of UTC (−getTimezoneOffset()). "
        "Used so day buckets match desktop tracking local dates.",
    ),
    timezone: str | None = Query(
        None, description="Client IANA zone (Intl.DateTimeFormat().resolvedOptions().timeZone)."
    ),
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Per-user work-performance metrics: application/interview volume per day/week/month.

    Applications are bucketed by ``applied_date``, interviews by ``start_time_utc``.
    Records whose bucket date is null are counted in totals but reported as
    ``undated`` rather than placed in a time bucket. Bucketing is done in Python so
    the query is portable across SQLite (dev) and Postgres (prod).
    Dates are bucketed in the client's local timezone (same as desktop bid_date).
    """
    _learn_manager_timezone(db, admin, timezone)
    granularity = granularity if granularity in ("day", "week", "month") else "week"
    max_periods = 90 if granularity == "day" else 52
    periods = max(1, min(periods, max_periods))
    period_offset = max(0, min(int(period_offset or 0), max_periods))
    offset = _clamp_tz_offset(tz_offset)
    buckets = _bucket_range(granularity, periods + period_offset, offset)[:periods]
    bucket_keys = [b["key"] for b in buckets]
    bucket_set = set(bucket_keys)
    account_filter = (account or "").strip()
    profile = _resolve_profile(db, profile_key)
    if (profile_key or "").strip() and profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    profile_id = profile.id if profile else None

    app_filters = [Application.deleted_at.is_(None)]
    # Person filter is applied after load (members = assigned-profile scope).
    if profile_id:
        app_filters.append(Application.profile_id == profile_id)
    elif account_filter:
        app_filters.append(Application.account == account_filter)
    app_rows = db.execute(
        select(
            Application.id, Application.user_id, Application.profile_id, Application.applied_date
        ).where(*app_filters)
    ).all()

    iv_filters = [Interview.deleted_at.is_(None)]
    if profile_id:
        iv_filters.append(Interview.profile_id == profile_id)
    # Legacy account filter applies to apps only (interviews have no account column).
    iv_rows = db.execute(
        select(
            Interview.user_id,
            Interview.profile_id,
            Interview.start_time_utc,
            Interview.application_id,
        ).where(*iv_filters)
    ).all()

    app_series = {k: 0 for k in bucket_keys}
    iv_series = {k: 0 for k in bucket_keys}
    undated_apps = 0
    undated_ivs = 0

    # profile_id -> list of (bucket_key|None) for apps / interviews in range
    profile_app_keys: dict[str, list[str | None]] = {}
    profile_iv_keys: dict[str, list[str | None]] = {}
    # supporter/editor attribution: (user_id, profile_id) -> keys
    editor_app_keys: dict[tuple[str, str | None], list[str | None]] = {}
    editor_iv_keys: dict[tuple[str, str | None], list[str | None]] = {}
    last_applied: dict[str, datetime] = {}
    # application_id -> submitter user_id, so an interview can be credited to
    # whoever submitted the application it came from (rather than whoever
    # happened to create the interview record) — this is what makes a
    # supporter's interview count reflect their own submitted applications.
    app_owner: dict[str, str] = {}

    for app_id, uid, pid, applied in app_rows:
        app_owner[app_id] = uid
        if applied is None:
            undated_apps += 1
            key = None
        else:
            if uid not in last_applied or applied > last_applied[uid]:
                last_applied[uid] = applied
            # applied_date is already a calendar day (desktop encodes the local
            # bid_date at UTC noon), so shifting it again double-applies the
            # offset and past ±12h buckets it into the wrong day. Interviews,
            # being real UTC instants, still need the shift below.
            key = _bucket_key(applied, granularity)
            if key in bucket_set:
                app_series[key] += 1
            else:
                key = None  # outside selected window
        if pid:
            profile_app_keys.setdefault(pid, []).append(key)
        editor_app_keys.setdefault((uid, pid), []).append(key)

    for uid, pid, start, application_id in iv_rows:
        if start is None:
            undated_ivs += 1
            key = None
        else:
            key = _bucket_key(_to_local(start, offset), granularity)
            if key in bucket_set:
                iv_series[key] += 1
            else:
                key = None
        if pid:
            profile_iv_keys.setdefault(pid, []).append(key)
        attributed_uid = app_owner.get(application_id, uid) if application_id else uid
        editor_iv_keys.setdefault((attributed_uid, pid), []).append(key)

    users_all = db.execute(select(User).order_by(User.email)).scalars().all()

    assignments = db.execute(
        select(ProfileAssignment.user_id, ProfileAssignment.profile_id)
    ).all()
    assigned_profiles: dict[str, set[str]] = {}
    for uid, pid in assignments:
        assigned_profiles.setdefault(uid, set()).add(pid)

    def _count_in_range(keys: list[str | None]) -> int:
        return sum(1 for k in keys if k is not None and k in bucket_set)

    def _member_row(u: User) -> dict:
        pids = assigned_profiles.get(u.id, set())
        if profile_id:
            pids = pids & {profile_id}
        app_n = 0
        iv_n = 0
        total_apps = 0
        for pid in pids:
            keys = profile_app_keys.get(pid, [])
            app_n += _count_in_range(keys)
            total_apps += len(keys)
            iv_n += _count_in_range(profile_iv_keys.get(pid, []))
        la = last_applied.get(u.id)
        return {
            "user_id": u.id,
            "email": u.email,
            "name": u.name,
            "role": u.role,
            "avatar_url": u.avatar_url,
            "timezone": u.timezone or "UTC",
            "applications_in_range": app_n,
            "interviews_in_range": iv_n,
            "applications_total": total_apps,
            "last_applied": la.isoformat() + "Z" if la else None,
        }

    def _supporter_row(u: User) -> dict:
        pids = assigned_profiles.get(u.id, set())
        if profile_id:
            pids = pids & {profile_id}
        app_n = 0
        iv_n = 0
        for pid in pids:
            app_n += _count_in_range(editor_app_keys.get((u.id, pid), []))
            iv_n += _count_in_range(editor_iv_keys.get((u.id, pid), []))
        if not profile_id:
            app_n += _count_in_range(editor_app_keys.get((u.id, None), []))
            iv_n += _count_in_range(editor_iv_keys.get((u.id, None), []))
        la = last_applied.get(u.id)
        return {
            "user_id": u.id,
            "email": u.email,
            "name": u.name,
            "role": u.role,
            "avatar_url": u.avatar_url,
            "timezone": u.timezone or "UTC",
            "applications_in_range": app_n,
            "interviews_in_range": iv_n,
            "applications_total": app_n,
            "last_applied": la.isoformat() + "Z" if la else None,
        }

    member_roles = {"member", "manager"}
    per_members: list[dict] = []
    per_supporters: list[dict] = []
    focus_user: User | None = None
    for u in users_all:
        role = (u.role or "").strip().lower()
        if user_id and u.id != user_id:
            continue
        if user_id and u.id == user_id:
            focus_user = u
        # Ranking tables exclude deactivated users unless one is explicitly
        # selected via the person filter (drill-down should still work).
        if not u.is_active and not user_id:
            continue
        if role == "member":
            per_members.append(_member_row(u))
        elif role == "supporter":
            per_supporters.append(_supporter_row(u))
        # Managers are omitted from the Members table; still filterable via person dropdown.

    # Members: interviews first (the stronger performance signal), applications
    # as tiebreaker. Supporters: applications only — interview attribution for
    # supporters isn't reliable enough yet to rank on (see Ranking's
    # showInterviews=false on the frontend).
    per_members.sort(
        key=lambda r: (
            -r["interviews_in_range"],
            -r["applications_in_range"],
            (r["name"] or r["email"]).lower(),
        )
    )
    per_supporters.sort(
        key=lambda r: (-r["applications_in_range"], (r["name"] or r["email"]).lower())
    )
    per_user_list = per_members + per_supporters

    # Person filter: rebuild series from role-aware scope (member=assigned profiles).
    if focus_user is not None:
        role = (focus_user.role or "").strip().lower()
        app_series = {k: 0 for k in bucket_keys}
        iv_series = {k: 0 for k in bucket_keys}
        undated_apps = 0
        undated_ivs = 0
        if role in member_roles:
            pids = assigned_profiles.get(focus_user.id, set())
            if profile_id:
                pids = pids & {profile_id}
            for pid in pids:
                for key in profile_app_keys.get(pid, []):
                    if key is None:
                        undated_apps += 1
                    elif key in bucket_set:
                        app_series[key] += 1
                for key in profile_iv_keys.get(pid, []):
                    if key is None:
                        undated_ivs += 1
                    elif key in bucket_set:
                        iv_series[key] += 1
        else:
            pids = assigned_profiles.get(focus_user.id, set())
            if profile_id:
                pids = pids & {profile_id}
            scope_pids: list[str | None] = list(pids)
            if not profile_id:
                scope_pids.append(None)
            for pid in scope_pids:
                for key in editor_app_keys.get((focus_user.id, pid), []):
                    if key is None:
                        undated_apps += 1
                    elif key in bucket_set:
                        app_series[key] += 1
                for key in editor_iv_keys.get((focus_user.id, pid), []):
                    if key is None:
                        undated_ivs += 1
                    elif key in bucket_set:
                        iv_series[key] += 1

    acct_filters = [Application.deleted_at.is_(None)]
    if user_id:
        acct_filters.append(Application.user_id == user_id)
    if profile_id:
        acct_filters.append(Application.profile_id == profile_id)
    accounts = sorted(
        {
            (a or "").strip()
            for a in db.execute(
                select(Application.account).where(*acct_filters)
            ).scalars().all()
            if (a or "").strip()
        },
        key=str.lower,
    )

    profiles = [
        {"key": p.key, "name": p.name}
        for p in db.execute(select(Profile).order_by(Profile.key)).scalars().all()
    ]

    app_counts = [app_series[k] for k in bucket_keys]
    iv_counts = [iv_series[k] for k in bucket_keys]
    range_start = buckets[0]["start"] if buckets else None
    range_end = buckets[-1]["end"] if buckets else None

    return {
        "granularity": granularity,
        "periods": periods,
        "period_offset": period_offset,
        "tz_offset": offset,
        "range_start": range_start,
        "range_end": range_end,
        "buckets": buckets,
        "series": {
            "applications": app_counts,
            "interviews": iv_counts,
        },
        "max_applications": max(app_counts) if app_counts else 0,
        "max_interviews": max(iv_counts) if iv_counts else 0,
        "undated": {"applications": undated_apps, "interviews": undated_ivs},
        "totals_in_range": {
            "applications": sum(app_counts),
            "interviews": sum(iv_counts),
            "active_users": sum(
                1 for r in per_user_list if r["applications_in_range"] > 0
            ),
        },
        "per_user": per_user_list,
        "per_members": per_members,
        "per_supporters": per_supporters,
        "users": [
            {
                "id": u.id,
                "email": u.email,
                "name": u.name,
                "role": u.role,
                "avatar_url": u.avatar_url,
            }
            for u in users_all
        ],
        "accounts": accounts,
        "profiles": profiles,
        "filter_user_id": user_id,
        "filter_account": account_filter or None,
        "filter_profile_key": profile.key if profile else None,
    }


@router.get("/tracking/duplicates")
def find_duplicates(
    user_id: str | None = None,
    account: str | None = None,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Detect logical duplicate applications (same user + company + position + job link).

    Records without a job link are not considered (the chosen duplicate key requires
    a link). Within each group the newest by ``last_modified`` is marked ``keep``.
    """
    filters = [Application.deleted_at.is_(None)]
    if user_id:
        filters.append(Application.user_id == user_id)
    account_filter = (account or "").strip()
    if account_filter:
        filters.append(Application.account == account_filter)

    rows = db.execute(
        select(Application, User.email)
        .join(User, User.id == Application.user_id)
        .where(*filters)
    ).all()

    groups: dict[tuple, list] = {}
    for app, email in rows:
        link = (app.job_link or "").strip().lower()
        if not link:
            continue
        key = (
            app.user_id,
            (app.company_name or "").strip().lower(),
            (app.position_title or "").strip().lower(),
            link,
        )
        groups.setdefault(key, []).append((app, email))

    def _rec(a: Application) -> dict:
        return {
            "id": a.id,
            "account": a.account,
            "applied_date": a.applied_date.isoformat() + "Z" if a.applied_date else None,
            "last_modified": a.last_modified.isoformat() + "Z" if a.last_modified else None,
        }

    out_groups = []
    total_removable = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(
            key=lambda m: (m[0].last_modified or datetime.min), reverse=True
        )
        keep_app, keep_email = members[0]
        remove = [a for a, _ in members[1:]]
        total_removable += len(remove)
        out_groups.append(
            {
                "user_id": keep_app.user_id,
                "user_email": keep_email,
                "company_name": keep_app.company_name,
                "position_title": keep_app.position_title,
                "job_link": keep_app.job_link,
                "keep": _rec(keep_app),
                "remove": [_rec(a) for a in remove],
            }
        )

    out_groups.sort(key=lambda g: len(g["remove"]), reverse=True)
    return {
        "groups": out_groups,
        "total_groups": len(out_groups),
        "total_removable": total_removable,
    }


@router.post("/tracking/duplicates/resolve")
def resolve_duplicates(
    payload: DuplicateResolve,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Soft-delete the confirmed duplicate applications so clients tombstone them on pull."""
    ids = [i for i in (payload.ids or []) if i]
    if not ids:
        return {"ok": True, "deleted": 0}
    now = datetime.utcnow()
    deleted = 0
    apps = db.execute(
        select(Application).where(Application.id.in_(ids))
    ).scalars().all()
    for app in apps:
        if app.deleted_at is not None:
            continue
        app.deleted_at = now
        app.last_modified = now
        deleted += 1
    db.commit()
    return {"ok": True, "deleted": deleted}


@router.post("/applications/restore")
def restore_applications(
    payload: DuplicateResolve,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Undo a soft-delete so the row reappears in its profile's history on the next pull.

    For the case a debugging session traces a "missing from history" report
    to a record that was actually tombstoned (accidentally, or as a stale
    duplicate that turned out to still matter) rather than a sync defect.
    """
    ids = [i for i in (payload.ids or []) if i]
    if not ids:
        return {"ok": True, "restored": 0}
    now = datetime.utcnow()
    restored = 0
    apps = db.execute(
        select(Application).where(Application.id.in_(ids))
    ).scalars().all()
    for app in apps:
        if app.deleted_at is None:
            continue
        app.deleted_at = None
        app.last_modified = now
        restored += 1
    db.commit()
    return {"ok": True, "restored": restored}


@router.get("/interviews")
def list_interviews(
    user_id: str | None = None,
    profile_key: str | None = None,
    q: str | None = None,
    include_deleted: bool = False,
    limit: int = 50,
    offset: int = 0,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Browse interview records, mirroring ``list_applications``.

    ``q`` matches the interview's own fields (title, interviewer) as well as
    the linked application's company/position, since interviews are usually
    looked up by company name rather than interviewer name.
    """
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    filters = [] if include_deleted else [Interview.deleted_at.is_(None)]
    if user_id:
        filters.append(Interview.user_id == user_id)
    profile = _resolve_profile(db, profile_key)
    if (profile_key or "").strip() and profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    if profile is not None:
        filters.append(Interview.profile_id == profile.id)
    search = (q or "").strip()
    if search:
        like = f"%{search}%"
        filters.append(
            or_(
                Interview.title.ilike(like),
                Interview.interviewer_name.ilike(like),
                Interview.interviewer_email.ilike(like),
                Interview.notes.ilike(like),
                Application.company_name.ilike(like),
                Application.position_title.ilike(like),
            )
        )

    total = db.execute(
        select(func.count())
        .select_from(Interview)
        .outerjoin(Application, Application.id == Interview.application_id)
        .where(*filters)
    ).scalar_one()

    rows = db.execute(
        select(Interview, User.email, User.name, User.avatar_url, Profile.key, Profile.name,
               Application.company_name, Application.position_title)
        .join(User, User.id == Interview.user_id)
        .outerjoin(Profile, Profile.id == Interview.profile_id)
        .outerjoin(Application, Application.id == Interview.application_id)
        .where(*filters)
        .order_by(Interview.last_modified.desc(), Interview.id)
        .offset(offset)
        .limit(limit)
    ).all()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "interviews": [
            {
                "id": iv.id,
                "user_id": iv.user_id,
                "user_email": email,
                "user_name": name,
                "user_avatar_url": avatar_url,
                "profile_key": pkey,
                "profile_name": pname,
                "application_id": iv.application_id,
                "company_name": company_name,
                "position_title": position_title,
                "title": iv.title,
                "interviewer_name": iv.interviewer_name,
                "interviewer_email": iv.interviewer_email,
                "interview_type": iv.interview_type,
                "status": iv.status,
                "start_time_utc": iv.start_time_utc.isoformat() + "Z" if iv.start_time_utc else None,
                "last_modified": iv.last_modified.isoformat() + "Z" if iv.last_modified else None,
                "deleted_at": iv.deleted_at.isoformat() + "Z" if iv.deleted_at else None,
            }
            for iv, email, name, avatar_url, pkey, pname, company_name, position_title in rows
        ],
    }


@router.post("/interviews/restore")
def restore_interviews(
    payload: DuplicateResolve,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Undo a soft-delete on interviews (see ``restore_applications``)."""
    ids = [i for i in (payload.ids or []) if i]
    if not ids:
        return {"ok": True, "restored": 0}
    now = datetime.utcnow()
    restored = 0
    interviews = db.execute(
        select(Interview).where(Interview.id.in_(ids))
    ).scalars().all()
    for iv in interviews:
        if iv.deleted_at is None:
            continue
        iv.deleted_at = None
        iv.last_modified = now
        restored += 1
    db.commit()
    return {"ok": True, "restored": restored}


@router.get("/sync/reconcile")
def reconcile_cross_account_duplicates(
    profile_key: str | None = None,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Find cross-account duplicate applications within a shared profile.

    The pre-redesign per-user behavior let two accounts on the same profile each
    insert their own copy of the same application. Unlike ``/tracking/duplicates``
    (which groups within a single user), this groups by ``profile_id`` across
    accounts so those legacy duplicate sets surface. Newest by ``last_modified``
    is kept; the rest are offered for soft-deletion via
    ``/tracking/duplicates/resolve``.
    """
    filters = [
        Application.deleted_at.is_(None),
        Application.profile_id.is_not(None),
    ]
    if (profile_key or "").strip():
        prof = db.execute(
            select(Profile).where(Profile.key == profile_key.strip())
        ).scalar_one_or_none()
        if prof is None:
            raise HTTPException(status_code=404, detail="Profile not found")
        filters.append(Application.profile_id == prof.id)

    rows = db.execute(
        select(Application, User.email)
        .join(User, User.id == Application.user_id)
        .where(*filters)
    ).all()

    profile_names = {
        p.id: (p.name or p.key)
        for p in db.execute(select(Profile)).scalars().all()
    }

    groups: dict[tuple, list] = {}
    for app, email in rows:
        link = (app.job_link or "").strip().lower()
        if not link:
            continue
        key = (
            app.profile_id,
            (app.company_name or "").strip().lower(),
            (app.position_title or "").strip().lower(),
            link,
        )
        groups.setdefault(key, []).append((app, email))

    def _rec(a: Application, email: str) -> dict:
        return {
            "id": a.id,
            "account": a.account,
            "editor_email": email,
            "applied_date": a.applied_date.isoformat() + "Z" if a.applied_date else None,
            "last_modified": a.last_modified.isoformat() + "Z" if a.last_modified else None,
        }

    out_groups = []
    total_removable = 0
    for (profile_id, *_), members in groups.items():
        # Only cross-account sets: more than one distinct editor.
        editors = {m[0].user_id for m in members}
        if len(members) < 2 or len(editors) < 2:
            continue
        members.sort(key=lambda m: (m[0].last_modified or datetime.min), reverse=True)
        keep_app, keep_email = members[0]
        remove = members[1:]
        total_removable += len(remove)
        out_groups.append(
            {
                "profile_id": profile_id,
                "profile_name": profile_names.get(profile_id),
                "company_name": keep_app.company_name,
                "position_title": keep_app.position_title,
                "job_link": keep_app.job_link,
                "keep": _rec(keep_app, keep_email),
                "remove": [_rec(a, em) for a, em in remove],
            }
        )

    out_groups.sort(key=lambda g: len(g["remove"]), reverse=True)
    return {
        "filter_profile_key": (profile_key or "").strip() or None,
        "groups": out_groups,
        "total_groups": len(out_groups),
        "total_removable": total_removable,
    }


@router.post("/sync/repair-account-scope")
def repair_account_scope(
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Recover personal rows onto real profiles (create/assign as needed).

    Personal ``profile_id IS NULL`` data was a client bug. This undeletes those
    rows, matches ``account`` to an existing profile or creates one, assigns it
    to the owning member, and clears ``deleted_at``.
    """
    from ..migrations import _recover_personal_into_profiles

    personal_before = db.execute(
        select(func.count())
        .select_from(Application)
        .where(Application.profile_id.is_(None))
    ).scalar_one()

    _recover_personal_into_profiles(db.connection())
    db.commit()

    personal_after = db.execute(
        select(func.count())
        .select_from(Application)
        .where(Application.profile_id.is_(None))
    ).scalar_one()
    return {
        "personal_before": int(personal_before),
        "personal_remaining": int(personal_after),
        "recovered": max(0, int(personal_before) - int(personal_after)),
    }


@router.get("/overview")
def overview(
    _admin: User = Depends(get_current_admin), db: Session = Depends(get_db)
) -> dict:
    now = datetime.utcnow()
    total_users = db.execute(select(func.count()).select_from(User)).scalar_one()
    total_clients = db.execute(select(func.count()).select_from(Client)).scalar_one()
    online = db.execute(
        select(func.count())
        .select_from(Client)
        .where(Client.last_seen >= now - ONLINE_WINDOW)
    ).scalar_one()
    total_apps = db.execute(select(func.count()).select_from(Application)).scalar_one()
    total_interviews = db.execute(select(func.count()).select_from(Interview)).scalar_one()
    errors_24h = db.execute(
        select(func.count())
        .select_from(ErrorReport)
        .where(ErrorReport.created_at >= now - timedelta(hours=24))
    ).scalar_one()

    users_by_role = {r: 0 for r in ("member", "manager", "supporter", "admin")}
    for raw_role, count in db.execute(
        select(User.role, func.count()).where(User.is_active.is_(True)).group_by(User.role)
    ).all():
        key = normalize_role(raw_role)
        users_by_role[key] = users_by_role.get(key, 0) + int(count)

    users_active = db.execute(
        select(func.count()).select_from(User).where(User.is_active.is_(True))
    ).scalar_one()
    users_online = db.execute(
        select(func.count(func.distinct(Client.user_id))).where(
            Client.user_id.is_not(None),
            Client.last_seen >= now - ONLINE_WINDOW,
        )
    ).scalar_one()

    return {
        "users": total_users,
        "clients": total_clients,
        "clients_online": online,
        "applications": total_apps,
        "interviews": total_interviews,
        "errors_24h": errors_24h,
        "server_time": now.isoformat() + "Z",
        "users_by_role": users_by_role,
        "users_active": users_active,
        "users_inactive": max(0, int(total_users) - int(users_active)),
        "users_online": users_online,
    }


@router.get("/profiles/geo")
def profiles_geo(
    _admin: User = Depends(get_current_admin), db: Session = Depends(get_db)
) -> dict:
    """Aggregate profiles into US states for the Stage dashboard choropleth.

    Parses free-text ``location`` / ``university_location`` into two-letter
    codes. Response ``states`` use jsVectorMap region ids (``US-CA``, …).
    """
    rows = db.execute(select(Profile).order_by(Profile.key)).scalars().all()
    by_code: dict[str, dict] = {}
    unmapped: list[dict] = []

    for p in rows:
        code = resolve_profile_state(p.location, p.university_location)
        display = (p.name or "").strip() or p.key
        loc_raw = (p.location or "").strip() or (p.university_location or "").strip()
        if not code:
            unmapped.append(
                {
                    "key": p.key,
                    "name": display,
                    "location": loc_raw,
                }
            )
            continue
        region = state_code_to_region(code)
        bucket = by_code.setdefault(
            code,
            {
                "code": code,
                "region": region,
                "count": 0,
                "profiles": [],
            },
        )
        bucket["count"] += 1
        bucket["profiles"].append(display)

    states = sorted(by_code.values(), key=lambda s: (-s["count"], s["code"]))
    for s in states:
        s["profiles"] = sorted(s["profiles"], key=lambda n: n.lower())

    return {
        "states": states,
        "unmapped": unmapped,
        "total_profiles": len(rows),
        "mapped": sum(s["count"] for s in states),
        "unmapped_count": len(unmapped),
    }


@router.get("/fleet")
def fleet(
    _admin: User = Depends(get_current_admin), db: Session = Depends(get_db)
) -> list[dict]:
    now = datetime.utcnow()
    rows = db.execute(select(Client).order_by(Client.last_seen.desc())).scalars().all()
    result = []
    for c in rows:
        user = db.get(User, c.user_id) if c.user_id else None
        result.append(
            {
                "device_id": c.device_id,
                "user_email": user.email if user else None,
                "app_version": c.app_version,
                "os_name": c.os_name,
                "hostname": c.hostname,
                "last_seen": c.last_seen.isoformat() + "Z",
                "online": c.last_seen >= now - ONLINE_WINDOW,
            }
        )
    return result


def _colleague_preview(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name or "",
        "role": normalize_role(user.role),
        "avatar_url": user.avatar_url,
        "is_active": bool(user.is_active),
    }


def _is_visible_colleague(viewer_role: str, other_role: str) -> bool:
    """Supporters only see member co-assignees; members (and others) see all non-admins."""
    other = normalize_role(other_role)
    if other == "admin":
        return False
    if normalize_role(viewer_role) == "supporter":
        return other == "member"
    return True


@router.get("/users")
def list_users(
    include_archived: bool = False,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    """List users with lightweight colleague summaries (shared ProfileAssignment).

    Archived users are left out unless ``include_archived`` (the Users page);
    every other page uses this list for people pickers.
    """
    all_rows = db.execute(select(User).order_by(User.created_at.desc())).scalars().all()
    users_by_id = {u.id: u for u in all_rows}
    rows = [u for u in all_rows if include_archived or u.is_active]
    team_size: dict[str, int] = {}
    for u in all_rows:
        if u.member_id and u.is_active:
            team_size[u.member_id] = team_size.get(u.member_id, 0) + 1

    assignments = db.execute(select(ProfileAssignment)).scalars().all()
    profiles = {
        p.id: p
        for p in db.execute(select(Profile)).scalars().all()
    }

    # profile_id -> assignee user_ids; user_id -> profile_ids
    assignees_by_profile: dict[str, list[str]] = {}
    profiles_by_user: dict[str, list[str]] = {}
    for a in assignments:
        assignees_by_profile.setdefault(a.profile_id, []).append(a.user_id)
        profiles_by_user.setdefault(a.user_id, []).append(a.profile_id)

    out: list[dict] = []
    for u in rows:
        pids = profiles_by_user.get(u.id, [])
        profile_keys = sorted(
            {
                profiles[pid].key
                for pid in pids
                if pid in profiles and (profiles[pid].key or "").strip()
            }
        )
        colleague_ids: set[str] = set()
        for pid in pids:
            for other_id in assignees_by_profile.get(pid, []):
                if other_id == u.id:
                    continue
                other = users_by_id.get(other_id)
                if other is None or not other.is_active:
                    continue
                if not _is_visible_colleague(u.role, other.role):
                    continue
                colleague_ids.add(other_id)

        colleagues = sorted(
            (_colleague_preview(users_by_id[cid]) for cid in colleague_ids),
            key=lambda c: (c["name"] or c["email"]).lower(),
        )
        out.append(
            {
                "id": u.id,
                "email": u.email,
                "name": u.name,
                "role": u.role,
                "is_active": u.is_active,
                "archived_at": u.archived_at.isoformat() + "Z" if u.archived_at else None,
                "team_size": team_size.get(u.id, 0),
                "avatar_url": u.avatar_url,
                "timezone": u.timezone,
                "created_at": u.created_at.isoformat() + "Z" if u.created_at else None,
                "profile_keys": profile_keys,
                "profile_count": len(profile_keys),
                "member_id": u.member_id,
                "can_review_work_plans": bool(u.can_review_work_plans),
                "member_name": (
                    (users_by_id[u.member_id].name or users_by_id[u.member_id].email)
                    if u.member_id in users_by_id
                    else None
                ),
                "colleague_count": len(colleagues),
                "colleagues": colleagues[:5],
            }
        )
    return out


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Escape hatch for creating admin accounts. Normal users sign up via the app."""
    email = str(payload.email).strip().lower()
    existing = db.execute(
        select(User).where(User.email == email)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Email already registered")
    role = normalize_role(payload.role, default="member")
    if role == "admin" and not is_true_admin(admin):
        raise HTTPException(
            status_code=403, detail="Only admins can create admin accounts"
        )
    user = User(
        email=email,
        name=payload.name,
        password_hash=hash_password(payload.password),
        role=role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "email": user.email, "role": user.role}


@router.post("/users/{user_id}/set-active")
def set_user_active(
    user_id: str,
    payload: UserSetActive,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Legacy toggle; same as archive (supporters left without a member) / restore."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.is_active:
        user_archive.restore_user(db, admin, user)
    else:
        user_archive.archive_user(db, admin, user)
    db.commit()
    return {"id": user.id, "email": user.email, "is_active": user.is_active}


class UserArchive(BaseModel):
    # What happens to an archived member's supporters.
    supporters: Literal["unlink", "move", "archive"] = "unlink"
    move_to: str | None = None


@router.post("/users/{user_id}/archive")
def archive_user(
    user_id: str,
    payload: UserArchive,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Archive: can't sign in, seats freed, hidden everywhere; data kept."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    result = user_archive.archive_user(db, admin, user, payload.supporters, payload.move_to)
    db.commit()
    return {"id": user.id, **result}


@router.post("/users/{user_id}/restore")
def restore_user(
    user_id: str,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    result = user_archive.restore_user(db, admin, user)
    db.commit()
    return {"id": user.id, **result}


@router.post("/users/{user_id}/set-role")
def set_user_role(
    user_id: str,
    payload: UserSetRole,
    admin: User = Depends(get_current_admin),
    session_id: str | None = Depends(get_current_session_id),
    db: Session = Depends(get_db),
) -> dict:
    role = normalize_role(payload.role, default="")
    if role not in ROLES:
        raise HTTPException(
            status_code=400,
            detail="role must be admin, manager, supporter, or member",
        )
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    # Only true admins may grant/revoke admin, or change another admin's role.
    if (
        role == "admin" or normalize_role(user.role) == "admin"
    ) and not is_true_admin(admin):
        raise HTTPException(
            status_code=403, detail="Only admins can change admin roles"
        )
    if user_id == admin.id and role != normalize_role(admin.role):
        if normalize_role(admin.role) == "admin" and role != "admin":
            raise HTTPException(
                status_code=400, detail="Cannot remove your own admin role"
            )
        if normalize_role(admin.role) == "manager" and role not in (
            "manager",
            "admin",
        ):
            raise HTTPException(
                status_code=400, detail="Cannot remove your own manager role"
            )
    old_role = normalize_role(user.role)
    profile_service.on_role_change(db, user, old_role, role)
    user.role = role
    if role != old_role:
        # Their next sign-in gets the new role's session limits.
        auth_sessions.end_user_sessions(
            db, user.id, reason="role_changed", keep=session_id if user.id == admin.id else None
        )
    db.commit()
    return {"id": user.id, "email": user.email, "role": user.role}


@router.post("/users/{user_id}/set-member")
def set_user_member(
    user_id: str,
    payload: UserSetMember,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Put a supporter on a member's team (``member_id: null`` unlinks).

    Their assignments on profiles the new member isn't on are dropped.
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    profile_service.link_supporter(db, user, payload.member_id or None)
    db.commit()
    return {"id": user.id, "member_id": user.member_id}


@router.post("/users/{user_id}/set-work-plan-reviewer")
def set_user_work_plan_reviewer(
    user_id: str,
    payload: UserSetWorkPlanReviewer,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Let a member read everyone's work plan and comment on it (no edits)."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.enabled and normalize_role(user.role) != "member":
        raise HTTPException(
            status_code=400,
            detail="Only members can be work plan reviewers; admins and managers already see every plan",
        )
    user.can_review_work_plans = payload.enabled
    db.commit()
    return {"id": user.id, "can_review_work_plans": user.can_review_work_plans}


@router.post("/users/{user_id}/set-timezone")
def set_user_timezone(
    user_id: str,
    payload: UserSetTimezone,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Admin-set IANA timezone, used to bucket a supporter's Colleagues activity."""
    tz = payload.timezone.strip()
    if tz not in available_timezones():
        raise HTTPException(status_code=400, detail="Unknown timezone")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    user.timezone = tz
    db.commit()
    return {"id": user.id, "email": user.email, "timezone": user.timezone}


@router.post("/users/{user_id}/set-password")
def set_user_password(
    user_id: str,
    payload: UserSetPassword,
    admin: User = Depends(get_current_admin),
    session_id: str | None = Depends(get_current_session_id),
    db: Session = Depends(get_db),
) -> dict:
    """Admin force-set a user's password. Signs them out everywhere."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    password = (payload.password or "").strip()
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    user.password_hash = hash_password(password)
    auth_sessions.end_user_sessions(
        db, user.id, reason="password_reset", keep=session_id if user.id == admin.id else None
    )
    db.commit()
    return {"ok": True, "id": user.id, "email": user.email}


@router.get("/signups")
def list_signups(
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = (
        db.execute(
            select(SignupRequest)
            .where(SignupRequest.status.in_(OPEN_SIGNUP_STATUSES))
            .order_by(SignupRequest.created_at.desc())
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": r.id,
            "email": r.email,
            "name": r.name,
            "status": r.status,
            "otp_expires_at": (
                r.otp_expires_at.isoformat() + "Z" if r.otp_expires_at else None
            ),
            "otp_attempts": r.otp_attempts,
            "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
        }
        for r in rows
    ]


@router.post("/signups/{signup_id}/issue-otp")
def issue_signup_otp(
    signup_id: str,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Generate a temporary OTP and return plaintext once for manual delivery."""
    req = db.get(SignupRequest, signup_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Signup request not found")
    if req.status not in OPEN_SIGNUP_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot issue OTP for status={req.status}",
        )

    otp = generate_otp()
    now = datetime.utcnow()
    req.otp_hash = hash_otp(otp)
    req.otp_expires_at = now + timedelta(minutes=OTP_TTL_MINUTES)
    req.otp_attempts = 0
    req.status = "otp_issued"
    req.updated_at = now
    db.commit()
    return {
        "id": req.id,
        "email": req.email,
        "otp": otp,
        "expires_at": req.otp_expires_at.isoformat() + "Z",
        "ttl_minutes": OTP_TTL_MINUTES,
        "message": (
            "Copy this OTP now and send it to the user manually. "
            "It will not be shown again."
        ),
    }


@router.post("/signups/{signup_id}/reject")
def reject_signup(
    signup_id: str,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    req = db.get(SignupRequest, signup_id)
    if req is None:
        raise HTTPException(status_code=404, detail="Signup request not found")
    if req.status not in OPEN_SIGNUP_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reject signup with status={req.status}",
        )
    req.status = "rejected"
    req.otp_hash = None
    req.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "id": req.id, "status": "rejected"}


@router.get("/users/{user_id}/data")
def user_data(
    user_id: str,
    account: str | None = None,
    include_deleted: bool = False,
    tz_offset: int | None = Query(
        None,
        description="Client offset minutes east of UTC (−getTimezoneOffset()). "
        "Used for this-week / this-month interview windows.",
    ),
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Return a user's synced tracking data, optionally filtered by profile/account."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    app_filters = [Application.user_id == user_id]
    if not include_deleted:
        app_filters.append(Application.deleted_at.is_(None))
    account_filter = (account or "").strip()
    if account_filter:
        app_filters.append(Application.account == account_filter)

    apps = db.execute(
        select(Application)
        .where(*app_filters)
        .order_by(Application.last_modified.desc())
        .limit(1000)
    ).scalars().all()

    iv_filters = [Interview.user_id == user_id]
    if not include_deleted:
        iv_filters.append(Interview.deleted_at.is_(None))
    interviews = db.execute(
        select(Interview)
        .where(*iv_filters)
        .order_by(Interview.start_time_utc.desc())
        .limit(1000)
    ).scalars().all()

    profiles = db.execute(
        select(Profile)
        .join(ProfileAssignment, ProfileAssignment.profile_id == Profile.id)
        .where(ProfileAssignment.user_id == user_id)
        .order_by(Profile.key)
    ).scalars().all()

    # Co-assignees per profile (colleagues). Supporters see members only;
    # members/managers see all non-admin co-assignees.
    profile_ids_for_colleagues = [p.id for p in profiles]
    colleagues_by_profile: dict[str, list[dict]] = {
        pid: [] for pid in profile_ids_for_colleagues
    }
    if profile_ids_for_colleagues:
        co_rows = db.execute(
            select(ProfileAssignment.profile_id, User)
            .join(User, User.id == ProfileAssignment.user_id)
            .where(
                ProfileAssignment.profile_id.in_(profile_ids_for_colleagues),
                ProfileAssignment.user_id != user_id,
            )
            .order_by(User.name, User.email)
        ).all()
        for pid, other in co_rows:
            if not other.is_active or not _is_visible_colleague(user.role, other.role):
                continue
            colleagues_by_profile.setdefault(pid, []).append(_colleague_preview(other))

    # Distinct account/profile labels present on this user's applications
    accounts = sorted(
        {
            (a or "").strip()
            for a in db.execute(
                select(Application.account).where(
                    Application.user_id == user_id,
                    Application.deleted_at.is_(None),
                )
            ).scalars().all()
            if (a or "").strip()
        },
        key=str.lower,
    )

    profile_ids = [p.id for p in profiles]
    offset = _clamp_tz_offset(tz_offset)
    local_today = _local_today(offset)
    week_start_local = local_today - timedelta(days=(local_today.weekday() - 5) % 7)
    month_start_local = local_today.replace(day=1)
    week_start_utc = week_start_local - timedelta(minutes=offset)
    month_start_utc = month_start_local - timedelta(minutes=offset)

    if profile_ids:
        applications_total = db.execute(
            select(func.count())
            .select_from(Application)
            .where(
                Application.user_id == user_id,
                Application.profile_id.in_(profile_ids),
                Application.deleted_at.is_(None),
            )
        ).scalar_one()
        applications_this_week = db.execute(
            select(func.count())
            .select_from(Application)
            .where(
                Application.user_id == user_id,
                Application.profile_id.in_(profile_ids),
                Application.deleted_at.is_(None),
                Application.applied_date.is_not(None),
                Application.applied_date >= week_start_utc,
            )
        ).scalar_one()
        applications_this_month = db.execute(
            select(func.count())
            .select_from(Application)
            .where(
                Application.user_id == user_id,
                Application.profile_id.in_(profile_ids),
                Application.deleted_at.is_(None),
                Application.applied_date.is_not(None),
                Application.applied_date >= month_start_utc,
            )
        ).scalar_one()
        interviews_this_week = db.execute(
            select(func.count())
            .select_from(Interview)
            .where(
                Interview.user_id == user_id,
                Interview.profile_id.in_(profile_ids),
                Interview.deleted_at.is_(None),
                Interview.start_time_utc >= week_start_utc,
            )
        ).scalar_one()
        interviews_this_month = db.execute(
            select(func.count())
            .select_from(Interview)
            .where(
                Interview.user_id == user_id,
                Interview.profile_id.in_(profile_ids),
                Interview.deleted_at.is_(None),
                Interview.start_time_utc >= month_start_utc,
            )
        ).scalar_one()
    else:
        applications_total = 0
        applications_this_week = 0
        applications_this_month = 0
        interviews_this_week = 0
        interviews_this_month = 0

    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "avatar_url": user.avatar_url,
            "role": user.role,
            "is_active": user.is_active,
        },
        "profiles": [
            {
                "key": p.key,
                "name": p.name,
                "email": p.email,
                "phone": p.phone,
                "location": p.location,
                "linkedin": p.linkedin,
                "university_name": p.university_name,
                "degree": p.degree,
                "colleagues": colleagues_by_profile.get(p.id, []),
            }
            for p in profiles
        ],
        "accounts": accounts,
        "filter_account": account_filter or None,
        "summary": {
            "applications_total": int(applications_total),
            "applications_this_week": int(applications_this_week),
            "applications_this_month": int(applications_this_month),
            "interviews_this_week": int(interviews_this_week),
            "interviews_this_month": int(interviews_this_month),
            "tz_offset": offset,
            "week_start": week_start_utc.isoformat() + "Z",
            "month_start": month_start_utc.isoformat() + "Z",
        },
        "applications": [
            {
                "id": a.id,
                "company_name": a.company_name,
                "position_title": a.position_title,
                "job_link": a.job_link,
                "status": a.status,
                "applied_date": a.applied_date.isoformat() + "Z" if a.applied_date else None,
                "account": a.account,
                "notes": a.notes,
                "last_modified": a.last_modified.isoformat() + "Z" if a.last_modified else None,
                "deleted_at": a.deleted_at.isoformat() + "Z" if a.deleted_at else None,
            }
            for a in apps
        ],
        "interviews": [
            {
                "id": iv.id,
                "title": iv.title,
                "start_time_utc": iv.start_time_utc.isoformat() + "Z" if iv.start_time_utc else None,
                "end_time_utc": iv.end_time_utc.isoformat() + "Z" if iv.end_time_utc else None,
                # Needed to render the time. Despite the field name, start/end are
                # naive wall clock in *this* zone, not UTC instants; the web reads
                # them with interviewDate(value, timezone). Omitting it makes the
                # UI fall back to new Date(), which treats the trailing Z as real
                # UTC and shifts the time by the viewer's offset.
                "timezone": iv.timezone,
                "status": iv.status,
                "interview_type": iv.interview_type,
                "application_id": iv.application_id,
            }
            for iv in interviews
        ],
    }


@router.delete("/users/{user_id}/data", status_code=status.HTTP_200_OK)
def erase_user_data(
    user_id: str,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Right-to-erasure: delete a user's applications, interviews, and work plans."""
    from sqlalchemy import delete as sa_delete

    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    # Delete interviews first: they FK-reference applications, so removing the
    # parent applications first violates the constraint under Postgres (prod).
    ivs = db.execute(
        sa_delete(Interview).where(Interview.user_id == user_id)
    ).rowcount
    apps = db.execute(
        sa_delete(Application).where(Application.user_id == user_id)
    ).rowcount
    item_ids = [
        r[0]
        for r in db.execute(
            select(WorkPlanItem.id).where(WorkPlanItem.user_id == user_id)
        ).all()
    ]
    if item_ids:
        db.execute(
            sa_delete(WorkPlanComment).where(WorkPlanComment.item_id.in_(item_ids))
        )
    db.execute(sa_delete(WorkPlanComment).where(WorkPlanComment.author_id == user_id))
    plans = db.execute(
        sa_delete(WorkPlanItem).where(WorkPlanItem.user_id == user_id)
    ).rowcount
    db.commit()
    return {
        "ok": True,
        "deleted_applications": apps,
        "deleted_interviews": ivs,
        "deleted_work_plan_items": plans,
    }


@router.delete("/users/{user_id}", status_code=status.HTTP_200_OK)
def delete_user_account(
    user_id: str,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Delete a user account and all associated data."""
    from sqlalchemy import delete as sa_delete
    from sqlalchemy import update as sa_update

    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if normalize_role(user.role) == "admin" and not is_true_admin(admin):
        raise HTTPException(
            status_code=403, detail="Only admins can delete admin accounts"
        )

    # Hard FKs: detach seats/clients before removing the user row (Postgres).
    now = datetime.utcnow()
    seats = db.execute(
        select(LicenseSeat).where(LicenseSeat.user_id == user_id)
    ).scalars().all()
    for seat in seats:
        if not seat.revoked and seat.revoked_at is None:
            seat.revoked = True
            seat.revoked_at = now
            seat.revoked_reason = "user_deleted"
        seat.user_id = None
    db.execute(
        sa_update(Client).where(Client.user_id == user_id).values(user_id=None)
    )

    db.execute(sa_delete(Interview).where(Interview.user_id == user_id))
    db.execute(sa_delete(Application).where(Application.user_id == user_id))
    item_ids = [
        r[0]
        for r in db.execute(
            select(WorkPlanItem.id).where(WorkPlanItem.user_id == user_id)
        ).all()
    ]
    if item_ids:
        db.execute(
            sa_delete(WorkPlanComment).where(WorkPlanComment.item_id.in_(item_ids))
        )
    db.execute(sa_delete(WorkPlanComment).where(WorkPlanComment.author_id == user_id))
    db.execute(sa_delete(WorkPlanItem).where(WorkPlanItem.user_id == user_id))
    db.execute(sa_delete(ProfileAssignment).where(ProfileAssignment.user_id == user_id))
    profile_service.unlink_team(db, user_id)
    avatar_overrides = list(db.execute(
        select(MemberAvatarOverride).where(
            or_(MemberAvatarOverride.member_id == user_id, MemberAvatarOverride.supporter_id == user_id)
        )
    ).scalars().all())
    from .team import _delete_override_files
    for override in avatar_overrides:
        _delete_override_files(override.member_id, override.supporter_id)
    db.execute(sa_delete(MemberAvatarOverride).where(
        or_(MemberAvatarOverride.member_id == user_id, MemberAvatarOverride.supporter_id == user_id)
    ))

    # Soft refs (no FK): null so history does not point at a missing user.
    for model in (
        SyncAudit,
        TelemetryEvent,
        ErrorReport,
        ConsentRecord,
        LicenseAudit,
        LicenseSeatUser,
    ):
        db.execute(sa_update(model).where(model.user_id == user_id).values(user_id=None))
    db.execute(
        sa_update(SignupRequest)
        .where(SignupRequest.completed_user_id == user_id)
        .values(completed_user_id=None)
    )

    from .users import delete_avatar_files

    delete_avatar_files(user_id)

    db.execute(sa_delete(AuthSession).where(AuthSession.user_id == user_id))
    db.delete(user)
    db.commit()
    return {"ok": True, "deleted_user": user_id}


@router.get("/errors")
def recent_errors(
    limit: int = 100,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        select(ErrorReport).order_by(ErrorReport.created_at.desc()).limit(min(limit, 500))
    ).scalars().all()
    user_ids = {e.user_id for e in rows if e.user_id}
    users: dict[str, User] = {}
    if user_ids:
        for u in db.execute(select(User).where(User.id.in_(user_ids))).scalars().all():
            users[u.id] = u
    return [
        {
            "id": e.id,
            "device_id": e.device_id,
            "user_id": e.user_id,
            "user_name": users[e.user_id].name if e.user_id in users else None,
            "user_email": users[e.user_id].email if e.user_id in users else None,
            "user_avatar_url": users[e.user_id].avatar_url if e.user_id in users else None,
            "app_version": e.app_version,
            "error_type": e.error_type,
            "message": e.message,
            "traceback": e.traceback,
            "created_at": e.created_at.isoformat() + "Z",
        }
        for e in rows
    ]


@router.get("/telemetry/summary")
def telemetry_summary(
    _admin: User = Depends(get_current_admin), db: Session = Depends(get_db)
) -> list[dict]:
    rows = db.execute(
        select(TelemetryEvent.event, func.count())
        .group_by(TelemetryEvent.event)
        .order_by(func.count().desc())
    ).all()
    return [{"event": event, "count": count} for event, count in rows]


@router.get("/telemetry/recent")
def telemetry_recent(
    limit: int = 100,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.execute(
        select(TelemetryEvent)
        .order_by(TelemetryEvent.created_at.desc())
        .limit(min(limit, 500))
    ).scalars().all()
    result = []
    for e in rows:
        props = None
        if e.properties:
            try:
                props = json.loads(e.properties)
            except (TypeError, ValueError):
                props = None
        result.append(
            {
                "event": e.event,
                "app_version": e.app_version,
                "device_id": e.device_id,
                "properties": props,
                "created_at": e.created_at.isoformat() + "Z",
            }
        )
    return result


# ----- Sync activity monitoring (profile-scoped sync redesign) -----

# A single push inserting more than this many brand-new rows is flagged as a
# likely epoch re-push storm / cross-account duplication event.
_NEW_INSERT_STORM_THRESHOLD = 200


@router.get("/sync/activity")
def sync_activity(
    hours: int = 24,
    limit: int = 50,
    offset: int = 0,
    user_id: str | None = None,
    profile_key: str | None = None,
    device_id: str | None = None,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Recent sync push activity with per-push outcomes and anomaly flags.

    Surfaces push volume, rejections, and suspected duplicate storms so admins
    can spot inefficient or cross-account sync behavior. Paginated - the
    returned ``totals`` are aggregated over the *whole* filtered window, not
    just the current page, so the summary cards stay stable while paging.
    """
    hours = max(1, min(hours, 24 * 30))
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    since = datetime.utcnow() - timedelta(hours=hours)

    filters = [SyncAudit.created_at >= since]
    if user_id:
        filters.append(SyncAudit.user_id == user_id)
    profile = _resolve_profile(db, profile_key)
    if (profile_key or "").strip() and profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    if profile is not None:
        filters.append(SyncAudit.profile_id == profile.id)
    device_search = (device_id or "").strip()
    if device_search:
        filters.append(SyncAudit.device_id.ilike(f"%{device_search}%"))

    (
        total,
        sum_accepted,
        sum_new_inserts,
        sum_rejected,
        sum_conflicts,
        sum_unscoped,
        sum_revision_conflicts,
        sum_missing_parent,
        sum_time_conflict,
        sum_error,
        insert_storms,
    ) = db.execute(
        select(
            func.count(SyncAudit.id),
            func.coalesce(func.sum(SyncAudit.accepted), 0),
            func.coalesce(func.sum(SyncAudit.new_inserts), 0),
            func.coalesce(func.sum(SyncAudit.rejected_not_assigned), 0),
            func.coalesce(func.sum(SyncAudit.version_conflicts), 0),
            func.coalesce(func.sum(SyncAudit.rejected_unscoped), 0),
            func.coalesce(func.sum(SyncAudit.revision_conflicts), 0),
            func.coalesce(func.sum(SyncAudit.rejected_missing_parent), 0),
            func.coalesce(func.sum(SyncAudit.rejected_time_conflict), 0),
            func.coalesce(func.sum(SyncAudit.rejected_error), 0),
            func.coalesce(
                func.sum(
                    case((SyncAudit.new_inserts >= _NEW_INSERT_STORM_THRESHOLD, 1), else_=0)
                ),
                0,
            ),
        ).where(*filters)
    ).one()

    rows = db.execute(
        select(SyncAudit)
        .where(*filters)
        .order_by(SyncAudit.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).scalars().all()

    # Lookups for human-readable labels.
    users = {u.id: u for u in db.execute(select(User)).scalars().all()}
    profile_names = {
        p.id: (p.name or p.key)
        for p in db.execute(select(Profile)).scalars().all()
    }

    def _flags(a: SyncAudit) -> list[str]:
        out: list[str] = []
        if a.new_inserts >= _NEW_INSERT_STORM_THRESHOLD:
            out.append("insert_storm")
        # unscoped/not_assigned are both counted in rejected_not_assigned;
        # show the more specific one when we can tell them apart.
        if a.rejected_unscoped > 0:
            out.append("unscoped")
        elif a.rejected_not_assigned > 0:
            out.append("not_assigned")
        if a.revision_conflicts > 0:
            out.append("revision_conflict")
        if a.version_conflicts > a.revision_conflicts:
            out.append("version_conflict")
        if a.rejected_missing_parent > 0:
            out.append("missing_parent")
        if a.rejected_time_conflict > 0:
            out.append("time_conflict")
        if a.rejected_error > 0:
            out.append("error")
        return out

    events = [
        {
            "id": a.id,
            "user_id": a.user_id,
            "user_name": users[a.user_id].name if a.user_id in users else None,
            "user_email": users[a.user_id].email if a.user_id in users else None,
            "user_avatar_url": users[a.user_id].avatar_url if a.user_id in users else None,
            "profile_id": a.profile_id,
            "profile_name": profile_names.get(a.profile_id) if a.profile_id else None,
            "device_id": a.device_id,
            "accepted": a.accepted,
            "new_inserts": a.new_inserts,
            "rejected_not_assigned": a.rejected_not_assigned,
            "version_conflicts": a.version_conflicts,
            "other_rejected": a.other_rejected,
            "rejected_unscoped": a.rejected_unscoped,
            "revision_conflicts": a.revision_conflicts,
            "rejected_missing_parent": a.rejected_missing_parent,
            "rejected_time_conflict": a.rejected_time_conflict,
            "rejected_error": a.rejected_error,
            "apps_in_request": a.apps_in_request,
            "interviews_in_request": a.interviews_in_request,
            "flags": _flags(a),
            "created_at": a.created_at.isoformat() + "Z" if a.created_at else None,
        }
        for a in rows
    ]

    # Aggregate window totals - over the whole filtered window, not just this page.
    agg = {
        "pushes": total,
        "accepted": sum_accepted,
        "new_inserts": sum_new_inserts,
        "rejected_not_assigned": sum_rejected,
        "version_conflicts": sum_conflicts,
        "rejected_unscoped": sum_unscoped,
        "revision_conflicts": sum_revision_conflicts,
        "rejected_missing_parent": sum_missing_parent,
        "rejected_time_conflict": sum_time_conflict,
        "rejected_error": sum_error,
        "insert_storms": insert_storms,
    }

    return {
        "window_hours": hours,
        "filter_user_id": user_id,
        "filter_profile_key": profile.key if profile else None,
        "filter_device_id": device_search or None,
        "total": total,
        "limit": limit,
        "offset": offset,
        "totals": agg,
        "events": events,
    }


def _trend_bucket_seconds(hours: int) -> int:
    """Bucket width that keeps a trend chart to roughly 40-60 points.

    The activity table already gives per-push detail; this is for spotting a
    shape over time (a client stuck in a loop shows as a flat, unmoving line -
    see the "Sync activity" help section) that's hard to eyeball from rows.
    """
    if hours <= 1:
        return 60  # 1 minute
    if hours <= 24:
        return 30 * 60  # 30 minutes
    if hours <= 24 * 7:
        return 4 * 3600  # 4 hours
    return 12 * 3600  # 12 hours


@router.get("/sync/trend")
def sync_trend(
    hours: int = 24,
    user_id: str | None = None,
    profile_key: str | None = None,
    device_id: str | None = None,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Bucketed accepted/conflict/rejected counts for the sync activity trend chart.

    Same filters as ``/sync/activity``, aggregated into fixed-width time
    buckets instead of one row per push.
    """
    hours = max(1, min(hours, 24 * 30))
    since = datetime.utcnow() - timedelta(hours=hours)

    filters = [SyncAudit.created_at >= since]
    if user_id:
        filters.append(SyncAudit.user_id == user_id)
    profile = _resolve_profile(db, profile_key)
    if (profile_key or "").strip() and profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")
    if profile is not None:
        filters.append(SyncAudit.profile_id == profile.id)
    device_search = (device_id or "").strip()
    if device_search:
        filters.append(SyncAudit.device_id.ilike(f"%{device_search}%"))

    bucket_seconds = _trend_bucket_seconds(hours)
    bucket_expr = func.to_timestamp(
        func.floor(extract("epoch", SyncAudit.created_at) / bucket_seconds) * bucket_seconds
    )

    rows = db.execute(
        select(
            bucket_expr.label("bucket"),
            func.coalesce(func.sum(SyncAudit.accepted), 0).label("accepted"),
            func.coalesce(func.sum(SyncAudit.version_conflicts), 0).label("version_conflicts"),
            func.coalesce(func.sum(SyncAudit.rejected_not_assigned), 0).label("not_assigned"),
            func.coalesce(func.sum(SyncAudit.rejected_unscoped), 0).label("unscoped"),
            func.coalesce(
                func.sum(
                    SyncAudit.other_rejected
                    - SyncAudit.rejected_missing_parent
                    - SyncAudit.rejected_time_conflict
                    - SyncAudit.rejected_error
                ),
                0,
            ).label("other_rejected"),
            func.coalesce(func.sum(SyncAudit.rejected_missing_parent), 0).label(
                "missing_parent"
            ),
            func.coalesce(func.sum(SyncAudit.rejected_time_conflict), 0).label("time_conflict"),
            func.coalesce(func.sum(SyncAudit.rejected_error), 0).label("error"),
        )
        .where(*filters)
        .group_by(bucket_expr)
        .order_by(bucket_expr)
    ).all()

    points = [
        {
            "bucket": bucket.isoformat() + "Z",
            "accepted": accepted,
            "version_conflicts": version_conflicts,
            "not_assigned": not_assigned,
            "unscoped": unscoped,
            "other_rejected": other_rejected,
            "missing_parent": missing_parent,
            "time_conflict": time_conflict,
            "error": error,
        }
        for (
            bucket,
            accepted,
            version_conflicts,
            not_assigned,
            unscoped,
            other_rejected,
            missing_parent,
            time_conflict,
            error,
        ) in rows
    ]

    return {
        "window_hours": hours,
        "bucket_seconds": bucket_seconds,
        "filter_user_id": user_id,
        "filter_profile_key": profile.key if profile else None,
        "filter_device_id": device_search or None,
        "points": points,
    }


@router.get("/profiles/{key}/data")
def profile_data(
    key: str,
    include_deleted: bool = False,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Shared dataset for a profile: applications/interviews owned by ``profile_id``.

    Shows who last edited each record (``user_id``) so admins can see the shared
    profile view rather than a single user's slice.
    """
    profile = db.execute(
        select(Profile).where(Profile.key == key)
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found")

    assignees = db.execute(
        select(User.id, User.email, User.name)
        .join(ProfileAssignment, ProfileAssignment.user_id == User.id)
        .where(ProfileAssignment.profile_id == profile.id)
        .order_by(User.email)
    ).all()
    email_by_user = {uid: email for uid, email, _ in assignees}

    app_filters = [Application.profile_id == profile.id]
    if not include_deleted:
        app_filters.append(Application.deleted_at.is_(None))
    apps = db.execute(
        select(Application)
        .where(*app_filters)
        .order_by(Application.last_modified.desc())
        .limit(2000)
    ).scalars().all()

    iv_filters = [Interview.profile_id == profile.id]
    if not include_deleted:
        iv_filters.append(Interview.deleted_at.is_(None))
    interviews = db.execute(
        select(Interview)
        .where(*iv_filters)
        .order_by(Interview.start_time_utc.desc())
        .limit(2000)
    ).scalars().all()

    # Fill in editor emails for any user not in the assignee set (historical).
    editor_ids = {a.user_id for a in apps} | {iv.user_id for iv in interviews}
    missing = [uid for uid in editor_ids if uid and uid not in email_by_user]
    if missing:
        for u in db.execute(
            select(User).where(User.id.in_(missing))
        ).scalars().all():
            email_by_user[u.id] = u.email

    return {
        "profile": {"key": profile.key, "name": profile.name},
        "assignees": [
            {"id": uid, "email": email, "name": name} for uid, email, name in assignees
        ],
        "counts": {"applications": len(apps), "interviews": len(interviews)},
        "applications": [
            {
                "id": a.id,
                "company_name": a.company_name,
                "position_title": a.position_title,
                "job_link": a.job_link,
                "status": a.status,
                "account": a.account,
                "applied_date": a.applied_date.isoformat() + "Z" if a.applied_date else None,
                "last_modified": a.last_modified.isoformat() + "Z" if a.last_modified else None,
                "deleted_at": a.deleted_at.isoformat() + "Z" if a.deleted_at else None,
                "last_editor_id": a.user_id,
                "last_editor_email": email_by_user.get(a.user_id),
            }
            for a in apps
        ],
        "interviews": [
            {
                "id": iv.id,
                "title": iv.title,
                "start_time_utc": iv.start_time_utc.isoformat() + "Z" if iv.start_time_utc else None,
                "end_time_utc": iv.end_time_utc.isoformat() + "Z" if iv.end_time_utc else None,
                # Needed to render the time. Despite the field name, start/end are
                # naive wall clock in *this* zone, not UTC instants; the web reads
                # them with interviewDate(value, timezone). Omitting it makes the
                # UI fall back to new Date(), which treats the trailing Z as real
                # UTC and shifts the time by the viewer's offset.
                "timezone": iv.timezone,
                "status": iv.status,
                "interview_type": iv.interview_type,
                "application_id": iv.application_id,
                "last_editor_id": iv.user_id,
                "last_editor_email": email_by_user.get(iv.user_id),
            }
            for iv in interviews
        ],
    }


# ----- CC Releases (user-portal download files) -----

_CC_FILES = {
    "app": {
        "filename": "CareerCraft.exe",
        "label": "CareerCraft App",
        "max_bytes": 250 * 1024 * 1024,
    },
    "cert": {
        "filename": "TekAbsurd_cert.cer",
        "label": "Signing Certificate",
        "max_bytes": 1 * 1024 * 1024,
    },
}


def _release_files_dir() -> Path:
    settings = get_settings()
    raw = (settings.release_files_dir or "").strip()
    if raw:
        path = Path(raw)
    else:
        path = Path(__file__).resolve().parent.parent / "nginx" / "userportal" / "downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _file_info(kind: str) -> dict:
    meta = _CC_FILES[kind]
    path = _release_files_dir() / meta["filename"]
    exists = path.is_file()
    size = path.stat().st_size if exists else 0
    mtime = datetime.utcfromtimestamp(path.stat().st_mtime).isoformat() + "Z" if exists else None
    sha256 = ""
    if exists and size <= 300 * 1024 * 1024:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        sha256 = h.hexdigest()
    return {
        "kind": kind,
        "label": meta["label"],
        "filename": meta["filename"],
        "exists": exists,
        "size_bytes": size,
        "modified_at": mtime,
        "sha256": sha256,
        "download_url": f"/userportal/downloads/{meta['filename']}",
    }


@router.get("/cc-releases")
def cc_releases_status(
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Current user-portal download files + recent release manifests."""
    latest = db.execute(
        select(Release).order_by(Release.published_at.desc()).limit(10)
    ).scalars().all()
    return {
        "files": [_file_info(kind) for kind in ("app", "cert")],
        "releases": [
            {
                "id": r.id,
                "channel": r.channel,
                "version": r.version,
                "url": r.url,
                "sha256": r.sha256,
                "mandatory": r.mandatory,
                "notes": r.notes,
                "published_at": r.published_at.isoformat() + "Z" if r.published_at else None,
            }
            for r in latest
        ],
    }


@router.post("/cc-releases/upload")
async def cc_releases_upload(
    kind: str = Form(...),
    file: UploadFile = File(...),
    version: str = Form(""),
    notes: str = Form(""),
    mandatory: str = Form("false"),
    channel: str = Form("stable"),
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Replace a user-portal download file. Optionally publish a release record for app uploads."""
    kind = (kind or "").strip().lower()
    if kind not in _CC_FILES:
        raise HTTPException(status_code=400, detail="kind must be 'app' or 'cert'")

    meta = _CC_FILES[kind]
    dest = _release_files_dir() / meta["filename"]
    # Overwrite in place so Docker file bind-mounts (host file -> container
    # path) pick up the new bytes immediately for nginx as well.
    hasher = hashlib.sha256()
    written = 0
    max_bytes = meta["max_bytes"]
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds max size of {max_bytes} bytes for {kind}",
                    )
                hasher.update(chunk)
                out.write(chunk)
        if written == 0:
            raise HTTPException(status_code=400, detail="Empty upload")
    finally:
        await file.close()

    sha256 = hasher.hexdigest()
    download_url = f"/userportal/downloads/{meta['filename']}"
    release_out = None
    mandatory_flag = str(mandatory).strip().lower() in ("1", "true", "yes", "on")
    if kind == "app" and (version or "").strip():
        release_channel = (channel or "stable").strip() or "stable"
        release_version = version.strip()
        current = latest_for_channel(db, release_channel)
        if current is not None and parse_version_tuple(
            release_version
        ) <= parse_version_tuple(current.version):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Version {release_version} is not newer than published "
                    f"{current.version}"
                ),
            )
        release = Release(
            channel=release_channel,
            version=release_version,
            url=download_url,
            sha256=sha256,
            mandatory=mandatory_flag,
            notes=(notes or "").strip(),
        )
        db.add(release)
        db.commit()
        db.refresh(release)
        release_out = {
            "id": release.id,
            "channel": release.channel,
            "version": release.version,
            "url": release.url,
            "sha256": release.sha256,
            "mandatory": release.mandatory,
            "notes": release.notes,
            "published_at": release.published_at.isoformat() + "Z",
        }

    return {
        "ok": True,
        "kind": kind,
        "filename": meta["filename"],
        "size_bytes": written,
        "sha256": sha256,
        "download_url": download_url,
        "release": release_out,
    }
