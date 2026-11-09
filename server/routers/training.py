"""Supporter training course: Markdown lessons, their images, and progress.

Every signed-in user can read the course and record their own progress;
admins and managers (the admin site) author it. They can also replace the
course with the deployed ``docs/bidder-training`` files (``/docs-update``).
"""

from __future__ import annotations

import re
from pathlib import PurePath

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import TrainingAsset, TrainingLesson, TrainingProgress, User
from ..security import get_current_admin, get_current_user, is_admin_site_user
from ..training_seed import ASSET_URL_PREFIX, load_seed

router = APIRouter(prefix="/training", tags=["training"])

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,95}$")
_BODY_MAX_CHARS = 500_000
_ASSET_MAX_BYTES = 5 * 1024 * 1024
# SVG is deliberately excluded: served from our origin it could run script.
_IMAGE_SIGNATURES = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),
}
_EXTENSIONS = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}


class LessonIn(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    slug: str | None = Field(default=None, max_length=96)
    body_md: str = Field(default="", max_length=_BODY_MAX_CHARS)


class ReorderIn(BaseModel):
    ids: list[str]


def _lesson_out(lesson: TrainingLesson) -> dict:
    return {
        "id": lesson.id,
        "slug": lesson.slug,
        "title": lesson.title,
        "body_md": lesson.body_md,
        "position": lesson.position,
        "updated_at": lesson.updated_at.isoformat() if lesson.updated_at else None,
    }


def _asset_out(asset: TrainingAsset) -> dict:
    return {
        "name": asset.name,
        "content_type": asset.content_type,
        "size": asset.size,
        "url": ASSET_URL_PREFIX + asset.name,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
    }


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:96] or "lesson"


def _unique_slug(db: Session, wanted: str, lesson_id: str | None = None) -> str:
    slug, n = wanted, 2
    while True:
        clash = db.execute(
            select(TrainingLesson.id).where(TrainingLesson.slug == slug)
        ).scalar_one_or_none()
        if clash is None or clash == lesson_id:
            return slug
        slug = f"{wanted[:90]}-{n}"
        n += 1


def _get_lesson(db: Session, lesson_id: str) -> TrainingLesson:
    lesson = db.get(TrainingLesson, lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    return lesson


@router.get("/course")
def course(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    lessons = db.execute(
        select(TrainingLesson).order_by(TrainingLesson.position, TrainingLesson.title)
    ).scalars()
    completed = db.execute(
        select(TrainingProgress.lesson_id).where(TrainingProgress.user_id == user.id)
    ).scalars()
    return {
        "lessons": [_lesson_out(lesson) for lesson in lessons],
        "completed": list(completed),
        "can_edit": is_admin_site_user(user),
    }


@router.post("/lessons/{lesson_id}/complete", status_code=204)
def complete_lesson(
    lesson_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Response:
    _get_lesson(db, lesson_id)
    exists = db.execute(
        select(TrainingProgress.id).where(
            TrainingProgress.user_id == user.id, TrainingProgress.lesson_id == lesson_id
        )
    ).scalar_one_or_none()
    if exists is None:
        db.add(TrainingProgress(user_id=user.id, lesson_id=lesson_id))
        db.commit()
    return Response(status_code=204)


@router.delete("/lessons/{lesson_id}/complete", status_code=204)
def uncomplete_lesson(
    lesson_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Response:
    db.execute(
        delete(TrainingProgress).where(
            TrainingProgress.user_id == user.id, TrainingProgress.lesson_id == lesson_id
        )
    )
    db.commit()
    return Response(status_code=204)


@router.post("/lessons")
def create_lesson(
    payload: LessonIn, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)
) -> dict:
    wanted = payload.slug or _slugify(payload.title)
    if not _SLUG_RE.match(wanted):
        raise HTTPException(status_code=422, detail="Use lowercase letters, numbers and dashes")
    last = db.execute(select(func.max(TrainingLesson.position))).scalar_one_or_none()
    lesson = TrainingLesson(
        slug=_unique_slug(db, wanted),
        title=payload.title.strip(),
        body_md=payload.body_md,
        position=(last if last is not None else -1) + 1,
        updated_by=admin.id,
    )
    db.add(lesson)
    db.commit()
    db.refresh(lesson)
    return _lesson_out(lesson)


@router.put("/lessons/{lesson_id}")
def update_lesson(
    lesson_id: str,
    payload: LessonIn,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    lesson = _get_lesson(db, lesson_id)
    if payload.slug is not None and payload.slug != lesson.slug:
        if not _SLUG_RE.match(payload.slug):
            raise HTTPException(
                status_code=422, detail="Use lowercase letters, numbers and dashes"
            )
        if _unique_slug(db, payload.slug, lesson.id) != payload.slug:
            raise HTTPException(status_code=409, detail="Another lesson already uses that URL")
        lesson.slug = payload.slug
    lesson.title = payload.title.strip()
    lesson.body_md = payload.body_md
    lesson.updated_by = admin.id
    db.commit()
    db.refresh(lesson)
    return _lesson_out(lesson)


@router.delete("/lessons/{lesson_id}", status_code=204)
def delete_lesson(
    lesson_id: str, _: User = Depends(get_current_admin), db: Session = Depends(get_db)
) -> Response:
    lesson = _get_lesson(db, lesson_id)
    # Explicit: SQLite doesn't enforce the FK cascade.
    db.execute(delete(TrainingProgress).where(TrainingProgress.lesson_id == lesson.id))
    db.delete(lesson)
    db.commit()
    return Response(status_code=204)


@router.post("/lessons/reorder")
def reorder_lessons(
    payload: ReorderIn, _: User = Depends(get_current_admin), db: Session = Depends(get_db)
) -> dict:
    lessons = {lesson.id: lesson for lesson in db.execute(select(TrainingLesson)).scalars()}
    if set(payload.ids) != set(lessons) or len(payload.ids) != len(lessons):
        raise HTTPException(status_code=409, detail="Lesson list changed; reload and try again")
    for position, lesson_id in enumerate(payload.ids):
        lessons[lesson_id].position = position
    db.commit()
    return {"ok": True}


def _compare_with_docs(db: Session):
    """What the deployed docs would change in the course.

    Lessons are matched by their URL (slug), so an updated lesson keeps its id
    and supporters' progress on it. Lessons and images that aren't in the docs
    are left alone.
    """
    seed_lessons, seed_assets = load_seed()
    if not seed_lessons:
        raise HTTPException(
            status_code=503, detail="The training docs aren't on the server. Check the deploy."
        )
    current = {
        lesson.slug: lesson
        for lesson in db.execute(
            select(TrainingLesson).order_by(TrainingLesson.position, TrainingLesson.title)
        ).scalars()
    }
    stored = {
        name: data
        for name, data in db.execute(select(TrainingAsset.name, TrainingAsset.data))
    }

    lessons = []
    for position, seed in enumerate(seed_lessons):
        lesson = current.get(seed.slug)
        if lesson is None:
            status = "new"
        elif lesson.title != seed.title or lesson.body_md != seed.body_md:
            status = "changed"
        else:
            status = "moved" if lesson.position != position else "same"
        lessons.append(
            {
                "slug": seed.slug,
                "title": seed.title,
                "status": status,
                # Docs imports save no author, so an author means a website edit.
                "edited_on_site": status == "changed" and lesson.updated_by is not None,
            }
        )
    seed_slugs = {seed.slug for seed in seed_lessons}
    kept = [lesson for slug, lesson in current.items() if slug not in seed_slugs]
    new_images = [a.name for a in seed_assets if a.name not in stored]
    changed_images = [a.name for a in seed_assets if a.name in stored and stored[a.name] != a.data]
    summary = {
        "lessons": lessons,
        "kept": [{"slug": lesson.slug, "title": lesson.title} for lesson in kept],
        "images": {
            "new": new_images,
            "changed": changed_images,
            "same": len(seed_assets) - len(new_images) - len(changed_images),
        },
    }
    summary["up_to_date"] = not new_images and not changed_images and all(
        x["status"] == "same" for x in lessons
    ) and all(  # lessons kept from the website already sit after the docs ones
        lesson.position >= len(seed_lessons) for lesson in kept
    )
    return summary, seed_lessons, seed_assets, current, kept


@router.get("/docs-update")
def preview_docs_update(
    _: User = Depends(get_current_admin), db: Session = Depends(get_db)
) -> dict:
    return _compare_with_docs(db)[0]


@router.post("/docs-update")
def apply_docs_update(
    _: User = Depends(get_current_admin), db: Session = Depends(get_db)
) -> dict:
    """Replace the course with the deployed docs, in the docs' order.

    Lessons only on the website move after the docs lessons; their content
    and every image not in the docs stay as they are.
    """
    summary, seed_lessons, seed_assets, current, kept = _compare_with_docs(db)
    for position, seed in enumerate(seed_lessons):
        lesson = current.get(seed.slug)
        if lesson is None:
            db.add(
                TrainingLesson(
                    slug=seed.slug, title=seed.title, body_md=seed.body_md, position=position
                )
            )
            continue
        if lesson.title != seed.title or lesson.body_md != seed.body_md:
            lesson.title = seed.title
            lesson.body_md = seed.body_md
            lesson.updated_by = None
        lesson.position = position
    for offset, lesson in enumerate(kept):
        lesson.position = len(seed_lessons) + offset
    changed, new = set(summary["images"]["changed"]), set(summary["images"]["new"])
    for seed in seed_assets:
        if seed.name in changed:
            asset = db.get(TrainingAsset, seed.name)
            asset.content_type = seed.content_type
            asset.data = seed.data
            asset.size = len(seed.data)
        elif seed.name in new:
            db.add(
                TrainingAsset(
                    name=seed.name,
                    content_type=seed.content_type,
                    data=seed.data,
                    size=len(seed.data),
                )
            )
    db.commit()
    return summary


@router.get("/assets")
def list_assets(_: User = Depends(get_current_admin), db: Session = Depends(get_db)) -> list:
    assets = db.execute(select(TrainingAsset).order_by(TrainingAsset.name)).scalars()
    return [_asset_out(asset) for asset in assets]


def _sniff_image(data: bytes) -> str | None:
    for content_type, signatures in _IMAGE_SIGNATURES.items():
        if any(data.startswith(sig) for sig in signatures):
            if content_type == "image/webp" and data[8:12] != b"WEBP":
                continue
            return content_type
    return None


def _asset_name(db: Session, filename: str, content_type: str) -> str:
    stem = re.sub(r"[^a-z0-9._-]+", "-", PurePath(filename or "image").stem.lower()).strip("-.")
    stem = (stem or "image")[:120]
    ext = _EXTENSIONS[content_type]
    name, n = f"{stem}{ext}", 2
    while db.get(TrainingAsset, name) is not None:
        name = f"{stem}-{n}{ext}"
        n += 1
    return name


@router.post("/assets")
async def upload_asset(
    file: UploadFile = File(...),
    _: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    data = await file.read(_ASSET_MAX_BYTES + 1)
    if len(data) > _ASSET_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Images must be 5 MB or smaller")
    content_type = _sniff_image(data)
    if content_type is None:
        raise HTTPException(status_code=415, detail="Upload a PNG, JPEG, GIF or WebP image")
    asset = TrainingAsset(
        name=_asset_name(db, file.filename or "", content_type),
        content_type=content_type,
        data=data,
        size=len(data),
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return _asset_out(asset)


@router.delete("/assets/{name}", status_code=204)
def delete_asset(
    name: str, _: User = Depends(get_current_admin), db: Session = Depends(get_db)
) -> Response:
    asset = db.get(TrainingAsset, name)
    if asset is None:
        raise HTTPException(status_code=404, detail="Image not found")
    db.delete(asset)
    db.commit()
    return Response(status_code=204)


@router.get("/assets/{name}")
def get_asset(
    name: str, _: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Response:
    asset = db.get(TrainingAsset, name)
    if asset is None:
        raise HTTPException(status_code=404, detail="Image not found")
    return Response(
        content=asset.data,
        media_type=asset.content_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )
