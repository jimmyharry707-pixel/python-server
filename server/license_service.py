"""The license service shared by CareerCraft, Sidekick and SnowLink.

A *license* is a key for one app (``License.product``) with a seat limit and
an optional expiry. A *seat* is one machine using the key, identified by a
hardware fingerprint. Activating binds a machine to a seat and returns a
signed lease plus a seat secret the machine renews it with. The lease lets
the app run offline for the license's offline days, and never past expiry.

Every refusal is a normal answer (``valid: False``) with a machine-readable
``code`` next to the human ``reason``, so an app can tell "the key expired or
is suspended: keep the activation, it may come back" from "this machine was
released or blocked: ask for the key". Anything else (network, 5xx, 429,
401) means the server could not say, and the app should keep its lease.
"""

from __future__ import annotations

import json
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, Iterable

from fastapi import HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .key_colors import customer_group, pick_key_color
from .licensing import (
    ProductNotConfigured,
    as_naive_utc,
    ensure_aware,
    get_active_public_pem,
    hash_key,
    hash_seat_secret,
    new_seat_secret,
    normalize_license_key,
    sign_lease,
    signing_kid,
    utcnow,
    verify_lease,
)
from .models import Client, License, LicenseAudit, LicenseSeat, LicenseSeatUser, User
from .presence import ONLINE_WINDOW, touch_client_presence
from .products import Product, get_product, product_name

# Refusal codes.
UNKNOWN_KEY = "unknown_key"
WRONG_PRODUCT = "wrong_product"
KEY_REQUIRED = "key_required"
INVALID_FINGERPRINT = "invalid_fingerprint"
SUSPENDED = "suspended"
EXPIRED = "expired"
SEAT_LIMIT = "seat_limit"
SEAT_BLOCKED = "seat_blocked"
SEAT_RELEASED = "seat_released"
NOT_ACTIVATED = "not_activated"
FINGERPRINT_MISMATCH = "fingerprint_mismatch"

# The license itself is off; the machine's activation is still good, so an
# app should keep it and try again later (an admin may extend or resume).
LICENSE_LEVEL_CODES = frozenset({SUSPENDED, EXPIRED})

EXPIRING_SOON = timedelta(days=14)

RELEASED_BY_ADMIN = "released_by_admin"
RELEASED_BY_USER = "released_by_user"
BLOCKED_BY_ADMIN = "blocked_by_admin"

_settings = get_settings()


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------
def client_ip(request: Request) -> str:
    """The caller's address as nginx saw it.

    nginx sets X-Real-IP to the connecting address and *appends* it to
    X-Forwarded-For, so only the last X-Forwarded-For entry is trustworthy;
    the first one is whatever the client sent.
    """
    real = (request.headers.get("x-real-ip") or "").strip()
    if real:
        return real[:64]
    forwarded = request.headers.get("x-forwarded-for") or ""
    hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
    if hops:
        return hops[-1][:64]
    if request.client and request.client.host:
        return request.client.host[:64]
    return "unknown"


_rate_lock = Lock()
_rate_buckets: dict[str, list[float]] = defaultdict(list)


def rate_limit(ip: str) -> None:
    limit = max(1, _settings.license_rate_limit_per_minute)
    now = time.monotonic()
    with _rate_lock:
        bucket = _rate_buckets[ip]
        cutoff = now - 60.0
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)
        if len(bucket) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many license requests; try again shortly",
            )
        bucket.append(now)


def reset_rate_limits() -> None:
    with _rate_lock:
        _rate_buckets.clear()


@dataclass
class Machine:
    """What an app reports about its machine. Empty values never overwrite."""

    hostname: str = ""
    os_name: str = ""
    os_version: str = ""
    app_version: str = ""
    os_user: str = ""

    @classmethod
    def from_request(cls, req: Any) -> "Machine":
        def clip(name: str, limit: int) -> str:
            return str(getattr(req, name, None) or "").strip()[:limit]

        return cls(
            hostname=clip("hostname", 255),
            os_name=clip("os_name", 128),
            os_version=clip("os_version", 128),
            app_version=clip("app_version", 64),
            os_user=clip("os_user", 255),
        )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _now() -> datetime:
    """Naive UTC, the way the database stores it."""
    return as_naive_utc(utcnow())


def iso(dt: datetime | None) -> str | None:
    aware = ensure_aware(dt)
    return aware.strftime("%Y-%m-%dT%H:%M:%SZ") if aware else None


def parse_features(raw: str | None) -> list[Any] | dict[str, Any]:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def features_json(features: list[Any] | dict[str, Any] | None) -> str:
    try:
        return json.dumps(features if features is not None else [])
    except Exception:
        return "[]"


def offline_days(lic: License) -> int:
    if lic.grace_days is not None and lic.grace_days > 0:
        return lic.grace_days
    return _settings.effective_lease_ttl_days


def seat_state(seat: LicenseSeat) -> str:
    if seat.blocked:
        return "blocked"
    if seat.revoked or seat.revoked_at is not None:
        return "released"
    return "active"


def key_color_for(db: Session, lic: License) -> int:
    """A color for ``lic`` that its customer's other keys use least.

    Its own color is kept when no other key of the customer has it.
    """
    group = customer_group(lic.customer)
    used = [
        color
        for license_id, customer, color in db.execute(
            select(License.id, License.customer, License.color)
        ).all()
        if license_id != lic.id and color is not None and customer_group(customer) == group
    ]
    if lic.color is not None and lic.color not in used:
        return lic.color
    return pick_key_color(used, lic.customer)


def seats_used(db: Session, license_id: str) -> int:
    return db.execute(
        select(func.count())
        .select_from(LicenseSeat)
        .where(
            LicenseSeat.license_id == license_id,
            LicenseSeat.revoked.is_(False),
            LicenseSeat.revoked_at.is_(None),
        )
    ).scalar_one()


def license_refusal(lic: License) -> tuple[str, str] | None:
    """Why ``lic`` can't be used right now, or None when it can."""
    if not lic.is_active or lic.revoked_at is not None:
        return SUSPENDED, "This license is suspended"
    expires = ensure_aware(lic.expires_at)
    if expires is not None and expires <= utcnow():
        return EXPIRED, "License has expired"
    return None


def license_status(lic: License, *, ever_used: bool, now: datetime | None = None) -> str:
    """suspended | expired | expiring | unused | active."""
    now = ensure_aware(now) or utcnow()
    if not lic.is_active or lic.revoked_at is not None:
        return "suspended"
    expires = ensure_aware(lic.expires_at)
    if expires is not None and expires <= now:
        return "expired"
    if expires is not None and expires <= now + EXPIRING_SOON:
        return "expiring"
    if not ever_used:
        return "unused"
    return "active"


def days_left(lic: License, now: datetime | None = None) -> int | None:
    """Whole days until expiry (negative once expired); None when it never expires."""
    expires = ensure_aware(lic.expires_at)
    if expires is None:
        return None
    seconds = (expires - (ensure_aware(now) or utcnow())).total_seconds()
    return math.ceil(seconds / 86400) if seconds > 0 else math.floor(seconds / 86400)


def audit(
    db: Session,
    *,
    event: str,
    product: str | None = None,
    license_id: str | None = None,
    seat_id: int | None = None,
    user_id: str | None = None,
    ip: str | None = None,
    detail: dict | str | None = None,
) -> None:
    if product is None and license_id:
        lic = db.get(License, license_id)
        product = lic.product if lic is not None else None
    detail_str = detail if isinstance(detail, str) else (
        json.dumps(detail) if detail is not None else None
    )
    db.add(
        LicenseAudit(
            event=event,
            product=product,
            license_id=license_id,
            seat_id=seat_id,
            user_id=user_id,
            ip=ip,
            detail=detail_str,
        )
    )


def _refuse(code: str, reason: str, **extra: Any) -> dict:
    return {"valid": False, "code": code, "reason": reason, **extra}


def _licensed_to(lic: License) -> str:
    return (lic.customer or lic.label or "").strip()


def _record_seat_user(
    db: Session, seat: LicenseSeat, user: User | None, os_user: str, now: datetime
) -> None:
    if user is not None:
        identity = f"user:{user.id}"
    elif os_user:
        identity = f"os:{os_user.lower()}"
    else:
        return
    row = db.execute(
        select(LicenseSeatUser).where(
            LicenseSeatUser.seat_id == seat.id, LicenseSeatUser.identity == identity
        )
    ).scalar_one_or_none()
    if row is None:
        db.add(
            LicenseSeatUser(
                seat_id=seat.id,
                identity=identity,
                user_id=user.id if user is not None else None,
                os_user=os_user,
                first_seen=now,
                last_seen=now,
            )
        )
    else:
        row.last_seen = now
        if os_user:
            row.os_user = os_user


def _check_in(
    db: Session,
    *,
    product: Product,
    seat: LicenseSeat,
    user: User | None,
    ip: str,
    machine: Machine,
    device_id: str,
) -> None:
    """Note that this machine called in, with whatever it said about itself."""
    now = _now()
    seat.last_ip = ip
    seat.last_seen_at = now
    for field in ("hostname", "os_name", "os_version", "app_version", "os_user"):
        value = getattr(machine, field)
        if value:
            setattr(seat, field, value)
    if device_id:
        seat.device_id = device_id[:128]
    _record_seat_user(db, seat, user, machine.os_user, now)
    if product.tracks_online:
        touch_client_presence(
            db,
            device_id=seat.device_id,
            user_id=user.id if user is not None else None,
            app_version=machine.app_version,
            os_name=machine.os_name,
            hostname=machine.hostname,
        )


def _issue_lease(db: Session, *, product: Product, lic: License, seat: LicenseSeat, user: User | None):
    try:
        token, lease_exp, jti = sign_lease(
            user_id=user.id if (user is not None and product.requires_account) else None,
            license_id=lic.id,
            seat_id=seat.id,
            device_fp=seat.hardware_fingerprint,
            plan=lic.plan or "standard",
            features=parse_features(lic.features),
            license_expires_at=lic.expires_at,
            lease_ttl_days=offline_days(lic),
            product=product.id,
        )
    except ProductNotConfigured as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Licensing for {product.name} is not set up on the server yet",
        ) from exc
    seat.token_jti = jti
    seat.lease_expires_at = as_naive_utc(lease_exp)
    seat.last_validated_at = _now()
    return token, lease_exp


def _lease_fields(product: Product, lic: License, token: str, lease_exp: datetime) -> dict:
    return {
        "valid": True,
        "product": product.id,
        "licensed_to": _licensed_to(lic),
        "lease_token": token,
        "lease_expires_at": lease_exp,
        "license_expires_at": ensure_aware(lic.expires_at),
        "server_time": utcnow(),
        "plan": lic.plan,
        "features": parse_features(lic.features),
        "public_key_pem": get_active_public_pem(product.id),
        "kid": signing_kid(product.id),
    }


def _find_seat(
    db: Session,
    *,
    product: Product,
    user: User | None,
    fingerprint: str,
    seat_secret: str,
) -> LicenseSeat | None:
    query = (
        select(LicenseSeat)
        .join(License, License.id == LicenseSeat.license_id)
        .where(
            LicenseSeat.hardware_fingerprint == fingerprint,
            LicenseSeat.seat_secret_hash == hash_seat_secret(seat_secret or ""),
            License.product == product.id,
        )
    )
    if product.requires_account:
        query = query.where(LicenseSeat.user_id == (user.id if user is not None else None))
    return db.execute(query).scalars().first()


# ---------------------------------------------------------------------------
# App-facing operations
# ---------------------------------------------------------------------------
def activate(
    db: Session,
    *,
    product: Product,
    user: User | None,
    ip: str,
    license_key: str,
    fingerprint: str,
    device_id: str = "",
    machine: Machine | None = None,
) -> dict:
    """Bind this machine to a seat of ``license_key`` and issue a lease."""
    machine = machine or Machine()
    user_id = user.id if user is not None else None
    fp = (fingerprint or "").strip()
    if len(fp) < 16 or len(fp) > 128:
        return _refuse(INVALID_FINGERPRINT, "Invalid hardware fingerprint")
    key = normalize_license_key(license_key)
    if not key:
        return _refuse(KEY_REQUIRED, "License key required")

    stmt = select(License).where(License.key_hash == hash_key(key))
    try:
        stmt = stmt.with_for_update()
    except Exception:
        pass
    lic = db.execute(stmt).scalar_one_or_none()

    def denied(code: str, reason: str, seat_id: int | None = None, **extra: Any) -> dict:
        audit(
            db,
            event="activate_denied",
            product=product.id,
            license_id=lic.id if lic is not None else None,
            seat_id=seat_id,
            user_id=user_id,
            ip=ip,
            detail={
                "code": code,
                "reason": reason,
                "hostname": machine.hostname or None,
                "os_user": machine.os_user or None,
            },
        )
        db.commit()
        return _refuse(code, reason, **extra)

    if lic is None:
        return denied(UNKNOWN_KEY, "Unknown license key")
    if lic.product != product.id:
        return denied(
            WRONG_PRODUCT,
            f"This license key is for {product_name(lic.product)}, not {product.name}",
        )
    refusal = license_refusal(lic)
    if refusal:
        return denied(*refusal)

    seat = db.execute(
        select(LicenseSeat).where(
            LicenseSeat.license_id == lic.id, LicenseSeat.hardware_fingerprint == fp
        )
    ).scalar_one_or_none()
    reactivated = False
    if seat is not None and seat.blocked:
        return denied(
            SEAT_BLOCKED,
            "This computer is blocked from this license. Ask your administrator.",
            seat_id=seat.id,
        )
    if seat is None or seat_state(seat) == "released":
        used = seats_used(db, lic.id)
        if used >= lic.seat_limit:
            return denied(
                SEAT_LIMIT,
                "Seat limit reached for this license",
                seat_id=seat.id if seat is not None else None,
                seat_limit=lic.seat_limit,
                seats_used=used,
            )
    if seat is None:
        seat = LicenseSeat(
            license_id=lic.id,
            user_id=user_id,
            hardware_fingerprint=fp,
            device_id=(device_id or fp)[:128],
            activated_at=_now(),
        )
        db.add(seat)
        db.flush()
    elif seat_state(seat) == "released":
        seat.revoked = False
        seat.revoked_at = None
        seat.revoked_reason = None
        reactivated = True

    if user_id:
        seat.user_id = user_id
    seat_secret = new_seat_secret()
    seat.seat_secret_hash = hash_seat_secret(seat_secret)
    _check_in(db, product=product, seat=seat, user=user, ip=ip, machine=machine, device_id=device_id)
    token, lease_exp = _issue_lease(db, product=product, lic=lic, seat=seat, user=user)
    audit(
        db,
        event="activate",
        product=product.id,
        license_id=lic.id,
        seat_id=seat.id,
        user_id=user_id,
        ip=ip,
        detail={
            "fingerprint": fp[:16] + "...",
            "hostname": machine.hostname or None,
            "os_user": machine.os_user or None,
            "reactivated": reactivated or None,
        },
    )
    db.commit()
    return {
        **_lease_fields(product, lic, token, lease_exp),
        "seat_secret": seat_secret,
        "seat_limit": lic.seat_limit,
        "seats_used": seats_used(db, lic.id),
    }


def refresh(
    db: Session,
    *,
    product: Product,
    user: User | None,
    ip: str,
    fingerprint: str,
    seat_secret: str,
    lease_token: str | None = None,
    device_id: str = "",
    machine: Machine | None = None,
) -> dict:
    """Issue a fresh lease for an activated machine."""
    machine = machine or Machine()
    user_id = user.id if user is not None else None
    fp = (fingerprint or "").strip()
    if not fp:
        return _refuse(INVALID_FINGERPRINT, "Invalid hardware fingerprint")

    seat = _find_seat(db, product=product, user=user, fingerprint=fp, seat_secret=seat_secret)
    if seat is None and lease_token:
        try:
            claims = verify_lease(
                lease_token,
                product=product.id,
                expected_user_id=user_id if product.requires_account else None,
            )
            seat = db.get(LicenseSeat, claims.get("seat_id"))
            if seat is not None and seat.seat_secret_hash != hash_seat_secret(seat_secret or ""):
                seat = None
        except ValueError:
            seat = None

    def denied(code: str, reason: str) -> dict:
        audit(
            db,
            event="refresh_denied",
            product=product.id,
            license_id=seat.license_id if seat is not None else None,
            seat_id=seat.id if seat is not None else None,
            user_id=user_id,
            ip=ip,
            detail={"code": code, "reason": reason, "os_user": machine.os_user or None},
        )
        db.commit()
        return _refuse(code, reason)

    if seat is None:
        return denied(NOT_ACTIVATED, "Device not activated")
    if seat.hardware_fingerprint != fp:
        return denied(FINGERPRINT_MISMATCH, "Device fingerprint mismatch")

    _check_in(db, product=product, seat=seat, user=user, ip=ip, machine=machine, device_id=device_id)
    if seat.blocked:
        return denied(
            SEAT_BLOCKED, "This computer is blocked from this license. Ask your administrator."
        )
    if seat_state(seat) == "released":
        return denied(
            SEAT_RELEASED,
            "This computer's seat was released. Enter the license key to activate it again.",
        )
    lic = db.get(License, seat.license_id)
    if lic is None:
        return denied(NOT_ACTIVATED, "Device not activated")
    refusal = license_refusal(lic)
    if refusal:
        return denied(*refusal)

    token, lease_exp = _issue_lease(db, product=product, lic=lic, seat=seat, user=user)
    audit(
        db,
        event="refresh",
        product=product.id,
        license_id=lic.id,
        seat_id=seat.id,
        user_id=user_id,
        ip=ip,
        detail={"os_user": machine.os_user} if machine.os_user else None,
    )
    db.commit()
    return _lease_fields(product, lic, token, lease_exp)


def release_own_seat(
    db: Session,
    *,
    product: Product,
    user: User | None,
    ip: str,
    fingerprint: str,
    seat_secret: str,
) -> dict:
    """The app gives its seat back (moving to another computer)."""
    fp = (fingerprint or "").strip()
    seat = _find_seat(db, product=product, user=user, fingerprint=fp, seat_secret=seat_secret)
    if seat is None:
        return _refuse(NOT_ACTIVATED, "Device not activated")
    if seat_state(seat) == "active":
        seat.revoked = True
        seat.revoked_at = _now()
        seat.revoked_reason = RELEASED_BY_USER
    seat.last_ip = ip
    seat.last_seen_at = _now()
    audit(
        db,
        event="release_seat",
        product=product.id,
        license_id=seat.license_id,
        seat_id=seat.id,
        user_id=user.id if user is not None else None,
        ip=ip,
        detail={"by": "app"},
    )
    db.commit()
    return {"valid": True}


def require_careercraft_seat(db: Session, *, token: str | None, user: User) -> LicenseSeat:
    """The live CareerCraft seat behind ``token``, or 402 saying why not."""

    def refuse(detail: str) -> HTTPException:
        return HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=detail)

    if not token:
        raise refuse("License lease token required (X-License-Token)")
    try:
        claims = verify_lease(token, product="careercraft", expected_user_id=user.id)
    except ValueError as exc:
        raise refuse(str(exc)) from exc
    seat_id = claims.get("seat_id")
    seat = db.get(LicenseSeat, seat_id) if seat_id is not None else None
    if seat is None or seat.user_id != user.id:
        raise refuse("Seat not found for this lease")
    if seat_state(seat) != "active":
        raise refuse("Seat has been revoked")
    lic = db.get(License, seat.license_id)
    if lic is None or lic.product != "careercraft":
        raise refuse("Seat not found for this lease")
    refusal = license_refusal(lic)
    if refusal:
        raise refuse(refusal[1])
    return seat


# ---------------------------------------------------------------------------
# Admin views
# ---------------------------------------------------------------------------
def _users_by_id(db: Session, ids: Iterable[str | None]) -> dict[str, User]:
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    rows = db.execute(select(User).where(User.id.in_(wanted))).scalars().all()
    return {u.id: u for u in rows}


def machine_rows(
    db: Session,
    seats: list[LicenseSeat],
    *,
    licenses: dict[str, License] | None = None,
    now: datetime | None = None,
) -> list[dict]:
    """Admin view of ``seats``: the machine, its users, when it was last seen."""
    if not seats:
        return []
    now = now or _now()
    if licenses is None:
        ids = {s.license_id for s in seats}
        licenses = {
            lic.id: lic
            for lic in db.execute(select(License).where(License.id.in_(ids))).scalars().all()
        }
    seat_ids = [s.id for s in seats]
    seat_users: dict[int, list[LicenseSeatUser]] = defaultdict(list)
    for row in db.execute(
        select(LicenseSeatUser)
        .where(LicenseSeatUser.seat_id.in_(seat_ids))
        .order_by(LicenseSeatUser.last_seen.desc())
    ).scalars():
        seat_users[row.seat_id].append(row)
    users = _users_by_id(
        db,
        [s.user_id for s in seats] + [r.user_id for rows in seat_users.values() for r in rows],
    )
    device_ids = {s.device_id for s in seats if s.device_id}
    clients = (
        {
            c.device_id: c
            for c in db.execute(select(Client).where(Client.device_id.in_(device_ids))).scalars()
        }
        if device_ids
        else {}
    )
    ever_used: set[str] = {s.license_id for s in seats}

    out = []
    for seat in seats:
        lic = licenses.get(seat.license_id)
        product = get_product(lic.product if lic is not None else None)
        tracks_online = bool(product and product.tracks_online)
        client = clients.get(seat.device_id) if tracks_online else None
        seen = [t for t in (seat.last_seen_at, seat.last_validated_at) if t]
        if client is not None and client.last_seen:
            seen.append(client.last_seen)
        last_seen = max(seen) if seen else None

        people = []
        for row in seat_users.get(seat.id, []):
            account = users.get(row.user_id) if row.user_id else None
            people.append(
                {
                    "user_id": row.user_id,
                    "email": account.email if account else None,
                    "name": account.name if account else None,
                    "avatar_url": account.avatar_url if account else None,
                    "os_user": row.os_user or None,
                    "deleted": bool(row.identity.startswith("user:") and account is None),
                    "first_seen": iso(row.first_seen),
                    "last_seen": iso(row.last_seen),
                    "current": bool(
                        (row.user_id and row.user_id == seat.user_id)
                        or (
                            not row.user_id
                            and row.os_user
                            and row.os_user.lower() == (seat.os_user or "").lower()
                        )
                    ),
                }
            )
        current = users.get(seat.user_id) if seat.user_id else None
        out.append(
            {
                "id": seat.id,
                "license_id": seat.license_id,
                "product": lic.product if lic is not None else None,
                "product_name": product_name(lic.product if lic is not None else None),
                "license_label": lic.label if lic is not None else "",
                "license_customer": lic.customer if lic is not None else "",
                "license_color": lic.color if lic is not None else None,
                "license_key_prefix": lic.key_prefix if lic is not None else "",
                "license_status": (
                    license_status(lic, ever_used=lic.id in ever_used, now=now)
                    if lic is not None
                    else None
                ),
                "hostname": seat.hostname or (client.hostname if client else "") or "",
                "os_name": seat.os_name or (client.os_name if client else "") or "",
                "os_version": seat.os_version or "",
                "app_version": seat.app_version or (client.app_version if client else "") or "",
                "os_user": seat.os_user or "",
                "hardware_fingerprint": seat.hardware_fingerprint,
                "device_id": seat.device_id,
                "state": seat_state(seat),
                "revoked": seat_state(seat) != "active",
                "revoked_at": iso(seat.revoked_at),
                "revoked_reason": seat.revoked_reason,
                "activated_at": iso(seat.activated_at),
                "last_validated_at": iso(seat.last_validated_at),
                "lease_expires_at": iso(seat.lease_expires_at),
                "last_seen_at": iso(last_seen),
                "last_ip": seat.last_ip,
                "tracks_online": tracks_online,
                "online": (
                    bool(client and client.last_seen and client.last_seen >= now - ONLINE_WINDOW)
                    if tracks_online
                    else None
                ),
                "user_id": seat.user_id,
                "user_email": current.email if current else None,
                "user_name": current.name if current else None,
                "users": people,
            }
        )
    return out


def license_rows(db: Session, licenses: list[License]) -> list[dict]:
    if not licenses:
        return []
    now = _now()
    by_id = {lic.id: lic for lic in licenses}
    seats = db.execute(
        select(LicenseSeat)
        .where(LicenseSeat.license_id.in_(list(by_id)))
        .order_by(LicenseSeat.activated_at.desc())
    ).scalars().all()
    machines: dict[str, list[dict]] = defaultdict(list)
    for row in machine_rows(db, list(seats), licenses=by_id, now=now):
        machines[row["license_id"]].append(row)

    out = []
    for lic in licenses:
        rows = machines.get(lic.id, [])
        active = [m for m in rows if m["state"] == "active"]
        seen = [m["last_seen_at"] for m in rows if m["last_seen_at"]]
        out.append(
            {
                "id": lic.id,
                "product": lic.product,
                "product_name": product_name(lic.product),
                "license_key": None,
                "key_prefix": lic.key_prefix,
                "label": lic.label,
                "customer": lic.customer or "",
                "color": lic.color,
                "notes": lic.notes or "",
                "plan": lic.plan,
                "features": parse_features(lic.features),
                "seat_limit": lic.seat_limit,
                "seats_used": len(active),
                "machines_total": len(rows),
                "is_active": bool(lic.is_active),
                "status": license_status(lic, ever_used=bool(rows), now=now),
                "days_left": days_left(lic, now),
                "offline_days": offline_days(lic),
                "offline_days_custom": lic.grace_days,
                "grace_days": lic.grace_days,
                "expires_at": iso(lic.expires_at),
                "revoked_at": iso(lic.revoked_at),
                "created_at": iso(lic.created_at),
                "updated_at": iso(lic.updated_at),
                "last_activity_at": max(seen) if seen else None,
                "seats": rows,
            }
        )
    return out


def event_rows(db: Session, events: list[LicenseAudit]) -> list[dict]:
    license_ids = {e.license_id for e in events if e.license_id}
    licenses = (
        {
            lic.id: lic
            for lic in db.execute(select(License).where(License.id.in_(license_ids))).scalars()
        }
        if license_ids
        else {}
    )
    seat_ids = {e.seat_id for e in events if e.seat_id is not None}
    seats = (
        {
            s.id: s
            for s in db.execute(select(LicenseSeat).where(LicenseSeat.id.in_(seat_ids))).scalars()
        }
        if seat_ids
        else {}
    )
    users = _users_by_id(db, [e.user_id for e in events])
    out = []
    for event in events:
        lic = licenses.get(event.license_id) if event.license_id else None
        seat = seats.get(event.seat_id) if event.seat_id is not None else None
        user = users.get(event.user_id) if event.user_id else None
        try:
            detail = json.loads(event.detail) if event.detail else None
        except Exception:
            detail = event.detail
        product = event.product or (lic.product if lic is not None else None)
        out.append(
            {
                "id": event.id,
                "event": event.event,
                "product": product,
                "product_name": product_name(product) if product else None,
                "license_id": event.license_id,
                "license_label": lic.label if lic is not None else None,
                "license_color": lic.color if lic is not None else None,
                "key_prefix": (
                    lic.key_prefix
                    if lic is not None
                    else (detail.get("key_prefix") if isinstance(detail, dict) else None)
                ),
                "seat_id": event.seat_id,
                "hostname": seat.hostname if seat is not None else None,
                "user_id": event.user_id,
                "user_email": user.email if user else None,
                "user_name": user.name if user else None,
                # Windows user, for apps without accounts (as reported at the time).
                "os_user": detail.get("os_user") if isinstance(detail, dict) else None,
                "ip": event.ip,
                "detail": detail,
                "created_at": iso(event.created_at),
            }
        )
    return out
