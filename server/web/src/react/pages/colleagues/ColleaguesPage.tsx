import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Row } from "../../api";
import { arr, ErrorBox, Loading, PageHeader, Search } from "../../ui";
import { Avatar } from "../../components/Avatar";
import { shiftDate } from "../../reportingPeriod";

type DayPreset = "today" | "yesterday" | "custom";

// Minutes east of UTC for `timeZone` at `instant`, DST-aware (Intl handles the
// transition rules so we don't hardcode offsets).
function zonedOffsetMinutes(instant: Date, timeZone: string): number {
  const parts: Record<string, string> = {};
  for (const part of new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(instant)) {
    parts[part.type] = part.value;
  }
  const asUtc = Date.UTC(
    Number(parts.year),
    Number(parts.month) - 1,
    Number(parts.day),
    Number(parts.hour),
    Number(parts.minute),
    Number(parts.second),
  );
  return Math.round((asUtc - instant.getTime()) / 60000);
}

// "Today" (or any instant) as a YYYY-MM-DD calendar date in `timeZone`.
function zonedDateString(timeZone: string, instant = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(instant);
}

// UTC instant range covering one calendar day in `timeZone`.
function zonedDayBoundsUtc(dateStr: string, timeZone: string) {
  const noonGuess = new Date(`${dateStr}T12:00:00Z`);
  const offset = zonedOffsetMinutes(noonGuess, timeZone);
  const start = new Date(Date.parse(`${dateStr}T00:00:00Z`) - offset * 60000);
  const end = new Date(start.getTime() + 24 * 60 * 60 * 1000 - 1);
  return { start, end };
}

// Hour-of-day (0-23) for `instant` in `timeZone`.
function zonedHour(instant: Date, timeZone: string): number {
  return Number(
    new Intl.DateTimeFormat("en-US", { timeZone, hour: "2-digit", hourCycle: "h23" }).format(
      instant,
    ),
  );
}

export function ColleaguesPage() {
  const colleaguesQuery = useQuery({ queryKey: ["colleagues"], queryFn: api.colleagues });
  const [find, setFind] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [preset, setPreset] = useState<DayPreset>("today");
  const [selectedDate, setSelectedDate] = useState(() => zonedDateString("UTC"));
  const [selectedApplication, setSelectedApplication] = useState<Row | null>(null);
  const colleagues = arr(colleaguesQuery.data?.colleagues || colleaguesQuery.data);
  const rows = colleagues.filter((row) =>
    JSON.stringify(row).toLowerCase().includes(find.toLowerCase()),
  );

  useEffect(() => {
    if (!selectedId && colleagues.length) setSelectedId(String(colleagues[0].user_id));
  }, [colleagues, selectedId]);

  const selectedColleague = colleagues.find((row) => String(row.user_id) === selectedId);
  const colleagueTz = String(selectedColleague?.timezone || "UTC");

  // "Today"/"Yesterday" mean the colleague's own local day, not the viewer's —
  // recompute whenever the selected colleague (and thus her timezone) changes.
  useEffect(() => {
    if (!selectedColleague || preset === "custom") return;
    const base = zonedDateString(colleagueTz);
    setSelectedDate(preset === "yesterday" ? shiftDate(base, -1) : base);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, colleagueTz]);

  const dayBounds = useMemo(() => {
    const { start, end } = zonedDayBoundsUtc(selectedDate, colleagueTz);
    return { modified_from: start.toISOString(), modified_to: end.toISOString() };
  }, [selectedDate, colleagueTz]);
  const activityQuery = useQuery({
    queryKey: ["colleague-activity", selectedId, selectedDate, colleagueTz],
    queryFn: () => api.teamApplications({ contributor_id: selectedId, ...dayBounds, limit: 200 }),
    enabled: Boolean(selectedId),
  });

  useEffect(() => setSelectedApplication(null), [selectedId, selectedDate]);
  if (colleaguesQuery.isLoading) return <Loading />;
  if (colleaguesQuery.error) return <ErrorBox error={colleaguesQuery.error} />;

  const applications = arr(activityQuery.data?.applications || activityQuery.data)
    .filter((row) => row.last_modified)
    .sort(
      (a, b) =>
        new Date(String(a.last_modified)).getTime() - new Date(String(b.last_modified)).getTime(),
    );
  const choosePreset = (next: DayPreset) => {
    setPreset(next);
    const base = zonedDateString(colleagueTz);
    if (next === "today") setSelectedDate(base);
    if (next === "yesterday") setSelectedDate(shiftDate(base, -1));
  };

  return (
    <>
      <PageHeader text="See when your colleagues last saved or edited each application — this is edit activity, not the original applied date, so it won't match the Performance tab's daily/weekly totals." />
      {!colleagues.length ? (
        <div className="card">
          <div className="card-body text-secondary">
            No colleagues are assigned to your profiles.
          </div>
        </div>
      ) : (
        <div className="stage-activity-layout">
          <aside className="card stage-activity-people">
            <div className="card-header">
              <h3 className="card-title">Colleagues</h3>
            </div>
            <div className="card-body border-bottom py-2">
              <Search value={find} onChange={setFind} />
            </div>
            <div className="stage-activity-person-list">
              {rows.map((row) => (
                <button
                  type="button"
                  className={`stage-activity-person ${String(row.user_id) === selectedId ? "is-active" : ""}`}
                  key={row.user_id}
                  onClick={() => setSelectedId(String(row.user_id))}
                >
                  <Avatar person={row} size="sm" />
                  <span className="min-w-0">
                    <strong>{row.name || row.email}</strong>
                    <small>
                      {Number(row.totals?.applications || 0).toLocaleString()} total applications
                    </small>
                  </span>
                  <span className={`stage-activity-presence ${row.online ? "is-online" : ""}`} />
                </button>
              ))}
              {!rows.length && <div className="p-3 text-secondary">No matching colleagues.</div>}
            </div>
          </aside>
          <main className="card stage-activity-main">
            <div className="card-body stage-activity-toolbar">
              <div className="d-flex align-items-center gap-2 min-w-0">
                {selectedColleague && <Avatar person={selectedColleague} size="md" />}
                <div className="min-w-0">
                  <h2 className="mb-0 text-truncate">
                    {selectedColleague?.name || selectedColleague?.email}
                  </h2>
                  <div className="text-secondary small">
                    Application activity · {colleagueTz} time
                  </div>
                </div>
              </div>
              <div className="stage-activity-date-controls" aria-label="Activity date">
                <div className="btn-group">
                  {(["today", "yesterday", "custom"] as DayPreset[]).map((key) => (
                    <button
                      type="button"
                      key={key}
                      className={`btn btn-sm ${preset === key ? "btn-primary" : "btn-outline-secondary"}`}
                      onClick={() => choosePreset(key)}
                    >
                      {key[0].toUpperCase() + key.slice(1)}
                    </button>
                  ))}
                </div>
                {preset === "custom" && (
                  <input
                    className="form-control form-control-sm"
                    type="date"
                    max={zonedDateString(colleagueTz)}
                    value={selectedDate}
                    aria-label="Choose activity date"
                    onChange={(event) => setSelectedDate(event.target.value)}
                  />
                )}
              </div>
            </div>
            {activityQuery.isLoading ? (
              <Loading />
            ) : activityQuery.error ? (
              <ErrorBox error={activityQuery.error} />
            ) : (
              <ActivityTimeline
                applications={applications}
                date={selectedDate}
                timeZone={colleagueTz}
                selected={selectedApplication}
                onSelect={setSelectedApplication}
              />
            )}
          </main>
        </div>
      )}
    </>
  );
}

function ActivityTimeline({
  applications,
  date,
  timeZone,
  selected,
  onSelect,
}: {
  applications: Row[];
  date: string;
  timeZone: string;
  selected: Row | null;
  onSelect: (row: Row) => void;
}) {
  const times = applications.map((row) => new Date(String(row.last_modified)));
  const formatTime = (value?: Date) =>
    value
      ? value.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", timeZone })
      : "—";
  const byHour = new Map<number, { application: Row; time: Date }[]>();
  applications.forEach((application, index) => {
    const hour = zonedHour(times[index], timeZone);
    const bucket = byHour.get(hour) || [];
    bucket.push({ application, time: times[index] });
    byHour.set(hour, bucket);
  });
  const hourRows = [...byHour.entries()].sort(([a], [b]) => a - b);
  const maxCount = Math.max(1, ...hourRows.map(([, items]) => items.length));
  const [expandedHour, setExpandedHour] = useState<number | null>(null);

  return (
    <div className="card-body stage-activity-content">
      <div className="stage-activity-summary">
        <div>
          <strong>{applications.length}</strong>
          <span>Applications</span>
        </div>
        <div>
          <strong>{formatTime(times[0])}</strong>
          <span>First saved</span>
        </div>
        <div>
          <strong>{formatTime(times[times.length - 1])}</strong>
          <span>Last saved</span>
        </div>
      </div>
      <div className="stage-activity-chart-wrap">
        <div className="stage-activity-chart-heading">
          <div>
            <h3>Applications saved per hour</h3>
            <span>{formatDay(date)} · {timeZone} time</span>
          </div>
          <span className="stage-activity-legend">Click an hour to see its applications</span>
        </div>
        {hourRows.length ? (
          <div
            className="stage-hour-chart"
            aria-label={`${applications.length} applications grouped by hour`}
          >
            {hourRows.map(([hour, items]) => {
              const isExpanded = expandedHour === hour;
              return (
                <div className="stage-hour-bar-group" key={hour}>
                  <button
                    type="button"
                    className={`stage-hour-row stage-hour-row-button ${isExpanded ? "is-expanded" : ""}`}
                    onClick={() => setExpandedHour(isExpanded ? null : hour)}
                    aria-expanded={isExpanded}
                  >
                    <strong className="stage-hour-label">{formatHour(hour)}</strong>
                    <div className="stage-hour-track">
                      <div
                        className="stage-hour-bar"
                        style={{ width: `${(items.length / maxCount) * 100}%` }}
                      />
                    </div>
                    <span className="stage-hour-count">{items.length}</span>
                  </button>
                  {isExpanded && (
                    <ul className="stage-hour-detail-list">
                      {items.map(({ application, time }, index) => (
                        <li key={application.id || index}>
                          <button
                            type="button"
                            className={`stage-hour-detail-item ${selected?.id === application.id ? "is-selected" : ""}`}
                            onClick={() => onSelect(application)}
                          >
                            <span className="stage-hour-detail-time">{formatTime(time)}</span>
                            <span className="min-w-0 text-truncate">
                              {application.company_name || "Unknown company"} —{" "}
                              {application.position_title || "Application"}
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="stage-activity-empty">
            <i className="ti ti-chart-dots-3" />
            <strong>No applications on this day</strong>
            <span>Choose another date to view application times.</span>
          </div>
        )}
      </div>
      {selected && (
        <div className="stage-activity-detail" role="status">
          <div className="stage-activity-detail-time">
            {formatTime(new Date(String(selected.last_modified)))}
          </div>
          <div className="min-w-0">
            <strong>{selected.position_title || "Application"}</strong>
            <span>{selected.company_name || "Unknown company"}</span>
          </div>
          {selected.profile_name || selected.profile_key ? (
            <span className="badge bg-secondary-lt">
              {selected.profile_name || selected.profile_key}
            </span>
          ) : null}
        </div>
      )}
    </div>
  );
}

function formatDay(value: string) {
  return new Date(`${value}T00:00:00`).toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

function formatHour(hour: number) {
  const value = new Date();
  value.setHours(0, 0, 0, 0);
  value.setHours(hour);
  return value.toLocaleTimeString([], { hour: "numeric" });
}
