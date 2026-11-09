const timezoneFamilies: Record<string, { family: string; standardOffset: number }> = {
  EST: { family: "ET", standardOffset: -5 },
  EDT: { family: "ET", standardOffset: -5 },
  CST: { family: "CT", standardOffset: -6 },
  CDT: { family: "CT", standardOffset: -6 },
  MST: { family: "MT", standardOffset: -7 },
  MDT: { family: "MT", standardOffset: -7 },
  PST: { family: "PT", standardOffset: -8 },
  PDT: { family: "PT", standardOffset: -8 },
  AKST: { family: "AKT", standardOffset: -9 },
  AKDT: { family: "AKT", standardOffset: -9 },
  HST: { family: "HT", standardOffset: -10 },
  AST: { family: "AT", standardOffset: -4 },
  ADT: { family: "AT", standardOffset: -4 },
  GMT: { family: "GMT", standardOffset: 0 },
  BST: { family: "GMT", standardOffset: 0 },
  UTC: { family: "UTC", standardOffset: 0 },
};

export function interviewDate(value: unknown, timezone: unknown) {
  const raw = clean(value);
  const match = raw
    .replace(/Z$/i, "")
    .replace(/[+-]\d{2}:\d{2}$/, "")
    .match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/);
  if (!match) return null;
  const abbreviation = clean(timezone).split(/\s+/)[0].toUpperCase();
  const source = timezoneFamilies[abbreviation];
  if (!source) {
    const fallback = new Date(raw);
    return Number.isNaN(fallback.getTime()) ? null : fallback;
  }

  // Match the desktop client: standard/daylight abbreviations in the same
  // family describe the same wall clock. Cross-family conversion uses the
  // families' standard offsets, keeping ET <-> PT consistently three hours.
  const localStandardOffset = browserStandardOffset();
  const hourDelta = localStandardOffset - source.standardOffset;
  return new Date(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
    Number(match[4]) + hourDelta,
    Number(match[5]),
    Number(match[6] || 0),
  );
}

function browserStandardOffset() {
  const year = new Date().getFullYear();
  // getTimezoneOffset is positive west of UTC. The larger of January/July is
  // the standard-time offset for the zones supported by the desktop client.
  const minutesWest = Math.max(
    new Date(year, 0, 1).getTimezoneOffset(),
    new Date(year, 6, 1).getTimezoneOffset(),
  );
  return -minutesWest / 60;
}

export function formatInterviewRange(startValue: unknown, endValue: unknown, timezone: unknown) {
  const start = interviewDate(startValue, timezone);
  if (!start) return "—";
  const end = interviewDate(endValue, timezone);
  const date = new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(start);
  return !end
    ? date
    : `${date} – ${new Intl.DateTimeFormat(undefined, { timeStyle: "short" }).format(end)}`;
}

function clean(value: unknown) {
  return value === null || value === undefined ? "" : String(value).trim();
}
