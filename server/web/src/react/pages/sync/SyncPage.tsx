import { useEffect, useState, type ReactNode } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, type Row } from "../../api";
import {
  arr,
  Card,
  ErrorBox,
  JsonDetail,
  Loading,
  Modal,
  NumberedPager,
  PageHeader,
  Search,
  SimpleTable,
  useNotice,
  when,
} from "../../ui";
import { PersonSelect, UserIdentity, userPerson } from "../../components/Avatar";

const HOUR_WINDOWS = [
  { label: "Last hour", value: 1 },
  { label: "24 hours", value: 24 },
  { label: "7 days", value: 24 * 7 },
  { label: "30 days", value: 24 * 30 },
];
const defaultPageSize = 25;

const TABS = [
  { key: "applications", label: "Application restore", icon: "ti-briefcase" },
  { key: "interviews", label: "Interview restore", icon: "ti-calendar-event" },
  { key: "reconcile", label: "Duplicate reconciliation", icon: "ti-scan" },
  { key: "activity", label: "Sync activity", icon: "ti-activity" },
] as const;
type TabKey = (typeof TABS)[number]["key"];

export function SyncPage() {
  const qc = useQueryClient(),
    notice = useNotice();
  const [help, setHelp] = useState(false);
  const [tab, setTab] = useState<TabKey>("applications");

  // Sync activity filters
  const [hours, setHours] = useState(24);
  const [user, setUser] = useState("");
  const [profile, setProfile] = useState("");
  const [deviceId, setDeviceId] = useState("");
  const debouncedDeviceId = useDebouncedValue(deviceId, 300);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(defaultPageSize);
  const hasActivityFilters = Boolean(user || profile || deviceId || hours !== 24);

  const users = useQuery({ queryKey: ["users"], queryFn: api.users }),
    profiles = useQuery({ queryKey: ["profiles"], queryFn: api.profiles });
  const activity = useQuery({
    queryKey: ["sync-activity", hours, user, profile, debouncedDeviceId, page, pageSize],
    queryFn: () =>
      api.syncActivity({
        hours,
        user_id: user || undefined,
        profile_key: profile || undefined,
        device_id: debouncedDeviceId || undefined,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      }),
    enabled: tab === "activity",
    placeholderData: keepPreviousData,
  });
  const trend = useQuery({
    queryKey: ["sync-trend", hours, user, profile, debouncedDeviceId],
    queryFn: () =>
      api.syncTrend({
        hours,
        user_id: user || undefined,
        profile_key: profile || undefined,
        device_id: debouncedDeviceId || undefined,
      }),
    enabled: tab === "activity",
    placeholderData: keepPreviousData,
  });

  const reconcile = useQuery({
    queryKey: ["reconcile", profile],
    queryFn: () => api.reconcile(profile || undefined),
    enabled: false,
  });

  // Application lookup & restore
  const [appQuery, setAppQuery] = useState("");
  const debouncedAppQuery = useDebouncedValue(appQuery, 300);
  const [appProfile, setAppProfile] = useState("");
  const [includeDeleted, setIncludeDeleted] = useState(true);
  const [appPage, setAppPage] = useState(1);
  const [appPageSize, setAppPageSize] = useState(defaultPageSize);
  const appSearch = useQuery({
    queryKey: [
      "admin-applications",
      debouncedAppQuery,
      appProfile,
      includeDeleted,
      appPage,
      appPageSize,
    ],
    queryFn: () =>
      api.adminApplications({
        q: debouncedAppQuery || undefined,
        profile_key: appProfile || undefined,
        include_deleted: includeDeleted,
        limit: appPageSize,
        offset: (appPage - 1) * appPageSize,
      }),
    enabled: tab === "applications",
    placeholderData: keepPreviousData,
  });
  async function restoreApplication(id: string) {
    try {
      const res = await api.restoreApplications([id]);
      notice.show(`Restored ${res.restored ?? 0} application(s)`);
      qc.invalidateQueries({ queryKey: ["admin-applications"] });
    } catch (e: any) {
      notice.show(e.message, "danger");
    }
  }

  // Interview lookup & restore
  const [ivQuery, setIvQuery] = useState("");
  const debouncedIvQuery = useDebouncedValue(ivQuery, 300);
  const [ivProfile, setIvProfile] = useState("");
  const [ivIncludeDeleted, setIvIncludeDeleted] = useState(true);
  const [ivPage, setIvPage] = useState(1);
  const [ivPageSize, setIvPageSize] = useState(defaultPageSize);
  const ivSearch = useQuery({
    queryKey: [
      "admin-interviews",
      debouncedIvQuery,
      ivProfile,
      ivIncludeDeleted,
      ivPage,
      ivPageSize,
    ],
    queryFn: () =>
      api.adminInterviews({
        q: debouncedIvQuery || undefined,
        profile_key: ivProfile || undefined,
        include_deleted: ivIncludeDeleted,
        limit: ivPageSize,
        offset: (ivPage - 1) * ivPageSize,
      }),
    enabled: tab === "interviews",
    placeholderData: keepPreviousData,
  });
  async function restoreInterview(id: string) {
    try {
      const res = await api.restoreInterviews([id]);
      notice.show(`Restored ${res.restored ?? 0} interview(s)`);
      qc.invalidateQueries({ queryKey: ["admin-interviews"] });
    } catch (e: any) {
      notice.show(e.message, "danger");
    }
  }

  const [data, setData] = useState<any>(null);
  const [appDetail, setAppDetail] = useState<any>(null);
  const [ivDetail, setIvDetail] = useState<any>(null);

  const events = arr(activity.data?.events);
  const activityTotal = Number(activity.data?.total ?? events.length);
  const totals: Row = activity.data?.totals || {};
  const trendBucketSeconds = Number(trend.data?.bucket_seconds ?? 0);
  const chartData = arr(trend.data?.points).map((p: Row) => ({
    label: formatBucketLabel(p.bucket, trendBucketSeconds),
    fullLabel: when(p.bucket),
    Accepted: Number(p.accepted || 0),
    Conflicts: Number(p.version_conflicts || 0),
    Rejected:
      Number(p.not_assigned || 0) +
      Number(p.unscoped || 0) +
      Number(p.other_rejected || 0) +
      Number(p.missing_parent || 0) +
      Number(p.time_conflict || 0) +
      Number(p.error || 0),
  }));
  const people = (users.data || []).map(userPerson);
  const profileOptions = profiles.data || [];
  const appRows = arr(appSearch.data?.applications);
  const appTotal = Number(appSearch.data?.total ?? appRows.length);
  const ivRows = arr(ivSearch.data?.interviews);
  const ivTotal = Number(ivSearch.data?.total ?? ivRows.length);

  return (
    <div className="stage-sync-page">
      <PageHeader
        text="Inspect synchronization activity, reconcile profiles, and repair broken data - without touching the database directly."
        action={
          <div className="d-flex gap-2">
            <button
              type="button"
              className="btn btn-outline-secondary"
              onClick={() => setHelp(true)}
            >
              <i className="ti ti-help me-1" />
              Help
            </button>
            <button
              className="btn btn-warning"
              onClick={async () => {
                try {
                  const x = await api.repairScope();
                  notice.show(`Repair complete: ${x.updated ?? x.repaired ?? 0} updated`);
                  qc.invalidateQueries({ queryKey: ["sync-activity"] });
                } catch (e: any) {
                  notice.show(e.message, "danger");
                }
              }}
            >
              <i className="ti ti-tool me-1" />
              Repair account scope
            </button>
          </div>
        }
      />

      <div className="stage-sync-tabs" role="tablist" aria-label="Sync tools">
        {TABS.map((t) => (
          <button
            type="button"
            key={t.key}
            role="tab"
            aria-selected={tab === t.key}
            className={`btn ${tab === t.key ? "btn-primary" : "btn-outline-secondary"}`}
            onClick={() => setTab(t.key)}
          >
            <i className={`ti ${t.icon} me-2`} />
            {t.label}
          </button>
        ))}
      </div>

      {tab === "applications" && (
        <Card
          title="Application lookup & restore"
          actions={
            <div className="d-flex align-items-center gap-2 flex-wrap">
              <label className="form-check form-check-single form-switch mb-0">
                <input
                  type="checkbox"
                  className="form-check-input"
                  checked={includeDeleted}
                  onChange={(e) => {
                    setIncludeDeleted(e.target.checked);
                    setAppPage(1);
                  }}
                />
                <span className="form-check-label">Include deleted</span>
              </label>
              <select
                className="form-select form-select-sm"
                style={{ minWidth: 160 }}
                value={appProfile}
                onChange={(e) => {
                  setAppProfile(e.target.value);
                  setAppPage(1);
                }}
              >
                <option value="">All profiles</option>
                {profileOptions.map((x) => (
                  <option value={x.key} key={x.key}>
                    {x.display_name || x.name || x.key}
                  </option>
                ))}
              </select>
              <div style={{ minWidth: 240 }}>
                <Search
                  value={appQuery}
                  onChange={(v) => {
                    setAppQuery(v);
                    setAppPage(1);
                  }}
                  placeholder="Company, position, or job link"
                />
              </div>
            </div>
          }
        >
          {appSearch.error ? (
            <div className="card-body">
              <ErrorBox error={appSearch.error} />
            </div>
          ) : (
            <>
              <NumberedPager
                page={appPage}
                total={appTotal}
                size={appPageSize}
                onChange={setAppPage}
                onSizeChange={(size) => {
                  setAppPageSize(size);
                  setAppPage(1);
                }}
                position="top"
              />
              <SimpleTable
                rows={appRows}
                onRow={setAppDetail}
                empty="No applications match this search."
                columns={[
                  { key: "company_name", label: "Company" },
                  { key: "position_title", label: "Position" },
                  {
                    key: "profile_name",
                    label: "Profile",
                    render: (r) => r.profile_name || r.profile_key || "—",
                  },
                  { key: "account", label: "Account", render: (r) => r.account || "—" },
                  { key: "status", label: "Status" },
                  { key: "applied_date", label: "Applied", render: (r) => when(r.applied_date) },
                  {
                    key: "deleted_at",
                    label: "Deleted",
                    render: (r) =>
                      r.deleted_at ? (
                        <span className="badge bg-danger-lt text-danger">
                          {when(r.deleted_at)}
                        </span>
                      ) : (
                        "—"
                      ),
                  },
                  {
                    key: "actions",
                    label: "",
                    render: (r) =>
                      r.deleted_at ? (
                        <button
                          type="button"
                          className="btn btn-sm btn-success"
                          onClick={(e) => {
                            e.stopPropagation();
                            restoreApplication(r.id);
                          }}
                        >
                          Restore
                        </button>
                      ) : null,
                  },
                ]}
              />
              <NumberedPager
                page={appPage}
                total={appTotal}
                size={appPageSize}
                onChange={setAppPage}
                onSizeChange={(size) => {
                  setAppPageSize(size);
                  setAppPage(1);
                }}
                position="bottom"
              />
            </>
          )}
        </Card>
      )}

      {tab === "interviews" && (
        <Card
          title="Interview lookup & restore"
          actions={
            <div className="d-flex align-items-center gap-2 flex-wrap">
              <label className="form-check form-check-single form-switch mb-0">
                <input
                  type="checkbox"
                  className="form-check-input"
                  checked={ivIncludeDeleted}
                  onChange={(e) => {
                    setIvIncludeDeleted(e.target.checked);
                    setIvPage(1);
                  }}
                />
                <span className="form-check-label">Include deleted</span>
              </label>
              <select
                className="form-select form-select-sm"
                style={{ minWidth: 160 }}
                value={ivProfile}
                onChange={(e) => {
                  setIvProfile(e.target.value);
                  setIvPage(1);
                }}
              >
                <option value="">All profiles</option>
                {profileOptions.map((x) => (
                  <option value={x.key} key={x.key}>
                    {x.display_name || x.name || x.key}
                  </option>
                ))}
              </select>
              <div style={{ minWidth: 240 }}>
                <Search
                  value={ivQuery}
                  onChange={(v) => {
                    setIvQuery(v);
                    setIvPage(1);
                  }}
                  placeholder="Company, title, or interviewer"
                />
              </div>
            </div>
          }
        >
          {ivSearch.error ? (
            <div className="card-body">
              <ErrorBox error={ivSearch.error} />
            </div>
          ) : (
            <>
              <NumberedPager
                page={ivPage}
                total={ivTotal}
                size={ivPageSize}
                onChange={setIvPage}
                onSizeChange={(size) => {
                  setIvPageSize(size);
                  setIvPage(1);
                }}
                position="top"
              />
              <SimpleTable
                rows={ivRows}
                onRow={setIvDetail}
                empty="No interviews match this search."
                columns={[
                  { key: "company_name", label: "Company", render: (r) => r.company_name || "—" },
                  {
                    key: "position_title",
                    label: "Position",
                    render: (r) => r.position_title || "—",
                  },
                  {
                    key: "profile_name",
                    label: "Profile",
                    render: (r) => r.profile_name || r.profile_key || "—",
                  },
                  { key: "title", label: "Interview" },
                  {
                    key: "interviewer_name",
                    label: "Interviewer",
                    render: (r) => r.interviewer_name || "—",
                  },
                  { key: "status", label: "Status" },
                  {
                    key: "start_time_utc",
                    label: "Scheduled",
                    render: (r) => when(r.start_time_utc),
                  },
                  {
                    key: "deleted_at",
                    label: "Deleted",
                    render: (r) =>
                      r.deleted_at ? (
                        <span className="badge bg-danger-lt text-danger">
                          {when(r.deleted_at)}
                        </span>
                      ) : (
                        "—"
                      ),
                  },
                  {
                    key: "actions",
                    label: "",
                    render: (r) =>
                      r.deleted_at ? (
                        <button
                          type="button"
                          className="btn btn-sm btn-success"
                          onClick={(e) => {
                            e.stopPropagation();
                            restoreInterview(r.id);
                          }}
                        >
                          Restore
                        </button>
                      ) : null,
                  },
                ]}
              />
              <NumberedPager
                page={ivPage}
                total={ivTotal}
                size={ivPageSize}
                onChange={setIvPage}
                onSizeChange={(size) => {
                  setIvPageSize(size);
                  setIvPage(1);
                }}
                position="bottom"
              />
            </>
          )}
        </Card>
      )}

      {tab === "reconcile" && (
        <Card
          title="Cross-account duplicate reconciliation"
          actions={
            <div className="d-flex align-items-center gap-2 flex-wrap">
              <select
                className="form-select form-select-sm"
                style={{ minWidth: 160 }}
                value={profile}
                onChange={(e) => setProfile(e.target.value)}
              >
                <option value="">All profiles</option>
                {profileOptions.map((x) => (
                  <option value={x.key} key={x.key}>
                    {x.display_name || x.name || x.key}
                  </option>
                ))}
              </select>
              <button
                className="btn btn-sm btn-primary"
                disabled={reconcile.isFetching}
                onClick={() => reconcile.refetch()}
              >
                <i className="ti ti-scan me-1" />
                {reconcile.isFetching ? "Scanning…" : "Run scan"}
              </button>
            </div>
          }
        >
          <div className="card-body">
            {reconcile.error ? (
              <ErrorBox error={reconcile.error} />
            ) : reconcile.data ? (
              <JsonDetail data={reconcile.data} />
            ) : (
              <p className="text-secondary mb-0">
                Choose an optional profile filter above, then run a scan to find legacy
                cross-account duplicates.
              </p>
            )}
          </div>
        </Card>
      )}

      {tab === "activity" && (
        <>
          <div className="card stage-perf-filter-card mb-3">
            <div className="card-body">
              <div className="stage-perf-filters">
                <div className="stage-perf-window stage-dash-period" aria-label="Time window">
                  {HOUR_WINDOWS.map((w) => (
                    <button
                      type="button"
                      className={`btn btn-sm ${hours === w.value ? "active" : "btn-outline-secondary"}`}
                      key={w.value}
                      onClick={() => {
                        setHours(w.value);
                        setPage(1);
                      }}
                    >
                      {w.label}
                    </button>
                  ))}
                </div>
                <div className="stage-perf-scope">
                  <label>
                    <span>Team member</span>
                    <PersonSelect
                      people={people}
                      value={user}
                      allLabel="All users"
                      ariaLabel="Filter by team member"
                      onChange={(id) => {
                        setUser(id);
                        setPage(1);
                      }}
                    />
                  </label>
                  <label>
                    <span>Profile</span>
                    <select
                      className="form-select form-select-sm"
                      value={profile}
                      onChange={(e) => {
                        setProfile(e.target.value);
                        setPage(1);
                      }}
                    >
                      <option value="">All profiles</option>
                      {profileOptions.map((x) => (
                        <option value={x.key} key={x.key}>
                          {x.display_name || x.name || x.key}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Device ID</span>
                    <div className="input-icon">
                      <span className="input-icon-addon">
                        <i className="ti ti-device-mobile" />
                      </span>
                      <input
                        className="form-control form-control-sm"
                        value={deviceId}
                        onChange={(e) => {
                          setDeviceId(e.target.value);
                          setPage(1);
                        }}
                        placeholder="Search by device"
                      />
                    </div>
                  </label>
                  {hasActivityFilters && (
                    <button
                      type="button"
                      className="btn btn-sm btn-outline-secondary stage-perf-clear"
                      onClick={() => {
                        setUser("");
                        setProfile("");
                        setDeviceId("");
                        setHours(24);
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

          {activity.isLoading ? (
            <Loading />
          ) : activity.error ? (
            <ErrorBox error={activity.error} />
          ) : (
            <>
              <div className="row row-cards g-2 g-md-3 mb-3">
                <SyncKpi label="Pushes" icon="ti-refresh" tone="primary" value={totals.pushes} />
                <SyncKpi
                  label="Accepted"
                  icon="ti-circle-check"
                  tone="success"
                  value={totals.accepted}
                />
                <SyncKpi
                  label="New inserts"
                  icon="ti-plus"
                  tone="azure"
                  value={totals.new_inserts}
                />
                <SyncKpi
                  label="Insert storms"
                  icon="ti-flame"
                  tone="danger"
                  value={totals.insert_storms}
                />
              </div>
              <div className="row row-cards g-2 g-md-3 mb-3">
                <SyncKpi
                  label="Not assigned"
                  icon="ti-user-off"
                  tone="warning"
                  value={totals.rejected_not_assigned}
                />
                <SyncKpi
                  label="Unscoped (no profile)"
                  icon="ti-link-off"
                  tone="warning"
                  value={totals.rejected_unscoped}
                />
                <SyncKpi
                  label="Version conflicts"
                  icon="ti-git-merge"
                  tone="danger"
                  value={totals.version_conflicts}
                />
                <SyncKpi
                  label="Other issues"
                  icon="ti-alert-triangle"
                  tone="danger"
                  value={
                    Number(totals.rejected_missing_parent || 0) +
                    Number(totals.rejected_time_conflict || 0) +
                    Number(totals.rejected_error || 0)
                  }
                />
              </div>

              <Card title="Sync trend" className="mb-3">
                <div className="card-body">
                  {trend.error ? (
                    <ErrorBox error={trend.error} />
                  ) : !chartData.length ? (
                    <p className="text-secondary mb-0">No sync activity in this window.</p>
                  ) : (
                    <div
                      className="stage-sync-trend-chart"
                      role="img"
                      aria-label="Accepted, conflicting, and rejected pushes over time"
                    >
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={chartData} margin={{ top: 8, right: 12, left: -14, bottom: 0 }}>
                          <CartesianGrid
                            stroke="var(--tblr-border-color)"
                            strokeDasharray="3 3"
                            vertical={false}
                          />
                          <XAxis
                            dataKey="label"
                            tick={{ fill: "var(--tblr-secondary)", fontSize: 11 }}
                            tickLine={false}
                            axisLine={false}
                            minTickGap={24}
                          />
                          <YAxis
                            allowDecimals={false}
                            tick={{ fill: "var(--tblr-secondary)", fontSize: 11 }}
                            tickLine={false}
                            axisLine={false}
                            width={44}
                          />
                          <RechartsTooltip
                            content={<SyncTrendTooltip />}
                            cursor={{ stroke: "var(--tblr-border-color)" }}
                          />
                          <Legend
                            verticalAlign="top"
                            height={28}
                            wrapperStyle={{ fontSize: 12 }}
                          />
                          <Line
                            type="monotone"
                            dataKey="Accepted"
                            stroke="var(--stage-green, #2f9e44)"
                            strokeWidth={2}
                            dot={false}
                          />
                          <Line
                            type="monotone"
                            dataKey="Conflicts"
                            stroke="var(--stage-red, #e03131)"
                            strokeWidth={2}
                            dot={false}
                          />
                          <Line
                            type="monotone"
                            dataKey="Rejected"
                            stroke="var(--stage-orange, #f59f00)"
                            strokeWidth={2}
                            dot={false}
                          />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  )}
                </div>
              </Card>

              <div className="card">
                <div className="card-header stage-panel-header">
                  <div>
                    <h3 className="card-title mb-0">Sync activity</h3>
                    <div className="text-secondary small">
                      {activityTotal.toLocaleString()} pushes
                    </div>
                  </div>
                  {activity.isFetching && (
                    <span className="text-secondary small" role="status">
                      <span className="spinner-border spinner-border-sm me-1" /> Updating…
                    </span>
                  )}
                </div>
                <NumberedPager
                  page={page}
                  total={activityTotal}
                  size={pageSize}
                  onChange={setPage}
                  onSizeChange={(size) => {
                    setPageSize(size);
                    setPage(1);
                  }}
                  position="top"
                />
                <SimpleTable
                  rows={events}
                  onRow={setData}
                  empty="No sync activity in this window."
                  columns={[
                    { key: "created_at", label: "Time", render: (r) => when(r.created_at) },
                    {
                      key: "user_email",
                      label: "User",
                      render: (row) =>
                        row.user_id || row.user_email ? (
                          <UserIdentity person={userPerson(row)} />
                        ) : (
                          "—"
                        ),
                    },
                    {
                      key: "profile_name",
                      label: "Profile",
                      render: (r) => r.profile_name || r.profile_key || "—",
                    },
                    { key: "accepted", label: "Accepted", className: "text-end" },
                    { key: "new_inserts", label: "New", className: "text-end" },
                    {
                      key: "rejected",
                      label: "Rejected",
                      className: "text-end",
                      render: (r) =>
                        Number(r.rejected_not_assigned || 0) + Number(r.other_rejected || 0),
                    },
                    { key: "version_conflicts", label: "Conflicts", className: "text-end" },
                    {
                      key: "flags",
                      label: "Flags",
                      render: (r) => (
                        <div className="d-flex gap-1 flex-wrap">
                          {arr(r.flags).map((flag) => (
                            <span className="badge bg-warning-lt text-warning" key={flag}>
                              {String(flag).replaceAll("_", " ")}
                            </span>
                          ))}
                        </div>
                      ),
                    },
                  ]}
                />
                <NumberedPager
                  page={page}
                  total={activityTotal}
                  size={pageSize}
                  onChange={setPage}
                  onSizeChange={(size) => {
                    setPageSize(size);
                    setPage(1);
                  }}
                  position="bottom"
                />
              </div>
            </>
          )}
        </>
      )}

      {data && (
        <Modal title="Sync event" onClose={() => setData(null)}>
          <JsonDetail data={data} />
        </Modal>
      )}
      {appDetail && (
        <Modal title="Application" onClose={() => setAppDetail(null)}>
          <JsonDetail data={appDetail} />
        </Modal>
      )}
      {ivDetail && (
        <Modal title="Interview" onClose={() => setIvDetail(null)}>
          <JsonDetail data={ivDetail} />
        </Modal>
      )}
      {help && <SyncHelpModal onClose={() => setHelp(false)} />}
      {notice.node}
    </div>
  );
}

function formatBucketLabel(iso: string, bucketSeconds: number): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  if (bucketSeconds <= 1800) {
    return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }
  if (bucketSeconds <= 14400) {
    return date.toLocaleString(undefined, {
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function SyncTrendTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload?: Row }>;
}) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;
  return (
    <div className="stage-trend-tooltip">
      <strong>{String(point.fullLabel || point.label)}</strong>
      <div>Accepted: {Number(point.Accepted || 0).toLocaleString()}</div>
      <div>Conflicts: {Number(point.Conflicts || 0).toLocaleString()}</div>
      <div>Rejected: {Number(point.Rejected || 0).toLocaleString()}</div>
    </div>
  );
}

function SyncKpi({
  label,
  icon,
  tone,
  value,
}: {
  label: string;
  icon: string;
  tone: string;
  value: unknown;
}) {
  const n = Number(value);
  return (
    <div className="col-6 col-lg">
      <div className="card">
        <div className="card-body d-flex justify-content-between align-items-start">
          <div>
            <div className="text-secondary">{label}</div>
            <div className="stage-metric-value">
              {Number.isFinite(n) ? n.toLocaleString() : "—"}
            </div>
          </div>
          <span className={`avatar bg-${tone}-lt text-${tone}`}>
            <i className={`ti ${icon}`} />
          </span>
        </div>
      </div>
    </div>
  );
}

function SyncHelpModal({ onClose }: { onClose: () => void }) {
  return (
    <Modal title="How to use the Sync tab" onClose={onClose} size="lg">
      <div className="stage-help-sections">
        <HelpSection icon="ti-briefcase" title="Application / interview lookup & restore">
          This is the tool to reach for when someone reports an application or interview is
          "missing." Search across every candidate's history by company, position, interviewer,
          or job link - check "Include deleted" to also find soft-deleted records. Find it here
          first, before assuming it's a sync bug. If a row shows a red <strong>Deleted</strong>{" "}
          badge, click <strong>Restore</strong> to bring it back into that profile's history.
        </HelpSection>
        <HelpSection icon="ti-scan" title="Duplicate reconciliation">
          Scans for legacy duplicate applications created across different accounts on the same
          profile. Optionally scope it to one profile with the filter first, then run the scan.
        </HelpSection>
        <HelpSection icon="ti-chart-line" title="Sync trend chart">
          Plots accepted, conflicting, and rejected pushes over time for the current filters -
          this is the fastest way to tell a healthy client from a stuck one. A device syncing
          normally shows accepted rows and a flat, near-zero conflicts/rejected line. A device
          stuck in a loop shows conflicts sitting at a constant, unmoving level push after push -
          exactly what a table of individual rows makes tedious to spot by eye. A downward
          conflicts trend after a fix means it's actively recovering; a line that never moves
          means it hasn't picked up the fix yet.
        </HelpSection>
        <HelpSection icon="ti-activity" title="Sync activity table & flags">
          One row per push a device made, for the exact detail behind a spike in the trend chart.
          The <strong>Flags</strong> column tells you which of six distinct problems occurred -
          they used to be lumped into three vague buckets, so now each shows up on its own:
          <ul className="stage-help-list">
            <li>
              <code>version_conflict</code> - server already has a newer copy of this row (the
              client's local edit was rejected in favor of the newer one). Expected occasionally
              on a shared profile; a problem only if the same rows keep repeating forever.
            </li>
            <li>
              <code>revision_conflict</code> - the stricter sibling of the above: the client's
              known revision number doesn't match the server's anymore.
            </li>
            <li>
              <code>not_assigned</code> - the device pushed a row for a profile it genuinely
              isn't assigned to. Worth checking profile assignments if this appears.
            </li>
            <li>
              <code>unscoped</code> - the row has no profile link at all. This is a client bug
              (a local row that never got tagged to a profile), not a permissions issue - it will
              be rejected 100% of the time, forever, until that client is updated.
            </li>
            <li>
              <code>missing_parent</code> - an interview's linked application isn't on the server
              yet, so the link can't be saved. Usually resolves once the application syncs.
            </li>
            <li>
              <code>time_conflict</code> - a real scheduling clash: two interviews overlap for
              the same person. Not a sync bug - an actual double-booking to resolve by hand.
            </li>
            <li>
              <code>error</code> - the save itself failed (malformed data, a server exception).
              Worth a closer look via the row's detail.
            </li>
            <li>
              <code>insert_storm</code> - an unusually large batch of brand-new rows in one push
              (duplicate import, not itself a failure - just worth a second look).
            </li>
          </ul>
          The summary cards and trend chart total the whole filtered time window, not just the
          current table page.
        </HelpSection>
        <HelpSection icon="ti-tool" title="Repair account scope">
          The button at the top of the page is a one-click bulk fix for a known class of
          account/profile-scope drift. Safe to run anytime - it only touches rows that are
          actually misscoped.
        </HelpSection>
      </div>
    </Modal>
  );
}

function HelpSection({
  icon,
  title,
  children,
}: {
  icon: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="stage-help-section">
      <div className="stage-help-section-title">
        <i className={`ti ${icon}`} />
        <strong>{title}</strong>
      </div>
      <div className="stage-help-text text-secondary mb-0">{children}</div>
    </div>
  );
}

function useDebouncedValue<T>(value: T, delay: number) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}
