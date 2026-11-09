import { useState, type FormEvent } from "react";
import { type Row } from "../../api";
import { Modal } from "../../ui";
import { fromLocalInput, timeZoneLabel, toLocalInput } from "./licenseUi";

const plans = ["standard", "professional", "enterprise"];

function inDays(days: number) {
  const date = new Date();
  date.setDate(date.getDate() + days);
  date.setHours(23, 59, 0, 0);
  return toLocalInput(date.toISOString());
}

/** Issue (no `license`) or edit a key. `onSubmit` gets the API body. */
export function LicenseForm({
  license,
  products,
  defaultProduct,
  onSubmit,
  onClose,
}: {
  license?: Row;
  products: Row[];
  defaultProduct?: string;
  onSubmit: (body: Row) => Promise<void>;
  onClose: () => void;
}) {
  const editing = !!license;
  const [product, setProduct] = useState(
    license?.product || defaultProduct || products[0]?.id || "careercraft",
  );
  const [label, setLabel] = useState(license?.label || "");
  const [customer, setCustomer] = useState(license?.customer || "");
  const [notes, setNotes] = useState(license?.notes || "");
  const [plan, setPlan] = useState(license?.plan || "standard");
  const [seatLimit, setSeatLimit] = useState(String(license?.seat_limit ?? 1));
  const [offlineDays, setOfflineDays] = useState(
    license?.offline_days_custom ? String(license.offline_days_custom) : "",
  );
  const [expires, setExpires] = useState(toLocalInput(license?.expires_at));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const productInfo = products.find((item) => item.id === product);
  const zone = timeZoneLabel();

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    const seats = Number(seatLimit);
    if (!Number.isInteger(seats) || seats < 1) return setError("Seat limit must be 1 or more.");
    const days = offlineDays.trim() ? Number(offlineDays) : null;
    if (days !== null && (!Number.isInteger(days) || days < 1 || days > 365))
      return setError("Offline days must be 1 to 365, or empty for the default.");
    const expiresAt = expires ? fromLocalInput(expires) : null;
    if (expires && !expiresAt) return setError("The expiry date is not valid.");
    const body: Row = {
      label: label.trim(),
      customer: customer.trim(),
      notes: notes.trim(),
      plan,
      seat_limit: seats,
      offline_days: days,
      expires_at: expiresAt,
    };
    if (!editing) body.product = product;
    else if (license) {
      // Send only what changed, so the event log lists real changes (the
      // date input drops seconds, so an untouched expiry would look edited).
      const before: Row = {
        label: license.label || "",
        customer: license.customer || "",
        notes: license.notes || "",
        plan: license.plan || "standard",
        seat_limit: license.seat_limit,
        offline_days: license.offline_days_custom ?? null,
      };
      for (const key of Object.keys(before)) if (body[key] === before[key]) delete body[key];
      if (expires === toLocalInput(license.expires_at)) delete body.expires_at;
      if (!Object.keys(body).length) {
        setError("Nothing changed.");
        return;
      }
    }
    setBusy(true);
    try {
      await onSubmit(body);
    } catch (caught: any) {
      setError(caught?.message || "Could not save the key");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={editing ? "Edit key" : "Issue a license key"}
      description={editing ? "Machines get the changes the next time they check in." : undefined}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="license-form" className="btn btn-primary" disabled={busy}>
            {busy ? "Saving…" : editing ? "Save changes" : "Issue key"}
          </button>
        </>
      }
    >
      <form id="license-form" onSubmit={submit}>
        {error && <div className="alert alert-danger">{error}</div>}
        <div className="row">
          <div className="col-md-6 mb-3">
            <label className="form-label" htmlFor="lic-product">
              App
            </label>
            {editing ? (
              <input
                id="lic-product"
                className="form-control"
                value={license?.product_name || product}
                disabled
              />
            ) : (
              <select
                id="lic-product"
                className="form-select"
                value={product}
                onChange={(event) => setProduct(event.target.value)}
              >
                {products.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            )}
            <div className="form-hint">
              {productInfo?.requires_account
                ? "Users sign in to their account, then enter the key."
                : "Users enter the key only. No account needed."}
            </div>
          </div>
          <div className="col-md-6 mb-3">
            <label className="form-label" htmlFor="lic-label">
              Label
            </label>
            <input
              id="lic-label"
              className="form-control"
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              placeholder="e.g. Team A"
              required
              autoFocus
            />
          </div>
          <div className="col-md-6 mb-3">
            <label className="form-label" htmlFor="lic-customer">
              Customer
            </label>
            <input
              id="lic-customer"
              className="form-control"
              value={customer}
              onChange={(event) => setCustomer(event.target.value)}
              placeholder="Person or company"
            />
            <div className="form-hint">The app shows this as "Licensed to".</div>
          </div>
          <div className="col-md-6 mb-3">
            <label className="form-label" htmlFor="lic-plan">
              Plan <span className="badge bg-secondary-lt ms-1">Not implemented yet</span>
            </label>
            {/* No app checks the plan yet, so it can't be changed here. */}
            <select
              id="lic-plan"
              className="form-select text-capitalize"
              value={plan}
              onChange={(event) => setPlan(event.target.value)}
              disabled
            >
              {[...new Set([...plans, plan])].map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
            <div className="form-hint">Every plan gets the same features in every app.</div>
          </div>
          <div className="col-md-6 mb-3">
            <label className="form-label" htmlFor="lic-seats">
              Seat limit
            </label>
            <input
              id="lic-seats"
              className="form-control"
              type="number"
              min={1}
              value={seatLimit}
              onChange={(event) => setSeatLimit(event.target.value)}
              required
            />
            <div className="form-hint">How many PCs can use the key at the same time.</div>
          </div>
          <div className="col-md-6 mb-3">
            <label className="form-label" htmlFor="lic-offline">
              Offline days
            </label>
            <input
              id="lic-offline"
              className="form-control"
              type="number"
              min={1}
              max={365}
              value={offlineDays}
              onChange={(event) => setOfflineDays(event.target.value)}
              placeholder="7 (default)"
            />
            <div className="form-hint">
              How long the app keeps working without reaching the server. Empty = 7 days.
            </div>
          </div>
          <div className="col-12 mb-3">
            <label className="form-label" htmlFor="lic-expires">
              Expires
            </label>
            <div className="d-flex gap-2 flex-wrap align-items-center">
              <input
                id="lic-expires"
                className="form-control stage-lic-expiry-input"
                type="datetime-local"
                value={expires}
                onChange={(event) => setExpires(event.target.value)}
              />
              <div className="btn-group btn-group-sm" role="group" aria-label="Expiry presets">
                {[
                  [30, "30 days"],
                  [90, "90 days"],
                  [365, "1 year"],
                ].map(([days, text]) => (
                  <button
                    key={days}
                    type="button"
                    className="btn btn-outline-secondary"
                    onClick={() => setExpires(inDays(Number(days)))}
                  >
                    {text}
                  </button>
                ))}
                <button
                  type="button"
                  className={`btn ${expires ? "btn-outline-secondary" : "btn-primary"}`}
                  onClick={() => setExpires("")}
                >
                  Never
                </button>
              </div>
            </div>
            <div className="form-hint">
              {expires ? `Time is in your time zone: ${zone}.` : "This key never expires."} The app
              stops working after this time.
            </div>
          </div>
          <div className="col-12">
            <label className="form-label" htmlFor="lic-notes">
              Notes
            </label>
            <textarea
              id="lic-notes"
              className="form-control"
              rows={2}
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              placeholder="Only admins see this"
            />
          </div>
        </div>
      </form>
    </Modal>
  );
}
