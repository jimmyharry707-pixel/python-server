import { useEffect, useRef, useState, type ReactNode } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api, type Row } from "../../api";
import { useMe } from "../../auth";
import { arr, ErrorBox, Loading, Modal, NumberedPager, PageHeader, staff } from "../../ui";
import { formatInterviewRange } from "../../interviewTime";
import { UserIdentity, userPerson } from "../../components/Avatar";
import {
  type DateRange,
  localDate,
  type PresetPeriod,
  reportingPeriod,
  reportingPeriodMeta,
  type ReportingPeriod,
  shiftDate,
  validDateRange,
  weekStart,
} from "../../reportingPeriod";

const tz = -new Date().getTimezoneOffset();
const defaultPageSize = 25;

export function PerformancePage() {
  const me = useMe();
  const isStaff = staff(me.role);
  const isMember = String(me.role).toLowerCase() === "member";
  const [period, setPeriod] = useState<ReportingPeriod>("week");
  const today = localDate();
  const earliest = shiftDate(today, -89);
  const [customDraft, setCustomDraft] = useState<DateRange>({ start: weekStart(), end: today });
  const [customRange, setCustomRange] = useState<DateRange>(customDraft);
  const [person, setPerson] = useState("");
  const [profile, setProfile] = useState("");
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 300);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(defaultPageSize);
  const [detail, setDetail] = useState<Row | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const tableRef = useRef<HTMLDivElement>(null);
  const meta = reportingPeriod(period, customRange);

  const users = useQuery({ queryKey: ["users"], queryFn: api.users, enabled: isStaff });
  const profiles = useQuery({ queryKey: ["profiles"], queryFn: api.profiles, enabled: isStaff });
  const metrics = useQuery({
    queryKey: ["performance-metrics", isStaff, period, customRange, person, profile],
    queryFn: async () => {
      const shared = {
        granularity: meta.current.granularity,
        periods: meta.current.periods,
        user_id: isStaff ? person || undefined : undefined,
        contributor_id: !isStaff ? person || undefined : undefined,
        profile_key: profile || undefined,
        tz_offset: tz,
      };
      const request = (params: Row) => (isStaff ? api.metrics(params) : api.observability(params));
      const [current, previous] = await Promise.all([
        request({ ...shared, period_offset: meta.current.period_offset }),
        request({ ...shared, period_offset: meta.prior.period_offset }).catch(() => null),
      ]);
      return { ...current, previous };
    },
    placeholderData: keepPreviousData,
  });
  const range = dateRange(metrics.data);
  const applications = useQuery({
    queryKey: [
      "performance-apps",
      isStaff,
      period,
      customRange,
      person,
      profile,
      debouncedSearch,
      page,
      pageSize,
      range,
    ],
    queryFn: () => {
      const params = {
        user_id: isStaff ? person || undefined : undefined,
        contributor_id: !isStaff ? person || undefined : undefined,
        profile_key: profile || undefined,
        q: debouncedSearch || undefined,
        limit: pageSize,
        offset: (page - 1) * pageSize,
        ...range,
      };
      return isStaff ? api.adminApplications(params) : api.teamApplications(params);
    },
    enabled: Boolean(
      !metrics.isPlaceholderData && metrics.data?.range_start && metrics.data?.range_end,
    ),
    placeholderData: keepPreviousData,
  });

  useEffect(() => {
    const resultTotal = Number((applications.data as Row | undefined)?.total ?? 0);
    const lastPage = Math.max(1, Math.ceil(resultTotal / pageSize));
    if (!applications.isPlaceholderData && page > lastPage) setPage(lastPage);
  }, [applications.data, applications.isPlaceholderData, page, pageSize]);

  if (metrics.isLoading || (applications.isLoading && !applications.data)) return <Loading />;
  if (metrics.error || applications.error)
    return <ErrorBox error={metrics.error || applications.error} />;
  const raw = metrics.data || {};
  const comparison = {
    current: raw.totals_in_range || {},
    previous: raw.previous?.totals_in_range || {},
  };
  const appData = applications.data as Row;
  const appRows = arr(appData?.applications || appData?.items || appData);
  const profileRows = isStaff ? profiles.data || [] : arr(raw.profiles || appData?.profiles);
  const memberRows = arr(raw.per_members);
  const supporterRows = arr(raw.per_supporters);
  const total = Number(appData?.total ?? appRows.length);
  const hasFilters = Boolean(person || profile || search || period !== "week");
  const changePage = (nextPage: number) => {
    setPage(nextPage);
    requestAnimationFrame(() => tableRef.current?.focus({ preventScroll: false }));
  };

  return (
    <>
      <PageHeader text="Track application output, pipeline health, and team performance." />
      <div className="card stage-perf-filter-card mb-3">
        <div className="card-body">
          <div className="stage-perf-filters">
            <div className="stage-perf-window stage-dash-period" aria-label="Reporting period">
              {(Object.keys(reportingPeriodMeta) as PresetPeriod[]).map((key) => (
                <button
                  type="button"
                  className={`btn btn-sm ${period === key ? "active" : "btn-ghost-secondary"}`}
                  key={key}
                  onClick={() => {
                    setPeriod(key);
                    setPage(1);
                  }}
                >
                  {"shortLabel" in reportingPeriodMeta[key]
                    ? reportingPeriodMeta[key].shortLabel
                    : reportingPeriodMeta[key].label}
                </button>
              ))}
              <button
                type="button"
                className={`btn btn-sm ${period === "custom" ? "active" : "btn-ghost-secondary"}`}
                onClick={() => {
                  setPeriod("custom");
                  setPage(1);
                }}
              >
                Custom
              </button>
            </div>
            <div className="stage-perf-scope">
              {isStaff && (
                <label>
                  <span>Team member</span>
                  <select
                    className="form-select form-select-sm"
                    value={person}
                    onChange={(event) => {
                      setPerson(event.target.value);
                      setPage(1);
                    }}
                  >
                    <option value="">All team members</option>
                    {(users.data || []).map((user) => (
                      <option key={user.id} value={user.id}>
                        {user.name || user.email}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {isMember && (
                <label>
                  <span>Supporter</span>
                  <select
                    className="form-select form-select-sm"
                    value={person}
                    onChange={(event) => {
                      setPerson(event.target.value);
                      setPage(1);
                    }}
                  >
                    <option value="">All supporters</option>
                    {supporterRows.map((supporter) => (
                      <option key={supporter.user_id || supporter.id} value={supporter.user_id}>
                        {supporter.name || supporter.email}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label>
                <span>Profile</span>
                <select
                  className="form-select form-select-sm"
                  value={profile}
                  onChange={(event) => {
                    setProfile(event.target.value);
                    setPage(1);
                  }}
                >
                  <option value="">{isStaff ? "All profiles" : "All assigned profiles"}</option>
                  {profileRows.map((item) => (
                    <option key={item.key || item.id} value={item.key}>
                      {item.name || item.display_name || item.key}
                    </option>
                  ))}
                </select>
              </label>
              {hasFilters && (
                <button
                  type="button"
                  className="btn btn-sm btn-ghost-secondary stage-perf-clear"
                  onClick={() => {
                    setPerson("");
                    setProfile("");
                    setSearch("");
                    setPeriod("week");
                    setPage(1);
                  }}
                >
                  <i className="ti ti-filter-off me-1" /> Clear filters
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
      {period === "custom" && (
        <form
          className="stage-custom-range mb-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (validDateRange(customDraft, earliest, today)) {
              setCustomRange(customDraft);
              setPage(1);
            }
          }}
        >
          <label>
            <span>From</span>
            <input
              className="form-control form-control-sm"
              type="date"
              min={earliest}
              max={customDraft.end || today}
              value={customDraft.start}
              onChange={(event) =>
                setCustomDraft((value) => ({ ...value, start: event.target.value }))
              }
            />
          </label>
          <label>
            <span>To</span>
            <input
              className="form-control form-control-sm"
              type="date"
              min={customDraft.start || earliest}
              max={today}
              value={customDraft.end}
              onChange={(event) =>
                setCustomDraft((value) => ({ ...value, end: event.target.value }))
              }
            />
          </label>
          <button
            className="btn btn-sm btn-primary"
            type="submit"
            disabled={!validDateRange(customDraft, earliest, today)}
          >
            Apply range
          </button>
          <span className="text-secondary small">Up to 90 days</span>
        </form>
      )}
      {raw.range_start && (
        <div className="stage-perf-range text-secondary small mb-3">
          <i className="ti ti-calendar me-1" /> {meta.label}: {formatDate(raw.range_start)} –{" "}
          {formatDate(raw.range_end)}
          {(metrics.isFetching || applications.isFetching) && (
            <span className="ms-2" role="status">
              <span className="spinner-border spinner-border-sm me-1" /> Updating…
            </span>
          )}
        </div>
      )}
      <div className="row row-deck row-cards g-2 g-md-3 mb-3">
        <Kpi
          label="Applications"
          icon="ti-briefcase"
          value={comparison.current.applications}
          previous={comparison.previous.applications}
          previousLabel={meta.previous}
        />
        <Kpi
          label="Interviews"
          icon="ti-calendar-event"
          value={comparison.current.interviews}
          previous={comparison.previous.interviews}
          previousLabel={meta.previous}
          tone="success"
        />
        <Kpi
          label="Interview rate"
          icon="ti-chart-funnel"
          value={rate(comparison.current)}
          previous={rate(comparison.previous)}
          previousLabel={meta.previous}
          tone="cyan"
          format="percent"
        />
      </div>
      {isStaff && (
        <div className="row row-cards g-2 g-md-3 mb-3">
          <div className="col-lg-7">
            <RoleTable
              title="Members"
              rows={memberRows}
              onSelect={(id) => {
                setPerson(id);
                setPage(1);
              }}
            />
          </div>
          <div className="col-lg-5">
            <RoleTable
              title="Supporters"
              rows={supporterRows}
              onSelect={(id) => {
                setPerson(id);
                setPage(1);
              }}
            />
          </div>
        </div>
      )}
      <div className="card stage-perf-applications" ref={tableRef} tabIndex={-1}>
        <div className="card-header stage-panel-header">
          <div>
            <h3 className="card-title mb-0">Applications</h3>
            <div className="text-secondary small">{total.toLocaleString()} results</div>
          </div>
          <div className="input-icon">
            <span className="input-icon-addon">
              <i className="ti ti-search" />
            </span>
            <input
              className="form-control form-control-sm"
              aria-label="Search applications"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setPage(1);
              }}
              placeholder="Search applications"
            />
          </div>
        </div>
        <NumberedPager
          page={page}
          total={total}
          size={pageSize}
          onChange={changePage}
          onSizeChange={(size) => {
            setPageSize(size);
            setPage(1);
          }}
          position="top"
        />
        <ApplicationTable
          rows={appRows}
          offset={(page - 1) * pageSize}
          isStaff={isStaff}
          onOpen={async (row) => {
            setDetailError("");
            if (!isStaff) {
              setDetail(row);
              return;
            }
            setDetailLoading(true);
            try {
              setDetail(await api.application(row.id));
            } catch (error) {
              setDetailError(error instanceof Error ? error.message : "Could not load details");
            } finally {
              setDetailLoading(false);
            }
          }}
          onClear={
            hasFilters
              ? () => {
                  setPerson("");
                  setProfile("");
                  setSearch("");
                  setPeriod("week");
                  setPage(1);
                }
              : undefined
          }
        />
        <NumberedPager
          page={page}
          total={total}
          size={pageSize}
          onChange={changePage}
          onSizeChange={(size) => {
            setPageSize(size);
            setPage(1);
          }}
          position="bottom"
        />
      </div>
      {detailLoading && (
        <div className="stage-detail-loading" role="status">
          <span className="spinner-border spinner-border-sm me-2" /> Loading application details…
        </div>
      )}
      {detailError && <div className="alert alert-danger mt-3">{detailError}</div>}
      {detail && (
        <Modal title="Application details" onClose={() => setDetail(null)}>
          <ApplicationDetail data={detail} />
        </Modal>
      )}
    </>
  );
}

function Kpi({
  label,
  icon,
  value,
  previous,
  tone = "primary",
  format = "number",
  previousLabel,
}: {
  label: string;
  icon: string;
  value: unknown;
  previous: unknown;
  tone?: string;
  format?: "number" | "percent";
  previousLabel: string;
}) {
  const delta = num(value) - num(previous);
  return (
    <div className="col-md-4">
      <div className="card">
        <div className="card-body d-flex justify-content-between align-items-start">
          <div>
            <div className="text-secondary">{label}</div>
            <div className="stage-metric-value">
              {format === "percent" ? `${num(value).toFixed(1)}%` : num(value).toLocaleString()}
            </div>
            <span
              className={`badge bg-${delta > 0 ? "success" : delta < 0 ? "danger" : "secondary"}-lt mt-2`}
            >
              <i
                className={`ti ti-${delta > 0 ? "arrow-up" : delta < 0 ? "arrow-down" : "minus"}`}
              />{" "}
              {format === "percent" ? Math.abs(delta).toFixed(1) : Math.abs(delta)} vs{" "}
              {previousLabel.toLowerCase()}
            </span>
          </div>
          <span className={`avatar bg-${tone}-lt text-${tone}`}>
            <i className={`ti ${icon}`} />
          </span>
        </div>
      </div>
    </div>
  );
}
function RoleTable({
  title,
  rows,
  onSelect,
}: {
  title: string;
  rows: Row[];
  onSelect: (id: string) => void;
}) {
  return (
    <div className="card h-100">
      <div className="card-header">
        <h3 className="card-title mb-0">{title}</h3>
      </div>
      {rows.length ? (
        <div className="table-responsive">
          <table className="table table-vcenter card-table table-sm mb-0">
            <thead>
              <tr>
                <th>User</th>
                <th className="text-end">Apps</th>
                <th className="text-end">Interviews</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr
                  key={row.user_id || index}
                  role="button"
                  tabIndex={0}
                  onClick={() => onSelect(row.user_id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") onSelect(row.user_id);
                  }}
                >
                  <td>
                    <UserIdentity person={userPerson(row)} />
                  </td>
                  <td className="text-end">{num(row.applications_in_range)}</td>
                  <td className="text-end">{num(row.interviews_in_range)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="card-body text-secondary">No {title.toLowerCase()} to show.</div>
      )}
    </div>
  );
}
function ApplicationTable({
  rows,
  offset,
  isStaff,
  onOpen,
  onClear,
}: {
  rows: Row[];
  offset: number;
  isStaff: boolean;
  onOpen: (row: Row) => void;
  onClear?: () => void;
}) {
  if (!rows.length)
    return (
      <div className="stage-perf-empty card-body text-center">
        <i className="ti ti-file-search" />
        <strong>No matching applications</strong>
        <span className="text-secondary">Try changing the search, period, or scope filters.</span>
        {onClear && (
          <button type="button" className="btn btn-sm btn-outline-primary" onClick={onClear}>
            Clear filters
          </button>
        )}
      </div>
    );
  return (
    <div className="table-responsive stage-perf-table-wrap">
      <table className="table table-vcenter card-table table-sm mb-0 stage-perf-table">
        <thead>
          <tr>
            <th className="stage-perf-col-number">#</th>
            <th>User</th>
            <th>Profile</th>
            <th>Company</th>
            <th>Position</th>
            <th className="stage-perf-col-status">Status</th>
            <th className="stage-perf-col-date">Applied</th>
            <th className="text-end stage-perf-col-interviews">Interviews</th>
            <th className="stage-perf-col-link">Link</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr
              key={row.id || index}
              className="stage-interactive-row"
              tabIndex={0}
              style={{ cursor: "pointer" }}
              onClick={() => onOpen(row)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onOpen(row);
                }
              }}
            >
              <td className="text-secondary">{offset + index + 1}</td>
              <td>
                <UserIdentity person={userPerson(row)} />
              </td>
              <td>
                <span className="badge bg-secondary-lt">
                  {row.profile_name || row.profile_key || "—"}
                </span>
              </td>
              <td className="stage-perf-truncate" title={String(row.company_name || "")}>
                {row.company_name || "—"}
              </td>
              <td className="stage-perf-truncate" title={String(row.position_title || "")}>
                {row.position_title || "—"}
              </td>
              <td>
                <Status value={row.status} />
              </td>
              <td>{formatDate(row.applied_date)}</td>
              <td className="text-end">{num(row.interview_count)}</td>
              <td>
                {row.job_link ? (
                  <a
                    className="btn btn-sm btn-ghost-secondary btn-icon"
                    href={row.job_link}
                    target="_blank"
                    rel="noreferrer"
                    aria-label={`Open job listing for ${row.position_title || row.company_name || "application"}`}
                    onClick={(event) => event.stopPropagation()}
                  >
                    <i className="ti ti-external-link" />
                  </a>
                ) : (
                  "—"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function Status({ value }: { value: unknown }) {
  const status = String(value || "unknown").toLowerCase();
  const tone = status.includes("offer")
    ? "success"
    : status.includes("reject")
      ? "danger"
      : status.includes("interview")
        ? "azure"
        : "secondary";
  return <span className={`badge bg-${tone}-lt text-${tone}`}>{status}</span>;
}
function ApplicationDetail({ data }: { data: Row }) {
  const application = data.application || data;
  const interviews = arr(data.interviews);
  return (
    <>
      <div className="mb-3">
        <Status value={application.status} />{" "}
        {application.job_link && (
          <a className="ms-2" href={application.job_link} target="_blank" rel="noreferrer">
            Job link
          </a>
        )}
      </div>
      <Detail label="User" value={<UserIdentity person={userPerson(application)} />} />
      <Detail label="Profile" value={application.profile_name || application.profile_key} />
      <Detail label="Company" value={application.company_name} />
      <Detail label="Position" value={application.position_title} />
      <Detail label="Applied" value={formatDate(application.applied_date)} />
      <Detail label="Notes" value={application.notes || "No notes"} />
      {interviews.length > 0 && (
        <div className="mt-4">
          <div className="stage-detail-section-label">Interviews ({interviews.length})</div>
          {interviews.map((interview, index) => (
            <div className="border-bottom py-2" key={interview.id || index}>
              <strong>{interview.title || interview.interview_type || "Interview"}</strong>
              <div className="text-secondary small">
                {formatInterviewRange(
                  interview.start_time_utc || interview.start_time,
                  interview.end_time_utc || interview.end_time,
                  interview.timezone,
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
function Detail({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="mb-3">
      <div className="stage-detail-section-label">{label}</div>
      <div>{value || "—"}</div>
    </div>
  );
}

function dateRange(metrics?: Row) {
  if (metrics?.range_start && metrics?.range_end)
    return { applied_from: metrics.range_start, applied_to: metrics.range_end };
  return {};
}
function rate(values: Row) {
  const applications = num(values.applications);
  return applications ? (num(values.interviews) / applications) * 100 : 0;
}
function useDebouncedValue<T>(value: T, delay: number) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}
function formatDate(value: unknown) {
  const date = new Date(String(value || ""));
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}
function num(value: unknown) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}
