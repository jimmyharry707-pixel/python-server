/**
 * Shared helpers for CareerCraft admin + user workspace UIs.
 * Attaches to window.BaymaxWorkspace (IIFE, no build step).
 */
(function (global) {
  "use strict";

  const BW = global.BaymaxWorkspace || (global.BaymaxWorkspace = {});

  const PROFILE_PALETTE = [
    "#70b5f9", "#57c26b", "#f6c177", "#c4a7e7", "#eb6f92",
    "#9ccfd8", "#f6a192", "#7aa2f7", "#a6e3a1", "#fab387",
    "#89b4fa", "#f5c2e7",
  ];

  /**
   * Fixed UTC offsets (hours) for interview timezone abbreviations — mirrors
   * interview_service.TIMEZONE_UTC_OFFSETS. Desktop sync stores wall-clock times
   * in start_time_utc with a literal "Z" (not true UTC); the timezone field is
   * the real zone for that wall clock.
   */
  const TIMEZONE_UTC_OFFSETS = {
    EST: -5, EDT: -4, CST: -6, CDT: -5, MST: -7, MDT: -6,
    PST: -8, PDT: -7, AKST: -9, AKDT: -8, HST: -10,
    AST: -4, ADT: -3, GMT: 0, UTC: 0, BST: 1,
  };

  const TIMEZONE_FAMILIES = {
    EST: "ET", EDT: "ET", CST: "CT", CDT: "CT",
    MST: "MT", MDT: "MT", PST: "PT", PDT: "PT",
    AKST: "AKT", AKDT: "AKT", AST: "AT", ADT: "AT",
    HST: "HT", GMT: "GMT", BST: "GMT", UTC: "UTC",
  };

  const FAMILY_STD_ABBR = {
    ET: "EST", CT: "CST", MT: "MST", PT: "PST",
    AKT: "AKST", AT: "AST", HT: "HST", GMT: "GMT", UTC: "UTC",
  };

  const STD_OFFSET_TO_FAMILY = {
    "-5": "ET", "-6": "CT", "-7": "MT", "-8": "PT",
    "-9": "AKT", "-10": "HT", "-4": "AT", "0": "GMT",
  };

  const FAMILY_DST_ABBR = {
    ET: "EDT", CT: "CDT", MT: "MDT", PT: "PDT",
    AKT: "AKDT", AT: "ADT", HT: null, GMT: "BST", UTC: null,
  };

  const FAMILY_LABELS = {
    ET: "Eastern Time", CT: "Central Time", MT: "Mountain Time", PT: "Pacific Time",
    AKT: "Alaska Time", AT: "Atlantic Time", HT: "Hawaii Time",
    GMT: "London Time", UTC: "Universal Time",
  };

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }

  function jsStr(s) {
    return JSON.stringify(String(s ?? ""));
  }

  function clientTzOffsetMinutes() {
    return -new Date().getTimezoneOffset();
  }

  function startOfLocalDay(d) {
    const x = new Date(d);
    x.setHours(0, 0, 0, 0);
    return x;
  }

  function addDays(d, n) {
    const x = new Date(d);
    x.setDate(x.getDate() + n);
    return x;
  }

  /** Week starts on Sunday (US calendar convention). */
  function startOfWeek(d) {
    const x = startOfLocalDay(d);
    return addDays(x, -x.getDay());
  }

  /** Team work-plan week: Saturday → Friday. */
  function startOfWeekSaturday(d) {
    const x = startOfLocalDay(d);
    const day = x.getDay(); // 0=Sun … 6=Sat
    const diff = (day - 6 + 7) % 7;
    return addDays(x, -diff);
  }

  function startOfWeekMonday(d) {
    const x = startOfLocalDay(d);
    const day = x.getDay();
    const diff = day === 0 ? -6 : 1 - day;
    return addDays(x, diff);
  }

  function startOfMonth(d) {
    return new Date(d.getFullYear(), d.getMonth(), 1);
  }

  function endOfMonth(d) {
    return new Date(d.getFullYear(), d.getMonth() + 1, 0, 23, 59, 59, 999);
  }

  function hashStr(s) {
    let h = 0;
    const str = String(s || "");
    for (let i = 0; i < str.length; i++) h = ((h << 5) - h + str.charCodeAt(i)) | 0;
    return Math.abs(h);
  }

  function timezoneFamily(abbr) {
    const a = String(abbr || "").toUpperCase();
    return TIMEZONE_FAMILIES[a] || a;
  }

  function familyStandardOffset(family) {
    const std = FAMILY_STD_ABBR[family];
    if (!std) return TIMEZONE_UTC_OFFSETS[family] ?? 0;
    return TIMEZONE_UTC_OFFSETS[std] ?? 0;
  }

  function isBrowserDst() {
    const y = new Date().getFullYear();
    const jan = new Date(y, 0, 1).getTimezoneOffset();
    const jul = new Date(y, 6, 1).getTimezoneOffset();
    return new Date().getTimezoneOffset() < Math.max(jan, jul);
  }

  function seasonalAbbrForFamily(family) {
    const fam = family || "UTC";
    if (isBrowserDst() && FAMILY_DST_ABBR[fam]) return FAMILY_DST_ABBR[fam];
    return FAMILY_STD_ABBR[fam] || fam;
  }

  function browserTimezoneAbbr() {
    const y = new Date().getFullYear();
    const jan = new Date(y, 0, 1).getTimezoneOffset();
    const jul = new Date(y, 6, 1).getTimezoneOffset();
    const stdMin = Math.max(jan, jul);
    const stdHours = -stdMin / 60;
    const family = STD_OFFSET_TO_FAMILY[String(stdHours)] || "UTC";
    return seasonalAbbrForFamily(family);
  }

  function timezoneAbbr(value) {
    const token = String(value || "").trim().split(/\s+/)[0].toUpperCase();
    if (token && Object.prototype.hasOwnProperty.call(TIMEZONE_UTC_OFFSETS, token)) return token;
    return browserTimezoneAbbr();
  }

  function displayTimezoneAbbr(value) {
    return seasonalAbbrForFamily(timezoneFamily(timezoneAbbr(value)));
  }

  function displayTimezoneLabel(value) {
    const abbr = displayTimezoneAbbr(value);
    const fam = timezoneFamily(abbr);
    const name = FAMILY_LABELS[fam];
    return name ? `${abbr} - ${name}` : abbr;
  }

  function parseWallClock(iso) {
    if (!iso) return null;
    const raw = String(iso).trim()
      .replace(/Z$/i, "")
      .replace(/[+-]\d{2}:\d{2}$/, "")
      .split(".")[0];
    const m = raw.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?/);
    if (!m) return null;
    return {
      y: +m[1], mo: +m[2] - 1, d: +m[3],
      h: +m[4], mi: +m[5], s: +(m[6] || 0),
    };
  }

  function interviewBounds(iv) {
    const startParts = parseWallClock(iv.start_time_utc || iv.start_time);
    if (!startParts) return { start: null, end: null };
    let endParts = parseWallClock(iv.end_time_utc || iv.end_time);
    const srcAbbr = timezoneAbbr(iv.timezone);
    const srcFam = timezoneFamily(srcAbbr);
    const localFam = timezoneFamily(browserTimezoneAbbr());
    const toLocalDate = (p) => new Date(p.y, p.mo, p.d, p.h, p.mi, p.s);

    if (srcFam === localFam) {
      const start = toLocalDate(startParts);
      let end = endParts ? toLocalDate(endParts) : new Date(start.getTime() + 3600000);
      if (!(end > start)) end = new Date(start.getTime() + 3600000);
      return { start, end };
    }

    const srcOff = familyStandardOffset(srcFam);
    const localStdOff = familyStandardOffset(localFam);
    const deltaH = localStdOff - srcOff;
    const shift = (p) => new Date(p.y, p.mo, p.d, p.h + deltaH, p.mi, p.s);
    const start = shift(startParts);
    let end = endParts ? shift(endParts) : new Date(start.getTime() + 3600000);
    if (!(end > start)) end = new Date(start.getTime() + 3600000);
    return { start, end };
  }

  function fmtDate(d) {
    return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric", year: "numeric" });
  }

  function fmtTime(d) {
    return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  }

  function fmtRange(a, b) {
    if (!a) return "—";
    if (!b) return fmtTime(a);
    return fmtTime(a) + " – " + fmtTime(b);
  }

  function fmtAppliedDate(iso) {
    if (!iso) return "—";
    return new Date(iso).toLocaleDateString();
  }

  function fmtCalendarDate(iso, opts) {
    if (!iso) return "";
    return new Date(iso).toLocaleDateString(undefined, { timeZone: "UTC", ...(opts || {}) });
  }

  /** Interview times are wall-clock + timezone abbr (Z on start_time_utc is not true UTC). */
  function fmtInterviewWallClock(iso, tz) {
    if (!iso) return "—";
    const raw = String(iso).trim()
      .replace(/Z$/i, "")
      .replace(/[+-]\d{2}:\d{2}$/, "")
      .split(".")[0]
      .replace("T", " ");
    const abbr = String(tz || "").trim().split(/\s+/)[0];
    return abbr ? `${raw} ${abbr}` : raw;
  }

  /** Encode local wall-clock as Z timestamp — matches desktop interview sync convention. */
  function toQueryIso(d) {
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}Z`;
  }

  function profileLabel(iv) {
    return iv.profile_name || iv.profile_key || (iv.profile_id ? "Profile" : "Personal");
  }

  function makeColorMap() {
    const map = {};
    return function colorForProfile(iv) {
      const key = iv.profile_id || iv.profile_key || iv.user_id || "none";
      if (!map[key]) {
        map[key] = PROFILE_PALETTE[hashStr(key) % PROFILE_PALETTE.length];
      }
      return map[key];
    };
  }

  /**
   * Dual-axis bar chart for applications vs interviews.
   * @param {object} [opts]
   * @param {"compact"|"full"} [opts.variant="full"]
   */
  function barChartSvg(buckets, series, maxAppsHint, maxIvsHint, opts) {
    const variant = (opts && opts.variant) || "full";
    const compact = variant === "compact";
    const apps = series.applications || [];
    const ivs = series.interviews || [];
    if (!buckets.length) {
      return `<p class="hint">No data to chart${compact ? " for this window" : ""}.</p>`;
    }

    const normBuckets = buckets.map((b, i) => {
      if (typeof b === "string") {
        return {
          key: b,
          label: compact ? b : b.replace(/^\d{4}-/, ""),
          start: null,
          end: null,
          i,
        };
      }
      return { key: b.key, label: b.label || b.key, start: b.start, end: b.end, i };
    });

    const maxApps = Math.max(1, maxAppsHint || 0, ...apps);
    const maxIvs = Math.max(1, maxIvsHint || 0, ...ivs);
    const n = normBuckets.length;
    const padL = compact ? 36 : 40;
    const padR = compact ? 36 : 40;
    const padB = compact ? 40 : 52;
    const padT = compact ? 10 : 14;
    const W = compact
      ? Math.max(360, Math.min(720, padL + padR + n * (n > 40 ? 16 : n > 20 ? 24 : 36)))
      : Math.max(520, padL + padR + n * (n > 40 ? 22 : n > 20 ? 32 : 46));
    const H = compact ? 160 : 240;
    const chartH = H - padB - padT;
    const chartW = W - padL - padR;
    const groupW = chartW / n;
    const barW = compact
      ? Math.max(3, Math.min(10, groupW / 2 - 2))
      : Math.max(4, Math.min(14, groupW / 2 - 3));
    const gap = compact ? 2 : 3;
    const labelEvery = compact
      ? (n > 45 ? 7 : n > 30 ? 4 : n > 14 ? 2 : 1)
      : (n > 45 ? 7 : n > 30 ? 4 : n > 18 ? 2 : 1);
    const fontSize = compact ? 9 : 10;
    const midApps = Math.round(maxApps / 2);
    const midIvs = Math.round(maxIvs / 2);

    let axis = `<line x1="${padL}" y1="${padT + chartH}" x2="${W - padR}" y2="${padT + chartH}" stroke="var(--line)" />`;
    axis += `<line x1="${padL}" y1="${padT}" x2="${padL}" y2="${padT + chartH}" stroke="var(--line)" />`;
    axis += `<line x1="${W - padR}" y1="${padT}" x2="${W - padR}" y2="${padT + chartH}" stroke="var(--line)" />`;
    [[0, maxApps], [chartH / 2, midApps], [chartH, 0]].forEach(([yOff, val]) => {
      const y = padT + yOff;
      axis += `<text x="${padL - 6}" y="${y + 3}" text-anchor="end" font-size="${fontSize}" fill="var(--accent)">${val}</text>`;
    });
    [[0, maxIvs], [chartH / 2, midIvs], [chartH, 0]].forEach(([yOff, val]) => {
      const y = padT + yOff;
      axis += `<text x="${W - padR + 6}" y="${y + 3}" text-anchor="start" font-size="${fontSize}" fill="var(--ok)">${val}</text>`;
    });

    let bars = "";
    normBuckets.forEach((b, i) => {
      const a = apps[i] || 0, iv = ivs[i] || 0;
      const ha = (a / maxApps) * chartH;
      const hi = (iv / maxIvs) * chartH;
      const gx = padL + i * groupW + (groupW - barW * 2 - gap) / 2;
      const tipRange = (b.start && b.end)
        ? `${fmtCalendarDate(b.start)} – ${fmtCalendarDate(b.end)}`
        : b.label;
      bars += `<rect x="${gx.toFixed(1)}" y="${(padT + chartH - ha).toFixed(1)}" width="${barW.toFixed(1)}" height="${ha.toFixed(1)}" fill="var(--accent)"><title>${esc(tipRange)}: ${a} apps</title></rect>`;
      bars += `<rect x="${(gx + barW + gap).toFixed(1)}" y="${(padT + chartH - hi).toFixed(1)}" width="${barW.toFixed(1)}" height="${hi.toFixed(1)}" fill="var(--ok)"><title>${esc(tipRange)}: ${iv} interviews</title></rect>`;
      if (i % labelEvery === 0 || i === n - 1) {
        const maxLen = compact ? 10 : 12;
        const short = String(b.label).length > maxLen && n > (compact ? 14 : 20)
          ? String(b.label).slice(0, maxLen - 2) + "…"
          : b.label;
        bars += `<text x="${(padL + i * groupW + groupW / 2).toFixed(1)}" y="${padT + chartH + (compact ? 14 : 16)}" text-anchor="middle" font-size="${fontSize}" fill="var(--muted)">${esc(short)}</text>`;
      }
    });

    if (compact) {
      return `<div style="overflow-x:auto; width:100%; max-width:760px;">
        <div style="display:flex; gap:14px; margin-bottom:8px; font-size:11px; color:var(--muted); flex-wrap:wrap;">
          <span><span class="swatch" style="background:var(--accent);display:inline-block;vertical-align:middle;"></span> Applications</span>
          <span><span class="swatch" style="background:var(--ok);display:inline-block;vertical-align:middle;"></span> Interviews</span>
        </div>
        <svg width="100%" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" style="max-height:160px;" role="img" aria-label="Applications and interviews">${axis}${bars}</svg>
      </div>`;
    }

    return `<div style="overflow-x:auto; border:1px solid var(--line); border-radius:10px; background:var(--panel); padding:10px;">
      <div style="display:flex; gap:16px; margin-bottom:6px; font-size:14px; color:var(--muted); flex-wrap:wrap;">
        <span><span style="display:inline-block;width:10px;height:10px;background:var(--accent);border-radius:2px;vertical-align:middle;"></span> Applications (left scale)</span>
        <span><span style="display:inline-block;width:10px;height:10px;background:var(--ok);border-radius:2px;vertical-align:middle;"></span> Interviews (right scale)</span>
        <span>Separate scales — interview bars use their own max</span>
      </div>
      <svg width="${W}" height="${H}" role="img" aria-label="Applications and interviews per period">${axis}${bars}</svg>
    </div>`;
  }

  Object.assign(BW, {
    esc,
    jsStr,
    clientTzOffsetMinutes,
    startOfLocalDay,
    addDays,
    startOfWeek,
    startOfWeekSaturday,
    startOfWeekMonday,
    startOfMonth,
    endOfMonth,
    hashStr,
    timezoneAbbr,
    displayTimezoneAbbr,
    displayTimezoneLabel,
    browserTimezoneAbbr,
    interviewBounds,
    fmtDate,
    fmtTime,
    fmtRange,
    fmtAppliedDate,
    fmtCalendarDate,
    fmtInterviewWallClock,
    toQueryIso,
    profileLabel,
    makeColorMap,
    barChartSvg,
    PROFILE_PALETTE,
  });
})(window);
