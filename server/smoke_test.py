"""End-to-end smoke test for the Baymax central server.

Runs against an in-process ASGI app with a temporary SQLite database. Exercises
the full flow the desktop client depends on plus the new licensing/profile/fleet
features. Run:

    server/.venv/Scripts/python.exe -m server.smoke_test
"""

from __future__ import annotations

import os
import tempfile
import uuid

# Isolate the DB before importing the app/settings.
_tmp_db = os.path.join(tempfile.gettempdir(), f"baymax_smoke_{uuid.uuid4().hex}.db")
os.environ["BAYMAX_DATABASE_URL"] = f"sqlite:///{_tmp_db}"
os.environ["BAYMAX_JWT_SECRET"] = "smoke-secret"
os.environ["BAYMAX_BOOTSTRAP_ADMIN_EMAIL"] = "admin@baymax.example.com"
os.environ["BAYMAX_BOOTSTRAP_ADMIN_PASSWORD"] = "adminpw"

from datetime import datetime  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from server.main import app  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}{(' - ' + detail) if detail and not condition else ''}")
    if not condition:
        failures.append(name)


def main() -> int:
    with TestClient(app) as client:
        API = "/api/v1"

        # Health (no prefix)
        r = client.get("/health")
        check("health", r.status_code == 200 and r.json()["status"] == "ok")

        # Admin login (bootstrap)
        r = client.post(
            f"{API}/auth/login",
            json={"email": "admin@baymax.example.com", "password": "adminpw"},
        )
        check("admin login", r.status_code == 200, r.text)
        admin_token = r.json().get("access_token")
        admin_h = {"Authorization": f"Bearer {admin_token}"}

        # Create a regular user
        r = client.post(
            f"{API}/admin/api/users",
            headers=admin_h,
            json={"email": "user1@baymax.example.com", "name": "User One", "password": "userpw"},
        )
        check("create user", r.status_code == 201, r.text)
        user_id = r.json().get("id")

        # User login
        r = client.post(
            f"{API}/auth/login",
            json={"email": "user1@baymax.example.com", "password": "userpw"},
        )
        check("user login", r.status_code == 200, r.text)
        tokens = r.json()
        user_h = {"Authorization": f"Bearer {tokens['access_token']}"}

        # Token refresh
        r = client.post(f"{API}/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        check("token refresh", r.status_code == 200, r.text)

        # /users/me
        r = client.get(f"{API}/users/me", headers=user_h)
        check("users/me", r.status_code == 200 and r.json()["email"] == "user1@baymax.example.com")

        # License create (admin) + activate (authenticated user + hardware fp)
        r = client.post(f"{API}/license", headers=admin_h, json={"label": "Team", "seat_limit": 1})
        check("license create", r.status_code == 201, r.text)
        license_key = r.json()["license_key"]
        license_id = r.json()["id"]
        key_prefix = r.json().get("key_prefix")

        fp_a = "a" * 64
        fp_b = "b" * 64
        device_id = "device-aaa"
        r = client.post(
            f"{API}/license/activate",
            headers=user_h,
            json={
                "license_key": license_key,
                "hardware_fingerprint": fp_a,
                "device_id": device_id,
                "app_version": "2.6.1",
            },
        )
        check("license activate", r.status_code == 200 and r.json().get("valid"), r.text)
        lease_token = r.json().get("lease_token")
        seat_secret = r.json().get("seat_secret")
        check("lease token issued", bool(lease_token) and bool(seat_secret))
        lic_h = {**user_h, "X-License-Token": lease_token}

        # Second device should exceed seat_limit=1
        r = client.post(
            f"{API}/license/activate",
            headers=user_h,
            json={
                "license_key": license_key,
                "hardware_fingerprint": fp_b,
                "device_id": "device-bbb",
            },
        )
        check("seat limit enforced", r.status_code == 200 and not r.json().get("valid"), r.text)

        # Refresh seat
        r = client.post(
            f"{API}/license/refresh",
            headers=user_h,
            json={
                "hardware_fingerprint": fp_a,
                "seat_secret": seat_secret,
                "lease_token": lease_token,
            },
        )
        check("license refresh", r.status_code == 200 and r.json().get("valid"), r.text)
        lease_token = r.json().get("lease_token") or lease_token
        lic_h = {**user_h, "X-License-Token": lease_token}

        r = client.get(f"{API}/license", headers=admin_h)
        check("license list", r.status_code == 200 and len(r.json()) >= 1, r.text)
        listed = next((row for row in r.json() if row["id"] == license_id), None)
        check(
            "license list shows prefix only",
            listed is not None and listed.get("key_prefix") == key_prefix and listed.get("license_key") is None,
        )

        # Deactivate whole license -> refresh must fail
        r = client.post(f"{API}/license/{license_id}/deactivate", headers=admin_h)
        check("license deactivate", r.status_code == 200 and r.json()["is_active"] is False, r.text)
        r = client.post(
            f"{API}/license/refresh",
            headers=user_h,
            json={
                "hardware_fingerprint": fp_a,
                "seat_secret": seat_secret,
                "lease_token": lease_token,
            },
        )
        check(
            "refresh denied after deactivate",
            r.status_code == 200 and not r.json().get("valid"),
            r.text,
        )

        # Reactivate
        r = client.post(f"{API}/license/{license_id}/reactivate", headers=admin_h)
        check("license reactivate", r.status_code == 200 and r.json()["is_active"] is True, r.text)

        # Delete license: refused while a machine still uses it
        r = client.delete(f"{API}/license/{license_id}", headers=admin_h)
        check("license delete refused while in use", r.status_code == 409, r.text)
        machines = client.get(f"{API}/license/machines", headers=admin_h).json()
        for machine in machines:
            if machine["license_id"] == license_id and machine["state"] == "active":
                client.post(f"{API}/license/seats/{machine['id']}/release", headers=admin_h)
        r = client.delete(f"{API}/license/{license_id}", headers=admin_h)
        check("license delete", r.status_code == 200 and r.json().get("ok") is True, r.text)
        r = client.get(f"{API}/license", headers=admin_h)
        check(
            "license gone after delete",
            r.status_code == 200 and all(row["id"] != license_id for row in r.json()),
            r.text,
        )

        # Create a fresh license for later sync tests that need a lease
        r = client.post(f"{API}/license", headers=admin_h, json={"label": "Team", "seat_limit": 2})
        check("license recreate", r.status_code == 201, r.text)
        license_key = r.json()["license_key"]
        license_id = r.json()["id"]
        r = client.post(
            f"{API}/license/activate",
            headers=user_h,
            json={
                "license_key": license_key,
                "hardware_fingerprint": fp_a,
                "device_id": device_id,
                "app_version": "2.6.1",
            },
        )
        check("license activate after recreate", r.status_code == 200 and r.json().get("valid"), r.text)
        lease_token = r.json().get("lease_token")
        seat_secret = r.json().get("seat_secret")
        lic_h = {**user_h, "X-License-Token": lease_token}

        # Admin creates a profile and assigns it
        r = client.put(
            f"{API}/admin/profiles/sample",
            headers=admin_h,
            json={"name": "Sample Person", "email": "sample@x.com", "location": "San Francisco, CA"},
        )
        check("profile upsert", r.status_code == 200, r.text)
        r = client.post(f"{API}/admin/profiles/sample/assign/{user_id}", headers=admin_h)
        check("profile assign", r.status_code == 201, r.text)

        # User fetches assigned profiles (requires lease)
        r = client.get(f"{API}/users/me/profiles", headers=lic_h)
        check(
            "assigned profiles",
            r.status_code == 200 and len(r.json()) == 1 and r.json()[0]["key"] == "sample",
            r.text,
        )
        # Without lease token -> 402
        r = client.get(f"{API}/users/me/profiles", headers=user_h)
        check("profiles gated without lease", r.status_code == 402, r.text)

        # Sync push (application + interview)
        now = datetime.utcnow().isoformat() + "Z"
        app_uuid = str(uuid.uuid4())
        iv_uuid = str(uuid.uuid4())
        r = client.post(
            f"{API}/sync/push",
            headers=lic_h,
            json={
                "applications": [
                    {
                        "id": app_uuid,
                        "company_name": "Acme",
                        "position_title": "Engineer",
                        "job_link": "https://acme.example/job",
                        "applied_date": now,
                        "status": "applied",
                        "last_modified": now,
                    }
                ],
                "interviews": [
                    {
                        "id": iv_uuid,
                        "title": "Phone screen",
                        "start_time_utc": "2026-08-01T15:00:00Z",
                        "end_time_utc": "2026-08-01T16:00:00Z",
                        "last_modified": now,
                    }
                ],
            },
        )
        check("sync push", r.status_code == 200 and len(r.json()["accepted"]) == 2, r.text)

        # Conflicting interview (overlap, no override) should be rejected as conflict
        r = client.post(
            f"{API}/sync/push",
            headers=lic_h,
            json={
                "applications": [],
                "interviews": [
                    {
                        "id": str(uuid.uuid4()),
                        "title": "Overlap",
                        "start_time_utc": "2026-08-01T15:30:00Z",
                        "end_time_utc": "2026-08-01T16:30:00Z",
                        "last_modified": now,
                    }
                ],
            },
        )
        rejected = r.json().get("rejected", [])
        check(
            "time conflict rejected",
            r.status_code == 200 and any(x["status"] == "conflict" for x in rejected),
            r.text,
        )

        # Sync pull returns the pushed rows
        r = client.get(f"{API}/sync/pull", headers=lic_h, params={"since": "1970-01-01T00:00:00Z"})
        data = r.json()
        check(
            "sync pull",
            r.status_code == 200
            and len(data["applications"]) == 1
            and len(data["interviews"]) == 1
            and data.get("has_more") is False,
            r.text,
        )

        # Isolation: applications list only shows own data
        r = client.get(f"{API}/applications/", headers=user_h)
        check("applications list", r.status_code == 200 and len(r.json()) == 1, r.text)

        # Team interview list: mine (assigned profiles + personal) + enrichment
        r = client.get(
            f"{API}/interviews/",
            headers=user_h,
            params={"scope": "mine", "from": "2026-07-01T00:00:00Z", "to": "2026-09-01T00:00:00Z"},
        )
        mine = r.json() if r.status_code == 200 else []
        check(
            "interviews mine",
            r.status_code == 200
            and len(mine) == 1
            and mine[0]["id"] == iv_uuid
            and "user_email" in mine[0]
            and "profile_id" in mine[0]
            and mine[0]["title"] == "Phone screen",
            r.text,
        )

        r = client.get(
            f"{API}/interviews/all",
            headers=user_h,
            params={"from": "2026-07-01T00:00:00Z", "to": "2026-09-01T00:00:00Z"},
        )
        check(
            "interviews all",
            r.status_code == 200 and any(x["id"] == iv_uuid for x in r.json()),
            r.text,
        )

        # Profile-scoped mine: push an interview with profile_key, then confirm it lists
        iv_prof = str(uuid.uuid4())
        r = client.post(
            f"{API}/sync/push",
            headers=lic_h,
            json={
                "applications": [],
                "interviews": [
                    {
                        "id": iv_prof,
                        "title": "Onsite",
                        "start_time_utc": "2026-08-10T18:00:00Z",
                        "end_time_utc": "2026-08-10T19:00:00Z",
                        "last_modified": now,
                        "profile_key": "sample",
                    }
                ],
            },
        )
        check(
            "sync push profile interview",
            r.status_code == 200
            and any(
                (x.get("id") if isinstance(x, dict) else x) == iv_prof
                for x in r.json().get("accepted", [])
            ),
            r.text,
        )

        r = client.get(
            f"{API}/interviews/",
            headers=user_h,
            params={"scope": "mine", "from": "2026-07-01T00:00:00Z", "to": "2026-09-01T00:00:00Z"},
        )
        mine2 = r.json() if r.status_code == 200 else []
        prof_row = next((x for x in mine2 if x["id"] == iv_prof), None)
        check(
            "interviews mine by profile",
            prof_row is not None
            and prof_row.get("profile_key") == "sample"
            and (prof_row.get("profile_name") or "").startswith("Sample"),
            r.text,
        )

        # Primary web UI at root
        r = client.get("/", follow_redirects=False)
        check(
            "web app root",
            r.status_code == 200 and "Absurd Technologies" in r.text,
            r.text[:400],
        )
        r = client.get("/login", follow_redirects=False)
        check(
            "web login",
            r.status_code == 200 and "Absurd Technologies" in r.text,
            r.text[:400],
        )
        r = client.get("/interviews", follow_redirects=False)
        check(
            "interviews page",
            r.status_code == 200 and "Absurd Technologies" in r.text,
            r.text[:400],
        )
        r = client.get("/stage/login", follow_redirects=False)
        check(
            "stage login redirects",
            r.status_code in (301, 302, 307, 308)
            and r.headers.get("location") in ("/login", "http://testserver/login"),
            str(r.status_code) + " " + str(r.headers.get("location")),
        )
        r = client.get("/deprecated", follow_redirects=False)
        check(
            "deprecated not served",
            r.status_code == 404,
            str(r.status_code),
        )
        r = client.get("/admin", follow_redirects=False)
        check(
            "admin redirects to login",
            r.status_code in (301, 302, 307, 308)
            and r.headers.get("location") in ("/login", "http://testserver/login"),
            str(r.status_code) + " " + str(r.headers.get("location")),
        )

        # user_id filter on interview list
        owner_id = mine[0].get("user_id") if mine else None
        r = client.get(
            f"{API}/interviews/",
            headers=user_h,
            params={
                "scope": "all",
                "from": "2026-07-01T00:00:00Z",
                "to": "2026-09-01T00:00:00Z",
                "user_id": owner_id or "missing",
            },
        )
        filtered = r.json() if r.status_code == 200 else []
        check(
            "interviews user_id filter",
            r.status_code == 200
            and owner_id
            and all(x.get("user_id") == owner_id for x in filtered)
            and any(x["id"] == iv_uuid for x in filtered),
            r.text[:300],
        )

        # Team observability (assigned-profile application history)
        r = client.get(f"{API}/team/observability", headers=user_h, params={"window": "7d"})
        obs = r.json() if r.status_code == 200 else {}
        check(
            "team observability",
            r.status_code == 200
            and obs.get("window") == "7d"
            and "series" in obs
            and "applications" in obs.get("series", {})
            and any(p.get("key") == "sample" for p in obs.get("profiles", [])),
            r.text,
        )
        r = client.get(f"{API}/team/observability", headers=user_h, params={"window": "all"})
        check("team observability all", r.status_code == 200 and r.json().get("window") == "all", r.text)

        r = client.get(f"{API}/team/applications", headers=user_h, params={"limit": 20})
        check(
            "team applications",
            r.status_code == 200 and "applications" in r.json() and "total" in r.json(),
            r.text,
        )

        # Colleagues: assign a second user to the same profile
        r = client.post(
            f"{API}/admin/api/users",
            headers=admin_h,
            json={"email": "user2@baymax.example.com", "password": "user2pw", "name": "User Two", "role": "member"},
        )
        check("create colleague user", r.status_code == 201, r.text)
        user2_id = r.json()["id"]
        r = client.post(f"{API}/admin/profiles/sample/assign/{user2_id}", headers=admin_h)
        check("assign colleague profile", r.status_code == 201, r.text)
        r = client.get(f"{API}/team/colleagues", headers=user_h)
        cols = r.json() if r.status_code == 200 else {}
        check(
            "team colleagues",
            r.status_code == 200
            and any(c.get("user_id") == user2_id for c in cols.get("colleagues", [])),
            r.text,
        )

        # Fleet presence via sync headers + telemetry + error + consent
        r = client.get(
            f"{API}/sync/pull",
            headers={
                **lic_h,
                "X-Device-Id": device_id,
                "X-App-Version": "2.6.1",
                "X-OS-Name": "Windows 11",
            },
            params={"since": "1970-01-01T00:00:00Z"},
        )
        check("sync pull presence", r.status_code == 200, r.text)

        r = client.get(f"{API}/admin/api/fleet", headers=admin_h)
        fleet_rows = r.json() if r.status_code == 200 else []
        matched = next((c for c in fleet_rows if c.get("device_id") == device_id), None)
        check(
            "fleet online via sync",
            matched is not None and matched.get("online") is True,
            str(matched),
        )

        r = client.post(
            f"{API}/fleet/telemetry",
            headers=user_h,
            json={
                "device_id": device_id,
                "app_version": "2.6.1",
                "events": [
                    {"event": "app_opened"},
                    {"event": "login_succeeded", "properties": {"method": "dialog"}},
                ],
            },
        )
        check("telemetry", r.status_code == 200, r.text)

        r = client.post(
            f"{API}/fleet/errors",
            headers=user_h,
            json={
                "device_id": device_id,
                "error_type": "ValueError",
                "message": "boom",
                "traceback": "Traceback (most recent call last):\n  ValueError: boom",
                "app_version": "2.6.1",
            },
        )
        check("error report", r.status_code == 200, r.text)

        r = client.get(f"{API}/admin/api/errors", headers=admin_h)
        err_rows = r.json() if r.status_code == 200 else []
        matched_err = next((e for e in err_rows if e.get("device_id") == device_id), None)
        check(
            "admin errors list",
            r.status_code == 200
            and matched_err is not None
            and "traceback" in matched_err
            and matched_err.get("traceback")
            and matched_err.get("error_type") == "ValueError",
            str(matched_err),
        )

        r = client.get(f"{API}/admin/api/telemetry/summary", headers=admin_h)
        summary = r.json() if r.status_code == 200 else []
        check(
            "admin telemetry summary",
            r.status_code == 200
            and isinstance(summary, list)
            and any(row.get("event") == "app_opened" for row in summary),
            str(summary),
        )

        r = client.get(f"{API}/admin/api/telemetry/recent", headers=admin_h)
        recent = r.json() if r.status_code == 200 else []
        check(
            "admin telemetry recent",
            r.status_code == 200
            and isinstance(recent, list)
            and any(row.get("event") == "login_succeeded" for row in recent),
            str(recent[:3]),
        )

        r = client.post(
            f"{API}/fleet/consent",
            headers=user_h,
            json={"device_id": device_id, "policy_version": "1.0", "accepted": True},
        )
        check("consent", r.status_code == 200, r.text)

        # Admin overview reflects the fleet
        r = client.get(f"{API}/admin/api/overview", headers=admin_h)
        o = r.json()
        check("admin overview", r.status_code == 200 and o["users"] >= 2 and o["clients"] >= 1, r.text)

        # Profiles geo choropleth payload (dashboard map)
        r = client.get(f"{API}/admin/api/profiles/geo", headers=admin_h)
        geo = r.json() if r.status_code == 200 else {}
        ca = next((s for s in geo.get("states", []) if s.get("code") == "CA"), None)
        check(
            "profiles geo",
            r.status_code == 200
            and isinstance(geo.get("states"), list)
            and isinstance(geo.get("unmapped"), list)
            and geo.get("mapped", 0) >= 1
            and ca is not None
            and ca.get("count", 0) >= 1,
            r.text,
        )
        r = client.get(f"{API}/admin/api/profiles/geo", headers=user_h)
        check("profiles geo authz", r.status_code == 403, r.text)

        # Per-user data view
        r = client.get(f"{API}/admin/api/users/{user_id}/data", headers=admin_h)
        check("admin user data", r.status_code == 200 and len(r.json()["applications"]) == 1, r.text)

        # Non-admin blocked from admin API
        r = client.get(f"{API}/admin/api/overview", headers=user_h)
        check("admin authz", r.status_code == 403, r.text)

        # Release publish + latest
        r = client.post(
            f"{API}/releases",
            headers=admin_h,
            json={"version": "2.7.0", "url": "https://x/CareerCraft.exe", "sha256": "abc", "mandatory": False, "notes": "n"},
        )
        check("release publish", r.status_code == 201, r.text)
        r = client.get(f"{API}/releases/latest")
        check("release latest", r.status_code == 200 and r.json()["version"] == "2.7.0", r.text)

        # Metrics endpoint
        r = client.get("/metrics")
        check(
            "metrics",
            r.status_code == 200 and "baymax_up 1" in r.text and "baymax_users_total" in r.text,
            r.text,
        )

        # Right-to-erasure: delete the user's data
        r = client.request("DELETE", f"{API}/admin/api/users/{user_id}/data", headers=admin_h)
        check("erase user data", r.status_code == 200 and r.json()["deleted_applications"] >= 1, r.text)
        r = client.get(f"{API}/admin/api/users/{user_id}/data", headers=admin_h)
        check("erase verified", r.status_code == 200 and len(r.json()["applications"]) == 0, r.text)

    print()
    if failures:
        print(f"FAILURES: {failures}")
        return 1
    print("ALL SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
