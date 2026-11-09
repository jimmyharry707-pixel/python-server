"""Tests for the work plan reviewer grant.

A member an admin marks as work plan reviewer may see the team overview,
read anyone's plan and comment on it, but not change it. Isolates a temp
SQLite DB. Run:

    server/.venv/bin/python -m server.test_work_plan_reviewer
"""

from __future__ import annotations

import os
import tempfile
import uuid
from datetime import date, timedelta

# Isolate the DB before importing the app/settings.
_tmp_db = os.path.join(tempfile.gettempdir(), f"baymax_wp_reviewer_{uuid.uuid4().hex}.db")
os.environ["BAYMAX_DATABASE_URL"] = f"sqlite:///{_tmp_db}"
os.environ["BAYMAX_JWT_SECRET"] = "wp-reviewer-test-secret"
os.environ["BAYMAX_BOOTSTRAP_ADMIN_EMAIL"] = "admin@baymax.example.com"
os.environ["BAYMAX_BOOTSTRAP_ADMIN_PASSWORD"] = "adminpw"
os.environ["BAYMAX_ENVIRONMENT"] = "development"

from fastapi.testclient import TestClient  # noqa: E402

from server.database import engine  # noqa: E402
from server.main import app  # noqa: E402
from server.migrations import _add_user_work_plan_reviewer, _columns  # noqa: E402

failures: list[str] = []
API = "/api/v1"


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}{(' - ' + detail) if detail and not condition else ''}")
    if not condition:
        failures.append(name)


def _login(client: TestClient, email: str, password: str) -> dict:
    r = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _user(client: TestClient, admin_h: dict, name: str, role: str) -> tuple[str, dict]:
    email = f"{name.lower()}@baymax.example.com"
    r = client.post(
        f"{API}/admin/api/users",
        headers=admin_h,
        json={"email": email, "name": name, "password": "password1", "role": role},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"], _login(client, email, "password1")


def _this_saturday() -> str:
    today = date.today()
    return (today - timedelta(days=(today.weekday() + 2) % 7)).isoformat()


def _grant(client: TestClient, headers: dict, user_id: str, enabled: bool):
    return client.post(
        f"{API}/admin/api/users/{user_id}/set-work-plan-reviewer",
        headers=headers,
        json={"enabled": enabled},
    )


def main() -> int:
    week = _this_saturday()
    with TestClient(app) as client:
        admin_h = _login(client, "admin@baymax.example.com", "adminpw")
        lead, lead_h = _user(client, admin_h, "Lead", "member")
        bob, bob_h = _user(client, admin_h, "Bob", "member")
        mgr, _ = _user(client, admin_h, "Mia", "manager")
        sam, _ = _user(client, admin_h, "Sam", "supporter")

        r = client.post(
            f"{API}/work-plans/",
            headers=bob_h,
            json={
                "horizon": "week",
                "period_start": week,
                "category": "current_job",
                "title": "Ship the release",
            },
        )
        assert r.status_code == 201, r.text
        item = r.json()["id"]
        r = client.post(f"{API}/work-plans/{item}/comments", headers=bob_h, json={"body": "mine"})
        bob_comment = r.json()["id"]

        # --- Before the grant, a member sees only their own plan ---------
        check(
            "member can't see overview",
            client.get(f"{API}/work-plans/overview?week_start={week}", headers=lead_h).status_code
            == 403,
        )
        check(
            "member can't read another plan",
            client.get(
                f"{API}/work-plans/?week_start={week}&user_id={bob}", headers=lead_h
            ).status_code
            == 403,
        )
        check(
            "member can't comment on another plan",
            client.post(
                f"{API}/work-plans/{item}/comments", headers=lead_h, json={"body": "hi"}
            ).status_code
            == 403,
        )

        # --- Granting ---------------------------------------------------
        check("member can't grant reviewer", _grant(client, bob_h, lead, True).status_code == 403)
        check("supporter can't be a reviewer", _grant(client, admin_h, sam, True).status_code == 400)
        check("manager can't be a reviewer", _grant(client, admin_h, mgr, True).status_code == 400)
        r = _grant(client, admin_h, lead, True)
        check("admin grants reviewer", r.status_code == 200, r.text)
        me = client.get(f"{API}/users/me", headers=lead_h).json()
        check("/users/me shows the grant", me.get("can_review_work_plans") is True, str(me))
        users = {u["id"]: u for u in client.get(f"{API}/admin/api/users", headers=admin_h).json()}
        check("users list shows the grant", users[lead]["can_review_work_plans"] is True)
        check("others stay off", users[bob]["can_review_work_plans"] is False)

        # --- Reviewer can read and comment ------------------------------
        r = client.get(f"{API}/work-plans/overview?week_start={week}", headers=lead_h)
        check("reviewer sees overview", r.status_code == 200, r.text)
        rows = {row["user_id"]: row for row in r.json().get("users", [])}
        check(
            "overview has bob's goal",
            bob in rows and [i["id"] for i in rows[bob]["items"]] == [item],
            str(rows.get(bob)),
        )
        r = client.get(f"{API}/work-plans/?week_start={week}&user_id={bob}", headers=lead_h)
        check("reviewer reads bob's plan", r.status_code == 200 and len(r.json()["week"]) == 1, r.text)
        check(
            "reviewer opens bob's goal",
            client.get(f"{API}/work-plans/{item}", headers=lead_h).status_code == 200,
        )
        check(
            "reviewer reads comments",
            client.get(f"{API}/work-plans/{item}/comments", headers=lead_h).status_code == 200,
        )
        r = client.post(f"{API}/work-plans/{item}/comments", headers=lead_h, json={"body": "Looks good"})
        check("reviewer comments", r.status_code == 201, r.text)
        lead_comment = r.json().get("id")

        # --- ...but can't change the plan --------------------------------
        check(
            "reviewer can't edit",
            client.patch(
                f"{API}/work-plans/{item}", headers=lead_h, json={"done": True}
            ).status_code
            == 403,
        )
        check(
            "reviewer can't move to next week",
            client.post(f"{API}/work-plans/{item}/next-week", headers=lead_h).status_code == 403,
        )
        check(
            "reviewer can't delete",
            client.delete(f"{API}/work-plans/{item}", headers=lead_h).status_code == 403,
        )
        r = client.post(
            f"{API}/work-plans/",
            headers=lead_h,
            json={
                "horizon": "month",
                "period_start": week[:8] + "01",
                "title": "Added by reviewer",
                "user_id": bob,
            },
        )
        check("reviewer can't add to bob's plan", r.status_code == 403, r.text)
        check(
            "reviewer can't delete bob's comment",
            client.delete(f"{API}/work-plans/comments/{bob_comment}", headers=lead_h).status_code
            == 403,
        )
        check(
            "reviewer deletes own comment",
            client.delete(f"{API}/work-plans/comments/{lead_comment}", headers=lead_h).status_code
            == 204,
        )
        check(
            "reviewer still has no admin site",
            client.get(f"{API}/admin/api/users", headers=lead_h).status_code == 403,
        )
        check(
            "bob's goal unchanged",
            client.get(f"{API}/work-plans/{item}", headers=bob_h).json()["done"] is False,
        )
        check(
            "non-reviewer still can't read the reviewer's plan",
            client.get(
                f"{API}/work-plans/?week_start={week}&user_id={lead}", headers=bob_h
            ).status_code
            == 403,
        )

        # --- Turning it off, and role changes ---------------------------
        _grant(client, admin_h, lead, False)
        check(
            "revoked reviewer loses overview",
            client.get(f"{API}/work-plans/overview?week_start={week}", headers=lead_h).status_code
            == 403,
        )
        _grant(client, admin_h, lead, True)
        client.post(f"{API}/admin/api/users/{lead}/set-role", headers=admin_h, json={"role": "manager"})
        client.post(f"{API}/admin/api/users/{lead}/set-role", headers=admin_h, json={"role": "member"})
        users = {u["id"]: u for u in client.get(f"{API}/admin/api/users", headers=admin_h).json()}
        check(
            "leaving member clears the grant",
            users[lead]["can_review_work_plans"] is False,
            str(users[lead]),
        )

    # --- Migration is safe to run again ---------------------------------
    with engine.begin() as conn:
        _add_user_work_plan_reviewer(conn)
        check("migration column exists", "can_review_work_plans" in _columns(conn, "users"))

    print()
    if failures:
        print(f"FAILURES ({len(failures)}): {failures}")
        return 1
    print("ALL WORK PLAN REVIEWER TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
