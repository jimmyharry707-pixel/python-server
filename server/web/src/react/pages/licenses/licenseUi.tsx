import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, type Row } from "../../api";
import { Confirm, useNotice } from "../../ui";
import { Avatar, AvatarGroup, userPerson } from "../../components/Avatar";

/* ---------- Time ---------- */

export function timeZoneLabel() {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const offset = new Intl.DateTimeFormat(undefined, { timeZoneName: "shortOffset" })
      .formatToParts(new Date())
      .find((part) => part.type === "timeZoneName")?.value;
    return [zone, offset && `(${offset})`].filter(Boolean).join(" ");
  } catch {
    return "your time zone";
  }
}

function parse(value: unknown) {
  if (!value) return null;
  const date = new Date(String(value));
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Exact local time with the zone, e.g. "Oct 29, 2026, 3:00 PM GMT+8". */
export function exact(value: unknown, fallback = "—") {
  const date = parse(value);
  if (!date) return fallback;
  // dateStyle/timeStyle can't be combined with timeZoneName (RangeError).
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
}

export function shortDate(value: unknown, fallback = "—") {
  const date = parse(value);
  return date ? date.toLocaleDateString(undefined, { dateStyle: "medium" }) : fallback;
}

/** "just now", "5 min ago", "3 h ago", "2 days ago", or "in 4 days". */
export function relative(value: unknown, fallback = "Never") {
  const date = parse(value);
  if (!date) return fallback;
  const diff = date.getTime() - Date.now();
  const past = diff < 0;
  const minutes = Math.round(Math.abs(diff) / 60000);
  let text: string;
  if (minutes < 1) return "just now";
  if (minutes < 60) text = `${minutes} min`;
  else if (minutes < 60 * 24) text = `${Math.round(minutes / 60)} h`;
  else {
    const days = Math.round(minutes / (60 * 24));
    text = `${days} day${days === 1 ? "" : "s"}`;
  }
  return past ? `${text} ago` : `in ${text}`;
}

export function Ago({ value, fallback = "Never" }: { value: unknown; fallback?: string }) {
  return <span title={exact(value, "")}>{relative(value, fallback)}</span>;
}

/** "Expires in 12 days", "Expired 3 days ago", "Never expires". */
export function expiryText(license: Row) {
  if (!license.expires_at) return "Never expires";
  const date = parse(license.expires_at);
  if (!date) return "";
  const hours = (date.getTime() - Date.now()) / 3600000;
  if (Math.abs(hours) < 24) return hours > 0 ? "Expires today" : "Expired today";
  const days = Number(license.days_left);
  if (Number.isFinite(days) && days !== 0)
    return days > 0
      ? `Expires in ${days} day${days === 1 ? "" : "s"}`
      : `Expired ${-days} day${days === -1 ? "" : "s"} ago`;
  return hours > 0 ? `Expires ${relative(date)}` : `Expired ${relative(date)}`;
}

/** ISO (UTC) -> value for <input type="datetime-local"> in the viewer's zone. */
export function toLocalInput(value: unknown) {
  const date = parse(value);
  if (!date) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function fromLocalInput(value: string) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

export function useDebounced<T>(value: T, delay = 300) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

/* ---------- Badges ---------- */

const licenseStatus: Record<string, [string, string, string]> = {
  active: ["Active", "bg-success-lt text-success", "ti-circle-check"],
  expiring: ["Expiring soon", "bg-warning-lt text-warning", "ti-clock-exclamation"],
  expired: ["Expired", "bg-danger-lt text-danger", "ti-clock-x"],
  suspended: ["Suspended", "bg-secondary-lt text-secondary", "ti-player-pause"],
  unused: ["Not used yet", "bg-azure-lt text-azure", "ti-circle-dashed"],
};

export function LicenseStatus({ status }: { status?: string | null }) {
  const [label, tone, icon] = licenseStatus[String(status)] || [
    status || "Unknown",
    "bg-secondary-lt",
    "ti-help",
  ];
  return (
    <span className={`badge ${tone} stage-lic-badge`}>
      <i className={`ti ${icon}`} aria-hidden="true" />
      {label}
    </span>
  );
}

const machineState: Record<string, [string, string, string]> = {
  active: ["Active", "bg-success-lt text-success", "ti-device-desktop-check"],
  released: ["Released", "bg-secondary-lt text-secondary", "ti-device-desktop-off"],
  blocked: ["Blocked", "bg-danger-lt text-danger", "ti-ban"],
};

export function MachineState({ state }: { state?: string | null }) {
  const [label, tone, icon] = machineState[String(state)] || machineState.released;
  return (
    <span className={`badge ${tone} stage-lic-badge`}>
      <i className={`ti ${icon}`} aria-hidden="true" />
      {label}
    </span>
  );
}

const productTone: Record<string, string> = {
  careercraft: "bg-blue-lt text-blue",
  sidekick: "bg-purple-lt text-purple",
  snowlink: "bg-cyan-lt text-cyan",
};

export function ProductBadge({ id, name }: { id?: string | null; name?: string | null }) {
  return (
    <span className={`badge ${productTone[String(id)] || "bg-secondary-lt"} stage-lic-badge`}>
      {name || id || "Unknown"}
    </span>
  );
}

export function OnlineDot({ machine }: { machine: Row }) {
  if (!machine.tracks_online) return null;
  return (
    <span
      className={`stage-status-dot ${machine.online ? "is-online" : "is-offline"}`}
      title={machine.online ? "Online now" : "Offline"}
      aria-label={machine.online ? "Online now" : "Offline"}
    />
  );
}

export function keyPrefix(license: Row) {
  return `${license.key_prefix || license.license_key_prefix || "?"}-****`;
}

/* A key's color. The server picks it when the key is issued, so one customer's
   keys differ (server/key_colors.py), and the same key has the same color on
   every tab. Same list, same order as the server's 10 colors; neighbors differ
   because a customer's keys take them in turn. */
const keyColors = [
  "blue",
  "orange",
  "green",
  "purple",
  "red",
  "teal",
  "yellow",
  "pink",
  "indigo",
  "lime",
];

export function keyColor(index: unknown) {
  const value = Number(index);
  if (index === null || index === undefined || !Number.isInteger(value) || value < 0)
    return "var(--tblr-border-color)";
  return `var(--tblr-${keyColors[value % keyColors.length]})`;
}

/** The key's prefix with its color swatch, e.g. "■ 3F9A1-****". */
export function KeyTag({ row, prefix }: { row?: Row; prefix?: string }) {
  const value = String(prefix || row?.key_prefix || row?.license_key_prefix || "");
  const color = row?.color ?? row?.license_color;
  return (
    <span
      className="stage-lic-key"
      title="The key's first part. Its color marks the same key on every tab; one customer's keys have different colors."
    >
      <span
        className="stage-lic-key-swatch"
        style={{ background: keyColor(color) }}
        aria-hidden="true"
      />
      <span className="font-monospace">{value || "?"}-****</span>
    </span>
  );
}

/** Who uses the machine now: the account (CareerCraft) or the Windows user. */
export function currentUserLabel(machine: Row) {
  const people = (machine.users as Row[]) || [];
  const current = people.find((person) => person.current);
  if (current) return personLabel(current);
  return machine.user_name || machine.user_email || machine.os_user || "";
}

/** Everything about a key's machines that the Keys search should match. */
export function machineSearchText(machine: Row) {
  return [
    machine.hostname,
    machine.os_user,
    machine.user_name,
    machine.user_email,
    machine.last_ip,
    ...((machine.users as Row[]) || []).flatMap((person) => [
      person.name,
      person.email,
      person.os_user,
    ]),
  ]
    .filter(Boolean)
    .join(" ");
}

/** The machines using a key now, each as "PC · who uses it". */
export function KeyMachines({ license, limit = 2 }: { license: Row; limit?: number }) {
  const machines = ((license.seats as Row[]) || []).filter((machine) => machine.state === "active");
  if (!machines.length)
    return (
      <span className="text-secondary">{Number(license.machines_total) ? "No PC now" : "—"}</span>
    );
  return (
    <div className="stage-lic-used-by">
      {machines.slice(0, limit).map((machine) => {
        const who = currentUserLabel(machine);
        return (
          <div
            key={machine.id}
            className="stage-lic-used-by-row"
            title={[machine.hostname || "Unnamed PC", who, machine.last_ip]
              .filter(Boolean)
              .join(" · ")}
          >
            <i className="ti ti-device-desktop text-secondary me-1" aria-hidden="true" />
            <span className="fw-medium">{machine.hostname || "Unnamed PC"}</span>
            {who && <span className="text-secondary"> · {who}</span>}
          </div>
        );
      })}
      {machines.length > limit && (
        <div className="text-secondary small">+{machines.length - limit} more</div>
      )}
    </div>
  );
}

export function licenseName(license: Row) {
  return license.label || license.license_label || keyPrefix(license);
}

export function windowsText(machine: Row) {
  return [machine.os_name, machine.os_version].filter(Boolean).join(" · ") || "Unknown system";
}

export function shortId(value: unknown, size = 12) {
  const text = String(value || "");
  return text ? `${text.slice(0, size)}${text.length > size ? "…" : ""}` : "—";
}

/* ---------- People on a machine ---------- */

function personLabel(person: Row) {
  if (person.user_id)
    return person.name || person.email || (person.deleted ? "Deleted user" : person.user_id);
  return person.os_user || "Unknown";
}

/** Current user first: accounts as avatars, Windows user names as text. */
export function MachineUsers({ machine, limit = 4 }: { machine: Row; limit?: number }) {
  const people = [...((machine.users as Row[]) || [])].sort(
    (a, b) => Number(!!b.current) - Number(!!a.current),
  );
  if (!people.length) {
    const fallback = machine.user_name || machine.user_email || machine.os_user;
    return <span className="text-secondary">{fallback || "—"}</span>;
  }
  const accounts = people.filter((person) => person.user_id);
  const windowsUsers = people.filter((person) => !person.user_id);
  return (
    <span className="d-inline-flex align-items-center gap-2 flex-wrap">
      {accounts.length > 0 && (
        <AvatarGroup
          people={accounts.map((person) =>
            userPerson({ ...person, id: person.user_id, name: personLabel(person) }),
          )}
          limit={limit}
        />
      )}
      {windowsUsers.slice(0, limit).map((person, index) => (
        <span
          key={`${person.os_user}-${index}`}
          className={`stage-lic-os-user${person.current ? " is-current" : ""}`}
          title={person.current ? "Current Windows user" : "Windows user seen before"}
        >
          <i className="ti ti-user" aria-hidden="true" />
          {person.os_user}
        </span>
      ))}
    </span>
  );
}

export function MachineUserList({ machine }: { machine: Row }) {
  const people = [...((machine.users as Row[]) || [])].sort(
    (a, b) => Number(!!b.current) - Number(!!a.current),
  );
  if (!people.length)
    return <div className="stage-detail-profile text-secondary">No users recorded yet.</div>;
  return (
    <>
      {people.map((person, index) => (
        <div className="stage-detail-profile" key={`${person.user_id || person.os_user}-${index}`}>
          {person.user_id ? (
            <Avatar
              person={userPerson({ ...person, id: person.user_id, name: personLabel(person) })}
              size="sm"
            />
          ) : (
            <span className="avatar avatar-sm bg-secondary-lt">
              <i className="ti ti-user" aria-hidden="true" />
            </span>
          )}
          <div className="min-w-0 flex-fill">
            <div className="fw-medium text-truncate">
              {personLabel(person)}
              {person.current && <span className="badge bg-primary-lt ms-2">Current</span>}
            </div>
            <div className="text-secondary small">
              {person.user_id
                ? [person.email !== personLabel(person) && person.email, person.os_user]
                    .filter(Boolean)
                    .join(" · ") || "Account"
                : "Windows user"}
            </div>
          </div>
          <div className="text-secondary small text-end">
            <div>
              Last seen <Ago value={person.last_seen} />
            </div>
            <div>First seen {shortDate(person.first_seen)}</div>
          </div>
        </div>
      ))}
    </>
  );
}

/* ---------- Events ---------- */

export const eventKinds: Record<string, [string, string, string]> = {
  activate: ["Activated", "ti-plug-connected", "text-success"],
  activate_denied: ["Activation refused", "ti-plug-x", "text-danger"],
  refresh: ["Renewed", "ti-refresh", "text-secondary"],
  refresh_denied: ["Renewal refused", "ti-refresh-alert", "text-danger"],
  create: ["Key issued", "ti-key", "text-primary"],
  update: ["Key edited", "ti-pencil", "text-primary"],
  extend: ["Expiry extended", "ti-calendar-plus", "text-primary"],
  suspend: ["Suspended", "ti-player-pause", "text-warning"],
  deactivate: ["Suspended", "ti-player-pause", "text-warning"],
  resume: ["Resumed", "ti-player-play", "text-success"],
  reactivate: ["Resumed", "ti-player-play", "text-success"],
  delete: ["Key deleted", "ti-trash", "text-danger"],
  release_seat: ["Seat released", "ti-device-desktop-off", "text-secondary"],
  revoke_seat: ["Seat released", "ti-device-desktop-off", "text-secondary"],
  block_seat: ["Machine blocked", "ti-ban", "text-danger"],
  restore_seat: ["Seat restored", "ti-device-desktop-check", "text-success"],
};

export const eventFilterOptions: [string, string][] = [
  ["", "All events"],
  ["activate", "Activated"],
  ["activate_denied", "Activation refused"],
  ["refresh", "Renewed"],
  ["refresh_denied", "Renewal refused"],
  ["create", "Key issued"],
  ["update", "Key edited"],
  ["extend", "Expiry extended"],
  ["suspend", "Suspended"],
  ["resume", "Resumed"],
  ["release_seat", "Seat released"],
  ["block_seat", "Machine blocked"],
  ["delete", "Key deleted"],
];

export function EventLabel({ event }: { event: string }) {
  const [label, icon, tone] = eventKinds[event] || [event, "ti-point", "text-secondary"];
  return (
    <span className="d-inline-flex align-items-center gap-1 text-nowrap">
      <i className={`ti ${icon} ${tone}`} aria-hidden="true" />
      {label}
    </span>
  );
}

const codeLabels: Record<string, string> = {
  unknown_key: "Unknown key",
  wrong_product: "Key is for another app",
  key_required: "No key entered",
  invalid_fingerprint: "Bad machine id",
  suspended: "Key suspended",
  expired: "Key expired",
  seat_limit: "No free seat",
  seat_blocked: "Machine is blocked",
  seat_released: "Seat was released",
  not_activated: "Not activated",
  fingerprint_mismatch: "Different machine",
};

const fieldLabels: Record<string, string> = {
  label: "Label",
  customer: "Customer",
  notes: "Notes",
  plan: "Plan",
  seat_limit: "Seat limit",
  grace_days: "Offline days",
  expires_at: "Expiry",
};

function changeValue(field: string, value: unknown) {
  if (value === null || value === undefined || value === "")
    return field === "expires_at" ? "never" : field === "grace_days" ? "default" : "empty";
  if (field === "expires_at") return shortDate(value);
  return String(value);
}

/** One line saying what happened, from an event's detail. */
export function eventSummary(event: Row) {
  const detail = event.detail;
  if (!detail || typeof detail !== "object") return detail ? String(detail) : "";
  if (event.event === "update")
    return Object.entries(detail as Record<string, Row>)
      .map(
        ([field, change]) =>
          `${fieldLabels[field] || field}: ${changeValue(field, change?.from)} → ${changeValue(field, change?.to)}`,
      )
      .join("; ");
  if (event.event === "extend") return `+${detail.days} days, until ${shortDate(detail.to)}`;
  if (event.event === "create")
    return [
      detail.seat_limit && `${detail.seat_limit} seat(s)`,
      detail.expires_at ? `expires ${shortDate(detail.expires_at)}` : "never expires",
    ]
      .filter(Boolean)
      .join(", ");
  if (event.event === "delete") {
    const removed = `${detail.seats_deleted || 0} machine record(s) removed`;
    return detail.machines_signed_out
      ? `${removed}, ${detail.machines_signed_out} still activated`
      : removed;
  }
  const parts: string[] = [];
  if (detail.reason) parts.push(String(detail.reason));
  else if (detail.code) parts.push(codeLabels[detail.code] || detail.code);
  if (detail.by) parts.push(detail.by === "app" ? "by the app" : "by an admin");
  if (detail.reactivated) parts.push("activated again");
  return parts.join(" · ");
}

/* ---------- Data refresh + machine actions ---------- */

export function useLicenseRefresh() {
  const queryClient = useQueryClient();
  return () =>
    queryClient.invalidateQueries({
      predicate: (query) => String(query.queryKey[0]).startsWith("license"),
    });
}

type SeatAction = { machine: Row; action: "release" | "block" | "unblock" };

const seatActionText: Record<SeatAction["action"], (host: string) => string> = {
  release: (host) =>
    `Release the seat of ${host}? The seat is freed. This PC can activate again later if a seat is free.`,
  block: (host) =>
    `Block ${host}? Its seat is freed and this PC can't use this key until you unblock it.`,
  unblock: (host) =>
    `Unblock ${host}? It stays released: the PC can activate again with the key if a seat is free.`,
};

const seatActionDone: Record<SeatAction["action"], string> = {
  release: "Seat released",
  block: "Machine blocked",
  unblock: "Machine unblocked",
};

/** Release / Block / Unblock with a confirm dialog. Render `node` once. */
export function useSeatActions(onDone?: (machine: Row) => void) {
  const notice = useNotice();
  const refresh = useLicenseRefresh();
  const [pending, setPending] = useState<SeatAction | null>(null);
  const node = pending && (
    <Confirm
      text={seatActionText[pending.action](pending.machine.hostname || "this machine")}
      onClose={() => setPending(null)}
      onConfirm={async () => {
        try {
          const updated =
            pending.action === "block"
              ? await api.blockSeat(pending.machine.id)
              : await api.releaseSeat(pending.machine.id);
          notice.show(seatActionDone[pending.action]);
          await refresh();
          onDone?.(updated);
        } catch (error: any) {
          notice.show(error?.message || "Could not change the machine", "danger");
        }
      }}
    />
  );
  return {
    ask: (machine: Row, action: SeatAction["action"]) => setPending({ machine, action }),
    node,
  };
}

export function seatMenuItems(
  machine: Row,
  ask: (machine: Row, action: SeatAction["action"]) => void,
) {
  const items = [];
  if (machine.state === "active")
    items.push({
      label: "Release seat",
      icon: "ti-device-desktop-off",
      onSelect: () => ask(machine, "release"),
    });
  if (machine.state === "blocked")
    items.push({
      label: "Unblock",
      icon: "ti-lock-open",
      onSelect: () => ask(machine, "unblock"),
    });
  else
    items.push({
      label: "Block machine",
      icon: "ti-ban",
      danger: true,
      separatorBefore: items.length > 0,
      onSelect: () => ask(machine, "block"),
    });
  return items;
}

export function SeatButtons({
  machine,
  ask,
}: {
  machine: Row;
  ask: (machine: Row, action: SeatAction["action"]) => void;
}) {
  return (
    <div className="d-flex gap-2 flex-wrap">
      {machine.state === "active" && (
        <button type="button" className="btn btn-sm" onClick={() => ask(machine, "release")}>
          <i className="ti ti-device-desktop-off me-1" />
          Release seat
        </button>
      )}
      {machine.state === "blocked" ? (
        <button type="button" className="btn btn-sm" onClick={() => ask(machine, "unblock")}>
          <i className="ti ti-lock-open me-1" />
          Unblock
        </button>
      ) : (
        <button
          type="button"
          className="btn btn-sm btn-ghost-danger"
          onClick={() => ask(machine, "block")}
        >
          <i className="ti ti-ban me-1" />
          Block machine
        </button>
      )}
    </div>
  );
}
