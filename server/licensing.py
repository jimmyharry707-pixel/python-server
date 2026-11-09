"""License key hashing and Ed25519 signed lease tokens.

Lease tokens are short-lived JWTs (alg=EdDSA) bound to a user, seat, and
hardware fingerprint. The client verifies them offline with the baked-in
public key; the lease ``exp`` claim *is* the offline grace window.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .config import get_dev_license_seed, get_settings

logger = logging.getLogger("baymax.licensing")


def utcnow() -> datetime:
    """Timezone-aware UTC now (naive-safe for DB storage via .replace)."""
    return datetime.now(timezone.utc)


def as_naive_utc(dt: datetime) -> datetime:
    """Store UTC as naive datetime for SQLAlchemy DateTime columns."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def ensure_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Key hashing
# ---------------------------------------------------------------------------
def normalize_license_key(key: str | None) -> str:
    return (key or "").strip().upper()


def hash_key(raw_key: str, pepper: str | None = None) -> str:
    """SHA-256 of pepper + normalized key. Stored instead of the raw key."""
    settings = get_settings()
    pepper = pepper if pepper is not None else settings.license_key_pepper
    material = f"{pepper}:{normalize_license_key(raw_key)}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def key_prefix(raw_key: str) -> str:
    """First segment of a XXXXX-XXXXX-... key for admin display."""
    normalized = normalize_license_key(raw_key)
    if "-" in normalized:
        return normalized.split("-", 1)[0]
    return normalized[:5]


def hash_seat_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def new_seat_secret() -> str:
    return secrets.token_urlsafe(32)


def new_license_key() -> str:
    raw = secrets.token_hex(10).upper()  # 20 hex chars
    return "-".join(raw[i : i + 5] for i in range(0, 20, 5))


# ---------------------------------------------------------------------------
# Ed25519 key loading
# ---------------------------------------------------------------------------
def _pem_unescape(value: str) -> str:
    """Turn .env-style ``\\n`` escapes into real newlines."""
    return (value or "").replace("\\n", "\n").strip()


def _private_from_seed(seed: bytes) -> Ed25519PrivateKey:
    if len(seed) != 32:
        raise ValueError("Ed25519 seed must be exactly 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(seed)


def _load_private_pem(pem: str) -> Ed25519PrivateKey:
    return serialization.load_pem_private_key(pem.encode("utf-8"), password=None)


def _public_pem(public: Ed25519PublicKey) -> str:
    return public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


DEFAULT_PRODUCT = "careercraft"


class ProductNotConfigured(RuntimeError):
    """An app has no signing key on this server (production only)."""


def _product_key_settings(product: str) -> tuple[str, str, str, str]:
    """(private PEM, kid, rotation-map JSON, env var name) for ``product``.

    CareerCraft keeps the original settings names so existing deployments and
    leases are unchanged; every other app has ``_<product>`` suffixed ones.
    """
    settings = get_settings()
    if product == DEFAULT_PRODUCT:
        return (
            settings.license_signing_key,
            settings.license_signing_kid,
            settings.license_public_keys,
            "BAYMAX_LICENSE_SIGNING_KEY",
        )
    return (
        getattr(settings, f"license_signing_key_{product}", "") or "",
        getattr(settings, f"license_signing_kid_{product}", "") or f"{product}-lic-1",
        getattr(settings, f"license_public_keys_{product}", "") or "",
        f"BAYMAX_LICENSE_SIGNING_KEY_{product.upper()}",
    )


def signing_kid(product: str = DEFAULT_PRODUCT) -> str:
    return _product_key_settings(product)[1]


def get_signing_private_key(product: str = DEFAULT_PRODUCT) -> Ed25519PrivateKey:
    # One cache entry per product however it's called (positional, keyword, default).
    return _signing_private_key(product)


@lru_cache
def _signing_private_key(product: str) -> Ed25519PrivateKey:
    settings = get_settings()
    pem, _kid, _map, env_name = _product_key_settings(product)
    raw = _pem_unescape(pem)
    if raw:
        return _load_private_pem(raw)
    if settings.is_production:
        raise ProductNotConfigured(
            f"{env_name} must be set in production "
            "(PKCS8 PEM of an Ed25519 private key)."
        )
    logger.warning(
        "Using insecure fixed development license signing seed for %s. "
        "Set %s for staging/production.",
        product,
        env_name,
    )
    seed = get_dev_license_seed()
    if product != DEFAULT_PRODUCT:
        # A different dev key per app, so a lease never verifies for another app.
        seed = hashlib.sha256(seed + b":" + product.encode("utf-8")).digest()
    return _private_from_seed(seed)


def get_public_keys(product: str = DEFAULT_PRODUCT) -> dict[str, Ed25519PublicKey]:
    """Map of kid -> public key for ``product`` (active key + any rotated keys)."""
    return _public_keys(product)


@lru_cache
def _public_keys(product: str) -> dict[str, Ed25519PublicKey]:
    _pem, kid, raw_map, env_name = _product_key_settings(product)
    keys: dict[str, Ed25519PublicKey] = {}

    raw_map = (raw_map or "").strip()
    if raw_map:
        try:
            mapping = json.loads(raw_map)
            if isinstance(mapping, dict):
                for map_kid, pem in mapping.items():
                    pub = serialization.load_pem_public_key(
                        _pem_unescape(str(pem)).encode("utf-8")
                    )
                    if isinstance(pub, Ed25519PublicKey):
                        keys[str(map_kid)] = pub
        except Exception as exc:
            logger.error("Failed to parse %s public keys: %s", env_name, exc)

    # Always include the active signing key.
    keys[kid] = get_signing_private_key(product).public_key()
    return keys


def get_active_public_pem(product: str = DEFAULT_PRODUCT) -> str:
    return _public_pem(get_signing_private_key(product).public_key())


def public_key_pems(product: str = DEFAULT_PRODUCT) -> dict[str, str]:
    return {kid: _public_pem(pub) for kid, pub in get_public_keys(product).items()}


# ---------------------------------------------------------------------------
# JWT (EdDSA) helpers
# ---------------------------------------------------------------------------
def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad)


def sign_lease(
    *,
    user_id: str | None,
    license_id: str,
    seat_id: int,
    device_fp: str,
    plan: str = "standard",
    features: list[str] | dict[str, Any] | None = None,
    license_expires_at: datetime | None = None,
    lease_ttl_days: int | None = None,
    product: str = DEFAULT_PRODUCT,
) -> tuple[str, datetime, str]:
    """Sign a lease JWT. Returns (token, lease_expires_at_aware, jti).

    ``user_id`` is the signed-in account for apps that have accounts; apps
    licensed by key alone get ``seat:<id>`` as the subject instead.
    """
    settings = get_settings()
    now = utcnow()
    ttl = lease_ttl_days if lease_ttl_days is not None else settings.effective_lease_ttl_days
    lease_exp = now + timedelta(days=max(1, ttl))

    # Hard license expiry caps the lease.
    lic_exp_aware = ensure_aware(license_expires_at)
    if lic_exp_aware is not None and lease_exp > lic_exp_aware:
        lease_exp = lic_exp_aware
    if lic_exp_aware is not None and lic_exp_aware <= now:
        raise ValueError("License has already expired")

    jti = uuid.uuid4().hex
    header = {
        "alg": "EdDSA",
        "typ": "JWT",
        "kid": signing_kid(product),
    }
    payload: dict[str, Any] = {
        "sub": user_id or f"seat:{seat_id}",
        "product": product,
        "license_id": license_id,
        "seat_id": seat_id,
        "device_fp": device_fp,
        "plan": plan or "standard",
        "features": features or [],
        "iat": int(now.timestamp()),
        "exp": int(lease_exp.timestamp()),
        "jti": jti,
    }
    if lic_exp_aware is not None:
        payload["license_exp"] = int(lic_exp_aware.timestamp())

    header_b64 = _b64url_encode(
        json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    payload_b64 = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    signature = get_signing_private_key(product).sign(signing_input)
    token = f"{header_b64}.{payload_b64}.{_b64url_encode(signature)}"
    return token, lease_exp, jti


def verify_lease(
    token: str,
    *,
    product: str = DEFAULT_PRODUCT,
    expected_user_id: str | None = None,
    expected_device_fp: str | None = None,
    max_clock_skew_seconds: int = 300,
) -> dict[str, Any]:
    """Verify signature and claims. Raises ValueError on failure.

    Only ``product``'s keys are tried, and the lease must name that product
    (leases from before multi-product licensing carry none: CareerCraft's).
    """
    parts = (token or "").split(".")
    if len(parts) != 3:
        raise ValueError("Malformed lease token")

    header_b64, payload_b64, sig_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
        signature = _b64url_decode(sig_b64)
    except Exception as exc:
        raise ValueError("Malformed lease token") from exc

    if header.get("alg") != "EdDSA":
        raise ValueError("Unsupported lease algorithm")

    kid = header.get("kid") or signing_kid(product)
    public = get_public_keys(product).get(kid)
    if public is None:
        raise ValueError(f"Unknown lease key id: {kid}")

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    try:
        public.verify(signature, signing_input)
    except Exception as exc:
        raise ValueError("Invalid lease signature") from exc

    if (payload.get("product") or DEFAULT_PRODUCT) != product:
        raise ValueError("Lease is for a different app")

    now = utcnow()
    exp = payload.get("exp")
    if exp is None:
        raise ValueError("Lease missing exp")
    exp_dt = datetime.fromtimestamp(int(exp), tz=timezone.utc)
    if now > exp_dt + timedelta(seconds=max_clock_skew_seconds):
        raise ValueError("Lease has expired")

    license_exp = payload.get("license_exp")
    if license_exp is not None:
        lic_exp_dt = datetime.fromtimestamp(int(license_exp), tz=timezone.utc)
        if now > lic_exp_dt + timedelta(seconds=max_clock_skew_seconds):
            raise ValueError("License has expired")

    iat = payload.get("iat")
    if iat is not None:
        iat_dt = datetime.fromtimestamp(int(iat), tz=timezone.utc)
        # Reject tokens issued far in the future (clock skew / tampering).
        if iat_dt > now + timedelta(seconds=max_clock_skew_seconds):
            raise ValueError("Lease iat is in the future")

    if expected_user_id and payload.get("sub") != expected_user_id:
        raise ValueError("Lease user mismatch")
    if expected_device_fp and payload.get("device_fp") != expected_device_fp:
        raise ValueError("Lease device fingerprint mismatch")

    return payload
