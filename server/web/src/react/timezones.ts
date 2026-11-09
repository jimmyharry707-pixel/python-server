// Curated, not exhaustive — covers where supporters/members are commonly based.
// Used to bucket a supporter's Colleagues activity by her own local day/hour,
// and to show a flag on the dashboard ranking tables. Offsets are standard
// time (no DST adjustment), same convention as interviewTime.ts's
// timezoneFamilies — good enough for picking/labeling a zone.
// `country` is a lowercase ISO 3166-1 alpha-2 code for the `flag-icons`
// package's `fi fi-xx` classes: real SVG flags, so rendering doesn't depend
// on the OS having a color-emoji font with flag glyphs (plain emoji flags
// fall back to bare two-letter text on many Linux setups).
export type TimezoneOption = { value: string; country?: string; label: string; offset: string };

export const TIMEZONE_OPTIONS: TimezoneOption[] = [
  { value: "Pacific/Honolulu", country: "us", label: "United States — Hawaii", offset: "UTC-10:00" },
  { value: "America/Anchorage", country: "us", label: "United States — Alaska", offset: "UTC-09:00" },
  { value: "America/Los_Angeles", country: "us", label: "United States — Pacific", offset: "UTC-08:00" },
  { value: "America/Vancouver", country: "ca", label: "Canada — Pacific", offset: "UTC-08:00" },
  { value: "America/Denver", country: "us", label: "United States — Mountain", offset: "UTC-07:00" },
  { value: "America/Mexico_City", country: "mx", label: "Mexico — Mexico City", offset: "UTC-06:00" },
  { value: "America/Chicago", country: "us", label: "United States — Central", offset: "UTC-06:00" },
  { value: "America/New_York", country: "us", label: "United States — Eastern", offset: "UTC-05:00" },
  { value: "America/Toronto", country: "ca", label: "Canada — Eastern", offset: "UTC-05:00" },
  { value: "America/Bogota", country: "co", label: "Colombia — Bogota", offset: "UTC-05:00" },
  { value: "America/Lima", country: "pe", label: "Peru — Lima", offset: "UTC-05:00" },
  {
    value: "America/Argentina/Buenos_Aires",
    country: "ar",
    label: "Argentina — Buenos Aires",
    offset: "UTC-03:00",
  },
  { value: "America/Sao_Paulo", country: "br", label: "Brazil — Sao Paulo", offset: "UTC-03:00" },
  { value: "UTC", label: "UTC", offset: "UTC+00:00" },
  { value: "Europe/Lisbon", country: "pt", label: "Portugal — Lisbon", offset: "UTC+00:00" },
  { value: "Europe/Dublin", country: "ie", label: "Ireland — Dublin", offset: "UTC+00:00" },
  { value: "Europe/London", country: "gb", label: "United Kingdom — London", offset: "UTC+00:00" },
  { value: "Africa/Casablanca", country: "ma", label: "Morocco — Casablanca", offset: "UTC+01:00" },
  { value: "Africa/Lagos", country: "ng", label: "Nigeria — Lagos", offset: "UTC+01:00" },
  { value: "Europe/Berlin", country: "de", label: "Germany — Berlin", offset: "UTC+01:00" },
  { value: "Europe/Paris", country: "fr", label: "France — Paris", offset: "UTC+01:00" },
  { value: "Europe/Madrid", country: "es", label: "Spain — Madrid", offset: "UTC+01:00" },
  { value: "Europe/Rome", country: "it", label: "Italy — Rome", offset: "UTC+01:00" },
  { value: "Europe/Amsterdam", country: "nl", label: "Netherlands — Amsterdam", offset: "UTC+01:00" },
  { value: "Europe/Warsaw", country: "pl", label: "Poland — Warsaw", offset: "UTC+01:00" },
  { value: "Africa/Cairo", country: "eg", label: "Egypt — Cairo", offset: "UTC+02:00" },
  { value: "Africa/Johannesburg", country: "za", label: "South Africa — Johannesburg", offset: "UTC+02:00" },
  { value: "Europe/Athens", country: "gr", label: "Greece — Athens", offset: "UTC+02:00" },
  { value: "Europe/Bucharest", country: "ro", label: "Romania — Bucharest", offset: "UTC+02:00" },
  { value: "Europe/Kyiv", country: "ua", label: "Ukraine — Kyiv", offset: "UTC+02:00" },
  { value: "Asia/Jerusalem", country: "il", label: "Israel — Jerusalem", offset: "UTC+02:00" },
  { value: "Europe/Istanbul", country: "tr", label: "Turkey — Istanbul", offset: "UTC+03:00" },
  { value: "Europe/Moscow", country: "ru", label: "Russia — Moscow", offset: "UTC+03:00" },
  { value: "Asia/Riyadh", country: "sa", label: "Saudi Arabia — Riyadh", offset: "UTC+03:00" },
  { value: "Africa/Nairobi", country: "ke", label: "Kenya — Nairobi", offset: "UTC+03:00" },
  { value: "Asia/Dubai", country: "ae", label: "United Arab Emirates — Dubai", offset: "UTC+04:00" },
  { value: "Asia/Karachi", country: "pk", label: "Pakistan — Karachi", offset: "UTC+05:00" },
  { value: "Asia/Kolkata", country: "in", label: "India — Kolkata", offset: "UTC+05:30" },
  { value: "Asia/Colombo", country: "lk", label: "Sri Lanka — Colombo", offset: "UTC+05:30" },
  { value: "Asia/Kathmandu", country: "np", label: "Nepal — Kathmandu", offset: "UTC+05:45" },
  { value: "Asia/Dhaka", country: "bd", label: "Bangladesh — Dhaka", offset: "UTC+06:00" },
  { value: "Asia/Yangon", country: "mm", label: "Myanmar — Yangon", offset: "UTC+06:30" },
  { value: "Asia/Bangkok", country: "th", label: "Thailand — Bangkok", offset: "UTC+07:00" },
  { value: "Asia/Ho_Chi_Minh", country: "vn", label: "Vietnam — Ho Chi Minh City", offset: "UTC+07:00" },
  { value: "Asia/Jakarta", country: "id", label: "Indonesia — Jakarta", offset: "UTC+07:00" },
  { value: "Asia/Singapore", country: "sg", label: "Singapore", offset: "UTC+08:00" },
  { value: "Asia/Kuala_Lumpur", country: "my", label: "Malaysia — Kuala Lumpur", offset: "UTC+08:00" },
  { value: "Asia/Manila", country: "ph", label: "Philippines — Manila", offset: "UTC+08:00" },
  { value: "Asia/Shanghai", country: "cn", label: "China — Shanghai", offset: "UTC+08:00" },
  { value: "Asia/Hong_Kong", country: "hk", label: "Hong Kong", offset: "UTC+08:00" },
  { value: "Asia/Taipei", country: "tw", label: "Taiwan — Taipei", offset: "UTC+08:00" },
  { value: "Australia/Perth", country: "au", label: "Australia — Perth", offset: "UTC+08:00" },
  { value: "Asia/Seoul", country: "kr", label: "South Korea — Seoul", offset: "UTC+09:00" },
  { value: "Asia/Tokyo", country: "jp", label: "Japan — Tokyo", offset: "UTC+09:00" },
  { value: "Australia/Sydney", country: "au", label: "Australia — Sydney", offset: "UTC+10:00" },
  { value: "Pacific/Auckland", country: "nz", label: "New Zealand — Auckland", offset: "UTC+12:00" },
];

/** Look up display info for an IANA zone value; falls back to the raw value
 * itself (no flag, no offset) for anything outside the curated list, e.g. a
 * zone a member's own browser reported that isn't in TIMEZONE_OPTIONS. */
export function resolveTimezoneOption(value: string | null | undefined): TimezoneOption {
  const v = value || "UTC";
  return TIMEZONE_OPTIONS.find((tz) => tz.value === v) || { value: v, label: v, offset: "" };
}
