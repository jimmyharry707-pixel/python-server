"""Authentication endpoints: signup (OTP-gated), sign in, renew and sign out.

Sessions themselves live in ``server/auth_sessions.py``.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import auth_sessions
from ..config import get_settings
from ..database import get_db
from ..license_service import client_ip
from ..models import AuthSession, SignupRequest, User
from ..schemas import (
    LoginRequest,
    RefreshRequest,
    SignupCompleteResponse,
    SignupPendingResponse,
    SignupRequestIn,
    SignupVerifyRequest,
    TokenResponse,
)
from ..security import (
    create_access_token,
    decode_token,
    hash_password,
    oauth2_scheme,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])
_settings = get_settings()

OTP_TTL_MINUTES = 30
OTP_MAX_ATTEMPTS = 5
OPEN_SIGNUP_STATUSES = ("pending", "otp_issued")


def _hash_otp(otp: str) -> str:
    material = f"{_settings.jwt_secret}:signup-otp:{otp.strip()}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def generate_otp() -> str:
    """Return an 8-digit numeric OTP (cryptographically random)."""
    return f"{secrets.randbelow(100_000_000):08d}"


def hash_otp(otp: str) -> str:
    return _hash_otp(otp)


def verify_otp(otp: str, otp_hash: str | None) -> bool:
    if not otp_hash:
        return False
    return secrets.compare_digest(_hash_otp(otp), otp_hash)


# The website's refresh token: HttpOnly (page scripts can't read it), sent
# only to the auth endpoints, never by other sites.
REFRESH_COOKIE = "baymax_refresh"
# A page on another site can't add this header without the browser asking
# first (CORS), so requiring it on cookie requests blocks forged requests.
WEB_HEADER = "X-Requested-With"


def _cookie_path() -> str:
    return f"{_settings.api_prefix}/auth"


def _cookie_secure() -> bool:
    return _settings.environment.lower() != "development"


def _require_web_header(request: Request) -> None:
    if not request.headers.get(WEB_HEADER):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=f"Missing {WEB_HEADER} header"
        )


def _set_refresh_cookie(response: Response, session: AuthSession) -> None:
    # Without "Keep me signed in" the cookie ends when the browser closes.
    max_age = None
    if session.remember:
        max_age = max(0, int((session.expires_at - datetime.utcnow()).total_seconds()))
    response.set_cookie(
        REFRESH_COOKIE,
        auth_sessions.refresh_token_for(session),
        max_age=max_age,
        path=_cookie_path(),
        secure=_cookie_secure(),
        httponly=True,
        samesite="strict",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        REFRESH_COOKIE,
        path=_cookie_path(),
        secure=_cookie_secure(),
        httponly=True,
        samesite="strict",
    )


def _token_response(user: User, session: AuthSession, *, cookie: bool) -> TokenResponse:
    minutes = (
        _settings.desktop_access_token_ttl_minutes
        if session.client == "desktop"
        else _settings.access_token_ttl_minutes
    )
    return TokenResponse(
        access_token=create_access_token(user.id, session.id, minutes),
        refresh_token=None if cookie else auth_sessions.refresh_token_for(session),
        expires_in=minutes * 60,
        user_id=user.id,
    )


@router.post("/signup", response_model=SignupPendingResponse, status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequestIn, db: Session = Depends(get_db)) -> SignupPendingResponse:
    email = str(payload.email).strip().lower()
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    existing_user = db.execute(
        select(User).where(User.email == email)
    ).scalar_one_or_none()
    if existing_user is not None:
        raise HTTPException(status_code=409, detail="Email already registered")

    open_req = db.execute(
        select(SignupRequest).where(
            SignupRequest.email == email,
            SignupRequest.status.in_(OPEN_SIGNUP_STATUSES),
        )
    ).scalar_one_or_none()
    if open_req is not None:
        raise HTTPException(
            status_code=409,
            detail="A signup request for this email is already pending admin approval",
        )

    req = SignupRequest(
        email=email,
        name=name,
        password_hash="",  # set later during OTP verify
        status="pending",
    )
    db.add(req)
    db.commit()
    return SignupPendingResponse()


@router.post("/signup/verify", response_model=SignupCompleteResponse)
def signup_verify(
    payload: SignupVerifyRequest, db: Session = Depends(get_db)
) -> SignupCompleteResponse:
    email = str(payload.email).strip().lower()
    otp = (payload.otp or "").strip()
    password = payload.password or ""
    if not otp:
        raise HTTPException(status_code=400, detail="OTP is required")
    if len(password) < 8:
        raise HTTPException(
            status_code=400, detail="Password must be at least 8 characters"
        )

    req = db.execute(
        select(SignupRequest)
        .where(
            SignupRequest.email == email,
            SignupRequest.status.in_(OPEN_SIGNUP_STATUSES),
        )
        .order_by(SignupRequest.created_at.desc())
    ).scalars().first()
    if req is None:
        raise HTTPException(status_code=404, detail="No pending signup for this email")

    if req.status != "otp_issued" or not req.otp_hash:
        raise HTTPException(
            status_code=400,
            detail="OTP has not been issued yet. Wait for an administrator.",
        )

    if req.otp_attempts >= OTP_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail="Too many invalid OTP attempts. Ask an admin to re-issue the OTP.",
        )

    if not req.otp_expires_at or req.otp_expires_at < datetime.utcnow():
        raise HTTPException(
            status_code=400,
            detail="OTP expired. Ask an admin to issue a new one.",
        )

    if not verify_otp(otp, req.otp_hash):
        req.otp_attempts = int(req.otp_attempts or 0) + 1
        req.updated_at = datetime.utcnow()
        db.commit()
        remaining = OTP_MAX_ATTEMPTS - req.otp_attempts
        raise HTTPException(
            status_code=401,
            detail=f"Invalid OTP ({remaining} attempts remaining)",
        )

    # Race: another verify or admin create may have claimed the email.
    existing_user = db.execute(
        select(User).where(User.email == email)
    ).scalar_one_or_none()
    if existing_user is not None:
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        email=email,
        name=req.name,
        password_hash=hash_password(password),
        role="member",
        is_active=True,
    )
    db.add(user)
    db.flush()
    req.status = "completed"
    req.completed_user_id = user.id
    req.password_hash = ""
    req.otp_hash = None
    req.updated_at = datetime.utcnow()
    db.commit()
    return SignupCompleteResponse(email=email)


@router.post("/login", response_model=TokenResponse, response_model_exclude_none=True)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> TokenResponse:
    if payload.use_cookie:
        _require_web_header(request)
    email = str(payload.email).strip().lower()
    user = db.execute(
        select(User).where(User.email == email)
    ).scalar_one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled"
        )
    auth_sessions.purge_ended_sessions(db)
    session = auth_sessions.start_session(
        db,
        user,
        client="web" if payload.use_cookie else "desktop",
        # The desktop app keeps itself signed in only when asked to.
        remember=payload.remember or not payload.use_cookie,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    db.commit()
    if payload.use_cookie:
        _set_refresh_cookie(response, session)
    return _token_response(user, session, cookie=payload.use_cookie)


@router.post("/refresh", response_model=TokenResponse, response_model_exclude_none=True)
def refresh(
    request: Request,
    response: Response,
    payload: RefreshRequest | None = None,
    db: Session = Depends(get_db),
):
    """Renew: the desktop app sends its refresh token, the website its cookie."""
    token = (payload.refresh_token if payload else "").strip()
    from_cookie = not token
    if from_cookie:
        _require_web_header(request)
        token = request.cookies.get(REFRESH_COOKIE, "")
    try:
        session = auth_sessions.renew_session(db, token)
        user = db.get(User, session.user_id)
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
            )
    except HTTPException as exc:
        if not from_cookie:
            raise
        failed = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        _clear_refresh_cookie(failed)
        return failed
    db.commit()
    if from_cookie:
        _set_refresh_cookie(response, session)
    return _token_response(user, session, cookie=from_cookie)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    payload: RefreshRequest | None = None,
    access_token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Response:
    """End this sign-in. Works with an expired access token, a refresh token
    in the body (desktop) or the website's cookie."""
    session: AuthSession | None = None
    if access_token:
        try:
            claims = decode_token(access_token, expected_type="access", verify_exp=False)
            session = db.get(AuthSession, claims.get("sid") or "")
        except HTTPException:
            session = None
    refresh_token = (payload.refresh_token if payload else "").strip()
    if session is None and not refresh_token and request.cookies.get(REFRESH_COOKIE):
        _require_web_header(request)
        refresh_token = request.cookies.get(REFRESH_COOKIE, "")
    if session is None and refresh_token:
        found = auth_sessions.find_session(db, refresh_token)
        session = found[0] if found else None
    if session is not None:
        auth_sessions.end_session(session)
        db.commit()
    done = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(done)
    return done


# Re-export for admin issue-otp (keeps OTP helpers in one place).
__all__ = [
    "router",
    "generate_otp",
    "hash_otp",
    "OTP_TTL_MINUTES",
    "OPEN_SIGNUP_STATUSES",
]
