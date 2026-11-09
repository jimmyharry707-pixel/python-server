import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Row } from "../../api";
import { Confirm, ErrorBox, Loading, Modal, useNotice } from "../../ui";
import { ActionMenu } from "../../components/primitives";
import { LicenseDetail } from "./LicenseDetail";
import { LicenseForm } from "./LicenseForm";
import {
  Ago,
  KeyMachines,
  KeyTag,
  LicenseStatus,
  ProductBadge,
  exact,
  expiryText,
  licenseName,
  machineSearchText,
  shortDate,
  useLicenseRefresh,
} from "./licenseUi";

type SortKey = "key" | "product" | "label" | "seats" | "status" | "expires" | "activity";

const statusOrder: Record<string, number> = {
  expired: 0,
  expiring: 1,
  suspended: 2,
  active: 3,
  unused: 4,
};

function sortValue(row: Row, key: SortKey): number | string {
  switch (key) {
    case "key":
      return String(row.key_prefix || "");
    case "product":
      return String(row.product_name || "");
    case "label":
      return String(row.label || row.customer || "").toLowerCase();
    case "seats":
      return Number(row.seats_used) || 0;
    case "status":
      return statusOrder[row.status] ?? 9;
    case "expires":
      return row.expires_at ? new Date(row.expires_at).getTime() : Number.MAX_SAFE_INTEGER;
    case "activity":
      return row.last_activity_at ? new Date(row.last_activity_at).getTime() : 0;
  }
}

export function LicensesTab({
  product,
  products,
  issuing,
  setIssuing,
}: {
  product: string;
  products: Row[];
  issuing: boolean;
  setIssuing: (value: boolean) => void;
}) {
  const notice = useNotice();
  const refresh = useLicenseRefresh();
  const licenses = useQuery({
    queryKey: ["licenses", product],
    queryFn: () => api.licenses(product || undefined),
    placeholderData: (previous) => previous,
  });
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; direction: 1 | -1 } | null>(null);
  const [created, setCreated] = useState<Row | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [pending, setPending] = useState<{ license: Row; action: "suspend" | "resume" } | null>(
    null,
  );

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const list = (licenses.data || []).filter(
      (license) =>
        !needle ||
        [
          license.label,
          license.customer,
          license.key_prefix,
          license.plan,
          license.product_name,
          license.notes,
          // Who uses it, and where: PC names, accounts, Windows users, IPs.
          ...((license.seats as Row[]) || []).map(machineSearchText),
        ].some((value) =>
          String(value || "")
            .toLowerCase()
            .includes(needle),
        ),
    );
    if (!sort) return list;
    return [...list].sort((a, b) => {
      const left = sortValue(a, sort.key);
      const right = sortValue(b, sort.key);
      const order =
        typeof left === "number" && typeof right === "number"
          ? left - right
          : String(left).localeCompare(String(right));
      return order * sort.direction;
    });
  }, [licenses.data, search, sort]);

  const detail = detailId ? (licenses.data || []).find((row) => row.id === detailId) : null;
  const sortBy = (key: SortKey) =>
    setSort((current) =>
      current?.key === key
        ? { key, direction: current.direction === 1 ? -1 : 1 }
        : { key, direction: 1 },
    );
  const header = (key: SortKey, label: string, className = "") => (
    <th className={className}>
      <button
        type="button"
        className="table-sort btn btn-link p-0 text-reset"
        onClick={() => sortBy(key)}
      >
        {label}
        {sort?.key === key && (
          <i className={`ti ti-chevron-${sort.direction === 1 ? "up" : "down"} ms-1`} />
        )}
      </button>
    </th>
  );
  const quick = async (operation: () => Promise<unknown>, message: string) => {
    try {
      await operation();
      notice.show(message);
      await refresh();
    } catch (error: any) {
      notice.show(error?.message || "License update failed", "danger");
    }
  };

  return (
    <>
      <div className="card">
        <div className="card-header stage-panel-header">
          <div>
            <h3 className="card-title mb-0">License keys</h3>
            <div className="text-secondary small">
              {licenses.data?.length || 0} keys{search && ` · ${rows.length} shown`}. Click a key to
              see its machines and users.
            </div>
          </div>
          <div className="input-icon stage-lic-search">
            <span className="input-icon-addon">
              <i className="ti ti-search" />
            </span>
            <input
              className="form-control"
              value={search}
              aria-label="Search keys"
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search key, customer, user, PC…"
            />
          </div>
        </div>
        {licenses.isLoading ? (
          <div className="card-body">
            <Loading />
          </div>
        ) : licenses.error ? (
          <div className="card-body">
            <ErrorBox error={licenses.error} />
          </div>
        ) : (
          <div className="table-responsive">
            <table className="table table-vcenter card-table table-sm table-hover stage-lic-table">
              <thead>
                <tr>
                  {header("key", "Key", "d-none d-sm-table-cell")}
                  {header("label", "Label")}
                  {header("product", "App", "d-none d-md-table-cell")}
                  <th className="d-none d-sm-table-cell">Used by</th>
                  {header("seats", "Seats", "d-none d-sm-table-cell")}
                  {header("status", "Status")}
                  {header("expires", "Expires")}
                  {header("activity", "Last activity", "d-none d-lg-table-cell")}
                  <th className="w-1" />
                </tr>
              </thead>
              <tbody>
                {rows.map((license) => (
                  <tr
                    key={license.id}
                    className="cursor-pointer stage-interactive-row"
                    tabIndex={0}
                    onClick={() => setDetailId(license.id)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setDetailId(license.id);
                      }
                    }}
                  >
                    <td className="text-nowrap d-none d-sm-table-cell">
                      <KeyTag row={license} />
                      <div className="text-secondary small" title={exact(license.created_at, "")}>
                        Issued {shortDate(license.created_at)}
                      </div>
                    </td>
                    <td>
                      <div className="fw-medium text-truncate stage-lic-cell-max">
                        {license.label || "—"}
                      </div>
                      <div className="text-secondary small text-truncate stage-lic-cell-max">
                        {[license.customer, license.plan].filter(Boolean).join(" · ")}
                      </div>
                      <div className="text-secondary small d-sm-none">
                        <KeyTag row={license} /> · {license.seats_used}/{license.seat_limit} seats
                      </div>
                      <div className="small d-sm-none mt-1">
                        <KeyMachines license={license} limit={1} />
                      </div>
                    </td>
                    <td className="d-none d-md-table-cell">
                      <ProductBadge id={license.product} name={license.product_name} />
                    </td>
                    <td className="d-none d-sm-table-cell">
                      <KeyMachines license={license} />
                    </td>
                    <td className="font-monospace text-nowrap d-none d-sm-table-cell">
                      {license.seats_used}/{license.seat_limit}
                    </td>
                    <td>
                      <LicenseStatus status={license.status} />
                    </td>
                    <td className="text-nowrap">
                      <div title={exact(license.expires_at, "Never expires")}>
                        {license.expires_at ? shortDate(license.expires_at) : "Never"}
                      </div>
                      {license.expires_at && (
                        <div
                          className={`small ${
                            license.status === "expired"
                              ? "text-danger"
                              : license.status === "expiring"
                                ? "text-warning"
                                : "text-secondary"
                          }`}
                        >
                          {expiryText(license)}
                        </div>
                      )}
                    </td>
                    <td className="d-none d-lg-table-cell text-secondary text-nowrap">
                      <Ago value={license.last_activity_at} />
                    </td>
                    <td onClick={(event) => event.stopPropagation()}>
                      <ActionMenu
                        label={`Actions for ${licenseName(license)}`}
                        items={[
                          {
                            label: "Open",
                            icon: "ti-eye",
                            onSelect: () => setDetailId(license.id),
                          },
                          {
                            label: "Extend 30 days",
                            icon: "ti-calendar-plus",
                            disabled: !license.expires_at,
                            onSelect: () =>
                              quick(
                                () => api.extendLicense(license.id, 30),
                                "Expiry extended by 30 days",
                              ),
                          },
                          license.status === "suspended"
                            ? {
                                label: "Resume",
                                icon: "ti-player-play",
                                separatorBefore: true,
                                onSelect: () => setPending({ license, action: "resume" }),
                              }
                            : {
                                label: "Suspend",
                                icon: "ti-player-pause",
                                danger: true,
                                separatorBefore: true,
                                onSelect: () => setPending({ license, action: "suspend" }),
                              },
                        ]}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!rows.length && (
              <div className="card-body text-center text-secondary">
                {licenses.data?.length
                  ? "No keys match your search."
                  : "No keys yet. Issue one to get started."}
              </div>
            )}
          </div>
        )}
      </div>

      {issuing && (
        <LicenseForm
          products={products}
          defaultProduct={product || undefined}
          onClose={() => setIssuing(false)}
          onSubmit={async (body) => {
            const license = await api.createLicense(body);
            setIssuing(false);
            setCreated(license);
            notice.show("Key issued");
            await refresh();
          }}
        />
      )}
      {created && <IssuedKey data={created} onClose={() => setCreated(null)} />}
      {detail && (
        <LicenseDetail license={detail} products={products} onClose={() => setDetailId(null)} />
      )}
      {pending && (
        <Confirm
          text={
            pending.action === "suspend"
              ? `Suspend "${licenseName(pending.license)}"? The key stops working on every machine at their next check-in. Machines keep their activation and work again when you resume.`
              : `Resume "${licenseName(pending.license)}"? Machines can use it again at their next check-in.`
          }
          onClose={() => setPending(null)}
          onConfirm={() =>
            quick(
              () => api.licenseAction(pending.license.id, pending.action),
              pending.action === "suspend" ? "Key suspended" : "Key resumed",
            )
          }
        />
      )}
    </>
  );
}

function IssuedKey({ data, onClose }: { data: Row; onClose: () => void }) {
  const key = String(data.license_key || "");
  const [copied, setCopied] = useState(false);
  return (
    <Modal title="License key" onClose={onClose}>
      <div className="alert alert-warning">
        <i className="ti ti-alert-triangle me-1" />
        Copy this key now. It will not be shown again.
      </div>
      <div className="text-secondary small mb-2">
        {data.product_name} · {data.label}
        {data.customer ? ` · ${data.customer}` : ""}
      </div>
      <div className="stage-license-key-box">
        <code>{key}</code>
      </div>
      <button
        type="button"
        className="btn btn-primary w-100 mt-3"
        onClick={async () => {
          await navigator.clipboard.writeText(key);
          setCopied(true);
        }}
      >
        <i className={`ti ${copied ? "ti-check" : "ti-copy"} me-1`} />
        {copied ? "Copied" : "Copy license key"}
      </button>
    </Modal>
  );
}
