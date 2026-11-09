import { useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Row } from "../../api";
import { Confirm, ErrorBox, Loading, Modal, PageHeader, useNotice } from "../../ui";
import { ActionMenu } from "../../components/primitives";
import { AvatarGroup, UserIdentity, userPerson } from "../../components/Avatar";
import {
  maskedPhone,
  ProfileDetails,
  ProfileEditor,
  profileName,
  readProfileJson,
} from "../../components/ProfileForm";

type SortKey = "name" | "location" | "assigned";
type Tab = "active" | "archived";

export function ProfilesPage() {
  const queryClient = useQueryClient();
  const notice = useNotice();
  const profiles = useQuery({ queryKey: ["profiles"], queryFn: api.profiles });
  const users = useQuery({ queryKey: ["users"], queryFn: api.users });
  const [tab, setTab] = useState<Tab>("active");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; direction: 1 | -1 }>({
    key: "name",
    direction: 1,
  });
  const [editing, setEditing] = useState<Row | null>(null);
  const [detail, setDetail] = useState<Row | null>(null);
  const [assigning, setAssigning] = useState<Row | null>(null);
  const [archiving, setArchiving] = useState<Row | null>(null);
  const [deleting, setDeleting] = useState<{ profile: Row; usage: Row } | null>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["profiles"] });
    queryClient.invalidateQueries({ queryKey: ["users"] });
    queryClient.invalidateQueries({ queryKey: ["users-all"] });
  };
  const run = async (operation: () => Promise<unknown>, message: string) => {
    try {
      await operation();
      notice.show(message);
      refresh();
    } catch (error: any) {
      notice.show(error?.message || "Update failed", "danger");
    }
  };

  const all = profiles.data || [];
  const counts = {
    active: all.filter((profile) => !profile.archived_at).length,
    archived: all.filter((profile) => profile.archived_at).length,
  };
  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return all
      .filter((profile) => (tab === "archived") === Boolean(profile.archived_at))
      .filter(
        (profile) =>
          !needle ||
          [profile.name, profile.key, profile.email, profile.location].some((value) =>
            String(value || "")
              .toLowerCase()
              .includes(needle),
          ),
      )
      .sort((a, b) => {
        if (sort.key === "assigned")
          return (assignedIds(a).length - assignedIds(b).length) * sort.direction;
        const av = String((sort.key === "name" ? profileName(a) : a.location) || "");
        const bv = String((sort.key === "name" ? profileName(b) : b.location) || "");
        return av.localeCompare(bv, undefined, { sensitivity: "base" }) * sort.direction;
      });
  }, [all, tab, search, sort]);
  if (profiles.isLoading || users.isLoading) return <Loading />;
  if (profiles.error || users.error) return <ErrorBox error={profiles.error || users.error} />;
  const userRows = (users.data || []).filter((user) => String(user.role).toLowerCase() !== "admin");
  const setSortKey = (key: SortKey) =>
    setSort((current) => ({
      key,
      direction: current.key === key ? (current.direction === 1 ? -1 : 1) : 1,
    }));

  const importJson = async (file?: File) => {
    if (!file) return;
    try {
      const created = await api.importProfiles(await readProfileJson(file));
      notice.show(`Imported ${created.length} profile${created.length === 1 ? "" : "s"}`);
      setTab("active");
      refresh();
    } catch (error: any) {
      notice.show(error?.message || "Import failed", "danger");
    } finally {
      if (importRef.current) importRef.current.value = "";
    }
  };
  const confirmDelete = async (profile: Row) => {
    try {
      setDeleting({ profile, usage: await api.profileUsage(profile.key) });
    } catch (error: any) {
      notice.show(error?.message || "Could not load profile data", "danger");
    }
  };

  return (
    <>
      <PageHeader text="Create resume profiles, choose who works on them, and archive old ones." />
      <div className="card">
        <div className="card-header stage-panel-header">
          <div className="d-flex flex-wrap align-items-center gap-3">
            <div className="btn-group btn-group-sm" role="group" aria-label="Profile status">
              {(["active", "archived"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  className={`btn ${tab === value ? "btn-primary" : "btn-outline-secondary"}`}
                  aria-pressed={tab === value}
                  onClick={() => setTab(value)}
                >
                  {value === "active" ? "Active" : "Archived"}
                  <span
                    className={`badge ms-2 ${tab === value ? "bg-white text-primary" : "bg-secondary-lt"}`}
                  >
                    {counts[value]}
                  </span>
                </button>
              ))}
            </div>
            {search && <span className="text-secondary small">{rows.length} shown</span>}
          </div>
          <div className="d-flex flex-wrap gap-2">
            <div className="input-icon">
              <span className="input-icon-addon">
                <i className="ti ti-search" />
              </span>
              <input
                className="form-control"
                value={search}
                aria-label="Search profiles"
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search profiles…"
              />
            </div>
            <input
              ref={importRef}
              hidden
              type="file"
              accept="application/json,.json"
              onChange={(event) => importJson(event.target.files?.[0])}
            />
            <button
              className="btn btn-outline-secondary"
              onClick={() => importRef.current?.click()}
            >
              <i className="ti ti-file-import me-1" />
              Import JSON
            </button>
            <button className="btn btn-primary" onClick={() => setEditing({})}>
              <i className="ti ti-plus me-1" />
              New profile
            </button>
          </div>
        </div>
        <div className="table-responsive">
          <table className="table table-vcenter card-table table-sm table-hover stage-profiles-table">
            <thead>
              <tr>
                <SortHeader field="name" label="Name" sort={sort} onClick={setSortKey} />
                <SortHeader field="location" label="Location" sort={sort} onClick={setSortKey} />
                <th>Phone</th>
                <SortHeader field="assigned" label="Assigned" sort={sort} onClick={setSortKey} />
                <th className="w-1" />
              </tr>
            </thead>
            <tbody>
              {rows.map((profile) => (
                <tr
                  key={profile.key}
                  className="cursor-pointer stage-interactive-row stage-profile-object-row"
                  tabIndex={0}
                  onClick={() => setDetail(profile)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setDetail(profile);
                    }
                  }}
                >
                  <td>
                    <div className="stage-profile-object-identity">
                      <span className="stage-profile-object-icon">
                        <i className="ti ti-file-cv" aria-hidden="true" />
                      </span>
                      <div className="min-w-0 text-start">
                        <div className="fw-semibold text-truncate">{profileName(profile)}</div>
                        <div className="stage-profile-object-meta">
                          <span>{profile.email || "Resume profile"}</span>
                        </div>
                      </div>
                    </div>
                  </td>
                  <td>
                    <span className="stage-profile-location">
                      <i className="ti ti-map-pin" aria-hidden="true" />
                      <span>{profile.location || "Not set"}</span>
                    </span>
                  </td>
                  <td>
                    <span
                      className="stage-profile-object-contact"
                      title="Phone number hidden for privacy"
                    >
                      <i className="ti ti-phone" aria-hidden="true" />
                      <span className="font-monospace">{maskedPhone(profile.phone)}</span>
                    </span>
                  </td>
                  <td onClick={(event) => event.stopPropagation()}>
                    <button
                      type="button"
                      className="stage-colleagues-btn"
                      title="Change who works on this profile"
                      disabled={Boolean(profile.archived_at)}
                      onClick={() => setAssigning(profile)}
                    >
                      <AvatarGroup
                        people={assignedPeople(profile, userRows)}
                        empty={
                          <span className="text-secondary">
                            <i className="ti ti-user-plus me-1" />
                            Assign
                          </span>
                        }
                      />
                    </button>
                  </td>
                  <td onClick={(event) => event.stopPropagation()}>
                    <ActionMenu
                      label={`Actions for ${profileName(profile)}`}
                      items={
                        profile.archived_at
                          ? [
                              {
                                label: "Restore",
                                icon: "ti-archive-off",
                                onSelect: () =>
                                  run(() => api.restoreProfile(profile.key), "Profile restored"),
                              },
                              {
                                label: "Delete permanently",
                                icon: "ti-trash",
                                danger: true,
                                separatorBefore: true,
                                onSelect: () => confirmDelete(profile),
                              },
                            ]
                          : [
                              {
                                label: "Edit",
                                icon: "ti-pencil",
                                onSelect: () => setEditing(profile),
                              },
                              {
                                label: "Assignments",
                                icon: "ti-user-plus",
                                onSelect: () => setAssigning(profile),
                              },
                              {
                                label: "Archive",
                                icon: "ti-archive",
                                separatorBefore: true,
                                onSelect: () => setArchiving(profile),
                              },
                            ]
                      }
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!rows.length && (
          <div className="card-body text-center text-secondary">
            {search
              ? "No profiles match your search."
              : tab === "archived"
                ? "No archived profiles."
                : "No profiles yet. Create one or import JSON."}
          </div>
        )}
      </div>
      {editing && (
        <ProfileEditor
          profile={editing}
          onClose={() => setEditing(null)}
          onSubmit={async (values) => {
            if (editing.key) await api.saveProfile(editing.key, values);
            else await api.createProfile(values);
            setEditing(null);
            setTab("active");
            refresh();
            notice.show(editing.key ? "Profile saved" : "Profile created");
          }}
        />
      )}
      {detail && (
        <Modal title={profileName(detail)} onClose={() => setDetail(null)}>
          {detail.archived_at ? (
            <div className="alert alert-secondary mb-4">
              Archived. It isn't on the desktop app; its applications and interviews are kept.
            </div>
          ) : (
            <div className="d-flex gap-2 mb-4">
              <button
                className="btn btn-primary btn-sm"
                onClick={() => {
                  setEditing(detail);
                  setDetail(null);
                }}
              >
                <i className="ti ti-pencil me-1" />
                Edit
              </button>
              <button
                className="btn btn-outline-primary btn-sm"
                onClick={() => {
                  setAssigning(detail);
                  setDetail(null);
                }}
              >
                <i className="ti ti-user-plus me-1" />
                Assignments
              </button>
            </div>
          )}
          <ProfileDetails profile={detail} />
          <div className="stage-wp-section-title mb-2">Assigned</div>
          <AssignedList ids={assignedIds(detail)} users={userRows} />
        </Modal>
      )}
      {assigning && (
        <AssignmentDialog
          profile={assigning}
          users={userRows}
          onClose={() => setAssigning(null)}
          onSaved={() => {
            setAssigning(null);
            refresh();
            notice.show("Assignments updated");
          }}
        />
      )}
      {archiving && (
        <Confirm
          text={`Archive ${profileName(archiving)}? It disappears from the desktop app for everyone assigned. Its applications and interviews are kept, and you can restore it from the Archived tab.`}
          onClose={() => setArchiving(null)}
          onConfirm={() => run(() => api.archiveProfile(archiving.key), "Profile archived")}
        />
      )}
      {deleting && (
        <Confirm
          text={`Permanently delete ${profileName(deleting.profile)}? This also deletes its ${deleting.usage.applications} application${deleting.usage.applications === 1 ? "" : "s"} and ${deleting.usage.interviews} interview${deleting.usage.interviews === 1 ? "" : "s"}. This cannot be undone.`}
          onClose={() => setDeleting(null)}
          onConfirm={() => run(() => api.deleteProfile(deleting.profile.key), "Profile deleted")}
        />
      )}
      {notice.node}
    </>
  );
}

const roleOf = (user: Row) => {
  const role = String(user.role || "").toLowerCase();
  return role === "user" ? "member" : role;
};

/** Members with their own supporters nested under them, then managers and
 * supporters nobody has linked to a member yet. */
function groupUsers(users: Row[]) {
  const members = users.filter((user) => roleOf(user) === "member");
  const memberIds = new Set(members.map((member) => member.id));
  const supporters = users.filter((user) => roleOf(user) === "supporter");
  return {
    teams: members.map((member) => ({
      member,
      supporters: supporters.filter((supporter) => supporter.member_id === member.id),
    })),
    managers: users.filter((user) => roleOf(user) === "manager"),
    unlinked: supporters.filter((supporter) => !memberIds.has(supporter.member_id)),
  };
}

function AssignmentDialog({
  profile,
  users,
  onClose,
  onSaved,
}: {
  profile: Row;
  users: Row[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const original = useMemo(() => new Set(assignedIds(profile)), [profile]);
  const [selected, setSelected] = useState(() => new Set(original));
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const groups = useMemo(() => groupUsers(users), [users]);
  const needle = search.trim().toLowerCase();
  const matches = (user: Row) =>
    !needle || `${user.name} ${user.email}`.toLowerCase().includes(needle);
  const changed = selected.size !== original.size || [...selected].some((id) => !original.has(id));

  const toggle = (user: Row) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(user.id)) {
        next.delete(user.id);
        // A supporter can't stay on a profile without their member.
        if (roleOf(user) === "member")
          users.filter((other) => other.member_id === user.id).forEach((s) => next.delete(s.id));
      } else {
        next.add(user.id);
        if (roleOf(user) === "supporter" && user.member_id) next.add(user.member_id);
      }
      return next;
    });
  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await api.setProfileAssignees(profile.key, [...selected]);
      onSaved();
    } catch (caught: any) {
      setError(caught?.message || "Could not save assignments");
      setBusy(false);
    }
  };
  const item = (user: Row, nested = false, disabled = false, hint?: string) => (
    <label
      key={user.id}
      className={`stage-assign-item${selected.has(user.id) ? " is-selected" : ""}${nested ? " ms-4" : ""}`}
      title={hint}
    >
      <input
        className="form-check-input"
        type="checkbox"
        checked={selected.has(user.id)}
        disabled={disabled}
        onChange={() => toggle(user)}
      />
      <UserIdentity
        person={userPerson(user)}
        secondary={user.is_active === false ? "Inactive" : hint || user.email}
      />
    </label>
  );
  const visibleTeams = groups.teams.filter(
    (team) => matches(team.member) || team.supporters.some(matches),
  );
  const managers = groups.managers.filter(matches);
  const unlinked = groups.unlinked.filter(matches);

  return (
    <Modal
      title={`Assignments · ${profileName(profile)}`}
      description="Checking a supporter adds their member too. Removing a member removes their supporters."
      onClose={onClose}
      footer={
        <>
          <span className="text-secondary small me-auto">{selected.size} assigned</span>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={busy || !changed} onClick={save}>
            {busy ? "Saving…" : "Save assignments"}
          </button>
        </>
      }
    >
      {error && <div className="alert alert-danger">{error}</div>}
      <div className="input-icon mb-3">
        <span className="input-icon-addon">
          <i className="ti ti-search" />
        </span>
        <input
          className="form-control"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search people"
          autoFocus
        />
      </div>
      <div className="stage-assign-list">
        {visibleTeams.length > 0 && (
          <div className="stage-detail-section-label">Members & their supporters</div>
        )}
        {visibleTeams.map((team) => (
          <div key={team.member.id}>
            {item(team.member)}
            {team.supporters
              .filter((s) => matches(s) || matches(team.member))
              .map((s) => item(s, true))}
          </div>
        ))}
        {managers.length > 0 && <div className="stage-detail-section-label mt-3">Managers</div>}
        {managers.map((user) => item(user))}
        {unlinked.length > 0 && (
          <div className="stage-detail-section-label mt-3">Supporters without a member</div>
        )}
        {unlinked.map((user) =>
          item(user, false, !original.has(user.id), "Link to a member on the Users page to assign"),
        )}
        {!visibleTeams.length && !managers.length && !unlinked.length && (
          <div className="text-secondary">No people match your search.</div>
        )}
      </div>
    </Modal>
  );
}

function AssignedList({ ids, users }: { ids: string[]; users: Row[] }) {
  const people = ids.map((id) => users.find((user) => user.id === id) || { id });
  if (!people.length) return <div className="text-secondary">Nobody is assigned yet.</div>;
  return (
    <div className="d-flex flex-column gap-2">
      {people.map((user) => (
        <UserIdentity
          key={user.id}
          person={userPerson(user)}
          secondary={
            roleOf(user) === "supporter" && user.member_name
              ? `Supporter · works for ${user.member_name}`
              : roleOf(user) || undefined
          }
        />
      ))}
    </div>
  );
}

function assignedPeople(profile: Row, users: Row[]) {
  return assignedIds(profile).map((id) =>
    userPerson(users.find((user) => user.id === id) || { id }),
  );
}

function SortHeader({
  field,
  label,
  sort,
  onClick,
}: {
  field: SortKey;
  label: string;
  sort: { key: SortKey; direction: 1 | -1 };
  onClick: (key: SortKey) => void;
}) {
  return (
    <th>
      <button className="table-sort btn btn-link p-0 text-reset" onClick={() => onClick(field)}>
        {label}
        {sort.key === field && (
          <i className={`ti ti-chevron-${sort.direction === 1 ? "up" : "down"} ms-1`} />
        )}
      </button>
    </th>
  );
}

function assignedIds(profile: Row): string[] {
  return Array.isArray(profile.assigned_user_ids) ? profile.assigned_user_ids : [];
}
