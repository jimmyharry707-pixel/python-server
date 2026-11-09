"""Tests for Stage profile settings APIs (name/email/password/avatar).

Isolates a temp SQLite DB, then exercises self-service profile endpoints and
admin delete cleanup of avatar files. Run:

    server/.venv/bin/python -m server.test_profile_settings
"""

from __future__ import annotations

import io
import os
import struct
import tempfile
import uuid
import zlib
from pathlib import Path

# Isolate the DB before importing the app/settings.
_tmp_db = os.path.join(tempfile.gettempdir(), f"baymax_profile_{uuid.uuid4().hex}.db")
os.environ["BAYMAX_DATABASE_URL"] = f"sqlite:///{_tmp_db}"
os.environ["BAYMAX_JWT_SECRET"] = "profile-test-secret"
os.environ["BAYMAX_BOOTSTRAP_ADMIN_EMAIL"] = "admin@baymax.example.com"
os.environ["BAYMAX_BOOTSTRAP_ADMIN_PASSWORD"] = "adminpw"
os.environ["BAYMAX_LICENSE_SIGNING_KEY"] = ""
os.environ["BAYMAX_ENVIRONMENT"] = "development"

from fastapi.testclient import TestClient  # noqa: E402

from server.main import app  # noqa: E402
from server.routers.users import (  # noqa: E402
    _AVATAR_DIR,
    _safe_avatar_stem,
    delete_avatar_files,
)

failures: list[str] = []
API = "/api/v1"


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}{(' - ' + detail) if detail and not condition else ''}")
    if not condition:
        failures.append(name)


def _png_bytes(width: int = 8, height: int = 8, rgb: tuple[int, int, int] = (10, 102, 194)) -> bytes:
    """Minimal valid RGB PNG."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _login(client: TestClient, email: str, password: str) -> dict:
    r = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _avatar_paths(user_id: str) -> list[Path]:
    stem = _safe_avatar_stem(user_id)
    if not _AVATAR_DIR.exists():
        return []
    return list(_AVATAR_DIR.glob(f"{stem}.*"))


def main() -> int:
    user_id: str | None = None
    other_id: str | None = None
    with TestClient(app) as client:
        # Bootstrap admin
        admin_h = _login(client, "admin@baymax.example.com", "adminpw")

        # Create member for self-service tests
        r = client.post(
            f"{API}/admin/api/users",
            headers=admin_h,
            json={
                "email": "profile.user@baymax.example.com",
                "name": "Profile User",
                "password": "oldpassword",
                "role": "member",
            },
        )
        check("create profile user", r.status_code == 201, r.text)
        user_id = r.json().get("id")
        user_h = _login(client, "profile.user@baymax.example.com", "oldpassword")

        # Second user (email collision target)
        r = client.post(
            f"{API}/admin/api/users",
            headers=admin_h,
            json={
                "email": "taken@baymax.example.com",
                "name": "Taken",
                "password": "takenpass1",
                "role": "member",
            },
        )
        check("create second user", r.status_code == 201, r.text)
        other_id = r.json().get("id")

        # GET /me includes avatar_url
        r = client.get(f"{API}/users/me", headers=user_h)
        me = r.json() if r.status_code == 200 else {}
        check(
            "users/me has avatar_url",
            r.status_code == 200 and "avatar_url" in me and me["avatar_url"] is None,
            r.text,
        )
        check("users/me name", me.get("name") == "Profile User", r.text)

        # PATCH display name
        r = client.patch(
            f"{API}/users/me",
            headers=user_h,
            json={"name": "Display Name"},
        )
        check(
            "patch display name",
            r.status_code == 200 and r.json().get("name") == "Display Name",
            r.text,
        )

        # Empty name rejected
        r = client.patch(f"{API}/users/me", headers=user_h, json={"name": "  "})
        check("reject empty name", r.status_code == 400, r.text)

        # Email change without password rejected
        r = client.patch(
            f"{API}/users/me",
            headers=user_h,
            json={"email": "profile.renamed@baymax.example.com"},
        )
        check("email change requires password", r.status_code == 400, r.text)

        # Email change with wrong password rejected
        r = client.patch(
            f"{API}/users/me",
            headers=user_h,
            json={
                "email": "profile.renamed@baymax.example.com",
                "current_password": "wrong-password",
            },
        )
        check("email change wrong password", r.status_code == 400, r.text)

        # Email collision rejected
        r = client.patch(
            f"{API}/users/me",
            headers=user_h,
            json={"email": "taken@baymax.example.com", "current_password": "oldpassword"},
        )
        check("email collision rejected", r.status_code == 400, r.text)

        # Email change with correct password
        r = client.patch(
            f"{API}/users/me",
            headers=user_h,
            json={
                "email": "profile.renamed@baymax.example.com",
                "current_password": "oldpassword",
            },
        )
        check(
            "email change ok",
            r.status_code == 200
            and r.json().get("email") == "profile.renamed@baymax.example.com",
            r.text,
        )
        # Re-login with new email (token still valid, but verify credentials)
        user_h = _login(client, "profile.renamed@baymax.example.com", "oldpassword")

        # Password change: wrong current
        r = client.post(
            f"{API}/users/me/password",
            headers=user_h,
            json={"current_password": "nope", "new_password": "newpassword"},
        )
        check("password wrong current", r.status_code == 400, r.text)

        # Password change: too short
        r = client.post(
            f"{API}/users/me/password",
            headers=user_h,
            json={"current_password": "oldpassword", "new_password": "short"},
        )
        check("password too short", r.status_code == 422 or r.status_code == 400, r.text)

        # Password change: success
        r = client.post(
            f"{API}/users/me/password",
            headers=user_h,
            json={"current_password": "oldpassword", "new_password": "newpassword"},
        )
        check("password change ok", r.status_code == 200 and r.json().get("ok") is True, r.text)

        r = client.post(
            f"{API}/auth/login",
            json={"email": "profile.renamed@baymax.example.com", "password": "oldpassword"},
        )
        check("old password rejected", r.status_code == 401, r.text)
        user_h = _login(client, "profile.renamed@baymax.example.com", "newpassword")
        check("new password login", True)

        # Avatar: reject non-image
        r = client.post(
            f"{API}/users/me/avatar",
            headers=user_h,
            files={"file": ("note.txt", io.BytesIO(b"hello"), "text/plain")},
        )
        check("avatar reject non-image", r.status_code == 400, r.text)

        # Avatar: reject oversized (>2 MiB)
        big = b"\x00" * (2 * 1024 * 1024 + 1)
        r = client.post(
            f"{API}/users/me/avatar",
            headers=user_h,
            files={"file": ("big.png", io.BytesIO(big), "image/png")},
        )
        check("avatar reject oversized", r.status_code in (400, 413), r.text)

        # Avatar: upload PNG
        png = _png_bytes()
        r = client.post(
            f"{API}/users/me/avatar",
            headers=user_h,
            files={"file": ("avatar.png", io.BytesIO(png), "image/png")},
        )
        avatar_url = r.json().get("avatar_url") if r.status_code == 200 else None
        check(
            "avatar upload",
            r.status_code == 200
            and isinstance(avatar_url, str)
            and avatar_url.startswith("/uploads/avatars/"),
            r.text,
        )
        on_disk = _avatar_paths(user_id)
        check("avatar file on disk", len(on_disk) == 1 and on_disk[0].is_file(), str(on_disk))

        # Avatar served
        if avatar_url:
            r = client.get(avatar_url)
            check(
                "avatar served",
                r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n",
                f"status={r.status_code} len={len(r.content)}",
            )

        r = client.get(f"{API}/users/me", headers=user_h)
        check(
            "users/me returns avatar_url",
            r.status_code == 200 and r.json().get("avatar_url") == avatar_url,
            r.text,
        )

        # Replace avatar (webp) — still one file for this user
        # Use PNG again labeled as webp would fail content sniffing; send real webp header-ish
        # Stick to PNG replace with different size to exercise delete+rewrite.
        png2 = _png_bytes(4, 4, (255, 0, 0))
        r = client.post(
            f"{API}/users/me/avatar",
            headers=user_h,
            files={"file": ("avatar2.png", io.BytesIO(png2), "image/png")},
        )
        check("avatar replace", r.status_code == 200, r.text)
        replacement_url = r.json().get("avatar_url") if r.status_code == 200 else None
        check(
            "avatar replace gets fresh URL",
            isinstance(replacement_url, str) and replacement_url != avatar_url,
            str(replacement_url),
        )
        check("avatar single file after replace", len(_avatar_paths(user_id)) == 1)

        # DELETE avatar
        r = client.delete(f"{API}/users/me/avatar", headers=user_h)
        check(
            "avatar delete",
            r.status_code == 200 and r.json().get("avatar_url") is None,
            r.text,
        )
        check("avatar files removed", _avatar_paths(user_id) == [])

        # Re-upload so admin-delete cleanup can be verified
        r = client.post(
            f"{API}/users/me/avatar",
            headers=user_h,
            files={"file": ("avatar.png", io.BytesIO(png), "image/png")},
        )
        check("avatar re-upload before admin delete", r.status_code == 200, r.text)
        check("avatar present before admin delete", len(_avatar_paths(user_id)) == 1)

        # Admin delete user removes avatar files
        r = client.delete(f"{API}/admin/api/users/{user_id}", headers=admin_h)
        check("admin delete user", r.status_code == 200 and r.json().get("ok") is True, r.text)
        check("avatar cleaned on user delete", _avatar_paths(user_id) == [])

        # Stage dashboard markup includes user menu + profile modal
        r = client.get("/dashboard")
        html = r.text if r.status_code == 200 else ""
        check("stage dashboard loads", r.status_code == 200, str(r.status_code))
        check("stage has user menu toggle", 'data-bs-toggle="dropdown"' in html and "userMenuToggle" in html)
        check("stage has profile settings item", "profileSettingsBtn" in html)
        check("stage has profile modal", "profileSettingsModal" in html)
        check("stage has theme menu item", "themeToggleMenu" in html)
        check("stage has sign out", "logoutBtn" in html)

        # Cleanup leftover second user (no avatar expected)
        if other_id:
            delete_avatar_files(other_id)
            client.delete(f"{API}/admin/api/users/{other_id}", headers=admin_h)

    # Best-effort cleanup of any leftover avatar files from this run
    if user_id:
        delete_avatar_files(user_id)

    print()
    if failures:
        print(f"FAILURES ({len(failures)}): {failures}")
        return 1
    print("ALL PROFILE SETTINGS TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
