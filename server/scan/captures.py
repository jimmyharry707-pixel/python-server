"""Reading SingleFile captures of application forms.

A capture is a whole web page saved as one HTML file. SingleFile writes the
page address and the save time in a comment at the top, and stores what was
typed into the form in the ``value`` attributes. Custom dropdowns (Greenhouse,
Lever...) keep the chosen answer as plain text next to the question, so both
are read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

# Not "head": minified pages often leave out </head>, which would hide the page.
_SKIP = {"script", "style", "svg", "noscript", "template"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "param", "source", "track", "wbr"}
_TEXT_INPUTS = {"text", "email", "tel", "number", "url", "date", "search", ""}
_SF_URL = re.compile(r"url:\s*(\S+)")
_SF_DATE = re.compile(
    r"saved date:\s*\w{3} (\w{3}) (\d{1,2}) (\d{4}) (\d\d):(\d\d):(\d\d) GMT([+-]\d{4})"
)
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
# Only clear wording counts as a confirmation page; anything less is skipped.
_CONFIRM = re.compile(
    r"thank you for (?:applying|your application|submitting)|"
    r"(?:your )?application (?:has been|was) (?:successfully )?(?:submitted|received)|"
    r"we(?:'ve| have) received your application|application submitted",
    re.I,
)
_ONSITE = re.compile(
    r"\b(hybrid|on-?site|in-office|in office|in the office|relocat\w*|commut\w*)\b", re.I
)
_FILE = re.compile(r"(?<![\w.])[\w()&'+-][\w()&'.+-]*\.(?:pdf|docx?)\b", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "texts")

    def __init__(self, tag: str, attrs: dict, parent: "Node | None"):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: list[Node] = []
        self.texts: list[tuple[int, str]] = []  # (position among children, text)


def _walk(node: Node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def text_of(node: Node, skip: Node | None = None) -> str:
    """All text under ``node`` in reading order, leaving out ``skip`` and
    dropdown option lists."""
    out: list[str] = []
    stack: list = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            out.append(item)
            continue
        if item is skip or item.attrs.get("role") in ("listbox", "option") or item.tag == "option":
            continue
        # Interleave texts and children in document order.
        seq: list = []
        ti = 0
        for i, child in enumerate(item.children):
            while ti < len(item.texts) and item.texts[ti][0] <= i:
                seq.append(item.texts[ti][1])
                ti += 1
            seq.append(child)
        seq.extend(t for _, t in item.texts[ti:])
        stack.extend(reversed(seq))
    return " ".join(" ".join(out).split())


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.cur = self.root
        self.comments: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_comment(self, data):
        if len(self.comments) < 5:
            self.comments.append(data)

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if self._skip or tag in _SKIP:
            if tag in _SKIP and tag not in _VOID:
                self._skip += 1
            return
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        if self._skip or tag in _SKIP:
            return
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in _SKIP and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        n = self.cur
        while n is not self.root and n.tag != tag:
            n = n.parent
        if n is not self.root:
            self.cur = n.parent

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._skip:
            return
        if data.strip():
            self.cur.texts.append((len(self.cur.children), data))


@dataclass
class Capture:
    rel: str
    size: int
    title: str = ""
    url: str = ""
    saved_local: datetime | None = None  # supporter's PC clock
    saved_utc: datetime | None = None
    answers: list[tuple[str, str]] = field(default_factory=list)
    attached: list[str] = field(default_factory=list)
    confirmation: bool = False
    onsite: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    form_fields: int = 0
    error: str = ""


def _clean_q(text: str) -> str:
    return re.sub(r"\s*\*\s*$", "", " ".join(text.split())).strip()


def _clean_a(text: str) -> str:
    a = " ".join(text.split())
    # Screen-reader text some dropdowns keep: "option X, selected. X"
    a = re.sub(r"^option (.+?), selected\.\s*(?:\1)?\s*", r"\1", a)
    if re.fullmatch(r"(?i)(yes\W*no|no\W*yes)", a):
        return ""  # both options shown, the choice wasn't saved
    return a[:300]


def _control_value(node: Node) -> str:
    if node.tag == "input":
        typ = node.attrs.get("type", "text").lower()
        if typ in _TEXT_INPUTS:
            return node.attrs.get("value", "").strip()
        return ""
    if node.tag == "textarea":
        return text_of(node)
    if node.tag == "select":
        for opt in _walk(node):
            if opt.tag == "option" and "selected" in opt.attrs:
                return " ".join("".join(t for _, t in opt.texts).split()) or opt.attrs.get("value", "")
    return ""


def _is_choice(node: Node | None) -> bool:
    return bool(node and node.tag == "input" and node.attrs.get("type", "").lower() in ("radio", "checkbox"))


def _answers(root: Node) -> list[tuple[str, str]]:
    by_id: dict[str, Node] = {}
    labels: list[Node] = []
    groups: list[Node] = []
    for n in _walk(root):
        if "id" in n.attrs:
            by_id.setdefault(n.attrs["id"], n)
        if n.tag == "label":
            labels.append(n)
        if n.tag == "fieldset" or n.attrs.get("role") in ("radiogroup", "group"):
            groups.append(n)

    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(q: str, a: str) -> None:
        q, a = _clean_q(q), _clean_a(a)
        if 2 <= len(q) <= 300 and q.lower() not in seen and q != a:
            seen.add(q.lower())
            out.append((q[:300], a))

    def option_label(choice: Node) -> str:
        lab = next((l for l in labels if l.attrs.get("for") and l.attrs.get("for") == choice.attrs.get("id")), None)
        if lab is not None:
            return text_of(lab)
        node = choice.parent
        for _ in range(3):  # the option's label often wraps the input a few levels up
            if node is None:
                break
            if node.tag == "label":
                return text_of(node)
            node = node.parent
        return choice.attrs.get("value", "yes")

    # Radio/checkbox groups first: their question is a legend or a label that
    # isn't tied to one input, which the label pass below would misread.
    for group in groups:
        legend = next((c for c in group.children if c.tag == "legend"), None)
        question = text_of(legend) if legend is not None else ""
        if not question and group.attrs.get("aria-labelledby"):
            ref = by_id.get(group.attrs["aria-labelledby"].split()[0])
            question = text_of(ref) if ref is not None else ""
        question = question or group.attrs.get("aria-label", "")
        if not question:
            continue
        chosen = [option_label(c) for c in _walk(group)
                  if _is_choice(c) and ("checked" in c.attrs or c.attrs.get("aria-checked") == "true")]
        add(question, ", ".join(chosen))

    in_legend = {id(n) for g in groups for c in g.children if c.tag == "legend" for n in _walk(c)}
    for label in labels:
        if id(label) in in_legend:
            continue
        control = by_id.get(label.attrs.get("for", ""))
        if control is None:
            control = next((c for c in _walk(label) if c.tag in ("input", "select", "textarea")), None)
        if _is_choice(control):
            continue  # an option of a radio/checkbox group, read below
        question = text_of(label)
        answer = _control_value(control) if control is not None else ""
        if not answer:
            # Custom dropdowns show the chosen answer as text near the label.
            node = label.parent
            for _ in range(4):
                if node is None:
                    break
                others = [l for l in _walk(node) if l.tag == "label" and l is not label]
                if others:
                    break
                pressed = [text_of(n) for n in _walk(node)
                           if n.attrs.get("aria-pressed") == "true" or n.attrs.get("aria-checked") == "true"]
                answer = ", ".join(p for p in pressed if p) or text_of(node, skip=label)
                if answer:
                    break
                node = node.parent
        add(question, answer[:300])

    return out


def read_capture(root: Path, path: Path) -> Capture:
    cap = Capture(rel=path.relative_to(root).as_posix(), size=path.stat().st_size)
    try:
        raw = path.read_text("utf-8", errors="replace")
    except OSError as exc:
        cap.error = f"Can't be read: {exc}"
        return cap
    b = _Builder()
    try:
        b.feed(raw)
        b.close()
    except Exception as exc:  # broken HTML is a finding, not a crash
        cap.error = f"Can't be read: {type(exc).__name__}"
        return cap
    head = "\n".join(b.comments)[:3000]
    if m := _SF_URL.search(head):
        cap.url = m.group(1)
    if m := _SF_DATE.search(head):
        mon, day, year, hh, mm, ss, off = m.groups()
        local = datetime(int(year), _MONTHS.get(mon, 1), int(day), int(hh), int(mm), int(ss))
        sign = 1 if off[0] == "+" else -1
        offset = timedelta(hours=int(off[1:3]), minutes=int(off[3:5])) * sign
        cap.saved_local, cap.saved_utc = local, local - offset
    else:
        cap.saved_local = datetime.fromtimestamp(path.stat().st_mtime)
    cap.title = " ".join(b.title.split())[:200]

    fields = [
        n for n in _walk(b.root)
        if (n.tag == "input" and n.attrs.get("type", "text").lower() in _TEXT_INPUTS | {"file"})
        or n.tag in ("textarea", "select")
    ]
    cap.form_fields = len(fields)
    cap.emails = sorted({
        e.lower() for n in fields if n.tag == "input" for e in _EMAIL.findall(n.attrs.get("value", ""))
    })
    cap.answers = _answers(b.root)
    body = text_of(b.root)
    cap.attached = sorted({f for f in _FILE.findall(body) if re.search(r"[A-Za-z]{2}", f)})[:6]
    cap.confirmation = cap.form_fields <= 1 and bool(_CONFIRM.search(body))
    questions = " ".join(q for q, _ in cap.answers)
    for sentence in re.split(r"(?<=[.!?])\s+|\s{2,}|•", body):
        s = sentence.strip()
        if _ONSITE.search(s) and "?" not in s and s not in questions and 15 < len(s) < 400:
            cap.onsite.append(s[:220])
            if len(cap.onsite) >= 3:
                break
    return cap


def find_captures(root: Path) -> tuple[list[Path], list[Path]]:
    """(HTML captures, other evidence such as screenshots)."""
    pages, other = [], []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            (pages if path.suffix.lower() in (".html", ".htm") else other).append(path)
    return pages, other
