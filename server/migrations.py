"""Lightweight, versioned migration runner (server side).

Greenfield schema is created with ``Base.metadata.create_all``. Additive
schema changes over time are expressed as ordered migration callables recorded
in a ``schema_migrations`` table so each runs exactly once.

Destructive migrations (e.g. license table rebuild) run *before* create_all
so dropped tables are recreated with the current ORM schema.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import Connection, text

from .database import Base, engine


def _account_fingerprint(account: str | None) -> str:
    """Normalize account labels so aliases collapse to one profile.

    ``JaneDoe - US``, ``Jane Doe``, and ``Jane doe`` → ``janedoe``.
    ``Annmarie Cole`` / ``Ann-Marie Cole`` → ``annmariecole``.
    """
    s = (account or "").strip().lower()
    s = re.sub(r"\s*-\s*us\s*$", "", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def _slug_from_account(account: str | None, fingerprint: str) -> str:
    s = (account or "").strip().lower()
    s = re.sub(r"\s*-\s*us\s*$", "", s)
    slug = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return slug or fingerprint or "profile"


def _display_name_from_account(account: str | None) -> str:
    s = (account or "").strip()
    s = re.sub(r"\s*-\s*US\s*$", "", s, flags=re.IGNORECASE).strip()
    return s or "Profile"


def _table_exists(conn: Connection, table: str) -> bool:
    dialect = conn.dialect.name
    try:
        if dialect == "sqlite":
            row = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
                {"t": table},
            ).fetchone()
            return row is not None
        row = conn.execute(text("SELECT to_regclass(:t)"), {"t": table}).fetchone()
        return bool(row and row[0])
    except Exception:
        return False


def _columns(conn: Connection, table: str) -> set[str]:
    dialect = conn.dialect.name
    try:
        if dialect == "sqlite":
            return {
                r[1] for r in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
            }
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = :t"
            ),
            {"t": table},
        ).fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


def _add_profile_scope(conn: Connection) -> None:
    """Add ``profile_id`` to applications/interviews and backfill from assignments.

    Profile-scoped sync redesign: data ownership shifts from per-user to
    per-profile. Existing rows are backfilled so nothing is orphaned:
      (a) ``account`` matches an assigned profile's key/name -> that profile;
      (b) otherwise, if the user has exactly one assigned profile -> that one;
      (c) otherwise left NULL, which the sync layer treats as personal data
          scoped to the owning ``user_id`` (avoids polluting the user's
          server-provisioned profile list with synthetic entries).
    Interviews inherit their linked application's profile when possible.
    """
    for table in ("applications", "interviews"):
        if _table_exists(conn, table) and "profile_id" not in _columns(conn, table):
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN profile_id VARCHAR(36)"))

    if not _table_exists(conn, "applications"):
        # Greenfield: create_all builds the tables/indexes from the ORM models.
        return

    has_profiles = _table_exists(conn, "profiles") and _table_exists(
        conn, "profile_assignments"
    )

    if has_profiles and "profile_id" in _columns(conn, "applications"):
        user_ids: set[str] = set()
        for (uid,) in conn.execute(
            text("SELECT DISTINCT user_id FROM applications WHERE profile_id IS NULL")
        ):
            if uid:
                user_ids.add(uid)

        for uid in user_ids:
            assigned = conn.execute(
                text(
                    "SELECT p.id, p.key, p.name FROM profiles p "
                    "JOIN profile_assignments pa ON pa.profile_id = p.id "
                    "WHERE pa.user_id = :u"
                ),
                {"u": uid},
            ).fetchall()
            if not assigned:
                continue

            label_to_pid: dict[str, str] = {}
            for pid, key, name in assigned:
                for label in (key, name):
                    norm = (label or "").strip().lower()
                    if norm:
                        label_to_pid.setdefault(norm, pid)

            # (a) account -> profile by key/name
            accounts = [
                (r[0] or "")
                for r in conn.execute(
                    text(
                        "SELECT DISTINCT account FROM applications "
                        "WHERE user_id = :u AND profile_id IS NULL"
                    ),
                    {"u": uid},
                )
            ]
            for acct in accounts:
                pid = label_to_pid.get(acct.strip().lower())
                if not pid:
                    continue
                conn.execute(
                    text(
                        "UPDATE applications SET profile_id = :p "
                        "WHERE user_id = :u AND profile_id IS NULL AND account = :a"
                    ),
                    {"p": pid, "u": uid, "a": acct},
                )

            # (b) sole assigned profile for whatever is left
            if len(assigned) == 1:
                conn.execute(
                    text(
                        "UPDATE applications SET profile_id = :p "
                        "WHERE user_id = :u AND profile_id IS NULL"
                    ),
                    {"p": assigned[0][0], "u": uid},
                )

    # Interviews: inherit the parent application's profile when linked.
    if "profile_id" in _columns(conn, "interviews"):
        conn.execute(
            text(
                "UPDATE interviews SET profile_id = ("
                "  SELECT a.profile_id FROM applications a "
                "  WHERE a.id = interviews.application_id"
                ") "
                "WHERE profile_id IS NULL AND application_id IS NOT NULL"
            )
        )
        if has_profiles:
            iv_user_ids: set[str] = set()
            for (uid,) in conn.execute(
                text("SELECT DISTINCT user_id FROM interviews WHERE profile_id IS NULL")
            ):
                if uid:
                    iv_user_ids.add(uid)
            for uid in iv_user_ids:
                assigned = conn.execute(
                    text(
                        "SELECT p.id FROM profiles p "
                        "JOIN profile_assignments pa ON pa.profile_id = p.id "
                        "WHERE pa.user_id = :u"
                    ),
                    {"u": uid},
                ).fetchall()
                if len(assigned) == 1:
                    conn.execute(
                        text(
                            "UPDATE interviews SET profile_id = :p "
                            "WHERE user_id = :u AND profile_id IS NULL"
                        ),
                        {"p": assigned[0][0], "u": uid},
                    )

    # Keyset indexes for profile-scoped pulls (existing DBs; create_all covers
    # greenfield via the ORM __table_args__).
    for table, index in (
        ("applications", "ix_applications_profile_keyset"),
        ("interviews", "ix_interviews_profile_keyset"),
    ):
        if _table_exists(conn, table):
            conn.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS {index} "
                    f"ON {table} (profile_id, last_modified, id)"
                )
            )


def _rebuild_license_signed_lease(conn: Connection) -> None:
    """Replace pre-redesign license tables (plaintext keys) with signed-lease schema.

    Pre-production breaking change: existing license rows are discarded.
    """
    dialect = conn.dialect.name
    cols: set[str] = set()
    try:
        if dialect == "sqlite":
            cols = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(licenses)")).fetchall()
            }
        else:
            rows = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'licenses'"
                )
            ).fetchall()
            cols = {row[0] for row in rows}
    except Exception:
        cols = set()
    if not cols:
        # Table missing — create_all will create it.
        return
    if "key_hash" in cols and "license_key" not in cols:
        # Already on the new schema.
        return
    for table in ("license_audits", "license_seats", "licenses"):
        conn.execute(text(f"DROP TABLE IF EXISTS {table}"))


def _add_application_dedup_index(conn: Connection) -> None:
    """Index the lookup that merges concurrent duplicate creates in a profile."""
    if _table_exists(conn, "applications"):
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_applications_profile_company "
                "ON applications (profile_id, company_name)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_applications_profile_dedup "
                "ON applications (profile_id, job_link)"
            )
        )


def _add_application_job_link_dedup_index(conn: Connection) -> None:
    """Index job_link lookups used when merging concurrent shared-profile creates."""
    if _table_exists(conn, "applications"):
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_applications_profile_dedup "
                "ON applications (profile_id, job_link)"
            )
        )


def _add_server_modified(conn: Connection) -> None:
    """Add the server-side pull cursor column and repoint the keyset indexes.

    Pulls previously filtered on the client-supplied ``last_modified``, so a row
    written by one account with an older edit time than another account's cursor
    was never delivered. ``server_modified`` is this server's write time, which
    always moves forward relative to any cursor it handed out.
    """
    for table in ("applications", "interviews"):
        if not _table_exists(conn, table):
            continue
        if "server_modified" not in _columns(conn, table):
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN server_modified TIMESTAMP"))
        conn.execute(
            text(
                f"UPDATE {table} SET server_modified = last_modified "
                "WHERE server_modified IS NULL"
            )
        )
        index = f"ix_{table}_profile_keyset"
        conn.execute(text(f"DROP INDEX IF EXISTS {index}"))
        conn.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS {index} "
                f"ON {table} (profile_id, server_modified, id)"
            )
        )
        conn.execute(
            text(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_server_modified "
                f"ON {table} (server_modified)"
            )
        )


def _repair_personal_apps_by_account(conn: Connection) -> None:
    """Attach orphaned personal apps whose ``account`` matches an assigned profile.

    Legacy / fail-open clients pushed shared work with empty ``profile_key`` and
    only an account display name (e.g. ``Thomas Wu``). Those rows landed with
    ``profile_id IS NULL`` and were invisible to peers. Match account against
    assigned profile key/name (case-insensitive), set ``profile_id``, and bump
    ``server_modified`` so pull cursors deliver the repaired rows.
    """
    if not _table_exists(conn, "applications"):
        return
    if "profile_id" not in _columns(conn, "applications"):
        return
    if not (
        _table_exists(conn, "profiles") and _table_exists(conn, "profile_assignments")
    ):
        return

    has_server_modified = "server_modified" in _columns(conn, "applications")
    now = datetime.utcnow()

    user_ids: set[str] = set()
    for (uid,) in conn.execute(
        text("SELECT DISTINCT user_id FROM applications WHERE profile_id IS NULL")
    ):
        if uid:
            user_ids.add(uid)

    for uid in user_ids:
        assigned = conn.execute(
            text(
                "SELECT p.id, p.key, p.name FROM profiles p "
                "JOIN profile_assignments pa ON pa.profile_id = p.id "
                "WHERE pa.user_id = :u"
            ),
            {"u": uid},
        ).fetchall()
        if not assigned:
            continue

        label_to_pid: dict[str, str] = {}
        for pid, key, name in assigned:
            for label in (key, name):
                norm = (label or "").strip().lower()
                if norm:
                    label_to_pid.setdefault(norm, pid)

        accounts = [
            (r[0] or "")
            for r in conn.execute(
                text(
                    "SELECT DISTINCT account FROM applications "
                    "WHERE user_id = :u AND profile_id IS NULL"
                ),
                {"u": uid},
            )
        ]
        for acct in accounts:
            pid = label_to_pid.get(acct.strip().lower())
            if not pid:
                continue
            if has_server_modified:
                conn.execute(
                    text(
                        "UPDATE applications SET profile_id = :p, "
                        "server_modified = :ts "
                        "WHERE user_id = :u AND profile_id IS NULL AND account = :a"
                    ),
                    {"p": pid, "u": uid, "a": acct, "ts": now},
                )
            else:
                conn.execute(
                    text(
                        "UPDATE applications SET profile_id = :p "
                        "WHERE user_id = :u AND profile_id IS NULL AND account = :a"
                    ),
                    {"p": pid, "u": uid, "a": acct},
                )

    # Interviews: inherit the parent application's profile when linked.
    if (
        _table_exists(conn, "interviews")
        and "profile_id" in _columns(conn, "interviews")
    ):
        iv_has_sm = "server_modified" in _columns(conn, "interviews")
        if iv_has_sm:
            conn.execute(
                text(
                    "UPDATE interviews SET profile_id = ("
                    "  SELECT a.profile_id FROM applications a "
                    "  WHERE a.id = interviews.application_id"
                    "), server_modified = :ts "
                    "WHERE profile_id IS NULL AND application_id IS NOT NULL "
                    "AND EXISTS ("
                    "  SELECT 1 FROM applications a "
                    "  WHERE a.id = interviews.application_id "
                    "  AND a.profile_id IS NOT NULL"
                    ")"
                ),
                {"ts": now},
            )
        else:
            conn.execute(
                text(
                    "UPDATE interviews SET profile_id = ("
                    "  SELECT a.profile_id FROM applications a "
                    "  WHERE a.id = interviews.application_id"
                    ") "
                    "WHERE profile_id IS NULL AND application_id IS NOT NULL "
                    "AND EXISTS ("
                    "  SELECT 1 FROM applications a "
                    "  WHERE a.id = interviews.application_id "
                    "  AND a.profile_id IS NOT NULL"
                    ")"
                )
            )


def _rename_user_role_to_member(conn: Connection) -> None:
    """Rename legacy account role ``user`` → ``member``."""
    if not _table_exists(conn, "users"):
        return
    conn.execute(text("UPDATE users SET role = 'member' WHERE role = 'user'"))


def _remap_work_plan_week_to_saturday(conn: Connection) -> None:
    """Remap weekly period_start to Saturday (Sat→Fri weeks).

    Earlier builds keyed weeks on Sunday, then briefly Friday. Team weeks are
    Saturday through Friday, so existing week rows must move to the Saturday
    that starts their week or they disappear from the current board.
    """
    from datetime import date, timedelta

    if not _table_exists(conn, "work_plan_items"):
        return
    if "period_start" not in _columns(conn, "work_plan_items"):
        return

    rows = conn.execute(
        text(
            "SELECT id, period_start FROM work_plan_items "
            "WHERE horizon = 'week' AND period_start IS NOT NULL"
        )
    ).fetchall()

    def to_saturday(raw: str) -> str | None:
        try:
            d = date.fromisoformat(str(raw).strip()[:10])
        except ValueError:
            return None
        # Python: Mon=0 … Fri=4 Sat=5 Sun=6
        if d.weekday() == 5:
            return d.isoformat()
        if d.weekday() == 6:  # Sunday week → prior Saturday
            return (d - timedelta(days=1)).isoformat()
        if d.weekday() == 4:  # Friday week → following Saturday
            return (d + timedelta(days=1)).isoformat()
        # Any other weekday: snap to the Saturday that starts this Sat–Fri week
        return (d - timedelta(days=(d.weekday() - 5) % 7)).isoformat()

    for item_id, period_start in rows:
        new_start = to_saturday(period_start or "")
        if not new_start or new_start == period_start:
            continue
        conn.execute(
            text(
                "UPDATE work_plan_items SET period_start = :p WHERE id = :i"
            ),
            {"p": new_start, "i": item_id},
        )


def _add_user_avatar_url(conn: Connection) -> None:
    """Add optional ``avatar_url`` on users for Stage profile photos."""
    if not _table_exists(conn, "users"):
        return
    if "avatar_url" in _columns(conn, "users"):
        return
    conn.execute(text("ALTER TABLE users ADD COLUMN avatar_url VARCHAR(512)"))


def _add_member_avatar_overrides(conn: Connection) -> None:
    id_column = (
        "id SERIAL PRIMARY KEY"
        if conn.dialect.name == "postgresql"
        else "id INTEGER PRIMARY KEY AUTOINCREMENT"
    )
    conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS member_avatar_overrides ("
            f"{id_column}, member_id VARCHAR(36) NOT NULL, "
            "supporter_id VARCHAR(36) NOT NULL, avatar_url VARCHAR(512) NOT NULL, "
            "updated_at TIMESTAMP, CONSTRAINT uq_member_supporter_avatar "
            "UNIQUE (member_id, supporter_id), FOREIGN KEY(member_id) REFERENCES users(id), "
            "FOREIGN KEY(supporter_id) REFERENCES users(id))"
        )
    )
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_member_avatar_member ON member_avatar_overrides (member_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_member_avatar_supporter ON member_avatar_overrides (supporter_id)"))


def _promote_supporter_avatar_overrides(conn: Connection) -> None:
    """Make existing member-uploaded supporter avatars visible site-wide."""
    if (
        not _table_exists(conn, "member_avatar_overrides")
        or not _table_exists(conn, "users")
        or "avatar_url" not in _columns(conn, "users")
    ):
        return
    conn.execute(
        text(
            "UPDATE users SET avatar_url = ("
            "SELECT mao.avatar_url FROM member_avatar_overrides mao "
            "WHERE mao.supporter_id = users.id "
            "ORDER BY mao.updated_at DESC, mao.id DESC LIMIT 1"
            ") WHERE users.id IN (SELECT supporter_id FROM member_avatar_overrides)"
        )
    )

def _purge_foreign_personal_scope(conn: Connection) -> None:
    """Soft-delete personal rows that are outside an assigned user's profiles.

    Fail-open sync accepted local multi-account dumps as ``profile_id IS NULL``
    (e.g. JaneDoe / John Roe apps under an operator assigned only to
    Thomas/Christian). Those rows inflate editor history far beyond assigned
    work. After account→assigned repair, remaining personal apps/interviews
    for users who have profile assignments are tombstoned.
    """
    if not _table_exists(conn, "profile_assignments"):
        return
    now = datetime.utcnow()

    if (
        _table_exists(conn, "applications")
        and "profile_id" in _columns(conn, "applications")
        and "deleted_at" in _columns(conn, "applications")
    ):
        has_sm = "server_modified" in _columns(conn, "applications")
        if has_sm:
            conn.execute(
                text(
                    "UPDATE applications SET deleted_at = :ts, server_modified = :ts "
                    "WHERE profile_id IS NULL AND deleted_at IS NULL "
                    "AND user_id IN (SELECT DISTINCT user_id FROM profile_assignments)"
                ),
                {"ts": now},
            )
        else:
            conn.execute(
                text(
                    "UPDATE applications SET deleted_at = :ts "
                    "WHERE profile_id IS NULL AND deleted_at IS NULL "
                    "AND user_id IN (SELECT DISTINCT user_id FROM profile_assignments)"
                ),
                {"ts": now},
            )

    if (
        _table_exists(conn, "interviews")
        and "profile_id" in _columns(conn, "interviews")
        and "deleted_at" in _columns(conn, "interviews")
    ):
        has_sm = "server_modified" in _columns(conn, "interviews")
        if has_sm:
            conn.execute(
                text(
                    "UPDATE interviews SET deleted_at = :ts, server_modified = :ts "
                    "WHERE profile_id IS NULL AND deleted_at IS NULL "
                    "AND user_id IN (SELECT DISTINCT user_id FROM profile_assignments)"
                ),
                {"ts": now},
            )
        else:
            conn.execute(
                text(
                    "UPDATE interviews SET deleted_at = :ts "
                    "WHERE profile_id IS NULL AND deleted_at IS NULL "
                    "AND user_id IN (SELECT DISTINCT user_id FROM profile_assignments)"
                ),
                {"ts": now},
            )


def _repair_and_purge_personal_scope(conn: Connection) -> None:
    """Attach assigned-account orphans, then purge leftover personal dumps.

    Superseded by ``_recover_personal_into_profiles`` (0011): personal scope was
    a client bug, not a real data model. Kept for migration history only.
    """
    _repair_personal_apps_by_account(conn)
    _purge_foreign_personal_scope(conn)


def _recover_personal_into_profiles(conn: Connection) -> None:
    """Undelete personal rows and attach every one to a real ``profile_id``.

    Personal (``profile_id IS NULL``) rows came from a client bug that synced
    local account dumps without a profile key. Recover tombstones, match
    ``account`` to an existing profile (or create one), assign that profile to
    the owning user, and clear ``deleted_at`` so member history is preserved.
    """
    if not _table_exists(conn, "applications"):
        return
    if "profile_id" not in _columns(conn, "applications"):
        return
    if not (
        _table_exists(conn, "profiles") and _table_exists(conn, "profile_assignments")
    ):
        return

    now = datetime.utcnow()
    has_sm = "server_modified" in _columns(conn, "applications")

    # fingerprint -> profile_id for existing profiles (key + name aliases).
    fp_to_pid: dict[str, str] = {}
    key_to_pid: dict[str, str] = {}
    for pid, key, name in conn.execute(
        text("SELECT id, key, name FROM profiles")
    ).fetchall():
        key_to_pid[(key or "").strip().lower()] = pid
        for label in (key, name):
            fp = _account_fingerprint(label)
            if fp:
                fp_to_pid.setdefault(fp, pid)

    # Distinct (user_id, account) still missing a profile.
    pairs = conn.execute(
        text(
            "SELECT DISTINCT user_id, account FROM applications "
            "WHERE profile_id IS NULL"
        )
    ).fetchall()

    # Prefer nicer display names when several aliases share a fingerprint.
    fp_best_name: dict[str, str] = {}
    fp_users: dict[str, set[str]] = {}
    pair_fp: list[tuple[str, str | None, str]] = []
    for uid, acct in pairs:
        if not uid:
            continue
        fp = _account_fingerprint(acct) or "default"
        pair_fp.append((uid, acct, fp))
        fp_users.setdefault(fp, set()).add(uid)
        display = _display_name_from_account(acct)
        prev = fp_best_name.get(fp)
        if prev is None or (
            (" " in display and " " not in prev)
            or (len(display) > len(prev) and "-" not in display)
        ):
            fp_best_name[fp] = display

    # Ensure a profile exists for every fingerprint.
    for fp, users in fp_users.items():
        pid = fp_to_pid.get(fp)
        if pid is None:
            display = fp_best_name.get(fp) or fp
            base_key = _slug_from_account(display, fp)
            key = base_key
            n = 2
            while key.lower() in key_to_pid:
                key = f"{base_key}-{n}"
                n += 1
            pid = str(uuid.uuid4())
            conn.execute(
                text(
                    "INSERT INTO profiles (id, key, name, email, phone, location, "
                    "linkedin, university_name, university_location, degree, "
                    "education_period, default_save_path, resume_filename, "
                    "cover_letter_filename, updated_at, created_at) "
                    "VALUES (:id, :key, :name, '', '', '', '', '', '', '', '', '', "
                    "'resume', 'cover_letter', :ts, :ts)"
                ),
                {"id": pid, "key": key, "name": display, "ts": now},
            )
            key_to_pid[key.lower()] = pid
            fp_to_pid[fp] = pid
            # Only auto-assign when we create a brand-new profile from orphan
            # account labels. Existing shared profiles (e.g. client-b) must not
            # gain accidental assignees — that duplicated another member's work.
            for uid in users:
                exists = conn.execute(
                    text(
                        "SELECT 1 FROM profile_assignments "
                        "WHERE user_id = :u AND profile_id = :p"
                    ),
                    {"u": uid, "p": pid},
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    text(
                        "INSERT INTO profile_assignments "
                        "(user_id, profile_id, assigned_at) "
                        "VALUES (:u, :p, :ts)"
                    ),
                    {"u": uid, "p": pid, "ts": now},
                )

    # Attach + undelete every personal application for these accounts.
    for uid, acct, fp in pair_fp:
        pid = fp_to_pid.get(fp)
        if not pid:
            continue
        if acct is None:
            acct_clause = "account IS NULL"
            params: dict = {"p": pid, "u": uid, "ts": now}
        else:
            acct_clause = "account = :a"
            params = {"p": pid, "u": uid, "a": acct, "ts": now}
        if has_sm:
            conn.execute(
                text(
                    "UPDATE applications SET profile_id = :p, deleted_at = NULL, "
                    f"server_modified = :ts "
                    f"WHERE user_id = :u AND profile_id IS NULL AND {acct_clause}"
                ),
                params,
            )
        else:
            conn.execute(
                text(
                    "UPDATE applications SET profile_id = :p, deleted_at = NULL "
                    f"WHERE user_id = :u AND profile_id IS NULL AND {acct_clause}"
                ),
                params,
            )

    # Interviews: inherit linked application profile and undelete.
    if (
        _table_exists(conn, "interviews")
        and "profile_id" in _columns(conn, "interviews")
    ):
        iv_has_sm = "server_modified" in _columns(conn, "interviews")
        if iv_has_sm:
            conn.execute(
                text(
                    "UPDATE interviews SET profile_id = ("
                    "  SELECT a.profile_id FROM applications a "
                    "  WHERE a.id = interviews.application_id"
                    "), deleted_at = NULL, server_modified = :ts "
                    "WHERE profile_id IS NULL AND application_id IS NOT NULL "
                    "AND EXISTS ("
                    "  SELECT 1 FROM applications a "
                    "  WHERE a.id = interviews.application_id "
                    "  AND a.profile_id IS NOT NULL"
                    ")"
                ),
                {"ts": now},
            )
        else:
            conn.execute(
                text(
                    "UPDATE interviews SET profile_id = ("
                    "  SELECT a.profile_id FROM applications a "
                    "  WHERE a.id = interviews.application_id"
                    "), deleted_at = NULL "
                    "WHERE profile_id IS NULL AND application_id IS NOT NULL "
                    "AND EXISTS ("
                    "  SELECT 1 FROM applications a "
                    "  WHERE a.id = interviews.application_id "
                    "  AND a.profile_id IS NOT NULL"
                    ")"
                )
            )

        # Orphans with no linked application: attach to the owner's default
        # profile if present, else any assigned profile.
        orphan_users = [
            r[0]
            for r in conn.execute(
                text("SELECT DISTINCT user_id FROM interviews WHERE profile_id IS NULL")
            ).fetchall()
            if r[0]
        ]
        for uid in orphan_users:
            row = conn.execute(
                text(
                    "SELECT p.id FROM profiles p "
                    "JOIN profile_assignments pa ON pa.profile_id = p.id "
                    "WHERE pa.user_id = :u "
                    "ORDER BY CASE WHEN lower(p.key) = 'default' THEN 0 ELSE 1 END, p.key "
                    "LIMIT 1"
                ),
                {"u": uid},
            ).fetchone()
            if not row:
                continue
            pid = row[0]
            if iv_has_sm:
                conn.execute(
                    text(
                        "UPDATE interviews SET profile_id = :p, deleted_at = NULL, "
                        "server_modified = :ts "
                        "WHERE user_id = :u AND profile_id IS NULL"
                    ),
                    {"p": pid, "u": uid, "ts": now},
                )
            else:
                conn.execute(
                    text(
                        "UPDATE interviews SET profile_id = :p, deleted_at = NULL "
                        "WHERE user_id = :u AND profile_id IS NULL"
                    ),
                    {"p": pid, "u": uid},
                )


def _fix_member_a_member_b_attribution(conn: Connection) -> None:
    """Move Member B's pre-start history off Member A; drop Client B dupes.

    Member A (supporter) bulk-synced Member B's local history on 2026-07-27 and
    recovery incorrectly assigned him to ``client-b``. Clients stay healthy:
    soft-deletes bump ``server_modified`` so pulls deliver tombstones; both
    users keep ``client-a``; only the accidental ``client-b`` assignment on
    Member A is removed after his Client B rows are tombstoned.
    """
    if not (
        _table_exists(conn, "users")
        and _table_exists(conn, "profiles")
        and _table_exists(conn, "applications")
    ):
        return

    member_a = conn.execute(
        text("SELECT id FROM users WHERE email = :e"),
        {"e": "member-a@example.com"},
    ).fetchone()
    member_b = conn.execute(
        text("SELECT id FROM users WHERE email = :e"),
        {"e": "member-b@example.com"},
    ).fetchone()
    client_a = conn.execute(
        text("SELECT id FROM profiles WHERE key = :k"),
        {"k": "client-a"},
    ).fetchone()
    client_b = conn.execute(
        text("SELECT id FROM profiles WHERE key = :k"),
        {"k": "client-b"},
    ).fetchone()
    if not (member_a and member_b and client_a):
        return

    member_a_id, member_b_id, client_a_id = member_a[0], member_b[0], client_a[0]
    client_b_id = client_b[0] if client_b else None
    now = datetime.utcnow()
    # Member A's account created 2026-07-24; older applied_date = prior work.
    cutoff = datetime(2026, 7, 24)
    has_sm = "server_modified" in _columns(conn, "applications")

    # 1) Client A history before Member A started → attribute to Member B.
    if has_sm:
        conn.execute(
            text(
                "UPDATE applications SET user_id = :member_b, server_modified = :ts "
                "WHERE profile_id = :client_a AND user_id = :member_a "
                "AND deleted_at IS NULL AND applied_date IS NOT NULL "
                "AND applied_date < :cutoff"
            ),
            {
                "member_b": member_b_id,
                "member_a": member_a_id,
                "client_a": client_a_id,
                "cutoff": cutoff,
                "ts": now,
            },
        )
    else:
        conn.execute(
            text(
                "UPDATE applications SET user_id = :member_b "
                "WHERE profile_id = :client_a AND user_id = :member_a "
                "AND deleted_at IS NULL AND applied_date IS NOT NULL "
                "AND applied_date < :cutoff"
            ),
            {
                "member_b": member_b_id,
                "member_a": member_a_id,
                "client_a": client_a_id,
                "cutoff": cutoff,
            },
        )

    if not client_b_id:
        return

    # 2) Unique Client B rows under Member A (no peer link match) → Member B.
    #    Duplicate link rows are soft-deleted in step 3.
    if has_sm:
        conn.execute(
            text(
                "UPDATE applications SET user_id = :member_b, server_modified = :ts "
                "WHERE profile_id = :client_b AND user_id = :member_a AND deleted_at IS NULL "
                "AND ("
                "  coalesce(job_link, '') = '' "
                "  OR NOT EXISTS ("
                "    SELECT 1 FROM applications b "
                "    WHERE b.profile_id = applications.profile_id "
                "    AND b.deleted_at IS NULL AND b.user_id <> applications.user_id "
                "    AND lower(rtrim(b.job_link, '/')) "
                "      = lower(rtrim(applications.job_link, '/'))"
                "  )"
                ")"
            ),
            {"member_b": member_b_id, "member_a": member_a_id, "client_b": client_b_id, "ts": now},
        )
    else:
        conn.execute(
            text(
                "UPDATE applications SET user_id = :member_b "
                "WHERE profile_id = :client_b AND user_id = :member_a AND deleted_at IS NULL "
                "AND ("
                "  coalesce(job_link, '') = '' "
                "  OR NOT EXISTS ("
                "    SELECT 1 FROM applications b "
                "    WHERE b.profile_id = applications.profile_id "
                "    AND b.deleted_at IS NULL AND b.user_id <> applications.user_id "
                "    AND lower(rtrim(b.job_link, '/')) "
                "      = lower(rtrim(applications.job_link, '/'))"
                "  )"
                ")"
            ),
            {"member_b": member_b_id, "member_a": member_a_id, "client_b": client_b_id},
        )

    # 3) Soft-delete remaining Member A Client B apps (duplicates of Member B).
    if has_sm:
        conn.execute(
            text(
                "UPDATE applications SET deleted_at = :ts, server_modified = :ts "
                "WHERE profile_id = :client_b AND user_id = :member_a AND deleted_at IS NULL"
            ),
            {"client_b": client_b_id, "member_a": member_a_id, "ts": now},
        )
    else:
        conn.execute(
            text(
                "UPDATE applications SET deleted_at = :ts "
                "WHERE profile_id = :client_b AND user_id = :member_a AND deleted_at IS NULL"
            ),
            {"client_b": client_b_id, "member_a": member_a_id, "ts": now},
        )

    # 4) Soft-delete Member A interviews on Client B (pre-start / wrong scope).
    if (
        _table_exists(conn, "interviews")
        and "profile_id" in _columns(conn, "interviews")
        and "deleted_at" in _columns(conn, "interviews")
    ):
        iv_sm = "server_modified" in _columns(conn, "interviews")
        if iv_sm:
            conn.execute(
                text(
                    "UPDATE interviews SET deleted_at = :ts, server_modified = :ts "
                    "WHERE profile_id = :client_b AND user_id = :member_a AND deleted_at IS NULL"
                ),
                {"client_b": client_b_id, "member_a": member_a_id, "ts": now},
            )
        else:
            conn.execute(
                text(
                    "UPDATE interviews SET deleted_at = :ts "
                    "WHERE profile_id = :client_b AND user_id = :member_a AND deleted_at IS NULL"
                ),
                {"client_b": client_b_id, "member_a": member_a_id, "ts": now},
            )

    # 5) Remove accidental Client B assignment from Member A (keep client-a).
    if _table_exists(conn, "profile_assignments"):
        conn.execute(
            text(
                "DELETE FROM profile_assignments "
                "WHERE user_id = :member_a AND profile_id = :client_b"
            ),
            {"member_a": member_a_id, "client_b": client_b_id},
        )


def _purge_orphan_profile_tracking(conn: Connection) -> None:
    """Hard-delete apps/interviews whose ``profile_id`` no longer exists.

    Admin profile deletes used to drop the profile row while leaving tracking
    rows behind (orphans). Wipe those so Performance and sync stay consistent.
    """
    if not _table_exists(conn, "profiles"):
        return
    if _table_exists(conn, "interviews") and "profile_id" in _columns(conn, "interviews"):
        # Linked interviews whose parent app is orphaned.
        if (
            _table_exists(conn, "applications")
            and "application_id" in _columns(conn, "interviews")
        ):
            conn.execute(
                text(
                    "DELETE FROM interviews WHERE application_id IS NOT NULL "
                    "AND application_id IN ("
                    "  SELECT a.id FROM applications a "
                    "  WHERE a.profile_id IS NOT NULL "
                    "  AND a.profile_id NOT IN (SELECT id FROM profiles)"
                    ")"
                )
            )
        conn.execute(
            text(
                "DELETE FROM interviews WHERE profile_id IS NOT NULL "
                "AND profile_id NOT IN (SELECT id FROM profiles)"
            )
        )
    if _table_exists(conn, "applications") and "profile_id" in _columns(
        conn, "applications"
    ):
        conn.execute(
            text(
                "DELETE FROM applications WHERE profile_id IS NOT NULL "
                "AND profile_id NOT IN (SELECT id FROM profiles)"
            )
        )


def _reset_default_document_filenames(conn: Connection) -> None:
    """Clear resume/cover-letter filenames stuck at the old blanket default.

    ``resume_filename``/``cover_letter_filename`` used to default to the
    literal strings ``"resume"``/``"cover_letter"`` at the ORM level, so
    every profile ended up with identical document filenames instead of
    falling back to the per-profile ``FirstName_LastName`` name the desktop
    client derives (``effective_document_filenames`` in
    ``gui/profile/store.py``). Clearing rows still at that literal default
    back to ``""`` lets the per-profile derivation apply again; anyone who
    already customized a filename keeps their value untouched.
    """
    if not _table_exists(conn, "profiles"):
        return
    cols = _columns(conn, "profiles")
    if "resume_filename" in cols:
        conn.execute(text("UPDATE profiles SET resume_filename = '' WHERE resume_filename = 'resume'"))
    if "cover_letter_filename" in cols:
        conn.execute(
            text(
                "UPDATE profiles SET cover_letter_filename = '' "
                "WHERE cover_letter_filename = 'cover_letter'"
            )
        )


def _add_sync_revisions_and_attribution(conn: Connection) -> None:
    """Add optimistic revisions and separate creator/editor attribution."""
    for table in ("applications", "interviews"):
        if not _table_exists(conn, table):
            continue
        cols = _columns(conn, table)
        if "creator_user_id" not in cols:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN creator_user_id VARCHAR(36)"))
        if "last_editor_user_id" not in cols:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN last_editor_user_id VARCHAR(36)"))
        if "revision" not in cols:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN revision INTEGER DEFAULT 1"))
        conn.execute(
            text(
                f"UPDATE {table} SET creator_user_id = user_id "
                "WHERE creator_user_id IS NULL"
            )
        )
        conn.execute(
            text(
                f"UPDATE {table} SET last_editor_user_id = user_id "
                "WHERE last_editor_user_id IS NULL"
            )
        )
        conn.execute(text(f"UPDATE {table} SET revision = 1 WHERE revision IS NULL"))


def _backfill_missing_attribution(conn: Connection) -> None:
    """Re-run the creator/editor backfill from 0017.

    0017 backfilled once, but rows inserted afterwards by an API build that
    predated the attribution columns landed with NULLs anyway (12 applications
    arrived that way during one deploy window). A NULL creator makes the desktop
    client's dedupe heal skip its attribution check and adopt a teammate's
    record over a brand-new local application, so leaving them is not cosmetic.
    ``user_id`` is NOT NULL and holds the last writer, so it is the right value.
    """
    for table in ("applications", "interviews"):
        if not _table_exists(conn, table):
            continue
        cols = _columns(conn, table)
        if "creator_user_id" in cols:
            conn.execute(
                text(
                    f"UPDATE {table} SET creator_user_id = user_id "
                    "WHERE creator_user_id IS NULL"
                )
            )
        if "last_editor_user_id" in cols:
            conn.execute(
                text(
                    f"UPDATE {table} SET last_editor_user_id = user_id "
                    "WHERE last_editor_user_id IS NULL"
                )
            )


def _relink_orphaned_interviews(conn: Connection) -> None:
    """Re-attach live interviews the server dropped the parent FK from.

    Before the push handler learned to reject an unresolvable ``application_id``,
    it stored NULL and accepted the row anyway; the client marks accepted rows
    clean, so the link was never re-sent. Repair only where the answer is
    unambiguous: exactly one live application on the *same profile and user*
    whose company name matches the interview title's leading company segment
    (the desktop titles interviews ``"<Company> - Interview"``). Anything less
    certain is left alone for a human.
    """
    if not (_table_exists(conn, "interviews") and _table_exists(conn, "applications")):
        return
    orphans = conn.execute(
        text(
            "SELECT id, user_id, profile_id, title FROM interviews "
            "WHERE application_id IS NULL AND deleted_at IS NULL "
            "AND profile_id IS NOT NULL AND title IS NOT NULL"
        )
    ).all()
    for iv_id, user_id, profile_id, title in orphans:
        company = str(title or "").split(" - ")[0].strip()
        if not company:
            continue
        matches = conn.execute(
            text(
                "SELECT id FROM applications "
                "WHERE deleted_at IS NULL AND profile_id = :pid AND user_id = :uid "
                "AND LOWER(TRIM(company_name)) = LOWER(:company)"
            ),
            {"pid": profile_id, "uid": user_id, "company": company},
        ).all()
        if len(matches) != 1:
            continue
        conn.execute(
            text("UPDATE interviews SET application_id = :aid WHERE id = :iid"),
            {"aid": matches[0][0], "iid": iv_id},
        )


def _add_user_timezone(conn: Connection) -> None:
    """Add an admin-settable IANA timezone per user, default ``UTC``.

    The Colleagues tab's activity timeline bucketed a supporter's application
    activity by whichever member's browser happened to be viewing it, so a
    supporter's whole day of work could land under the viewer's "yesterday".
    An admin-set timezone lets the timeline bucket by the supporter's own
    local day/hour instead.
    """
    if not _table_exists(conn, "users"):
        return
    if "timezone" in _columns(conn, "users"):
        return
    conn.execute(text("ALTER TABLE users ADD COLUMN timezone VARCHAR(64) DEFAULT 'UTC'"))
    conn.execute(text("UPDATE users SET timezone = 'UTC' WHERE timezone IS NULL"))


def _add_sync_audit_detail_counters(conn: Connection) -> None:
    """Split ``sync_audits``' three rejection buckets into their real reasons.

    ``rejected_not_assigned`` conflates a genuine cross-account push with a row
    that has no profile link at all ("unscoped") - a client-side bug, not a
    permissions issue. ``version_conflicts`` conflates the timestamp-based
    check with the separate optimistic-concurrency revision check.
    ``other_rejected`` conflates three unrelated failure modes (a dropped
    interview<->application link, a real interview time clash, and a hard
    save error) with no way to tell which occurred. This adds a finer counter
    for each specific reason, additive alongside the existing ones so nothing
    that already reads the old columns breaks; the push handler now increments
    both the old aggregate and the new specific counter at each site.
    """
    if not _table_exists(conn, "sync_audits"):
        return
    cols = _columns(conn, "sync_audits")
    new_columns = (
        "rejected_unscoped",
        "revision_conflicts",
        "rejected_missing_parent",
        "rejected_time_conflict",
        "rejected_error",
    )
    for column in new_columns:
        if column not in cols:
            conn.execute(
                text(f"ALTER TABLE sync_audits ADD COLUMN {column} INTEGER DEFAULT 0")
            )
    conn.execute(
        text(
            "UPDATE sync_audits SET "
            + ", ".join(f"{column} = 0" for column in new_columns)
            + " WHERE "
            + " OR ".join(f"{column} IS NULL" for column in new_columns)
        )
    )


# Ordered list of (migration_id, apply_fn). apply_fn receives a live Connection.
# Add new entries here; never edit or reorder already-shipped ones.
def _add_interview_pipeline_fields(conn: Connection) -> None:
    """Pipeline tracking columns on interviews (see ``server.pipeline``).

    ``status`` already records whether the meeting happened; ``outcome`` is the
    separate axis recording whether it advanced the application.
    """
    if not _table_exists(conn, "interviews"):
        return
    existing = _columns(conn, "interviews")
    additions = (
        ("stage", "VARCHAR(32)"),
        ("round_index", "INTEGER"),
        ("outcome", "VARCHAR(16)"),
        ("outcome_at", "TIMESTAMP"),
        ("next_step", "TEXT"),
        ("next_step_due", "TIMESTAMP"),
    )
    for name, decl in additions:
        if name not in existing:
            conn.execute(text(f"ALTER TABLE interviews ADD COLUMN {name} {decl}"))
    conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_interviews_stage ON interviews (stage)")
    )
    conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_interviews_outcome ON interviews (outcome)")
    )


def _backfill_interview_pipeline(conn: Connection) -> None:
    """Classify existing interviews onto the ladder without re-entry.

    Mirrors the desktop client's ``0014_backfill_interview_pipeline`` exactly,
    so both sides derive the same values independently. Deliberately does not
    touch ``server_modified``: this derivation adds no information a client
    could not compute itself, and bumping the pull cursor would re-deliver every
    historical interview to every client for nothing.
    """
    if not _table_exists(conn, "interviews"):
        return
    if "stage" not in _columns(conn, "interviews"):
        return

    from .pipeline import TYPE_TO_STAGE

    # 1. Stage from the free-text type.
    for interview_type, stage in TYPE_TO_STAGE.items():
        conn.execute(
            text(
                "UPDATE interviews SET stage = :stage "
                "WHERE stage IS NULL AND TRIM(COALESCE(interview_type, '')) = :t"
            ),
            {"stage": stage, "t": interview_type},
        )

    # 2. Round number: position within the application, ordered by start time.
    conn.execute(
        text(
            """
            UPDATE interviews SET round_index = (
                SELECT COUNT(*) FROM interviews b
                WHERE b.application_id = interviews.application_id
                  AND b.deleted_at IS NULL
                  AND (
                      b.start_time_utc < interviews.start_time_utc
                      OR (b.start_time_utc = interviews.start_time_utc
                          AND b.id <= interviews.id)
                  )
            )
            WHERE round_index IS NULL
              AND application_id IS NOT NULL
              AND deleted_at IS NULL
            """
        )
    )

    # 3. Outcome. A cancelled round was withdrawn from; a completed round
    #    followed by another round at the same company was, by definition,
    #    passed - advancing is the proof. Everything else stays 'pending' for
    #    the user to judge; never infer a failure.
    conn.execute(
        text(
            "UPDATE interviews SET outcome = 'withdrew' "
            "WHERE LOWER(TRIM(COALESCE(status, ''))) = 'cancelled' "
            "  AND (outcome IS NULL OR outcome = '' OR outcome = 'pending')"
        )
    )
    conn.execute(
        text(
            """
            UPDATE interviews SET outcome = 'passed'
            WHERE LOWER(TRIM(COALESCE(status, ''))) = 'completed'
              AND (outcome IS NULL OR outcome = '' OR outcome = 'pending')
              AND application_id IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM interviews b
                  WHERE b.application_id = interviews.application_id
                    AND b.id <> interviews.id
                    AND b.deleted_at IS NULL
                    AND b.start_time_utc > interviews.start_time_utc
              )
            """
        )
    )
    conn.execute(
        text(
            "UPDATE interviews SET outcome = 'pending' "
            "WHERE outcome IS NULL OR outcome = ''"
        )
    )


def _add_secondary_education(conn: Connection) -> None:
    """Optional second education history on ``profiles``.

    Lets a profile record two entries (e.g. a Bachelor's and a Master's)
    instead of just one. Mirrors the existing primary columns; blank means
    the profile only has one entry, so nothing downstream needs to treat
    these as required.
    """
    if not _table_exists(conn, "profiles"):
        return
    existing = _columns(conn, "profiles")
    additions = (
        ("university_name_2", "VARCHAR(255)"),
        ("university_location_2", "VARCHAR(255)"),
        ("degree_2", "VARCHAR(255)"),
        ("education_period_2", "VARCHAR(128)"),
    )
    for name, decl in additions:
        if name not in existing:
            conn.execute(text(f"ALTER TABLE profiles ADD COLUMN {name} {decl} DEFAULT ''"))
    conn.execute(
        text(
            "UPDATE profiles SET "
            + ", ".join(f"{name} = ''" for name, _ in additions)
            + " WHERE "
            + " OR ".join(f"{name} IS NULL" for name, _ in additions)
        )
    )


def _add_member_teams_and_profile_archive(conn: Connection) -> None:
    """Explicit supporter→member link (``users.member_id``) and profile archive.

    A supporter used to count as a member's supporter only by sharing a
    profile assignment, so one with no profile was invisible to every member
    and one on a profile shared by two members showed up for both. Backfill
    the link where that guess is unambiguous: a supporter whose profiles are
    shared with exactly one active member. Everyone else stays unlinked for an
    admin to settle on the Users page.
    """
    if _table_exists(conn, "users") and "member_id" not in _columns(conn, "users"):
        conn.execute(text("ALTER TABLE users ADD COLUMN member_id VARCHAR(36)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_member_id ON users (member_id)"))
    if _table_exists(conn, "profiles") and "archived_at" not in _columns(conn, "profiles"):
        conn.execute(text("ALTER TABLE profiles ADD COLUMN archived_at TIMESTAMP"))
    if not _table_exists(conn, "profile_assignments"):
        return
    rows = conn.execute(
        text(
            "SELECT s.id, MIN(m.id), COUNT(DISTINCT m.id) FROM users s "
            "JOIN profile_assignments a ON a.user_id = s.id "
            "JOIN profile_assignments b ON b.profile_id = a.profile_id "
            "JOIN users m ON m.id = b.user_id "
            "WHERE LOWER(s.role) = 'supporter' AND LOWER(m.role) IN ('member', 'user') "
            "AND m.is_active = :active AND s.member_id IS NULL "
            "GROUP BY s.id"
        ),
        {"active": True},
    ).fetchall()
    for supporter_id, member_id, member_count in rows:
        if member_count == 1:
            conn.execute(
                text("UPDATE users SET member_id = :m WHERE id = :s"),
                {"m": member_id, "s": supporter_id},
            )


def _add_user_archived_at(conn: Connection) -> None:
    """Deactivate becomes archive: keep ``is_active`` as the switch, add a date.

    Users already deactivated count as archived as of this migration.
    """
    if not _table_exists(conn, "users"):
        return
    if "archived_at" not in _columns(conn, "users"):
        conn.execute(text("ALTER TABLE users ADD COLUMN archived_at TIMESTAMP"))
    conn.execute(
        text("UPDATE users SET archived_at = :now WHERE is_active = :off AND archived_at IS NULL"),
        {"now": datetime.utcnow(), "off": False},
    )


def _add_columns(conn: Connection, table: str, columns: tuple[tuple[str, str], ...]) -> None:
    existing = _columns(conn, table)
    for name, ddl in columns:
        if name not in existing:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def _add_multi_product_licensing(conn: Connection) -> None:
    """One license service for CareerCraft, Sidekick and SnowLink.

    Licenses gain a product (every existing key is CareerCraft's), a customer
    and notes. Seats gain the machine's details, a last-seen time and a
    ``blocked`` flag that separates "seat released, the PC may come back" from
    "this PC is barred from the key". Seats revoked before this were described
    to admins as "the device will need a new activation", so they stay
    released, not blocked.
    """
    if _table_exists(conn, "licenses"):
        _add_columns(
            conn,
            "licenses",
            (
                ("product", "VARCHAR(32) DEFAULT 'careercraft'"),
                ("customer", "VARCHAR(255) DEFAULT ''"),
                ("notes", "TEXT"),
                ("updated_at", "TIMESTAMP"),
            ),
        )
        conn.execute(
            text("UPDATE licenses SET product = 'careercraft' WHERE product IS NULL OR product = ''")
        )
        conn.execute(text("UPDATE licenses SET customer = '' WHERE customer IS NULL"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_licenses_product ON licenses (product)"))

    if _table_exists(conn, "license_seats"):
        text_columns = ("hostname", "os_name", "os_version", "app_version", "os_user")
        _add_columns(
            conn,
            "license_seats",
            (
                ("blocked", "BOOLEAN DEFAULT FALSE"),
                ("hostname", "VARCHAR(255) DEFAULT ''"),
                ("os_name", "VARCHAR(128) DEFAULT ''"),
                ("os_version", "VARCHAR(128) DEFAULT ''"),
                ("app_version", "VARCHAR(64) DEFAULT ''"),
                ("os_user", "VARCHAR(255) DEFAULT ''"),
                ("last_seen_at", "TIMESTAMP"),
            ),
        )
        conn.execute(
            text("UPDATE license_seats SET blocked = :off WHERE blocked IS NULL"), {"off": False}
        )
        for column in text_columns:
            conn.execute(text(f"UPDATE license_seats SET {column} = '' WHERE {column} IS NULL"))
        conn.execute(
            text(
                "UPDATE license_seats SET last_seen_at = last_validated_at "
                "WHERE last_seen_at IS NULL"
            )
        )
        # The machine details CareerCraft already reported live in the fleet
        # table, keyed by the seat's device_id.
        if _table_exists(conn, "clients"):
            for column in ("hostname", "os_name", "app_version"):
                conn.execute(
                    text(
                        f"UPDATE license_seats SET {column} = COALESCE(("
                        f"SELECT c.{column} FROM clients c "
                        f"WHERE c.device_id = license_seats.device_id), '') "
                        f"WHERE {column} = ''"
                    )
                )

    if _table_exists(conn, "license_audits"):
        _add_columns(conn, "license_audits", (("product", "VARCHAR(32)"),))
        conn.execute(
            text("UPDATE license_audits SET product = 'careercraft' WHERE product IS NULL")
        )
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_license_audits_product ON license_audits (product)")
        )


def _add_license_colors(conn: Connection) -> None:
    """Give every key a color, oldest first, so one customer's keys differ."""
    if not _table_exists(conn, "licenses"):
        return
    _add_columns(conn, "licenses", (("color", "INTEGER"),))
    from .key_colors import customer_group, pick_key_color

    used: dict[str, list[int]] = {}
    rows = conn.execute(
        text("SELECT id, customer, color FROM licenses ORDER BY created_at, id")
    ).all()
    for license_id, customer, color in rows:
        group = used.setdefault(customer_group(customer), [])
        if color is None:
            color = pick_key_color(group, customer)
            conn.execute(
                text("UPDATE licenses SET color = :color WHERE id = :id"),
                {"color": color, "id": license_id},
            )
        group.append(color)


def _add_application_client_created_at(conn: Connection) -> None:
    """Best guess at when each existing application was made on the PC.

    The earlier of the server insert time and the client edit time: a record
    made offline syncs later than it was made, and one edited later has a
    newer edit time than its insert.
    """
    if not _table_exists(conn, "applications"):
        return
    _add_columns(conn, "applications", (("client_created_at", "TIMESTAMP"),))
    least = "MIN" if conn.dialect.name == "sqlite" else "LEAST"
    conn.execute(
        text(
            "UPDATE applications SET client_created_at = "
            f"{least}(COALESCE(created_at, last_modified), COALESCE(last_modified, created_at)) "
            "WHERE client_created_at IS NULL"
        )
    )


def _ack_old_withdrawn_applications(conn: Connection) -> None:
    """Only recent withdrawals should reach reviewers when quality alerts ship.

    Applications withdrawn more than 7 days ago count as already seen.
    """
    from datetime import timedelta

    # The table was removed with the Withdrawn inbox (see 0034); a fresh
    # database never has it.
    if not _table_exists(conn, "application_quality_acks"):
        return
    conn.execute(
        text(
            "INSERT INTO application_quality_acks (application_id, acked_by, acked_at, comment) "
            "SELECT a.id, NULL, :now, '' FROM applications a "
            "WHERE LOWER(a.status) = 'withdrawn' AND a.last_modified < :cutoff "
            "AND NOT EXISTS (SELECT 1 FROM application_quality_acks q "
            "WHERE q.application_id = a.id)"
        ),
        {"now": datetime.utcnow(), "cutoff": datetime.utcnow() - timedelta(days=7)},
    )


def _add_review_scan_note(conn: Connection) -> None:
    if _table_exists(conn, "review_scans"):
        _add_columns(conn, "review_scans", (("reviewer_note", "TEXT NOT NULL DEFAULT ''"),))


def _add_user_work_plan_reviewer(conn: Connection) -> None:
    if _table_exists(conn, "users"):
        _add_columns(
            conn, "users", (("can_review_work_plans", "BOOLEAN NOT NULL DEFAULT FALSE"),)
        )


MIGRATIONS: list[tuple[str, Callable[[Connection], None]]] = [
    ("0001_rebuild_license_signed_lease", _rebuild_license_signed_lease),
    ("0002_add_profile_scope", _add_profile_scope),
    ("0003_application_dedup_index", _add_application_dedup_index),
    ("0004_server_modified_cursor", _add_server_modified),
    ("0005_application_job_link_dedup", _add_application_job_link_dedup_index),
    ("0006_repair_personal_apps_by_account", _repair_personal_apps_by_account),
    ("0007_rename_user_role_to_member", _rename_user_role_to_member),
    ("0008_work_plan_week_saturday", _remap_work_plan_week_to_saturday),
    ("0009_user_avatar_url", _add_user_avatar_url),
    ("0010_repair_and_purge_personal_scope", _repair_and_purge_personal_scope),
    ("0011_recover_personal_into_profiles", _recover_personal_into_profiles),
    ("0012_recover_orphan_personal_interviews", _recover_personal_into_profiles),
    ("0013_fix_member_a_member_b_attribution", _fix_member_a_member_b_attribution),
    ("0014_purge_orphan_profile_tracking", _purge_orphan_profile_tracking),
    ("0015_member_avatar_overrides", _add_member_avatar_overrides),
    ("0016_promote_supporter_avatar_overrides", _promote_supporter_avatar_overrides),
    ("0017_sync_revisions_and_attribution", _add_sync_revisions_and_attribution),
    ("0018_reset_default_document_filenames", _reset_default_document_filenames),
    ("0019_backfill_missing_attribution", _backfill_missing_attribution),
    ("0020_relink_orphaned_interviews", _relink_orphaned_interviews),
    ("0021_sync_audit_detail_counters", _add_sync_audit_detail_counters),
    ("0022_add_user_timezone", _add_user_timezone),
    ("0023_interview_pipeline_fields", _add_interview_pipeline_fields),
    ("0024_backfill_interview_pipeline", _backfill_interview_pipeline),
    ("0025_add_secondary_education", _add_secondary_education),
    ("0027_member_teams_and_profile_archive", _add_member_teams_and_profile_archive),
    ("0028_user_archived_at", _add_user_archived_at),
    ("0029_multi_product_licensing", _add_multi_product_licensing),
    ("0031_license_colors", _add_license_colors),
    ("0032_application_client_created_at", _add_application_client_created_at),
    ("0035_review_scan_note", _add_review_scan_note),
    ("0036_user_work_plan_reviewer", _add_user_work_plan_reviewer),
]


def _seed_training_course(conn: Connection) -> bool:
    from .training_seed import seed_training_course

    return seed_training_course(conn)


def _backfill_license_seat_users(conn: Connection) -> None:
    """Record each seat's current account as a user seen on that machine."""
    conn.execute(
        text(
            "INSERT INTO license_seat_users "
            "(seat_id, identity, user_id, os_user, first_seen, last_seen) "
            "SELECT s.id, 'user:' || s.user_id, s.user_id, '', s.activated_at, "
            "COALESCE(s.last_validated_at, s.activated_at) "
            "FROM license_seats s "
            "WHERE s.user_id IS NOT NULL AND NOT EXISTS ("
            "SELECT 1 FROM license_seat_users u "
            "WHERE u.seat_id = s.id AND u.identity = 'user:' || s.user_id)"
        )
    )


def _replace_quality_tables_with_review_settings(conn: Connection) -> None:
    """The org-wide quality page became the daily review scan.

    Carry any saved thresholds and staffing agencies into the scan's default
    options, then drop the old tables (and the Withdrawn inbox's "seen" marks).
    """
    import json

    options: dict = {}
    if _table_exists(conn, "quality_settings"):
        row = conn.execute(
            text("SELECT pace_minutes, duplicate_window_days, excluded_platforms FROM quality_settings")
        ).first()
        if row is not None:
            options["pace_minutes"] = row[0]
            options["duplicate_window_days"] = row[1]
            options["excluded_sites"] = [
                p.strip() for p in (row[2] or "").replace("\n", ",").split(",") if p.strip()
            ]
    if _table_exists(conn, "staffing_agencies"):
        names = [r[0] for r in conn.execute(text("SELECT name FROM staffing_agencies ORDER BY name"))]
        if names:
            options["staffing_agencies"] = names
    has_row = conn.execute(text("SELECT COUNT(*) FROM review_settings")).scalar()
    if options and not has_row:
        conn.execute(
            text("INSERT INTO review_settings (id, options, updated_at) VALUES (1, :o, :t)"),
            {"o": json.dumps(options), "t": datetime.utcnow()},
        )
    for table in ("application_quality_acks", "staffing_agencies", "quality_settings"):
        if _table_exists(conn, table):
            conn.execute(text(f"DROP TABLE {table}"))


# Run after create_all, so they can rely on every ORM table existing. A step
# returning False is not recorded and runs again on the next start.
POST_CREATE_MIGRATIONS: list[tuple[str, Callable[[Connection], bool | None]]] = [
    ("0026_seed_training_course", _seed_training_course),
    ("0030_backfill_license_seat_users", _backfill_license_seat_users),
    ("0033_ack_old_withdrawn_applications", _ack_old_withdrawn_applications),
    ("0034_replace_quality_tables_with_review_settings", _replace_quality_tables_with_review_settings),
]


def _ensure_migrations_table(conn: Connection) -> None:
    conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "id VARCHAR(128) PRIMARY KEY, applied_at VARCHAR(64))"
        )
    )


def _applied_ids(conn: Connection) -> set[str]:
    rows = conn.execute(text("SELECT id FROM schema_migrations")).fetchall()
    return {row[0] for row in rows}


def run_migrations() -> None:
    """Apply pending migrations, then ensure all ORM tables exist."""
    # Import models so Base.metadata is fully populated before create_all.
    from . import models  # noqa: F401

    with engine.begin() as conn:
        _ensure_migrations_table(conn)
        done = _applied_ids(conn)
        for migration_id, apply_fn in MIGRATIONS:
            if migration_id in done:
                continue
            apply_fn(conn)
            conn.execute(
                text("INSERT INTO schema_migrations (id, applied_at) VALUES (:i, :t)"),
                {"i": migration_id, "t": datetime.utcnow().isoformat()},
            )

    # After drops (or on greenfield), create any missing tables from the models.
    Base.metadata.create_all(bind=engine)

    # Data steps that need the ORM tables to exist already.
    with engine.begin() as conn:
        done = _applied_ids(conn)
        for migration_id, apply_fn in POST_CREATE_MIGRATIONS:
            if migration_id in done:
                continue
            if apply_fn(conn) is False:
                continue  # nothing to apply yet; retry on the next start
            conn.execute(
                text("INSERT INTO schema_migrations (id, applied_at) VALUES (:i, :t)"),
                {"i": migration_id, "t": datetime.utcnow().isoformat()},
            )
