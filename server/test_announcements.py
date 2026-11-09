"""Tests for member announcements.

Isolates a temp SQLite DB, then checks that staff author announcements, only
members read them (with per-member read state), and supporters are shut out.
Run:

    python -m server.test_announcements
"""

from __future__ import annotations

import os
import tempfile
import uuid

# Isolate the DB before importing the app/settings.
_tmp_db = os.path.join(tempfile.gettempdir(), f"baymax_announcements_{uuid.uuid4().hex}.db")
os.environ["BAYMAX_DATABASE_URL"] = f"sqlite:///{_tmp_db}"
os.environ["BAYMAX_JWT_SECRET"] = "announcements-test-secret"
os.environ["BAYMAX_BOOTSTRAP_ADMIN_EMAIL"] = "admin@baymax.example.com"
os.environ["BAYMAX_BOOTSTRAP_ADMIN_PASSWORD"] = "adminpw"
os.environ["BAYMAX_LICENSE_SIGNING_KEY"] = ""
os.environ["BAYMAX_ENVIRONMENT"] = "development"

from fastapi.testclient import TestClient  # noqa: E402

from server.main import app  # noqa: E402
from server.test_profile_settings import _png_bytes  # noqa: E402

failures: list[str] = []
API = "/api/v1"
BASE = f"{API}/announcements"


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}{(' - ' + detail) if detail and not condition else ''}")
    if not condition:
        failures.append(name)


def _login(client: TestClient, email: str, password: str) -> dict:
    r = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_user(client: TestClient, admin_h: dict, role: str) -> dict:
    email = f"{role}-{uuid.uuid4().hex[:6]}@baymax.example.com"
    r = client.post(
        f"{API}/admin/api/users",
        headers=admin_h,
        json={"email": email, "name": role.title(), "password": "userpass12", "role": role},
    )
    assert r.status_code == 201, r.text
    return _login(client, email, "userpass12")


def main() -> int:
    with TestClient(app) as client:
        admin_h = _login(client, "admin@baymax.example.com", "adminpw")
        manager_h = _make_user(client, admin_h, "manager")
        member_h = _make_user(client, admin_h, "member")
        member2_h = _make_user(client, admin_h, "member")
        supporter_h = _make_user(client, admin_h, "supporter")

        # Access control: everything needs a token, supporters are shut out.
        check("list needs auth", client.get(BASE).status_code == 401)
        check("unread-count needs auth", client.get(f"{BASE}/unread-count").status_code == 401)
        check("manage needs auth", client.get(f"{BASE}/manage").status_code == 401)
        for label, method, path in (
            ("list", "get", ""),
            ("unread-count", "get", "/unread-count"),
            ("read-all", "post", "/read-all"),
        ):
            r = getattr(client, method)(BASE + path, headers=supporter_h)
            check(f"supporter {label} forbidden", r.status_code == 403, r.text)
            r = getattr(client, method)(BASE + path, headers=admin_h)
            check(f"admin {label} is member-only", r.status_code == 403, r.text)

        # Nothing yet.
        r = client.get(BASE, headers=member_h)
        check("empty list", r.status_code == 200 and r.json() == {"items": [], "unread_count": 0}, r.text)

        # Members can't author.
        r = client.post(BASE, headers=member_h, json={"title": "x", "body_md": "x"})
        check("member create forbidden", r.status_code == 403, r.text)
        r = client.post(BASE, headers=supporter_h, json={"title": "x", "body_md": "x"})
        check("supporter create forbidden", r.status_code == 403, r.text)
        check("member manage forbidden", client.get(f"{BASE}/manage", headers=member_h).status_code == 403)
        r = client.post(BASE, headers=admin_h, json={"title": "   ", "body_md": ""})
        check("blank title rejected", r.status_code == 422, r.text)

        # Authoring, with an image from the shared asset store.
        r = client.post(
            f"{API}/training/assets",
            headers=admin_h,
            files={"file": ("Notice Banner.PNG", _png_bytes(), "image/png")},
        )
        asset = r.json()
        body = f"## Heads up\n\n**New** policy.\n\n![banner]({asset['url']})\n"
        r = client.post(BASE, headers=admin_h, json={"title": "  Policy update ", "body_md": body})
        first = r.json() if r.status_code == 201 else {}
        check("admin create", r.status_code == 201 and first.get("title") == "Policy update", r.text)
        r = client.post(BASE, headers=manager_h, json={"title": "Second", "body_md": "Hello"})
        second = r.json() if r.status_code == 201 else {}
        check("manager create", r.status_code == 201, r.text)

        # Member side: newest first, unread by default, markdown intact.
        r = client.get(BASE, headers=member_h)
        data = r.json()
        titles = [x["title"] for x in data["items"]]
        check("member sees both, newest first", titles == ["Second", "Policy update"], str(titles))
        check("all unread", data["unread_count"] == 2 and not any(x["read"] for x in data["items"]))
        check("markdown preserved", data["items"][1]["body_md"] == body)
        r = client.get(f"{BASE}/unread-count", headers=member_h)
        check("unread-count", r.json() == {"count": 2}, r.text)
        r = client.get(asset["url"], headers=member_h)
        check("member loads announcement image", r.status_code == 200 and r.headers["content-type"] == "image/png")

        # Read state is per member and idempotent.
        r = client.post(f"{BASE}/{first['id']}/read", headers=member_h)
        client.post(f"{BASE}/{first['id']}/read", headers=member_h)
        check("mark read", r.status_code == 204, r.text)
        check("unread drops", client.get(f"{BASE}/unread-count", headers=member_h).json()["count"] == 1)
        check("other member unaffected", client.get(f"{BASE}/unread-count", headers=member2_h).json()["count"] == 2)
        items = {x["id"]: x["read"] for x in client.get(BASE, headers=member_h).json()["items"]}
        check("read flag", items == {first["id"]: True, second["id"]: False}, str(items))
        r = client.post(f"{BASE}/nope/read", headers=member_h)
        check("unknown id 404", r.status_code == 404, r.text)

        # Editing keeps read state.
        r = client.put(
            f"{BASE}/{first['id']}",
            headers=manager_h,
            json={"title": "Policy update v2", "body_md": "Changed"},
        )
        check("update", r.status_code == 200 and r.json()["body_md"] == "Changed", r.text)
        check("edit keeps read state", client.get(f"{BASE}/unread-count", headers=member_h).json()["count"] == 1)
        r = client.put(f"{BASE}/{first['id']}", headers=member_h, json={"title": "x", "body_md": ""})
        check("member update forbidden", r.status_code == 403, r.text)
        r = client.put(f"{BASE}/nope", headers=admin_h, json={"title": "x", "body_md": ""})
        check("update unknown 404", r.status_code == 404, r.text)

        # A later announcement is unread again for everyone.
        r = client.post(BASE, headers=admin_h, json={"title": "Third", "body_md": ""})
        third = r.json()
        check("new announcement unread", client.get(f"{BASE}/unread-count", headers=member_h).json()["count"] == 2)

        r = client.post(f"{BASE}/read-all", headers=member_h)
        check("read-all", r.status_code == 204 and client.get(f"{BASE}/unread-count", headers=member_h).json()["count"] == 0)
        check("read-all is per member", client.get(f"{BASE}/unread-count", headers=member2_h).json()["count"] == 3)

        # Staff overview counts members only.
        r = client.get(f"{BASE}/manage", headers=admin_h)
        manage = r.json()
        reads = {x["id"]: x["read_count"] for x in manage["items"]}
        check("manage lists all", len(manage["items"]) == 3, r.text)
        check("manage member_count", manage["member_count"] == 2, str(manage["member_count"]))
        check("manage read_count", reads[first["id"]] == 1 and reads[third["id"]] == 1, str(reads))
        check("manager can manage", client.get(f"{BASE}/manage", headers=manager_h).status_code == 200)

        # Delete removes the item and its read rows.
        r = client.delete(f"{BASE}/{second['id']}", headers=member_h)
        check("member delete forbidden", r.status_code == 403, r.text)
        r = client.delete(f"{BASE}/{second['id']}", headers=admin_h)
        check("delete", r.status_code == 204, r.text)
        data = client.get(BASE, headers=member_h).json()
        check("deleted gone", [x["title"] for x in data["items"]] == ["Third", "Policy update v2"])
        check("unread after delete", client.get(f"{BASE}/unread-count", headers=member2_h).json()["count"] == 2)
        r = client.delete(f"{BASE}/{second['id']}", headers=admin_h)
        check("delete twice 404", r.status_code == 404, r.text)

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
