export const reportingPeriodMeta = {
  today: {
    label: "Today",
    previous: "Yesterday",
    current: { granularity: "day", periods: 1, period_offset: 0 },
    prior: { granularity: "day", periods: 1, period_offset: 1 },
    trend: { granularity: "day", periods: 1, period_offset: 0 },
  },
  yesterday: {
    label: "Yesterday",
    previous: "Previous day",
    current: { granularity: "day", periods: 1, period_offset: 1 },
    prior: { granularity: "day", periods: 1, period_offset: 2 },
    trend: { granularity: "day", periods: 1, period_offset: 1 },
  },
  week: {
    label: "This week",
    previous: "Last week",
    current: { granularity: "week", periods: 1, period_offset: 0 },
    prior: { granularity: "week", periods: 1, period_offset: 1 },
    trend: { granularity: "day", periods: 14 },
  },
  lastWeek: {
    label: "Last week",
    previous: "Previous week",
    current: { granularity: "week", periods: 1, period_offset: 1 },
    prior: { granularity: "week", periods: 1, period_offset: 2 },
    trend: { granularity: "day", periods: 7, period_offset: daysSinceLastWeek() },
  },
  month: {
    label: "This month",
    previous: "Last month",
    current: { granularity: "month", periods: 1, period_offset: 0 },
    prior: { granularity: "month", periods: 1, period_offset: 1 },
    trend: { granularity: "week", periods: 12 },
  },
  trend12: {
    label: "Last 12 weeks",
    shortLabel: "12 weeks",
    previous: "Prior 12 weeks",
    current: { granularity: "week", periods: 12, period_offset: 0 },
    prior: { granularity: "week", periods: 12, period_offset: 12 },
    trend: { granularity: "week", periods: 12 },
  },
} as const;

export type PresetPeriod = keyof typeof reportingPeriodMeta;
export type ReportingPeriod = PresetPeriod | "custom";
export type DateRange = { start: string; end: string };

export function localDate(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function weekStart() {
  const date = new Date();
  date.setDate(date.getDate() - ((date.getDay() + 1) % 7));
  return localDate(date);
}

export function shiftDate(value: string, days: number) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export function daysBetween(start: string, end: string) {
  const startTime = new Date(`${start}T00:00:00Z`).getTime();
  const endTime = new Date(`${end}T00:00:00Z`).getTime();
  return Math.round((endTime - startTime) / 86_400_000);
}

function daysSinceLastWeek() {
  return ((new Date().getDay() + 1) % 7) + 1;
}

export function validDateRange(range: DateRange, earliest: string, latest: string) {
  if (!range.start || !range.end) return false;
  const duration = daysBetween(range.start, range.end);
  return range.start >= earliest && range.end <= latest && duration >= 0 && duration < 90;
}

export function reportingPeriod(period: ReportingPeriod, range: DateRange) {
  return period === "custom" ? customPeriodMeta(range) : reportingPeriodMeta[period];
}

/** Concrete [start, end] local dates for a preset - for a filter that sends
 *  plain date bounds straight to an endpoint, rather than the
 *  granularity/periods/offset triple a period-over-period comparison endpoint
 *  interprets server-side. */
export function presetDateRange(period: PresetPeriod): DateRange {
  const today = localDate();
  switch (period) {
    case "today":
      return { start: today, end: today };
    case "yesterday": {
      const day = shiftDate(today, -1);
      return { start: day, end: day };
    }
    case "week":
      return { start: weekStart(), end: today };
    case "lastWeek": {
      const end = shiftDate(weekStart(), -1);
      return { start: shiftDate(end, -6), end };
    }
    case "month":
      return { start: `${today.slice(0, 7)}-01`, end: today };
    case "trend12":
      return { start: shiftDate(today, -84), end: today };
  }
}

export function customPeriodMeta(range: DateRange) {
  const periods = daysBetween(range.start, range.end) + 1;
  const periodOffset = daysBetween(range.end, localDate());
  const label = `${formatRangeDate(range.start)}–${formatRangeDate(range.end)}`;
  return {
    label,
    previous: `Previous ${periods} day${periods === 1 ? "" : "s"}`,
    current: { granularity: "day", periods, period_offset: periodOffset },
    prior: { granularity: "day", periods, period_offset: periodOffset + periods },
    trend: { granularity: "day", periods, period_offset: periodOffset },
  };
}

function formatRangeDate(value: string) {
  return new Date(`${value}T00:00:00Z`).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}
