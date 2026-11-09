import { useMemo, useState, type FormEvent, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Row } from "../../api";
import { arr, Confirm, ErrorBox, Loading, Modal, PageHeader, useNotice, when } from "../../ui";
import { AvatarGroup, PersonSelect, UserIdentity, userPerson } from "../../components/Avatar";
import { TimezoneBadge, TimezoneSelect } from "../../components/Timezone";

type Tab = "people" | "supporters" | "signups" | "archived";
type Person = ReturnType<typeof userPerson>;

// Matches the crown/headset role cues on Avatar.tsx (manager amber, supporter
// teal); member gets its own icon/color here since Avatar.tsx leaves it plain.
const ROLE_META: Record<string, { icon: string; color: string }> = {
  manager: { icon: "ti-crown", color: "#f59f00" },
  member: { icon: "ti-user", color: "#4263eb" },
  supporter: { icon: "ti-headset", color: "#0c8599" },
};

export function UsersPage() {
  const queryClient = useQueryClient();
  const notice = useNotice();
  const query = useQuery({
    queryKey: ["users-all"],
    queryFn: async () => {
      const [users, fleet, signups, profiles] = await Promise.all([
        api.allUsers(),
        api.fleet(),
        api.signups(),
        api.profiles(),
      ]);
      return {
        users: arr(users),
        fleet: arr(fleet),
        signups: arr(signups),
        profiles: arr(profiles),
      };
    },
  });
  const [tab, setTab] = useState<Tab>("people");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [roleChange, setRoleChange] = useState<{ user: Row; role: string } | null>(null);
  const [archiving, setArchiving] = useState<Row | null>(null);
  const [restoring, setRestoring] = useState<Row | null>(null);
  const [detail, setDetail] = useState<Row | null>(null);
  const [passwordUser, setPasswordUser] = useState<Row | null>(null);
  const [deleteUser, setDeleteUser] = useState<Row | null>(null);
  const [rejectSignup, setRejectSignup] = useState<Row | null>(null);
  const [otp, setOtp] = useState<Row | null>(null);
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["users-all"] });
    queryClient.invalidateQueries({ queryKey: ["users"] });
    queryClient.invalidateQueries({ queryKey: ["profiles"] });
  };
  const perform = async (operation: () => Promise<unknown>, message: string) => {
    try {
      await operation();
      notice.show(message);
      refresh();
    } catch (error: any) {
      notice.show(error?.message || "Update failed", "danger");
    }
  };

  const everyone = useMemo<Row[]>(() => {
    if (!query.data) return [];
    const fleet = devicesByEmail(query.data.fleet);
    return query.data.users
      .filter((user) => normalizeRole(user.role) !== "admin")
      .map((user): Row => ({
        ...user,
        role: normalizeRole(user.role),
        devices: fleet.get(String(user.email).toLowerCase()) || [],
      }));
  }, [query.data]);
  const active = everyone.filter((user) => user.is_active !== false);
  const members = active.filter((user) => user.role === "member").map((user) => userPerson(user));
  const matches = (user: Row) => {
    const needle = search.trim().toLowerCase();
    return (
      !needle ||
      [user.name, user.email, user.role, user.member_name].some((value) =>
        String(value || "")
          .toLowerCase()
          .includes(needle),
      )
    );
  };
  const people = active.filter((user) => user.role !== "supporter");
  const supporters = active.filter((user) => user.role === "supporter");
  const archived = everyone.filter((user) => user.is_active === false);
  const signups = query.data?.signups || [];
  const unlinked = supporters.filter((user) => !user.member_name).length;

  if (query.isLoading) return <Loading />;
  if (query.error) return <ErrorBox error={query.error} />;
  const selected = everyone.find((user) => user.id === selectedId) || null;
  const teamOf = (memberId: string) => supporters.filter((user) => user.member_id === memberId);
  const profileNames = (user: Row) => {
    const keys = new Set<string>(Array.isArray(user.profile_keys) ? user.profile_keys : []);
    return query
      .data!.profiles.filter((profile) => keys.has(profile.key))
      .map((profile) => ({
        key: profile.key,
        name: profile.name || profile.key,
        archived: !!profile.archived_at,
      }));
  };
  const onRole = (user: Row, role: string) => {
    if (role === user.role) return;
    if (role === "supporter" || (user.role === "member" && teamOf(user.id).length))
      setRoleChange({ user, role });
    else perform(() => api.userRole(user.id, role), "Role updated");
  };
  const tabs: { key: Tab; label: string; count: number; warn?: boolean }[] = [
    { key: "people", label: "Members & managers", count: people.length },
    { key: "supporters", label: "Supporters", count: supporters.length, warn: unlinked > 0 },
    { key: "signups", label: "Pending signups", count: signups.length, warn: signups.length > 0 },
    { key: "archived", label: "Archived", count: archived.length },
  ];

  return (
    <>
      <PageHeader text="Manage accounts, roles, teams and access. Click a person to edit them." />
      <div className="stage-users-toolbar">
        <div className="btn-group stage-users-tabs" role="tablist" aria-label="User groups">
          {tabs.map((item) => (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={tab === item.key}
              className={`btn ${tab === item.key ? "btn-primary" : "btn-outline-secondary"}`}
              onClick={() => setTab(item.key)}
            >
              {item.label}
              <span
                className={`badge ms-2 ${
                  tab === item.key
                    ? "bg-white text-primary"
                    : item.warn
                      ? "bg-warning text-white"
                      : "bg-secondary-lt"
                }`}
              >
                {item.count}
              </span>
            </button>
          ))}
        </div>
        {tab !== "signups" && (
          <div className="input-icon stage-users-search">
            <span className="input-icon-addon">
              <i className="ti ti-search" />
            </span>
            <input
              className="form-control"
              value={search}
              aria-label="Search people"
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search name, email, member…"
            />
          </div>
        )}
      </div>

      {tab === "signups" && (
        <SignupPanel
          rows={signups}
          onOtp={async (signup) => {
            try {
              setOtp(await api.issueOtp(signup.id));
              refresh();
            } catch (error: any) {
              notice.show(error.message, "danger");
            }
          }}
          onReject={setRejectSignup}
        />
      )}
      {tab === "people" && (
        <PeopleTable
          rows={people.filter(matches)}
          columns={["Role", "Team", "Profiles", "Last seen"]}
          empty="No members or managers match."
          compact
          onOpen={(user) => setSelectedId(user.id)}
          cells={(user) => [
            <RoleBadge role={user.role} />,
            user.role === "member" ? (
              <AvatarGroup
                people={teamOf(user.id).map(userPerson)}
                limit={5}
                empty={<span className="text-secondary">No supporters</span>}
              />
            ) : (
              <span className="text-secondary">—</span>
            ),
            <ProfileCount user={user} />,
            <LastSeen user={user} />,
          ]}
        />
      )}
      {tab === "supporters" && (
        <SupporterTable
          supporters={supporters.filter(matches)}
          members={active.filter((user) => user.role === "member")}
          onOpen={(user) => setSelectedId(user.id)}
        />
      )}
      {tab === "archived" && (
        <PeopleTable
          rows={archived.filter(matches)}
          columns={["Role", "Archived", ""]}
          empty="Nobody is archived."
          onOpen={(user) => setSelectedId(user.id)}
          cells={(user) => [
            <RoleBadge role={user.role} />,
            <span className="text-secondary">
              {user.archived_at ? when(user.archived_at) : "—"}
            </span>,
            <div className="btn-list flex-nowrap justify-content-end">
              <button
                className="btn btn-sm btn-outline-primary"
                onClick={(event) => {
                  event.stopPropagation();
                  setRestoring(user);
                }}
              >
                <i className="ti ti-archive-off me-1" />
                Restore
              </button>
              <button
                className="btn btn-sm btn-ghost-danger"
                onClick={(event) => {
                  event.stopPropagation();
                  setDeleteUser(user);
                }}
              >
                Delete
              </button>
            </div>,
          ]}
        />
      )}

      {selected && (
        <UserPanel
          user={selected}
          members={members}
          team={teamOf(selected.id)}
          profiles={profileNames(selected)}
          onClose={() => setSelectedId(null)}
          onRole={(role) => onRole(selected, role)}
          onMember={(memberId) =>
            perform(
              () => api.userMember(selected.id, memberId || null),
              memberId ? "Supporter linked" : "Supporter unlinked",
            )
          }
          onWorkPlanReviewer={(enabled) =>
            perform(
              () => api.userWorkPlanReviewer(selected.id, enabled),
              enabled ? "Now a work plan reviewer" : "No longer a work plan reviewer",
            )
          }
          onTimezone={(timezone) =>
            perform(() => api.userTimezone(selected.id, timezone), "Timezone updated")
          }
          onData={async () => {
            try {
              setDetail(await api.userData(selected.id));
            } catch (error: any) {
              notice.show(error.message, "danger");
            }
          }}
          onPassword={() => setPasswordUser(selected)}
          onArchive={() => setArchiving(selected)}
          onRestore={() => setRestoring(selected)}
          onDelete={() => setDeleteUser(selected)}
        />
      )}
      {roleChange && (
        <RoleChangeDialog
          user={roleChange.user}
          role={roleChange.role}
          members={members}
          teamSize={teamOf(roleChange.user.id).length}
          onClose={() => setRoleChange(null)}
          onSave={async (memberId) => {
            const { user, role } = roleChange;
            await perform(async () => {
              await api.userRole(user.id, role);
              if (role === "supporter" && memberId) await api.userMember(user.id, memberId);
            }, "Role updated");
            setRoleChange(null);
          }}
        />
      )}
      {archiving && (
        <ArchiveDialog
          user={archiving}
          team={teamOf(archiving.id)}
          members={members.filter((member) => member.id !== archiving.id)}
          onClose={() => setArchiving(null)}
          onArchive={async (options) => {
            await perform(() => api.archiveUser(archiving.id, options), "User archived");
            setArchiving(null);
            setSelectedId(null);
          }}
        />
      )}
      {restoring && (
        <Confirm
          text={`Restore ${restoring.name || restoring.email}? They can sign in again and show up in lists and dashboards. Their license seat comes back if the license still has room; otherwise they activate again.`}
          onClose={() => setRestoring(null)}
          onConfirm={async () => {
            try {
              const result = await api.restoreUser(restoring.id);
              notice.show(
                result.seats_without_room
                  ? "Restored. The license is full, so they'll need to activate again."
                  : "User restored",
              );
              refresh();
            } catch (error: any) {
              notice.show(error?.message || "Restore failed", "danger");
            }
          }}
        />
      )}
      {detail && <UserDetail data={detail} onClose={() => setDetail(null)} />}
      {passwordUser && (
        <PasswordDialog
          user={passwordUser}
          onClose={() => setPasswordUser(null)}
          onSave={async (password) => {
            await perform(() => api.userPassword(passwordUser.id, password), "Password reset");
            setPasswordUser(null);
          }}
        />
      )}
      {otp && <OtpDialog data={otp} onClose={() => setOtp(null)} />}
      {deleteUser && (
        <Confirm
          text={`Permanently delete ${deleteUser.email}? This also deletes the applications and interviews they logged and cannot be undone. Archiving keeps their data.`}
          onClose={() => setDeleteUser(null)}
          onConfirm={async () => {
            await perform(() => api.deleteUser(deleteUser.id), "User deleted");
            setDeleteUser(null);
            setSelectedId(null);
          }}
        />
      )}
      {rejectSignup && (
        <Confirm
          text={`Reject the signup request for ${rejectSignup.email}?`}
          onClose={() => setRejectSignup(null)}
          onConfirm={async () => {
            await perform(() => api.rejectSignup(rejectSignup.id), "Signup rejected");
            setRejectSignup(null);
          }}
        />
      )}
      {notice.node}
    </>
  );
}

function RoleBadge({ role }: { role: string }) {
  const meta = ROLE_META[role] || ROLE_META.member;
  return (
    <span className="stage-role-badge" style={{ color: meta.color }}>
      <i className={`ti ${meta.icon}`} aria-hidden="true" />
      {role}
    </span>
  );
}

function ProfileCount({ user }: { user: Row }) {
  const count = Number(user.profile_count) || 0;
  return (
    <span className={count ? "" : "text-secondary"}>
      <i className="ti ti-id me-1 text-secondary" aria-hidden="true" />
      {count}
    </span>
  );
}

function LastSeen({ user }: { user: Row }) {
  const summary = deviceSummary(user.devices as Row[]);
  return (
    <span className="d-inline-flex align-items-center gap-2">
      <span className={`stage-status-dot ${summary.online ? "is-online" : "is-offline"}`} />
      <span className="text-secondary small">
        {summary.online ? "Online" : summary.lastSeen ? when(summary.lastSeen) : "Never"}
      </span>
    </span>
  );
}

function PeopleTable({
  rows,
  columns,
  cells,
  empty,
  onOpen,
  compact = false,
}: {
  rows: Row[];
  columns: string[];
  /** Hide the trailing columns on phones (keeps person, role, team). */
  compact?: boolean;
  cells: (user: Row) => ReactNode[];
  empty: string;
  onOpen: (user: Row) => void;
}) {
  return (
    <div className="card">
      <div className="table-responsive">
        <table
          className={`table table-vcenter card-table table-hover mb-0 stage-people-table ${compact ? "is-compact" : ""}`}
        >
          <thead>
            <tr>
              <th>Person</th>
              {columns.map((column, index) => (
                <th key={`${column}-${index}`}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((user) => (
              <PersonRow key={user.id} user={user} onOpen={onOpen}>
                {cells(user).map((cell, index) => (
                  <td key={index}>{cell}</td>
                ))}
              </PersonRow>
            ))}
          </tbody>
        </table>
      </div>
      {!rows.length && <div className="card-body text-secondary">{empty}</div>}
    </div>
  );
}

function PersonRow({
  user,
  onOpen,
  children,
}: {
  user: Row;
  onOpen: (user: Row) => void;
  children: ReactNode;
}) {
  return (
    <tr
      className="cursor-pointer stage-interactive-row"
      tabIndex={0}
      onClick={() => onOpen(user)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpen(user);
        }
      }}
    >
      <td>
        <UserIdentity person={userPerson(user)} size="md" />
      </td>
      {children}
    </tr>
  );
}

/** Supporters grouped under the member they work for; unlinked ones first. */
function SupporterTable({
  supporters,
  members,
  onOpen,
}: {
  supporters: Row[];
  members: Row[];
  onOpen: (user: Row) => void;
}) {
  const groups: { key: string; member: Row | null; rows: Row[] }[] = [];
  const unlinked = supporters.filter((user) => !user.member_name);
  if (unlinked.length) groups.push({ key: "none", member: null, rows: unlinked });
  [...members]
    .sort((a, b) => String(a.name || a.email).localeCompare(String(b.name || b.email)))
    .forEach((member) => {
      const rows = supporters.filter((user) => user.member_id === member.id);
      if (rows.length) groups.push({ key: member.id, member, rows });
    });
  return (
    <div className="card">
      <div className="table-responsive">
        <table className="table table-vcenter card-table table-hover mb-0 stage-people-table is-supporters">
          <thead>
            <tr>
              <th>Person</th>
              <th>Profiles</th>
              <th>Timezone</th>
              <th>Last seen</th>
            </tr>
          </thead>
          {groups.map((group) => (
            <tbody key={group.key}>
              <tr className="stage-people-group">
                <td colSpan={4}>
                  {group.member ? (
                    <span className="d-inline-flex align-items-center gap-2">
                      <span className="text-secondary">Works for</span>
                      <UserIdentity person={userPerson(group.member)} secondary={null} />
                    </span>
                  ) : (
                    <span className="text-warning fw-medium">
                      <i className="ti ti-alert-triangle me-1" />
                      No member yet — open a supporter to link them, so their member can assign them
                    </span>
                  )}
                  <span className="badge bg-secondary-lt ms-2">{group.rows.length}</span>
                </td>
              </tr>
              {group.rows.map((user) => (
                <PersonRow key={user.id} user={user} onOpen={onOpen}>
                  <td>
                    <ProfileCount user={user} />
                  </td>
                  <td>
                    <TimezoneBadge value={user.timezone || "UTC"} showOffset />
                  </td>
                  <td>
                    <LastSeen user={user} />
                  </td>
                </PersonRow>
              ))}
            </tbody>
          ))}
        </table>
      </div>
      {!groups.length && <div className="card-body text-secondary">No supporters match.</div>}
    </div>
  );
}

function PanelSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mb-4">
      <div className="stage-detail-section-label mb-2">{title}</div>
      {children}
    </div>
  );
}

function UserPanel({
  user,
  members,
  team,
  profiles,
  onClose,
  onRole,
  onMember,
  onWorkPlanReviewer,
  onTimezone,
  onData,
  onPassword,
  onArchive,
  onRestore,
  onDelete,
}: {
  user: Row;
  members: Person[];
  team: Row[];
  profiles: { key: string; name: string; archived: boolean }[];
  onClose: () => void;
  onRole: (role: string) => void;
  onMember: (memberId: string) => void;
  onWorkPlanReviewer: (enabled: boolean) => void;
  onTimezone: (timezone: string) => void;
  onData: () => void;
  onPassword: () => void;
  onArchive: () => void;
  onRestore: () => void;
  onDelete: () => void;
}) {
  const isArchived = user.is_active === false;
  const devices = user.devices as Row[];
  return (
    <Modal
      title={user.name || user.email}
      description={user.email}
      variant="sheet"
      size="md"
      onClose={onClose}
      footer={
        isArchived ? (
          <>
            <button className="btn btn-ghost-danger me-auto" onClick={onDelete}>
              Delete permanently
            </button>
            <button className="btn btn-primary" onClick={onRestore}>
              <i className="ti ti-archive-off me-1" />
              Restore
            </button>
          </>
        ) : (
          <>
            <button className="btn btn-outline-danger me-auto" onClick={onArchive}>
              <i className="ti ti-archive me-1" />
              Archive
            </button>
            <button className="btn" onClick={onPassword}>
              <i className="ti ti-key me-1" />
              Reset password
            </button>
            <button className="btn" onClick={onData}>
              <i className="ti ti-database me-1" />
              View data
            </button>
          </>
        )
      }
    >
      <div className="d-flex align-items-center justify-content-between mb-4">
        <UserIdentity person={userPerson(user)} size="lg" secondary={<LastSeen user={user} />} />
        {isArchived && (
          <span className="badge bg-secondary-lt">Archived {when(user.archived_at)}</span>
        )}
      </div>
      <PanelSection title="Role">
        <select
          className="form-select"
          value={user.role}
          disabled={isArchived}
          onChange={(event) => onRole(event.target.value)}
        >
          {["manager", "member", "supporter"].map((role) => (
            <option key={role}>{role}</option>
          ))}
        </select>
      </PanelSection>
      {user.role === "supporter" && (
        <PanelSection title="Works for">
          <PersonSelect
            people={members}
            value={
              user.member_id && members.some((member) => member.id === user.member_id)
                ? user.member_id
                : ""
            }
            onChange={onMember}
            allLabel="No member"
            disabled={isArchived}
            ariaLabel={`Member for ${user.email}`}
          />
          {!user.member_name && (
            <div className="form-hint text-warning mt-1">
              No member can assign this supporter until you link them.
            </div>
          )}
        </PanelSection>
      )}
      {user.role === "member" && (
        <PanelSection title={`Team (${team.length})`}>
          {team.length ? (
            <div className="d-flex flex-column gap-2">
              {team.map((supporter) => (
                <UserIdentity key={supporter.id} person={userPerson(supporter)} />
              ))}
            </div>
          ) : (
            <div className="text-secondary">No supporters yet.</div>
          )}
        </PanelSection>
      )}
      {user.role === "member" && (
        <PanelSection title="Work plans">
          <label className="form-check form-switch mb-0" htmlFor={`wp-reviewer-${user.id}`}>
            <input
              id={`wp-reviewer-${user.id}`}
              className="form-check-input"
              type="checkbox"
              checked={Boolean(user.can_review_work_plans)}
              disabled={isArchived}
              onChange={(event) => onWorkPlanReviewer(event.target.checked)}
            />
            <span className="form-check-label">Work plan reviewer</span>
          </label>
          <div className="form-hint ms-5">
            Can see every member's work plan and comment on it, but can't change it.
          </div>
        </PanelSection>
      )}
      <PanelSection title="Timezone">
        {user.role === "supporter" && !isArchived ? (
          <TimezoneSelect
            value={user.timezone || "UTC"}
            onChange={onTimezone}
            ariaLabel={`Timezone for ${user.email}`}
          />
        ) : (
          // Members' and managers' timezone is captured automatically from
          // their own browser on login (_learn_member_timezone in team.py,
          // _learn_manager_timezone in admin.py) — editing it here would just
          // be overwritten on next login.
          <TimezoneBadge value={user.timezone || "UTC"} showOffset />
        )}
      </PanelSection>
      <PanelSection title={`Profiles (${profiles.length})`}>
        {profiles.length ? (
          <div className="d-flex flex-column gap-1">
            {profiles.map((profile) => (
              <div key={profile.key} className="d-flex align-items-center gap-2">
                <i className="ti ti-file-cv text-secondary" />
                <span>{profile.name}</span>
                {profile.archived && <span className="badge bg-secondary-lt">Archived</span>}
              </div>
            ))}
          </div>
        ) : (
          <div className="text-secondary">Not on any profile.</div>
        )}
      </PanelSection>
      <PanelSection title={`Devices (${devices.length})`}>
        {devices.length ? (
          <div className="d-flex flex-column gap-2">
            {devices.map((device, index) => (
              <div key={device.id || index} className="small">
                <i className="ti ti-device-desktop me-1" />
                <span className="fw-medium">
                  {device.device_name || device.hostname || "Device"}
                </span>
                <span className="text-secondary">
                  {" "}
                  · {device.os_name || "Unknown OS"} · v{device.app_version || "—"} · last seen{" "}
                  {when(device.last_seen)}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-secondary">No devices registered.</div>
        )}
      </PanelSection>
    </Modal>
  );
}

function ArchiveDialog({
  user,
  team,
  members,
  onClose,
  onArchive,
}: {
  user: Row;
  team: Row[];
  members: Person[];
  onClose: () => void;
  onArchive: (options: { supporters: string; move_to: string | null }) => Promise<void>;
}) {
  const [choice, setChoice] = useState(team.length ? "move" : "unlink");
  const [moveTo, setMoveTo] = useState("");
  const [busy, setBusy] = useState(false);
  const blocked = choice === "move" && !moveTo;
  const options: [string, string][] = [
    ["move", "Move them to another member"],
    ["archive", "Archive them too"],
    ["unlink", "Leave them without a member"],
  ];
  return (
    <Modal
      title={`Archive ${user.name || user.email}?`}
      size="md"
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            className="btn btn-danger"
            disabled={busy || blocked}
            onClick={async () => {
              setBusy(true);
              try {
                await onArchive({ supporters: choice, move_to: moveTo || null });
              } finally {
                setBusy(false);
              }
            }}
          >
            Archive
          </button>
        </>
      }
    >
      <ul className="mb-3 ps-3">
        <li>They can't sign in, and their license seat is freed.</li>
        <li>They disappear from people lists, dashboards and rankings.</li>
        <li>Applications and interviews they logged are kept and still count in totals.</li>
        <li>You can restore them from the Archived tab.</li>
      </ul>
      {team.length > 0 && (
        <>
          <div className="fw-medium mb-2">
            {team.length} supporter{team.length === 1 ? "" : "s"} work
            {team.length === 1 ? "s" : ""} for them:
          </div>
          <div className="mb-2">
            <AvatarGroup people={team.map(userPerson)} limit={8} />
          </div>
          {options.map(([value, label]) => (
            <label className="form-check" key={value}>
              <input
                className="form-check-input"
                type="radio"
                name="supporters"
                checked={choice === value}
                onChange={() => setChoice(value)}
              />
              <span className="form-check-label">{label}</span>
            </label>
          ))}
          {choice === "move" && (
            <div className="mt-2 ms-4">
              <PersonSelect
                people={members}
                value={moveTo}
                onChange={setMoveTo}
                allLabel="Choose a member"
                ariaLabel="Member to move supporters to"
              />
            </div>
          )}
        </>
      )}
    </Modal>
  );
}

function RoleChangeDialog({
  user,
  role,
  members,
  teamSize,
  onClose,
  onSave,
}: {
  user: Row;
  role: string;
  members: Person[];
  teamSize: number;
  onClose: () => void;
  onSave: (memberId: string) => Promise<void>;
}) {
  const [memberId, setMemberId] = useState("");
  const [busy, setBusy] = useState(false);
  const toSupporter = role === "supporter";
  return (
    <Modal
      title={`Change role · ${user.name || user.email}`}
      onClose={onClose}
      size="md"
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await onSave(memberId);
              } finally {
                setBusy(false);
              }
            }}
          >
            Make {role}
          </button>
        </>
      }
    >
      {user.role === "member" && teamSize > 0 && (
        <div className="alert alert-warning">
          {teamSize} supporter{teamSize === 1 ? "" : "s"} work{teamSize === 1 ? "s" : ""} for{" "}
          {user.name || user.email}. They'll be left without a member until you link them to someone
          else.
        </div>
      )}
      {toSupporter && (
        <>
          <label className="form-label">Works for</label>
          <PersonSelect
            people={members.filter((member) => member.id !== user.id)}
            value={memberId}
            onChange={setMemberId}
            allLabel="Choose later"
            ariaLabel="Member this supporter works for"
          />
          <div className="form-hint mt-2">
            That member can then assign this supporter to their own profiles.
          </div>
        </>
      )}
    </Modal>
  );
}

function SignupPanel({
  rows,
  onOtp,
  onReject,
}: {
  rows: Row[];
  onOtp: (row: Row) => void;
  onReject: (row: Row) => void;
}) {
  return (
    <div className="card mb-3">
      <div className="card-header">
        <div>
          <h3 className="card-title mb-0">Pending signups</h3>
          <div className="text-secondary small">
            Issue an OTP, then send it to the user manually.
          </div>
        </div>
        <span className="badge bg-secondary-lt ms-auto">{rows.length}</span>
      </div>
      <div className="table-responsive">
        <table className="table table-vcenter card-table table-sm mb-0">
          <thead>
            <tr>
              <th>Email</th>
              <th>Name</th>
              <th>Status</th>
              <th>Requested</th>
              <th>OTP expires</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.length ? (
              rows.map((row) => (
                <tr key={row.id}>
                  <td>{row.email}</td>
                  <td>{row.name || "—"}</td>
                  <td>
                    <span
                      className={`badge ${row.status === "otp_issued" ? "bg-success-lt text-success" : "bg-secondary-lt"}`}
                    >
                      {row.status || "pending"}
                    </span>
                  </td>
                  <td className="text-secondary">{when(row.created_at)}</td>
                  <td className="text-secondary">{when(row.otp_expires_at)}</td>
                  <td>
                    <div className="btn-list flex-nowrap">
                      <button className="btn btn-sm btn-outline-primary" onClick={() => onOtp(row)}>
                        Issue OTP
                      </button>
                      <button
                        className="btn btn-sm btn-outline-danger"
                        onClick={() => onReject(row)}
                      >
                        Reject
                      </button>
                    </div>
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={6} className="text-secondary">
                  No pending signups
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PasswordDialog({
  user,
  onClose,
  onSave,
}: {
  user: Row;
  onClose: () => void;
  onSave: (password: string) => Promise<void>;
}) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget),
      password = String(form.get("password")),
      confirm = String(form.get("confirm"));
    if (password.length < 8) return setError("Password must be at least 8 characters.");
    if (password !== confirm) return setError("Passwords do not match.");
    setBusy(true);
    try {
      await onSave(password);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={`Reset password · ${user.email}`} onClose={onClose}>
      <form onSubmit={submit}>
        {error && <div className="alert alert-danger">{error}</div>}
        <label className="form-label">New password</label>
        <input
          className="form-control mb-3"
          type="password"
          name="password"
          minLength={8}
          required
          autoFocus
        />
        <label className="form-label">Confirm password</label>
        <input
          className="form-control mb-3"
          type="password"
          name="confirm"
          minLength={8}
          required
        />
        <div className="text-end">
          <button className="btn btn-primary" disabled={busy}>
            Reset password
          </button>
        </div>
      </form>
    </Modal>
  );
}
function OtpDialog({ data, onClose }: { data: Row; onClose: () => void }) {
  const code = String(data.otp || data.code || "issued");
  return (
    <Modal title="Signup OTP" onClose={onClose}>
      <p className="text-secondary">
        Send this one-time code to <strong>{data.email}</strong>. It is only displayed here once.
      </p>
      <div className="input-group">
        <input className="form-control font-monospace fs-2 text-center" readOnly value={code} />
        <button
          className="btn btn-outline-primary"
          onClick={() => navigator.clipboard.writeText(code)}
        >
          <i className="ti ti-copy me-1" />
          Copy
        </button>
      </div>
      {data.otp_expires_at && (
        <div className="text-secondary small mt-2">Expires {when(data.otp_expires_at)}</div>
      )}
    </Modal>
  );
}
function UserDetail({ data, onClose }: { data: Row; onClose: () => void }) {
  const user = data.user || data,
    applications = arr(data.applications),
    interviews = arr(data.interviews);
  return (
    <Modal title={`${user.name || user.email || "User"} data`} onClose={onClose}>
      <div className="mb-4">
        <UserIdentity person={user} size="lg" secondary={user.role} />
      </div>
      <div className="row g-3 mb-4">
        <Stat label="Applications" value={data.application_count ?? applications.length} />
        <Stat label="Interviews" value={data.interview_count ?? interviews.length} />
        <Stat label="Profiles" value={arr(data.profiles).length} />
      </div>
      <div className="stage-detail-section-label">Recent activity</div>
      {applications.length ? (
        <div className="table-responsive">
          <table className="table table-sm">
            <tbody>
              {applications.slice(0, 10).map((item, index) => (
                <tr key={item.id || index}>
                  <td>{item.company_name || "Application"}</td>
                  <td>{item.position_title}</td>
                  <td className="text-secondary">{when(item.applied_date)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-secondary">No application data.</p>
      )}
    </Modal>
  );
}
function Stat({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="col-4">
      <div className="border rounded p-3 text-center">
        <div className="h2 mb-0">{Number(value) || 0}</div>
        <div className="text-secondary small">{label}</div>
      </div>
    </div>
  );
}

function devicesByEmail(rows: Row[]) {
  const map = new Map<string, Row[]>();
  rows.forEach((device) => {
    const key = String(device.user_email || "")
      .trim()
      .toLowerCase();
    if (!key) return;
    const list = map.get(key) || [];
    list.push(device);
    map.set(key, list);
  });
  map.forEach((list) =>
    list.sort((a, b) => String(b.last_seen || "").localeCompare(String(a.last_seen || ""))),
  );
  return map;
}
function deviceSummary(devices: Row[]) {
  return {
    count: devices.length,
    online: devices.some((device) => device.online),
    lastSeen: devices[0]?.last_seen || null,
  };
}
function normalizeRole(value: unknown) {
  const role = String(value || "").toLowerCase();
  return role === "user" ? "member" : role || "member";
}
