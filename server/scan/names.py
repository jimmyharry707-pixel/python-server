"""Company-name and job-link helpers shared by the scan checks."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_LEGAL_SUFFIXES = frozenset(
    {
        "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co",
        "company", "plc", "gmbh", "lp", "llp", "pbc", "sa", "ag", "bv", "pte", "pty",
    }
)
_NON_WORD = re.compile(r"[^a-z0-9]+")
_JOBRIGHT_ID = re.compile(r"(?:[?&]jr_id=|jobright\.ai/jobs/info/)([0-9a-f]{20,32})", re.I)
# A page from the middle of an application flow, not the job posting itself
# (e.g. apply.appone.com/application/resume). It won't open the job later.
_FLOW_PAGE = re.compile(
    r"/(?:application|apply)/(?:resume|education|experience|questions|review|voluntary|"
    r"personal|contact|documents|profile|step\d*)\b",
    re.I,
)
JOBRIGHT_HOST = "jobright.ai"


def company_tokens(name: str | None) -> tuple[str, ...]:
    """``"Bytex.Technology, Inc."`` -> ``("bytex", "technology")``."""
    raw = (name or "").lower().replace("&", " and ")
    tokens = [t for t in _NON_WORD.split(raw) if t]
    if tokens and tokens[0] == "the" and len(tokens) > 1:
        tokens = tokens[1:]
    while len(tokens) > 1 and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    if tokens in (["unknown"], ["company", "name"]):
        return ()
    return tuple(tokens)


def _contains_run(longer: tuple[str, ...], shorter: tuple[str, ...]) -> bool:
    n = len(shorter)
    return any(longer[i : i + n] == shorter for i in range(len(longer) - n + 1))


# Words that say nothing about which company it is; a name made only of these
# never matches part of another name ("Health" vs "Duo Health").
_GENERIC = frozenset(
    {
        "health", "tech", "technology", "technologies", "solutions", "systems", "group",
        "labs", "software", "data", "digital", "global", "services", "consulting",
        "engineer", "engineering", "ai", "io", "inc", "holdings", "partners", "capital",
    }
)


def same_company(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """Equal names, or one name's words appear in order inside the other.

    Partial matches need a distinctive name: at least 4 letters and not made
    only of generic words, so "Health" doesn't match "Duo Health".
    """
    if not a or not b:
        return False
    if "".join(a) == "".join(b):
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len("".join(shorter)) < 4 or all(t in _GENERIC for t in shorter):
        return False
    return _contains_run(longer, shorter)


def is_agency(tokens: tuple[str, ...], agencies: list[tuple[str, ...]]) -> bool:
    """The company name contains a listed agency name (``Robert Half Technology``)."""
    if not tokens:
        return False
    compact = "".join(tokens)
    for agency in agencies:
        if not agency:
            continue
        if compact == "".join(agency) or (len(agency) <= len(tokens) and _contains_run(tokens, agency)):
            return True
    return False


def link_host(url: str | None) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        host = (urlsplit(raw).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def host_matches(host: str, domain: str) -> bool:
    domain = domain.strip().lower()
    if domain.startswith("www."):
        domain = domain[4:]
    return bool(domain) and (host == domain or host.endswith("." + domain))


def host_path(url: str | None) -> str:
    """``https://www.x.com/a/b/?q=1`` -> ``x.com/a/b``."""
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw.lower()
    return link_host(raw) + parts.path.rstrip("/")


def jobright_id(url: str | None) -> str:
    """The Jobright job ID in a Jobright link or a ``jr_id=`` tag, or ""."""
    m = _JOBRIGHT_ID.search(url or "")
    return m.group(1).lower() if m else ""


# Job links are compared to warn the reviewer, never to decide on their own:
# a site can show one job under several addresses, or many jobs under one.

_GREENHOUSE_ID = re.compile(r"greenhouse\.io/(?!embed/)[^/?#]+/jobs/(\d{5,})|[?&]gh_jid=(\d{5,})", re.I)
_LEVER_ID = re.compile(r"lever\.co/[^/?#]+/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)
_DICE_ID = re.compile(
    r"dice\.com/(?:job-detail|job-applications)/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I
)
_ASHBY_ID = re.compile(
    r"(?:ashbyhq\.com/[^/?#]+/|[?&]ashby_jid=)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I
)
# Query parts that name the job (UKG opportunityId, Taleo job, Indeed jk...).
_ID_PARAMS = frozenset(
    {
        "token", "for", "gh_jid", "ashby_jid", "jobid", "job_id", "job", "jk", "jid", "id", "pid",
        "req", "reqid", "req_id", "jobreqid", "job_req_id", "career_job_req_id", "requisitionid",
        "requisition_id", "opportunityid", "postingid", "jobpostingid", "posting_id", "positionid",
        "vacancyid", "currentjobid", "jobcode",
    }
)
# Query parts that only say where the click came from.
_TRACKING_PARAMS = frozenset(
    {
        "jr_id", "gh_src", "src", "source", "ref", "referrer", "referral", "lever-source", "lever-origin",
        "lever-via", "iis", "iisn", "mode", "trk", "trackingid", "refid", "sid", "lang", "locale", "hl",
        "gclid", "fbclid", "mc_cid", "mc_eid", "codes", "utm", "searchlink", "searchid",
    }
)
# Last path parts that are a step of applying, not the job itself
# (Lever's /apply, Ashby's /application, iCIMS's /job, Workday's /applyManually).
_APPLY_STEPS = frozenset(
    {
        "apply", "application", "applications", "applymanually", "apply-manually", "autofillwithresume",
        "usemylastapplication", "login", "register", "job", "candidate", "thanks", "thank-you",
        "thankyou", "confirmation", "success", "submitted",
    }
)
# Cleaning keeps a page people open: a last "/job" (iCIMS, careers sites) is
# the job itself there, not a step.
_CLEAN_STEPS = _APPLY_STEPS - {"job", "candidate"}
_LOCALE = re.compile(r"[a-z]{2}-[a-z]{2}")
_ID_LIKE = re.compile(r"\d{3,}|[0-9a-f]{8}-[0-9a-f]{4}", re.I)

LINK_MATCH_LABELS = {
    "same": "same link",
    "jr": "same Jobright job",
    "gh": "same Greenhouse job",
    "lever": "same Lever job",
    "ashby": "same Ashby job",
    "wd": "same Workday job",
    "dice": "same Dice job",
    "page": "same page address",
    "page_weak": "same general page (no job number in the link)",
}
_ID_KINDS = ("jr", "gh", "lever", "ashby", "wd", "dice")


def _split(url: str) -> tuple[str, list[str], list[tuple[str, str]]]:
    """Host, lowercased path parts without apply steps, and query pairs."""
    raw = url if "://" in url else "https://" + url
    try:
        parts = urlsplit(raw)
    except ValueError:
        return "", [], []
    segs = [s for s in parts.path.lower().split("/") if s]
    if len(segs) > 1 and _LOCALE.fullmatch(segs[0]):
        segs = segs[1:]  # Workday's /en-US/ is optional
    while len(segs) > 1 and segs[-1] in _APPLY_STEPS:
        segs.pop()
    pairs = []
    for part in parts.query.split("&"):
        name, _, value = part.partition("=")
        if name and value:
            pairs.append((name.lower(), value.lower()))
    return link_host(raw), segs, pairs


def job_keys(url: str | None) -> dict[str, str]:
    """Every way to recognise the job behind a link, as {kind: key}.

    Two links match when they share a key of the same kind."""
    raw = (url or "").strip()
    if not raw:
        return {}
    keys: dict[str, str] = {}
    jr = jobright_id(raw)
    if jr:
        keys["jr"] = jr
    for kind, rx in (("gh", _GREENHOUSE_ID), ("lever", _LEVER_ID), ("ashby", _ASHBY_ID), ("dice", _DICE_ID)):
        m = rx.search(raw)
        if m:
            keys[kind] = next(g for g in m.groups() if g).lower()
    host, segs, pairs = _split(raw)
    if not host:
        return keys
    if host.endswith("greenhouse.io") and "gh" not in keys:
        token = dict(pairs).get("token", "")
        if token.isdigit() and len(token) >= 5:
            keys["gh"] = token
    if host.endswith((".myworkdayjobs.com", ".myworkdaysite.com")) and segs:
        tenant = host.split(".")[0]
        if host.endswith(".myworkdaysite.com") and len(segs) > 1 and segs[0] == "recruiting":
            tenant = segs[1]
        req = segs[-1].rsplit("_", 1)[-1] if "_" in segs[-1] else ""
        if any(ch.isdigit() for ch in req):
            keys["wd"] = f"{tenant}:{req}"  # the location part of the path can differ
    has_id = any(_ID_LIKE.search(s) for s in segs)
    kept = sorted(
        f"{n}={v}" for n, v in pairs
        if n in _ID_PARAMS or (not has_id and n not in _TRACKING_PARAMS and not n.startswith("utm_"))
    )
    page = host + "/" + "/".join(segs) + ("?" + "&".join(kept) if kept else "")
    strong = has_id or any(n in _ID_PARAMS for n, _ in pairs) or any(_ID_LIKE.search(k) for k in kept)
    keys["page" if strong else "page_weak"] = page
    return keys


def link_match(a: str | None, b: str | None) -> str:
    """How two job links point at the same job ("" when they don't), as a
    kind from LINK_MATCH_LABELS. The strongest shared key wins."""
    a, b = (a or "").strip(), (b or "").strip()
    if not a or not b:
        return ""
    if a.rstrip("/").lower() == b.rstrip("/").lower():
        return "same"
    ka, kb = job_keys(a), job_keys(b)
    for kind in _ID_KINDS:
        if ka.get(kind) and ka.get(kind) == kb.get(kind):
            return kind
    if any(ka.get(k) and kb.get(k) for k in _ID_KINDS):
        return ""  # both carry a job number of the same kind, and they differ
    for kind in ("page", "page_weak"):
        if ka.get(kind) and ka.get(kind) == kb.get(kind):
            return kind
    return ""


_GREENHOUSE_EMBED = re.compile(r"/embed/job_app/?$", re.I)


def clean_job_link(url: str | None) -> str:
    """The job's own posting address: no step of applying, no click tracking.

    ``job-boards.greenhouse.io/embed/job_app?for=acme&token=4821`` becomes
    ``https://job-boards.greenhouse.io/acme/jobs/4821``; a trailing ``/apply``
    (Lever, Workday), ``/application`` (Ashby) or thank-you step goes;
    ``utm_*``, ``gh_src`` and the other tracking parts go; the job's own ID
    parameters and anything unknown stay. The extension's
    ``util.cleanJobLink`` does the same: both are tested against
    ``extension/test/fixtures/job_links.json``."""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw if "://" in raw else "https://" + raw)
    except ValueError:
        return raw
    if not parts.netloc:
        return raw
    origin = f"{parts.scheme.lower()}://{parts.netloc.lower()}"
    pairs = [p for p in parts.query.split("&") if p]
    names_ = [p.partition("=")[0].lower() for p in pairs]
    if host_matches(parts.hostname or "", "greenhouse.io") and _GREENHOUSE_EMBED.search(parts.path):
        values = {n: p.partition("=")[2] for n, p in zip(names_, pairs)}
        board, token = values.get("for", ""), values.get("token", "")
        if board and token.isdigit():
            return f"{origin}/{board}/jobs/{token}"
    path = parts.path or "/"
    segs = path.rstrip("/").split("/")
    trimmed = False
    while len([s for s in segs if s]) > 1 and segs[-1].lower() in _CLEAN_STEPS:
        segs.pop()
        trimmed = True
    if trimmed:
        path = "/".join(segs)
    kept = [
        p for n, p in zip(names_, pairs)
        if n in _ID_PARAMS or not (n in _TRACKING_PARAMS or n.startswith("utm_"))
    ]
    return origin + path + ("?" + "&".join(kept) if kept else "")


def is_flow_page(url: str | None) -> bool:
    return bool(_FLOW_PAGE.search(url or ""))


def parse_sites(value: str | list[str] | None) -> list[str]:
    items = value if isinstance(value, list) else re.split(r"[,\n]+", value or "")
    out: list[str] = []
    for item in items:
        host = link_host(item) or item.strip().lower()
        if host and host not in out:
            out.append(host)
    return out


def is_withdrawn(status: str | None) -> bool:
    return (status or "").strip().lower() == "withdrawn"


def is_placeholder_record(company: str | None, title: str | None) -> bool:
    c = (company or "").strip().lower()
    t = (title or "").strip().lower()
    return c in ("", "unknown", "company name", "company") or t in (
        "", "unknown", "unknown position", "job title", "position", "title",
    )
