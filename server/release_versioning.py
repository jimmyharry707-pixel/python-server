"""Version comparison for the release manifest (``Release.version`` strings).

Kept independent of the client's ``version.py`` — the server is a separate
deployable and must not import client source.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Release


def parse_version_tuple(value: str, parts: int = 3) -> tuple[int, ...]:
    """Coerce a version-ish string into a fixed-length int tuple for comparison.

    Only the leading dot-separated numeric run of each chunk is meaningful (a
    trailing ``-rc1``/``-dirty`` label is dropped); non-numeric/empty chunks
    become 0, and the result is padded/truncated to ``parts`` entries.
    """
    result = []
    for chunk in str(value or "0").split(".")[:parts]:
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        result.append(int(digits) if digits else 0)
    while len(result) < parts:
        result.append(0)
    return tuple(result)


def latest_for_channel(db: Session, channel: str) -> Release | None:
    """The release with the highest parsed version for ``channel``.

    Ties (equal parsed version) break on the most recently published row.
    """
    releases = (
        db.execute(select(Release).where(Release.channel == channel))
        .scalars()
        .all()
    )
    if not releases:
        return None
    return max(
        releases,
        key=lambda r: (parse_version_tuple(r.version), r.published_at),
    )
