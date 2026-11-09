"""Summary report: what saved daily reviews add up to over a date range.

Built only from finished scans (their stored results and the reviewer's
verdicts), never by re-checking raw tracker data. When the same supporter's
day was reviewed more than once, the latest review counts.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime

from .checks import FINDING_LABELS

_STATUSES = ("fail", "issue", "check", "ok")
_VERDICTS = ("pass", "issue", "fail")
# Day-level findings that are problems worth counting.
_DAY_CODES = {"untracked_folder", "unmatched_capture", "different_answers"}


def _empty() -> dict:
    return {
        "days": 0,
        "records": 0,
        "jobs": 0,
        "status": {k: 0 for k in _STATUSES},
        "verdicts": {**{k: 0 for k in _VERDICTS}, "none": 0},
        "problems": Counter(),
    }


def _add(total: dict, scan: dict) -> dict:
    """Add one scan to a running total; returns this scan's own numbers."""
    s = scan["summary"] or {}
    jobs = scan["jobs"]
    one = _empty()
    one["days"] = 1
    one["records"] = s.get("records", 0)
    one["jobs"] = len(jobs)
    for job in jobs:
        one["status"][job["status"] if job["status"] in _STATUSES else "ok"] += 1
        one["verdicts"][job["verdict"] if job["verdict"] in _VERDICTS else "none"] += 1
        codes = {f.get("code") for f in job["findings"] if f.get("level") != "info" and f.get("code")}
        one["problems"].update(codes)
    one["problems"].update(f.get("code") for f in scan["day_findings"] if f.get("code") in _DAY_CODES)
    for key in ("days", "records", "jobs"):
        total[key] += one[key]
    for k, v in one["status"].items():
        total["status"][k] += v
    for k, v in one["verdicts"].items():
        total["verdicts"][k] += v
    total["problems"].update(one["problems"])
    return one


def _public(t: dict, top: int = 5) -> dict:
    out = {k: v for k, v in t.items() if k != "problems"}
    out["top_problems"] = [
        {"code": code, "label": FINDING_LABELS.get(code, code), "count": n}
        for code, n in t["problems"].most_common(top)
    ]
    out["fail_rate"] = round(t["status"]["fail"] * 100 / t["jobs"], 1) if t["jobs"] else 0
    return out


def build_summary(scans: list[dict], *, date_from: date, date_to: date, made_by: str) -> dict:
    """``scans``: finished scans, each a dict with id, work_date, supporter
    (id, name), reviewer, summary, day_findings, reviewer_note, finished_at
    and jobs (status, verdict, findings)."""
    latest: dict[tuple[str, str], dict] = {}
    repeats = 0
    for scan in sorted(scans, key=lambda x: x["finished_at"] or datetime.min):
        key = (scan["supporter"]["id"], scan["work_date"])
        if key in latest:
            repeats += 1
        latest[key] = scan

    team = _empty()
    people: dict[str, dict] = {}
    for scan in sorted(latest.values(), key=lambda x: x["work_date"]):
        sup = scan["supporter"]
        person = people.setdefault(sup["id"], {"id": sup["id"], "name": sup["name"], "total": _empty(),
                                               "by_day": [], "notes": []})
        one = _add(person["total"], scan)
        _add(team, scan)
        person["by_day"].append({
            "date": scan["work_date"],
            "scan_id": scan["id"],
            "reviewer": scan["reviewer"],
            "jobs": one["jobs"],
            "status": one["status"],
            "verdicts": one["verdicts"],
        })
        if (scan.get("reviewer_note") or "").strip():
            person["notes"].append({"date": scan["work_date"], "note": scan["reviewer_note"].strip()})

    supporters = [
        {"id": p["id"], "name": p["name"], **_public(p["total"], 3), "by_day": p["by_day"], "notes": p["notes"]}
        for p in sorted(people.values(), key=lambda p: p["name"].lower())
    ]
    data = {
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "made_by": made_by,
        "made_at": datetime.utcnow().isoformat() + "Z",
        "repeated_days": repeats,
        "team": {**_public(team), "supporters": len(supporters)},
        "supporters": supporters,
    }
    data["text"] = render_text(data)
    return data


def _counts(status: dict, verdicts: dict, jobs: int) -> tuple[str, str]:
    st = f"{status['fail']} fail · {status['issue']} issue · {status['check']} to check · {status['ok']} ok"
    reviewed = jobs - verdicts["none"]
    vd = (f"{verdicts['pass']} pass · {verdicts['issue']} issue · {verdicts['fail']} fail"
          + (f" · {verdicts['none']} not reviewed" if verdicts["none"] else "")) if reviewed else "none given"
    return st, vd


def _day(value: str) -> str:
    try:
        return date.fromisoformat(value).strftime("%a %b %d").replace(" 0", " ")
    except ValueError:
        return value


def render_text(data: dict) -> str:
    t = data["team"]
    line = "-" * 72
    out = [
        f"REVIEW SUMMARY · {data['date_from']} to {data['date_to']}",
        "=" * 72,
        f"Made by {data['made_by']} on {data['made_at'][:10]}",
    ]
    if not t["days"]:
        out += ["", "No finished reviews in this period."]
        return "\n".join(out) + "\n"
    out.append(f"{t['days']} reviewed days · {t['supporters']} supporter{'s' if t['supporters'] != 1 else ''}")
    if data["repeated_days"]:
        out.append(f"{data['repeated_days']} days were reviewed more than once; the latest review counts.")
    st, vd = _counts(t["status"], t["verdicts"], t["jobs"])
    out += ["", "EVERYONE", line,
            f"Jobs checked:  {t['jobs']} (from {t['records']} tracker records)",
            f"Scan results:  {st} ({t['fail_rate']}% fail)",
            f"Your verdicts: {vd}"]
    if t["top_problems"]:
        out.append("Most common problems: " + ", ".join(f"{p['label']} ({p['count']})" for p in t["top_problems"]))

    for s in data["supporters"]:
        st, vd = _counts(s["status"], s["verdicts"], s["jobs"])
        out += ["", s["name"].upper() or "SUPPORTER", line,
                f"Days reviewed: {s['days']}",
                f"Jobs:          {s['jobs']} · {st} ({s['fail_rate']}% fail)",
                f"Verdicts:      {vd}"]
        if s["top_problems"]:
            out.append("Most common:   " + ", ".join(f"{p['label']} ({p['count']})" for p in s["top_problems"]))
        out.append("By day:")
        for d in s["by_day"]:
            dst = d["status"]
            dv = d["verdicts"]
            verdicts = (f"{dv['pass']} pass / {dv['issue']} issue / {dv['fail']} fail"
                        if d["jobs"] - dv["none"] else "no verdicts")
            out.append(f"  {_day(d['date']):<11} {d['jobs']:>3} jobs · {dst['fail']} fail · {dst['issue']} issue"
                       f" · {verdicts} · by {d['reviewer']}")
        if s["notes"]:
            out.append("Reviewer notes:")
            for n in s["notes"]:
                first, *rest = n["note"].splitlines()
                out.append(f"  {_day(n['date'])}: {first}")
                out += [f"    {r}" for r in rest]
    out.append("")
    out.append("Counts come from saved daily reviews only. Problems are counted once per job.")
    return "\n".join(out) + "\n"
