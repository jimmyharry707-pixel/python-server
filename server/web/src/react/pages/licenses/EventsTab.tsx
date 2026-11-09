import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Row } from "../../api";
import { ErrorBox, Loading, NumberedPager } from "../../ui";
import {
  Ago,
  EventLabel,
  ProductBadge,
  eventFilterOptions,
  eventSummary,
  KeyTag,
  exact,
} from "./licenseUi";

/** The key's tag; a deleted key's prefix survives only in its delete event. */
function EventKey({ event }: { event: Row }) {
  const prefix = event.key_prefix || event.detail?.key_prefix;
  return prefix ? <KeyTag row={event} prefix={String(prefix)} /> : <span>Deleted key</span>;
}

function who(event: Row) {
  return (
    event.user_name ||
    event.user_email ||
    event.os_user ||
    (event.detail?.by === "app" ? "The app" : "")
  );
}

export function EventsTab({ product }: { product: string }) {
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(50);
  const [kind, setKind] = useState("");
  useEffect(() => setPage(1), [product, kind, size]);
  const events = useQuery({
    queryKey: ["license-events", product, kind, page, size],
    queryFn: () =>
      api.licenseEvents({
        product: product || undefined,
        event: kind || undefined,
        limit: size,
        offset: (page - 1) * size,
      }),
    placeholderData: (previous) => previous,
  });
  const rows = events.data?.items || [];
  const total = events.data?.total || 0;

  return (
    <div className="card">
      <div className="card-header stage-panel-header">
        <div>
          <h3 className="card-title mb-0">Events</h3>
          <div className="text-secondary small">
            Every activation, renewal, refusal and admin change. Newest first.
          </div>
        </div>
        <select
          className="form-select stage-lic-filter"
          aria-label="Event type"
          value={kind}
          onChange={(event) => setKind(event.target.value)}
        >
          {eventFilterOptions.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>
      {events.isLoading ? (
        <div className="card-body">
          <Loading />
        </div>
      ) : events.error ? (
        <div className="card-body">
          <ErrorBox error={events.error} />
        </div>
      ) : (
        <>
          <div className="table-responsive">
            <table className="table table-vcenter card-table table-sm stage-lic-table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Event</th>
                  <th className="d-none d-md-table-cell">What happened</th>
                  <th className="d-none d-md-table-cell">App</th>
                  <th>Key</th>
                  <th>Machine</th>
                  <th className="d-none d-lg-table-cell">Who</th>
                  <th className="d-none d-md-table-cell">IP</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((event) => (
                  <tr key={event.id}>
                    <td className="text-nowrap text-secondary" title={exact(event.created_at)}>
                      <Ago value={event.created_at} />
                    </td>
                    <td>
                      <EventLabel event={event.event} />
                      {eventSummary(event) && (
                        <div className="text-secondary small d-md-none stage-lic-summary">
                          {eventSummary(event)}
                        </div>
                      )}
                    </td>
                    <td className="stage-lic-summary d-none d-md-table-cell">
                      {eventSummary(event) || "—"}
                    </td>
                    <td className="d-none d-md-table-cell">
                      {event.product ? (
                        <ProductBadge id={event.product} name={event.product_name} />
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="text-nowrap">
                      {event.license_id ? (
                        <span title={event.license_label || ""}>
                          <EventKey event={event} />
                          {event.license_label && (
                            <div className="text-secondary small text-truncate stage-lic-cell-max">
                              {event.license_label}
                            </div>
                          )}
                        </span>
                      ) : (
                        <span className="text-secondary">—</span>
                      )}
                    </td>
                    <td className="text-nowrap">{event.hostname || "—"}</td>
                    <td className="d-none d-lg-table-cell text-nowrap">{who(event) || "—"}</td>
                    <td className="d-none d-md-table-cell font-monospace small">
                      {event.ip || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!rows.length && (
            <div className="card-body text-center text-secondary">No events yet.</div>
          )}
          {total > 0 && (
            <div className="card-footer">
              <NumberedPager
                page={page}
                total={total}
                size={size}
                onChange={setPage}
                onSizeChange={setSize}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** The latest events for one license or one machine, for detail panels. */
export function RecentEvents({ filter }: { filter: { license_id?: string; seat_id?: number } }) {
  const events = useQuery({
    queryKey: ["license-events", "recent", filter],
    queryFn: () => api.licenseEvents({ ...filter, limit: 15 }),
  });
  if (events.isLoading) return <div className="text-secondary small">Loading events…</div>;
  if (events.error) return <ErrorBox error={events.error} />;
  const rows = events.data?.items || [];
  if (!rows.length) return <div className="text-secondary small">No events yet.</div>;
  return (
    <ul className="list-unstyled mb-0 stage-detail-profiles">
      {rows.map((event) => (
        <li className="stage-detail-profile stage-lic-event" key={event.id}>
          <div className="min-w-0 flex-fill">
            <EventLabel event={event.event} />
            {eventSummary(event) && (
              <div className="text-secondary small stage-lic-summary">{eventSummary(event)}</div>
            )}
          </div>
          <div className="text-secondary small text-end text-nowrap">
            <div title={exact(event.created_at)}>
              <Ago value={event.created_at} />
            </div>
            <div className="font-monospace">
              {[filter.seat_id ? null : event.hostname, event.ip].filter(Boolean).join(" · ")}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}
