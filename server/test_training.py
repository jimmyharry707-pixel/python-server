"""Tests for the supporter training course APIs.

Isolates a temp SQLite DB, checks the course is seeded from
docs/bidder-training, then exercises reading, progress, admin editing and image
assets. Run:

    python -m server.test_training
"""

from __future__ import annotations

import os
import tempfile
import uuid

# Isolate the DB before importing the app/settings.
_tmp_db = os.path.join(tempfile.gettempdir(), f"baymax_training_{uuid.uuid4().hex}.db")
os.environ["BAYMAX_DATABASE_URL"] = f"sqlite:///{_tmp_db}"
os.environ["BAYMAX_JWT_SECRET"] = "training-test-secret"
os.environ["BAYMAX_BOOTSTRAP_ADMIN_EMAIL"] = "admin@baymax.example.com"
os.environ["BAYMAX_BOOTSTRAP_ADMIN_PASSWORD"] = "adminpw"
os.environ["BAYMAX_LICENSE_SIGNING_KEY"] = ""
os.environ["BAYMAX_ENVIRONMENT"] = "development"

from fastapi.testclient import TestClient  # noqa: E402

from server.main import app  # noqa: E402
from server.test_profile_settings import _png_bytes  # noqa: E402
from server.training_seed import load_seed  # noqa: E402

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


def main() -> int:
    with TestClient(app) as client:
        admin_h = _login(client, "admin@baymax.example.com", "adminpw")
        r = client.post(
            f"{API}/admin/api/users",
            headers=admin_h,
            json={
                "email": "bidder@baymax.example.com",
                "name": "Bidder",
                "password": "bidderpass1",
                "role": "supporter",
            },
        )
        check("create supporter", r.status_code == 201, r.text)
        sup_h = _login(client, "bidder@baymax.example.com", "bidderpass1")

        # Seeded course
        r = client.get(f"{API}/training/course", headers=sup_h)
        course = r.json() if r.status_code == 200 else {}
        lessons = course.get("lessons", [])
        slugs = [lesson["slug"] for lesson in lessons]
        check("course readable by supporter", r.status_code == 200, r.text)
        check("supporter cannot edit", course.get("can_edit") is False)
        check("seed imported chapters", len(lessons) == 16, str(slugs))
        check("overview first, troubleshooting last", slugs[:1] == ["overview"] and slugs[-1] == "troubleshooting")
        check("revision notes not imported", "revision-notes" not in slugs)
        screening = next((x for x in lessons if x["slug"] == "screening"), {})
        body = screening.get("body_md", "")
        check("chapter title cleaned", screening.get("title") == "Screen the job before you start")
        check("nav line stripped", "[←" not in body)
        check("lesson links rewritten", "[Next:" not in body and "](/training/" in body)
        check("image links rewritten", "](/api/v1/training/assets/04-careercraft-duplicate-toast.png)" in body)
        check("no .md links left", ".md)" not in "".join(x["body_md"] for x in lessons))

        r = client.get(f"{API}/training/assets/04-careercraft-duplicate-toast.png", headers=sup_h)
        check("seeded image served", r.status_code == 200 and r.headers["content-type"] == "image/png")
        check("image needs auth", client.get(f"{API}/training/assets/icon-settings.png").status_code == 401)

        # Progress
        lid = screening["id"]
        r = client.post(f"{API}/training/lessons/{lid}/complete", headers=sup_h)
        client.post(f"{API}/training/lessons/{lid}/complete", headers=sup_h)  # idempotent
        done = client.get(f"{API}/training/course", headers=sup_h).json()["completed"]
        check("mark complete", r.status_code == 204 and done == [lid], str(done))
        client.delete(f"{API}/training/lessons/{lid}/complete", headers=sup_h)
        done = client.get(f"{API}/training/course", headers=sup_h).json()["completed"]
        check("unmark complete", done == [])

        # Supporter can't author
        r = client.put(
            f"{API}/training/lessons/{lid}", headers=sup_h, json={"title": "x", "body_md": "x"}
        )
        check("supporter edit forbidden", r.status_code == 403, r.text)
        r = client.post(f"{API}/training/lessons", headers=sup_h, json={"title": "x"})
        check("supporter create forbidden", r.status_code == 403, r.text)

        # Admin authoring
        check("admin can edit", client.get(f"{API}/training/course", headers=admin_h).json()["can_edit"])
        r = client.put(
            f"{API}/training/lessons/{lid}",
            headers=admin_h,
            json={"title": "Screening", "slug": "screening", "body_md": "## New body\n"},
        )
        check("admin update", r.status_code == 200 and r.json()["body_md"] == "## New body\n", r.text)
        r = client.put(
            f"{API}/training/lessons/{lid}",
            headers=admin_h,
            json={"title": "Screening", "slug": "overview", "body_md": ""},
        )
        check("slug clash rejected", r.status_code == 409, r.text)
        r = client.put(
            f"{API}/training/lessons/{lid}",
            headers=admin_h,
            json={"title": "Screening", "slug": "Bad Slug", "body_md": ""},
        )
        check("bad slug rejected", r.status_code == 422, r.text)

        r = client.post(f"{API}/training/lessons", headers=admin_h, json={"title": "Overview"})
        new = r.json() if r.status_code == 200 else {}
        check("create dedupes slug", new.get("slug") == "overview-2", r.text)
        check("create appends", new.get("position") == len(lessons), str(new))

        ids = [x["id"] for x in client.get(f"{API}/training/course", headers=admin_h).json()["lessons"]]
        reordered = [ids[-1], *ids[:-1]]
        r = client.post(f"{API}/training/lessons/reorder", headers=admin_h, json={"ids": reordered})
        after = [x["id"] for x in client.get(f"{API}/training/course", headers=admin_h).json()["lessons"]]
        check("reorder", r.status_code == 200 and after == reordered, r.text)
        r = client.post(f"{API}/training/lessons/reorder", headers=admin_h, json={"ids": ids[:-1]})
        check("partial reorder rejected", r.status_code == 409)

        client.post(f"{API}/training/lessons/{new['id']}/complete", headers=sup_h)
        r = client.delete(f"{API}/training/lessons/{new['id']}", headers=admin_h)
        done = client.get(f"{API}/training/course", headers=sup_h).json()["completed"]
        check("delete lesson + progress", r.status_code == 204 and new["id"] not in done)

        # Assets
        png = _png_bytes()
        r = client.post(
            f"{API}/training/assets",
            headers=admin_h,
            files={"file": ("My Screenshot.PNG", png, "image/png")},
        )
        asset = r.json() if r.status_code == 200 else {}
        check("upload image", asset.get("name") == "my-screenshot.png", r.text)
        check("upload url", asset.get("url") == "/api/v1/training/assets/my-screenshot.png")
        r = client.post(
            f"{API}/training/assets",
            headers=admin_h,
            files={"file": ("my screenshot.png", png, "image/png")},
        )
        check("upload keeps names unique", r.json().get("name") == "my-screenshot-2.png", r.text)
        r = client.post(
            f"{API}/training/assets",
            headers=admin_h,
            files={"file": ("evil.svg", b"<svg onload=alert(1)></svg>", "image/svg+xml")},
        )
        check("svg rejected", r.status_code == 415, r.text)
        r = client.post(
            f"{API}/training/assets",
            headers=sup_h,
            files={"file": ("x.png", png, "image/png")},
        )
        check("supporter upload forbidden", r.status_code == 403)
        names = [a["name"] for a in client.get(f"{API}/training/assets", headers=admin_h).json()]
        check("list assets", "my-screenshot.png" in names and len(names) == len(load_seed()[1]) + 2, str(len(names)))
        r = client.delete(f"{API}/training/assets/my-screenshot-2.png", headers=admin_h)
        check("delete asset", r.status_code == 204)

        # Update from docs
        check(
            "supporter docs update forbidden",
            client.get(f"{API}/training/docs-update", headers=sup_h).status_code == 403
            and client.post(f"{API}/training/docs-update", headers=sup_h).status_code == 403,
        )
        r = client.post(f"{API}/training/lessons", headers=admin_h, json={"title": "Team extras"})
        extra = r.json()
        client.post(f"{API}/training/lessons/{lid}/complete", headers=sup_h)
        client.delete(f"{API}/training/assets/icon-settings.png", headers=admin_h)
        r = client.get(f"{API}/training/docs-update", headers=admin_h)
        plan = r.json() if r.status_code == 200 else {}
        by_slug = {x["slug"]: x for x in plan.get("lessons", [])}
        check("docs preview", r.status_code == 200 and not plan.get("up_to_date"), r.text)
        check(
            "website edit flagged",
            by_slug.get("screening", {}).get("status") == "changed"
            and by_slug["screening"]["edited_on_site"],
            str(by_slug.get("screening")),
        )
        check("unchanged lesson", by_slug.get("glossary", {}).get("status") in ("same", "moved"))
        kept = [x["slug"] for x in plan.get("kept", [])]
        check("website-only lesson kept", kept == ["team-extras"])
        check("missing image listed", plan.get("images", {}).get("new") == ["icon-settings.png"])

        r = client.post(f"{API}/training/docs-update", headers=admin_h)
        check("docs update applied", r.status_code == 200, r.text)
        course = client.get(f"{API}/training/course", headers=sup_h).json()
        after = {x["slug"]: x for x in course["lessons"]}
        seed_lessons = load_seed()[0]
        docs_order = [s.slug for s in seed_lessons] + ["team-extras"]
        check("course order is the docs", [x["slug"] for x in course["lessons"]] == docs_order)
        screening_doc = next(s.body_md for s in seed_lessons if s.slug == "screening")
        check("lesson body restored", after["screening"]["body_md"] == screening_doc)
        check(
            "lesson id and progress kept",
            after["screening"]["id"] == lid and course["completed"] == [lid],
        )
        check("website lesson content kept", after["team-extras"]["body_md"] == extra["body_md"])
        r = client.get(f"{API}/training/assets/icon-settings.png", headers=sup_h)
        check("image restored", r.status_code == 200)
        names = [a["name"] for a in client.get(f"{API}/training/assets", headers=admin_h).json()]
        check("website image kept", "my-screenshot.png" in names)
        plan = client.get(f"{API}/training/docs-update", headers=admin_h).json()
        check("up to date after update", plan.get("up_to_date") is True, str(plan))

    print(f"\n{len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
