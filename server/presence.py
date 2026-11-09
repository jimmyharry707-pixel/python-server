"""Client online presence via last_seen upserts.

Fleet "online" is derived from ``Client.last_seen`` (see admin/team
``ONLINE_WINDOW``). Sync push/pull and license activate refresh that timestamp.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from .licensing import as_naive_utc, utcnow
from .models import Client

# A client seen within this long counts as online.
ONLINE_WINDOW = timedelta(minutes=10)


def touch_client_presence(
    db: Session,
    *,
    device_id: str | None,
    user_id: str | None,
    app_version: str = "",
    os_name: str = "",
    hostname: str = "",
) -> None:
    """Upsert a fleet ``Client`` row and bump ``last_seen``.

    No-op when ``device_id`` is missing. Does not commit — caller owns the
    transaction.
    """
    if not device_id:
        return
    client = db.get(Client, device_id)
    now = as_naive_utc(utcnow())
    if client is None:
        client = Client(device_id=device_id, first_seen=now)
        db.add(client)
    if user_id:
        client.user_id = user_id
    client.app_version = app_version or client.app_version or ""
    client.os_name = os_name or client.os_name or ""
    client.hostname = hostname or client.hostname or ""
    client.last_seen = now
