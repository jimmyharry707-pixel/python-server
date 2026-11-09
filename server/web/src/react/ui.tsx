import { useState, type FormEvent, type ReactNode, type RefObject } from "react";
import { Dialog } from "./components/primitives";
import { useToast } from "./components/ToastProvider";
export const staff = (r?: string) => ["admin", "manager"].includes(String(r).toLowerCase());
export const fmt = (v: any) =>
  v === null || v === undefined || v === ""
    ? "—"
    : typeof v === "object"
      ? JSON.stringify(v)
      : String(v);
export const when = (v: any) => {
  if (!v) return "—";
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
};
/** Server timestamps are naive UTC (no zone suffix); show them in the viewer's zone. */
export const whenUtc = (v?: string | null) => {
  if (!v) return "—";
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(v) ? v : `${v}Z`);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
};
export const arr = (v: any): any[] =>
  Array.isArray(v) ? v : Array.isArray(v?.items) ? v.items : Array.isArray(v?.rows) ? v.rows : [];
export function Loading() {
  return (
    <div className="card placeholder-glow">
      <div className="card-body">
        <div className="placeholder col-4 mb-3" />
        <div className="placeholder col-12 mb-2" />
        <div className="placeholder col-8" />
      </div>
    </div>
  );
}
export function ErrorBox({ error }: { error: any }) {
  return <div className="alert alert-danger">{error?.message || "Something went wrong"}</div>;
}
export function Empty({ text = "No records found." }: { text?: string }) {
  return <div className="text-secondary text-center py-4">{text}</div>;
}
export function PageHeader({ text, action }: { text: string; action?: ReactNode }) {
  return (
    <div className="d-flex justify-content-between align-items-center mb-3">
      <p className="text-secondary mb-0">{text}</p>
      {action}
    </div>
  );
}
export function Card({
  title,
  actions,
  children,
  className = "",
}: {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`card ${className}`}>
      <div className="card-header">
        <h3 className="card-title">{title}</h3>
        {actions && <div className="card-actions">{actions}</div>}
      </div>
      {children}
    </div>
  );
}
export function Search({
  value,
  onChange,
  placeholder = "Search…",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <input
      type="search"
      className="form-control form-control-sm"
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}
type Col = { key: string; label: string; render?: (r: any) => ReactNode; className?: string };
export function SimpleTable({
  rows,
  columns,
  empty = "No records found.",
  onRow,
}: {
  rows: any[];
  columns: Col[];
  empty?: string;
  onRow?: (r: any) => void;
}) {
  return (
    <div className="table-responsive">
      <table className="table table-vcenter card-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th className={c.className} key={c.key}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr
              key={r.id || r.key || i}
              onClick={() => onRow?.(r)}
              style={onRow ? { cursor: "pointer" } : undefined}
            >
              {columns.map((c) => (
                <td className={c.className} key={c.key}>
                  {c.render ? c.render(r) : fmt(r[c.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {!rows.length && <Empty text={empty} />}
    </div>
  );
}
export function Modal({
  title,
  description,
  children,
  onClose,
  footer,
  size = "lg",
  variant = "center",
  returnFocusRef,
  className,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  onClose: () => void;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
  variant?: "center" | "sheet";
  returnFocusRef?: RefObject<HTMLElement | null>;
  className?: string;
}) {
  return (
    <Dialog
      title={title}
      description={description}
      onClose={onClose}
      footer={footer}
      size={size}
      variant={variant}
      returnFocusRef={returnFocusRef}
      className={className}
    >
      {children}
    </Dialog>
  );
}
export function Confirm({
  text,
  onConfirm,
  onClose,
}: {
  text: string;
  onConfirm: () => Promise<any> | any;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title="Confirm action"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-danger"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await onConfirm();
                onClose();
              } finally {
                setBusy(false);
              }
            }}
          >
            Confirm
          </button>
        </>
      }
    >
      <p>{text}</p>
    </Modal>
  );
}
export function JsonDetail({ data }: { data: any }) {
  return (
    <pre className="bg-body-secondary rounded p-3 overflow-auto" style={{ maxHeight: "60vh" }}>
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}
export function Pager({
  page,
  total,
  size = 25,
  onChange,
}: {
  page: number;
  total: number;
  size?: number;
  onChange: (p: number) => void;
}) {
  return (
    <div className="card-footer d-flex justify-content-between">
      <span className="text-secondary small">{total} records</span>
      <div className="btn-group">
        <button
          type="button"
          className="btn btn-sm"
          disabled={page <= 1}
          onClick={() => onChange(page - 1)}
        >
          Previous
        </button>
        <button
          type="button"
          className="btn btn-sm"
          disabled={page * size >= total}
          onClick={() => onChange(page + 1)}
        >
          Next
        </button>
      </div>
    </div>
  );
}
export function NumberedPager({
  page,
  total,
  size = 25,
  onChange,
  onSizeChange,
  position = "bottom",
}: {
  page: number;
  total: number;
  size?: number;
  onChange: (page: number) => void;
  onSizeChange?: (size: number) => void;
  position?: "top" | "bottom";
}) {
  const pageCount = Math.max(1, Math.ceil(total / size));
  const current = Math.min(Math.max(1, page), pageCount);
  const start = total ? (current - 1) * size + 1 : 0;
  const end = Math.min(current * size, total);
  const pages = paginationItems(current, pageCount);
  return (
    <nav
      className={`stage-numbered-pager stage-numbered-pager-${position}`}
      aria-label={`${position === "top" ? "Top" : "Bottom"} table pagination`}
    >
      <div className="stage-pager-summary">
        Showing {start.toLocaleString()}–{end.toLocaleString()} of {total.toLocaleString()}
      </div>
      <div className="stage-pager-controls">
        <button
          type="button"
          className="btn btn-sm btn-icon"
          disabled={current <= 1}
          aria-label="Previous page"
          onClick={() => onChange(current - 1)}
        >
          <i className="ti ti-chevron-left" />
        </button>
        <div className="btn-group stage-pager-pages" aria-label="Page numbers">
          {pages.map((item, index) =>
            item === "ellipsis" ? (
              <span className="btn btn-sm disabled stage-pager-ellipsis" key={`ellipsis-${index}`}>
                …
              </span>
            ) : (
              <button
                type="button"
                className={`btn btn-sm ${item === current ? "active" : ""}`}
                aria-current={item === current ? "page" : undefined}
                aria-label={`Page ${item}`}
                key={item}
                onClick={() => onChange(item)}
              >
                {item}
              </button>
            ),
          )}
        </div>
        <button
          type="button"
          className="btn btn-sm btn-icon"
          disabled={current >= pageCount}
          aria-label="Next page"
          onClick={() => onChange(current + 1)}
        >
          <i className="ti ti-chevron-right" />
        </button>
        {onSizeChange && (
          <select
            className="form-select form-select-sm stage-pager-size"
            aria-label="Rows per page"
            value={size}
            onChange={(event) => onSizeChange(Number(event.target.value))}
          >
            {[25, 50, 100].map((option) => (
              <option value={option} key={option}>
                {option} / page
              </option>
            ))}
          </select>
        )}
      </div>
    </nav>
  );
}

function paginationItems(page: number, pageCount: number): Array<number | "ellipsis"> {
  if (pageCount <= 7) return Array.from({ length: pageCount }, (_, index) => index + 1);
  const visible = new Set([1, pageCount, page - 1, page, page + 1]);
  const values = [...visible]
    .filter((value) => value > 0 && value <= pageCount)
    .sort((a, b) => a - b);
  const result: Array<number | "ellipsis"> = [];
  values.forEach((value, index) => {
    if (index && value - values[index - 1] > 1) result.push("ellipsis");
    result.push(value);
  });
  return result;
}
export function useNotice() {
  return { ...useToast(), node: null };
}
export function FormModal({
  title,
  fields,
  initial = {},
  onSave,
  onClose,
}: {
  title: string;
  fields: { key: string; label: string; type?: string; required?: boolean; options?: string[] }[];
  initial?: any;
  onSave: (v: any) => Promise<any>;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setBusy(true);
    try {
      await onSave(Object.fromEntries(new FormData(e.currentTarget)));
      onClose();
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={title} onClose={onClose}>
      <form onSubmit={submit}>
        {fields.map((f) => (
          <div className="mb-3" key={f.key}>
            <label className="form-label">{f.label}</label>
            {f.options ? (
              <select
                className="form-select"
                name={f.key}
                defaultValue={initial[f.key] || f.options[0]}
              >
                {f.options.map((o) => (
                  <option key={o}>{o}</option>
                ))}
              </select>
            ) : (
              <input
                className="form-control"
                name={f.key}
                type={f.type || "text"}
                required={f.required}
                defaultValue={initial[f.key] ?? ""}
              />
            )}
          </div>
        ))}
        <div className="d-flex justify-content-end gap-2">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Saving…" : "Save"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
