"""CareerCraft's license endpoints and the admin license API.

Apps call the product endpoints in ``routers/licensing.py``
(``/licensing/{product}/...``). CareerCraft's own ``/license/activate`` and
``/license/refresh`` predate them and stay exactly as installed copies call
them; both run the same code in ``server/license_service.py``.

The admin side (everything else here) covers every product: issue, edit,
extend, suspend/resume and delete keys, release or block machines, and read
the machine list and event log.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import license_service as svc
from ..database import get_db
from ..licensing import (
    as_naive_utc,
    ensure_aware,
    hash_key,
    key_prefix,
    new_license_key,
    public_key_pems,
    signing_kid,
    utcnow,
)
from ..models import License, LicenseAudit, LicenseSeat, LicenseSeatUser, User
from ..products import PRODUCTS, get_product
from ..schemas import (
    LicenseActivateRequest,
    LicenseActivateResponse,
    LicenseCreateRequest,
    LicenseExtendRequest,
    LicenseRefreshRequest,
    LicenseRefreshResponse,
    LicenseUpdateRequest,
)
from ..security import get_current_admin, get_current_user

logger = logging.getLogger("baymax.license")
router = APIRouter(prefix="/license", tags=["license"])

_CAREERCRAFT = PRODUCTS["careercraft"]
MAX_SEATS = 10000
MAX_OFFLINE_DAYS = 365

# Kept for callers that predate license_service.
_audit = svc.audit
_seats_used = svc.seats_used
_client_ip = svc.client_ip


def require_active_license(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_license_token: str | None = Header(default=None, alias="X-License-Token"),
) -> LicenseSeat:
    """Verify the CareerCraft lease and that its seat and license are live."""
    token = x_license_token or request.headers.get("X-License-Token")
    return svc.require_careercraft_seat(db, token=token, user=user)


# ---------------------------------------------------------------------------
# CareerCraft app endpoints (unchanged contract)
# ---------------------------------------------------------------------------
@router.get("/public-keys")
def public_keys() -> dict:
    """CareerCraft's Ed25519 public keys for offline lease verification."""
    return {"keys": public_key_pems("careercraft"), "active_kid": signing_kid("careercraft")}


@router.post("/activate", response_model=LicenseActivateResponse)
def activate(
    req: LicenseActivateRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    ip = svc.client_ip(request)
    svc.rate_limit(ip)
    return svc.activate(
        db,
        product=_CAREERCRAFT,
        user=user,
        ip=ip,
        license_key=req.license_key,
        fingerprint=req.hardware_fingerprint,
        device_id=req.device_id or "",
        machine=svc.Machine.from_request(req),
    )


@router.post("/refresh", response_model=LicenseRefreshResponse)
def refresh(
    req: LicenseRefreshRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    ip = svc.client_ip(request)
    svc.rate_limit(ip)
    return svc.refresh(
        db,
        product=_CAREERCRAFT,
        user=user,
        ip=ip,
        fingerprint=req.hardware_fingerprint,
        seat_secret=req.seat_secret,
        lease_token=req.lease_token,
        device_id=req.device_id or "",
        machine=svc.Machine.from_request(req),
    )


# ---------------------------------------------------------------------------
# Admin: products, licenses, machines, events
# ---------------------------------------------------------------------------
def _get_license(db: Session, license_id: str) -> License:
    lic = db.get(License, license_id)
    if lic is None:
        raise HTTPException(status_code=404, detail="License not found")
    return lic


def _get_seat(db: Session, seat_id: int) -> LicenseSeat:
    seat = db.get(LicenseSeat, seat_id)
    if seat is None:
        raise HTTPException(status_code=404, detail="Machine not found")
    return seat


def _license_out(db: Session, lic: License) -> dict:
    return svc.license_rows(db, [lic])[0]


def _seat_out(db: Session, seat: LicenseSeat) -> dict:
    return svc.machine_rows(db, [seat])[0]


def _check_seat_limit(value: int) -> int:
    if value < 1 or value > MAX_SEATS:
        raise HTTPException(status_code=400, detail=f"Seat limit must be 1 to {MAX_SEATS}")
    return value


def _check_offline_days(value: int | None) -> int | None:
    if value is None or value == 0:
        return None
    if value < 1 or value > MAX_OFFLINE_DAYS:
        raise HTTPException(
            status_code=400, detail=f"Offline days must be 1 to {MAX_OFFLINE_DAYS}"
        )
    return value


def _naive_expiry(value: Any):
    return as_naive_utc(value) if value is not None else None


@router.get("/products")
def list_products(
    _admin: User = Depends(get_current_admin), db: Session = Depends(get_db)
) -> list[dict]:
    licenses = dict(
        db.execute(select(License.product, func.count()).group_by(License.product)).all()
    )
    machines = dict(
        db.execute(
            select(License.product, func.count())
            .select_from(LicenseSeat)
            .join(License, License.id == LicenseSeat.license_id)
            .where(LicenseSeat.revoked.is_(False), LicenseSeat.revoked_at.is_(None))
            .group_by(License.product)
        ).all()
    )
    return [
        {
            "id": p.id,
            "name": p.name,
            "requires_account": p.requires_account,
            "tracks_online": p.tracks_online,
            "licenses": licenses.get(p.id, 0),
            "active_machines": machines.get(p.id, 0),
        }
        for p in PRODUCTS.values()
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_license(
    req: LicenseCreateRequest,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    product = get_product(req.product)
    if product is None:
        raise HTTPException(status_code=400, detail=f"Unknown product: {req.product}")
    days = req.offline_days if req.offline_days is not None else req.grace_days
    raw_key = new_license_key()
    lic = License(
        product=product.id,
        key_hash=hash_key(raw_key),
        key_prefix=key_prefix(raw_key),
        label=(req.label or "").strip()[:255],
        customer=(req.customer or "").strip()[:255],
        notes=(req.notes or "").strip() or None,
        plan=(req.plan or "standard").strip()[:64] or "standard",
        features=svc.features_json(req.features),
        seat_limit=_check_seat_limit(req.seat_limit),
        grace_days=_check_offline_days(days),
        expires_at=_naive_expiry(req.expires_at),
    )
    lic.color = svc.key_color_for(db, lic)
    db.add(lic)
    db.flush()
    svc.audit(
        db,
        event="create",
        product=product.id,
        license_id=lic.id,
        user_id=admin.id,
        ip=svc.client_ip(request),
        detail={
            "label": lic.label,
            "seat_limit": lic.seat_limit,
            "expires_at": svc.iso(lic.expires_at),
        },
    )
    db.commit()
    db.refresh(lic)
    return {**_license_out(db, lic), "license_key": raw_key}


@router.get("")
def list_licenses(
    product: str | None = None,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    query = select(License).order_by(License.created_at.desc())
    if product:
        query = query.where(License.product == product)
    return svc.license_rows(db, list(db.execute(query).scalars().all()))


@router.get("/machines")
def list_machines(
    product: str | None = None,
    state: str | None = None,
    q: str | None = None,
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    """Every machine that has used a key, newest activity first."""
    query = select(LicenseSeat).join(License, License.id == LicenseSeat.license_id)
    if product:
        query = query.where(License.product == product)
    if state == "active":
        query = query.where(LicenseSeat.revoked.is_(False), LicenseSeat.revoked_at.is_(None))
    elif state == "blocked":
        query = query.where(LicenseSeat.blocked.is_(True))
    elif state == "released":
        query = query.where(
            or_(LicenseSeat.revoked.is_(True), LicenseSeat.revoked_at.is_not(None)),
            LicenseSeat.blocked.is_(False),
        )
    seats = list(db.execute(query).scalars().all())
    rows = svc.machine_rows(db, seats)
    needle = (q or "").strip().lower()
    if needle:

        def haystack(row: dict) -> str:
            people = " ".join(
                f"{u.get('email') or ''} {u.get('name') or ''} {u.get('os_user') or ''}"
                for u in row["users"]
            )
            return " ".join(
                str(row.get(k) or "")
                for k in (
                    "hostname",
                    "last_ip",
                    "os_user",
                    "os_name",
                    "app_version",
                    "license_label",
                    "license_customer",
                    "license_key_prefix",
                    "user_email",
                    "user_name",
                )
            ).lower() + " " + people.lower()

        rows = [row for row in rows if needle in haystack(row)]
    rows.sort(key=lambda r: r["last_seen_at"] or "", reverse=True)
    return rows


@router.get("/events")
def list_events(
    product: str | None = None,
    license_id: str | None = None,
    seat_id: int | None = None,
    event: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    query = select(LicenseAudit)
    if product:
        query = query.where(LicenseAudit.product == product)
    if license_id:
        query = query.where(LicenseAudit.license_id == license_id)
    if seat_id is not None:
        query = query.where(LicenseAudit.seat_id == seat_id)
    if event:
        query = query.where(LicenseAudit.event == event)
    total = db.execute(select(func.count()).select_from(query.subquery())).scalar_one()
    events = (
        db.execute(
            query.order_by(LicenseAudit.created_at.desc(), LicenseAudit.id.desc())
            .offset(offset)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return {"items": svc.event_rows(db, list(events)), "total": total}


@router.patch("/{license_id}")
def update_license(
    license_id: str,
    req: LicenseUpdateRequest,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Change a key in place. Machines pick it up at their next renewal."""
    lic = _get_license(db, license_id)
    sent = req.model_fields_set
    changes: dict[str, Any] = {}

    def change(field: str, value: Any, shown: Any = None) -> None:
        old = getattr(lic, field)
        if old != value:
            changes[field] = {
                "from": shown(old) if shown else old,
                "to": shown(value) if shown else value,
            }
            setattr(lic, field, value)

    if "label" in sent and req.label is not None:
        change("label", req.label.strip()[:255])
    if "customer" in sent and req.customer is not None:
        change("customer", req.customer.strip()[:255])
        if "customer" in changes:
            # Keep the key's color apart from its new customer's other keys.
            lic.color = svc.key_color_for(db, lic)
    if "notes" in sent:
        change("notes", (req.notes or "").strip() or None)
    if "plan" in sent and req.plan:
        change("plan", req.plan.strip()[:64])
    if "seat_limit" in sent and req.seat_limit is not None:
        limit = _check_seat_limit(req.seat_limit)
        used = svc.seats_used(db, lic.id)
        if limit < used:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{used} machines use this key. Release some before "
                    f"lowering the limit to {limit}."
                ),
            )
        change("seat_limit", limit)
    if "offline_days" in sent:
        change("grace_days", _check_offline_days(req.offline_days))
    if "expires_at" in sent:
        change("expires_at", _naive_expiry(req.expires_at), svc.iso)

    if changes:
        lic.updated_at = as_naive_utc(utcnow())
        svc.audit(
            db,
            event="update",
            product=lic.product,
            license_id=lic.id,
            user_id=admin.id,
            ip=svc.client_ip(request),
            detail=changes,
        )
        db.commit()
    return _license_out(db, lic)


@router.post("/{license_id}/extend")
def extend_license(
    license_id: str,
    req: LicenseExtendRequest,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Push the expiry ``days`` later, counting from today if it already passed."""
    lic = _get_license(db, license_id)
    current = ensure_aware(lic.expires_at)
    if current is None:
        raise HTTPException(status_code=400, detail="This license never expires")
    start = max(current, utcnow())
    new_expiry = start + timedelta(days=req.days)
    lic.expires_at = as_naive_utc(new_expiry)
    lic.updated_at = as_naive_utc(utcnow())
    svc.audit(
        db,
        event="extend",
        product=lic.product,
        license_id=lic.id,
        user_id=admin.id,
        ip=svc.client_ip(request),
        detail={"days": req.days, "from": svc.iso(current), "to": svc.iso(new_expiry)},
    )
    db.commit()
    return _license_out(db, lic)


def _set_suspended(
    db: Session, request: Request, admin: User, license_id: str, *, suspended: bool
) -> dict:
    lic = _get_license(db, license_id)
    lic.is_active = not suspended
    if not suspended:
        lic.revoked_at = None
    lic.updated_at = as_naive_utc(utcnow())
    svc.audit(
        db,
        event="suspend" if suspended else "resume",
        product=lic.product,
        license_id=lic.id,
        user_id=admin.id,
        ip=svc.client_ip(request),
    )
    db.commit()
    return _license_out(db, lic)


@router.post("/{license_id}/suspend")
def suspend_license(
    license_id: str,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Stop the key working everywhere. Machines keep their activation."""
    return _set_suspended(db, request, admin, license_id, suspended=True)


@router.post("/{license_id}/resume")
def resume_license(
    license_id: str,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    return _set_suspended(db, request, admin, license_id, suspended=False)


# Old names for suspend/resume.
@router.post("/{license_id}/deactivate", include_in_schema=False)
def deactivate_license(
    license_id: str,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    return _set_suspended(db, request, admin, license_id, suspended=True)


@router.post("/{license_id}/reactivate", include_in_schema=False)
def reactivate_license(
    license_id: str,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    return _set_suspended(db, request, admin, license_id, suspended=False)


@router.delete("/{license_id}", status_code=status.HTTP_200_OK)
def delete_license(
    license_id: str,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Delete a key for good. Its event log is kept.

    A working key can't be deleted while machines use it, so a live customer
    isn't cut off by mistake. A suspended or expired key can: no machine can
    use it anyway. Its machines lose their activation and ask for a key at
    their next check.
    """
    lic = _get_license(db, license_id)
    used = svc.seats_used(db, lic.id)
    if used and svc.license_refusal(lic) is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{used} machine(s) still use this key. Suspend the key first, "
                "or release its machines."
            ),
        )
    seat_ids = [
        row[0]
        for row in db.execute(
            select(LicenseSeat.id).where(LicenseSeat.license_id == license_id)
        ).all()
    ]
    if seat_ids:
        db.execute(sa_delete(LicenseSeatUser).where(LicenseSeatUser.seat_id.in_(seat_ids)))
    seats_deleted = db.execute(
        sa_delete(LicenseSeat).where(LicenseSeat.license_id == license_id)
    ).rowcount
    svc.audit(
        db,
        event="delete",
        product=lic.product,
        license_id=lic.id,
        user_id=admin.id,
        ip=svc.client_ip(request),
        detail={
            "key_prefix": lic.key_prefix,
            "label": lic.label,
            "seats_deleted": seats_deleted,
            "machines_signed_out": used,
        },
    )
    prefix = lic.key_prefix
    db.delete(lic)
    db.commit()
    return {
        "ok": True,
        "id": license_id,
        "key_prefix": prefix,
        "seats_deleted": seats_deleted,
        "machines_signed_out": used,
    }


def _release(db: Session, seat: LicenseSeat, *, block: bool) -> None:
    now = as_naive_utc(utcnow())
    if seat.revoked_at is None or not seat.revoked:
        seat.revoked_at = now
    seat.revoked = True
    seat.blocked = block
    seat.revoked_reason = svc.BLOCKED_BY_ADMIN if block else svc.RELEASED_BY_ADMIN


def _seat_action(
    db: Session, request: Request, admin: User, seat: LicenseSeat, *, block: bool
) -> None:
    was = svc.seat_state(seat)
    _release(db, seat, block=block)
    svc.audit(
        db,
        event="block_seat" if block else "release_seat",
        license_id=seat.license_id,
        seat_id=seat.id,
        user_id=admin.id,
        ip=svc.client_ip(request),
        detail={"by": "admin", "was": was, "hostname": seat.hostname or None},
    )
    db.commit()


@router.post("/seats/{seat_id}/release")
def release_seat(
    seat_id: int,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Free the seat. The machine may activate again later (also unblocks)."""
    seat = _get_seat(db, seat_id)
    _seat_action(db, request, admin, seat, block=False)
    return _seat_out(db, seat)


@router.post("/seats/{seat_id}/block")
def block_seat(
    seat_id: int,
    request: Request,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Free the seat and bar this machine from the key."""
    seat = _get_seat(db, seat_id)
    _seat_action(db, request, admin, seat, block=True)
    return _seat_out(db, seat)


@router.post("/{license_id}/revoke-seat", include_in_schema=False)
def revoke_seat(
    license_id: str,
    request: Request,
    device_id: str | None = None,
    hardware_fingerprint: str | None = None,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Old admin call: release a seat found by fingerprint or device id."""
    query = select(LicenseSeat).where(LicenseSeat.license_id == license_id)
    if hardware_fingerprint:
        query = query.where(LicenseSeat.hardware_fingerprint == hardware_fingerprint)
    elif device_id:
        query = query.where(LicenseSeat.device_id == device_id)
    else:
        raise HTTPException(status_code=400, detail="Provide device_id or hardware_fingerprint")
    seat = db.execute(query).scalars().first()
    if seat is None:
        raise HTTPException(status_code=404, detail="Seat not found")
    _seat_action(db, request, admin, seat, block=False)
    return {
        "ok": True,
        "device_id": seat.device_id,
        "hardware_fingerprint": seat.hardware_fingerprint,
    }
