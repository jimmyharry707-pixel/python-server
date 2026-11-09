import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type Row } from "../../api";
import { useMe } from "../../auth";
import { arr, ErrorBox, Loading, staff } from "../../ui";
import { UserIdentity } from "../../components/Avatar";
import { TimezoneBadge } from "../../components/Timezone";
import { interviewDate } from "../../interviewTime";
import jsVectorMap from "jsvectormap";
import "jsvectormap/dist/jsvectormap.min.css";
import "../../vendor/us-aea-en";
import {
  type DateRange,
  localDate,
  type PresetPeriod,
  reportingPeriod,
  reportingPeriodMeta as periodMeta,
  type ReportingPeriod as Period,
  shiftDate,
  validDateRange,
  weekStart,
} from "../../reportingPeriod";

const tz = -new Date().getTimezoneOffset();

export function DashboardPage() {
  const me = useMe();
  return staff(me.role) ? <StaffDashboard /> : <MemberDashboard />;
}

function StaffDashboard() {
  const [period, setPeriod] = useState<Period>("week");
  const today = localDate();
  const earliest = shiftDate(today, -89);
  const [customDraft, setCustomDraft] = useState<DateRange>({ start: weekStart(), end: today });
  const [customRange, setCustomRange] = useState<DateRange>(customDraft);
  const meta = reportingPeriod(period, customRange);
  const query = useQuery({
    queryKey: ["staff-dashboard", period, customRange.start, customRange.end],
    queryFn: async () => {
      const issues: string[] = [];
      const optional = async <T,>(label: string, request: Promise<T>): Promise<T | null> => {
        try {
          return await request;
        } catch {
          issues.push(label);
          return null;
        }
      };
      const [overview, current, previous, trend, geo] = await Promise.all([
        api.overview(),
        api.metrics({ ...meta.current, tz_offset: tz }),
        optional("comparison", api.metrics({ ...meta.prior, tz_offset: tz })),
        optional("trend", api.metrics({ ...meta.trend, tz_offset: tz })),
        optional("geography", api.profilesGeo()),
      ]);
      return { overview, current, previous, trend, geo, issues };
    },
  });
  if (query.isLoading) return <Loading />;
  if (query.error) return <ErrorBox error={query.error} />;
  const data = query.data!;
  const current = data.current?.totals_in_range || {};
  const previous = data.previous?.totals_in_range || {};
  const members = arr(data.current?.per_members);
  const supporters = arr(data.current?.per_supporters);
  const active = (rows: Row[]) =>
    rows.filter((row) => num(row.applications_in_range) + num(row.interviews_in_range) > 0).length;
  const activeMembers = active(members);
  const activeSupporters = active(supporters);
  const applications = num(current.applications);
  const interviews = num(current.interviews);
  const previousApplications = num(previous.applications);
  const previousInterviews = num(previous.interviews);
  const interviewRate = applications ? (interviews / applications) * 100 : 0;
  const previousInterviewRate = previousApplications
    ? (previousInterviews / previousApplications) * 100
    : 0;
  const totalProfiles = num(data.geo?.total_profiles);
  const profileCoverage = totalProfiles ? (num(data.geo?.mapped) / totalProfiles) * 100 : 0;
  return (
    <>
      {data.issues.length > 0 && (
        <div className="alert alert-warning py-2 mb-3" role="status">
          <i className="ti ti-alert-circle me-2" />
          Some dashboard panels could not be refreshed: {data.issues.join(", ")}.
        </div>
      )}
      <div className="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3">
        <div className="d-flex flex-wrap gap-1 stage-dash-period">
          {(Object.keys(periodMeta) as PresetPeriod[]).map((key) => (
            <button
              type="button"
              key={key}
              className={`btn btn-sm ${key === period ? "active" : "btn-ghost-secondary"}`}
              onClick={() => setPeriod(key)}
            >
              {key === "trend12" ? "12 weeks" : periodMeta[key].label}
            </button>
          ))}
          <button
            type="button"
            className={`btn btn-sm ${period === "custom" ? "active" : "btn-ghost-secondary"}`}
            onClick={() => setPeriod("custom")}
          >
            Custom
          </button>
        </div>
        <Link className="btn btn-sm btn-outline-primary" to="/performance">
          View Performance <i className="ti ti-arrow-right ms-1" />
        </Link>
      </div>
      {period === "custom" && (
        <form
          className="stage-custom-range mb-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (validDateRange(customDraft, earliest, today)) setCustomRange(customDraft);
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
                setCustomDraft((range) => ({ ...range, start: event.target.value }))
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
                setCustomDraft((range) => ({ ...range, end: event.target.value }))
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
      <div className="stage-kpi-grid mb-3">
        <Metric
          label="Applications"
          value={applications}
          previous={data.previous ? previous.applications : undefined}
          icon="ti-briefcase"
          hint={meta.label}
          to="/performance"
        />
        <Metric
          label="Interviews"
          value={interviews}
          previous={data.previous ? previous.interviews : undefined}
          icon="ti-calendar-event"
          tone="success"
          hint={meta.label}
          to="/interviews"
        />
        <Metric
          label="Interview rate"
          value={interviewRate}
          displayValue={`${formatPercent(interviewRate)}%`}
          icon="ti-chart-funnel"
          tone="cyan"
          footer={
            data.previous ? (
              <>
                <Delta
                  current={interviewRate}
                  previous={previousInterviewRate}
                  decimals={1}
                  suffix=" pp"
                />{" "}
                <span className="text-secondary">vs {meta.previous.toLowerCase()}</span>
              </>
            ) : (
              <span className="text-secondary">Comparison unavailable</span>
            )
          }
          to="/performance"
        />
        <Metric
          label="Active contributors"
          value={activeMembers + activeSupporters}
          icon="ti-users-group"
          tone="warning"
          footer={
            <span className="text-secondary">
              {activeMembers} members · {activeSupporters} supporters
            </span>
          }
          to="/performance"
        />
      </div>
      <SecondaryMetrics
        profileCoverage={profileCoverage}
        mappedProfiles={num(data.geo?.mapped)}
        totalProfiles={totalProfiles}
        usersOnline={num(data.overview?.users_online)}
        usersTotal={num(data.overview?.users)}
        usersInactive={num(data.overview?.users_inactive)}
      />
      <div className="row row-cards g-2 g-md-3">
        <div className="col-lg-7">
          <Panel title="Activity trend" subtitle={trendSubtitle(data.trend)}>
            <Trend data={data.trend} />
          </Panel>
        </div>
        <div className="col-lg-5">
          <Panel
            title="Profiles by state"
            subtitle={`${num(data.geo?.mapped)} of ${num(data.geo?.total_profiles)} profiles mapped`}
            action="/profiles"
          >
            <Geo geo={data.geo} />
          </Panel>
        </div>
      </div>
      <div className="row row-cards g-2 g-md-3 mt-1">
        <div className="col-lg-7">
          <Panel
            title="Member ranking"
            subtitle={`Ranked by interviews, then applications · ${meta.label.toLowerCase()}`}
            action="/performance"
          >
            <Ranking rows={members} />
          </Panel>
        </div>
        <div className="col-lg-5">
          <Panel
            title="Supporter ranking"
            subtitle={`Ranked by applications · ${meta.label.toLowerCase()}`}
            action="/performance"
          >
            <Ranking rows={supporters} showInterviews={false} showTimezone />
          </Panel>
        </div>
      </div>
    </>
  );
}

function MemberDashboard() {
  const me = useMe();
  const [period, setPeriod] = useState<Period>("week");
  const today = localDate();
  const earliest = shiftDate(today, -89);
  const [customDraft, setCustomDraft] = useState<DateRange>({ start: weekStart(), end: today });
  const [customRange, setCustomRange] = useState<DateRange>(customDraft);
  const meta = reportingPeriod(period, customRange);
  const query = useQuery({
    queryKey: ["member-dashboard", period, customRange.start, customRange.end],
    queryFn: async () => {
      const now = new Date(),
        cutoff = new Date(),
        requestFrom = new Date(now),
        requestTo = new Date(now);
      cutoff.setDate(cutoff.getDate() + 45);
      // Fetch a padded UTC window, then apply the source-timezone conversion
      // used by the Interviews page before deciding what is upcoming.
      requestFrom.setDate(requestFrom.getDate() - 1);
      requestTo.setDate(requestTo.getDate() + 46);
      const [current, previous, trend, interviewResult, plan, colleagues] = await Promise.all([
        api.observability({ ...meta.current, tz_offset: tz }),
        api.observability({ ...meta.prior, tz_offset: tz }).catch(() => null),
        api.observability({ ...meta.trend, tz_offset: tz }).catch(() => null),
        api
          .interviews({
            scope: "mine",
            from: requestFrom.toISOString(),
            to: requestTo.toISOString(),
            limit: 200,
          })
          .catch(() => []),
        me.role.toLowerCase() === "supporter"
          ? null
          : api.workPlan({ week_start: workPlanWeekStart() }).catch(() => null),
        api.colleagues().catch(() => ({})),
      ]);
      return {
        current,
        previous,
        trend,
        interviews: upcomingInterviews(
          Array.isArray(interviewResult)
            ? interviewResult
            : arr((interviewResult as Row)?.interviews),
          now,
          cutoff,
        ),
        plan,
        colleagues,
      };
    },
  });
  if (query.isLoading) return <Loading />;
  if (query.error) return <ErrorBox error={query.error} />;
  const data = query.data!;
  const current = data.current?.totals_in_range || {};
  const previous = data.previous?.totals_in_range || {};
  const applicationsInRange = num(current.applications);
  const interviewsInRange = num(current.interviews);
  const previousApplications = num(previous.applications);
  const previousInterviews = num(previous.interviews);
  const interviewRate = applicationsInRange ? (interviewsInRange / applicationsInRange) * 100 : 0;
  const previousInterviewRate = previousApplications
    ? (previousInterviews / previousApplications) * 100
    : 0;
  const topMembers = arr(data.current?.per_members);
  const topSupporters = arr(data.current?.per_supporters);
  const activeMembers = topMembers.filter(
    (row) => num(row.applications_in_range) + num(row.interviews_in_range) > 0,
  ).length;
  const activeSupporters = topSupporters.filter(
    (row) => num(row.applications_in_range) + num(row.interviews_in_range) > 0,
  ).length;
  const colleagueData = data.colleagues as Row;
  const colleagues = arr(colleagueData?.colleagues || colleagueData);
  const colleagueSupporterIds = new Set(
    colleagues
      .filter((row) => String(row.role || "").toLowerCase() === "supporter")
      .map((row) => String(row.user_id || row.id || "")),
  );
  const visibleSupporters = topSupporters.slice(0, topMembers.length);
  const canUseWorkPlan = me.role.toLowerCase() !== "supporter";
  const openPlan = ["week", "month", "quarter"].reduce(
    (total, key) => total + arr(data.plan?.[key]).filter((item) => !item.done).length,
    0,
  );
  return (
    <>
      <div className="d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3">
        <div>
          <h2 className="mb-0 fs-3">Hi, {me.name || me.email}</h2>
          <div className="text-secondary small">Your work on assigned profiles</div>
        </div>
        <div className="stage-dash-period d-flex flex-wrap gap-1">
          {(Object.keys(periodMeta) as PresetPeriod[]).map((key) => (
            <button
              type="button"
              key={key}
              className={`btn btn-sm ${period === key ? "active" : "btn-ghost-secondary"}`}
              onClick={() => setPeriod(key)}
            >
              {key === "trend12" ? "12 weeks" : periodMeta[key].label}
            </button>
          ))}
          <button
            type="button"
            className={`btn btn-sm ${period === "custom" ? "active" : "btn-ghost-secondary"}`}
            onClick={() => setPeriod("custom")}
          >
            Custom
          </button>
        </div>
      </div>
      {period === "custom" && (
        <DateRangeForm
          draft={customDraft}
          earliest={earliest}
          latest={today}
          onChange={setCustomDraft}
          onApply={() => setCustomRange(customDraft)}
        />
      )}
      <div className="stage-kpi-grid mb-3">
        <Metric
          label="Applications"
          value={applicationsInRange}
          previous={data.previous ? previous.applications : undefined}
          icon="ti-briefcase"
          hint={meta.label}
          to="/performance"
        />
        <Metric
          label="Interviews"
          value={interviewsInRange}
          previous={data.previous ? previous.interviews : undefined}
          icon="ti-calendar-event"
          tone="success"
          hint={meta.label}
          to="/interviews"
        />
        <Metric
          label="Interview rate"
          value={interviewRate}
          displayValue={`${formatPercent(interviewRate)}%`}
          icon="ti-chart-funnel"
          tone="cyan"
          footer={
            data.previous ? (
              <>
                <Delta
                  current={interviewRate}
                  previous={previousInterviewRate}
                  decimals={1}
                  suffix=" pp"
                />{" "}
                <span className="text-secondary">vs {meta.previous.toLowerCase()}</span>
              </>
            ) : (
              <span className="text-secondary">Comparison unavailable</span>
            )
          }
          to="/performance"
        />
        <Metric
          label="Active contributors"
          value={activeMembers + activeSupporters}
          icon="ti-users-group"
          tone="warning"
          footer={
            <span className="text-secondary">
              {activeMembers} members · {activeSupporters} supporters
            </span>
          }
          to="/performance"
        />
      </div>
      <MemberSecondaryMetrics
        upcoming={data.interviews.length}
        openPlan={data.plan ? openPlan : null}
        profiles={arr(data.current?.profiles).length}
        colleagues={colleagues.length}
      />
      <div className="row row-cards g-2 g-md-3 mb-3">
        <div className={canUseWorkPlan ? "col-lg-7" : "col-12"}>
          <Panel
            title="Upcoming interviews"
            subtitle={`${data.interviews.length} scheduled`}
            action="/interviews"
          >
            <Interviews rows={data.interviews} />
          </Panel>
        </div>
        {canUseWorkPlan && (
          <div className="col-lg-5">
            <Plan plan={data.plan} />
          </div>
        )}
      </div>
      <div className="row row-cards g-2 g-md-3 mb-3">
        <div className="col-lg-7">
          <Panel title="Activity trend" subtitle={trendSubtitle(data.trend)}>
            <Trend data={data.trend} />
          </Panel>
        </div>
        <div className="col-lg-5">
          <Panel
            title="Your colleagues"
            subtitle="People sharing your profiles"
            action="/colleagues"
          >
            <Colleagues rows={colleagues} />
          </Panel>
        </div>
      </div>
      <div className="row row-cards g-2 g-md-3 mt-1">
        <div className="col-lg-7">
          <Panel
            title="Top members"
            subtitle={`Ranked by interviews, then applications · ${meta.label.toLowerCase()}`}
            action="/performance"
          >
            <Ranking rows={topMembers} showAll highlightId={String(me.id)} />
          </Panel>
        </div>
        <div className="col-lg-5">
          <Panel
            title="Top supporters"
            subtitle={`Ranked by applications · ${meta.label.toLowerCase()}`}
            action="/performance"
          >
            <Ranking
              rows={visibleSupporters}
              showAll
              highlightIds={colleagueSupporterIds}
              showInterviews={false}
              showTimezone
            />
          </Panel>
        </div>
      </div>
    </>
  );
}

function Metric({
  label,
  value,
  displayValue,
  previous,
  icon,
  tone = "primary",
  hint,
  footer,
  to,
}: {
  label: string;
  value: unknown;
  displayValue?: string;
  previous?: unknown;
  icon: string;
  tone?: string;
  hint?: string;
  footer?: ReactNode;
  to?: string;
}) {
  const content = (
    <div className="card stage-kpi-card h-100">
      <div className="card-body d-flex flex-column">
        <div className="stage-kpi-card-head">
          <div className="stage-kpi-label">{label}</div>
          <span className={`stage-kpi-icon bg-${tone}-lt text-${tone}`} aria-hidden="true">
            <i className={`ti ${icon}`} />
          </span>
        </div>
        <div className="stage-metric-value">{displayValue ?? num(value).toLocaleString()}</div>
        <div className="stage-kpi-meta mt-auto">
          {footer ?? (
            <>
              {previous !== undefined && <Delta current={value} previous={previous} />}{" "}
              {hint && <span className="text-secondary">{hint}</span>}
            </>
          )}
        </div>
      </div>
    </div>
  );
  return to ? (
    <Link className="stage-kpi-link" to={to} aria-label={`View ${label.toLowerCase()}`}>
      {content}
    </Link>
  ) : (
    content
  );
}
function Delta({
  current,
  previous,
  decimals = 0,
  suffix = "",
}: {
  current: unknown;
  previous: unknown;
  decimals?: number;
  suffix?: string;
}) {
  const delta = num(current) - num(previous);
  const tone = delta > 0 ? "success" : delta < 0 ? "danger" : "secondary";
  return (
    <span className={`badge bg-${tone}-lt text-${tone} stage-metric-delta`}>
      <i className={`ti ti-${delta > 0 ? "arrow-up" : delta < 0 ? "arrow-down" : "minus"}`} />
      {Math.abs(delta).toFixed(decimals)}
      {suffix}
    </span>
  );
}

function SecondaryMetrics({
  profileCoverage,
  mappedProfiles,
  totalProfiles,
  usersOnline,
  usersTotal,
  usersInactive,
}: {
  profileCoverage: number;
  mappedProfiles: number;
  totalProfiles: number;
  usersOnline: number;
  usersTotal: number;
  usersInactive: number;
}) {
  const items = [
    {
      label: "Profile coverage",
      value: `${formatPercent(profileCoverage)}%`,
      detail: `${mappedProfiles} of ${totalProfiles} mapped`,
      icon: "ti-map-pin-check",
      to: "/profiles",
    },
    {
      label: "Users online",
      value: usersOnline.toLocaleString(),
      detail: `${usersTotal.toLocaleString()} total users`,
      icon: "ti-wifi",
      to: "/users",
    },
    {
      label: "Inactive users",
      value: usersInactive.toLocaleString(),
      detail: usersInactive ? "May need attention" : "All users active",
      icon: "ti-user-pause",
      to: "/users",
    },
  ];
  return (
    <div className="stage-secondary-metrics mb-3" aria-label="Workspace health metrics">
      {items.map((item) => (
        <Link className="stage-secondary-metric" to={item.to} key={item.label}>
          <span className="stage-secondary-metric-icon" aria-hidden="true">
            <i className={`ti ${item.icon}`} />
          </span>
          <span className="stage-secondary-metric-copy">
            <span className="stage-secondary-metric-label">{item.label}</span>
            <span className="stage-secondary-metric-detail">{item.detail}</span>
          </span>
          <strong>{item.value}</strong>
        </Link>
      ))}
    </div>
  );
}

function MemberSecondaryMetrics({
  upcoming,
  openPlan,
  profiles,
  colleagues,
}: {
  upcoming: number;
  openPlan: number | null;
  profiles: number;
  colleagues: number;
}) {
  const items = [
    {
      label: "Upcoming interviews",
      value: upcoming.toLocaleString(),
      detail: "Next 45 days",
      icon: "ti-calendar-due",
      to: "/interviews",
    },
    openPlan !== null
      ? {
          label: "Open plan items",
          value: openPlan.toLocaleString(),
          detail: openPlan ? "Still to complete" : "Plan is clear",
          icon: "ti-checkbox",
          to: "/workplan",
        }
      : {
          label: "Assigned profiles",
          value: profiles.toLocaleString(),
          detail: "Profiles in your scope",
          icon: "ti-id",
          to: "/performance",
        },
    openPlan !== null
      ? {
          label: "Assigned profiles",
          value: profiles.toLocaleString(),
          detail: "Profiles in your scope",
          icon: "ti-id",
          to: "/performance",
        }
      : {
          label: "Colleagues",
          value: colleagues.toLocaleString(),
          detail: "Sharing your profiles",
          icon: "ti-users",
          to: "/colleagues",
        },
  ];
  return (
    <div className="stage-secondary-metrics mb-3" aria-label="Your workspace metrics">
      {items.map((item, index) => (
        <Link className="stage-secondary-metric" to={item.to} key={`${item.label}-${index}`}>
          <span className="stage-secondary-metric-icon" aria-hidden="true">
            <i className={`ti ${item.icon}`} />
          </span>
          <span className="stage-secondary-metric-copy">
            <span className="stage-secondary-metric-label">{item.label}</span>
            <span className="stage-secondary-metric-detail">{item.detail}</span>
          </span>
          <strong>{item.value}</strong>
        </Link>
      ))}
    </div>
  );
}

function DateRangeForm({
  draft,
  earliest,
  latest,
  onChange,
  onApply,
}: {
  draft: DateRange;
  earliest: string;
  latest: string;
  onChange: (range: DateRange) => void;
  onApply: () => void;
}) {
  const valid = validDateRange(draft, earliest, latest);
  return (
    <form
      className="stage-custom-range mb-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (valid) onApply();
      }}
    >
      <label>
        <span>From</span>
        <input
          className="form-control form-control-sm"
          type="date"
          min={earliest}
          max={draft.end || latest}
          value={draft.start}
          onChange={(event) => onChange({ ...draft, start: event.target.value })}
        />
      </label>
      <label>
        <span>To</span>
        <input
          className="form-control form-control-sm"
          type="date"
          min={draft.start || earliest}
          max={latest}
          value={draft.end}
          onChange={(event) => onChange({ ...draft, end: event.target.value })}
        />
      </label>
      <button className="btn btn-sm btn-primary" type="submit" disabled={!valid}>
        Apply range
      </button>
      <span className="text-secondary small">Up to 90 days</span>
    </form>
  );
}
function Panel({
  title,
  subtitle,
  action,
  children,
}: {
  title: string;
  subtitle?: string;
  action?: string;
  children: ReactNode;
}) {
  return (
    <div className="card h-100">
      <div className="card-header stage-panel-header">
        <div>
          <h3 className="card-title mb-0">{title}</h3>
          {subtitle && <div className="text-secondary small">{subtitle}</div>}
        </div>
        {action && (
          <Link className="btn btn-sm btn-ghost-secondary btn-icon" to={action}>
            <i className="ti ti-arrow-up-right" />
          </Link>
        )}
      </div>
      <div className="card-body">{children}</div>
    </div>
  );
}
function Trend({ data }: { data: Row | null | undefined }) {
  const apps = arr(data?.series?.applications).map(num),
    ivs = arr(data?.series?.interviews).map(num),
    labels = arr(data?.buckets || data?.labels),
    granularity = String(data?.granularity || "day"),
    count = Math.max(apps.length, ivs.length);
  if (!count) return <p className="text-secondary small mb-0">No activity yet.</p>;
  const chartData = Array.from({ length: count }, (_, index) => ({
    label: shortBucket(labels[index], index, granularity),
    fullLabel: fullBucket(labels[index], index),
    Applications: apps[index] || 0,
    Interviews: ivs[index] || 0,
  }));
  const totalApps = apps.reduce((sum, value) => sum + value, 0);
  const totalInterviews = ivs.reduce((sum, value) => sum + value, 0);
  return (
    <div className="stage-dash-trend" aria-label="Applications and interviews over time">
      <div className="stage-trend-section">
        <div className="stage-trend-heading">
          <span>Applications</span>
          <strong>{totalApps.toLocaleString()}</strong>
        </div>
        <div className="stage-trend-apps" role="img" aria-label="Applications by time period">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={chartData}
              syncId="dashboard-activity"
              margin={{ top: 8, right: 8, left: -14, bottom: 0 }}
            >
              <CartesianGrid
                stroke="var(--tblr-border-color)"
                strokeDasharray="3 3"
                vertical={false}
              />
              <XAxis dataKey="label" hide />
              <YAxis
                allowDecimals={false}
                domain={[0, (maximum: number) => Math.max(1, maximum)]}
                tick={{ fill: "var(--tblr-secondary)", fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                width={34}
              />
              <Tooltip content={<TrendTooltip />} cursor={{ fill: "var(--stage-hover-bg)" }} />
              <Bar dataKey="Applications" fill="var(--stage-brand)" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="stage-trend-section stage-trend-section-interviews">
        <div className="stage-trend-heading">
          <span>Interviews</span>
          <strong>{totalInterviews.toLocaleString()}</strong>
        </div>
        <div className="stage-trend-interviews" role="img" aria-label="Interviews by time period">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={chartData}
              syncId="dashboard-activity"
              margin={{ top: 4, right: 8, left: -14, bottom: 0 }}
            >
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
                domain={[0, (maximum: number) => Math.max(1, maximum)]}
                tick={{ fill: "var(--tblr-secondary)", fontSize: 11 }}
                tickLine={false}
                axisLine={false}
                width={34}
              />
              <Tooltip content={<TrendTooltip />} cursor={{ fill: "var(--stage-hover-bg)" }} />
              <Bar
                dataKey="Interviews"
                fill="var(--stage-cyan)"
                minPointSize={3}
                maxBarSize={18}
                radius={[3, 3, 0, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

function TrendTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload?: Row }>;
}) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;
  const applications = num(point.Applications);
  const interviews = num(point.Interviews);
  const rate = applications ? (interviews / applications) * 100 : 0;
  return (
    <div className="stage-trend-tooltip">
      <strong>{String(point.fullLabel || point.label)}</strong>
      <span>Applications: {applications.toLocaleString()}</span>
      <span>Interviews: {interviews.toLocaleString()}</span>
      <span>Interview rate: {formatPercent(rate)}%</span>
    </div>
  );
}
function Ranking({
  rows,
  showAll = false,
  highlightId,
  highlightIds,
  showInterviews = true,
  showTimezone = false,
}: {
  rows: Row[];
  showAll?: boolean;
  highlightId?: string;
  highlightIds?: Set<string>;
  showInterviews?: boolean;
  showTimezone?: boolean;
}) {
  if (!rows.length) return <p className="text-secondary small mb-0">No activity in this period.</p>;
  return (
    <div className="table-responsive">
      <table className="table table-vcenter table-sm mb-0 stage-rank-table">
        <thead>
          <tr>
            <th className="stage-rank-col">#</th>
            <th>Person</th>
            {showTimezone && <th className="stage-rank-tz-col" aria-label="Timezone" />}
            <th className="text-end">Apps</th>
            {showInterviews && <th className="text-end">Interviews</th>}
          </tr>
        </thead>
        <tbody>
          {(showAll ? rows : rows.slice(0, 8)).map((row, index) => {
            const id = String(row.user_id || row.id || "");
            const highlighted = (highlightId && id === highlightId) || highlightIds?.has(id);
            return (
              <tr key={id || index} className={highlighted ? "stage-rank-highlight" : undefined}>
                <td className="stage-rank-col">
                  <RankBadge rank={index + 1} />
                </td>
                <td>
                  <UserIdentity person={{ ...row, id: row.user_id || row.id }} />
                </td>
                {showTimezone && (
                  <td className="stage-rank-tz-col">
                    <TimezoneBadge value={row.timezone} />
                  </td>
                )}
                <td className="text-end">{num(row.applications_in_range)}</td>
                {showInterviews && <td className="text-end">{num(row.interviews_in_range)}</td>}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
function RankBadge({ rank }: { rank: number }) {
  const medal = rank === 1 ? "gold" : rank === 2 ? "silver" : rank === 3 ? "bronze" : null;
  if (!medal) return <span className="stage-rank-number">{rank}</span>;
  return (
    <span className={`stage-rank-medal stage-rank-medal-${medal}`} title={`Rank #${rank}`}>
      <i className="ti ti-medal" aria-hidden="true" />
    </span>
  );
}
function Geo({ geo }: { geo: Row | null | undefined }) {
  const rows = arr(geo?.states || geo?.items);
  return rows.length ? (
    <div className="stage-geo">
      <UsProfilesMap rows={rows} />
      <div className="stage-geo-footer">
        <div className="stage-geo-legend" aria-hidden="true">
          <span>Fewer</span>
          <span className="stage-geo-legend-scale" />
          <span>More</span>
        </div>
        {num(geo?.unmapped_count) > 0 && (
          <Link className="text-decoration-none" to="/profiles">
            <span className="badge bg-warning-lt text-warning">
              {num(geo?.unmapped_count)} unmapped
            </span>
          </Link>
        )}
      </div>
    </div>
  ) : (
    <div className="stage-us-map stage-geo-empty d-flex align-items-center justify-content-center text-secondary">
      <div className="text-center">
        <i className="ti ti-map-pin-off d-block mb-2" aria-hidden="true" />
        No mapped profile data.
      </div>
    </div>
  );
}

function UsProfilesMap({ rows }: { rows: Row[] }) {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!host.current) return;
    const container = host.current;
    // jsVectorMap's destroy method does not consistently remove its generated
    // SVG during React effect remounts, so always begin with an empty host.
    container.replaceChildren();
    const values: Record<string, number> = {};
    const profiles: Record<string, string[]> = {};
    rows.forEach((row) => {
      const region = String(row.region || `US-${row.code}`);
      values[region] = num(row.count || row.profiles);
      profiles[region] = Array.isArray(row.profiles) ? row.profiles.map(String) : [];
    });
    const styles = getComputedStyle(document.documentElement);
    const surface = styles.getPropertyValue("--stage-hover-bg-strong").trim() || "#e9ecef";
    const border = styles.getPropertyValue("--tblr-border-color").trim() || "#d0d5dd";
    const brand = styles.getPropertyValue("--stage-brand").trim() || "#1261d7";
    const map = new jsVectorMap({
      selector: container,
      map: "us_aea_en",
      backgroundColor: "transparent",
      zoomOnScroll: false,
      zoomButtons: false,
      regionStyle: {
        initial: { fill: surface, stroke: border, strokeWidth: 0.7, fillOpacity: 1 },
        hover: { fillOpacity: 0.82, cursor: "pointer" },
      },
      visualizeData: { scale: [surface, brand], values },
      onRegionTooltipShow: (
        _event: unknown,
        tooltip: { text: (value?: string) => string },
        code: string,
      ) => {
        const count = values[code] || 0;
        const names = profiles[code] || [];
        const state = tooltip.text();
        const sample = names.length
          ? ` · ${names.slice(0, 3).join(", ")}${names.length > 3 ? ` +${names.length - 3}` : ""}`
          : "";
        tooltip.text(`${state}: ${count} profile${count === 1 ? "" : "s"}${sample}`);
      },
    });
    return () => {
      map.destroy();
      container.replaceChildren();
    };
  }, [rows]);

  return (
    <div
      ref={host}
      className="stage-us-map stage-us-map-canvas"
      role="img"
      aria-label="United States map shaded by profile count"
    />
  );
}
function Interviews({ rows }: { rows: Row[] }) {
  return rows.length ? (
    <ul className="list-unstyled mb-0 stage-dash-iv-list">
      {rows.slice(0, 5).map((row, index) => (
        <li className="stage-dash-iv-item" key={row.id || index}>
          <Link className="stage-dash-iv-link" to="/interviews">
            <InterviewDateBadge row={row} />
            <div className="stage-dash-iv-copy">
              <div className="stage-dash-iv-title">
                {String(row.title || row.position_title || "Interview")}
              </div>
              <div className="stage-dash-iv-company">
                {String(row.company_name || row.profile_name || "Scheduled interview")}
              </div>
              <div className="stage-dash-iv-meta">
                <span>
                  <i className="ti ti-clock" aria-hidden="true" /> {formatUpcomingTime(row)}
                </span>
                {(row.interview_type || row.profile_name) && (
                  <span>{String(row.interview_type || row.profile_name)}</span>
                )}
              </div>
            </div>
            <i className="ti ti-chevron-right stage-dash-iv-chevron" aria-hidden="true" />
          </Link>
        </li>
      ))}
    </ul>
  ) : (
    <div className="stage-dash-iv-empty">
      <i className="ti ti-calendar-check" aria-hidden="true" />
      <div>
        <strong>No upcoming interviews</strong>
        <span>Newly scheduled interviews will appear here.</span>
      </div>
    </div>
  );
}

function InterviewDateBadge({ row }: { row: Row }) {
  const date = interviewDate(row.start_time_utc, row.timezone);
  if (!date) return <span className="stage-dash-iv-date">—</span>;
  return (
    <time className="stage-dash-iv-date" dateTime={date.toISOString()}>
      <span>{date.toLocaleDateString(undefined, { month: "short" })}</span>
      <strong>{date.toLocaleDateString(undefined, { day: "numeric" })}</strong>
    </time>
  );
}

function formatUpcomingTime(row: Row) {
  const start = interviewDate(row.start_time_utc, row.timezone);
  if (!start) return "Time unavailable";
  const end = interviewDate(row.end_time_utc, row.timezone);
  const formatter = new Intl.DateTimeFormat(undefined, { timeStyle: "short" });
  return end && end > start
    ? `${formatter.format(start)}–${formatter.format(end)}`
    : formatter.format(start);
}
function Plan({ plan }: { plan: Row | null }) {
  const unavailable = !plan;
  const horizons = ["week", "month", "quarter"].map((key) => {
    const rows = arr(plan?.[key]);
    const done = rows.filter((row) => row.done).length;
    return {
      key,
      label: key === "week" ? "This week" : key === "month" ? "This month" : "This quarter",
      total: rows.length,
      done,
      open: rows.length - done,
      percent: rows.length ? Math.round((done / rows.length) * 100) : 0,
    };
  });
  const total = horizons.reduce((sum, item) => sum + item.total, 0);
  const week = horizons[0];
  const supportingHorizons = horizons.slice(1);
  return (
    <div className="card h-100 stage-dash-plan-card">
      <div className="card-header stage-panel-header">
        <div>
          <h3 className="card-title mb-0">Weekly plan</h3>
          <div className="text-secondary small">Your priorities for this Saturday–Friday cycle</div>
        </div>
        <Link className="btn btn-sm btn-ghost-secondary btn-icon" to="/workplan">
          <i className="ti ti-arrow-up-right" />
        </Link>
      </div>
      <div className="card-body stage-dash-plan-body">
        {unavailable ? (
          <div className="stage-dash-plan-empty">
            <i className="ti ti-cloud-off" aria-hidden="true" />
            <strong>Work plan unavailable</strong>
            <span>The summary could not be loaded. Open Work Plan to try again.</span>
            <Link className="btn btn-sm btn-outline-primary mt-2" to="/workplan">
              Open work plan
            </Link>
          </div>
        ) : total ? (
          <>
            <div className={`stage-dash-plan-overall ${week.total ? "" : "is-empty"}`}>
              <div>
                <span>Weekly completion</span>
                <strong>{week.total ? `${week.percent}%` : "—"}</strong>
              </div>
              <span>
                {week.total
                  ? `${week.done} of ${week.total} weekly items completed`
                  : "No weekly items have been added"}
              </span>
              <div
                className="stage-dash-plan-overall-bar"
                role="progressbar"
                aria-label="Weekly work plan completion"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={week.percent}
              >
                <span style={{ width: `${week.percent}%` }} />
              </div>
            </div>
            <div className="stage-dash-plan-support-label">Longer-term outlook</div>
            <div className="stage-dash-plan-horizons">
              {supportingHorizons.map((item) => (
                <section
                  className={`stage-dash-plan-horizon stage-dash-plan-horizon-${item.key}`}
                  key={item.key}
                >
                  <div className="stage-dash-plan-horizon-head">
                    <div>
                      <strong>{item.label}</strong>
                      <span>
                        {item.done} of {item.total} completed
                      </span>
                    </div>
                    <strong>{item.percent}%</strong>
                  </div>
                  <div
                    className="stage-dash-plan-bar"
                    role="progressbar"
                    aria-label={`${item.label} completion`}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={item.percent}
                  >
                    <span style={{ width: `${item.percent}%` }} />
                  </div>
                </section>
              ))}
            </div>
            <div
              className={`stage-dash-plan-attention ${week.open ? "is-open" : week.total ? "is-clear" : "is-empty"}`}
            >
              <i className={`ti ti-${week.open ? "clock-exclamation" : "circle-check"}`} />
              {week.open
                ? `${week.open} item${week.open === 1 ? "" : "s"} remaining this week`
                : week.total
                  ? "This week's plan is complete"
                  : "Add weekly priorities to get started"}
            </div>
          </>
        ) : (
          <div className="stage-dash-plan-empty">
            <i className="ti ti-clipboard-list" aria-hidden="true" />
            <strong>No plan items yet</strong>
            <span>Create a plan to track weekly, monthly, and quarterly goals.</span>
            <Link className="btn btn-sm btn-primary mt-2" to="/workplan">
              Create work plan
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
function Colleagues({ rows }: { rows: Row[] }) {
  return rows.length ? (
    <div className="stage-dash-colleague-list">
      {rows.map((row, index) => {
        const shared = arr(row.shared_profiles);
        const online = Boolean(row.online);
        return (
          <article className="stage-dash-colleague-card" key={row.user_id || index}>
            <div className="stage-dash-colleague-head">
              <UserIdentity
                person={{ ...row, id: row.user_id || row.id }}
                secondary={String(row.role || "Colleague")}
              />
              <span
                className={`stage-dash-colleague-presence ${online ? "is-online" : "is-offline"}`}
                title={online ? "Currently online" : exactLastSeen(row.last_seen)}
              >
                <span aria-hidden="true" />
                {online ? "Online" : relativeLastSeen(row.last_seen)}
              </span>
            </div>
            <div className="stage-dash-colleague-profiles">
              <i className="ti ti-id" aria-hidden="true" />
              {shared.length
                ? `${shared
                    .slice(0, 2)
                    .map((profile) => String(profile.name || profile.key))
                    .join(", ")}${shared.length > 2 ? ` +${shared.length - 2}` : ""}`
                : "No shared profile details"}
            </div>
            <div className="stage-dash-colleague-stats">
              <span>
                <strong>{num(row.totals?.applications).toLocaleString()}</strong> applications
              </span>
              <span>
                <strong>{num(row.totals?.interviews).toLocaleString()}</strong> interviews
              </span>
              <span className="stage-dash-colleague-week">
                <strong>{num(row.progress?.applications_this_week).toLocaleString()}</strong> this
                week
              </span>
            </div>
          </article>
        );
      })}
    </div>
  ) : (
    <div className="stage-dash-colleague-empty">
      <i className="ti ti-users-minus" aria-hidden="true" />
      <span>No colleagues on shared profiles.</span>
    </div>
  );
}

function relativeLastSeen(value: unknown) {
  if (!value) return "Never seen";
  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) return "Offline";
  const minutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60_000));
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return days < 7
    ? `${days}d ago`
    : date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function exactLastSeen(value: unknown) {
  if (!value) return "No recorded activity";
  const date = new Date(String(value));
  return Number.isNaN(date.getTime())
    ? "Last seen unavailable"
    : `Last seen ${date.toLocaleString()}`;
}
function workPlanWeekStart() {
  const date = new Date();
  date.setDate(date.getDate() - ((date.getDay() + 1) % 7));
  return localDate(date);
}
function upcomingInterviews(rows: Row[], now: Date, cutoff: Date) {
  return rows
    .filter((row) => {
      const start = interviewDate(row.start_time_utc, row.timezone);
      const status = String(row.status || "").toLowerCase();
      return Boolean(
        start &&
        start >= now &&
        start <= cutoff &&
        status !== "cancelled" &&
        status !== "completed",
      );
    })
    .sort((a, b) => {
      const left = interviewDate(a.start_time_utc, a.timezone)?.getTime() || 0;
      const right = interviewDate(b.start_time_utc, b.timezone)?.getTime() || 0;
      return left - right;
    });
}
function num(value: unknown) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}
function formatPercent(value: number) {
  return value.toLocaleString(undefined, { maximumFractionDigits: 1 });
}
function trendSubtitle(data: Row | null | undefined) {
  const count = arr(data?.buckets).length;
  const granularity = String(data?.granularity || "period");
  return count ? `Last ${count} ${granularity}${count === 1 ? "" : "s"}` : "Activity over time";
}
function shortBucket(value: unknown, index: number, granularity = "day") {
  const bucket = value && typeof value === "object" ? (value as Row) : null;
  if (granularity === "month" && bucket?.label) return String(bucket.label);
  const text = String(bucket?.start || value || "");
  const date = new Date(text);
  if (!text || Number.isNaN(date.getTime())) return String(index + 1);
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}
function fullBucket(value: unknown, index: number) {
  const bucket = value && typeof value === "object" ? (value as Row) : null;
  return String(bucket?.label || shortBucket(value, index));
}
