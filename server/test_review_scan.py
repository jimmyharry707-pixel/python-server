"""Tests for the daily review scan.

Builds a made-up day for one supporter (tracker records, a resumes ZIP and a
snapshots ZIP), runs a scan through the API and checks what it finds, who can
see what, and that uploaded files are deleted. Run:

    python -m server.test_review_scan
"""

from __future__ import annotations

import io
import os
import tempfile
import time
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

# Isolate the DB and the scan folder before importing the app/settings.
_tmp = Path(tempfile.mkdtemp(prefix="baymax_review_"))
os.environ["BAYMAX_DATABASE_URL"] = f"sqlite:///{_tmp / 'db.sqlite'}"
os.environ["BAYMAX_SCAN_DIR"] = str(_tmp / "scans")
os.environ["BAYMAX_JWT_SECRET"] = "review-test-secret"
os.environ["BAYMAX_BOOTSTRAP_ADMIN_EMAIL"] = "admin@baymax.example.com"
os.environ["BAYMAX_BOOTSTRAP_ADMIN_PASSWORD"] = "adminpw"
os.environ["BAYMAX_LICENSE_SIGNING_KEY"] = ""
os.environ["BAYMAX_ENVIRONMENT"] = "development"

from fastapi.testclient import TestClient  # noqa: E402

from server.database import SessionLocal  # noqa: E402
from server.main import app  # noqa: E402
from server.models import Application, Profile, ReviewScan  # noqa: E402
from server.scan import captures as cap_mod, names, worker  # noqa: E402
from server.scan.archive import ScanFileError, check_zip  # noqa: E402
from server.scan.checks import Clock, Job, Rec, _duplicate_checks  # noqa: E402
from server.scan.options import ScanOptions  # noqa: E402

failures: list[str] = []
API = "/api/v1"
R = f"{API}/review"
DAY = "2026-10-23"


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}{(' - ' + str(detail)[:300]) if detail and not condition else ''}")
    if not condition:
        failures.append(name)


# ----- made-up files -----


def pdf(lines: list[str], pages: int = 1) -> bytes:
    """A small valid PDF with text pypdf can read."""
    objs: list[bytes] = []
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(pages))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    font_id = 3 + pages * 2
    for i in range(pages):
        page_lines = lines if i == 0 else [f"Page {i + 1}"]
        body = "BT /F1 11 Tf 72 720 Td 14 TL " + " ".join(
            "(" + ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") '" for ln in page_lines
        ) + " ET"
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {4 + i * 2} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode()
        )
        data = body.encode("latin-1")
        objs.append(b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for n, obj in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


RESUME = [
    "John Q", "Austin, TX | (512) 555-0100 | john.q@example.com",
    "PROFESSIONAL SUMMARY", "Senior backend engineer with Python, AWS and Kafka experience building services.",
    "WORK EXPERIENCE", "Initrode - Senior Software Engineer 2019 - 2026",
    "Built event-driven services, REST APIs and data pipelines for payments and billing systems.",
    "Improved query performance and observability across distributed services in production.",
]


def cover(company: str, title: str, extra: str = "") -> list[str]:
    return [
        "John Q", "john.q@example.com | (512) 555-0100", "Dear Hiring Manager,",
        f"I am excited to apply for the {title} position at {company}.",
        "I bring years of backend engineering experience with Python, AWS and distributed systems.",
        extra or "I would welcome the chance to contribute to the team.", "Best regards, John Q",
    ]


def capture(url: str, title: str, saved: str, body: str) -> str:
    return (
        f"<!DOCTYPE html> <html lang=en><!--\n Page saved with SingleFile \n url: {url} \n"
        f" saved date: {saved} GMT+0530 (India Standard Time)\n--><meta charset=utf-8>"
        f"<title>{title}</title><style>.x{{color:red}}</style><script>var a='<label>';</script>"
        f"<body><h1>{title}</h1><form>{body}</form></body></html>"
    )


def greenhouse_form(auth: str = "Yes", extra: str = "") -> str:
    return (
        "<div><label for=first_name>First Name<span>*</span></label><input id=first_name type=text value=John></div>"
        "<div><label for=email>Email</label><input id=email type=text value=john.q@example.com></div>"
        "<div class=field><label id=q1-label for=q1>Are you legally authorized to work in the US?*</label>"
        f"<div class=select__control><div class=select__single-value>{auth}</div>"
        "<input id=q1 role=combobox class=select__input value=''></div></div>"
        "<div class=field><label for=q2>Will you now or in the future require sponsorship?</label>"
        "<div><div>No</div><input id=q2 role=combobox value=''></div></div>"
        "<div class=field><label for=q3>How did you hear about this job?</label><select id=q3>"
        "<option value=''>Select</option><option value=jb selected>Job Board</option></select></div>"
        "<div>Resume/CV <span>John_Q_resume.pdf</span></div>" + extra
    )


ASHBY_YESNO = (
    "<div><label for=abc>Do you have authorization to work for any employer in the US?</label>"
    "<div><button aria-pressed=true>Yes</button><button aria-pressed=false>No</button>"
    "<input type=checkbox name=abc value=on checked></div></div>"
)
BAMBOO_RADIO = (
    "<fieldset><legend><div><label>Do you now or will you in the future require sponsorship?</label></div></legend>"
    "<div><span><label><span><input type=radio name=s value=Yes></span><span>Yes</span></label></span>"
    "<span><label><span><input type=radio name=s value=No checked></span><span>No</span></label></span></div></fieldset>"
)


def jr(n: int) -> str:
    return f"{n:024x}"


def make_zip(files: dict[str, bytes | str], times: dict[str, tuple] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            info = zipfile.ZipInfo(name, date_time=(times or {}).get(name, (2026, 10, 23, 8, 0, 0)))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data.encode() if isinstance(data, str) else data)
    return buf.getvalue()


def day_files() -> tuple[bytes, bytes]:
    folders = {
        "Acme Health": ("Acme Health", "Senior Backend Engineer", RESUME, 1, ""),
        "Globex": ("Globex", "Staff Engineer", RESUME, 1, ""),
        "Initech": ("Initech", "Platform Engineer", RESUME, 1, ""),
        "Hooli": ("Hooli", "Backend Engineer", RESUME, 1, ""),
        "Robert Half Technology": ("Robert Half Technology", "Python Developer", RESUME, 1, ""),
        "Vandelay": ("Vandelay", "Software Engineer", RESUME, 1, ""),
        "Soylent": ("Soylent", "Data Engineer", RESUME[:3] + ["Kafka expert [need to verify] with 12 years"] + RESUME[3:], 3,
                    "At Globex I would bring the same energy."),
        "Umbrella": ("Umbrella", "Senior Developer", RESUME, 1, ""),
        "Stark": ("Stark", "Lead Engineer", RESUME, 1, ""),
        "Cyberdyne": ("Cyberdyne", "Robotics Engineer", RESUME, 1, ""),  # never tracked
    }
    files: dict[str, bytes | str] = {}
    times: dict[str, tuple] = {}
    for folder, (company, title, resume, pages, extra) in folders.items():
        files[f"{DAY}/{folder}/John_Q_resume.pdf"] = pdf(resume, pages)
        files[f"{DAY}/{folder}/John_Q_cover_letter.pdf"] = pdf(cover(company, title, extra))
    times[f"{DAY}/Globex/John_Q_resume.pdf"] = (2026, 10, 23, 10, 20, 0)  # made after its capture
    resumes = make_zip(files, times)

    gh = "https://job-boards.greenhouse.io/embed/job_app?for={}&token={}&jr_id={}"
    snaps = {
        "Job Application for Senior Backend Engineer at Acme Health.html": capture(
            gh.format("acme", 1, jr(1)), "Job Application for Senior Backend Engineer at Acme Health",
            "Fri Oct 23 2026 09:10:00", greenhouse_form()),
        "No title.html": capture(  # Globex, saved before its resume was made
            gh.format("globex", 2, jr(2)), "", "Fri Oct 23 2026 10:05:00", greenhouse_form()),
        "Initech.html": capture(gh.format("initech", 3, jr(3)), "Initech", "Fri Oct 23 2026 10:30:00", ASHBY_YESNO),
        "Hooli.html": capture(gh.format("hooli", 4, jr(4)), "Hooli", "Fri Oct 23 2026 10:40:00", BAMBOO_RADIO),
        "RH.html": capture(gh.format("rht", 5, jr(5)), "Python Developer", "Fri Oct 23 2026 10:50:00", ""),
        "LinkedIn.html": capture("https://www.linkedin.com/jobs/view/99", "Vandelay", "Fri Oct 23 2026 11:00:00", ""),
        "Apply for Senior Developer.html": capture(  # Umbrella: matched by host + title
            "https://apply.appone.com/application/education", "Umbrella - Senior Developer",
            "Fri Oct 23 2026 11:20:00", ""),
        "Stark form.html": capture(
            gh.format("stark", 9, jr(9)), "Job Application for Lead Engineer at Stark", "Fri Oct 23 2026 11:30:00",
            greenhouse_form(auth="No", extra=(
                "<div><label for=c1>Do you consent to biometric identity verification?</label>"
                "<div><div>Yes</div><input id=c1 role=combobox value=''></div></div>"
                "<p>Employees are expected to work from the office at least 3 days a week in our hybrid model.</p>"))),
        "Stark thanks.html": capture(
            gh.format("stark", 9, jr(9)), "Thank you", "Fri Oct 23 2026 11:31:00",
            "<div><h2>Thank you for applying!</h2><p>Your application has been submitted.</p></div>"),
        "Tyrell.html": capture("https://jobs.lever.co/tyrell/1", "Replicant Engineer at Tyrell",
                               "Fri Oct 23 2026 12:00:00", ""),
        "screenshot.png": b"\x89PNG fake",
    }
    snapshots = make_zip({f"{DAY}/{k}": v for k, v in snaps.items()})
    return resumes, snapshots


# ----- setup -----


def _login(client: TestClient, email: str, password: str) -> dict:
    r = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _user(client: TestClient, admin_h: dict, name: str, role: str) -> tuple[str, dict]:
    email = f"{name.lower()}-{uuid.uuid4().hex[:6]}@baymax.example.com"
    r = client.post(f"{API}/admin/api/users", headers=admin_h,
                    json={"email": email, "name": name, "password": "userpass12", "role": role})
    assert r.status_code == 201, r.text
    return r.json()["id"], _login(client, email, "userpass12")


def _app(profile_id: str, creator: str, company: str, title: str, link: str, *, day: str = DAY,
         at: str = "04:00", status: str = "Applied", notes: str = "", folder: str | None = None) -> str:
    app_id = str(uuid.uuid4())
    d = datetime.fromisoformat(day)
    created = datetime.fromisoformat(f"{day}T{at}:00")
    with SessionLocal() as db:
        db.add(Application(
            id=app_id, user_id=creator, creator_user_id=creator, profile_id=profile_id,
            company_name=company, position_title=title, job_link=link,
            applied_date=d.replace(hour=12), client_created_at=created, last_modified=created,
            status=status, notes=notes,
            resume_file_path=f"C:/CareerCraft/resumes/{day}/{folder or company}/John_Q_resume.pdf",
        ))
        db.commit()
    return app_id


def upload(client, headers, supporter_id, resumes, snapshots, work_date=DAY, names_=("2026-10-23_resumes_Sam.zip", "2026-10-23_screenshots_Sam.zip")):
    return client.post(
        f"{R}/scans", headers=headers,
        data={"supporter_id": supporter_id, "work_date": work_date},
        files={"resumes": (names_[0], resumes, "application/zip"),
               "snapshots": (names_[1], snapshots, "application/zip")},
    )


def wait_ready(client, headers, scan_id, timeout=60) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        data = client.get(f"{R}/scans/{scan_id}", headers=headers).json()
        if data["status"] not in ("queued", "scanning"):
            return data
        time.sleep(0.3)
    return data


def unit_tests() -> None:
    check("jobright id from tag", names.jobright_id(f"https://x.io/a?jr_id={jr(7)}") == jr(7))
    check("jobright id from link", names.jobright_id(f"https://jobright.ai/jobs/info/{jr(8)}?utm=1") == jr(8))
    lm = names.link_match
    check("greenhouse token tells jobs apart", not lm("https://job-boards.greenhouse.io/embed/job_app?for=a&token=1",
                                                      "https://job-boards.greenhouse.io/embed/job_app?for=a&token=2"))
    check("jobright page = tagged company link", lm(f"https://jobright.ai/jobs/info/{jr(1)}",
                                                    f"https://boards.greenhouse.io/a/jobs/4012345?jr_id={jr(1)}") == "jr")
    check("greenhouse board = company embed", lm("https://acme.com/careers?gh_jid=4012345",
                                                 "https://job-boards.greenhouse.io/acme/jobs/4012345?utm_source=x") == "gh")
    lever = "https://jobs.lever.co/acme/0b1c2d3e-aaaa-bbbb-cccc-1234567890ab"
    check("lever apply page = posting", lm(lever + "/apply", lever + "?lever-source=x") == "lever")
    wd = "https://acme.wd5.myworkdayjobs.com/{}External/job/{}/Data-Engineer_{}"
    check("workday location and locale ignored", lm(wd.format("en-US/", "Remote", "R12345"),
                                                    wd.format("", "New-York", "R12345") + "/apply") == "wd")
    check("workday different req", not lm(wd.format("", "Remote", "R12345"), wd.format("", "Remote", "R12346")))
    ukg = "https://recruiting.ultipro.com/ACM1000/JobBoard/9f8e/OpportunityDetail?opportunityId={}"
    check("ukg jobs told apart", not lm(ukg.format(1111), ukg.format(2222)))
    check("ukg same job", lm(ukg.format(1111) + "&utm_source=x", ukg.format(1111)) == "page")
    taleo = "https://acme.taleo.net/careersection/2/jobdetail.ftl?job={}"
    check("taleo jobs told apart", not lm(taleo.format(111), taleo.format(222)))
    check("different jobright ids differ", not lm(f"https://acme.com/careers?jr_id={jr(1)}", f"https://acme.com/careers?jr_id={jr(2)}"))
    check("general page is a weak match", lm("https://acme.com/careers", "https://www.acme.com/careers/") == "page_weak")
    check("unknown job part kept on a general page", not lm("https://acme.com/careers?position=data",
                                                            "https://acme.com/careers?position=ml"))
    check("flow page", names.is_flow_page("https://apply.appone.com/application/resume"))
    check("not flow page", not names.is_flow_page("https://jobs.lever.co/acme/123/apply"))
    t = names.company_tokens
    check("generic name doesn't match part", not names.same_company(t("Health"), t("Duo Health")))
    check("distinct name matches part", names.same_company(t("Acme"), t("Acme Health LLC")))
    check("placeholder record", names.is_placeholder_record("Company Name", "Job Title"))

    def rec(i, company, link, day, *, profile="p1", status="Applied", at=4):
        d = datetime.fromisoformat(day)
        return Rec(id=str(i), profile_id=profile, profile_name="", creator_id="u", creator_name="Sam", company=company,
                   title="Engineer", link=link, status=status, notes="", applied_day=d.date(), created_utc=d.replace(hour=at))
    work = datetime.fromisoformat(DAY).date()
    me = rec(0, "Acme Health", "https://acme.com/jobs/12345", DAY, at=6)
    pool = [
        rec(1, "Acme Health", "https://acme.com/jobs/12345?utm_source=x", DAY, at=5),  # earlier today
        rec(2, "Acme Health", "https://acme.com/jobs/12345", DAY, at=7),  # later today: that one gets it
        rec(3, "Acme Health", "https://acme.com/jobs/12345", "2026-01-05"),  # long ago, same link
        rec(4, "Acme Health Inc.", "https://acme.com/jobs/999", "2026-10-01"),  # company, in window
        rec(5, "ACME", "https://acme.com/jobs/888", "2026-09-01"),  # company, outside window
        rec(6, "Acme Health", "https://acme.com/jobs/12345", "2026-10-02", profile="p2"),  # another candidate
        rec(7, "Acme Health", "https://acme.com/jobs/12345", "2026-10-03", status="Withdrawn"),
    ]
    job = Job(records=[me])
    _duplicate_checks(job, pool, ScanOptions(), work, Clock(timedelta(hours=5, minutes=30), "UTC+05:30"))
    found = {f["code"]: f for f in job.findings}
    link_ids = [(m["date"], m["time"], m["how"]) for m in found.get("same_link", {}).get("matches", [])]
    check("link matches listed newest first", link_ids == [(DAY, "10:30", "same page address"), ("2026-01-05", "", "same link")], link_ids)
    check("duplicates are warnings", {f["level"] for f in job.findings} == {"check"}, job.findings)
    company = [(m["company"], m["how"]) for m in found.get("same_company", {}).get("matches", [])]
    check("company match in window only", company == [("Acme Health Inc.", "same name")], company)
    job = Job(records=[rec(8, "Robert Half", "https://rh.com/jobs/1", DAY)])
    _duplicate_checks(job, [rec(9, "Robert Half", "https://rh.com/jobs/2", "2026-10-20")],
                      ScanOptions(staffing_agencies=["Robert Half"]), work, Clock(timedelta(0), "UTC"))
    check("agency not matched by company", not job.findings, job.findings)

    page = capture("https://x", "T", "Fri Oct 23 2026 09:10:00", greenhouse_form() + ASHBY_YESNO + BAMBOO_RADIO)
    p = _tmp / "unit.html"
    p.write_text(page)
    cap = cap_mod.read_capture(_tmp, p)
    answers = dict(cap.answers)
    check("text input answer", answers.get("First Name") == "John", cap.answers)
    check("custom dropdown answer", answers.get("Are you legally authorized to work in the US?") == "Yes", cap.answers)
    check("select answer", answers.get("How did you hear about this job?") == "Job Board", cap.answers)
    check("pressed button answer", answers.get("Do you have authorization to work for any employer in the US?") == "Yes", cap.answers)
    check("radio group answer", answers.get("Do you now or will you in the future require sponsorship?") == "No", cap.answers)
    check("saved time and offset", cap.saved_local == datetime(2026, 10, 23, 9, 10)
          and cap.saved_utc == datetime(2026, 10, 23, 3, 40), (cap.saved_local, cap.saved_utc))
    check("attached file", cap.attached == ["John_Q_resume.pdf"], cap.attached)
    check("script text ignored", "<label>" not in " ".join(q for q, _ in cap.answers))
    check("a form isn't a confirmation", not cap.confirmation)

    bad = _tmp / "bad.zip"
    for name, data in (("escape", make_zip({"../evil.pdf": pdf(["x"])})),
                       ("absolute", make_zip({"/etc/evil.pdf": pdf(["x"])})),
                       ("no documents", make_zip({"notes.txt": "hi"}))):
        bad.write_bytes(data)
        try:
            check_zip(bad, "resumes")
            check(f"zip refused: {name}", False)
        except ScanFileError:
            check(f"zip refused: {name}", True)


def main() -> int:
    unit_tests()
    with TestClient(app) as client:
        admin_h = _login(client, "admin@baymax.example.com", "adminpw")
        _, manager_h = _user(client, admin_h, "Manny", "manager")
        alice, alice_h = _user(client, admin_h, "Alice", "member")
        bob, bob_h = _user(client, admin_h, "Bob", "member")
        sam, sam_h = _user(client, admin_h, "Sam", "supporter")
        tia, _ = _user(client, admin_h, "Tia", "supporter")
        for s, m in ((sam, alice), (tia, bob)):
            client.post(f"{API}/admin/api/users/{s}/set-member", headers=admin_h, json={"member_id": m})
        key = client.post(f"{API}/team/member/profiles", headers=alice_h,
                          json={"name": "John Q", "email": "john.q@example.com", "phone": "(512) 555-0100"}).json()["key"]
        client.put(f"{API}/team/member/profiles/{key}/supporters", headers=alice_h, json={"supporter_ids": [sam]})
        with SessionLocal() as db:
            pid = db.query(Profile).filter_by(key=key).one().id

        gh = "https://job-boards.greenhouse.io/embed/job_app?for={}&token={}&jr_id={}"
        # The day: times are UTC; the supporter's PC is UTC+05:30.
        _app(pid, sam, "Acme Health", "Senior Backend Engineer", gh.format("acme", 1, jr(1)), at="03:35")
        _app(pid, sam, "Globex", "Staff Engineer", gh.format("globex", 2, jr(2)), at="04:30")
        _app(pid, sam, "Globex", "Staff Engineer", gh.format("globex", 2, jr(2)), at="04:32")
        _app(pid, sam, "Initech", "Platform Engineer", gh.format("initech", 3, jr(3)), at="04:55")
        _app(pid, sam, "Hooli", "Backend Engineer", gh.format("hooli", 4, jr(4)), at="05:05")
        _app(pid, sam, "Robert Half Technology", "Python Developer", gh.format("rht", 5, jr(5)), at="05:15")
        _app(pid, sam, "Vandelay", "Software Engineer", "https://www.linkedin.com/jobs/view/99", at="05:25")
        _app(pid, sam, "Soylent", "Data Engineer", gh.format("soylent", 7, jr(7)), at="05:35")
        _app(pid, sam, "Umbrella", "Senior Developer", "https://apply.appone.com/application/resume", at="05:45")
        _app(pid, sam, "Wonka", "Chocolate Engineer", gh.format("wonka", 8, jr(8)), at="05:50", status="Withdrawn")
        _app(pid, sam, "Stark", "Lead Engineer", gh.format("stark", 9, jr(9)), at="05:52")
        _app(pid, alice, "Massive Dynamic", "Engineer", "https://linkedin.com/jobs/view/5", at="06:00")  # member's own
        # History on the same profile.
        _app(pid, sam, "Initech", "Platform Engineer", gh.format("initech", 3, jr(3)), day="2026-10-10")
        _app(pid, alice, "Hooli Inc.", "Frontend Engineer", gh.format("hooli", 40, jr(40)), day="2026-10-01")
        _app(pid, sam, "Robert Half", "Java Developer", gh.format("rh", 50, jr(50)), day="2026-10-15")
        _app(pid, sam, "Acme Health", "Data Engineer", gh.format("acme", 60, jr(60)), day="2026-09-01")  # outside window
        # Long ago, the same Soylent job without the Jobright tag.
        _app(pid, alice, "Soylent Corp", "Data Engineer", "https://job-boards.greenhouse.io/embed/job_app?for=soylent&token=7",
             day="2026-06-01")

        resumes, snapshots = day_files()

        # --- access ---
        check("needs auth", client.get(f"{R}/supporters").status_code == 401)
        check("supporter shut out", client.get(f"{R}/supporters", headers=sam_h).status_code == 403)
        names_alice = [s["name"] for s in client.get(f"{R}/supporters", headers=alice_h).json()]
        check("member sees own team", names_alice == ["Sam"], names_alice)
        names_mgr = {s["name"] for s in client.get(f"{R}/supporters", headers=manager_h).json()}
        check("manager sees all supporters", {"Sam", "Tia"} <= names_mgr, names_mgr)
        r = client.get(f"{R}/day", headers=bob_h, params={"supporter_id": sam, "work_date": DAY})
        check("other member can't preview", r.status_code == 404, r.text)
        r = client.get(f"{R}/day", headers=alice_h, params={"supporter_id": sam, "work_date": DAY})
        check("day preview counts", r.json() == {"records": 11, "finished_scans": 0}, r.text)
        check("other member can't upload", upload(client, bob_h, sam, resumes, snapshots).status_code == 404)

        # --- upload checks ---
        r = upload(client, alice_h, sam, b"not a zip", snapshots)
        check("not a zip refused", r.status_code == 400 and "isn't a ZIP" in r.text, r.text)
        r = upload(client, alice_h, sam, snapshots, resumes)
        check("swapped ZIPs refused", r.status_code == 400, r.text)
        r = upload(client, alice_h, sam, resumes, snapshots, names_=("2026-10-22_resumes_Sam.zip", "2026-10-23_screenshots_Sam.zip"))
        check("upload ok", r.status_code == 201 and r.json()["status"] == "uploaded", r.text)
        scan = r.json()
        check("date warning", any("2026-10-22" in w for w in scan["uploads"]["warnings"]), scan["uploads"])
        check("files saved", (worker.scan_dir(scan["id"]) / "resumes.zip").exists())
        r = upload(client, alice_h, sam, resumes, snapshots)
        check("one open scan per reviewer", r.status_code == 409, r.text)
        r = client.get(f"{R}/current", headers=alice_h)
        check("current scan", r.json()["scan"]["id"] == scan["id"])
        check("manager has no open scan", client.get(f"{R}/current", headers=manager_h).json()["scan"] is None)
        r = client.post(f"{R}/scans/{scan['id']}/start", headers=manager_h, json={})
        check("only the reviewer starts", r.status_code == 403, r.text)

        # --- scan ---
        opts = client.get(f"{R}/defaults", headers=alice_h).json()
        check("default window", opts["duplicate_window_days"] == 30)
        opts["staffing_agencies"] = ["Robert Half"]
        r = client.post(f"{R}/scans/{scan['id']}/start", headers=alice_h, json=opts)
        check("start", r.status_code == 200 and r.json()["status"] in ("queued", "scanning", "ready"), r.text)
        data = wait_ready(client, alice_h, scan["id"])
        check("scan ready", data["status"] == "ready", (data["status"], data.get("error")))
        jobs: dict = {}
        for j in data.get("jobs", []):
            jobs.setdefault(j["company"], j)
        texts = {c: " | ".join(f["level"] + ":" + f["text"] for f in j["findings"]) for c, j in jobs.items()}
        codes_of = {c: {f["code"]: f for f in j["findings"]} for c, j in jobs.items()}
        s = data["summary"]
        check("summary counts", (s["records"], s["jobs"], s["same_link"], s["same_company"], s["resume_folders"])
              == (11, 11, 3, 1, 10), s)
        check("member's own record left out", "Massive Dynamic" not in jobs)
        check("clean job ok", jobs["Acme Health"]["status"] == "ok", texts.get("Acme Health"))
        globex = [j for j in data["jobs"] if j["company"] == "Globex"]
        check("records aren't merged", len(globex) == 2 and "same_link" not in codes_of["Globex"], codes_of["Globex"])
        second = {f["code"]: f for f in globex[1]["findings"]}.get("same_link", {})
        check("same link today is a warning with values", second.get("level") == "check"
              and [(m["time"], m["how"], m["who"]) for m in second.get("matches", [])] == [("10:00", "same link", "Sam")], second)
        check("both records show the shared folder and capture", all(
            g["details"]["folder"] and len(g["details"]["captures"]) == 1 for g in globex), [g["details"] for g in globex])
        check("capture before resume", "before this resume was made" in texts["Globex"], texts["Globex"])
        initech = codes_of["Initech"].get("same_link", {})
        check("same link earlier day", initech.get("level") == "check"
              and initech["matches"][0]["date"] == "2026-10-10" and jobs["Initech"]["status"] != "fail", texts["Initech"])
        soylent = codes_of["Soylent"].get("same_link", {})
        check("old record found by a similar link", [(m["date"], m["how"], m["who"]) for m in soylent.get("matches", [])]
              == [("2026-06-01", "same page address", "Alice")], soylent)
        hooli = codes_of["Hooli"].get("same_company", {})
        check("same company in window", hooli.get("level") == "check"
              and [(m["company"], m["date"], m["title"]) for m in hooli["matches"]]
              == [("Hooli Inc.", "2026-10-01", "Frontend Engineer")], hooli)
        check("agency skipped", "same_company" not in codes_of["Robert Half Technology"], texts["Robert Half Technology"])
        check("outside window ignored", "same_company" not in codes_of["Acme Health"])
        check("excluded site", "linkedin.com, an excluded site" in texts["Vandelay"], texts["Vandelay"])
        check("leftover marker", "fail:Resume has leftover text" in texts["Soylent"] and "need to verify" in texts["Soylent"], texts["Soylent"])
        check("page limit", "Resume is 3 pages" in texts["Soylent"], texts["Soylent"])
        check("other company in cover", "names Globex, another" in texts["Soylent"], texts["Soylent"])
        check("no capture", "No form capture" in texts["Soylent"], texts["Soylent"])
        check("flow link", "page inside the application" in texts["Umbrella"], texts["Umbrella"])
        check("matched by host and title", "No form capture" not in texts["Umbrella"], texts["Umbrella"])
        check("withdrawn without reason", "Withdrawn without a reason" in texts["Wonka"], texts["Wonka"])
        check("withdrawn needs no capture", "No form capture" not in texts["Wonka"], texts["Wonka"])
        check("confirmation found", "Confirmation page saved" in texts["Stark"], texts["Stark"])
        check("consent flagged", "biometric" in texts["Stark"], texts["Stark"])
        check("onsite wording", "work from the office" in texts["Stark"], texts["Stark"])
        day = " | ".join(f["text"] for f in data["day_findings"])
        check("folder without record", "Cyberdyne" in day, day)
        check("capture without record", "Tyrell" in day, day)
        check("other evidence", "1 other evidence" in day, day)
        check("answer consistency", "work authorization" in day and "Stark" in day, day)
        check("local clock", "UTC+05:30" in data["summary"]["clock"], data["summary"])
        stark = jobs["Stark"]["details"]
        check("job details keep captures", len(stark["captures"]) == 2 and stark["captures"][0]["answers"], stark)
        report = data["report_text"]
        check("report text", report.startswith("DAILY REVIEW: Sam · 2026-10-23") and "OPTIONS USED" in report
              and "Staffing agencies: Robert Half" in report, report[:300])
        check("report shows matching values", "This job:  Hooli · Backend Engineer" in report
              and "2026-10-01       Hooli Inc. · Frontend Engineer · by Alice" in report, report)
        check("defaults remembered", client.get(f"{R}/defaults", headers=manager_h).json()["staffing_agencies"] == ["Robert Half"])

        # --- who sees it ---
        check("other member can't see", client.get(f"{R}/scans/{scan['id']}", headers=bob_h).status_code == 404)
        check("manager can see", client.get(f"{R}/scans/{scan['id']}", headers=manager_h).status_code == 200)
        check("manager can't finish", client.post(f"{R}/scans/{scan['id']}/finish", headers=manager_h).status_code == 403)

        # --- viewing files through signed links ---
        acme = jobs["Acme Health"]
        resume_rel = next(f["rel"] for f in acme["details"]["files"] if f["kind"] == "resume")
        r = client.get(f"{R}/scans/{scan['id']}/file-link", headers=alice_h, params={"area": "resumes", "path": resume_rel})
        check("file link", r.status_code == 200 and r.json()["kind"] == "pdf", r.text)
        pdf_url = r.json()["url"]
        r = client.get(pdf_url)  # no login header: the signed link is enough
        check("pdf served", r.status_code == 200 and r.headers["content-type"] == "application/pdf"
              and r.content.startswith(b"%PDF"), r.headers)
        check("pdf headers", r.headers.get("x-content-type-options") == "nosniff"
              and "inline" in r.headers.get("content-disposition", "")
              and "no-store" in r.headers.get("cache-control", ""), r.headers)
        cap_rel = jobs["Stark"]["details"]["captures"][0]["rel"]
        r = client.get(f"{R}/scans/{scan['id']}/file-link", headers=alice_h, params={"area": "snapshots", "path": cap_rel})
        html_url = r.json()["url"]
        r = client.get(html_url)
        csp = r.headers.get("content-security-policy", "")
        check("capture served sandboxed", r.status_code == 200 and r.headers["content-type"].startswith("text/html")
              and csp.startswith("sandbox") and "default-src 'none'" in csp, r.headers)
        token = html_url.split("/files/")[1].split("/")[0]
        bad = token[:-1] + ("0" if token[-1] != "0" else "1")
        check("tampered link refused", client.get(html_url.replace(token, bad)).status_code == 403)
        for label, params in (("escape", {"area": "resumes", "path": "../resumes.zip"}),
                              ("zip itself", {"area": "snapshots", "path": "../snapshots.zip"}),
                              ("bad area", {"area": "..", "path": "resumes.zip"}),
                              ("missing", {"area": "resumes", "path": "nope.pdf"})):
            r = client.get(f"{R}/scans/{scan['id']}/file-link", headers=alice_h, params=params)
            check(f"file link refused: {label}", r.status_code == 404, r.text)
        r = client.get(f"{R}/scans/{scan['id']}/file-link", headers=bob_h, params={"area": "resumes", "path": resume_rel})
        check("other member gets no link", r.status_code == 404)
        r = client.get(f"{R}/scans/{scan['id']}/file-link", headers=manager_h, params={"area": "resumes", "path": resume_rel})
        check("manager can view files", r.status_code == 200)
        extra = {f["rel"] for d in data["day_findings"] for f in d.get("files", [])}
        check("day findings carry files", any("Cyberdyne" in x for x in extra) and any(x.endswith("screenshot.png") for x in extra), extra)
        shot = next(x for x in extra if x.endswith("screenshot.png"))
        r = client.get(f"{R}/scans/{scan['id']}/file-link", headers=alice_h, params={"area": "snapshots", "path": shot})
        check("screenshot link", r.json()["kind"] == "image", r.text)

        # --- verdicts and notes ---
        job_url = f"{R}/scans/{scan['id']}/jobs"
        r = client.put(f"{job_url}/{acme['id']}", headers=alice_h, json={"verdict": "pass", "note": " Looks right "})
        check("save verdict", r.status_code == 200 and r.json()["verdict"] == "pass" and r.json()["note"] == "Looks right", r.text)
        r = client.put(f"{job_url}/{jobs['Soylent']['id']}", headers=alice_h, json={"verdict": "fail", "note": "Fake Kafka claim"})
        check("save fail verdict", r.status_code == 200)
        check("bad verdict refused", client.put(f"{job_url}/{acme['id']}", headers=alice_h, json={"verdict": "great"}).status_code == 422)
        check("manager can't judge", client.put(f"{job_url}/{acme['id']}", headers=manager_h, json={"verdict": "fail"}).status_code == 403)
        check("unknown job", client.put(f"{job_url}/999999", headers=alice_h, json={"verdict": "pass"}).status_code == 404)
        r = client.put(f"{R}/scans/{scan['id']}/note", headers=alice_h, json={"note": "Run the duplicate check before each job."})
        check("save day note", r.status_code == 200, r.text)
        again = client.get(f"{R}/scans/{scan['id']}", headers=alice_h).json()
        saved = {j["company"]: j for j in again["jobs"]}
        check("verdict kept", saved["Acme Health"]["verdict"] == "pass" and saved["Acme Health"]["reviewed_at"], saved["Acme Health"])
        check("note kept", again["reviewer_note"] == "Run the duplicate check before each job.")

        # --- finish ---
        r = client.post(f"{R}/scans/{scan['id']}/finish", headers=alice_h)
        check("finish", r.status_code == 200 and r.json()["status"] == "finished", r.text)
        check("files deleted on finish", not worker.scan_dir(scan["id"]).exists())
        final = r.json()["report_text"]
        check("final report", "Finished:" in final)
        check("report has verdicts", "Reviewer: Pass. Looks right" in final and "Reviewer: Fail. Fake Kafka claim" in final
              and "Reviewer: not reviewed" in final and "2 of 11 jobs reviewed" in final, final[:1500])
        check("report has day note", "REVIEWER'S NOTE" in final and "Run the duplicate check" in final)
        check("files gone after finish", client.get(pdf_url).status_code == 404)
        r = client.get(f"{R}/scans/{scan['id']}/file-link", headers=alice_h, params={"area": "resumes", "path": resume_rel})
        check("no new links after finish", r.status_code == 409, r.text)
        check("no verdicts after finish", client.put(f"{job_url}/{acme['id']}", headers=alice_h, json={"verdict": "fail"}).status_code == 409)
        check("no open scan after finish", client.get(f"{R}/current", headers=alice_h).json()["scan"] is None)
        hist = client.get(f"{R}/scans", headers=alice_h).json()
        check("history", hist["total"] == 1 and hist["items"][0]["id"] == scan["id"], hist)
        check("history for manager", client.get(f"{R}/scans", headers=manager_h).json()["total"] == 1)
        check("history hidden from other member", client.get(f"{R}/scans", headers=bob_h).json()["total"] == 0)
        r = client.get(f"{R}/scans/{scan['id']}/report.txt", headers=manager_h)
        check("report download", r.status_code == 200 and "2026-10-23_review_Sam.txt" in r.headers.get("content-disposition", ""), r.headers)
        r = client.get(f"{R}/day", headers=alice_h, params={"supporter_id": sam, "work_date": DAY})
        check("day shows finished scan", r.json()["finished_scans"] == 1)

        # --- cancel ---
        r = upload(client, alice_h, sam, resumes, snapshots)
        second = r.json()["id"]
        r = client.post(f"{R}/scans/{second}/cancel", headers=alice_h)
        check("cancel", r.status_code == 200 and r.json()["status"] == "cancelled", r.text)
        check("files deleted on cancel", not worker.scan_dir(second).exists())
        check("cancelled not in history", client.get(f"{R}/scans", headers=alice_h).json()["total"] == 1)

        # --- abandoned scans expire ---
        r = upload(client, alice_h, sam, resumes, snapshots)
        third = r.json()["id"]
        with SessionLocal() as db:
            db.get(ReviewScan, third).updated_at = datetime.utcnow() - timedelta(hours=49)
            db.commit()
        stranger = worker.scan_dir(str(uuid.uuid4()))  # another server's upload in a shared folder
        stranger.mkdir(parents=True)
        (stranger / "resumes.zip").write_bytes(b"x")
        os.utime(stranger, (time.time() - 7200, time.time() - 7200))
        worker.sweep(force=True)
        with SessionLocal() as db:
            check("idle scan expires", db.get(ReviewScan, third).status == "expired")
        check("expired files deleted", not worker.scan_dir(third).exists())
        check("unknown folders are left alone", (stranger / "resumes.zip").exists())

        # --- summary report ---
        month = {"date_from": "2026-10-01", "date_to": "2026-10-31"}
        r = client.get(f"{R}/summary", headers=alice_h, params=month)
        check("summary", r.status_code == 200, r.text)
        sm = r.json()
        team = sm["team"]
        check("summary totals", (team["days"], team["jobs"], team["records"], team["supporters"]) == (1, 11, 11, 1), team)
        check("summary verdicts", team["verdicts"] == {"pass": 1, "issue": 0, "fail": 1, "none": 9}, team["verdicts"])
        check("summary statuses", team["status"] == data["summary"]["status"], (team["status"], data["summary"]["status"]))
        codes = {p["code"] for p in team["top_problems"]}
        check("summary top problems", "same_link" in codes, team["top_problems"])
        sup = sm["supporters"][0]
        check("summary per supporter", sup["name"] == "Sam" and len(sup["by_day"]) == 1 and sup["notes"][0]["note"].startswith("Run the duplicate"), sup)
        text = sm["text"]
        check("summary text", text.startswith("REVIEW SUMMARY · 2026-10-01 to 2026-10-31") and "\nSAM\n" in text
              and "Reviewer notes:" in text and "1 pass / 0 issue / 1 fail" in text, text[:800])
        check("manager summary", client.get(f"{R}/summary", headers=manager_h, params=month).json()["team"]["days"] == 1)
        r = client.get(f"{R}/summary", headers=bob_h, params=month)
        check("other member's summary is empty", r.json()["team"]["days"] == 0 and "No finished reviews" in r.json()["text"], r.text)
        r = client.get(f"{R}/summary", headers=alice_h, params={"date_from": "2026-09-01", "date_to": "2026-09-30"})
        check("summary outside range", r.json()["team"]["days"] == 0)
        r = client.get(f"{R}/summary", headers=alice_h, params={"date_from": "2026-10-31", "date_to": "2026-10-01"})
        check("summary bad range", r.status_code == 400)
        # Reviewing the same day again: the latest review counts once.
        again = upload(client, alice_h, sam, resumes, snapshots).json()
        client.post(f"{R}/scans/{again['id']}/start", headers=alice_h, json=opts)
        check("second scan ready", wait_ready(client, alice_h, again["id"])["status"] == "ready")
        client.post(f"{R}/scans/{again['id']}/finish", headers=alice_h)
        sm = client.get(f"{R}/summary", headers=alice_h, params=month).json()
        check("re-reviewed day counted once", sm["team"]["days"] == 1 and sm["repeated_days"] == 1
              and sm["team"]["verdicts"]["none"] == 11 and "reviewed more than once" in sm["text"], sm["team"])

    print(f"\n{len(failures)} failure(s)" if failures else "\nAll review scan checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
