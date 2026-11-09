"""Matching a day's files to the tracker records, and the automatic checks.

Everything here works on plain data (no database), so it is easy to test.
Finding levels:

* ``fail``  - breaks a hard rule (excluded site, leftover text)
* ``issue`` - a supporter mistake to correct (no capture, wrong phone...)
* ``check`` - something the reviewer should look at and decide (a matching
  job link or company name, onsite wording, consent)
* ``info``  - a neutral fact (confirmation saved, made quickly)

Every tracker record is its own job here. Records are never merged by link,
because a link match is only a hint; matches are shown to the reviewer.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import names
from .captures import Capture
from .documents import (
    JobFolder,
    contacts,
    find_markers,
    mentions_company,
    names_other_company,
    title_found,
)
from .options import ScanOptions

LEVELS = ("fail", "issue", "check", "info")

# Short names for finding codes, used when a summary counts problems.
FINDING_LABELS = {
    "placeholder_record": "Placeholder tracker record",
    "withdrawn_no_reason": "Withdrawn without a reason",
    "excluded_site": "Job on an excluded site",
    "no_link": "No job link",
    "flow_link": "Link is a page inside the application",
    "not_jobright_link": "Not a Jobright link",
    "jobright_apply": "Applied through Jobright's Apply button",
    "same_link": "Job link matches another record",
    "same_company": "Company matches within the window",
    "no_resume_folder": "No resume in the ZIP",
    "no_resume_file": "Folder without a resume",
    "unreadable_file": "Resume or cover letter can't be read",
    "no_text": "Resume or cover letter without text",
    "leftover_text": "Leftover ChatGPT text",
    "too_many_pages": "Too many pages",
    "wrong_email": "Wrong email in resume",
    "wrong_phone": "Wrong phone in resume",
    "cover_missing_company": "Cover letter doesn't name the company",
    "cover_other_company": "Cover letter names another company",
    "cover_missing_title": "Cover letter misses the job title",
    "no_capture": "No form capture",
    "withdrawn_but_captured": "Withdrawn but captured",
    "unreadable_capture": "Capture can't be read",
    "capture_before_resume": "Form saved before its resume",
    "form_email": "Wrong email in the form",
    "referral_answer": "Answered Referral",
    "referred_yes": "Said they were referred",
    "consent_answer": "Consent or background-check answer",
    "onsite_wording": "Onsite or hybrid wording",
    "different_answers": "Different answers across jobs",
    "untracked_folder": "Resume folder not tracked",
    "unmatched_capture": "Capture without a tracker record",
}
_RANK = {lvl: i for i, lvl in enumerate(LEVELS)}
_CONSENT = re.compile(
    r"biometric|background check|e-?signature|signature|attest|certify|drug (?:test|screen)|"
    r"transcri|record(?:ing)? (?:of )?(?:your|the) interview|electronic(?:ally)? sign",
    re.I,
)
_HEARD = re.compile(r"how did you (?:hear|find|learn)|source of (?:application|referral)", re.I)
_REFERRED = re.compile(r"\breferred\b|referral", re.I)
# Yes/No questions whose answer shouldn't change from one job to the next.
# "Authorized ... without sponsorship?" is an authorization question (Yes),
# so sponsorship only counts when the question asks if it's needed.
_CATEGORIES = {
    "work authorization": re.compile(r"(authori[sz]ed|eligible|legally).{0,40}work|authori[sz]ation to work", re.I),
    "needing visa sponsorship": re.compile(r"^(?!.*without).*(require|need).{0,60}sponsor", re.I),
    "being referred by an employee": re.compile(r"were you referred|referred by", re.I),
}


@dataclass
class Rec:
    """A tracker record, as loaded by the worker."""

    id: str
    profile_id: str | None
    profile_name: str
    creator_id: str | None
    creator_name: str
    company: str
    title: str
    link: str
    status: str
    notes: str
    applied_day: date | None
    created_utc: datetime | None
    resume_path: str = ""


@dataclass
class Job:
    records: list[Rec]
    folder: JobFolder | None = None
    captures: list[Capture] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)

    @property
    def main(self) -> Rec:
        real = [r for r in self.records if not names.is_placeholder_record(r.company, r.title)]
        return (real or self.records)[0]

    @property
    def withdrawn(self) -> bool:
        return all(names.is_withdrawn(r.status) for r in self.records)

    @property
    def status(self) -> str:
        levels = [f["level"] for f in self.findings if f["level"] != "info"]
        return min(levels, key=_RANK.get) if levels else "ok"

    def add(self, code: str, level: str, area: str, text: str, **extra) -> None:
        self.findings.append({"code": code, "level": level, "area": area, "text": text, **extra})


def _norm_folder(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _yes_no(answer: str) -> str:
    """"Yes", "No", or "" when the answer isn't a plain yes or no."""
    a = answer.strip().lower()
    if re.match(r"yes\b", a):
        return "Yes"
    if re.match(r"no\b", a):
        return "No"
    return ""


def _short(question: str, limit: int = 90) -> str:
    """A question cut to its first sentence, or at a word, for findings."""
    q = question.split("?")[0] + "?" if "?" in question else question
    if len(q) <= limit:
        return q
    return q[:limit].rsplit(" ", 1)[0] + "…"


class Clock:
    """Shows UTC tracker times on the supporter's PC clock."""

    def __init__(self, offset: timedelta, label: str):
        self.offset, self.label = offset, label

    def local(self, utc: datetime | None) -> datetime | None:
        return utc + self.offset if utc else None

    def hm(self, utc: datetime | None) -> str:
        t = self.local(utc)
        return t.strftime("%H:%M") if t else "--:--"


def pick_clock(captures: list[Capture], fallback_offset: timedelta, fallback_label: str) -> Clock:
    offsets = Counter(
        c.saved_local - c.saved_utc for c in captures if c.saved_local and c.saved_utc
    )
    if offsets:
        off = offsets.most_common(1)[0][0]
        minutes = int(off.total_seconds() // 60)
        return Clock(off, f"UTC{'+' if minutes >= 0 else '-'}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}")
    return Clock(fallback_offset, fallback_label)


def group_jobs(records: list[Rec]) -> list[Job]:
    """One job per tracker record, in the order they were made."""
    return [Job(records=[r]) for r in sorted(records, key=lambda r: (r.created_utc or datetime.max, r.id))]


def match_folders(jobs: list[Job], folders: list[JobFolder]) -> list[JobFolder]:
    """Pair each job with its resume folder; returns folders left over."""
    left = list(folders)
    by_name = {_norm_folder(f.name): f for f in folders}
    for job in jobs:
        for rec in job.records:
            parts = [p for p in rec.resume_path.replace("\\", "/").split("/") if p]
            if len(parts) >= 2:
                f = by_name.get(_norm_folder(parts[-2]))
                if f is not None:
                    # Two records can point at one folder (Generate clicked
                    # twice); both show it.
                    job.folder = f
                    if f in left:
                        left.remove(f)
                    break
    for job in jobs:
        if job.folder is not None:
            continue
        tokens = names.company_tokens(job.main.company)
        match = [f for f in left if names.company_tokens(f.name) == tokens or
                 _norm_folder(f.name) == _norm_folder(job.main.company)]
        if len(match) == 1:
            job.folder = match[0]
            left.remove(match[0])
    return left


_STRENGTH = {"same": 3, "jr": 3, "gh": 3, "lever": 3, "ashby": 3, "wd": 3, "page": 2, "page_weak": 1}


def match_captures(jobs: list[Job], captures: list[Capture]) -> list[Capture]:
    """Pair captures with jobs by the page address saved inside each capture;
    file names aren't reliable. Returns captures left over.

    A capture whose link matches several records (the same job tracked
    twice) is shown with each of them."""
    left: list[Capture] = []
    for cap in captures:
        hp, host = names.host_path(cap.url), names.link_host(cap.url)
        found: Job | None = None
        scored = [(_STRENGTH.get(names.link_match(cap.url, j.main.link), 0), j) for j in jobs]
        best = max((s for s, _ in scored), default=0)
        hits = [j for s, j in scored if s == best] if best else []
        if best >= 2:
            for j in hits:
                j.captures.append(cap)
            continue
        if len(hits) > 1:
            hits = [j for j in hits if mentions_company(cap.title, j.main.company)]
        if len(hits) == 1:
            found = hits[0]
        if found is None and hp:
            same_path = [j for j in jobs if names.host_path(j.main.link) == hp]
            found = same_path[0] if len(same_path) == 1 else None
        if found is None and host:
            same_host = [j for j in jobs if any(names.link_host(r.link) == host for r in j.records)]
            if len(same_host) == 1:
                found = same_host[0]
            elif same_host:
                named = [j for j in same_host if mentions_company(cap.title, j.main.company)]
                found = named[0] if len(named) == 1 else None
        if found is None and cap.title:
            named = [j for j in jobs if mentions_company(cap.title, j.main.company)]
            found = named[0] if len(named) == 1 else None
        if found is None:
            left.append(cap)
        else:
            found.captures.append(cap)
    for job in jobs:
        job.captures.sort(key=lambda c: c.saved_local or datetime.max)
    return left


def _record_checks(job: Job, opts: ScanOptions) -> None:
    recs = job.records
    if any(names.is_placeholder_record(r.company, r.title) for r in recs):
        job.add("placeholder_record", "issue", "record", "A tracker record still says 'Company Name' / 'Job Title'.")
    if job.withdrawn:
        reason = next((r.notes for r in recs if r.notes.strip()), "")
        if not reason:
            job.add("withdrawn_no_reason", "issue", "record", "Marked Withdrawn without a reason in the notes.")
        else:
            job.add("withdrawn", "info", "record", f"Withdrawn: {reason[:160]}")
    link = job.main.link.strip()
    host = names.link_host(link)
    cap_hosts = {names.link_host(c.url) for c in job.captures if c.url}
    excluded = [s for s in opts.excluded_sites if any(names.host_matches(h, s) for h in {host} | cap_hosts if h)]
    if excluded:
        job.add("excluded_site", "fail", "record", f"Job is on {excluded[0]}, an excluded site.")
    if not link:
        job.add("no_link", "issue", "record", "No job link in the tracker.")
    elif names.is_flow_page(link):
        job.add("flow_link", "issue", "record", "The tracker link is a page inside the application, not the job "
                "posting, so it won't open the job later.")
    if link and opts.require_jobright_link and not names.host_matches(host, names.JOBRIGHT_HOST):
        job.add("not_jobright_link", "issue", "record", "The tracker link isn't the Jobright job link.")
    if not opts.allow_jobright_apply:
        tagged = any(names.jobright_id(u) and not names.host_matches(names.link_host(u), names.JOBRIGHT_HOST)
                     for u in [link] + [c.url for c in job.captures])
        if tagged:
            job.add("jobright_apply", "issue", "record", "Applied through Jobright's Apply button (the link carries Jobright's tag).")


_MAX_MATCHES = 10


def _match_row(h: Rec, how: str, work_day: date, clock: Clock) -> dict:
    """An earlier record's values, shown next to the warning."""
    days = (work_day - h.applied_day).days if h.applied_day else None
    return {
        "date": h.applied_day.isoformat() if h.applied_day else "",
        "time": clock.hm(h.created_utc) if days == 0 else "",
        "days_ago": days,
        "who": h.creator_name,
        "company": h.company,
        "title": h.title,
        "link": h.link,
        "status": h.status,
        "how": how,
    }


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _duplicate_checks(job: Job, pool: list[Rec], opts: ScanOptions, work_day: date, clock: Clock) -> None:
    """Warn about the candidate's earlier records with a matching job link
    (any time before) or company name (within the window).

    Neither match is reliable enough to decide on its own, so these are
    warnings that list the other records' values for the reviewer."""
    if job.withdrawn:
        return
    me = job.main
    mine = (me.created_utc or datetime.max, me.id)
    agencies = [names.company_tokens(a) for a in opts.staffing_agencies]
    tokens = names.company_tokens(me.company)
    company_check = bool(tokens) and not names.is_agency(tokens, agencies)
    window = opts.duplicate_window_days
    by_link: list[tuple[Rec, str]] = []
    by_company: list[tuple[Rec, str]] = []
    for h in pool:
        if h.id == me.id or h.profile_id != me.profile_id or names.is_withdrawn(h.status) or not h.applied_day:
            continue
        days = (work_day - h.applied_day).days
        if days < 0 or (days == 0 and not (h.created_utc or datetime.max, h.id) < mine):
            continue  # later than this record; that one gets the warning
        how = names.link_match(me.link, h.link)
        if how:
            by_link.append((h, names.LINK_MATCH_LABELS[how]))
            continue
        if not company_check or days > window or (days == window and not opts.window_includes_last_day):
            continue
        other = names.company_tokens(h.company)
        if names.is_agency(other, agencies) or not names.same_company(tokens, other):
            continue
        by_company.append((h, "same name" if "".join(other) == "".join(tokens) else "similar name"))

    def newest(pairs: list[tuple[Rec, str]]) -> list[dict]:
        pairs.sort(key=lambda p: (p[0].applied_day, p[0].created_utc or datetime.min), reverse=True)
        return [_match_row(h, how, work_day, clock) for h, how in pairs[:_MAX_MATCHES]]

    if by_link:
        job.add("same_link", "check", "history",
                f"The job link matches {_plural(len(by_link), 'earlier record')} for this candidate. "
                "Compare them and decide if it's the same job.", matches=newest(by_link))
    if by_company:
        job.add("same_company", "check", "history",
                f"The company name matches {_plural(len(by_company), 'record')} for this candidate in the "
                f"last {window} days. Compare them and decide if it's the same end client.",
                matches=newest(by_company))


def _file_checks(job: Job, opts: ScanOptions, contact: tuple[str, str], all_companies: list[str]) -> None:
    folder = job.folder
    if folder is None:
        if not job.withdrawn:
            job.add("no_resume_folder", "issue", "resume", "No resume folder for this job in the resumes ZIP.")
        return
    resume, cover = folder.first("resume"), folder.first("cover")
    email, phone = contact
    for doc, label, limit in ((resume, "Resume", opts.resume_max_pages),
                              (cover, "Cover letter", opts.cover_letter_max_pages)):
        area = "resume" if label == "Resume" else "cover"
        if doc is None:
            if label == "Resume":
                job.add("no_resume_file", "issue", area, "The job's folder has no resume file.")
            else:
                job.add("no_cover_letter", "info", area, "No cover letter in the folder.")
            continue
        if doc.error:
            job.add("unreadable_file", "issue", area, f"{label}: {doc.error}.")
            continue
        if doc.ext == ".pdf" and len(doc.text.strip()) < 200:
            job.add("no_text", "issue", area, f"{label} has almost no readable text (an image or an empty page?).")
            continue
        markers = find_markers(doc.text, opts.leftover_markers)
        if markers:
            job.add("leftover_text", "fail", area, f"{label} has leftover text: " + " | ".join(f"“{m}”" for m in markers[:3]))
        if limit and doc.pages and doc.pages > limit:
            job.add("too_many_pages", "issue", area, f"{label} is {doc.pages} pages (limit {limit}).")
        emails, phones = contacts(doc.text)
        if email and emails and email not in emails:
            job.add("wrong_email", "fail", area, f"{label} shows {', '.join(sorted(emails))}, but the profile email is {email}.")
        if phone and phones and phone not in phones:
            job.add("wrong_phone", "issue", area, f"{label} phone number differs from the profile.")
    if cover is not None and not cover.error and cover.text.strip():
        company = job.main.company
        if not mentions_company(cover.text, company) and not mentions_company(cover.text, folder.name):
            job.add("cover_missing_company", "issue", "cover", f"The cover letter doesn't name {company}.")
        others = names_other_company(cover.text, company, [c for c in all_companies if c != company])
        if others:
            job.add("cover_other_company", "issue", "cover", f"The cover letter names {', '.join(others[:3])}, another job from today. "
                    "Check it isn't left over.")
        if job.main.title and not title_found(cover.text, job.main.title):
            job.add("cover_missing_title", "check", "cover", "The cover letter doesn't seem to mention the job title.")


def _capture_checks(job: Job, opts: ScanOptions, contact: tuple[str, str]) -> None:
    if not job.captures:
        if not job.withdrawn:
            job.add("no_capture", "issue", "capture", "No form capture. If it wasn't submitted, mark it Withdrawn.")
        return
    if job.withdrawn:
        job.add("withdrawn_but_captured", "check", "capture", "Marked Withdrawn, but its form was captured. Was it submitted?")
    if len(job.captures) > 1:
        job.add("several_captures", "info", "capture", f"{len(job.captures)} captures (form steps or retakes).")
    for cap in job.captures:
        if cap.error:
            job.add("unreadable_capture", "issue", "capture", f"{cap.rel}: {cap.error}.")
    if any(c.confirmation for c in job.captures):
        job.add("confirmation", "info", "capture", "Confirmation page saved.")
    resume = job.folder.first("resume") if job.folder else None
    saved = [c.saved_local for c in job.captures if c.saved_local]
    if opts.check_capture_order and resume and resume.made_at and saved and max(saved) < resume.made_at - timedelta(minutes=1):
        job.add("capture_before_resume", "check", "capture", f"The form was saved at {max(saved):%H:%M}, before this resume was made "
                f"at {resume.made_at:%H:%M}. The resume in the ZIP may not be the one uploaded.")
    if not any(c.attached for c in job.captures):
        job.add("no_attachment_seen", "info", "capture", "No attached file name is visible in the capture.")
    email = contact[0]
    for cap in job.captures:
        if email and cap.emails and email not in cap.emails:
            job.add("form_email", "issue", "capture", f"The form's email is {', '.join(cap.emails)}, not the profile email.")
            break
    seen: set[str] = set()
    for cap in job.captures:
        for q, a in cap.answers:
            if q in seen:
                continue
            seen.add(q)
            if _HEARD.search(q) and _REFERRED.search(a):
                job.add("referral_answer", "issue", "capture", f"“{_short(q)}” was answered “{a[:40]}”. Supporters never pick Referral.")
            elif _CATEGORIES["being referred by an employee"].search(q) and _yes_no(a) == "Yes":
                job.add("referred_yes", "issue", "capture", f"“{_short(q)}” was answered Yes.")
            elif opts.flag_consent_answers and _CONSENT.search(q) and a:
                job.add("consent_answer", "check", "capture", f"Consent question: “{_short(q)}” → {a[:40]}")
    snippets: list[str] = []
    for cap in job.captures:
        snippets += [s for s in cap.onsite if s not in snippets]
    for snippet in snippets[:2]:
        job.add("onsite_wording", "check", "capture", f"The page mentions: “{snippet[:160]}”")


def _consistency(jobs: list[Job]) -> list[dict]:
    seen: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for job in jobs:
        for cap in job.captures:
            for q, a in cap.answers:
                if not a:
                    continue
                value = _yes_no(a)
                if not value:
                    continue
                for cat, rx in _CATEGORIES.items():
                    if rx.search(q):
                        if job.main.company not in seen[cat][value]:
                            seen[cat][value].append(job.main.company)
                        break
    out = []
    for cat, values in seen.items():
        if len(values) > 1:
            parts = [f"{v} ({len(c)}: {', '.join(c[:4])}{'…' if len(c) > 4 else ''})"
                     for v, c in sorted(values.items(), key=lambda kv: -len(kv[1]))]
            out.append({"code": "different_answers", "level": "check", "text": f"Different answers about {cat} on different jobs: " + "; ".join(parts)})
    return out


def run_checks(
    *,
    records: list[Rec],
    history: list[Rec],
    folders: list[JobFolder],
    captures: list[Capture],
    other_evidence: list[str],
    opts: ScanOptions,
    work_day: date,
    contacts_by_profile: dict[str | None, tuple[str, str]],
    clock: Clock,
) -> tuple[list[Job], list[dict], dict]:
    jobs = group_jobs(records)
    # Today's records count as earlier records for the ones made after them.
    pool = list({r.id: r for r in [*history, *records]}.values())
    spare_folders = match_folders(jobs, folders)
    spare_captures = match_captures(jobs, captures)
    companies = list(dict.fromkeys(j.main.company for j in jobs))

    previous: datetime | None = None
    for job in jobs:
        contact = contacts_by_profile.get(job.main.profile_id, ("", ""))
        _record_checks(job, opts)
        _duplicate_checks(job, pool, opts, work_day, clock)
        _file_checks(job, opts, contact, companies)
        _capture_checks(job, opts, contact)
        made = job.main.created_utc
        if opts.pace_minutes and previous and made and made - previous < timedelta(minutes=opts.pace_minutes):
            gap = (made - previous).total_seconds() / 60
            job.add("fast_pace", "info", "record", f"Made {gap:.0f} min after the previous application.")
        previous = made or previous
        job.findings.sort(key=lambda f: _RANK[f["level"]])

    # Day-level findings can carry files, so the reviewer can open them too.
    day: list[dict] = []
    for f in spare_folders:
        day.append({"code": "untracked_folder", "level": "issue", "text": f"Resume folder “{f.name}” has no tracker record. Was it tracked (Continue clicked)?",
                    "files": [{"area": "resumes", "rel": d.rel, "label": d.rel.rsplit("/", 1)[-1]} for d in f.files]})
    for c in spare_captures:
        day.append({"code": "unmatched_capture", "level": "issue", "text": f"Capture “{c.title or c.rel}” doesn't match any tracker record.",
                    "files": [{"area": "snapshots", "rel": c.rel, "label": c.rel.rsplit("/", 1)[-1]}]})
    if other_evidence:
        day.append({"code": "other_evidence", "level": "info", "text": f"{len(other_evidence)} other evidence files (screenshots etc.) weren't matched automatically.",
                    "files": [{"area": "snapshots", "rel": r, "label": r.rsplit("/", 1)[-1]} for r in other_evidence[:50]]})
    if opts.check_answer_consistency:
        day.extend(_consistency(jobs))
    times = [clock.local(j.main.created_utc) for j in jobs if j.main.created_utc]
    if len(times) > 1:
        span = (max(times) - min(times)).total_seconds() / 60
        day.append({"code": "work_span", "level": "info", "text": f"Worked {min(times):%H:%M}–{max(times):%H:%M} ({clock.label}), "
                    f"about {span / max(1, len(times) - 1):.0f} min per job."})

    status = Counter(j.status for j in jobs)
    levels = Counter(f["level"] for j in jobs for f in j.findings)
    summary = {
        "records": len(records),
        "jobs": len(jobs),
        "same_link": sum(any(f["code"] == "same_link" for f in j.findings) for j in jobs),
        "same_company": sum(any(f["code"] == "same_company" for f in j.findings) for j in jobs),
        "withdrawn": sum(j.withdrawn for j in jobs),
        "resume_folders": len(folders),
        "captures": len(captures),
        "other_evidence": len(other_evidence),
        "jobs_without_capture": sum(not j.captures and not j.withdrawn for j in jobs),
        "jobs_without_resume": sum(j.folder is None and not j.withdrawn for j in jobs),
        "confirmations": sum(any(c.confirmation for c in j.captures) for j in jobs),
        "status": {k: status.get(k, 0) for k in ("fail", "issue", "check", "ok")},
        "findings": {k: levels.get(k, 0) for k in LEVELS},
        "clock": clock.label,
    }
    return jobs, day, summary
