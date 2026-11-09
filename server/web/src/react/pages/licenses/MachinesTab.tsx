import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Row } from "../../api";
import { ErrorBox, Loading, Modal } from "../../ui";
import { ActionMenu } from "../../components/primitives";
import { RecentEvents } from "./EventsTab";
import {
  Ago,
  KeyTag,
  LicenseStatus,
  MachineState,
  MachineUserList,
  MachineUsers,
  OnlineDot,
  ProductBadge,
  SeatButtons,
  exact,
  shortId,
  useDebounced,
  useSeatActions,
  seatMenuItems,
  windowsText,
} from "./licenseUi";

const stateFilters: [string, string][] = [
  ["active", "Active"],
  ["released", "Released"],
  ["blocked", "Blocked"],
  ["", "All"],
];

export function MachinesTab({ product }: { product: string }) {
  const [state, setState] = useState("active");
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim());
  const [openId, setOpenId] = useState<number | null>(null);
  // Kept so the panel stays open when an action moves the machine out of the filter.
  const [openRow, setOpenRow] = useState<Row | null>(null);
  const machines = useQuery({
    queryKey: ["license-machines", product, state, q],
    queryFn: () =>
      api.licenseMachines({ product: product || undefined, state: state || undefined, q }),
    placeholderData: (previous) => previous,
  });
  const seat = useSeatActions((updated) => setOpenRow(updated));
  const rows = machines.data || [];
  const open =
    openId === null
      ? null
      : rows.find((row) => row.id === openId) || (openRow?.id === openId ? openRow : null);
  const show = (machine: Row) => {
    setOpenRow(machine);
    setOpenId(machine.id);
  };

  return (
    <>
      <div className="card">
        <div className="card-header stage-panel-header">
          <div>
            <h3 className="card-title mb-0">Machines</h3>
            <div className="text-secondary small">
              Every PC that has used a key. Online status is shown for CareerCraft only.
            </div>
          </div>
          <div className="d-flex gap-2 flex-wrap">
            <div className="btn-group btn-group-sm" role="group" aria-label="Machine state">
              {stateFilters.map(([value, label]) => (
                <button
                  key={value || "all"}
                  type="button"
                  className={`btn ${state === value ? "btn-primary" : "btn-outline-secondary"}`}
                  onClick={() => setState(value)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="input-icon stage-lic-search">
              <span className="input-icon-addon">
                <i className="ti ti-search" />
              </span>
              <input
                className="form-control"
                value={search}
                aria-label="Search machines"
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search PC, user, IP, key…"
              />
            </div>
          </div>
        </div>
        {machines.isLoading ? (
          <div className="card-body">
            <Loading />
          </div>
        ) : machines.error ? (
          <div className="card-body">
            <ErrorBox error={machines.error} />
          </div>
        ) : (
          <div className="table-responsive">
            <table className="table table-vcenter card-table table-sm table-hover stage-lic-table">
              <thead>
                <tr>
                  <th>Machine</th>
                  <th className="d-none d-sm-table-cell">App</th>
                  <th className="d-none d-lg-table-cell">Key</th>
                  <th>Users</th>
                  <th>Last IP</th>
                  <th>Last seen</th>
                  <th className="d-none d-xl-table-cell">Activated</th>
                  <th>State</th>
                  <th className="w-1" />
                </tr>
              </thead>
              <tbody>
                {rows.map((machine) => (
                  <tr
                    key={machine.id}
                    className="cursor-pointer stage-interactive-row"
                    tabIndex={0}
                    onClick={() => show(machine)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        show(machine);
                      }
                    }}
                  >
                    <td>
                      <div className="fw-medium text-nowrap">
                        {machine.hostname || <span className="text-secondary">Unnamed PC</span>}
                      </div>
                      <div className="text-secondary small text-nowrap">{windowsText(machine)}</div>
                      <div className="d-sm-none mt-1">
                        <ProductBadge id={machine.product} name={machine.product_name} />
                        <span className="text-secondary small ms-1">
                          {machine.app_version ? `v${machine.app_version}` : ""}
                        </span>
                      </div>
                    </td>
                    <td className="text-nowrap d-none d-sm-table-cell">
                      <ProductBadge id={machine.product} name={machine.product_name} />
                      <div className="text-secondary small">
                        {machine.app_version ? `v${machine.app_version}` : "—"}
                      </div>
                    </td>
                    <td className="d-none d-lg-table-cell">
                      <KeyTag row={machine} />
                      <div className="text-secondary small text-truncate stage-lic-cell-max">
                        {[machine.license_label, machine.license_customer]
                          .filter(Boolean)
                          .join(" · ") || "No label"}
                      </div>
                    </td>
                    <td>
                      <MachineUsers machine={machine} />
                    </td>
                    <td className="font-monospace small text-nowrap">{machine.last_ip || "—"}</td>
                    <td className="text-nowrap">
                      <span className="d-inline-flex align-items-center gap-2">
                        <OnlineDot machine={machine} />
                        {machine.online ? (
                          <span title={exact(machine.last_seen_at)}>Online now</span>
                        ) : (
                          <Ago value={machine.last_seen_at} />
                        )}
                      </span>
                    </td>
                    <td className="d-none d-xl-table-cell text-nowrap text-secondary">
                      <Ago value={machine.activated_at} />
                    </td>
                    <td>
                      <MachineState state={machine.state} />
                    </td>
                    <td onClick={(event) => event.stopPropagation()}>
                      <ActionMenu
                        label={`Actions for ${machine.hostname || "machine"}`}
                        items={[
                          {
                            label: "View details",
                            icon: "ti-eye",
                            onSelect: () => show(machine),
                          },
                          ...seatMenuItems(machine, seat.ask).map((item, index) => ({
                            ...item,
                            separatorBefore: index === 0 || item.separatorBefore,
                          })),
                        ]}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!rows.length && (
              <div className="card-body text-center text-secondary">
                {q || state ? "No machines match." : "No machine has used a key yet."}
              </div>
            )}
          </div>
        )}
      </div>
      {open && <MachineDetail machine={open} onClose={() => setOpenId(null)} ask={seat.ask} />}
      {seat.node}
    </>
  );
}

export function MachineDetail({
  machine,
  onClose,
  ask,
}: {
  machine: Row;
  onClose: () => void;
  ask: ReturnType<typeof useSeatActions>["ask"];
}) {
  const facts: [string, ReactNode][] = [
    ["Windows", windowsText(machine)],
    [
      "App",
      <>
        {machine.product_name}
        {machine.app_version ? ` v${machine.app_version}` : ""}
      </>,
    ],
    [
      "Key",
      <>
        <KeyTag row={machine} /> {machine.license_label || machine.license_customer || ""}{" "}
        {machine.license_status && <LicenseStatus status={machine.license_status} />}
      </>,
    ],
    ["Last IP", <span className="font-monospace">{machine.last_ip || "—"}</span>],
    [
      "Last seen",
      <>
        {machine.online ? "Online now" : <Ago value={machine.last_seen_at} />}
        <div className="text-secondary small">{exact(machine.last_seen_at, "")}</div>
      </>,
    ],
    [
      "Activated",
      <>
        <Ago value={machine.activated_at} />
        <div className="text-secondary small">{exact(machine.activated_at, "")}</div>
      </>,
    ],
    [
      "Offline pass valid until",
      machine.state === "active" ? exact(machine.lease_expires_at) : "—",
    ],
    [
      "Machine id",
      <span className="font-monospace" title={machine.hardware_fingerprint}>
        {shortId(machine.hardware_fingerprint, 16)}
      </span>,
    ],
  ];
  // Apps without a separate install id send the machine id twice.
  if (machine.device_id && machine.device_id !== machine.hardware_fingerprint)
    facts.push([
      "Device id",
      <span className="font-monospace" title={machine.device_id}>
        {shortId(machine.device_id, 16)}
      </span>,
    ]);
  if (machine.state !== "active")
    facts.push([
      machine.state === "blocked" ? "Blocked" : "Released",
      <>
        {exact(machine.revoked_at)}
        {machine.revoked_reason && (
          <div className="text-secondary small">{reasonText(machine.revoked_reason)}</div>
        )}
      </>,
    ]);

  return (
    <Modal
      title={machine.hostname || "Unnamed PC"}
      description={windowsText(machine)}
      variant="sheet"
      size="md"
      onClose={onClose}
      footer={<SeatButtons machine={machine} ask={ask} />}
    >
      <div className="d-flex gap-2 flex-wrap mb-3">
        <MachineState state={machine.state} />
        <ProductBadge id={machine.product} name={machine.product_name} />
        {machine.tracks_online && (
          <span className="badge bg-secondary-lt d-inline-flex align-items-center gap-1">
            <OnlineDot machine={machine} />
            {machine.online ? "Online" : "Offline"}
          </span>
        )}
      </div>
      <dl className="stage-lic-facts">
        {facts.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <div className="stage-detail-section-label mt-3">
        Users <span className="text-secondary fw-normal">{(machine.users || []).length}</span>
      </div>
      <div className="stage-detail-profiles mb-3">
        <MachineUserList machine={machine} />
      </div>
      <div className="stage-detail-section-label">Recent events</div>
      <RecentEvents filter={{ seat_id: machine.id }} />
    </Modal>
  );
}

function reasonText(reason: string) {
  const known: Record<string, string> = {
    released_by_admin: "Released by an admin",
    released_by_user: "Released from the app",
    blocked_by_admin: "Blocked by an admin",
    admin_revoke: "Released by an admin",
    user_archived: "The user was archived",
    user_deleted: "The user was deleted",
  };
  return known[reason] || reason;
}
