"""Checking and unpacking the uploaded ZIPs safely.

The ZIPs come from supporters' PCs, so they are treated as untrusted: names
that try to leave the folder are refused, sizes are capped while unpacking
(a ZIP can lie about its sizes), and only expected file types are unpacked.
"""

from __future__ import annotations

import os
import re
import time
import zipfile
from pathlib import Path, PurePosixPath

MAX_UPLOAD_BYTES = 90 * 1024 * 1024  # per ZIP; nginx allows 200 MB per request
MAX_ENTRIES = 3000
MAX_UNPACKED_BYTES = 400 * 1024 * 1024
MAX_FILE_BYTES = 80 * 1024 * 1024

KINDS = {
    "resumes": {".pdf", ".docx", ".doc"},
    "snapshots": {".html", ".htm", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf", ".txt"},
}
_JUNK = re.compile(r"(^|/)(__MACOSX|\.DS_Store|Thumbs\.db|desktop\.ini)(/|$)", re.I)
_DATE = re.compile(r"(20\d\d)[-_.](\d\d)[-_.](\d\d)")


class ScanFileError(ValueError):
    """A problem with an uploaded ZIP, worded for the reviewer."""


def _safe_parts(name: str) -> list[str] | None:
    """Path parts of a ZIP entry, or None if the name tries to escape."""
    name = name.replace("\\", "/")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return None
    parts = [p for p in PurePosixPath(name).parts if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    clean = []
    for p in parts:
        p = "".join(ch for ch in p if ch >= " " and ch not in '<>:"|?*')[:180].strip()
        if not p or p in (".", ".."):
            return None
        clean.append(p)
    return clean


def date_in_name(name: str) -> str:
    m = _DATE.search(name or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def check_zip(path: Path, kind: str, original_name: str = "") -> dict:
    """Validate a ZIP without unpacking it. Returns counts for the scan record."""
    label = "resumes ZIP" if kind == "resumes" else "snapshots ZIP"
    if not zipfile.is_zipfile(path):
        raise ScanFileError(f"The {label} isn't a ZIP file.")
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise ScanFileError(f"The {label} can't be opened: {exc}") from None
    if len(infos) > MAX_ENTRIES:
        raise ScanFileError(f"The {label} has {len(infos)} entries; the limit is {MAX_ENTRIES}.")
    total = 0
    usable = ignored = 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise ScanFileError(f"The {label} is password-protected.")
        parts = _safe_parts(info.filename)
        if parts is None:
            raise ScanFileError(f"The {label} contains an unsafe file name: {info.filename[:80]}")
        if info.is_dir() or _JUNK.search(info.filename):
            continue
        total += info.file_size
        if Path(parts[-1]).suffix.lower() in KINDS[kind]:
            usable += 1
        else:
            ignored += 1
    if total > MAX_UNPACKED_BYTES:
        raise ScanFileError(f"The {label} unpacks to more than {MAX_UNPACKED_BYTES // 2**20} MB.")
    if not usable:
        wanted = "PDF or Word files" if kind == "resumes" else "saved web pages (HTML) or images"
        raise ScanFileError(f"The {label} has no {wanted} in it.")
    return {
        "name": original_name or path.name,
        "bytes": path.stat().st_size,
        "files": usable,
        "ignored": ignored,
        "unpacked_bytes": total,
        "date_in_name": date_in_name(original_name),
    }


def extract_zip(path: Path, dest: Path, kind: str) -> list[Path]:
    """Unpack the expected file types into ``dest``. Each file keeps the time
    stored in the ZIP (the supporter's PC clock) as its modification time."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    written_total = 0
    out: list[Path] = []
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            parts = _safe_parts(info.filename)
            if parts is None:
                raise ScanFileError(f"Unsafe file name in the ZIP: {info.filename[:80]}")
            if info.is_dir() or _JUNK.search(info.filename):
                continue
            if Path(parts[-1]).suffix.lower() not in KINDS[kind]:
                continue
            target = root.joinpath(*parts)
            if root not in target.resolve().parents:
                raise ScanFileError(f"Unsafe file name in the ZIP: {info.filename[:80]}")
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with zf.open(info) as src, target.open("wb") as dst:
                while chunk := src.read(256 * 1024):
                    written += len(chunk)
                    written_total += len(chunk)
                    if written > MAX_FILE_BYTES or written_total > MAX_UNPACKED_BYTES:
                        raise ScanFileError("The ZIP unpacks to more than its stated size.")
                    dst.write(chunk)
            try:
                stamp = time.mktime((*info.date_time, 0, 0, -1))
                os.utime(target, (stamp, stamp))
            except (OverflowError, ValueError, OSError):
                pass
            out.append(target)
    return out
