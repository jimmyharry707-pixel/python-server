import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import FullCalendar from "@fullcalendar/react";
import dayGridPlugin from "@fullcalendar/daygrid";
import timeGridPlugin from "@fullcalendar/timegrid";
import listPlugin from "@fullcalendar/list";
import interactionPlugin from "@fullcalendar/interaction";
import type { EventContentArg, EventInput } from "@fullcalendar/core";
import { api, type Row } from "../../api";
import { useMe } from "../../auth";
import { ErrorBox, Modal, PageHeader, staff } from "../../ui";
import { StateMessage } from "../../components/primitives";
import { Avatar, UserIdentity, userPerson } from "../../components/Avatar";
import { formatInterviewRange as formatRange, interviewDate } from "../../interviewTime";
import { PipelineBoard, PipelineModal } from "./PipelineView";
import { InsightsView } from "./InsightsView";

type Range = { start: string; end: string };
const colors = ["#1261d7", "#2fb344", "#ae3ec9", "#f59f00", "#d63939", "#4196ff"];
// A pursuit that ended is not a pipeline, and one with nothing scheduled and
// weeks of silence is not one you can act on today. Both are hidden by
// default, same as the desktop client's pipeline tab - on a real database
// this is the difference between 20 active rows and 77 mixed together.
const INACTIVE_PIPELINE_STATES = new Set(["stalled", "rejected", "withdrawn"]);

export function InterviewsPage() {
  const me = useMe();
  const isStaff = staff(me.role);
  const [range, setRange] = useState<Range | null>(null);
  const [scope, setScope] = useState(isStaff ? "all" : "mine");
  const [personId, setPersonId] = useState("");
  const [profileId, setProfileId] = useState("");
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");
  const [search, setSearch] = useState("");
  const [detail, setDetail] = useState<Row | null>(null);
  const compact = useCompactCalendar();
  const [viewName, setViewName] = useState(compact ? "listWeek" : "timeGridWeek");
  const [view, setView] = useState<"calendar" | "board" | "insights">("calendar");
  const [state, setState] = useState("");
  const [month, setMonth] = useState("");
  const [pipelineRow, setPipelineRow] = useState<Row | null>(null);
  const [showAllStates, setShowAllStates] = useState(false);
  const isPipeline = view === "board";
  const isInsights = view === "insights";
  // Once more than one person's pursuits can be on screen, lead each row with
  // whose it is. Narrowing to a single person (the Person filter) makes that
  // redundant, so it drops away rather than repeating the same name everywhere.
  const showPipelineOwner = scope === "all" && !personId;

  const catalog = useQuery({
    queryKey: ["interview-filter-catalog"],
    enabled: isStaff,
    queryFn: async () => {
      const [users, profiles] = await Promise.all([api.users(), api.profiles()]);
      return { users: array(users), profiles: array(profiles) };
    },
  });
  // The pipeline deliberately ignores the calendar's date window: a pursuit's
  // position depends on rounds that may have happened months ago, and windowing
  // them out would silently understate how far along it is.
  const pipeline = useQuery({
    queryKey: ["interview-pipeline", scope, personId, profileId, state, month],
    enabled: isPipeline,
    queryFn: () =>
      api.interviewPipeline({
        scope,
        assigned_user_id: isStaff ? personId : "",
        profile_id: isStaff ? profileId : "",
        state,
        month,
        limit: 5000,
      }),
  });
  const query = useQuery({
    queryKey: ["interviews", scope, personId, profileId, status, range],
    enabled: Boolean(range) && view === "calendar",
    queryFn: async () => {
      const result = await api.interviews({
        scope,
        assigned_user_id: isStaff ? personId : "",
        profile_id: isStaff ? profileId : "",
        status,
        from: range!.start,
        to: range!.end,
        limit: 5000,
      });
      return Array.isArray(result) ? result : array((result as Row)?.interviews);
    },
  });

  const rows = query.data || [];
  const types = useMemo(
    () => [...new Set(rows.map((row) => text(row.interview_type)).filter(Boolean))].sort(),
    [rows],
  );
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return rows.filter((row) => {
      if (type && text(row.interview_type) !== type) return false;
      return !needle || searchable(row).some((value) => text(value).toLowerCase().includes(needle));
    });
  }, [rows, search, type]);
  const colorByOwner = useMemo(() => {
    const keys = [...new Set(filtered.map(ownerKey))];
    return new Map(keys.map((key) => [key, ownerColor(key)]));
  }, [filtered]);
  const events = useMemo(() => toCalendarEvents(filtered, colorByOwner), [filtered, colorByOwner]);

  const months = useMemo(() => array(pipeline.data?.months).map(String), [pipeline.data]);
  // The state dropdown, when set, is an explicit ask and wins outright. With
  // it left on "All states", default to what the desktop client shows:
  // active and offer pursuits, with stalled/rejected/withdrawn one toggle away.
  const applyActiveDefault = !state && !showAllStates;
  const hiddenInactiveCount = useMemo(() => {
    if (!applyActiveDefault) return 0;
    return array(pipeline.data?.applications).filter((row) =>
      INACTIVE_PIPELINE_STATES.has(text(row.pipeline_state)),
    ).length;
  }, [pipeline.data, applyActiveDefault]);
  const pipelineRows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const all = array(pipeline.data?.applications);
    const base = applyActiveDefault
      ? all.filter((row) => !INACTIVE_PIPELINE_STATES.has(text(row.pipeline_state)))
      : all;
    if (!needle) return base;
    return base.filter((row) =>
      [row.company_name, row.position_title, row.profile_name, row.next_step].some((value) =>
        text(value).toLowerCase().includes(needle),
      ),
    );
  }, [pipeline.data, search, applyActiveDefault]);

  // The open drawer must not keep showing the snapshot it was opened with: a
  // status edit inside it changes the row's derived state, and a stale header
  // would contradict the board behind it. Re-resolve by id after every refetch,
  // falling back to the snapshot when the edit moved the row out of the active
  // filter (there is nothing fresher to show, and closing mid-read is worse).
  const openRow = useMemo(() => {
    if (!pipelineRow) return null;
    const id = text(pipelineRow.application_id);
    return pipelineRows.find((row) => text(row.application_id) === id) || pipelineRow;
  }, [pipelineRow, pipelineRows]);

  return (
    <>
      <PageHeader text="Track where every application sits in its interview process, and when each round happens." />
      <div className="card">
        <div className="card-header stage-pipe-tabs-header">
          <div className="stage-pipe-tabs" role="tablist" aria-label="Interview views">
            {(
              [
                ["calendar", "Calendar", "ti-calendar-event"],
                ["board", "Pipeline", "ti-layout-kanban"],
                ["insights", "Insights", "ti-chart-histogram"],
              ] as const
            ).map(([key, label, icon]) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={view === key}
                className={`stage-pipe-tab${view === key ? " is-active" : ""}`}
                onClick={() => setView(key)}
              >
                <i className={`ti ${icon}`} /> {label}
              </button>
            ))}
          </div>
        </div>
        {!isInsights && (
        <div className="card-header stage-panel-header stage-iv-filter-bar">
          <div className="stage-iv-filters">
            {isStaff && (
              <>
                <Filter
                  label="Scope"
                  value={scope}
                  onChange={(value) => {
                    setScope(value);
                    setPersonId("");
                    setProfileId("");
                  }}
                >
                  <option value="all">Everyone</option>
                  <option value="mine">My assignments</option>
                </Filter>
                <Filter
                  label="Person"
                  value={personId}
                  onChange={(value) => {
                    setPersonId(value);
                    if (value) setScope("all");
                  }}
                >
                  <option value="">All people</option>
                  {(catalog.data?.users || []).map((user) => (
                    <option key={text(user.id)} value={text(user.id)}>
                      {personLabel(user)}
                    </option>
                  ))}
                </Filter>
                <Filter
                  label="Profile"
                  value={profileId}
                  onChange={(value) => {
                    setProfileId(value);
                    if (value) setScope("all");
                  }}
                >
                  <option value="">All profiles</option>
                  {(catalog.data?.profiles || []).map((profile) => (
                    <option key={text(profile.id)} value={text(profile.id)}>
                      {profileLabel(profile)}
                    </option>
                  ))}
                </Filter>
              </>
            )}
            {!isStaff && (
              <div className="stage-iv-filter stage-iv-scope-toggle">
                <span>Show interviews</span>
                <div className="btn-group btn-group-sm" role="group" aria-label="Interview scope">
                  <button
                    type="button"
                    className={`btn ${scope === "mine" ? "btn-primary" : "btn-outline-secondary"}`}
                    aria-pressed={scope === "mine"}
                    onClick={() => setScope("mine")}
                  >
                    Mine
                  </button>
                  <button
                    type="button"
                    className={`btn ${scope === "all" ? "btn-primary" : "btn-outline-secondary"}`}
                    aria-pressed={scope === "all"}
                    onClick={() => setScope("all")}
                  >
                    Everyone
                  </button>
                </div>
              </div>
            )}
            {isPipeline ? (
              <>
                <Filter label="Month" value={month} onChange={setMonth}>
                  <option value="">All time</option>
                  {months.map((value) => (
                    <option key={value} value={value}>
                      {monthLabel(value)}
                    </option>
                  ))}
                </Filter>
                <Filter label="State" value={state} onChange={setState}>
                  <option value="">All states</option>
                  <option value="active">Active</option>
                  <option value="stalled">Stalled</option>
                  <option value="offer">Offer</option>
                  <option value="rejected">Closed</option>
                  <option value="withdrawn">Withdrawn</option>
                </Filter>
                <label className="stage-iv-filter stage-iv-scope-toggle">
                  <span>&nbsp;</span>
                  <button
                    type="button"
                    className={`btn btn-sm ${showAllStates ? "btn-primary" : "btn-outline-secondary"}`}
                    aria-pressed={showAllStates}
                    disabled={Boolean(state)}
                    title={
                      state
                        ? "Clear the State filter above to use this toggle"
                        : "Include pursuits that have gone quiet, or ended in a rejection or a withdrawal"
                    }
                    onClick={() => setShowAllStates((value) => !value)}
                  >
                    <i className="ti ti-history-toggle" /> Show stalled & closed
                  </button>
                </label>
              </>
            ) : (
              <>
                <Filter label="Status" value={status} onChange={setStatus}>
                  <option value="">All statuses</option>
                  <option value="scheduled">Scheduled</option>
                  <option value="completed">Completed</option>
                  <option value="cancelled">Cancelled</option>
                  <option value="rescheduled">Rescheduled</option>
                </Filter>
                <Filter label="Type" value={type} onChange={setType}>
                  <option value="">All types</option>
                  {types.map((item) => (
                    <option key={item}>{item}</option>
                  ))}
                </Filter>
              </>
            )}
            <label className="stage-iv-filter stage-iv-search">
              <span>Search</span>
              <div className="input-icon">
                <span className="input-icon-addon">
                  <i className="ti ti-search" />
                </span>
                <input
                  className="form-control form-control-sm"
                  type="search"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Company, role, or interviewer"
                />
              </div>
            </label>
          </div>
          <span className="text-secondary small stage-iv-count">
            {isPipeline ? (
              <>
                {pipelineRows.length} application{pipelineRows.length === 1 ? "" : "s"}
                {hiddenInactiveCount
                  ? ` · ${hiddenInactiveCount} stalled or closed hidden`
                  : ""}
              </>
            ) : (
              <>
                {filtered.length}
                {filtered.length !== rows.length ? ` of ${rows.length}` : ""} interview
                {filtered.length === 1 ? "" : "s"}
              </>
            )}
          </span>
        </div>
        )}
        {isInsights ? (
          <div className="card-body">
            <InsightsView isStaff={isStaff} people={catalog.data?.users} />
          </div>
        ) : isPipeline ? (
          <div className="card-body stage-pipe-body">
            <div className="stage-iv-context mb-3">
              <span>
                <i className="ti ti-stack-2" /> One row per application
                {month ? ` · active in ${monthLabel(month)}` : ""}
              </span>
              <PipelineTotals totals={pipeline.data?.totals} />
            </div>
            {pipeline.error && <ErrorBox error={pipeline.error} />}
            {pipeline.isFetching && (
              <div className="text-secondary small mb-2">
                <div className="spinner-border spinner-border-sm" /> Loading pipeline…
              </div>
            )}
            {!pipeline.isFetching && !pipeline.error && !pipelineRows.length && (
              <StateMessage
                icon="ti-timeline-event"
                title={
                  hiddenInactiveCount
                    ? "Nothing active right now"
                    : month
                      ? `No activity in ${monthLabel(month)}`
                      : "Nothing in the pipeline yet"
                }
                description={
                  hiddenInactiveCount
                    ? `${hiddenInactiveCount} pursuit${hiddenInactiveCount === 1 ? " is" : "s are"} stalled or closed. Toggle "Show stalled & closed" to see ${hiddenInactiveCount === 1 ? "it" : "them"}.`
                    : month
                      ? "No application had a round that month. Try another month, or All time."
                      : "An application joins the pipeline as soon as it has one interview."
                }
              />
            )}
            {Boolean(pipelineRows.length) && (
              <PipelineBoard
                rows={pipelineRows}
                onOpen={setPipelineRow}
                showOwner={showPipelineOwner}
              />
            )}
          </div>
        ) : (
          <div className="card-body">
            <div className="stage-iv-context mb-3">
              <span>
                <i className="ti ti-calendar" /> {calendarViewLabel(viewName)}
              </span>
              <span>
                <i className="ti ti-world" /> Times shown in {localTimeZone()}
              </span>
            </div>
            {(query.error || catalog.error) && <ErrorBox error={query.error || catalog.error} />}
            <div className="stage-iv-calendar-shell">
              <div className="stage-iv-calendar">
                <FullCalendar
                  key={compact ? "compact" : "wide"}
                  plugins={[dayGridPlugin, timeGridPlugin, listPlugin, interactionPlugin]}
                  initialView={compact ? "listWeek" : "timeGridWeek"}
                  headerToolbar={{
                    left: "prev,next today",
                    center: "title",
                    right: "dayGridMonth,timeGridWeek,timeGridDay,listWeek",
                  }}
                  buttonText={{
                    today: "Today",
                    month: "Month",
                    week: "Week",
                    day: "Day",
                    list: "List",
                  }}
                  events={events}
                  datesSet={(info) => {
                    setViewName(info.view.type);
                    setRange({ start: info.start.toISOString(), end: info.end.toISOString() });
                  }}
                  eventClick={(info) => setDetail(info.event.extendedProps.interview as Row)}
                  eventContent={renderEvent}
                  nowIndicator
                  navLinks
                  stickyHeaderDates
                  allDaySlot={false}
                  dayMaxEvents={4}
                  slotMinTime="06:00:00"
                  slotMaxTime="22:00:00"
                  height="auto"
                  eventTimeFormat={{ hour: "numeric", minute: "2-digit", meridiem: "short" }}
                />
              </div>
              {query.isFetching && (
                <div className="stage-iv-calendar-state">
                  <div className="spinner-border spinner-border-sm" /> Loading interviews…
                </div>
              )}
              {!query.isFetching && !query.error && !filtered.length && (
                <div className="stage-iv-calendar-empty">
                  <StateMessage
                    icon="ti-calendar-off"
                    title={
                      rows.length
                        ? "No matching interviews"
                        : `No interviews in this ${calendarViewLabel(viewName).replace(" view", "").toLowerCase()}`
                    }
                    description={
                      rows.length
                        ? "Try clearing or changing the filters."
                        : `Move to another date range or check back after interviews are scheduled.`
                    }
                  />
                </div>
              )}
            </div>
          </div>
        )}
      </div>
      {openRow && <PipelineModal row={openRow} onClose={() => setPipelineRow(null)} />}
      {detail && (
        <Modal
          size="lg"
          variant="center"
          title={eventTitle(detail)}
          onClose={() => setDetail(null)}
        >
          <InterviewDetail interview={detail} />
        </Modal>
      )}
    </>
  );
}

/** "2026-09" -> "September 2026". Parsed as UTC noon so the month never slips
 *  a day either side of the boundary in the viewer's timezone. */
function monthLabel(value: string) {
  const [year, month] = value.split("-").map(Number);
  if (!year || !month) return value;
  return new Intl.DateTimeFormat(undefined, { month: "long", year: "numeric" }).format(
    new Date(Date.UTC(year, month - 1, 1, 12)),
  );
}

function PipelineTotals({ totals }: { totals?: unknown }) {
  const data = (totals || {}) as Row;
  return (
    <span className="stage-pipe-totals">
      <span>
        <i className="ti ti-player-play" /> {Number(data.active) || 0} active
      </span>
      <span>
        <i className="ti ti-hourglass-low" /> {Number(data.stalled) || 0} stalled
      </span>
      <span>
        <i className="ti ti-confetti" /> {Number(data.offers) || 0} offer
        {Number(data.offers) === 1 ? "" : "s"}
      </span>
    </span>
  );
}

export function toCalendarEvents(rows: Row[], colorByOwner?: Map<string, string>): EventInput[] {
  return rows.flatMap((row) => {
    const start = interviewDate(row.start_time_utc, row.timezone);
    if (!start) return [];
    const end = interviewDate(row.end_time_utc, row.timezone);
    const color = colorByOwner?.get(ownerKey(row)) || colors[0];
    return [
      {
        id: text(row.id) || `${start.toISOString()}-${eventTitle(row)}`,
        title: eventTitle(row),
        start,
        end: end && end > start ? end : undefined,
        backgroundColor: color,
        borderColor: color,
        extendedProps: { interview: row },
      },
    ];
  });
}

function renderEvent(info: EventContentArg) {
  const row = info.event.extendedProps.interview as Row;
  if (info.view.type.startsWith("list")) {
    return (
      <div className="stage-iv-list-grid">
        <div className="stage-iv-list-interview">
          <Avatar person={userPerson(row)} size="xs" showManagerCrown={false} />
          <span className="stage-iv-list-value" title={info.event.title}>
            {info.event.title}
          </span>
        </div>
        <ListValue label="Applied" value={formatApplicationDate(row.application_applied_date)} />
        <ListValue label="Company" value={row.company_name} />
        <ListValue label="Timezone" value={row.timezone} />
      </div>
    );
  }
  return (
    <div className="stage-iv-event-compact">
      <Avatar person={userPerson(row)} size="xs" showManagerCrown={false} />
      <span className="stage-iv-event-title" title={info.event.title}>
        {info.event.title}
      </span>
    </div>
  );
}

function ListValue({ label, value }: { label: string; value: unknown }) {
  return (
    <span className="stage-iv-list-field">
      <span className="stage-iv-list-label">{label}</span>
      <span className="stage-iv-list-value">{text(value) || "—"}</span>
    </span>
  );
}

function InterviewDetail({ interview }: { interview: Row }) {
  return (
    <div className="stage-iv-detail">
      <div className="stage-iv-detail-header">
        {interview.user_name || interview.user_email ? (
          <UserIdentity person={userPerson(interview)} />
        ) : (
          <span className="text-secondary">Unassigned interview</span>
        )}
        <div className="stage-iv-detail-status">{statusBadge(interview.status)}</div>
      </div>

      <div className="stage-iv-detail-when">
        <span className="stage-iv-detail-icon">
          <i className="ti ti-calendar-time" />
        </span>
        <div>
          <div className="stage-detail-section-label">Date and time</div>
          <div className="stage-iv-detail-when-value">{formatInterviewRange(interview)}</div>
          <div className="text-secondary small">
            Source timezone: {text(interview.timezone) || "—"}
          </div>
        </div>
      </div>

      <div className="stage-detail-section-label mb-2">Interview details</div>
      <div className="stage-detail-grid stage-iv-detail-grid">
        <Detail
          icon="ti-user-square-rounded"
          label="Profile"
          value={interview.profile_name || interview.profile_key}
        />
        <Detail icon="ti-category" label="Type" value={interview.interview_type} />
        <Detail icon="ti-building" label="Company" value={interview.company_name} />
        <Detail icon="ti-briefcase" label="Position" value={interview.position_title} />
        <Detail icon="ti-map-pin" label="Location" value={interview.location} />
        <Detail icon="ti-user" label="Interviewer" value={interview.interviewer_name} />
      </div>
      {(interview.description || interview.notes) && (
        <div className="stage-iv-detail-notes">
          {interview.description && (
            <DetailText label="Description" value={interview.description} />
          )}
          {interview.notes && <DetailText label="Notes" value={interview.notes} />}
        </div>
      )}
    </div>
  );
}

function Filter({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
}) {
  return (
    <label className="stage-iv-filter">
      <span>{label}</span>
      <select
        className="form-select form-select-sm"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {children}
      </select>
    </label>
  );
}
function Detail({ icon, label, value }: { icon?: string; label: string; value: unknown }) {
  return (
    <div className="stage-iv-detail-field">
      {icon && <i className={`ti ${icon} stage-iv-detail-field-icon`} />}
      <div>
        <div className="stage-detail-section-label">{label}</div>
        <div className="stage-iv-detail-field-value">{text(value) || "—"}</div>
      </div>
    </div>
  );
}
function DetailText({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="stage-iv-detail-text">
      <div className="stage-detail-section-label">{label}</div>
      <div className="text-secondary" style={{ whiteSpace: "pre-wrap" }}>
        {text(value)}
      </div>
    </div>
  );
}
function statusBadge(value: unknown) {
  const status = text(value).toLowerCase() || "scheduled";
  const tone =
    status === "completed"
      ? "success"
      : status === "cancelled"
        ? "danger"
        : status === "rescheduled"
          ? "warning"
          : "azure";
  return <span className={`badge bg-${tone}-lt text-capitalize`}>{status}</span>;
}
function eventTitle(row: Row) {
  return (
    text(row.title) ||
    [row.company_name, row.position_title].filter(Boolean).join(" · ") ||
    "Interview"
  );
}
function ownerKey(row: Row) {
  return text(row.user_id || row.user_email || row.user_name) || "Unassigned";
}
function ownerColor(key: string) {
  let hash = 0;
  for (const character of key) hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  return colors[hash % colors.length];
}
function profileLabel(row: Row) {
  return text(row.name || row.key || row.email) || "Unnamed profile";
}
function personLabel(row: Row) {
  return text(row.name || row.email) || "Unnamed person";
}
function searchable(row: Row) {
  return [
    row.title,
    row.company_name,
    row.position_title,
    row.profile_name,
    row.profile_key,
    row.interviewer_name,
    row.location,
    row.notes,
  ];
}
function formatInterviewRange(interview: Row) {
  return formatRange(interview.start_time_utc, interview.end_time_utc, interview.timezone);
}
function formatApplicationDate(value: unknown) {
  const date = new Date(text(value));
  return Number.isNaN(date.getTime())
    ? "—"
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}
function useCompactCalendar() {
  const [compact, setCompact] = useState(() => window.matchMedia("(max-width: 767.98px)").matches);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 767.98px)");
    const update = () => setCompact(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return compact;
}
function calendarViewLabel(view: string) {
  if (view === "dayGridMonth") return "Month view";
  if (view === "timeGridDay") return "Day view";
  if (view.startsWith("list")) return "Agenda view";
  return "Week view";
}
function localTimeZone() {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "your local timezone";
}
function text(value: unknown) {
  return value === null || value === undefined ? "" : String(value).trim();
}
function array(value: unknown): Row[] {
  return Array.isArray(value) ? value : [];
}
