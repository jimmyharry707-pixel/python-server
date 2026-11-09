import { useState } from "react";
import { api, type Row } from "../../api";
import { Confirm, Modal, useNotice } from "../../ui";
import { ActionMenu } from "../../components/primitives";
import { RecentEvents } from "./EventsTab";
import { LicenseForm } from "./LicenseForm";
import { MachineDetail } from "./MachinesTab";
import {
  Ago,
  KeyTag,
  LicenseStatus,
  MachineState,
  MachineUsers,
  OnlineDot,
  ProductBadge,
  exact,
  expiryText,
  keyPrefix,
  licenseName,
  seatMenuItems,
  useLicenseRefresh,
  useSeatActions,
  windowsText,
} from "./licenseUi";

type Pending = "suspend" | "resume" | "delete";

const pendingText: Record<Pending, string> = {
  suspend:
    "Suspend this key? The key stops working on every machine at their next check-in. Machines keep their activation and work again when you resume.",
  resume: "Resume this key? Machines can use it again at their next check-in.",
  delete:
    "Delete this key for good? Its machine records are removed. The event log is kept. This can't be undone.",
};

export function LicenseDetail({
  license,
  products,
  onClose,
}: {
  license: Row;
  products: Row[];
  onClose: () => void;
}) {
  const notice = useNotice();
  const refresh = useLicenseRefresh();
  const [editing, setEditing] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [machineId, setMachineId] = useState<number | null>(null);
  const seat = useSeatActions();
  const machines: Row[] = Array.isArray(license.seats) ? license.seats : [];
  const openMachine = machines.find((machine) => machine.id === machineId) || null;
  const suspended = license.status === "suspended";
  const inUse = Number(license.seats_used) > 0;
  // A suspended or expired key can go even with machines on it: none can use it.
  const stopped = suspended || license.status === "expired";
  const deleteBlocked = inUse && !stopped;
  const deleteText = inUse
    ? `Delete this key for good? ${license.seats_used} machine(s) still have it activated. They lose the activation and ask for a key at their next check. The event log is kept. This can't be undone.`
    : pendingText.delete;

  const run = async (operation: () => Promise<unknown>, message: string) => {
    setBusy(true);
    try {
      await operation();
      notice.show(message);
      await refresh();
      return true;
    } catch (error: any) {
      notice.show(error?.message || "License update failed", "danger");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const extend = (days: number) =>
    run(() => api.extendLicense(license.id, days), `Expiry extended by ${days} days`);

  return (
    <>
      <Modal
        title={licenseName(license)}
        description={[license.customer, keyPrefix(license)].filter(Boolean).join(" · ")}
        variant="sheet"
        size="md"
        onClose={onClose}
        footer={
          <>
            <button
              type="button"
              className="btn btn-ghost-danger me-auto"
              disabled={deleteBlocked}
              title={deleteBlocked ? "Suspend the key first, or release its machines." : undefined}
              onClick={() => setPending("delete")}
            >
              <i className="ti ti-trash me-1" />
              Delete
            </button>
            <button
              type="button"
              className={`btn ${suspended ? "btn-success" : "btn-outline-warning"}`}
              onClick={() => setPending(suspended ? "resume" : "suspend")}
            >
              <i className={`ti ${suspended ? "ti-player-play" : "ti-player-pause"} me-1`} />
              {suspended ? "Resume" : "Suspend"}
            </button>
            <button type="button" className="btn btn-primary" onClick={() => setEditing(true)}>
              <i className="ti ti-pencil me-1" />
              Edit
            </button>
          </>
        }
      >
        <div className="d-flex gap-2 flex-wrap mb-3">
          <KeyTag row={license} />
          <LicenseStatus status={license.status} />
          <ProductBadge id={license.product} name={license.product_name} />
          <span className="badge bg-secondary-lt text-capitalize">
            {license.plan || "standard"}
          </span>
        </div>
        {deleteBlocked && (
          <div className="text-secondary small mb-3">
            Machines still use this key. To delete it, suspend it first, or release its machines.
          </div>
        )}

        <div className="stage-detail-stats stage-lic-stats mb-3">
          <Stat value={`${license.seats_used}/${license.seat_limit}`} label="Seats used" />
          <Stat value={license.machines_total} label="Machines ever" />
          <Stat value={`${license.offline_days} d`} label="Offline days" />
        </div>

        <div className="stage-lic-expiry mb-3">
          <div>
            <div className="fw-medium">{expiryText(license)}</div>
            <div className="text-secondary small">
              {license.expires_at ? exact(license.expires_at) : "No end date"}
            </div>
          </div>
          <div className="d-flex gap-1 flex-wrap align-items-center">
            <span className="text-secondary small me-1">Extend</span>
            {[30, 90, 365].map((days) => (
              <button
                key={days}
                type="button"
                className="btn btn-sm"
                disabled={busy || !license.expires_at}
                title={
                  license.expires_at
                    ? "Counted from today if the key already expired"
                    : "This key never expires"
                }
                onClick={() => extend(days)}
              >
                +{days === 365 ? "1 year" : `${days} days`}
              </button>
            ))}
          </div>
        </div>
        {!license.expires_at && (
          <div className="text-secondary small mb-3">
            This key never expires. To set an end date, use Edit.
          </div>
        )}

        {license.notes && (
          <>
            <div className="stage-detail-section-label">Notes</div>
            <p className="stage-lic-notes">{license.notes}</p>
          </>
        )}

        <div className="stage-detail-section-label">
          Machines <span className="text-secondary fw-normal">{machines.length}</span>
        </div>
        <ul className="stage-detail-profiles list-unstyled mb-3">
          {machines.length ? (
            machines.map((machine) => (
              <li
                className="stage-detail-profile cursor-pointer stage-interactive-row"
                key={machine.id}
                tabIndex={0}
                onClick={() => setMachineId(machine.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    setMachineId(machine.id);
                  }
                }}
              >
                <div className="min-w-0 flex-fill">
                  <div className="fw-medium d-flex align-items-center gap-2">
                    <OnlineDot machine={machine} />
                    <span className="text-truncate">{machine.hostname || "Unnamed PC"}</span>
                  </div>
                  <div className="text-secondary small text-truncate">
                    {windowsText(machine)}
                    {machine.app_version ? ` · v${machine.app_version}` : ""}
                  </div>
                  <div className="mt-1">
                    <MachineUsers machine={machine} limit={3} />
                  </div>
                </div>
                <div className="text-end small">
                  <MachineState state={machine.state} />
                  <div className="text-secondary mt-1">
                    {machine.online ? "Online now" : <Ago value={machine.last_seen_at} />}
                  </div>
                </div>
                <div onClick={(event) => event.stopPropagation()}>
                  <ActionMenu
                    label={`Actions for ${machine.hostname || "machine"}`}
                    items={seatMenuItems(machine, seat.ask)}
                  />
                </div>
              </li>
            ))
          ) : (
            <li className="stage-detail-profile text-secondary">
              No machine has used this key yet.
            </li>
          )}
        </ul>

        <div className="stage-detail-section-label">Recent events</div>
        <RecentEvents filter={{ license_id: license.id }} />
      </Modal>

      {editing && (
        <LicenseForm
          license={license}
          products={products}
          onClose={() => setEditing(false)}
          onSubmit={async (body) => {
            await api.updateLicense(license.id, body);
            notice.show("Key saved");
            await refresh();
            setEditing(false);
          }}
        />
      )}
      {pending && (
        <Confirm
          text={pending === "delete" ? deleteText : pendingText[pending]}
          onClose={() => setPending(null)}
          onConfirm={async () => {
            if (pending === "delete") {
              const ok = await run(() => api.deleteLicense(license.id), "Key deleted");
              if (ok) onClose();
            } else {
              await run(
                () => api.licenseAction(license.id, pending),
                pending === "suspend" ? "Key suspended" : "Key resumed",
              );
            }
          }}
        />
      )}
      {openMachine && (
        <MachineDetail machine={openMachine} onClose={() => setMachineId(null)} ask={seat.ask} />
      )}
      {seat.node}
    </>
  );
}

function Stat({ value, label }: { value: unknown; label: string }) {
  return (
    <div className="stage-detail-stat">
      <div className="stage-detail-stat-value">{String(value ?? "—")}</div>
      <div className="stage-detail-stat-label">{label}</div>
    </div>
  );
}
