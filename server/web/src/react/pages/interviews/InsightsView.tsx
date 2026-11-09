import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, type Row } from "../../api";
import { arr, ErrorBox, Loading } from "../../ui";
import { StateMessage } from "../../components/primitives";
import { PersonSelect, userPerson } from "../../components/Avatar";
import {
  type DateRange,
  localDate,
  type PresetPeriod,
  presetDateRange,
  reportingPeriodMeta,
  type ReportingPeriod,
  shiftDate,
  validDateRange,
  weekStart,
} from "../../reportingPeriod";

// Status colors mirror the badge tones already used for interview status
// elsewhere in this app (statusBadge in InterviewsPage.tsx): azure for
// scheduled/upcoming, danger for cancelled. Validated as a fixed status
// scale (never reordered), not a reorderable categorical palette.
const STATUS_COLORS = {
  passed: "var(--stage-green, #2f9e44)",
  failed: "var(--stage-red, #e03131)",
  awaiting_results: "var(--stage-orange, #f59f00)",
  upcoming: "var(--stage-azure, #4299e1)",
  cancelled: "var(--stage-muted, #6b7280)",
} as const;
// Applied -> Offer, one hue light-to-dark (validated with --ordinal against
// both light and dark surfaces).
const FUNNEL_SHADES = ["#9cc3f2", "#6ba5ec", "#3c84e3", "#1261d7", "#0849a3"];
// Two-series categorical pair, validated (light + dark) with
// validate_palette.js: worst adjacent normal-vision ΔE 30.9.
const TREND_COLORS = { intro: "var(--stage-brand, #1261d7)", technical: "#d6336c" };
// Today/yesterday are too narrow to say anything about interview pass rates;
// "All" (no date bound at all) is the one Performance doesn't offer but this
// tab needs, since a member's interview history is often the whole point.
const INSIGHTS_PERIODS = (Object.keys(reportingPeriodMeta) as PresetPeriod[]).filter(
  (key) => key !== "today" && key !== "yesterday",
);
type InsightsPeriod = "all" | ReportingPeriod;
// Admins, managers, and supporters don't have interview histories of their
// own to look up here - only the members they support do.
const NON_MEMBER_ROLES = new Set(["admin", "manager", "supporter"]);

export function InsightsView({ isStaff, people }: { isStaff: boolean; people?: Row[] }) {
  const today = localDate();
  const earliest = shiftDate(today, -89);
  const [period, setPeriod] = useState<InsightsPeriod>("week");
  const [customDraft, setCustomDraft] = useState<DateRange>({ start: weekStart(), end: today });
  const [customRange, setCustomRange] = useState<DateRange>(customDraft);
  const [personId, setPersonId] = useState("");
  const [profileId, setProfileId] = useState("");
  const range: DateRange =
    period === "all" ? { start: "", end: "" } : period === "custom" ? customRange : presetDateRange(period);
  const members = (people || []).filter(
    (user) => !NON_MEMBER_ROLES.has(String(user.role || "").trim().toLowerCase()),
  );

  const profiles = useQuery({ queryKey: ["profiles"], queryFn: api.profiles, enabled: isStaff });
  const scope = isStaff ? "all" : "mine";
  const insights = useQuery({
    queryKey: ["interview-insights", scope, personId, profileId, range.start, range.end],
    queryFn: () =>
      api.interviewInsights({
        scope,
        assigned_user_id: isStaff ? personId || undefined : undefined,
        profile_id: isStaff ? profileId || undefined : undefined,
        applied_from: range.start || undefined,
        applied_to: range.end || undefined,
        trend_granularity: "month",
      }),
    placeholderData: keepPreviousData,
  });

  const hasFilters = Boolean(period !== "week" || personId || profileId);
  const clearFilters = () => {
    setPeriod("week");
    setPersonId("");
    setProfileId("");
  };

  const data = (insights.data || {}) as Row;
  const summary = (data.summary || {}) as Row;
  const passRate = (data.pass_rate || {}) as Row;
  const outcomesByType = arr(data.outcomes_by_type);
  const stageComparison = arr(data.stage_comparison);
  const funnel = arr(data.funnel);
  const trendPoints = arr(data.trend?.points);

  return (
    <div className="stage-insights">
      <div className="card stage-perf-filter-card mb-3">
        <div className="card-body">
          <div className="stage-insights-filter-row">
            <div className="stage-perf-window stage-dash-period" aria-label="Applied-date range">
              <button
                type="button"
                className={`btn btn-sm ${period === "all" ? "active" : "btn-ghost-secondary"}`}
                onClick={() => setPeriod("all")}
              >
                All
              </button>
              {INSIGHTS_PERIODS.map((key) => (
                <button
                  type="button"
                  className={`btn btn-sm ${period === key ? "active" : "btn-ghost-secondary"}`}
                  key={key}
                  onClick={() => setPeriod(key)}
                >
                  {periodButtonLabel(key)}
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
            {isStaff && (
              <>
                <div className="stage-insights-person-picker">
                  <PersonSelect
                    people={members.map(userPerson)}
                    value={personId}
                    onChange={setPersonId}
                    allLabel="All members"
                    ariaLabel="Person"
                    size="xs"
                  />
                </div>
                <select
                  className="form-select form-select-sm stage-insights-profile-select"
                  aria-label="Profile"
                  value={profileId}
                  onChange={(event) => setProfileId(event.target.value)}
                >
                  <option value="">All profiles</option>
                  {(profiles.data || []).map((item: Row) => (
                    <option key={String(item.id)} value={String(item.id)}>
                      {String(item.name || item.key || "Unnamed profile")}
                    </option>
                  ))}
                </select>
                {hasFilters && (
                  <button
                    type="button"
                    className="btn btn-sm btn-ghost-secondary stage-perf-clear"
                    onClick={clearFilters}
                  >
                    <i className="ti ti-filter-off me-1" /> Clear filters
                  </button>
                )}
              </>
            )}
          </div>
        </div>
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

      {insights.error && <ErrorBox error={insights.error} />}
      {insights.isLoading ? (
        <Loading />
      ) : (
        <>
          <SummaryCards summary={summary} />
          <div className="row row-cards g-2 g-md-3 mb-3">
            <PassRateCard title="Intro pass rate" data={passRate.intro} />
            <PassRateCard title="Technical pass rate" data={passRate.technical} />
            <PassRateCard title="Overall pass rate" data={passRate.overall} />
          </div>

          <div className="card mb-3">
            <div className="card-header stage-panel-header">
              <div>
                <h3 className="card-title mb-0">Interview outcomes by type</h3>
                <div className="text-secondary small">
                  How each kind of round is going - passed, not passed (including ghosted or
                  withdrawn), awaiting a result, still upcoming, or cancelled
                </div>
              </div>
            </div>
            <div className="card-body">
              {outcomesByType.length ? (
                <OutcomesChart rows={outcomesByType} />
              ) : (
                <StateMessage
                  icon="ti-calendar-off"
                  title="No interviews in this range"
                  description="Try widening the date range or clearing the other filters."
                />
              )}
            </div>
          </div>

          <div className="row row-cards g-2 g-md-3 mb-3">
            <div className="col-lg-6">
              <div className="card h-100">
                <div className="card-header stage-panel-header">
                  <div>
                    <h3 className="card-title mb-0">Pass rate by stage</h3>
                    <div className="text-secondary small">Judged rounds only</div>
                  </div>
                </div>
                <div className="card-body">
                  {stageComparison.some((row) => Number(row.decided) > 0) ? (
                    <StageComparisonChart rows={stageComparison} />
                  ) : (
                    <p className="text-secondary small mb-0">No rounds judged yet.</p>
                  )}
                </div>
              </div>
            </div>
            <div className="col-lg-6">
              <div className="card h-100">
                <div className="card-header stage-panel-header">
                  <div>
                    <h3 className="card-title mb-0">Application progression</h3>
                    <div className="text-secondary small">Share of applied reaching each stage</div>
                  </div>
                </div>
                <div className="card-body">
                  {funnel.length ? (
                    <FunnelChart rows={funnel} />
                  ) : (
                    <p className="text-secondary small mb-0">No applications in this range.</p>
                  )}
                </div>
              </div>
            </div>
          </div>

          <div className="card mb-3">
            <div className="card-header stage-panel-header">
              <div>
                <h3 className="card-title mb-0">Pass rate trend</h3>
                <div className="text-secondary small">Intro and technical rounds, by month</div>
              </div>
            </div>
            <div className="card-body">
              {trendPoints.length ? (
                <TrendChart points={trendPoints} />
              ) : (
                <p className="text-secondary small mb-0">Not enough judged rounds yet to trend.</p>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function SummaryCards({ summary }: { summary: Row }) {
  const tiles: Array<{ key: string; label: string; icon: string; tone: string }> = [
    { key: "total", label: "Total scheduled", icon: "ti-calendar-stats", tone: "primary" },
    { key: "completed", label: "Completed", icon: "ti-checks", tone: "azure" },
    { key: "passed", label: "Passed", icon: "ti-check", tone: "success" },
    { key: "failed", label: "Not passed", icon: "ti-x", tone: "danger" },
    { key: "awaiting_results", label: "Awaiting results", icon: "ti-clock-hour-3", tone: "orange" },
    { key: "upcoming", label: "Upcoming", icon: "ti-calendar-time", tone: "cyan" },
  ];
  return (
    <div className="row row-cards g-2 g-md-3 mb-3">
      {tiles.map((tile) => (
        <div className="col-6 col-lg" key={tile.key}>
          <div className="card h-100">
            <div className="card-body d-flex justify-content-between align-items-start">
              <div>
                <div className="text-secondary stage-insights-tile-label">{tile.label}</div>
                <div className="stage-metric-value">{num(summary[tile.key]).toLocaleString()}</div>
              </div>
              <span className={`avatar bg-${tile.tone}-lt text-${tile.tone}`}>
                <i className={`ti ${tile.icon}`} />
              </span>
            </div>
          </div>
        </div>
      ))}
      <div className="col-6 col-lg">
        <div className="card h-100 stage-insights-cancelled" title="Kept separate from failed - these never ran">
          <div className="card-body d-flex justify-content-between align-items-start">
            <div>
              <div className="text-secondary stage-insights-tile-label">Cancelled</div>
              <div className="stage-metric-value">{num(summary.cancelled).toLocaleString()}</div>
            </div>
            <span className="avatar bg-secondary-lt text-secondary">
              <i className="ti ti-calendar-off" />
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

function PassRateCard({ title, data }: { title: string; data?: Row }) {
  const passed = num(data?.passed);
  const decided = num(data?.decided);
  const rate = data?.rate;
  return (
    <div className="col-md-4">
      <div className="card h-100">
        <div className="card-body">
          <div className="text-secondary">{title}</div>
          <div className="stage-metric-value">
            {rate === null || rate === undefined ? "—" : `${rate}%`}
          </div>
          <div className="text-secondary small mt-1">
            {decided ? `${passed} of ${decided} passed · ${rate}%` : "No rounds judged yet"}
          </div>
        </div>
      </div>
    </div>
  );
}

function OutcomesChart({ rows }: { rows: Row[] }) {
  const chartData = rows.map((row) => ({
    label: String(row.label || row.type),
    passed: num(row.passed),
    failed: num(row.failed),
    awaiting_results: num(row.awaiting_results),
    upcoming: num(row.upcoming),
    cancelled: num(row.cancelled),
    passed_share_of_scheduled: row.passed_share_of_scheduled,
  }));
  const height = Math.max(120, chartData.length * 48);
  return (
    <div style={{ width: "100%", height }} role="img" aria-label="Interview outcomes by type">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={chartData}
          layout="vertical"
          margin={{ top: 4, right: 48, left: 8, bottom: 4 }}
        >
          <CartesianGrid stroke="var(--tblr-border-color)" strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" allowDecimals={false} hide />
          <YAxis
            type="category"
            dataKey="label"
            width={120}
            tick={{ fill: "var(--tblr-secondary)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip content={<OutcomesTooltip />} cursor={{ fill: "var(--stage-hover-bg)" }} />
          <Legend
            verticalAlign="top"
            height={28}
            wrapperStyle={{ fontSize: 12 }}
            formatter={legendLabel}
          />
          <Bar dataKey="passed" name="passed" stackId="a" fill={STATUS_COLORS.passed} />
          <Bar dataKey="failed" name="failed" stackId="a" fill={STATUS_COLORS.failed} />
          <Bar
            dataKey="awaiting_results"
            name="awaiting_results"
            stackId="a"
            fill={STATUS_COLORS.awaiting_results}
          />
          <Bar dataKey="upcoming" name="upcoming" stackId="a" fill={STATUS_COLORS.upcoming} />
          <Bar
            dataKey="cancelled"
            name="cancelled"
            stackId="a"
            fill={STATUS_COLORS.cancelled}
            radius={[0, 3, 3, 0]}
            label={renderShareLabel(chartData)}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// Recharts' Bar `label` render only reliably forwards position/value props
// (x, y, width, height, index) - not the original data row (`payload` is
// undefined there, unlike Tooltip/Legend) - so the row is looked up by index
// out of the same array the chart was given, via closure, rather than trusted
// off the render props.
function renderShareLabel(rows: Row[]) {
  return (props: Row) => {
    const { x, y, width, height, index } = props;
    const share = rows[index]?.passed_share_of_scheduled;
    if (share === null || share === undefined) return null;
    return (
      <text
        x={Number(x) + Number(width) + 8}
        y={Number(y) + Number(height) / 2}
        dy={4}
        fontSize={11}
        fill="var(--tblr-secondary)"
      >
        {`${share}% passed`}
      </text>
    );
  };
}

function OutcomesTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload?: Row }> }) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;
  return (
    <div className="stage-trend-tooltip">
      <strong>{String(point.label)}</strong>
      <span>Passed: {num(point.passed)}</span>
      <span>Not passed: {num(point.failed)}</span>
      <span>Awaiting results: {num(point.awaiting_results)}</span>
      <span>Upcoming: {num(point.upcoming)}</span>
      <span>Cancelled: {num(point.cancelled)}</span>
    </div>
  );
}

function legendLabel(value: string) {
  const labels: Record<string, string> = {
    passed: "Passed",
    failed: "Not passed",
    awaiting_results: "Awaiting results",
    upcoming: "Upcoming",
    cancelled: "Cancelled",
  };
  return labels[value] || value;
}

function StageComparisonChart({ rows }: { rows: Row[] }) {
  const chartData = rows.map((row) => ({
    label: String(row.label),
    rate: num(row.rate ?? 0),
    passed: num(row.passed),
    decided: num(row.decided),
    rateLabel: row.rate === null || row.rate === undefined ? "—" : `${row.rate}%`,
  }));
  return (
    <div style={{ width: "100%", height: 200 }} role="img" aria-label="Pass rate by stage">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={chartData} layout="vertical" margin={{ top: 4, right: 80, left: 8, bottom: 4 }}>
          <CartesianGrid stroke="var(--tblr-border-color)" strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" domain={[0, 100]} hide />
          <YAxis
            type="category"
            dataKey="label"
            width={80}
            tick={{ fill: "var(--tblr-secondary)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip content={<StageTooltip />} cursor={{ fill: "var(--stage-hover-bg)" }} />
          <Bar
            dataKey="rate"
            fill="var(--stage-brand, #1261d7)"
            radius={[0, 3, 3, 0]}
            minPointSize={2}
            label={renderStageRateLabel(chartData)}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function renderStageRateLabel(rows: Row[]) {
  return (props: Row) => {
    const { x, y, width, height, index } = props;
    const row = rows[index];
    if (!row) return null;
    return (
      <text
        x={Number(x) + Number(width) + 8}
        y={Number(y) + Number(height) / 2}
        dy={4}
        fontSize={11}
        fill="var(--tblr-secondary)"
      >
        {`${row.rateLabel} · ${num(row.passed)}/${num(row.decided)}`}
      </text>
    );
  };
}

function StageTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload?: Row }> }) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;
  return (
    <div className="stage-trend-tooltip">
      <strong>{String(point.label)}</strong>
      <span>
        {num(point.passed)} of {num(point.decided)} passed · {String(point.rateLabel)}
      </span>
    </div>
  );
}

function FunnelChart({ rows }: { rows: Row[] }) {
  const applied = num(rows[0]?.count) || 1;
  const chartData = rows.map((row) => ({
    label: String(row.label),
    key: String(row.key),
    count: num(row.count),
    percentOfApplied: Math.round((num(row.count) / applied) * 1000) / 10,
  }));
  return (
    <div style={{ width: "100%", height: 220 }} role="img" aria-label="Application progression funnel">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={chartData} layout="vertical" margin={{ top: 4, right: 90, left: 8, bottom: 4 }}>
          <CartesianGrid stroke="var(--tblr-border-color)" strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" hide />
          <YAxis
            type="category"
            dataKey="label"
            width={80}
            tick={{ fill: "var(--tblr-secondary)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip content={<FunnelTooltip />} cursor={{ fill: "var(--stage-hover-bg)" }} />
          <Bar dataKey="count" radius={[0, 3, 3, 0]} label={renderFunnelLabel(chartData)}>
            {chartData.map((row, index) => (
              <Cell key={row.key} fill={FUNNEL_SHADES[index] || FUNNEL_SHADES[FUNNEL_SHADES.length - 1]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function renderFunnelLabel(rows: Row[]) {
  return (props: Row) => {
    const { x, y, width, height, index } = props;
    const row = rows[index];
    if (!row) return null;
    return (
      <text
        x={Number(x) + Number(width) + 8}
        y={Number(y) + Number(height) / 2}
        dy={4}
        fontSize={11}
        fill="var(--tblr-secondary)"
      >
        {`${num(row.count).toLocaleString()} · ${row.percentOfApplied}%`}
      </text>
    );
  };
}

function FunnelTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload?: Row }> }) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;
  return (
    <div className="stage-trend-tooltip">
      <strong>{String(point.label)}</strong>
      <span>
        {num(point.count).toLocaleString()} ({point.percentOfApplied}% of applied)
      </span>
    </div>
  );
}

function TrendChart({ points }: { points: Row[] }) {
  const chartData = points.map((point) => ({
    label: String(point.label),
    Intro: point.intro?.rate ?? null,
    Technical: point.technical?.rate ?? null,
    introDecided: num(point.intro?.decided),
    technicalDecided: num(point.technical?.decided),
  }));
  return (
    <div style={{ width: "100%", height: 240 }} role="img" aria-label="Pass rate trend by month">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={chartData} margin={{ top: 8, right: 12, left: -14, bottom: 0 }}>
          <CartesianGrid stroke="var(--tblr-border-color)" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fill: "var(--tblr-secondary)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            minTickGap={24}
          />
          <YAxis
            domain={[0, 100]}
            tick={{ fill: "var(--tblr-secondary)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={40}
          />
          <Tooltip content={<TrendTooltip />} cursor={{ stroke: "var(--tblr-border-color)" }} />
          <Legend verticalAlign="top" height={28} wrapperStyle={{ fontSize: 12 }} />
          <Line type="monotone" dataKey="Intro" stroke={TREND_COLORS.intro} strokeWidth={2} dot={false} connectNulls />
          <Line
            type="monotone"
            dataKey="Technical"
            stroke={TREND_COLORS.technical}
            strokeWidth={2}
            dot={false}
            connectNulls
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function TrendTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload?: Row }> }) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;
  return (
    <div className="stage-trend-tooltip">
      <strong>{String(point.label)}</strong>
      <span>
        Intro: {point.Intro === null || point.Intro === undefined ? "—" : `${point.Intro}%`} (
        {num(point.introDecided)} judged)
      </span>
      <span>
        Technical: {point.Technical === null || point.Technical === undefined ? "—" : `${point.Technical}%`} (
        {num(point.technicalDecided)} judged)
      </span>
    </div>
  );
}

function periodButtonLabel(key: PresetPeriod): string {
  const meta = reportingPeriodMeta[key] as { label: string; shortLabel?: string };
  return meta.shortLabel ?? meta.label;
}
function num(value: unknown) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}
