import { useMemo, useRef, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Row } from "../../api";
import { AvatarGroup, UserIdentity, userPerson } from "../../components/Avatar";
import { ActionMenu } from "../../components/primitives";
import {
  ProfileDetails,
  ProfileEditor,
  profileName,
  readProfileJson,
} from "../../components/ProfileForm";
import { Confirm, ErrorBox, Loading, Modal, PageHeader, useNotice } from "../../ui";

const imageTypes = new Set(["image/jpeg", "image/png", "image/webp"]);

function ManageSearch({
  value,
  onChange,
  placeholder,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  label: string;
}) {
  return (
    <div className="input-icon stage-member-manage-search">
      <span className="input-icon-addon">
        <i className="ti ti-search" aria-hidden="true" />
      </span>
      <input
        type="search"
        className="form-control"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        aria-label={label}
      />
      {value && (
        <button
          type="button"
          className="stage-search-clear"
          aria-label="Clear search"
          onClick={() => onChange("")}
        >
          <i className="ti ti-x" />
        </button>
      )}
    </div>
  );
}

const includes = (values: unknown[], needle: string) =>
  !needle.trim() ||
  values.some((value) =>
    String(value || "")
      .toLowerCase()
      .includes(needle.trim().toLowerCase()),
  );

function useMemberData() {
  const supporters = useQuery({
    queryKey: ["member-supporters"],
    queryFn: api.memberSupporters,
    retry: false,
  });
  const profiles = useQuery({
    queryKey: ["member-profiles"],
    queryFn: api.memberProfiles,
    retry: false,
  });
  const client = useQueryClient();
  const refresh = () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ["member-supporters"] }),
      client.invalidateQueries({ queryKey: ["member-profiles"] }),
    ]);
  return { supporters, profiles, refresh };
}

type Draft = Record<string, Set<string>>;
const draftFrom = (profiles: Row[]): Draft =>
  Object.fromEntries(
    profiles.map((profile) => [profile.key, new Set<string>(profile.supporter_ids || [])]),
  );

export function MemberUsersPage() {
  const { supporters, profiles, refresh } = useMemberData();
  const notice = useNotice();
  const input = useRef<HTMLInputElement>(null);
  const [target, setTarget] = useState<Row | null>(null);
  const [passwordTarget, setPasswordTarget] = useState<Row | null>(null);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const saved = useMemo(() => draftFrom(profiles.data || []), [profiles.data]);
  const current = draft || saved;
  const rows = useMemo(
    () => (supporters.data || []).filter((row) => includes([row.name, row.email], search)),
    [supporters.data, search],
  );
  if (supporters.isLoading || profiles.isLoading) return <Loading />;
  if (supporters.error || profiles.error)
    return <ErrorBox error={supporters.error || profiles.error} />;
  const profileRows = profiles.data || [];
  const changedKeys = profileRows
    .map((profile) => profile.key)
    .filter((key) => {
      const a = current[key] || new Set(),
        b = saved[key] || new Set();
      return a.size !== b.size || [...a].some((id) => !b.has(id));
    });
  const changes = changedKeys.reduce((total, key) => {
    const a = current[key] || new Set<string>(),
      b = saved[key] || new Set<string>();
    return (
      total + [...a].filter((id) => !b.has(id)).length + [...b].filter((id) => !a.has(id)).length
    );
  }, 0);

  const toggle = (key: string, supporterId: string) =>
    setDraft((value) => {
      const next: Draft = Object.fromEntries(
        Object.entries(value || saved).map(([k, ids]) => [k, new Set(ids)]),
      );
      const ids = next[key] || (next[key] = new Set());
      ids.has(supporterId) ? ids.delete(supporterId) : ids.add(supporterId);
      return next;
    });
  const save = async () => {
    setBusy(true);
    try {
      await api.setMemberAssignments(
        Object.fromEntries(changedKeys.map((key) => [key, [...(current[key] || [])]])),
      );
      await refresh();
      setDraft(null);
      notice.show("Assignments saved");
    } catch (error: any) {
      notice.show(error?.message || "Could not save assignments", "danger");
    } finally {
      setBusy(false);
    }
  };
  const upload = async (file?: File) => {
    if (!file || !target) return;
    if (!imageTypes.has(file.type) || file.size > 2 * 1024 * 1024) {
      notice.show("Choose a JPEG, PNG, or WebP image up to 2 MB", "danger");
      return;
    }
    setBusy(true);
    try {
      await api.supporterAvatar(target.id, file);
      await refresh();
      notice.show("Custom avatar saved");
    } catch (error: any) {
      notice.show(error.message, "danger");
    } finally {
      setBusy(false);
      setTarget(null);
      if (input.current) input.current.value = "";
    }
  };
  return (
    <>
      <PageHeader text="Choose which of your profiles each of your supporters works on." />
      <div className="card stage-member-manage-card">
        <div className="card-header stage-member-manage-toolbar">
          <div className="stage-member-manage-heading">
            <span className="stage-member-manage-icon bg-primary-lt text-primary">
              <i className="ti ti-headset" />
            </span>
            <div>
              <h3 className="card-title mb-0">My supporters</h3>
              <div className="small text-secondary">Tick a box to put a supporter on a profile</div>
            </div>
          </div>
          <div className="stage-member-manage-tools">
            <span className="badge bg-secondary-lt stage-member-count">
              {rows.length} of {supporters.data?.length || 0}
            </span>
            <ManageSearch
              value={search}
              onChange={setSearch}
              placeholder="Search supporters…"
              label="Search supporters"
            />
          </div>
        </div>
        {!rows.length ? (
          <div className="stage-member-empty">
            <i className="ti ti-users-minus" />
            <div className="fw-medium">
              {search ? "No matching supporters" : "No supporters on your team yet"}
            </div>
            <div className="small text-secondary">
              {search
                ? "Try a different name or email."
                : "Ask an admin or manager to add supporters to your team."}
            </div>
          </div>
        ) : (
          <div className="table-responsive">
            <table className="table table-vcenter card-table stage-assign-grid">
              <thead>
                <tr>
                  <th className="stage-assign-grid-sticky">Supporter</th>
                  {profileRows.map((profile) => (
                    <th key={profile.key} className="text-center" title={profile.email || ""}>
                      <div className="stage-assign-grid-profile">
                        <i className="ti ti-file-cv" aria-hidden="true" />
                        <span className="text-truncate">{profileName(profile)}</span>
                      </div>
                    </th>
                  ))}
                  <th className="w-1">
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const inactive = row.is_active === false;
                  return (
                    <tr key={row.id} className={inactive ? "text-secondary" : undefined}>
                      <td className="stage-assign-grid-sticky">
                        <UserIdentity
                          person={userPerson(row)}
                          size="md"
                          secondary={inactive ? "Inactive" : row.email}
                        />
                      </td>
                      {profileRows.map((profile) => {
                        const on = Boolean(current[profile.key]?.has(row.id));
                        return (
                          <td key={profile.key} className="text-center">
                            <input
                              className="form-check-input m-0"
                              type="checkbox"
                              checked={on}
                              // Inactive supporters can be taken off, not added.
                              disabled={busy || (inactive && !on)}
                              aria-label={`${row.name || row.email} on ${profileName(profile)}`}
                              onChange={() => toggle(profile.key, row.id)}
                            />
                          </td>
                        );
                      })}
                      <td>
                        <ActionMenu
                          label={`Actions for ${row.name || row.email}`}
                          items={[
                            {
                              label: "Change avatar",
                              icon: "ti-photo",
                              disabled: busy,
                              onSelect: () => {
                                setTarget(row);
                                input.current?.click();
                              },
                            },
                            ...(row.custom_avatar_url
                              ? [
                                  {
                                    label: "Restore default avatar",
                                    icon: "ti-refresh",
                                    onSelect: async () => {
                                      try {
                                        await api.removeSupporterAvatar(row.id);
                                        await refresh();
                                        notice.show("Default avatar restored");
                                      } catch (error: any) {
                                        notice.show(error.message, "danger");
                                      }
                                    },
                                  },
                                ]
                              : []),
                            {
                              label: "Reset password",
                              icon: "ti-key",
                              separatorBefore: true,
                              onSelect: () => setPasswordTarget(row),
                            },
                          ]}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {!profileRows.length && (
              <div className="card-body text-secondary border-top">
                You have no profiles yet. Create one on the Profiles page, then come back to assign
                supporters.
              </div>
            )}
          </div>
        )}
        {changes > 0 && (
          <div className="card-footer stage-assign-grid-savebar">
            <span className="text-secondary">
              {changes} unsaved change{changes === 1 ? "" : "s"}
            </span>
            <div className="d-flex gap-2">
              <button className="btn" disabled={busy} onClick={() => setDraft(null)}>
                Discard
              </button>
              <button className="btn btn-primary" disabled={busy} onClick={save}>
                {busy ? "Saving…" : "Save changes"}
              </button>
            </div>
          </div>
        )}
      </div>
      <input
        ref={input}
        hidden
        type="file"
        accept="image/jpeg,image/png,image/webp"
        onChange={(event) => upload(event.target.files?.[0])}
      />
      {passwordTarget && (
        <PasswordReset
          supporter={passwordTarget}
          onClose={() => setPasswordTarget(null)}
          onSaved={() => {
            setPasswordTarget(null);
            notice.show("Supporter password reset");
          }}
        />
      )}
      {notice.node}
    </>
  );
}

export function MemberProfilesPage() {
  const { supporters, profiles, refresh } = useMemberData();
  const notice = useNotice();
  const importRef = useRef<HTMLInputElement>(null);
  const [search, setSearch] = useState("");
  const [detail, setDetail] = useState<Row | null>(null);
  const [editing, setEditing] = useState<Row | null>(null);
  const [assigning, setAssigning] = useState<Row | null>(null);
  const [archiving, setArchiving] = useState<Row | null>(null);
  if (profiles.isLoading || supporters.isLoading) return <Loading />;
  if (profiles.error || supporters.error)
    return <ErrorBox error={profiles.error || supporters.error} />;
  const team = supporters.data || [];
  const rows = (profiles.data || []).filter((row) =>
    includes([row.name, row.email, row.location], search),
  );
  const teamPeople = (ids: string[] = []) =>
    ids.map((id) => userPerson(team.find((supporter) => supporter.id === id) || { id }));
  const importJson = async (file?: File) => {
    if (!file) return;
    try {
      const created = await api.importMemberProfiles(await readProfileJson(file));
      await refresh();
      notice.show(`Imported ${created.length} profile${created.length === 1 ? "" : "s"}`);
    } catch (error: any) {
      notice.show(error?.message || "Import failed", "danger");
    } finally {
      if (importRef.current) importRef.current.value = "";
    }
  };
  return (
    <>
      <PageHeader text="Create and maintain your resume profiles and choose which supporters work on them." />
      <div className="card stage-member-manage-card">
        <div className="card-header stage-member-manage-toolbar">
          <div className="stage-member-manage-heading">
            <span className="stage-member-manage-icon bg-azure-lt text-azure">
              <i className="ti ti-id" />
            </span>
            <div>
              <h3 className="card-title mb-0">My profiles</h3>
              <div className="small text-secondary">Resume identity and who works on it</div>
            </div>
          </div>
          <div className="stage-member-manage-tools flex-wrap">
            <ManageSearch
              value={search}
              onChange={setSearch}
              placeholder="Search profiles…"
              label="Search my profiles"
            />
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
        {!rows.length ? (
          <div className="stage-member-empty">
            <i className="ti ti-id-off" />
            <div className="fw-medium">{search ? "No matching profiles" : "No profiles yet"}</div>
            <div className="small text-secondary">
              {search
                ? "Try another search term."
                : "Create a profile or import a profile JSON file to get started."}
            </div>
          </div>
        ) : (
          <div className="table-responsive">
            <table className="table table-vcenter card-table table-hover stage-member-manage-table stage-member-profile-table">
              <thead>
                <tr>
                  <th>Profile</th>
                  <th>Supporters</th>
                  <th>Location</th>
                  <th className="w-1">
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.key}
                    className="cursor-pointer stage-profile-object-row"
                    onClick={() => setDetail(row)}
                  >
                    <td>
                      <div className="stage-profile-object-identity">
                        <span className="stage-profile-object-icon">
                          <i className="ti ti-file-cv" aria-hidden="true" />
                        </span>
                        <div className="min-w-0">
                          <div className="fw-semibold text-truncate">{profileName(row)}</div>
                          <div className="stage-profile-object-meta">
                            <span>{row.email || "No email set"}</span>
                          </div>
                        </div>
                      </div>
                    </td>
                    <td onClick={(event) => event.stopPropagation()}>
                      <button
                        type="button"
                        className="stage-colleagues-btn"
                        title="Choose supporters"
                        disabled={!team.length}
                        onClick={() => setAssigning(row)}
                      >
                        <AvatarGroup
                          people={teamPeople(row.supporter_ids)}
                          empty={
                            <span className="text-secondary">
                              <i className="ti ti-user-plus me-1" />
                              {team.length ? "Assign" : "No team yet"}
                            </span>
                          }
                        />
                      </button>
                    </td>
                    <td>
                      <span className="stage-profile-location">
                        <i className="ti ti-map-pin" />
                        {row.location || "Not set"}
                      </span>
                    </td>
                    <td onClick={(event) => event.stopPropagation()}>
                      <ActionMenu
                        label={`Actions for ${profileName(row)}`}
                        items={[
                          { label: "View details", icon: "ti-eye", onSelect: () => setDetail(row) },
                          {
                            label: "Edit profile",
                            icon: "ti-pencil",
                            onSelect: () => setEditing(row),
                          },
                          {
                            label: "Supporters",
                            icon: "ti-user-plus",
                            disabled: !team.length,
                            onSelect: () => setAssigning(row),
                          },
                          {
                            label: "Archive",
                            icon: "ti-archive",
                            separatorBefore: true,
                            onSelect: () => setArchiving(row),
                          },
                        ]}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {detail && (
        <Modal title={profileName(detail)} onClose={() => setDetail(null)}>
          <ProfileDetails profile={detail} />
          <div className="stage-wp-section-title mb-2">Supporters</div>
          <div className="mb-3">
            <AvatarGroup people={teamPeople(detail.supporter_ids)} empty="None yet" />
          </div>
          <div className="text-end">
            <button
              className="btn btn-primary"
              onClick={() => {
                setEditing(detail);
                setDetail(null);
              }}
            >
              <i className="ti ti-pencil me-1" />
              Edit profile
            </button>
          </div>
        </Modal>
      )}
      {editing && (
        <ProfileEditor
          profile={editing}
          onClose={() => setEditing(null)}
          onSubmit={async (values) => {
            if (editing.key) await api.updateMemberProfile(editing.key, values);
            else await api.createMemberProfile(values);
            setEditing(null);
            await refresh();
            notice.show(editing.key ? "Profile updated" : "Profile created");
          }}
        />
      )}
      {assigning && (
        <SupporterPicker
          profile={assigning}
          team={team}
          onClose={() => setAssigning(null)}
          onSaved={async () => {
            setAssigning(null);
            await refresh();
            notice.show("Supporters updated");
          }}
        />
      )}
      {archiving && (
        <Confirm
          text={`Archive ${profileName(archiving)}? It disappears from the desktop app and from this page for everyone on it. Its applications and interviews are kept; ask an admin or manager if you need it back.`}
          onClose={() => setArchiving(null)}
          onConfirm={async () => {
            try {
              await api.archiveMemberProfile(archiving.key);
              await refresh();
              notice.show("Profile archived");
            } catch (error: any) {
              notice.show(error?.message || "Could not archive profile", "danger");
            }
          }}
        />
      )}
      {notice.node}
    </>
  );
}

function SupporterPicker({
  profile,
  team,
  onClose,
  onSaved,
}: {
  profile: Row;
  team: Row[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [selected, setSelected] = useState(() => new Set<string>(profile.supporter_ids || []));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await api.setMemberProfileSupporters(profile.key, [...selected]);
      onSaved();
    } catch (caught: any) {
      setError(caught?.message || "Could not save supporters");
      setBusy(false);
    }
  };
  return (
    <Modal
      title={`Supporters · ${profileName(profile)}`}
      onClose={onClose}
      size="md"
      footer={
        <>
          <span className="text-secondary small me-auto">{selected.size} selected</span>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={busy} onClick={save}>
            {busy ? "Saving…" : "Save"}
          </button>
        </>
      }
    >
      {error && <div className="alert alert-danger">{error}</div>}
      <div className="stage-assign-list">
        {team.map((supporter) => {
          const checked = selected.has(supporter.id);
          const inactive = supporter.is_active === false;
          return (
            <label
              className={`stage-assign-item${checked ? " is-selected" : ""}`}
              key={supporter.id}
            >
              <input
                className="form-check-input"
                type="checkbox"
                checked={checked}
                disabled={inactive && !checked}
                onChange={() =>
                  setSelected((current) => {
                    const next = new Set(current);
                    next.has(supporter.id) ? next.delete(supporter.id) : next.add(supporter.id);
                    return next;
                  })
                }
              />
              <UserIdentity
                person={userPerson(supporter)}
                secondary={inactive ? "Inactive" : supporter.email}
              />
            </label>
          );
        })}
      </div>
    </Modal>
  );
}

function PasswordReset({
  supporter,
  onClose,
  onSaved,
}: {
  supporter: Row;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    const data = new FormData(event.currentTarget);
    const password = String(data.get("password") || "");
    const confirm = String(data.get("confirm") || "");
    if (password !== confirm) {
      setError("Passwords do not match");
      setBusy(false);
      return;
    }
    try {
      await api.supporterPassword(supporter.id, password);
      onSaved();
    } catch (caught: any) {
      setError(caught.message || "Password reset failed");
      setBusy(false);
    }
  };
  return (
    <Modal title={`Reset password · ${supporter.name || supporter.email}`} onClose={onClose}>
      <form onSubmit={submit}>
        {error && <div className="alert alert-danger">{error}</div>}
        <label className="form-label">New password</label>
        <input
          className="form-control mb-3"
          name="password"
          type="password"
          minLength={8}
          required
          autoFocus
        />
        <label className="form-label">Confirm password</label>
        <input
          className="form-control mb-3"
          name="confirm"
          type="password"
          minLength={8}
          required
        />
        <div className="text-end">
          <button className="btn btn-primary" disabled={busy}>
            {busy ? "Resetting…" : "Reset password"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
