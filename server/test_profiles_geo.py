"""API tests for GET /admin/api/profiles/geo (Stage dashboard map)."""

from __future__ import annotations

import os
import tempfile
import uuid

_tmp_db = os.path.join(tempfile.gettempdir(), f"baymax_profiles_geo_{uuid.uuid4().hex}.db")
os.environ["BAYMAX_DATABASE_URL"] = f"sqlite:///{_tmp_db}"
os.environ["BAYMAX_JWT_SECRET"] = "profiles-geo-test-secret"
os.environ["BAYMAX_BOOTSTRAP_ADMIN_EMAIL"] = "admin@baymax.example.com"
os.environ["BAYMAX_BOOTSTRAP_ADMIN_PASSWORD"] = "adminpw"
os.environ["BAYMAX_LICENSE_SIGNING_KEY"] = ""
os.environ["BAYMAX_ENVIRONMENT"] = "development"

from fastapi.testclient import TestClient  # noqa: E402

from server.main import app  # noqa: E402

API = "/api/v1"


def test_profiles_geo_maps_and_authz():
    with TestClient(app) as client:
        r = client.post(
            f"{API}/auth/login",
            json={"email": "admin@baymax.example.com", "password": "adminpw"},
        )
        assert r.status_code == 200
        admin_h = {"Authorization": f"Bearer {r.json()['access_token']}"}

        r = client.post(
            f"{API}/admin/api/users",
            headers=admin_h,
            json={
                "email": "member@baymax.example.com",
                "name": "Member",
                "password": "memberpw",
            },
        )
        assert r.status_code == 201

        r = client.post(
            f"{API}/auth/login",
            json={"email": "member@baymax.example.com", "password": "memberpw"},
        )
        assert r.status_code == 200
        member_h = {"Authorization": f"Bearer {r.json()['access_token']}"}

        r = client.put(
            f"{API}/admin/profiles/sf",
            headers=admin_h,
            json={"name": "SF Profile", "location": "San Francisco, CA"},
        )
        assert r.status_code == 200
        r = client.put(
            f"{API}/admin/profiles/remote",
            headers=admin_h,
            json={"name": "Remote Profile", "location": "Remote"},
        )
        assert r.status_code == 200

        r = client.get(f"{API}/admin/api/profiles/geo", headers=admin_h)
        assert r.status_code == 200
        body = r.json()
        assert body["mapped"] >= 1
        assert body["unmapped_count"] >= 1
        ca = next(s for s in body["states"] if s["code"] == "CA")
        assert ca["region"] == "US-CA"
        assert ca["count"] >= 1
        assert "SF Profile" in ca["profiles"]

        r = client.get(f"{API}/admin/api/profiles/geo", headers=member_h)
        assert r.status_code == 403
