"""Tests for member self-service profiles, supporter teams, and archiving.

Isolates a temp SQLite DB, then exercises the admin and member profile/team
endpoints and the team-link backfill migration. Run:

    server/.venv/bin/python -m server.test_member_profiles
"""

from __future__ import annotations

import os
import tempfile
import uuid

# Isolate the DB before importing the app/settings.
_tmp_db = os.path.join(tempfile.gettempdir(), f"baymax_member_profiles_{uuid.uuid4().hex}.db")
os.environ["BAYMAX_DATABASE_URL"] = f"sqlite:///{_tmp_db}"
os.environ["BAYMAX_JWT_SECRET"] = "member-profiles-test-secret"
os.environ["BAYMAX_BOOTSTRAP_ADMIN_EMAIL"] = "admin@baymax.example.com"
os.environ["BAYMAX_BOOTSTRAP_ADMIN_PASSWORD"] = "adminpw"
os.environ["BAYMAX_ENVIRONMENT"] = "development"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from server.database import SessionLocal, engine  # noqa: E402
from server.main import app  # noqa: E402
from server.migrations import _add_member_teams_and_profile_archive  # noqa: E402
from server.models import Application, Profile  # noqa: E402

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


def _assigned(client: TestClient, admin_h: dict, key: str) -> set[str]:
    rows = client.get(f"{API}/admin/profiles", headers=admin_h).json()
    return set(next(p for p in rows if p["key"] == key)["assigned_user_ids"])


def admin_id(client: TestClient, admin_h: dict) -> str:
    return client.get(f"{API}/users/me", headers=admin_h).json()["id"]


def main() -> int:
    with TestClient(app) as client:
        admin_h = _login(client, "admin@baymax.example.com", "adminpw")
        alice, alice_h = _user(client, admin_h, "Alice", "member")
        bob, bob_h = _user(client, admin_h, "Bob", "member")
        sam, sam_h = _user(client, admin_h, "Sam", "supporter")
        tia, _ = _user(client, admin_h, "Tia", "supporter")

        # --- Team links (admin only) -------------------------------------
        r = client.post(
            f"{API}/admin/api/users/{sam}/set-member", headers=alice_h, json={"member_id": alice}
        )
        check("member can't link supporters", r.status_code == 403, r.text)
        r = client.post(
            f"{API}/admin/api/users/{sam}/set-member", headers=admin_h, json={"member_id": alice}
        )
        check("admin links supporter to member", r.status_code == 200, r.text)
        r = client.post(
            f"{API}/admin/api/users/{alice}/set-member", headers=admin_h, json={"member_id": bob}
        )
        check("only supporters can be linked", r.status_code == 400, r.text)
        client.post(f"{API}/admin/api/users/{tia}/set-member", headers=admin_h, json={"member_id": bob})
        users = {u["id"]: u for u in client.get(f"{API}/admin/api/users", headers=admin_h).json()}
        check("users list shows member", users[sam]["member_name"] == "Alice", str(users[sam]))

        r = client.get(f"{API}/team/member/supporters", headers=alice_h)
        check(
            "member sees own team before any profile",
            r.status_code == 200 and [s["id"] for s in r.json()] == [sam],
            r.text,
        )

        # --- Member creates/imports profiles -----------------------------
        r = client.post(f"{API}/team/member/profiles", headers=alice_h, json={"name": "John Smith"})
        check("member creates profile", r.status_code == 201, r.text)
        john = r.json()["key"]
        check("server generates key", john == "john-smith", john)
        check("creator auto-assigned", _assigned(client, admin_h, john) == {alice})

        r = client.post(f"{API}/team/member/profiles", headers=bob_h, json={"name": "John Smith"})
        check(
            "same name never overwrites",
            r.status_code == 201 and r.json()["key"] == "john-smith-2",
            r.text,
        )
        with SessionLocal() as db:
            first = db.query(Profile).filter_by(key="john-smith").one()
            check("first profile untouched", first.name == "John Smith")

        r = client.post(
            f"{API}/team/member/profiles/import",
            headers=alice_h,
            json={"profiles": [{"name": "Jane Doe", "key": "john-smith"}, {"name": "Jane Doe"}]},
        )
        keys = [p["key"] for p in r.json()] if r.status_code == 201 else []
        check("import dedupes keys and ignores file key", keys == ["jane-doe", "jane-doe-2"], r.text)
        r = client.post(f"{API}/team/member/profiles/import", headers=alice_h, json=[{"email": "x@y.z"}])
        check("import without a name rejected", r.status_code == 400, r.text)
        r = client.post(f"{API}/team/member/profiles", headers=sam_h, json={"name": "Nope"})
        check("supporter can't create profiles", r.status_code == 403, r.text)

        # --- Member assigns own supporters -------------------------------
        r = client.put(
            f"{API}/team/member/profiles/{john}/supporters",
            headers=alice_h,
            json={"supporter_ids": [sam]},
        )
        check("member assigns own supporter", r.status_code == 200, r.text)
        check("supporter on profile", _assigned(client, admin_h, john) == {alice, sam})
        r = client.put(
            f"{API}/team/member/profiles/{john}/supporters",
            headers=alice_h,
            json={"supporter_ids": [sam, tia]},
        )
        check("member can't assign another member's supporter", r.status_code == 403, r.text)
        r = client.put(
            f"{API}/team/member/profiles/john-smith-2/supporters",
            headers=alice_h,
            json={"supporter_ids": [sam]},
        )
        check("member can't assign to a profile they aren't on", r.status_code == 404, r.text)

        r = client.put(
            f"{API}/team/member/assignments",
            headers=alice_h,
            json={"assignments": {"jane-doe": [sam], john: []}},
        )
        check("grid save", r.status_code == 200, r.text)
        check("grid removed", _assigned(client, admin_h, john) == {alice})
        check("grid added", _assigned(client, admin_h, "jane-doe") == {alice, sam})
        r = client.put(
            f"{API}/team/member/assignments",
            headers=alice_h,
            json={"assignments": {john: [sam], "john-smith-2": [sam]}},
        )
        check("grid save is all or nothing", r.status_code == 404 and _assigned(client, admin_h, john) == {alice}, r.text)

        # --- Admin assignment invariant ----------------------------------
        r = client.put(
            f"{API}/admin/profiles/{john}/assignees", headers=admin_h, json={"user_ids": [alice, tia]}
        )
        check("admin can't add supporter without their member", r.status_code == 400, r.text)
        r = client.put(
            f"{API}/admin/profiles/{john}/assignees",
            headers=admin_h,
            json={"user_ids": [alice, bob, tia]},
        )
        check("admin adds member and their supporter", r.status_code == 200, r.text)
        r = client.delete(f"{API}/admin/profiles/{john}/assign/{bob}", headers=admin_h)
        check(
            "unassigning a member drops their supporters",
            r.status_code == 204 and _assigned(client, admin_h, john) == {alice},
            r.text,
        )
        r = client.post(f"{API}/admin/profiles/{john}/assign/{tia}", headers=admin_h)
        check("legacy assign keeps the rule", r.status_code == 400, r.text)

        # Re-linking a supporter drops assignments the new member isn't on.
        client.put(f"{API}/team/member/profiles/{john}/supporters", headers=alice_h, json={"supporter_ids": [sam]})
        client.post(f"{API}/admin/api/users/{sam}/set-member", headers=admin_h, json={"member_id": bob})
        check("relink drops foreign assignments", sam not in _assigned(client, admin_h, john))
        client.post(f"{API}/admin/api/users/{sam}/set-member", headers=admin_h, json={"member_id": alice})

        # Role changes keep links valid.
        client.post(f"{API}/admin/api/users/{bob}/set-role", headers=admin_h, json={"role": "manager"})
        users = {u["id"]: u for u in client.get(f"{API}/admin/api/users", headers=admin_h).json()}
        check("member losing role unlinks their team", users[tia]["member_id"] is None, str(users[tia]))

        # --- Archive -----------------------------------------------------
        r = client.post(f"{API}/license", headers=admin_h, json={"label": "Team", "seat_limit": 2})
        r = client.post(
            f"{API}/license/activate",
            headers=alice_h,
            json={
                "license_key": r.json()["license_key"],
                "hardware_fingerprint": "a" * 64,
                "device_id": "dev-alice",
                "app_version": "2.6.1",
            },
        )
        check("license activate", r.status_code == 200 and r.json().get("valid"), r.text)
        lic_h = {**alice_h, "X-License-Token": r.json()["lease_token"]}
        with SessionLocal() as db:
            pid = db.query(Profile).filter_by(key=john).one().id
            db.add(Application(id=str(uuid.uuid4()), user_id=alice, profile_id=pid, company_name="Acme"))
            db.commit()
        keys = [p["key"] for p in client.get(f"{API}/users/me/profiles", headers=lic_h).json()]
        check("desktop gets active profile", john in keys, str(keys))

        r = client.delete(f"{API}/admin/profiles/{john}", headers=alice_h)
        check("member can't delete", r.status_code == 403, r.text)
        r = client.post(f"{API}/team/member/profiles/{john}/archive", headers=alice_h)
        check("member archives", r.status_code == 200, r.text)
        keys = [p["key"] for p in client.get(f"{API}/users/me/profiles", headers=lic_h).json()]
        check("archived profile left off desktop", john not in keys, str(keys))
        keys = [p["key"] for p in client.get(f"{API}/team/member/profiles", headers=alice_h).json()]
        check("archived profile hidden from member", john not in keys, str(keys))
        with SessionLocal() as db:
            check("archive keeps applications", db.query(Application).filter_by(profile_id=pid).count() == 1)
        r = client.post(f"{API}/admin/profiles/{john}/restore", headers=alice_h)
        check("member can't restore", r.status_code == 403, r.text)
        r = client.post(f"{API}/admin/profiles/{john}/restore", headers=admin_h)
        check("admin restores", r.status_code == 200 and r.json()["archived_at"] is None, r.text)
        r = client.get(f"{API}/admin/profiles/{john}/usage", headers=admin_h)
        check("usage counts", r.json() == {"applications": 1, "interviews": 0}, r.text)

        # --- Archiving users --------------------------------------------
        client.put(f"{API}/team/member/profiles/{john}/supporters", headers=alice_h, json={"supporter_ids": [sam]})
        r = client.post(f"{API}/admin/api/users/{sam}/archive", headers=alice_h, json={})
        check("member can't archive", r.status_code == 403, r.text)
        r = client.post(f"{API}/admin/api/users/{sam}/archive", headers=admin_h, json={})
        check("admin archives supporter", r.status_code == 200, r.text)
        r = client.post(f"{API}/auth/login", json={"email": "sam@baymax.example.com", "password": "password1"})
        check("archived user can't sign in", r.status_code != 200, r.text)
        ids = [u["id"] for u in client.get(f"{API}/admin/api/users", headers=admin_h).json()]
        check("archived hidden from people lists", sam not in ids)
        rows = client.get(f"{API}/admin/api/users?include_archived=true", headers=admin_h).json()
        check("users page still sees archived", any(u["id"] == sam and u["archived_at"] for u in rows))
        r = client.get(f"{API}/team/member/supporters", headers=alice_h)
        check("archived supporter hidden from member", sam not in [s["id"] for s in r.json()], r.text)
        check("archived hidden from profile assignees", sam not in _assigned(client, admin_h, john))
        rankings = client.get(f"{API}/team/observability", headers=alice_h).json()
        check(
            "archived left out of rankings",
            sam not in [row["id"] for row in rankings["per_supporters"]],
        )
        client.put(f"{API}/team/member/profiles/{john}/supporters", headers=alice_h, json={"supporter_ids": []})
        client.put(f"{API}/admin/profiles/{john}/assignees", headers=admin_h, json={"user_ids": [alice]})
        client.post(f"{API}/admin/api/users/{sam}/restore", headers=admin_h)
        check("restore is lossless", sam in _assigned(client, admin_h, john))

        r = client.post(f"{API}/admin/api/users/{alice}/archive", headers=admin_h, json={"supporters": "move"})
        check("moving supporters needs a target", r.status_code == 400, r.text)
        cara, _ = _user(client, admin_h, "Cara", "member")
        r = client.post(
            f"{API}/admin/api/users/{alice}/archive",
            headers=admin_h,
            json={"supporters": "move", "move_to": cara},
        )
        check("archive member, move team", r.status_code == 200 and r.json()["revoked_seats"] == 1, r.text)
        users = {u["id"]: u for u in client.get(f"{API}/admin/api/users", headers=admin_h).json()}
        check("team moved to new member", users[sam]["member_id"] == cara, str(users.get(sam)))
        r = client.get(f"{API}/admin/profiles/{john}/usage", headers=admin_h)
        check("archived member's data kept", r.json()["applications"] == 1, r.text)
        r = client.post(f"{API}/admin/api/users/{alice}/restore", headers=admin_h)
        check("restore gives seat back", r.status_code == 200 and r.json()["restored_seats"] == 1, r.text)
        r = client.post(f"{API}/admin/api/users/{admin_id(client, admin_h)}/archive", headers=admin_h, json={})
        check("can't archive yourself", r.status_code == 400, r.text)
        alice_h = _login(client, "alice@baymax.example.com", "password1")
        client.post(f"{API}/admin/api/users/{sam}/set-member", headers=admin_h, json={"member_id": alice})

        # --- Backfill migration -----------------------------------------
        client.put(f"{API}/team/member/profiles/jane-doe/supporters", headers=alice_h, json={"supporter_ids": [sam]})
        with engine.begin() as conn:
            conn.execute(text("UPDATE users SET member_id = NULL"))
            # Tia shares profiles with Alice and Bob (Bob is a member again).
            conn.execute(text("UPDATE users SET role = 'member' WHERE id = :b"), {"b": bob})
            conn.execute(
                text(
                    "INSERT INTO profile_assignments (user_id, profile_id, assigned_at) "
                    "VALUES (:u, :p, CURRENT_TIMESTAMP)"
                ),
                {"u": tia, "p": pid},
            )
            conn.execute(
                text(
                    "INSERT INTO profile_assignments (user_id, profile_id, assigned_at) "
                    "SELECT :u, profile_id, CURRENT_TIMESTAMP FROM profile_assignments "
                    "WHERE user_id = :b"
                ),
                {"u": tia, "b": bob},
            )
            _add_member_teams_and_profile_archive(conn)
            links = dict(conn.execute(text("SELECT id, member_id FROM users")).fetchall())
        check("backfill links single-member supporter", links[sam] == alice, str(links[sam]))
        check("backfill leaves ambiguous supporter unlinked", links[tia] is None, str(links[tia]))

    print()
    if failures:
        print(f"FAILURES ({len(failures)}): {failures}")
        return 1
    print("ALL MEMBER PROFILE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
