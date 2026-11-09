"""Interview endpoints for the team calendar (mine by assigned profiles, or all)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Application, Interview, Profile, User
from ..pipeline import (
    STAGE_INDEX,
    STAGE_LABELS,
    STAGE_ORDER,
    TERMINAL_OUTCOMES,
    normalize_outcome,
    outcome_label,
    stage_index,
    stage_label,
    summarize_application,
)
from ..security import get_current_user, get_user_profile_ids

router = APIRouter(prefix="/interviews", tags=["interviews"])

_DEFAULT_WINDOW_WEEKS = 8
_MAX_LIMIT = 5000


def _parse_utc(value: str | None, *, field: str) -> datetime | None:
    """Parse an ISO timestamp into naive UTC (server storage convention)."""
    if not value:
        return None
    from datetime import timezone

    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field}: {value}") from exc
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt

def _serialize(
    iv: Interview,
    *,
    user: User | None = None,
    profile: Profile | None = None,
    application: Application | None = None,
) -> dict:
    return {
        "id": iv.id,
        "application_id": iv.application_id,
        "title": iv.title,
        "description": iv.description,
        "start_time_utc": iv.start_time_utc.isoformat() + "Z" if iv.start_time_utc else None,
        "end_time_utc": iv.end_time_utc.isoformat() + "Z" if iv.end_time_utc else None,
        "timezone": iv.timezone,
        "location": iv.location,
        "interviewer_name": iv.interviewer_name,
        "interviewer_email": iv.interviewer_email,
        "interview_type": iv.interview_type,
        "notes": iv.notes,
        "status": iv.status,
        "stage": iv.stage,
        "stage_label": stage_label(iv.stage),
        "stage_index": stage_index(iv.stage),
        "round_index": iv.round_index,
        "outcome": normalize_outcome(iv.outcome),
        "outcome_label": outcome_label(iv.outcome),
        "outcome_at": iv.outcome_at.isoformat() + "Z" if iv.outcome_at else None,
        "next_step": iv.next_step,
        "next_step_due": (
            iv.next_step_due.isoformat() + "Z" if iv.next_step_due else None
        ),
        "last_modified": iv.last_modified.isoformat() + "Z" if iv.last_modified else None,
        "deleted_at": iv.deleted_at.isoformat() + "Z" if iv.deleted_at else None,
        "revision": iv.revision,
        "creator_user_id": iv.creator_user_id,
        "last_editor_user_id": iv.last_editor_user_id,
        "user_id": iv.user_id,
        "user_name": (user.name if user else None) or "",
        "user_email": (user.email if user else None) or "",
        "user_avatar_url": user.avatar_url if user else None,
        "profile_id": iv.profile_id,
        "profile_key": profile.key if profile else None,
        "profile_name": (profile.name if profile else None) or (profile.key if profile else None),
        "company_name": application.company_name if application else None,
        "position_title": application.position_title if application else None,
        "application_status": application.status if application else None,
        "application_job_link": application.job_link if application else None,
        "application_account": application.account if application else None,
        "application_notes": application.notes if application else None,
        "application_applied_date": (
            application.applied_date.isoformat() + "Z"
            if application and application.applied_date
            else None
        ),
    }


def _enrich(rows: list[Interview], db: Session) -> list[dict]:
    if not rows:
        return []
    user_ids = {iv.user_id for iv in rows if iv.user_id}
    profile_ids = {iv.profile_id for iv in rows if iv.profile_id}
    app_ids = {iv.application_id for iv in rows if iv.application_id}

    users: dict[str, User] = {}
    if user_ids:
        users = {
            u.id: u
            for u in db.execute(select(User).where(User.id.in_(user_ids))).scalars().all()
        }
    profiles: dict[str, Profile] = {}
    if profile_ids:
        profiles = {
            p.id: p
            for p in db.execute(select(Profile).where(Profile.id.in_(profile_ids))).scalars().all()
        }
    apps: dict[str, Application] = {}
    if app_ids:
        apps = {
            a.id: a
            for a in db.execute(select(Application).where(Application.id.in_(app_ids)))
            .scalars()
            .all()
        }

    return [
        _serialize(
            iv,
            user=users.get(iv.user_id),
            profile=profiles.get(iv.profile_id) if iv.profile_id else None,
            application=apps.get(iv.application_id) if iv.application_id else None,
        )
        for iv in rows
    ]


def _list_interviews(
    *,
    db: Session,
    user: User,
    scope: Literal["mine", "all"],
    limit: int,
    from_utc: datetime | None,
    to_utc: datetime | None,
    status: str | None,
    profile_id: str | None,
    user_id: str | None = None,
    assigned_user_id: str | None = None,
) -> list[dict]:
    conditions = [Interview.deleted_at.is_(None)]

    if scope == "mine":
        assigned = get_user_profile_ids(db, user.id)
        personal = and_(Interview.profile_id.is_(None), Interview.user_id == user.id)
        if assigned:
            conditions.append(or_(Interview.profile_id.in_(assigned), personal))
        else:
            conditions.append(personal)
    elif scope != "all":
        raise HTTPException(status_code=400, detail="scope must be 'mine' or 'all'")

    if from_utc is not None:
        conditions.append(Interview.end_time_utc >= from_utc)
    if to_utc is not None:
        conditions.append(Interview.start_time_utc <= to_utc)
    if status:
        conditions.append(Interview.status == status)
    if profile_id:
        conditions.append(Interview.profile_id == profile_id)
    if user_id:
        conditions.append(Interview.user_id == user_id)
    if assigned_user_id:
        # Person filter: interviews on profiles assigned to that user, plus their personal rows.
        their_profiles = get_user_profile_ids(db, assigned_user_id)
        personal = and_(
            Interview.profile_id.is_(None),
            Interview.user_id == assigned_user_id,
        )
        if their_profiles:
            conditions.append(or_(Interview.profile_id.in_(their_profiles), personal))
        else:
            conditions.append(personal)

    rows = db.execute(
        select(Interview)
        .where(*conditions)
        .order_by(Interview.start_time_utc.asc())
        .limit(min(max(limit, 1), _MAX_LIMIT))
    ).scalars().all()
    return _enrich(list(rows), db)


def _default_window() -> tuple[datetime, datetime]:
    now = datetime.utcnow()
    start = now - timedelta(days=7)
    end = now + timedelta(weeks=_DEFAULT_WINDOW_WEEKS)
    return start, end


@router.get("/")
def list_interviews(
    scope: Literal["mine", "all"] = Query("mine"),
    limit: int = Query(2000, ge=1, le=_MAX_LIMIT),
    from_time: str | None = Query(None, alias="from"),
    to_time: str | None = Query(None, alias="to"),
    status: str | None = None,
    profile_id: str | None = None,
    user_id: str | None = None,
    assigned_user_id: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """List interviews for the team calendar.

    ``scope=mine`` returns interviews on profiles assigned to the caller, plus
    personal (``profile_id IS NULL``) rows owned by the caller.
    ``scope=all`` returns every non-deleted interview.
    Optional ``user_id`` further filters by interview owner (editor).
    Optional ``assigned_user_id`` filters by profiles assigned to that person
    (plus their personal interviews).
    """
    from_utc = _parse_utc(from_time, field="from")
    to_utc = _parse_utc(to_time, field="to")
    if from_utc is None and to_utc is None:
        from_utc, to_utc = _default_window()
    return _list_interviews(
        db=db,
        user=user,
        scope=scope,
        limit=limit,
        from_utc=from_utc,
        to_utc=to_utc,
        status=status,
        profile_id=profile_id,
        user_id=user_id,
        assigned_user_id=assigned_user_id,
    )


@router.get("/all")
def all_team_interviews(
    limit: int = Query(2000, ge=1, le=_MAX_LIMIT),
    from_time: str | None = Query(None, alias="from"),
    to_time: str | None = Query(None, alias="to"),
    status: str | None = None,
    profile_id: str | None = None,
    user_id: str | None = None,
    assigned_user_id: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    """All team members' interviews (alias of ``scope=all``)."""
    from_utc = _parse_utc(from_time, field="from")
    to_utc = _parse_utc(to_time, field="to")
    if from_utc is None and to_utc is None:
        from_utc, to_utc = _default_window()
    return _list_interviews(
        db=db,
        user=user,
        scope="all",
        limit=limit,
        from_utc=from_utc,
        to_utc=to_utc,
        status=status,
        profile_id=profile_id,
        user_id=user_id,
        assigned_user_id=assigned_user_id,
    )


def _pipeline_rows(
    *,
    db: Session,
    user: User,
    scope: Literal["mine", "all"],
    limit: int,
    profile_id: str | None,
    user_id: str | None,
    assigned_user_id: str | None,
) -> list[dict]:
    """Group the caller's accessible interviews into one row per application.

    The pipeline is derived rather than stored: an application's position is
    whatever its interviews say it is, so there is no second copy of the truth
    to drift. Volumes here are already capped and profile-scoped, so grouping in
    Python costs less than the denormalized columns it avoids.

    Returns every accessible pursuit with its whole journey. Month and state
    filtering happens afterwards in ``_matches``, against complete rows -
    windowing the interviews here would slice journeys in half: a pursuit whose
    screen was in July and whose onsite is in September would appear, in an
    August view, to have no rounds at all.
    """
    rows = _list_interviews(
        db=db,
        user=user,
        scope=scope,
        limit=limit,
        from_utc=None,
        to_utc=None,
        status=None,
        profile_id=profile_id,
        user_id=user_id,
        assigned_user_id=assigned_user_id,
    )

    grouped: dict[str, list[dict]] = {}
    meta: dict[str, dict] = {}
    for row in rows:
        # Interviews with no application are personal calendar entries, not a
        # pursuit of anything - they have no pipeline to sit in.
        app_id = row.get("application_id")
        if not app_id:
            continue
        grouped.setdefault(app_id, []).append(
            {
                "id": row["id"],
                "title": row["title"],
                "stage": row["stage"],
                "stage_label": row["stage_label"],
                "stage_index": row["stage_index"],
                "round_index": row["round_index"],
                "status": row["status"],
                "outcome": row["outcome"],
                "outcome_label": row["outcome_label"],
                "interview_type": row["interview_type"],
                "interviewer_name": row["interviewer_name"],
                "timezone": row["timezone"],
                "next_step": row["next_step"],
                "next_step_due": _parse_utc(row["next_step_due"], field="next_step_due"),
                "start_time_utc": _parse_utc(row["start_time_utc"], field="start_time"),
            }
        )
        meta.setdefault(app_id, row)

    out: list[tuple[dict, dict]] = []
    for app_id, steps in grouped.items():
        head = meta[app_id]
        summary = summarize_application(
            steps, application_status=head.get("application_status")
        )
        row = {
            "application_id": app_id,
            "company_name": head.get("company_name"),
            "position_title": head.get("position_title"),
            "application_status": head.get("application_status"),
            "application_job_link": head.get("application_job_link"),
            "application_account": head.get("application_account"),
            "application_notes": head.get("application_notes"),
            "application_applied_date": head.get("application_applied_date"),
            "profile_id": head.get("profile_id"),
            "profile_name": head.get("profile_name"),
            "profile_key": head.get("profile_key"),
            "user_id": head.get("user_id"),
            "user_name": head.get("user_name"),
            "user_email": head.get("user_email"),
            "user_avatar_url": head.get("user_avatar_url"),
            **_isoformat_summary(summary),
        }
        # Keep the summary's real ``last_activity`` datetime alongside the
        # wire row (whose own copy is already an ISO-Z string) so the sort
        # below can compare it directly, with no reparsing.
        out.append((summary, row))

    now = datetime.utcnow()
    out.sort(key=lambda item: _pipeline_sort_key(item[0], item[1]["company_name"], now=now))
    return [row for _summary, row in out]


def _pipeline_sort_key(summary: dict, company_name: str | None, *, now: datetime) -> tuple:
    """Furthest rung first, then most recently active, then company name.

    Two pursuits tied on rung rarely differ meaningfully by rounds recorded -
    once several share a rung, "most rounds" stops discriminating and the list
    falls back to whatever order the database happened to return, which reads
    as no order at all. Recency is the signal a person actually wants: which
    of these did I last touch. Company name is the final, fully-deterministic
    tiebreak, so two rows tied on everything else never swap places between
    refreshes. Mirrors the desktop client's pipeline tab - see
    ``pipeline_timing_for`` in ``interview_service.py``.
    """
    last_activity = summary.get("last_activity")
    return (
        -(summary.get("current_stage_index") or 0),
        (now - last_activity) if last_activity else timedelta.max,
        company_name or "",
    )


def _isoformat_summary(summary: dict) -> dict:
    """Render a pipeline summary's datetimes as the wire's ISO-Z strings."""

    def iso(value):
        return value.isoformat() + "Z" if isinstance(value, datetime) else value

    result = dict(summary)
    for key in ("next_scheduled", "last_activity", "next_step_due"):
        result[key] = iso(result.get(key))
    result["steps"] = [
        {**step, "start_time_utc": iso(step.get("start_time_utc")),
         "next_step_due": iso(step.get("next_step_due"))}
        for step in summary["steps"]
    ]
    return result


def _month_bounds(month: str) -> tuple[datetime, datetime]:
    """Parse ``YYYY-MM`` into a half-open [start, next month) UTC range."""
    try:
        year, mon = (int(part) for part in month.split("-", 1))
        start = datetime(year, mon, 1)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid month: {month} (expected YYYY-MM)"
        ) from exc
    end = datetime(year + (mon == 12), (mon % 12) + 1, 1)
    return start, end


def _active_months(rows: list[dict]) -> list[str]:
    """Months (newest first) in which any round happened, for the month picker.

    Built from the unfiltered set so the picker never offers an empty month and
    never hides one that has activity.
    """
    months = {
        str(step["start_time_utc"])[:7]
        for row in rows
        for step in row["steps"]
        if step.get("start_time_utc")
    }
    return sorted((m for m in months if len(m) == 7), reverse=True)


@router.get("/pipeline")
def interview_pipeline(
    scope: Literal["mine", "all"] = Query("mine"),
    limit: int = Query(2000, ge=1, le=_MAX_LIMIT),
    month: str | None = Query(None, description="YYYY-MM; omit for all history"),
    profile_id: str | None = None,
    user_id: str | None = None,
    assigned_user_id: str | None = None,
    state: str | None = Query(None, description="active|stalled|offer|rejected|withdrawn"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """One row per application: its rounds in order, and how many it passed.

    ``month`` selects applications that had a round that month; each row still
    carries its whole journey, because a pursuit's position depends on rounds
    that may have happened long before the month being viewed.
    """
    active_from, active_to = _month_bounds(month) if month else (None, None)

    # Unfiltered first, so the month picker can list every month with activity
    # even while a single month is selected.
    everything = _pipeline_rows(
        db=db,
        user=user,
        scope=scope,
        limit=limit,
        profile_id=profile_id,
        user_id=user_id,
        assigned_user_id=assigned_user_id,
    )
    months = _active_months(everything)

    rows = [
        row
        for row in everything
        if _matches(row, active_from=active_from, active_to=active_to, state=state)
    ]
    return {
        "stages": [
            {"key": key, "label": STAGE_LABELS[key], "index": i + 1}
            for i, key in enumerate(STAGE_ORDER)
        ],
        "applications": rows,
        "months": months,
        "month": month,
        # The funnel follows the same selection, so the chart and the rows below
        # it always describe the same set.
        "funnel": _funnel(rows),
        "totals": {
            "applications": len(rows),
            "active": sum(1 for r in rows if r["pipeline_state"] == "active"),
            "stalled": sum(1 for r in rows if r["pipeline_state"] == "stalled"),
            "offers": sum(1 for r in rows if r["pipeline_state"] == "offer"),
        },
        # Lifetime, not filtered by month/state: "how many did I pass" should
        # not change because the pipeline view's own toggle or month picker
        # happens to be narrowed.
        "interview_stats": _interview_stats(everything),
    }


def _matches(
    row: dict,
    *,
    active_from: datetime | None,
    active_to: datetime | None,
    state: str | None,
) -> bool:
    """Whether a pipeline row passes the month and state filters."""
    if state and row["pipeline_state"] != state:
        return False
    if not (active_from or active_to):
        return True
    for step in row["steps"]:
        start = step.get("start_time_utc")
        if not start:
            continue
        # Steps are ISO-Z strings by this point; compare on the YYYY-MM prefix
        # so no reparsing is needed.
        stamp = str(start)[:7]
        if active_from and stamp < active_from.strftime("%Y-%m"):
            continue
        if active_to and stamp >= active_to.strftime("%Y-%m"):
            continue
        return True
    return False


def _funnel(rows: list[dict]) -> list[dict]:
    """Reach and conversion at each rung of the ladder.

    ``reached`` counts applications whose furthest rung is at least this one, so
    the series is monotonically non-increasing and reads as a funnel. Conversion
    is measured against the rung above, which is what turns a flat interview
    count into a diagnosis of *where* pursuits are lost.
    """
    series: list[dict] = []
    previous = 0
    for index, key in enumerate(STAGE_ORDER, start=1):
        reached = sum(1 for r in rows if (r["current_stage_index"] or 0) >= index)
        # Not every company runs every rung. ``held`` counts applications that
        # actually had a round here, so a stage nobody uses reads as an empty
        # bar instead of a misleading 100% conversion.
        held = sum(
            1 for r in rows if any(s["stage"] == key for s in r["steps"])
        )
        series.append(
            {
                "key": key,
                "label": STAGE_LABELS[key],
                "index": index,
                "reached": reached,
                "held": held,
                "conversion": round(reached / previous * 100, 1) if previous else None,
            }
        )
        previous = reached
    return series


def _interview_stats(rows: list[dict]) -> dict:
    """Pass/fail outcomes across every round, overall and by stage.

    Where ``_funnel`` answers "how far did applications get", this answers
    "how did the rounds that happened actually go" - the number the funnel's
    reach/conversion figures cannot show, because they are silent on outcome.
    """
    steps = [step for row in rows for step in row["steps"]]

    def stats_for(subset: list[dict]) -> dict:
        passed = sum(1 for s in subset if s["outcome"] == "passed")
        failed = sum(1 for s in subset if s["outcome"] == "failed")
        judged = passed + failed
        return {
            "held": len(subset),
            "passed": passed,
            "failed": failed,
            "pass_rate": round(passed / judged * 100, 1) if judged else None,
        }

    return {
        **stats_for(steps),
        "final_round_reached": sum(
            1 for r in rows if (r["current_stage_index"] or 0) >= STAGE_INDEX["final"]
        ),
        "by_stage": [
            {"key": key, "label": STAGE_LABELS[key], **stats_for([s for s in steps if s["stage"] == key])}
            for key in STAGE_ORDER
        ],
    }


# --- Interview Insights (Interviews page tab) -------------------------------
#
# Scoped by application, not by interview: the date range is the *applied*
# date, so "how did interviewing go for what I applied to in March" reads
# correctly even though a March application's onsite may not happen until
# April. Every card and chart below is drawn from the same population of
# applications, filtered the same way, so none of them can silently disagree
# with each other.

_INSIGHTS_STAGE_LABELS = {**STAGE_LABELS, "screen": "Intro"}
_INSIGHTS_STAGE_COMPARISON = ("screen", "technical", "behavioral", "final")
_INSIGHTS_FUNNEL_STAGES = ("screen", "technical", "final", "offer")
_TREND_MAX_POINTS = {"week": 26, "month": 12}


def _parse_date_only(value: str | None, *, field: str, end_of_day: bool = False) -> datetime | None:
    """Parse a ``YYYY-MM-DD`` filter bound into naive UTC, inclusive of the whole day."""
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        day = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid {field}: {value} (expected YYYY-MM-DD)"
        ) from exc
    return day + timedelta(days=1, microseconds=-1) if end_of_day else day


def _insights_applications(
    *,
    db: Session,
    user: User,
    scope: Literal["mine", "all"],
    profile_id: str | None,
    assigned_user_id: str | None,
    applied_from: datetime | None,
    applied_to: datetime | None,
    company: str | None,
    position: str | None,
) -> list[Application]:
    """The application population Insights measures: scoped, then date/text filtered."""
    conditions = [Application.deleted_at.is_(None)]
    if scope == "mine":
        assigned = get_user_profile_ids(db, user.id)
        conditions.append(Application.profile_id.in_(assigned) if assigned else Application.id.is_(None))
    elif scope != "all":
        raise HTTPException(status_code=400, detail="scope must be 'mine' or 'all'")
    if profile_id:
        conditions.append(Application.profile_id == profile_id)
    if assigned_user_id:
        their_profiles = get_user_profile_ids(db, assigned_user_id)
        conditions.append(
            Application.profile_id.in_(their_profiles) if their_profiles else Application.id.is_(None)
        )
    if applied_from is not None:
        conditions.append(Application.applied_date.is_not(None))
        conditions.append(Application.applied_date >= applied_from)
    if applied_to is not None:
        conditions.append(Application.applied_date.is_not(None))
        conditions.append(Application.applied_date <= applied_to)
    if company and company.strip():
        conditions.append(Application.company_name.ilike(f"%{company.strip()}%"))
    if position and position.strip():
        conditions.append(Application.position_title.ilike(f"%{position.strip()}%"))
    return list(db.execute(select(Application).where(*conditions)).scalars().all())


def _outcome_bucket_counts(rows: list[dict], *, now: datetime) -> dict:
    """One row's worth of status buckets - shared by the summary card and each
    interview-type row, so the two can never define "upcoming" differently.

    Every row lands in exactly one of the five buckets below, so they always
    sum to ``total``. Before this, a ghosted or withdrawn round (outcome not
    "passed"/"failed") and a round whose date had already passed without ever
    being marked completed (still ``status="scheduled"``, so it didn't qualify
    as "upcoming" either) matched none of the independent counts and silently
    vanished from every chart - a type's card never added up to its own total.
    """
    completed = sum(1 for r in rows if r["status"] == "completed")
    cancelled = passed = failed = upcoming = awaiting_results = 0
    for r in rows:
        if r["status"] == "cancelled":
            cancelled += 1
        elif r["outcome"] == "passed":
            passed += 1
        elif r["outcome"] in TERMINAL_OUTCOMES:
            failed += 1
        elif r["status"] == "scheduled" and (r["start_time_utc"] or datetime.max) >= now:
            upcoming += 1
        else:
            awaiting_results += 1
    return {
        "total": len(rows),
        "completed": completed,
        "passed": passed,
        "failed": failed,
        "awaiting_results": awaiting_results,
        "upcoming": upcoming,
        "cancelled": cancelled,
    }


def _pass_rate(rows: list[dict]) -> dict:
    """Passed vs. every round that didn't end in an open "pending".

    A ghosted (``no_response``) or withdrawn round is not a pass, even though
    nobody ever clicked "failed" on it - counting only explicit passed/failed
    verdicts as "decided" let those silently drop out of the denominator and
    inflated the rate for anyone whose interviews mostly end in silence rather
    than a stated rejection.
    """
    passed = sum(1 for r in rows if r["outcome"] == "passed")
    decided = passed + sum(1 for r in rows if r["outcome"] in TERMINAL_OUTCOMES)
    return {
        "passed": passed,
        "decided": decided,
        "rate": round(passed / decided * 100, 1) if decided else None,
    }


def _bucket_key(start: datetime, granularity: str) -> tuple[str, str]:
    """(sort key, display label) for the trend chart's x-axis."""
    if granularity == "month":
        return start.strftime("%Y-%m"), start.strftime("%b %Y")
    monday = start - timedelta(days=start.weekday())
    monday = datetime(monday.year, monday.month, monday.day)
    return monday.strftime("%Y-%m-%d"), f"{monday.strftime('%b')} {monday.day}"


def _insights(
    app_ids: list[str],
    interviews: list[dict],
    *,
    trend_granularity: str,
    now: datetime | None = None,
) -> dict:
    """Aggregate one application population + its (already type-filtered)
    interviews into every card/chart the Insights tab shows.
    """
    now = now or datetime.utcnow()
    steps = [
        {
            "application_id": iv["application_id"],
            "interview_type": iv.get("interview_type") or "Other",
            "stage": iv.get("stage"),
            "status": str(iv.get("status") or "").lower(),
            "outcome": iv.get("outcome") or "pending",
            "start_time_utc": _parse_utc(iv.get("start_time_utc"), field="start_time_utc"),
        }
        for iv in interviews
    ]

    by_type: dict[str, list[dict]] = {}
    for step in steps:
        by_type.setdefault(step["interview_type"], []).append(step)
    outcomes_by_type = []
    for interview_type in sorted(by_type):
        rows = by_type[interview_type]
        buckets = _outcome_bucket_counts(rows, now=now)
        rate = _pass_rate(rows)
        outcomes_by_type.append(
            {
                "type": interview_type,
                "label": interview_type,
                **{k: v for k, v in buckets.items() if k != "total"},
                "scheduled_total": buckets["total"],
                "passed_share_of_scheduled": (
                    round(buckets["passed"] / buckets["total"] * 100, 1) if buckets["total"] else None
                ),
                "pass_rate": rate["rate"],
            }
        )

    by_application: dict[str, list[dict]] = {}
    for step in steps:
        by_application.setdefault(step["application_id"], []).append(step)

    stage_index_by_app: dict[str, int] = {}
    for app_id, app_steps in by_application.items():
        summary = summarize_application(
            [dict(s, round_index=None, next_step=None, next_step_due=None) for s in app_steps],
            now=now,
        )
        stage_index_by_app[app_id] = summary["current_stage_index"]

    funnel = [{"key": "applied", "label": "Applied", "count": len(app_ids)}]
    for key in _INSIGHTS_FUNNEL_STAGES:
        threshold = STAGE_INDEX[key]
        reached = sum(1 for app_id in app_ids if stage_index_by_app.get(app_id, 0) >= threshold)
        funnel.append({"key": key, "label": _INSIGHTS_STAGE_LABELS[key], "count": reached})

    trend_points: dict[str, dict] = {}
    for step in steps:
        if not step["start_time_utc"]:
            continue
        key, label = _bucket_key(step["start_time_utc"], trend_granularity)
        trend_points.setdefault(key, {"key": key, "label": label, "screen": [], "technical": []})
        if step["stage"] in ("screen", "technical"):
            trend_points[key][step["stage"]].append(step)

    cap = _TREND_MAX_POINTS.get(trend_granularity, 26)
    ordered_keys = sorted(trend_points)[-cap:]
    trend = {
        "granularity": trend_granularity,
        "points": [
            {
                "key": key,
                "label": trend_points[key]["label"],
                "intro": _pass_rate(trend_points[key]["screen"]),
                "technical": _pass_rate(trend_points[key]["technical"]),
            }
            for key in ordered_keys
        ],
    }

    return {
        "summary": _outcome_bucket_counts(steps, now=now),
        "pass_rate": {
            "intro": _pass_rate([s for s in steps if s["stage"] == "screen"]),
            "technical": _pass_rate([s for s in steps if s["stage"] == "technical"]),
            "overall": _pass_rate(steps),
        },
        "outcomes_by_type": outcomes_by_type,
        "stage_comparison": [
            {
                "key": key,
                "label": _INSIGHTS_STAGE_LABELS[key],
                **_pass_rate([s for s in steps if s["stage"] == key]),
            }
            for key in _INSIGHTS_STAGE_COMPARISON
        ],
        "funnel": funnel,
        "trend": trend,
    }


@router.get("/insights")
def interview_insights(
    scope: Literal["mine", "all"] = Query("mine"),
    profile_id: str | None = None,
    assigned_user_id: str | None = None,
    applied_from: str | None = None,
    applied_to: str | None = None,
    company: str | None = None,
    position: str | None = None,
    interview_type: str | None = None,
    trend_granularity: Literal["week", "month"] = Query("week"),
    limit: int = Query(5000, ge=1, le=_MAX_LIMIT),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Interview outcomes for the applications applied to in a date range.

    The date range filters *applications* by ``applied_date`` - not interviews
    by when they happened - so every card and chart describes the same
    "what I applied to in this window" population. ``interview_type`` further
    narrows the interviews counted (it has no meaning for an application with
    no interview yet, so it never removes anything from the Applied count).
    """
    from_dt = _parse_date_only(applied_from, field="applied_from")
    to_dt = _parse_date_only(applied_to, field="applied_to", end_of_day=True)

    apps = _insights_applications(
        db=db,
        user=user,
        scope=scope,
        profile_id=profile_id,
        assigned_user_id=assigned_user_id,
        applied_from=from_dt,
        applied_to=to_dt,
        company=company,
        position=position,
    )
    app_ids = [a.id for a in apps][:limit]

    all_interviews: list[dict] = []
    if app_ids:
        rows = db.execute(
            select(Interview).where(
                Interview.deleted_at.is_(None),
                Interview.application_id.in_(app_ids),
            )
        ).scalars().all()
        all_interviews = _enrich(list(rows), db)

    available_types = sorted({iv["interview_type"] for iv in all_interviews if iv.get("interview_type")})
    interviews = (
        [iv for iv in all_interviews if iv.get("interview_type") == interview_type]
        if interview_type
        else all_interviews
    )

    result = _insights(app_ids, interviews, trend_granularity=trend_granularity)
    result["filters"] = {"interview_types": available_types}
    return result
