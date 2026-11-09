"""The plain-text report saved with each scan.

Built from the stored results, so it can be rebuilt at Finish with the
reviewer's verdicts and notes. Plain text so it pastes cleanly into Slack.
"""

from __future__ import annotations

from datetime import datetime

from .options import ScanOptions

_TAG = {"fail": "FAIL", "issue": "ISSUE", "check": "CHECK", "ok": "OK", "info": "info"}
_VERDICT = {"pass": "Pass", "issue": "Issue", "fail": "Fail"}


def _line(char: str = "-") -> str:
    return char * 72


def _matches(finding: dict, details: dict) -> list[str]:
    """This job's values, then each matching record's, under a warning."""
    me = (details.get("records") or [{}])[0]
    key = "link" if finding.get("code") == "same_link" else "company"
    out = [f"       This job:  {me.get('company', '')} · {me.get('title', '')}"]
    if key == "link":
        out.append(f"                  {me.get('link', '')}")
    for m in finding["matches"]:
        when = m["date"] + (f" {m['time']}" if m.get("time") else "")
        out.append(f"       {when:<16} {m['company']} · {m['title']} · by {m['who']} · {m['status'] or 'no status'}"
                   f" ({m['how']})")
        if key == "link":
            out.append(f"                  {m['link']}")
    return out


def build_report(
    *,
    supporter: str,
    reviewer: str,
    work_date: str,
    status: str,
    scanned_at: datetime | None,
    finished_at: datetime | None,
    summary: dict,
    day_findings: list[dict],
    jobs: list[dict],
    options: ScanOptions,
    uploads: dict,
    reviewer_note: str = "",
) -> str:
    s = summary or {}
    st = s.get("status", {})
    out = [
        f"DAILY REVIEW: {supporter} · {work_date}",
        _line("="),
        f"Reviewer: {reviewer}",
        f"Scanned:  {scanned_at:%Y-%m-%d %H:%M} UTC" if scanned_at else "Scanned:  not yet",
    ]
    if finished_at:
        out.append(f"Finished: {finished_at:%Y-%m-%d %H:%M} UTC")
    out.append(f"Status:   {status}")
    for key, label in (("resumes", "Resumes ZIP"), ("snapshots", "Snapshots ZIP")):
        info = uploads.get(key) or {}
        if info:
            out.append(f"{label}: {info.get('name', '')} ({info.get('files', 0)} files)")
    out += ["", "SUMMARY", _line()]
    out.append(f"Tracker records: {s.get('records', 0)}")
    matches = [f"{s[k]} {label}" for k, label in (("same_link", "with a matching job link"),
                                                  ("same_company", "with a matching company")) if s.get(k)]
    if matches:
        out.append(f"To compare:      {' · '.join(matches)}")
    out.append(f"Resume folders:  {s.get('resume_folders', 0)}")
    out.append(f"Captures:        {s.get('captures', 0)}"
               + (f" (+{s['other_evidence']} other files)" if s.get("other_evidence") else ""))
    if s.get("withdrawn"):
        out.append(f"Withdrawn:       {s['withdrawn']}")
    out.append(
        f"Jobs:            {st.get('fail', 0)} fail · {st.get('issue', 0)} issue · "
        f"{st.get('check', 0)} to check · {st.get('ok', 0)} ok"
    )
    reviewed = [j for j in jobs if j.get("verdict")]
    if reviewed:
        counts = {v: sum(j.get("verdict") == v for j in reviewed) for v in _VERDICT}
        out.append(
            f"Reviewer:        {counts['pass']} pass · {counts['issue']} issue · {counts['fail']} fail"
            f" ({len(reviewed)} of {len(jobs)} jobs reviewed)"
        )
    elif jobs:
        out.append("Reviewer:        no jobs reviewed")
    if s.get("clock"):
        out.append(f"Times shown on the supporter's clock ({s['clock']}).")

    if reviewer_note.strip():
        out += ["", "REVIEWER'S NOTE", _line()]
        out += [line.rstrip() for line in reviewer_note.strip().splitlines()]

    if day_findings:
        out += ["", "WHOLE DAY", _line()]
        for f in day_findings:
            out.append(f"[{_TAG.get(f['level'], f['level'])}] {f['text']}")

    out += ["", "JOBS", _line()]
    for i, job in enumerate(jobs, 1):
        d = job.get("details") or {}
        head = f"{i}. [{_TAG.get(job['status'], job['status'])}] {job['company']}"
        if job.get("title"):
            head += f" · {job['title']}"
        if d.get("time"):
            head += f" · {d['time']}"
        out.append(head)
        if job.get("verdict"):
            out.append(f"   Reviewer: {_VERDICT.get(job['verdict'], job['verdict'])}"
                       + (f". {job['note']}" if job.get("note") else ""))
        elif job.get("note"):
            out.append(f"   Reviewer note: {job['note']}")
        elif reviewed:
            out.append("   Reviewer: not reviewed")
        for f in job.get("findings") or []:
            out.append(f"   - [{_TAG.get(f['level'], f['level'])}] {f['text']}")
            if f.get("matches"):
                out += _matches(f, d)
        if not job.get("findings"):
            out.append("   - No automatic findings.")
    if not jobs:
        out.append("No tracker records for this supporter on this day.")

    o = options
    out += ["", "OPTIONS USED", _line()]
    out.append(
        f"Same company window: {o.duplicate_window_days} days"
        f" ({'including' if o.window_includes_last_day else 'excluding'} day {o.duplicate_window_days})"
    )
    out.append("Matching job links and company names: shown as warnings for the reviewer to decide")
    out.append(f"Tracker link must be Jobright: {'yes' if o.require_jobright_link else 'no'}"
               f" · Jobright Apply allowed: {'yes' if o.allow_jobright_apply else 'no'}")
    out.append(f"Excluded sites: {', '.join(o.excluded_sites) or 'none'}")
    out.append(f"Staffing agencies: {', '.join(o.staffing_agencies) or 'none'}")
    out.append(f"Leftover markers: {', '.join(o.leftover_markers) or 'none'}")
    pages = []
    if o.resume_max_pages:
        pages.append(f"resume ≤ {o.resume_max_pages}")
    if o.cover_letter_max_pages:
        pages.append(f"cover letter ≤ {o.cover_letter_max_pages}")
    out.append(f"Page limits: {', '.join(pages) or 'off'}")
    extras = [name for flag, name in (
        (o.flag_consent_answers, "consent answers"),
        (o.check_answer_consistency, "answer consistency"),
        (o.check_capture_order, "capture order"),
    ) if flag]
    out.append(f"Also checked: {', '.join(extras) or 'nothing else'}"
               + (f" · pace under {o.pace_minutes} min" if o.pace_minutes else ""))
    out.append("")
    out.append("Automatic checks only find duplicates, missing items and wrong records. "
               "Resume content and form answers are judged by the reviewer.")
    return "\n".join(out) + "\n"
