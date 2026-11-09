"""Initial content for the supporter training course.

The course is authored in ``docs/bidder-training/`` (one Markdown file per
chapter plus ``images/``). On first start the server imports it into the
``training_lessons`` / ``training_assets`` tables; from then on the database is
the source of truth and admins edit it on the website. Deploying edited docs
changes nothing by itself: an admin applies them with **Update from docs** in
the Training editor (``POST /training/docs-update``).

While importing, repo-relative links are rewritten to web paths so the stored
Markdown is exactly what the editor shows:

* ``04-screening.md#46-x`` → ``/training/screening#46-x``
* ``images/foo.png``       → ``/api/v1/training/assets/foo.png``

The per-chapter "← Contents · Next →" lines are dropped (the course wizard has
its own navigation), as are links to the manager-only ``REVISION_NOTES.md``.
"""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Connection, text

ASSET_URL_PREFIX = "/api/v1/training/assets/"
COURSE_URL_PREFIX = "/training/"

_SERVER_DIR = Path(__file__).resolve().parent
# Docker copies the docs folder next to the server package (see Dockerfile);
# a source checkout reads it straight from docs/.
_SEED_DIRS = (
    _SERVER_DIR / "training_seed",
    _SERVER_DIR.parent / "docs" / "bidder-training",
)
_SKIP_FILES = {"REVISION_NOTES.md"}
_MD_LINK = re.compile(r"\]\((?!https?:|/|#)([^)#\s]+\.md)(#[^)\s]*)?\)")
_IMG_LINK = re.compile(r"\]\(images/([^)\s]+)\)")


@dataclass
class SeedLesson:
    slug: str
    title: str
    body_md: str


@dataclass
class SeedAsset:
    name: str
    content_type: str
    data: bytes


def slug_for_file(filename: str) -> str:
    stem = Path(filename).stem
    if stem.upper() == "README":
        return "overview"
    stem = re.sub(r"^\d+-", "", stem)
    stem = re.sub(r"^appendix-", "", stem)
    return stem.lower()


def _title_and_body(raw: str, slug: str) -> tuple[str, str]:
    lines = raw.splitlines()
    title = slug.replace("-", " ").capitalize()
    if lines and lines[0].startswith("# "):
        heading = lines.pop(0)[2:].strip()
        # "4. Screen the job…" / "Appendix A — Checklists" → the bare name; the
        # wizard numbers steps itself.
        heading = re.sub(r"^\d+\.\s+", "", heading)
        heading = re.sub(r"^Appendix [A-Z]\s+[—-]\s+", "", heading)
        title = "Course overview" if slug == "overview" else heading
    kept = [
        line
        for line in lines
        if not line.startswith("[←") and "REVISION_NOTES.md" not in line
    ]
    return title, "\n".join(kept).strip() + "\n"


def _rewrite_links(body: str) -> str:
    def md(match: re.Match[str]) -> str:
        return f"]({COURSE_URL_PREFIX}{slug_for_file(match.group(1))}{match.group(2) or ''})"

    body = _MD_LINK.sub(md, body)
    return _IMG_LINK.sub(lambda m: f"]({ASSET_URL_PREFIX}{m.group(1)})", body)


def seed_dir() -> Path | None:
    return next((d for d in _SEED_DIRS if (d / "README.md").is_file()), None)


def load_seed(directory: Path | None = None) -> tuple[list[SeedLesson], list[SeedAsset]]:
    directory = directory or seed_dir()
    if directory is None:
        return [], []

    files = sorted(
        p for p in directory.glob("*.md") if p.name not in _SKIP_FILES
    )
    # README first, then numbered chapters, then appendices.
    files.sort(key=lambda p: (p.stem.upper() != "README", p.stem.startswith("appendix"), p.stem))

    lessons = []
    for path in files:
        slug = slug_for_file(path.name)
        title, body = _title_and_body(path.read_text(encoding="utf-8"), slug)
        lessons.append(SeedLesson(slug=slug, title=title, body_md=_rewrite_links(body)))

    assets = []
    image_dir = directory / "images"
    if image_dir.is_dir():
        for path in sorted(image_dir.iterdir()):
            content_type = mimetypes.guess_type(path.name)[0] or ""
            if path.is_file() and content_type.startswith("image/"):
                assets.append(SeedAsset(path.name, content_type, path.read_bytes()))
    return lessons, assets


def seed_training_course(conn: Connection) -> bool:
    """Import the bundled course into an empty course.

    Returns False when no seed content was found, so the caller can retry once
    the files are deployed.
    """
    if conn.execute(text("SELECT COUNT(*) FROM training_lessons")).scalar_one():
        return True
    lessons, assets = load_seed()
    if not lessons:
        return False

    import uuid
    from datetime import datetime

    now = datetime.utcnow()
    for position, lesson in enumerate(lessons):
        conn.execute(
            text(
                "INSERT INTO training_lessons (id, slug, title, body_md, position, updated_at) "
                "VALUES (:id, :slug, :title, :body, :pos, :now)"
            ),
            {
                "id": str(uuid.uuid4()),
                "slug": lesson.slug,
                "title": lesson.title,
                "body": lesson.body_md,
                "pos": position,
                "now": now,
            },
        )
    existing = {row[0] for row in conn.execute(text("SELECT name FROM training_assets"))}
    for asset in assets:
        if asset.name in existing:
            continue
        conn.execute(
            text(
                "INSERT INTO training_assets (name, content_type, data, size, updated_at) "
                "VALUES (:name, :ctype, :data, :size, :now)"
            ),
            {
                "name": asset.name,
                "ctype": asset.content_type,
                "data": asset.data,
                "size": len(asset.data),
                "now": now,
            },
        )
    return True
