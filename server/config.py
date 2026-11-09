"""Server configuration via environment variables.

All settings can be provided through a local ``.env`` file (see
``.env.example``) or real environment variables. Defaults are safe for local
development only - production MUST override ``BAYMAX_JWT_SECRET``,
``BAYMAX_LICENSE_SIGNING_KEY``, and ``BAYMAX_DATABASE_URL``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Always load server/.env regardless of process cwd (repo root vs server/).
_SERVER_DIR = Path(__file__).resolve().parent
_ENV_FILE = _SERVER_DIR / ".env"

# Fixed 32-byte seed for development-only Ed25519 license signing.
# Production MUST set BAYMAX_LICENSE_SIGNING_KEY (PKCS8 PEM).
_DEV_LICENSE_SEED = b"baymax-dev-license-signing-key!!"  # exactly 32 bytes


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BAYMAX_",
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core
    environment: str = "development"  # development | staging | production
    api_prefix: str = "/api/v1"

    # Database. SQLite for dev; Postgres in prod, e.g.
    #   postgresql+psycopg://user:pass@host:5432/baymax
    # Relative sqlite paths are resolved under server/ (see database.py).
    database_url: str = "sqlite:///./baymax_server.db"

    # Auth / JWT (user session tokens - HS256)
    jwt_secret: str = "dev-insecure-change-me"
    jwt_algorithm: str = "HS256"
    # Short on purpose: the website renews it quietly a minute before it ends.
    access_token_ttl_minutes: int = 15
    # The desktop app renews only before a sync, so its token lasts longer.
    # Ending a session still works at once: every request checks the session.
    desktop_access_token_ttl_minutes: int = 60

    # Sign-in sessions (server/auth_sessions.py). A session ends after the idle
    # time without use, or at the hard limit, whichever comes first.
    session_idle_days: int = 7  # "Keep me signed in" and the desktop app
    session_short_idle_hours: int = 12  # website without "Keep me signed in"
    session_max_days: int = 14  # members and supporters
    session_staff_max_days: int = 7  # admins and managers
    # Two tabs (or desktop threads) renewing at the same moment both present
    # the same refresh token; the slower one is still accepted this long.
    session_refresh_grace_seconds: int = 30

    # Licensing: signed lease tokens (Ed25519). Lease TTL is the offline window.
    license_lease_ttl_days: int = 7
    # Deprecated alias kept for older .env files; preferred: license_lease_ttl_days.
    license_offline_grace_days: int = 7
    # PKCS8 PEM of the Ed25519 private key. Use \n for newlines in .env.
    # Empty in development -> derived from the fixed insecure seed above.
    license_signing_key: str = ""
    license_signing_kid: str = "baymax-lic-1"
    # Optional JSON map of kid -> SubjectPublicKeyInfo PEM for rotation/verification.
    # Empty -> derived from the active signing key.
    license_public_keys: str = ""
    # The other apps on the license service (server/products.py) each sign
    # with their own key, so a leaked key only affects one app. Same format as
    # license_signing_key. Empty in development -> derived from the dev seed;
    # in production an app's keys can't be activated until its key is set.
    license_signing_key_sidekick: str = ""
    license_signing_kid_sidekick: str = "sidekick-lic-1"
    license_public_keys_sidekick: str = ""
    license_signing_key_snowlink: str = ""
    license_signing_kid_snowlink: str = "snowlink-lic-1"
    license_public_keys_snowlink: str = ""
    # Pepper mixed into license-key hashes (change in production).
    license_key_pepper: str = "dev-license-pepper-change-me"
    # Rate-limit window for activate/refresh (requests per IP per minute).
    license_rate_limit_per_minute: int = 20

    # Where open daily review scans keep their uploaded ZIPs (deleted when the
    # scan is finished, cancelled or idle for 48 hours). Empty -> server/data/scans.
    scan_dir: str = ""

    # Sync ingest guardrails (per user, sliding 60s window).
    sync_push_rate_per_minute: int = 30
    sync_push_rows_per_minute: int = 20000

    # Pull-cursor safety lag. A push stamps ``server_modified`` shortly before it
    # commits, so a pull running in that gap sees nothing yet but would otherwise
    # hand out a cursor above the stamp - skipping the row forever. The cursor is
    # therefore held this far behind the wall clock. Rows are still returned up to
    # "now", so this costs re-delivery (idempotent), never delivery latency.
    sync_cursor_lag_seconds: int = 10
    # Upper bound when the watermark is derived from in-flight transactions, so a
    # single long-running transaction (backup, report, idle-in-transaction leak)
    # cannot pin every client into re-downloading its whole history.
    sync_cursor_max_lag_seconds: int = 300

    # CORS (comma separated origins for the admin dashboard / web front-end).
    # Never use "*" with credentials in production.
    cors_origins: str = "http://localhost:8000,http://127.0.0.1:8000"

    # Bootstrap admin (created on first start if no users exist)
    bootstrap_admin_email: str = "admin@baymax.example.com"
    bootstrap_admin_password: str = "changeme-admin"
    bootstrap_admin_name: str = "Administrator"

    # Directory where CareerCraft.exe / TekAbsurd_cert.cer live for the
    # /userportal downloads page. Empty = server/nginx/userportal/downloads.
    # In Docker, mount the host files (or dir) here and into nginx.
    release_files_dir: str = ""

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        raw = (self.cors_origins or "").strip()
        if not raw:
            return ["http://localhost:8000"]
        if raw == "*":
            # Credentials + wildcard is invalid; fall back to localhost for safety.
            return ["http://localhost:8000", "http://127.0.0.1:8000"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]

    @property
    def effective_lease_ttl_days(self) -> int:
        # Prefer the new setting; fall back to the legacy grace-days name.
        if self.license_lease_ttl_days and self.license_lease_ttl_days > 0:
            return self.license_lease_ttl_days
        return max(1, self.license_offline_grace_days)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_dev_license_seed() -> bytes:
    """Expose the fixed development seed (used by licensing.py)."""
    return _DEV_LICENSE_SEED

