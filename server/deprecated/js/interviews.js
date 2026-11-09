/**
 * Shared Interviews calendar/table for admin + user apps.
 * BaymaxWorkspace.mountInterviews(root, config)
 */
(function (global) {
  "use strict";

  const BW = global.BaymaxWorkspace || (global.BaymaxWorkspace = {});
  const HOUR_START = 6;
  const HOUR_END = 22;
  const MAX_LANES = 4;

  function mountInterviews(root, config) {
    if (!root) throw new Error("mountInterviews: root required");
    const api = config.api;
    if (typeof api !== "function") throw new Error("mountInterviews: config.api required");

    const filters = Object.assign(
      { scope: true, user: false, profile: true, status: true, type: true },
      config.filters || {}
    );
    const role = config.role || "user";
    const title = config.title || "Interviews";
    const subtitle =
      config.subtitle ||
      (role === "admin"
        ? "Team schedule, calendar, and interview activity."
        : "Your schedule, calendar, and interview activity.");

    const state = {
      scope: config.defaultScope || (filters.scope ? "mine" : "all"),
      userId: "",
      view: "calendar",
      period: "week",
      anchor: BW.startOfLocalDay(new Date()),
      interviews: [],
      selectedId: null,
      users: [],
    };
    const colorForProfile = BW.makeColorMap();

    function $(sel) {
      return root.querySelector(sel);
    }

    function ensurePopover() {
      let pop = document.getElementById("ws-iv-popover");
      if (!pop) {
        pop = document.createElement("div");
        pop.id = "ws-iv-popover";
        pop.className = "day-popover hidden";
        document.body.appendChild(pop);
      }
      return pop;
    }

    root.innerHTML = `
      <div class="page-head">
        <div>
          <h1 class="page-title">${BW.esc(title)}</h1>
          <p class="page-sub">${BW.esc(subtitle)}</p>
        </div>
        <span class="hint" data-iv="countHint"></span>
      </div>

      <div class="toolbar" data-iv="filters">
        ${filters.scope ? `
        <div class="scope-toggle" data-iv="scopeToggle">
          <button type="button" data-scope="mine" class="${state.scope === "mine" ? "active" : ""}">Mine</button>
          <button type="button" data-scope="all" class="${state.scope === "all" ? "active" : ""}">All</button>
        </div>` : ""}
        <div class="seg" data-iv="viewToggle">
          <button type="button" data-view="calendar" class="active">Calendar</button>
          <button type="button" data-view="table">Table</button>
        </div>
        ${filters.user ? `
        <select data-iv="userFilter">
          <option value="">All users</option>
        </select>` : ""}
        ${filters.status ? `
        <select data-iv="statusFilter">
          <option value="">All statuses</option>
          <option value="scheduled">Scheduled</option>
          <option value="completed">Completed</option>
          <option value="cancelled">Cancelled</option>
          <option value="rescheduled">Rescheduled</option>
        </select>` : ""}
        ${filters.profile ? `
        <select data-iv="profileFilter">
          <option value="">All profiles</option>
        </select>` : ""}
        ${filters.type ? `
        <select data-iv="typeFilter" class="hidden">
          <option value="">All types</option>
        </select>` : ""}
      </div>

      <div class="toolbar tight" data-iv="calToolbar">
        <button class="btn-primary btn" type="button" data-iv="today">Today</button>
        <button type="button" class="btn btn-icon" data-iv="prev" title="Previous" aria-label="Previous">
          <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 18l-6-6 6-6"/></svg>
        </button>
        <button type="button" class="btn btn-icon" data-iv="next" title="Next" aria-label="Next">
          <svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 18l6-6-6-6"/></svg>
        </button>
        <span class="period-label" data-iv="periodLabel"></span>
        <div class="spacer"></div>
        <div class="seg" data-iv="periodToggle">
          <button type="button" data-period="week" class="active">Week</button>
          <button type="button" data-period="month">Month</button>
        </div>
      </div>

      <div class="legend" data-iv="legend"></div>

      <div class="ws-iv-layout">
        <div class="ws-iv-main panel">
          <div class="ws-iv-calendar" data-iv="calendarPane"></div>
          <div class="ws-iv-table hidden" data-iv="tablePane"></div>
        </div>
        <aside class="ws-iv-detail" data-iv="detailPane">
          <div class="empty">Select an interview to see details.</div>
        </aside>
      </div>`;

    function clientFiltered() {
      const typeSel = $('[data-iv="typeFilter"]');
      const type = typeSel ? typeSel.value : "";
      if (!type) return state.interviews.slice();
      return state.interviews.filter((iv) => (iv.interview_type || "") === type);
    }

    function visibleRange() {
      if (state.view === "table") {
        const from = BW.addDays(BW.startOfLocalDay(new Date()), -14);
        const to = BW.addDays(BW.startOfLocalDay(new Date()), 8 * 7);
        return { from, to };
      }
      if (state.period === "week") {
        const from = BW.startOfWeek(state.anchor);
        const to = BW.addDays(from, 7);
        return { from: BW.addDays(from, -3), to: BW.addDays(to, 3) };
      }
      const first = BW.startOfMonth(state.anchor);
      const last = BW.endOfMonth(state.anchor);
      const gridStart = BW.startOfWeek(first);
      const gridEnd = BW.addDays(BW.startOfWeek(last), 7);
      return { from: BW.addDays(gridStart, -3), to: BW.addDays(gridEnd, 3) };
    }

    function rebuildProfileFilter() {
      const sel = $('[data-iv="profileFilter"]');
      if (!sel) return;
      const cur = sel.value;
      const seen = new Map();
      for (const iv of state.interviews) {
        if (!iv.profile_id) continue;
        if (!seen.has(iv.profile_id)) seen.set(iv.profile_id, BW.profileLabel(iv));
      }
      let html = '<option value="">All profiles</option>';
      for (const [id, name] of [...seen.entries()].sort((a, b) => a[1].localeCompare(b[1]))) {
        html += `<option value="${BW.esc(id)}">${BW.esc(name)}</option>`;
      }
      sel.innerHTML = html;
      if ([...seen.keys()].includes(cur) || cur === "") sel.value = cur;
    }

    function rebuildTypeFilter() {
      const sel = $('[data-iv="typeFilter"]');
      if (!sel) return;
      const cur = sel.value;
      const types = new Set();
      for (const iv of state.interviews) if (iv.interview_type) types.add(iv.interview_type);
      let html = '<option value="">All types</option>';
      for (const t of [...types].sort()) html += `<option value="${BW.esc(t)}">${BW.esc(t)}</option>`;
      sel.innerHTML = html;
      if (types.has(cur) || cur === "") sel.value = cur;
    }

    function rebuildUserFilter() {
      const sel = $('[data-iv="userFilter"]');
      if (!sel) return;
      const cur = state.userId || sel.value;
      const seen = new Map();
      for (const u of state.users) {
        if (u && u.id) seen.set(u.id, u.email + (u.name ? " — " + u.name : ""));
      }
      for (const iv of state.interviews) {
        if (!iv.user_id || seen.has(iv.user_id)) continue;
        seen.set(iv.user_id, iv.user_email || iv.user_name || iv.user_id);
      }
      let html = '<option value="">All users</option>';
      for (const [id, label] of [...seen.entries()].sort((a, b) => a[1].localeCompare(b[1]))) {
        html += `<option value="${BW.esc(id)}">${BW.esc(label)}</option>`;
      }
      sel.innerHTML = html;
      if ([...seen.keys()].includes(cur) || cur === "") {
        sel.value = cur;
        state.userId = cur;
      }
    }

    function renderLegend() {
      const el = $('[data-iv="legend"]');
      const map = new Map();
      for (const iv of clientFiltered()) {
        const key = iv.profile_id || iv.profile_key || ("u:" + (iv.user_id || "none"));
        if (!map.has(key)) map.set(key, { label: BW.profileLabel(iv), color: colorForProfile(iv) });
      }
      if (!map.size) { el.innerHTML = ""; return; }
      let html = '<span class="lab">Profiles</span>';
      for (const { label, color } of map.values()) {
        html += `<span class="leg-chip"><span class="swatch" style="background:${color}"></span>${BW.esc(label)}</span>`;
      }
      el.innerHTML = html;
    }

    function overlaps(a, b) {
      return a.start < b.end && b.start < a.end;
    }

    function lanePack(dayEvents) {
      const items = dayEvents.map((ev) => ({ ...ev, lane: 0 })).sort((a, b) => a.start - b.start || a.end - b.end);
      const laneEnds = [];
      const conflictIds = new Set();
      for (const ev of items) {
        let placed = false;
        for (let i = 0; i < laneEnds.length; i++) {
          if (laneEnds[i] <= ev.start) {
            ev.lane = i;
            laneEnds[i] = ev.end;
            placed = true;
            break;
          }
        }
        if (!placed) {
          ev.lane = laneEnds.length;
          laneEnds.push(ev.end);
        }
      }
      for (let i = 0; i < items.length; i++) {
        for (let j = i + 1; j < items.length; j++) {
          if (overlaps(items[i], items[j])) {
            conflictIds.add(items[i].id);
            conflictIds.add(items[j].id);
          }
        }
      }
      return { items, maxLane: Math.max(1, laneEnds.length), conflictIds };
    }

    function updatePeriodLabel() {
      const el = $('[data-iv="periodLabel"]');
      const tz = BW.browserTimezoneAbbr();
      if (state.period === "week") {
        const from = BW.startOfWeek(state.anchor);
        const to = BW.addDays(from, 6);
        el.textContent = from.toLocaleDateString(undefined, { month: "short", day: "numeric" })
          + " – " + to.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
          + " · " + tz;
      } else {
        el.textContent = state.anchor.toLocaleDateString(undefined, { month: "long", year: "numeric" })
          + " · " + tz;
      }
    }

    function relativeDate(d) {
      const today = BW.startOfLocalDay(new Date());
      const day = BW.startOfLocalDay(d);
      const diff = Math.round((day - today) / 86400000);
      if (diff === 0) return "Today";
      if (diff === 1) return "Tomorrow";
      if (diff === -1) return "Yesterday";
      return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
    }

    function clearDetail() {
      $('[data-iv="detailPane"]').innerHTML = '<div class="empty">Select an interview to see details.</div>';
    }

    function showDetail(iv) {
      const { start, end } = BW.interviewBounds(iv);
      const color = colorForProfile(iv);
      const st = (iv.status || "scheduled").toLowerCase();
      const localTz = BW.browserTimezoneAbbr();
      const localRange = start
        ? BW.fmtDate(start) + " · " + BW.fmtRange(start, end) + " " + localTz
        : "—";
      const storedAbbr = BW.timezoneAbbr(iv.timezone);
      const displayTz = BW.displayTimezoneLabel(iv.timezone);
      let tzLabel = displayTz;
      if (storedAbbr && storedAbbr !== BW.displayTimezoneAbbr(iv.timezone)) {
        tzLabel = `${displayTz} (stored ${storedAbbr})`;
      }
      const rows = [
        ["When (local)", localRange],
        ["Timezone", tzLabel],
        ["Profile", BW.profileLabel(iv)],
        ["Status", iv.status || "—"],
        ["Type", iv.interview_type || "—"],
        ["Company", iv.company_name || "—"],
        ["Role", iv.position_title || "—"],
        ["Location", iv.location || "—"],
        ["Interviewer", [iv.interviewer_name, iv.interviewer_email].filter(Boolean).join(" · ") || "—"],
        ["Editor", [iv.user_name, iv.user_email].filter(Boolean).join(" · ") || "—"],
      ];
      let html = `<h2><span class="swatch" style="background:${color};display:inline-block;vertical-align:middle;margin-right:8px;"></span>${BW.esc(iv.title || "Interview")}</h2>`;
      html += `<span class="pill ${BW.esc(st)}">${BW.esc(iv.status || "scheduled")}</span>`;
      html += '<div class="meta-grid">';
      for (const [k, v] of rows) {
        html += `<div><div class="k">${BW.esc(k)}</div><div class="v">${BW.esc(v)}</div></div>`;
      }
      html += "</div>";
      if (iv.description) html += `<div class="k">Description</div><div class="notes">${BW.esc(iv.description)}</div>`;
      if (iv.notes) html += `<div class="k" style="margin-top:10px;">Notes</div><div class="notes">${BW.esc(iv.notes)}</div>`;
      $('[data-iv="detailPane"]').innerHTML = html;
    }

    function selectInterview(id) {
      state.selectedId = id;
      const iv = state.interviews.find((x) => x.id === id);
      if (iv) showDetail(iv);
      if (state.view === "calendar") renderCalendar();
      else renderTable();
    }

    function hidePopover() {
      ensurePopover().classList.add("hidden");
    }

    function openDayPopover(evt, dayMs) {
      evt.stopPropagation();
      const dayStart = BW.startOfLocalDay(new Date(dayMs));
      const dayEnd = BW.addDays(dayStart, 1);
      const list = clientFiltered().map((iv) => {
        const { start, end } = BW.interviewBounds(iv);
        return { ...iv, start, end };
      }).filter((iv) => iv.start && iv.start < dayEnd && (iv.end || iv.start) > dayStart)
        .sort((a, b) => a.start - b.start);

      const pop = ensurePopover();
      let html = `<h3>${BW.esc(BW.fmtDate(dayStart))}</h3>`;
      if (!list.length) html += '<div class="hint">No interviews</div>';
      for (const iv of list) {
        const color = colorForProfile(iv);
        html += `<div class="row" data-select-id="${BW.esc(iv.id)}">
          <span class="swatch" style="background:${color};margin-top:3px;"></span>
          <div><div><strong>${BW.esc(iv.title || "Interview")}</strong></div>
          <div class="hint">${BW.esc(BW.fmtRange(iv.start, iv.end))} ${BW.esc(BW.browserTimezoneAbbr())} · ${BW.esc(BW.profileLabel(iv))}</div></div>
        </div>`;
      }
      pop.innerHTML = html;
      pop.classList.remove("hidden");
      const x = Math.min(evt.clientX, window.innerWidth - 380);
      const y = Math.min(evt.clientY, window.innerHeight - 200);
      pop.style.left = Math.max(8, x) + "px";
      pop.style.top = Math.max(8, y) + "px";
      pop.querySelectorAll("[data-select-id]").forEach((row) => {
        row.addEventListener("click", () => {
          selectInterview(row.getAttribute("data-select-id"));
          hidePopover();
        });
      });
    }

    function renderWeek() {
      const weekStart = BW.startOfWeek(state.anchor);
      const days = Array.from({ length: 7 }, (_, i) => BW.addDays(weekStart, i));
      const today = BW.startOfLocalDay(new Date());
      const list = clientFiltered().map((iv) => {
        const { start, end } = BW.interviewBounds(iv);
        return { ...iv, start, end };
      }).filter((iv) => iv.start);

      let html = '<div class="cal-wrap"><div class="week-scroll"><div class="week-grid">';
      html += `<div class="week-head" style="grid-column:1;font-size:11px;color:var(--muted);display:flex;align-items:center;justify-content:center;">${BW.esc(BW.browserTimezoneAbbr())}</div>`;
      days.forEach((d, i) => {
        const isToday = d.getTime() === today.getTime();
        html += `<div class="week-head${isToday ? " today" : ""}" style="grid-column:${i + 2}"><div class="dname">${d.toLocaleDateString(undefined, { weekday: "short" })}</div><div class="ddate">${d.getDate()}</div></div>`;
      });

      html += '<div style="grid-column:1;grid-row:2">';
      for (let h = HOUR_START; h < HOUR_END; h++) {
        const label = new Date(2000, 0, 1, h).toLocaleTimeString(undefined, { hour: "numeric" });
        html += `<div class="time-col">${BW.esc(label)}</div>`;
      }
      html += "</div>";

      days.forEach((d, di) => {
        const dayStart = BW.startOfLocalDay(d);
        const dayEnd = BW.addDays(dayStart, 1);
        const dayEvs = list.filter((iv) => iv.start < dayEnd && (iv.end || iv.start) > dayStart).map((iv) => ({
          ...iv,
          start: Math.max(iv.start.getTime(), dayStart.getTime()),
          end: Math.min((iv.end || new Date(iv.start.getTime() + 3600000)).getTime(), dayEnd.getTime()),
          rawStart: iv.start,
          rawEnd: iv.end || new Date(iv.start.getTime() + 3600000),
        }));
        const { items, maxLane, conflictIds } = lanePack(dayEvs);
        const gridTop = dayStart.getTime() + HOUR_START * 3600000;
        const gridBottom = dayStart.getTime() + HOUR_END * 3600000;

        html += `<div class="day-col" style="grid-column:${di + 2};grid-row:2" data-day="${di}">`;
        const visible = items.filter((ev) => ev.lane < MAX_LANES);
        const overflow = items.filter((ev) => ev.lane >= MAX_LANES);
        const laneCount = Math.min(maxLane, MAX_LANES);

        for (const ev of visible) {
          const topMs = Math.max(ev.start, gridTop) - gridTop;
          const botMs = Math.min(ev.end, gridBottom) - gridTop;
          if (botMs <= 0 || topMs >= (HOUR_END - HOUR_START) * 3600000) continue;
          const topPct = (topMs / ((HOUR_END - HOUR_START) * 3600000)) * 100;
          const heightPct = Math.max(2, ((botMs - topMs) / ((HOUR_END - HOUR_START) * 3600000)) * 100);
          const widthPct = 100 / laneCount;
          const leftPct = ev.lane * widthPct;
          const color = colorForProfile(ev);
          const earlyOrLate = ev.rawStart.getTime() < gridTop || ev.rawEnd.getTime() > gridBottom;
          const cls = [
            "evt",
            conflictIds.has(ev.id) ? "conflict" : "",
            (ev.status || "").toLowerCase(),
            state.selectedId === ev.id ? "selected" : "",
          ].filter(Boolean).join(" ");
          const tip = [
            ev.title,
            BW.profileLabel(ev),
            BW.fmtRange(ev.rawStart, ev.rawEnd),
            earlyOrLate ? "(extends outside 6 AM–10 PM)" : "",
            conflictIds.has(ev.id) ? "⚠ Overlaps another interview" : "",
          ].filter(Boolean).join("\n");
          html += `<div class="${cls}" style="top:${topPct}%;height:${heightPct}%;left:calc(${leftPct}% + 1px);width:calc(${widthPct}% - 3px);background:${color};border-left:3px solid rgba(0,0,0,.35);"
            title="${BW.esc(tip)}" data-select-id="${BW.esc(ev.id)}">
            <div class="et">${earlyOrLate ? "⋮ " : ""}${BW.esc(ev.title || "Interview")}</div>
            <div class="em">${BW.esc(BW.profileLabel(ev))}</div>
          </div>`;
        }
        if (overflow.length) {
          html += `<div class="more-chip" style="bottom:4px;" title="${overflow.length} more">+${overflow.length} more</div>`;
        }
        html += "</div>";
      });

      html += "</div></div></div>";
      const pane = $('[data-iv="calendarPane"]');
      pane.innerHTML = html;
      pane.querySelectorAll("[data-select-id]").forEach((el) => {
        el.addEventListener("click", (e) => {
          e.stopPropagation();
          selectInterview(el.getAttribute("data-select-id"));
        });
      });
    }

    function renderMonth() {
      const first = BW.startOfMonth(state.anchor);
      const gridStart = BW.startOfWeek(first);
      const today = BW.startOfLocalDay(new Date());
      const list = clientFiltered().map((iv) => {
        const { start, end } = BW.interviewBounds(iv);
        return { ...iv, start, end };
      }).filter((iv) => iv.start);

      let html = '<div class="cal-wrap"><div class="month-grid">';
      const dows = Array.from({ length: 7 }, (_, i) => BW.addDays(gridStart, i));
      for (const d of dows) {
        html += `<div class="month-dow">${d.toLocaleDateString(undefined, { weekday: "short" })}</div>`;
      }
      for (let i = 0; i < 42; i++) {
        const d = BW.addDays(gridStart, i);
        const dayStart = BW.startOfLocalDay(d);
        const dayEnd = BW.addDays(dayStart, 1);
        const inMonth = d.getMonth() === state.anchor.getMonth();
        const isToday = dayStart.getTime() === today.getTime();
        const dayEvs = list.filter((iv) => iv.start < dayEnd && (iv.end || iv.start) > dayStart)
          .sort((a, b) => a.start - b.start);
        const shown = dayEvs.slice(0, 3);
        const more = dayEvs.length - shown.length;
        html += `<div class="month-cell${inMonth ? "" : " out"}${isToday ? " today" : ""}" data-day-ms="${dayStart.getTime()}">`;
        html += `<div class="mday">${d.getDate()}</div>`;
        for (const ev of shown) {
          const color = colorForProfile(ev);
          html += `<span class="mchip" style="background:${color}" title="${BW.esc(ev.title)}">${BW.esc(BW.fmtTime(ev.start))} ${BW.esc(ev.title || "")}</span>`;
        }
        if (more > 0) html += `<div class="mmore">+${more} more</div>`;
        html += "</div>";
      }
      html += "</div></div>";
      const pane = $('[data-iv="calendarPane"]');
      pane.innerHTML = html;
      pane.querySelectorAll("[data-day-ms]").forEach((cell) => {
        cell.addEventListener("click", (e) => openDayPopover(e, +cell.getAttribute("data-day-ms")));
      });
    }

    function renderCalendar() {
      updatePeriodLabel();
      if (state.period === "week") renderWeek();
      else renderMonth();
    }

    function renderTable() {
      updatePeriodLabel();
      let list = clientFiltered().map((iv) => {
        const { start, end } = BW.interviewBounds(iv);
        return { ...iv, start, end };
      }).filter((iv) => iv.start);
      list.sort((a, b) => a.start - b.start);

      const pane = $('[data-iv="tablePane"]');
      if (!list.length) {
        pane.innerHTML = '<div class="loading">No interviews in this range.</div>';
        return;
      }

      let html = `<table><thead><tr>
        <th>Date</th><th>Time</th><th>Title</th><th>Profile</th><th>Type</th><th>Status</th><th>Company</th><th>Editor</th>
      </tr></thead><tbody>`;
      let prevWeek = null;
      for (const iv of list) {
        const wk = BW.startOfWeek(iv.start).getTime();
        const weekStart = prevWeek !== null && wk !== prevWeek;
        prevWeek = wk;
        const color = colorForProfile(iv);
        const st = (iv.status || "scheduled").toLowerCase();
        html += `<tr class="iv-row${weekStart ? " week-start" : ""}${state.selectedId === iv.id ? " selected" : ""}" data-select-id="${BW.esc(iv.id)}">
          <td>${BW.esc(relativeDate(iv.start))}</td>
          <td>${BW.esc(BW.fmtRange(iv.start, iv.end || iv.start))} ${BW.esc(BW.browserTimezoneAbbr())}</td>
          <td>${BW.esc(iv.title || "Interview")}</td>
          <td><span class="pchip"><span class="swatch" style="background:${color}"></span>${BW.esc(BW.profileLabel(iv))}</span></td>
          <td>${BW.esc(iv.interview_type || "—")}</td>
          <td><span class="pill ${BW.esc(st)}">${BW.esc(iv.status || "scheduled")}</span></td>
          <td>${BW.esc(iv.company_name || "—")}</td>
          <td>${BW.esc(iv.user_name || iv.user_email || "—")}</td>
        </tr>`;
      }
      html += "</tbody></table>";
      pane.innerHTML = html;
      pane.querySelectorAll("[data-select-id]").forEach((row) => {
        row.addEventListener("click", () => selectInterview(row.getAttribute("data-select-id")));
      });
    }

    async function refresh() {
      const pane = state.view === "calendar" ? $('[data-iv="calendarPane"]') : $('[data-iv="tablePane"]');
      pane.innerHTML = '<div class="loading">Loading…</div>';
      const { from, to } = visibleRange();
      const params = new URLSearchParams({
        scope: state.scope,
        from: BW.toQueryIso(from),
        to: BW.toQueryIso(to),
        limit: "5000",
      });
      const statusSel = $('[data-iv="statusFilter"]');
      const profileSel = $('[data-iv="profileFilter"]');
      if (statusSel && statusSel.value) params.set("status", statusSel.value);
      if (profileSel && profileSel.value) params.set("profile_id", profileSel.value);
      if (filters.user && state.userId) params.set("user_id", state.userId);
      try {
        if (filters.user && typeof config.loadUsers === "function" && !state.users.length) {
          try { state.users = (await config.loadUsers()) || []; } catch (_) { state.users = []; }
        }
        state.interviews = await api("/interviews/?" + params.toString());
        rebuildProfileFilter();
        rebuildTypeFilter();
        rebuildUserFilter();
        renderLegend();
        $('[data-iv="countHint"]').textContent =
          state.interviews.length + " interview" + (state.interviews.length === 1 ? "" : "s");
        if (state.view === "calendar") renderCalendar();
        else renderTable();
        if (state.selectedId) {
          const still = state.interviews.find((i) => i.id === state.selectedId);
          if (still) showDetail(still);
          else { state.selectedId = null; clearDetail(); }
        }
      } catch (e) {
        pane.innerHTML = '<div class="err" style="padding:16px;">' + BW.esc(e.message) + "</div>";
      }
    }

    function setView(v) {
      state.view = v;
      $('[data-iv="viewToggle"]').querySelectorAll("button").forEach((b) =>
        b.classList.toggle("active", b.dataset.view === v)
      );
      $('[data-iv="calToolbar"]').classList.toggle("hidden", v !== "calendar");
      $('[data-iv="calendarPane"]').classList.toggle("hidden", v !== "calendar");
      $('[data-iv="tablePane"]').classList.toggle("hidden", v !== "table");
      const typeSel = $('[data-iv="typeFilter"]');
      if (typeSel) typeSel.classList.toggle("hidden", v !== "table");
      if (v === "table") renderTable();
      else renderCalendar();
    }

    // Bind toolbar events
    const scopeToggle = $('[data-iv="scopeToggle"]');
    if (scopeToggle) {
      scopeToggle.querySelectorAll("button").forEach((b) => {
        b.addEventListener("click", () => {
          state.scope = b.dataset.scope;
          scopeToggle.querySelectorAll("button").forEach((x) =>
            x.classList.toggle("active", x.dataset.scope === state.scope)
          );
          refresh();
        });
      });
    }
    $('[data-iv="viewToggle"]').querySelectorAll("button").forEach((b) => {
      b.addEventListener("click", () => setView(b.dataset.view));
    });
    $('[data-iv="periodToggle"]').querySelectorAll("button").forEach((b) => {
      b.addEventListener("click", () => {
        state.period = b.dataset.period;
        $('[data-iv="periodToggle"]').querySelectorAll("button").forEach((x) =>
          x.classList.toggle("active", x.dataset.period === state.period)
        );
        refresh();
      });
    });
    $('[data-iv="today"]').addEventListener("click", () => {
      state.anchor = BW.startOfLocalDay(new Date());
      refresh();
    });
    $('[data-iv="prev"]').addEventListener("click", () => {
      if (state.period === "week") state.anchor = BW.addDays(state.anchor, -7);
      else state.anchor = new Date(state.anchor.getFullYear(), state.anchor.getMonth() - 1, 1);
      refresh();
    });
    $('[data-iv="next"]').addEventListener("click", () => {
      if (state.period === "week") state.anchor = BW.addDays(state.anchor, 7);
      else state.anchor = new Date(state.anchor.getFullYear(), state.anchor.getMonth() + 1, 1);
      refresh();
    });
    ["statusFilter", "profileFilter", "typeFilter"].forEach((key) => {
      const sel = $(`[data-iv="${key}"]`);
      if (!sel) return;
      sel.addEventListener("change", () => {
        if (key === "typeFilter") {
          renderLegend();
          if (state.view === "calendar") renderCalendar();
          else renderTable();
        } else refresh();
      });
    });
    const userSel = $('[data-iv="userFilter"]');
    if (userSel) {
      userSel.addEventListener("change", () => {
        state.userId = userSel.value;
        refresh();
      });
    }

    const onDocClick = (e) => {
      const pop = document.getElementById("ws-iv-popover");
      if (pop && !pop.classList.contains("hidden") && !pop.contains(e.target)) hidePopover();
    };
    document.addEventListener("click", onDocClick);

    const controller = {
      refresh,
      destroy() {
        document.removeEventListener("click", onDocClick);
        root.innerHTML = "";
        root._wsInterviews = null;
      },
    };
    root._wsInterviews = controller;
    refresh();
    return controller;
  }

  BW.mountInterviews = mountInterviews;
})(window);
