"""Work-plan todos: week (categorized), month, and calendar-quarter horizons."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..security import (
    can_manage_others_work_plans,
    can_view_others_work_plans,
    get_current_work_plan_reviewer,
    get_current_work_plan_user,
    normalize_role,
)
from ..models import User, WorkPlanComment, WorkPlanItem
from ..schemas import (
    WorkPlanCommentCreate,
    WorkPlanItemCreate,
    WorkPlanItemUpdate,
)

router = APIRouter(prefix="/work-plans", tags=["work-plans"])

WEEK_CATEGORIES = frozenset({"current_job", "self_study", "work_plans"})
HORIZONS = frozenset({"week", "month", "quarter"})


def _parse_iso_date(value: str, *, field: str) -> date:
    raw = (value or "").strip()
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid {field}: expected YYYY-MM-DD"
        ) from exc


def _saturday_of(d: date) -> date:
    """Week starts on Saturday (team convention: Saturday → Friday)."""
    # Python weekday: Mon=0 … Sat=5 … Sun=6
    return d - timedelta(days=(d.weekday() - 5) % 7)


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _quarter_start(d: date) -> date:
    q_month = ((d.month - 1) // 3) * 3 + 1
    return date(d.year, q_month, 1)


def _periods_for_week(week_start: date) -> dict:
    week_end = week_start + timedelta(days=6)  # Friday
    month = _month_start(week_start)
    quarter = _quarter_start(week_start)
    q = (quarter.month - 1) // 3 + 1
    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "month_start": month.isoformat(),
        "quarter_start": quarter.isoformat(),
        "quarter_label": f"Q{q} {quarter.year}",
        "month_label": month.strftime("%B %Y"),
    }


def _legacy_week_period_keys(saturday: date) -> list[str]:
    """Include pre-migration Sunday/Friday week keys for the same Sat–Fri week."""
    return [
        saturday.isoformat(),
        (saturday + timedelta(days=1)).isoformat(),  # legacy Sunday start
        (saturday - timedelta(days=1)).isoformat(),  # legacy Friday start
    ]


def _comment_counts(db: Session, item_ids: list[str]) -> dict[str, int]:
    if not item_ids:
        return {}
    rows = db.execute(
        select(WorkPlanComment.item_id, func.count())
        .where(WorkPlanComment.item_id.in_(item_ids))
        .group_by(WorkPlanComment.item_id)
    ).all()
    return {item_id: int(count) for item_id, count in rows}


def _comments_by_item(db: Session, item_ids: list[str]) -> dict[str, list[dict]]:
    """Return comments for plan cards without issuing one query per item."""
    if not item_ids:
        return {}
    comments = db.execute(
        select(WorkPlanComment)
        .where(WorkPlanComment.item_id.in_(item_ids))
        .order_by(WorkPlanComment.created_at.asc())
    ).scalars().all()
    author_ids = {comment.author_id for comment in comments}
    authors = (
        {
            user.id: user
            for user in db.execute(select(User).where(User.id.in_(author_ids))).scalars().all()
        }
        if author_ids
        else {}
    )
    grouped: dict[str, list[dict]] = {}
    for comment in comments:
        grouped.setdefault(comment.item_id, []).append(
            _serialize_comment(comment, authors.get(comment.author_id))
        )
    return grouped


def _serialize(item: WorkPlanItem, *, comment_count: int = 0) -> dict:
    return {
        "id": item.id,
        "user_id": item.user_id,
        "horizon": item.horizon,
        "period_start": item.period_start,
        "category": item.category,
        "title": item.title,
        "contents": item.contents,
        "done": bool(item.done),
        "sort_order": item.sort_order,
        "comment_count": int(comment_count),
        "created_at": item.created_at.isoformat() + "Z" if item.created_at else None,
        "updated_at": item.updated_at.isoformat() + "Z" if item.updated_at else None,
    }


def _serialize_comment(comment: WorkPlanComment, author: User | None) -> dict:
    return {
        "id": comment.id,
        "item_id": comment.item_id,
        "author_id": comment.author_id,
        "author_name": (author.name if author else "") or "",
        "author_email": (author.email if author else "") or "",
        "author_role": normalize_role(author.role if author else None),
        "author_avatar_url": author.avatar_url if author else None,
        "body": comment.body,
        "created_at": comment.created_at.isoformat() + "Z" if comment.created_at else None,
    }


def _resolve_target_user_id(
    caller: User,
    requested: str | None,
    *,
    write: bool,
) -> str:
    if not requested or requested == caller.id:
        return caller.id
    allowed = (
        can_manage_others_work_plans(caller) if write else can_view_others_work_plans(caller)
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Not allowed")
    return requested


def _validate_create(payload: WorkPlanItemCreate) -> None:
    if payload.horizon not in HORIZONS:
        raise HTTPException(
            status_code=400, detail="horizon must be week, month, or quarter"
        )
    _parse_iso_date(payload.period_start, field="period_start")
    title = (payload.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    if payload.horizon == "week":
        if not payload.category or payload.category not in WEEK_CATEGORIES:
            raise HTTPException(
                status_code=400,
                detail="category must be current_job, self_study, or work_plans for week items",
            )
    elif payload.category:
        raise HTTPException(
            status_code=400, detail="category is only allowed for week items"
        )


def _get_item_or_404(db: Session, item_id: str) -> WorkPlanItem:
    item = db.get(WorkPlanItem, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Work plan item not found")
    return item


def _assert_can_view(caller: User, item: WorkPlanItem) -> None:
    if item.user_id != caller.id and not can_view_others_work_plans(caller):
        raise HTTPException(status_code=403, detail="Not allowed")


def _assert_can_mutate(caller: User, item: WorkPlanItem) -> None:
    """Plan owner, admin or manager. Work plan reviewers can't change others' plans."""
    if item.user_id != caller.id and not can_manage_others_work_plans(caller):
        raise HTTPException(status_code=403, detail="Not allowed")


def _assert_can_comment(caller: User, item: WorkPlanItem) -> None:
    """Plan owner, admin, manager or work plan reviewer may comment."""
    if item.user_id != caller.id and not can_view_others_work_plans(caller):
        raise HTTPException(status_code=403, detail="Not allowed")


@router.get("/")
def get_plan(
    week_start: str = Query(..., description="Saturday start of the selected week (YYYY-MM-DD)"),
    user_id: str | None = Query(
        None, description="Admins, managers and reviewers: view another user's plan"
    ),
    user: User = Depends(get_current_work_plan_user),
    db: Session = Depends(get_db),
) -> dict:
    ws = _parse_iso_date(week_start, field="week_start")
    if ws != _saturday_of(ws):
        raise HTTPException(status_code=400, detail="week_start must be a Saturday")
    periods = _periods_for_week(ws)
    target = _resolve_target_user_id(user, user_id, write=False)
    week_keys = _legacy_week_period_keys(ws)

    rows = db.execute(
        select(WorkPlanItem)
        .where(
            WorkPlanItem.user_id == target,
            (
                (
                    (WorkPlanItem.horizon == "week")
                    & (WorkPlanItem.period_start.in_(week_keys))
                )
                | (
                    (WorkPlanItem.horizon == "month")
                    & (WorkPlanItem.period_start == periods["month_start"])
                )
                | (
                    (WorkPlanItem.horizon == "quarter")
                    & (WorkPlanItem.period_start == periods["quarter_start"])
                )
            ),
        )
        .order_by(WorkPlanItem.sort_order.asc(), WorkPlanItem.created_at.asc())
    ).scalars().all()

    item_ids = [row.id for row in rows]
    counts = _comment_counts(db, item_ids)
    comments_by_item = _comments_by_item(db, item_ids)
    week_items: list[dict] = []
    month_items: list[dict] = []
    quarter_items: list[dict] = []
    for row in rows:
        ser = _serialize(row, comment_count=counts.get(row.id, 0))
        ser["comments"] = comments_by_item.get(row.id, [])
        if row.horizon == "week":
            week_items.append(ser)
        elif row.horizon == "month":
            month_items.append(ser)
        else:
            quarter_items.append(ser)

    return {
        "user_id": target,
        "periods": periods,
        "week": week_items,
        "month": month_items,
        "quarter": quarter_items,
    }


@router.get("/overview")
def overview(
    week_start: str = Query(..., description="Saturday start of the selected week (YYYY-MM-DD)"),
    _viewer: User = Depends(get_current_work_plan_reviewer),
    db: Session = Depends(get_db),
) -> dict:
    ws = _parse_iso_date(week_start, field="week_start")
    if ws != _saturday_of(ws):
        raise HTTPException(status_code=400, detail="week_start must be a Saturday")
    periods = _periods_for_week(ws)
    week_keys = _legacy_week_period_keys(ws)

    users = db.execute(
        select(User)
        .where(
            User.is_active.is_(True),
            User.role.in_(("member", "user")),
        )
        .order_by(User.email.asc())
    ).scalars().all()

    items = db.execute(
        select(WorkPlanItem)
        .where(
            WorkPlanItem.horizon == "week",
            WorkPlanItem.period_start.in_(week_keys),
        )
        .order_by(WorkPlanItem.sort_order.asc(), WorkPlanItem.created_at.asc())
    ).scalars().all()

    counts = _comment_counts(db, [i.id for i in items])
    by_user: dict[str, list[dict]] = {}
    for item in items:
        by_user.setdefault(item.user_id, []).append(
            _serialize(item, comment_count=counts.get(item.id, 0))
        )

    rows = []
    for u in users:
        user_items = by_user.get(u.id, [])
        counts_map = {
            "current_job": 0,
            "self_study": 0,
            "work_plans": 0,
            "done": 0,
            "total": 0,
        }
        for it in user_items:
            counts_map["total"] += 1
            if it.get("done"):
                counts_map["done"] += 1
            cat = it.get("category") or ""
            if cat in counts_map:
                counts_map[cat] += 1
        rows.append(
            {
                "user_id": u.id,
                "user_name": u.name or "",
                "user_email": u.email,
                "avatar_url": u.avatar_url,
                "counts": counts_map,
                "items": user_items,
            }
        )

    return {"periods": periods, "users": rows}


@router.get("/{item_id}")
def get_item(
    item_id: str,
    user: User = Depends(get_current_work_plan_user),
    db: Session = Depends(get_db),
) -> dict:
    item = _get_item_or_404(db, item_id)
    _assert_can_view(user, item)
    counts = _comment_counts(db, [item.id])
    return _serialize(item, comment_count=counts.get(item.id, 0))


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_item(
    payload: WorkPlanItemCreate,
    user: User = Depends(get_current_work_plan_user),
    db: Session = Depends(get_db),
) -> dict:
    _validate_create(payload)
    target = _resolve_target_user_id(user, payload.user_id, write=True)

    if target != user.id:
        if not db.get(User, target):
            raise HTTPException(status_code=404, detail="User not found")

    q = select(func.coalesce(func.max(WorkPlanItem.sort_order), -1)).where(
        WorkPlanItem.user_id == target,
        WorkPlanItem.horizon == payload.horizon,
        WorkPlanItem.period_start == payload.period_start,
    )
    if payload.horizon == "week":
        q = q.where(WorkPlanItem.category == payload.category)
    max_order = db.execute(q).scalar_one()

    item = WorkPlanItem(
        user_id=target,
        horizon=payload.horizon,
        period_start=payload.period_start,
        category=payload.category if payload.horizon == "week" else None,
        title=payload.title.strip(),
        contents=(payload.contents or None),
        done=False,
        sort_order=int(max_order) + 1,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _serialize(item, comment_count=0)


@router.patch("/{item_id}")
def update_item(
    item_id: str,
    payload: WorkPlanItemUpdate,
    user: User = Depends(get_current_work_plan_user),
    db: Session = Depends(get_db),
) -> dict:
    item = _get_item_or_404(db, item_id)
    _assert_can_mutate(user, item)

    data = payload.model_dump(exclude_unset=True)
    if "title" in data:
        title = (data["title"] or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="title cannot be empty")
        item.title = title
    if "contents" in data:
        item.contents = data["contents"]
    if "done" in data and data["done"] is not None:
        item.done = bool(data["done"])
    if "sort_order" in data and data["sort_order"] is not None:
        item.sort_order = int(data["sort_order"])
    if "category" in data:
        if item.horizon != "week":
            raise HTTPException(
                status_code=400, detail="category is only allowed for week items"
            )
        cat = data["category"]
        if cat not in WEEK_CATEGORIES:
            raise HTTPException(
                status_code=400,
                detail="category must be current_job, self_study, or work_plans",
            )
        item.category = cat

    item.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(item)
    counts = _comment_counts(db, [item.id])
    return _serialize(item, comment_count=counts.get(item.id, 0))


@router.post("/{item_id}/next-week")
def move_to_next_week(
    item_id: str,
    user: User = Depends(get_current_work_plan_user),
    db: Session = Depends(get_db),
) -> dict:
    item = _get_item_or_404(db, item_id)
    _assert_can_mutate(user, item)
    if item.horizon != "week":
        raise HTTPException(status_code=400, detail="Only weekly goals can be sent to next week")

    current = _parse_iso_date(item.period_start, field="period_start")
    next_start = _saturday_of(current) + timedelta(days=7)

    max_order = db.execute(
        select(func.coalesce(func.max(WorkPlanItem.sort_order), -1)).where(
            WorkPlanItem.user_id == item.user_id,
            WorkPlanItem.horizon == "week",
            WorkPlanItem.period_start == next_start.isoformat(),
            WorkPlanItem.category == item.category,
        )
    ).scalar_one()

    item.period_start = next_start.isoformat()
    item.sort_order = int(max_order) + 1
    item.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(item)
    counts = _comment_counts(db, [item.id])
    return _serialize(item, comment_count=counts.get(item.id, 0))


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def delete_item(
    item_id: str,
    user: User = Depends(get_current_work_plan_user),
    db: Session = Depends(get_db),
) -> Response:
    item = _get_item_or_404(db, item_id)
    _assert_can_mutate(user, item)
    db.execute(sa_delete(WorkPlanComment).where(WorkPlanComment.item_id == item_id))
    db.delete(item)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{item_id}/comments")
def list_comments(
    item_id: str,
    user: User = Depends(get_current_work_plan_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    item = _get_item_or_404(db, item_id)
    _assert_can_view(user, item)
    comments = db.execute(
        select(WorkPlanComment)
        .where(WorkPlanComment.item_id == item_id)
        .order_by(WorkPlanComment.created_at.asc())
    ).scalars().all()
    author_ids = {c.author_id for c in comments}
    authors: dict[str, User] = {}
    if author_ids:
        authors = {
            u.id: u
            for u in db.execute(select(User).where(User.id.in_(author_ids))).scalars().all()
        }
    return [_serialize_comment(c, authors.get(c.author_id)) for c in comments]


@router.post("/{item_id}/comments", status_code=status.HTTP_201_CREATED)
def create_comment(
    item_id: str,
    payload: WorkPlanCommentCreate,
    user: User = Depends(get_current_work_plan_user),
    db: Session = Depends(get_db),
) -> dict:
    item = _get_item_or_404(db, item_id)
    _assert_can_comment(user, item)
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="body is required")
    comment = WorkPlanComment(item_id=item_id, author_id=user.id, body=body)
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return _serialize_comment(comment, user)


@router.delete(
    "/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_comment(
    comment_id: str,
    user: User = Depends(get_current_work_plan_user),
    db: Session = Depends(get_db),
) -> Response:
    comment = db.get(WorkPlanComment, comment_id)
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")
    item = _get_item_or_404(db, comment.item_id)
    # Author, plan owner, admin or manager may delete. A reviewer deletes only their own.
    if (
        comment.author_id != user.id
        and item.user_id != user.id
        and not can_manage_others_work_plans(user)
    ):
        raise HTTPException(status_code=403, detail="Not allowed")
    db.delete(comment)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
