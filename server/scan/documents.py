"""Reading the resumes ZIP: one folder per job with a resume and a cover letter.

CareerCraft's Export ZIP lays files out as ``<date>/<Company>/<files>``; ZIPs
made by hand often drop the date folder. Both work. Loose files (no company
folder) are grouped by the company part of the file name.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .names import company_tokens

DOC_TYPES = {".pdf", ".docx", ".doc"}
_DATE_DIR = re.compile(r"^20\d\d[-_.]\d\d[-_.]\d\d$")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]*)?\(?\d{3}\)?[\s.-]*\d{3}[\s.-]*\d{4}(?!\d)")
_GENERIC = {"health", "tech", "technology", "technologies", "solutions", "systems", "group",
            "labs", "software", "data", "digital", "global", "services", "ai", "io", "inc"}


@dataclass
class DocFile:
    rel: str            # path inside the ZIP, used by the viewer later
    kind: str           # resume | cover | other
    ext: str
    made_at: datetime | None  # file time from the ZIP (supporter's PC clock)
    pages: int | None = None
    page_size: str = ""
    text: str = ""
    error: str = ""


@dataclass
class JobFolder:
    name: str
    rel: str
    files: list[DocFile] = field(default_factory=list)

    def first(self, kind: str) -> DocFile | None:
        return next((f for f in self.files if f.kind == kind), None)


def _kind(name: str) -> str:
    low = name.lower()
    if "cover" in low:
        return "cover"
    if "resume" in low or re.search(r"(^|[^a-z])cv([^a-z]|$)", low):
        return "resume"
    return "other"


def find_folders(root: Path) -> list[JobFolder]:
    folders: dict[str, JobFolder] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in DOC_TYPES:
            continue
        parent = path.parent
        rel = path.relative_to(root).as_posix()
        if parent == root or _DATE_DIR.match(parent.name):
            # Loose file: "Company_Role_Req_Resume.pdf" -> "Company".
            name = re.split(r"[_]", path.stem)[0].strip() or path.stem
            key = (parent.relative_to(root).as_posix() + "/" + name).strip("/")
        else:
            name = parent.name
            key = parent.relative_to(root).as_posix()
        folder = folders.setdefault(key, JobFolder(name=name, rel=key))
        made = datetime.fromtimestamp(path.stat().st_mtime)
        folder.files.append(DocFile(rel=rel, kind=_kind(path.name), ext=path.suffix.lower(), made_at=made))
    out = list(folders.values())
    for folder in out:
        # A single unnamed document is the resume.
        if not folder.first("resume"):
            other = folder.first("other")
            if other is not None:
                other.kind = "resume"
    return sorted(out, key=lambda f: f.name.lower())


def read_document(root: Path, doc: DocFile) -> None:
    path = root / doc.rel
    try:
        if doc.ext == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            doc.pages = len(reader.pages)
            if reader.pages:
                box = reader.pages[0].mediabox
                w, h = round(float(box.width)), round(float(box.height))
                doc.page_size = {(612, 792): "Letter", (595, 842): "A4"}.get((w, h), f"{w}x{h} pt")
            doc.text = "\n".join((p.extract_text() or "") for p in reader.pages)
        elif doc.ext == ".docx":
            with zipfile.ZipFile(path) as zf:
                xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
            paras = re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)
            doc.text = "\n".join("".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)) for p in paras)
        else:
            doc.error = "Old .doc files can't be read automatically"
    except Exception as exc:  # a broken file is a finding, not a crash
        doc.error = f"Can't be read: {type(exc).__name__}"


def find_markers(text: str, markers: list[str]) -> list[str]:
    """Leftover ChatGPT or template text, each with a little context."""
    found: list[str] = []
    spans: list[tuple[int, int]] = []
    for marker in markers:
        if marker == "[...]":
            pattern = r"\[[^\]\n]{1,80}\]"
        elif re.fullmatch(r"[\w ]+", marker):
            pattern = r"(?<!\w)" + re.escape(marker) + r"(?!\w)"
        else:
            pattern = re.escape(marker)
        for m in re.finditer(pattern, text, re.I):
            if any(m.start() < e and s < m.end() for s, e in spans):
                continue  # the same spot, found by another marker
            spans.append((m.start(), m.end()))
            # About 30 characters either side, cut at whole words.
            before = text[max(0, m.start() - 30) : m.start()]
            after = text[m.end() : m.end() + 30]
            if m.start() > 30 and " " in before:
                before = before.split(" ", 1)[1]
            if m.end() + 30 < len(text) and " " in after:
                after = after.rsplit(" ", 1)[0]
            snippet = " ".join((before + m.group(0) + after).split())
            if snippet not in found:
                found.append(snippet)
            if len(found) >= 8:
                return found
    return found


def contacts(text: str) -> tuple[set[str], set[str]]:
    emails = {e.lower().rstrip(".") for e in EMAIL.findall(text)}
    phones = {re.sub(r"\D", "", p)[-10:] for p in PHONE.findall(text)}
    return emails, {p for p in phones if len(p) == 10}


def _norm(text: str) -> str:
    return " " + " ".join(re.findall(r"[a-z0-9]+", text.lower())) + " "


def mentions_company(text: str, name: str) -> bool:
    """The text names the company, allowing a shorter form ("Thermo Fisher"
    for "Thermo Fisher Scientific", "Jaxon" for "Jaxon.AI")."""
    tokens = list(company_tokens(name))
    if not tokens:
        return True
    norm = _norm(text)
    compact = norm.replace(" ", "")
    while tokens:
        if f" {' '.join(tokens)} " in norm or (len(tokens) > 1 and "".join(tokens) in compact):
            return True
        tokens = tokens[:-1]
        if len(tokens) == 1 and (len(tokens[0]) < 4 or tokens[0] in _GENERIC):
            break
    return False


def names_other_company(text: str, own: str, others: list[str]) -> list[str]:
    """Other companies from the same day that appear in this text, written
    with their capital letters (so "mesh" the word doesn't count as Mesh)."""
    own_tokens = company_tokens(own)
    hits = []
    for other in others:
        tokens = company_tokens(other)
        if not tokens or tokens == own_tokens or set(tokens) <= set(own_tokens):
            continue
        if len("".join(tokens)) < 4 or all(t in _GENERIC for t in tokens):
            continue
        shown = re.sub(r"[,.]?\s*(Inc|LLC|Ltd|Corp)\.?$", "", other.strip())
        if re.search(r"(?<![\w])" + re.escape(shown) + r"(?![\w])", text):
            hits.append(other)
    return hits


def title_found(text: str, title: str) -> bool:
    words = [w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 2]
    if not words:
        return True
    norm = _norm(text)
    return sum(f" {w} " in norm for w in words) / len(words) >= 0.6
