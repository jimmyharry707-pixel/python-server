import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Row } from "../../api";
import { Card, ErrorBox, Loading, PageHeader, Pager, Search, SimpleTable, when } from "../../ui";
const useFiltered = (rows: Row[], search: string) =>
  useMemo(
    () => rows.filter((r) => JSON.stringify(r).toLowerCase().includes(search.toLowerCase())),
    [rows, search],
  );
export function TelemetryPage() {
  const s = useQuery({ queryKey: ["telemetry-summary"], queryFn: api.telemetrySummary }),
    r = useQuery({ queryKey: ["telemetry-recent"], queryFn: api.telemetryRecent });
  const [find, setFind] = useState("");
  const [page, setPage] = useState(1);
  const recent = useFiltered(r.data || [], find),
    max = Math.max(1, ...(s.data || []).map((x) => Number(x.count) || 0));
  if (s.isLoading || r.isLoading) return <Loading />;
  if (s.error || r.error) return <ErrorBox error={s.error || r.error} />;
  return (
    <>
      <PageHeader text="Aggregated event counts and recent client events." />
      <div className="row row-deck row-cards">
        <div className="col-lg-5">
          <Card title="Top events">
            <div className="card-body">
              {(s.data || []).slice(0, 8).map((x) => (
                <div className="mb-3" key={x.event}>
                  <div className="d-flex justify-content-between small">
                    <span>{x.event}</span>
                    <strong>{x.count}</strong>
                  </div>
                  <div className="progress progress-sm">
                    <div
                      className="progress-bar"
                      style={{ width: `${(Number(x.count) / max) * 100}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>
        <div className="col-lg-7">
          <Card title="Event summary">
            <SimpleTable
              rows={s.data || []}
              columns={[
                { key: "event", label: "Event", className: "font-monospace" },
                { key: "count", label: "Count", className: "text-end" },
              ]}
            />
          </Card>
        </div>
      </div>
      <Card
        title="Recent events"
        className="mt-3"
        actions={
          <Search
            value={find}
            onChange={(v) => {
              setFind(v);
              setPage(1);
            }}
          />
        }
      >
        <SimpleTable
          rows={recent.slice((page - 1) * 25, page * 25)}
          columns={[
            { key: "created_at", label: "Time", render: (x) => when(x.created_at) },
            { key: "event", label: "Event", className: "font-monospace" },
            { key: "app_version", label: "Version" },
            {
              key: "device_id",
              label: "Device",
              render: (x) => <span title={x.device_id}>{x.device_id?.slice(0, 12) || "—"}</span>,
            },
            {
              key: "properties",
              label: "Properties",
              render: (x) => (
                <span className="text-truncate d-block" style={{ maxWidth: "18rem" }}>
                  {JSON.stringify(x.properties || {})}
                </span>
              ),
            },
          ]}
        />
        <Pager page={page} total={recent.length} onChange={setPage} />
      </Card>
    </>
  );
}
