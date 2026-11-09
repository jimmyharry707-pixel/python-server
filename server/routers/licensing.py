"""License endpoints for every app: ``/licensing/{product}/...``.

Sidekick and SnowLink call these with the key alone. CareerCraft's products
need a signed-in account (``Authorization: Bearer``) here too; its installed
copies use the older ``/license/*`` paths, which run the same code.

    POST /licensing/{product}/activate     key + machine -> lease + seat secret
    POST /licensing/{product}/refresh      seat secret   -> fresh lease
    POST /licensing/{product}/release      give the seat back
    GET  /licensing/{product}/public-keys  keys to verify leases offline

The full contract, for app developers, is docs/licensing/CLIENT_PROTOCOL.md.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import license_service as svc
from ..database import get_db
from ..licensing import ProductNotConfigured, public_key_pems, signing_kid
from ..models import User
from ..products import Product, get_product
from ..schemas import (
    LicenseActivateRequest,
    LicenseActivateResponse,
    LicenseRefreshRequest,
    LicenseRefreshResponse,
    LicenseReleaseRequest,
    LicenseReleaseResponse,
)
from ..security import get_current_user, oauth2_scheme

router = APIRouter(prefix="/licensing", tags=["licensing"])


def _product(product: str) -> Product:
    found = get_product(product)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Unknown product: {product}")
    return found


def _optional_user(
    token: str | None = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User | None:
    """The signed-in user when a Bearer token is sent (a bad token is still 401)."""
    if not token:
        return None
    return get_current_user(token=token, db=db)


def _account_for(product: Product, user: User | None) -> User | None:
    if product.requires_account and user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Sign in to {product.name} first",
        )
    # Key-only apps never bind a seat to an account, even if a token was sent.
    return user if product.requires_account else None


@router.get("/{product}/public-keys")
def public_keys(product: str) -> dict:
    found = _product(product)
    try:
        keys = public_key_pems(found.id)
    except ProductNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Licensing for {found.name} is not set up on the server yet",
        ) from exc
    return {"product": found.id, "keys": keys, "active_kid": signing_kid(found.id)}


@router.post("/{product}/activate", response_model=LicenseActivateResponse)
def activate(
    product: str,
    req: LicenseActivateRequest,
    request: Request,
    user: User | None = Depends(_optional_user),
    db: Session = Depends(get_db),
) -> dict:
    found = _product(product)
    account = _account_for(found, user)
    ip = svc.client_ip(request)
    svc.rate_limit(ip)
    return svc.activate(
        db,
        product=found,
        user=account,
        ip=ip,
        license_key=req.license_key,
        fingerprint=req.hardware_fingerprint,
        device_id=req.device_id or "",
        machine=svc.Machine.from_request(req),
    )


@router.post("/{product}/refresh", response_model=LicenseRefreshResponse)
def refresh(
    product: str,
    req: LicenseRefreshRequest,
    request: Request,
    user: User | None = Depends(_optional_user),
    db: Session = Depends(get_db),
) -> dict:
    found = _product(product)
    account = _account_for(found, user)
    ip = svc.client_ip(request)
    svc.rate_limit(ip)
    return svc.refresh(
        db,
        product=found,
        user=account,
        ip=ip,
        fingerprint=req.hardware_fingerprint,
        seat_secret=req.seat_secret,
        lease_token=req.lease_token,
        device_id=req.device_id or "",
        machine=svc.Machine.from_request(req),
    )


@router.post("/{product}/release", response_model=LicenseReleaseResponse)
def release(
    product: str,
    req: LicenseReleaseRequest,
    request: Request,
    user: User | None = Depends(_optional_user),
    db: Session = Depends(get_db),
) -> dict:
    found = _product(product)
    account = _account_for(found, user)
    ip = svc.client_ip(request)
    svc.rate_limit(ip)
    return svc.release_own_seat(
        db,
        product=found,
        user=account,
        ip=ip,
        fingerprint=req.hardware_fingerprint,
        seat_secret=req.seat_secret,
    )
