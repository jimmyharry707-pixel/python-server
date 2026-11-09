/**
 * Shared Performance (observability / tracking) UI for admin + user apps.
 * BaymaxWorkspace.mountPerformance(root, config)
 */
(function (global) {
  "use strict";

  const BW = global.BaymaxWorkspace || (global.BaymaxWorkspace = {});

  const TRK_PERIOD_OPTIONS = {
    day: [
      [1, "Last 1 day"],
      [2, "Last 2 days"],
      [7, "Last 7 days"],
      [14, "Last 14 days"],
      [30, "Last 30 days"],
      [60, "Last 60 days"],
      [90, "Last 90 days"],
    ],
    week: [
      [1, "Last 1 week"],
      [8, "Last 8 weeks"],
      [12, "Last 12 weeks"],
      [26, "Last 26 weeks"],
      [52, "Last 52 weeks"],
    ],
    month: [
      [1, "Last 1 month"],
      [6, "Last 6 months"],
      [12, "Last 12 months"],
      [24, "Last 24 months"],
    ],
  };

  function trkDefaultPeriods(granularity) {
    if (granularity === "day") return 30;
    if (granularity === "month") return 12;
    return 12;
  }

  function formatTrkRange(startIso, endIso) {
    if (!startIso || !endIso) return "";
    const opts = { year: "numeric", month: "short", day: "numeric" };
    return `${BW.fmtCalendarDate(startIso, opts)} → ${BW.fmtCalendarDate(endIso, opts)}`;
  }

  function granularityWord(g) {
    return g === "day" ? "daily" : g === "month" ? "monthly" : "weekly";
  }

  function mountPerformance(root, config) {
    if (!root) throw new Error("mountPerformance: root required");
    const api = config.api;
    if (typeof api !== "function") throw new Error("mountPerformance: config.api required");

    const role = config.role || "user";
    const isAdmin = role === "admin";
    const filters = Object.assign(
      {
        windowChips: !isAdmin,
        granularity: isAdmin,
        user: isAdmin,
        profile: true,
        tableChartToggle: !isAdmin,
      },
      config.filters || {}
    );
    const features = Object.assign(
      {
        perUserTable: isAdmin,
        duplicates: isAdmin,
        browseDetail: isAdmin,
        adminBrowse: isAdmin,
      },
      config.features || {}
    );
    const endpoints = Object.assign(
      isAdmin
        ? {
            metrics: "/admin/api/tracking/metrics",
            applications: "/admin/api/applications",
            applicationDetail: "/admin/api/applications/",
            duplicates: "/admin/api/tracking/duplicates",
            resolveDuplicates: "/admin/api/tracking/duplicates/resolve",
            deleteUserData: "/admin/api/users/",
          }
        : {
            metrics: "/team/observability",
            applications: "/team/applications",
          },
      config.endpoints || {}
    );
    const chartVariant = config.chartVariant || (isAdmin ? "full" : "compact");
    const title = config.title || "Performance";
    const subtitle =
      config.subtitle ||
      (isAdmin
        ? "Applications and interviews by user over time."
        : "Applications and interviews across your profiles.");
    const appLimit = config.appLimit || (isAdmin ? 50 : 50);

    const state = {
      window: "7d",
      view: "table",
      granularity: "week",
      periods: 12,
      userId: "",
      profileKey: "",
      scope: "user",
      q: "",
      offset: 0,
      limit: appLimit,
      selectedAppId: "",
      lastMetrics: null,
    };

    function $(sel) {
      return root.querySelector(sel);
    }

    // ---- shell markup ----
    let filterHtml = "";
    if (filters.windowChips) {
      filterHtml += `
        <div class="seg" data-perf="window">
          <button type="button" data-window="1d">1 day</button>
          <button type="button" data-window="2d">2 days</button>
          <button type="button" data-window="7d" class="active">1 week</button>
          <button type="button" data-window="14d">2 weeks</button>
          <button type="button" data-window="30d">1 month</button>
          <button type="button" data-window="all">All</button>
        </div>`;
    }
    if (filters.granularity) {
      filterHtml += `
        <label>Granularity
          <select data-perf="gran">
            <option value="day">Daily</option>
            <option value="week" selected>Weekly</option>
            <option value="month">Monthly</option>
          </select>
        </label>
        <label>Window
          <select data-perf="periods"></select>
        </label>`;
    }
    if (filters.user) {
      filterHtml += `<label>User<select data-perf="user"><option value="">All users</option></select></label>`;
    }
    if (filters.profile) {
      if (isAdmin) {
        filterHtml += `<label>Profile<select data-perf="profile"><option value="">All profiles</option></select></label>`;
      } else {
        filterHtml += `<select data-perf="profile"><option value="">All assigned profiles</option></select>`;
      }
    }
    if (filters.tableChartToggle) {
      filterHtml += `
        <div class="seg" data-perf="viewToggle">
          <button type="button" data-view="table" class="active">Table</button>
          <button type="button" data-view="chart">Chart</button>
        </div>`;
    }

    const headHtml = isAdmin
      ? `<h2>${BW.esc(title)}</h2>
         <p class="page-sub">${BW.esc(subtitle)}</p>
         <p class="trk-range" data-perf="rangeHintAdmin"></p>`
      : `<div class="page-head">
          <div>
            <h1 class="page-title">${BW.esc(title)}</h1>
            <p class="page-sub">${BW.esc(subtitle)}</p>
          </div>
          <span class="hint" data-perf="rangeHint"></span>
        </div>`;

    let bodyHtml = `
      ${headHtml}
      <div class="toolbar">${filterHtml}</div>
      <div class="cards" data-perf="cards"></div>`;

    if (filters.tableChartToggle) {
      bodyHtml += `
        <div class="panel" style="margin-bottom:16px;">
          <div class="panel-head"><h2 class="panel-title">Metrics</h2></div>
          <div class="panel-body">
            <div data-perf="metricsTable"></div>
            <div data-perf="chart" class="hidden"></div>
          </div>
        </div>`;
    } else {
      bodyHtml += `<div data-perf="chart" style="margin-bottom:22px;"></div>`;
    }

    if (features.perUserTable) {
      bodyHtml += `<div data-perf="perUser" style="margin-bottom:22px;"></div>`;
    }

    if (features.duplicates) {
      bodyHtml += `
        <div style="margin-bottom:22px;" data-perf="dupSection">
          <h2>Duplicate records</h2>
          <div class="toolbar">
            <button type="button" class="btn" data-perf="findDup">Find duplicates</button>
            <span class="hint" style="margin:0;">Same company + position + job link (per user). Keeps the newest, soft-deletes the rest.</span>
          </div>
          <div data-perf="dupResult"></div>
        </div>`;
    }

    if (features.adminBrowse) {
      bodyHtml += `
        <h2 style="margin-top:8px;">Browse records</h2>
        <div class="toolbar">
          <div class="scope-toggle" role="group" aria-label="Browse scope" data-perf="browseScope">
            <button type="button" data-scope="user" class="active">User</button>
            <button type="button" data-scope="profile">Profile</button>
          </div>
          <input data-perf="q" type="search" placeholder="company, title, link" style="min-width:220px;" />
          <button type="button" class="btn-primary btn" data-perf="search">
            <svg class="ico ico-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
            Search
          </button>
        </div>
        <div data-perf="browse"></div>
        <div data-perf="detail"></div>`;
    } else {
      bodyHtml += `
        <div class="panel">
          <div class="panel-head">
            <h2 class="panel-title">Applications</h2>
            <span class="hint" data-perf="appHint"></span>
          </div>
          <div class="panel-body">
            <div class="toolbar tight" style="margin-bottom:12px;">
              <div class="search-row">
                <input data-perf="q" type="search" placeholder="company, title, link" />
                <button type="button" class="btn-primary btn" data-perf="search">
                  <svg class="ico ico-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>
                  Search
                </button>
              </div>
            </div>
            <div data-perf="browse" class="loading">Loading applications…</div>
          </div>
        </div>`;
    }

    root.innerHTML = bodyHtml;

    function fillPeriodSelect() {
      const sel = $('[data-perf="periods"]');
      if (!sel) return;
      const opts = TRK_PERIOD_OPTIONS[state.granularity] || TRK_PERIOD_OPTIONS.week;
      const allowed = new Set(opts.map(([v]) => v));
      if (!allowed.has(state.periods)) state.periods = trkDefaultPeriods(state.granularity);
      sel.innerHTML = opts
        .map(([v, label]) => `<option value="${v}"${v === state.periods ? " selected" : ""}>${BW.esc(label)}</option>`)
        .join("");
    }

    function populateSelects(users, profiles) {
      const uSel = $('[data-perf="user"]');
      const pSel = $('[data-perf="profile"]');
      if (uSel && users) {
        uSel.innerHTML = [`<option value="">All users</option>`]
          .concat(
            (users || []).map(
              (u) =>
                `<option value="${BW.esc(u.id)}"${u.id === state.userId ? " selected" : ""}>${BW.esc(u.email)}${u.name ? " — " + BW.esc(u.name) : ""}</option>`
            )
          )
          .join("");
        uSel.value = state.userId;
      }
      if (pSel && profiles) {
        const blank = isAdmin ? "All profiles" : "All assigned profiles";
        pSel.innerHTML = [`<option value="">${blank}</option>`]
          .concat(
            (profiles || []).map((p) => {
              const key = p.key;
              const label = isAdmin
                ? `${p.key}${p.name ? " — " + p.name : ""}`
                : p.name || p.key;
              return `<option value="${BW.esc(key)}"${key === state.profileKey ? " selected" : ""}>${BW.esc(label)}</option>`;
            })
          )
          .join("");
        pSel.value = state.profileKey;
      }
    }

    function setRangeHint(text) {
      const a = $('[data-perf="rangeHint"]');
      const b = $('[data-perf="rangeHintAdmin"]');
      if (a) a.textContent = text || "";
      if (b) b.textContent = text || "";
    }

    function renderUserMetricsTable(d) {
      const buckets = d.buckets || [];
      const apps = (d.series && d.series.applications) || [];
      const ivs = (d.series && d.series.interviews) || [];
      const tableEl = $('[data-perf="metricsTable"]');
      const chartEl = $('[data-perf="chart"]');
      if (!tableEl || !chartEl) return;

      tableEl.classList.toggle("hidden", state.view !== "table");
      chartEl.classList.toggle("hidden", state.view !== "chart");

      if (state.view === "chart") {
        chartEl.innerHTML = BW.barChartSvg(buckets, d.series || {}, d.max_applications, d.max_interviews, {
          variant: chartVariant,
        });
        return;
      }
      if (!buckets.length) {
        tableEl.innerHTML = '<p class="hint">No data for this window.</p>';
        return;
      }
      let rows = "";
      let seq = 1;
      for (let i = buckets.length - 1; i >= 0; i--) {
        const b = buckets[i];
        const label = typeof b === "string" ? b : b.label || b.key;
        rows += `<tr>
          <td class="mono">${seq++}</td>
          <td>${BW.esc(label)}</td>
          <td>${apps[i] || 0}</td>
          <td>${ivs[i] || 0}</td>
        </tr>`;
      }
      tableEl.innerHTML = `
        <table>
          <thead><tr><th>#</th><th>Period</th><th>Applications</th><th>Interviews</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>`;
    }

    async function loadMetrics() {
      const cards = $('[data-perf="cards"]');
      const chart = $('[data-perf="chart"]');
      const perUser = $('[data-perf="perUser"]');
      const metricsTable = $('[data-perf="metricsTable"]');
      const loadingTarget = filters.tableChartToggle
        ? state.view === "chart"
          ? chart
          : metricsTable
        : chart;
      if (loadingTarget) loadingTarget.innerHTML = '<div class="loading">Loading…</div>';

      const params = new URLSearchParams();
      params.set("tz_offset", String(BW.clientTzOffsetMinutes()));
      if (isAdmin) {
        params.set("granularity", state.granularity);
        params.set("periods", String(state.periods));
        if (state.userId) params.set("user_id", state.userId);
        if (state.profileKey) params.set("profile_key", state.profileKey);
      } else {
        params.set("window", state.window);
        if (state.profileKey) params.set("profile_key", state.profileKey);
      }

      let d;
      try {
        d = await api(endpoints.metrics + "?" + params.toString());
      } catch (e) {
        if (cards) cards.innerHTML = "";
        if (loadingTarget) loadingTarget.innerHTML = `<div class="err" style="padding:16px;">${BW.esc(e.message)}</div>`;
        return;
      }
      state.lastMetrics = d;
      populateSelects(d.users || null, d.profiles || null);

      if (!isAdmin && d.message) {
        if (cards) cards.innerHTML = "";
        setRangeHint("");
        if (metricsTable) {
          metricsTable.innerHTML = `<div class="placeholder-tab"><h2>No profiles</h2><p>${BW.esc(d.message)}</p></div>`;
        }
        if (chart) chart.innerHTML = "";
        const browse = $('[data-perf="browse"]');
        if (browse) browse.innerHTML = "";
        return;
      }

      const totals = d.totals_in_range || {};
      const undated = d.undated || {};
      const rangeText = isAdmin
        ? formatTrkRange(d.range_start, d.range_end)
        : (() => {
            const rs = BW.fmtCalendarDate(d.range_start);
            const re = BW.fmtCalendarDate(d.range_end);
            return rs && re ? `${rs} – ${re}` : "";
          })();

      if (isAdmin) {
        const granLabel = granularityWord(d.granularity || state.granularity);
        setRangeHint(rangeText ? `Date range: ${rangeText} (${granLabel})` : "");
        const appN = totals.applications || 0;
        const ivN = totals.interviews || 0;
        const ivSub =
          appN > 0 && ivN > 0 && appN >= ivN * 3
            ? `${rangeText ? rangeText + " · " : ""}scale independent of apps`
            : rangeText || null;
        const cardItems = [
          ["Apps in range", String(appN), rangeText || null],
          ["Interviews in range", String(ivN), ivSub],
          ["Active users", String(totals.active_users || 0), null],
          ["Undated apps", String(undated.applications || 0), null],
        ];
        if (undated.interviews) {
          cardItems.push(["Undated interviews", String(undated.interviews), null]);
        }
        cards.innerHTML = cardItems
          .map(
            ([l, n, sub]) =>
              `<div class="card"><div class="n">${BW.esc(n)}</div><div class="l">${BW.esc(l)}</div>${
                sub ? `<div style="color:var(--muted);font-size:13px;margin-top:6px;">${BW.esc(sub)}</div>` : ""
              }</div>`
          )
          .join("");
        const granLbl = granularityWord(d.granularity || state.granularity);
        chart.innerHTML =
          `<h2>Applications &amp; interviews · ${BW.esc(rangeText || granLbl)} (${BW.esc(granLbl)})</h2>` +
          BW.barChartSvg(d.buckets || [], d.series || {}, d.max_applications, d.max_interviews, {
            variant: chartVariant,
          });

        if (perUser) {
          const rows = d.per_user || [];
          perUser.innerHTML = `
            <h2>Per-user performance (${rows.length})${rangeText ? ` · ${BW.esc(rangeText)}` : ""}</h2>
            <table><thead><tr>
              <th style="width:3em;">#</th><th>User</th><th>Apps in range</th><th>Interviews in range</th><th>Total apps</th><th>Last applied</th><th>Actions</th>
            </tr></thead><tbody>${
              rows
                .map(
                  (r, i) => `<tr data-drill-user="${BW.esc(r.user_id)}" style="cursor:pointer" title="Click to filter by this user">
                <td style="color:var(--muted);">${i + 1}</td>
                <td>${BW.esc(r.email || r.user_id)}${r.name ? " — " + BW.esc(r.name) : ""}</td>
                <td>${r.applications_in_range}</td>
                <td>${r.interviews_in_range}</td>
                <td>${r.applications_total}</td>
                <td>${r.last_applied ? BW.fmtAppliedDate(r.last_applied) : "-"}</td>
                <td class="actions"><button class="link danger" data-del-user="${BW.esc(r.user_id)}" data-del-email="${BW.esc(r.email || r.user_id)}">Delete data</button></td>
              </tr>`
                )
                .join("") || `<tr><td colspan="7">No data for this window / filter</td></tr>`
            }</tbody></table>`;
          perUser.querySelectorAll("[data-drill-user]").forEach((tr) => {
            tr.addEventListener("click", () => drillUser(tr.getAttribute("data-drill-user")));
          });
          perUser.querySelectorAll("[data-del-user]").forEach((btn) => {
            btn.addEventListener("click", (e) => {
              e.stopPropagation();
              deleteClientData(btn.getAttribute("data-del-user"), btn.getAttribute("data-del-email"));
            });
          });
        }
      } else {
        setRangeHint(rangeText);
        cards.innerHTML = [
          ["Applications", totals.applications || 0],
          ["Interviews", totals.interviews || 0],
          ["Undated apps", undated.applications || 0],
          ["Undated interviews", undated.interviews || 0],
        ]
          .map(([l, n]) => `<div class="card"><div class="n">${n}</div><div class="l">${l}</div></div>`)
          .join("");
        renderUserMetricsTable(d);
      }
    }

    function drillUser(userId) {
      state.userId = userId;
      state.scope = "user";
      state.offset = 0;
      state.selectedAppId = "";
      const uSel = $('[data-perf="user"]');
      if (uSel) uSel.value = userId;
      const scopeEl = $('[data-perf="browseScope"]');
      if (scopeEl) {
        scopeEl.querySelectorAll("button").forEach((b) =>
          b.classList.toggle("active", b.dataset.scope === "user")
        );
      }
      loadMetrics();
      loadBrowse();
    }

    async function deleteClientData(userId, email) {
      if (
        !confirm(
          `Permanently delete ALL applications and interviews for ${email}?\n\nThis is a hard delete and cannot be undone.`
        )
      )
        return;
      try {
        const res = await api(endpoints.deleteUserData + encodeURIComponent(userId) + "/data", {
          method: "DELETE",
        });
        await Promise.all([loadMetrics(), loadBrowse()]);
        const dup = $('[data-perf="dupResult"]');
        if (dup) dup.innerHTML = "";
        const detail = $('[data-perf="detail"]');
        if (detail) detail.innerHTML = "";
        alert(
          `Deleted ${res.deleted_applications} application(s) and ${res.deleted_interviews} interview(s) for ${email}.`
        );
      } catch (e) {
        alert(e.message || String(e));
      }
    }

    async function loadBrowse() {
      const wrap = $('[data-perf="browse"]');
      const detail = $('[data-perf="detail"]');
      if (!wrap) return;
      wrap.innerHTML = '<div class="loading">Loading…</div>';
      if (detail) detail.innerHTML = "";

      const params = new URLSearchParams({
        limit: String(state.limit),
        offset: String(state.offset),
      });
      if (state.q) params.set("q", state.q);

      if (isAdmin) {
        if (state.scope === "user" && state.userId) params.set("user_id", state.userId);
        if (state.scope === "profile") {
          if (state.profileKey) params.set("profile_key", state.profileKey);
          else if (state.userId) params.set("user_id", state.userId);
        } else if (state.profileKey) {
          params.set("profile_key", state.profileKey);
        }
      } else {
        if (state.profileKey) params.set("profile_key", state.profileKey);
      }

      let d;
      try {
        d = await api(endpoints.applications + "?" + params.toString());
      } catch (e) {
        wrap.innerHTML = `<div class="err" style="padding:16px;">${BW.esc(e.message)}</div>`;
        return;
      }
      if (d.users || d.profiles) populateSelects(d.users || [], d.profiles || []);

      const apps = d.applications || [];
      const total = d.total || 0;
      const start = total === 0 ? 0 : state.offset + 1;
      const end = Math.min(state.offset + state.limit, total);

      if (!isAdmin) {
        const hint = $('[data-perf="appHint"]');
        if (hint) {
          hint.textContent = total ? `Showing ${start}–${end} of ${total}` : "No applications";
        }
        if (!apps.length) {
          wrap.innerHTML = '<p class="hint">No applications match these filters.</p>';
          return;
        }
        wrap.innerHTML = `
          <div class="pager">
            <button class="link" data-page="-1" ${state.offset === 0 ? "disabled" : ""}>Prev</button>
            <button class="link" data-page="1" ${end >= total ? "disabled" : ""}>Next</button>
          </div>
          <table>
            <thead><tr>
              <th>#</th><th>Editor</th><th>Profile</th><th>Company</th><th>Position</th>
              <th>Status</th><th>Applied</th><th>Interviews</th><th>Link</th>
            </tr></thead>
            <tbody>${apps
              .map(
                (a, i) => `<tr>
              <td class="mono">${state.offset + i + 1}</td>
              <td>${BW.esc(a.user_email || a.user_name || "—")}</td>
              <td>${BW.esc(a.profile_name || a.profile_key || "—")}</td>
              <td>${BW.esc(a.company_name || "—")}</td>
              <td>${BW.esc(a.position_title || "—")}</td>
              <td>${BW.esc(a.status || "—")}</td>
              <td>${BW.fmtAppliedDate(a.applied_date)}</td>
              <td>${a.interview_count || 0}</td>
              <td>${
                a.job_link
                  ? `<a class="link" href="${BW.esc(a.job_link)}" target="_blank" rel="noopener">open</a>`
                  : "—"
              }</td>
            </tr>`
              )
              .join("")}</tbody>
          </table>`;
        wrap.querySelectorAll("[data-page]").forEach((btn) => {
          btn.addEventListener("click", () => {
            if (btn.disabled) return;
            state.offset = Math.max(0, state.offset + parseInt(btn.getAttribute("data-page"), 10) * state.limit);
            loadBrowse();
          });
        });
        return;
      }

      // Admin browse
      const scopeHint =
        state.scope === "profile"
          ? state.profileKey
            ? `Profile: ${state.profileKey}`
            : "Profile scope — select a profile to narrow"
          : state.userId
            ? "Filtered by user"
            : "User scope — all users";
      const headCols =
        state.scope === "profile"
          ? `<th style="width:3em;">#</th><th>Last editor</th><th>Profile</th><th>Company</th><th>Position</th><th>Status</th><th>Applied</th><th>Interviews</th><th>Link</th>`
          : `<th style="width:3em;">#</th><th>User</th><th>Profile</th><th>Company</th><th>Position</th><th>Status</th><th>Applied</th><th>Interviews</th><th>Link</th>`;

      wrap.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; flex-wrap:wrap; gap:8px;">
          <span style="color:var(--muted); font-size:13px;">Showing ${start}–${end} of ${total} · ${BW.esc(scopeHint)}</span>
          <span class="actions">
            <button class="link" data-page="-1" ${state.offset === 0 ? "disabled" : ""}>Prev</button>
            <button class="link" data-page="1" ${end >= total ? "disabled" : ""}>Next</button>
          </span>
        </div>
        <table>
          <thead><tr>${headCols}</tr></thead>
          <tbody>${
            apps
              .map((a, i) => {
                const who = BW.esc(a.user_email || a.user_id || "-");
                const prof = BW.esc(a.profile_key || a.account || "-");
                const selected = a.id === state.selectedAppId ? " selected" : "";
                return `<tr class="trk-row${selected}" data-app-id="${BW.esc(a.id)}">
                  <td style="color:var(--muted);">${state.offset + i + 1}</td>
                  <td>${who}</td>
                  <td>${prof}</td>
                  <td>${BW.esc(a.company_name)}</td>
                  <td>${BW.esc(a.position_title)}</td>
                  <td>${BW.esc(a.status)}</td>
                  <td>${a.applied_date ? BW.fmtAppliedDate(a.applied_date) : "-"}</td>
                  <td>${a.interview_count || 0}</td>
                  <td>${
                    a.job_link
                      ? `<a href="${BW.esc(a.job_link)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">open</a>`
                      : "-"
                  }</td>
                </tr>`;
              })
              .join("") || `<tr><td colspan="9">No applications match these filters</td></tr>`
          }</tbody>
        </table>`;

      wrap.querySelectorAll("[data-page]").forEach((btn) => {
        btn.addEventListener("click", () => {
          if (btn.disabled) return;
          state.offset = Math.max(0, state.offset + parseInt(btn.getAttribute("data-page"), 10) * state.limit);
          state.selectedAppId = "";
          loadBrowse();
        });
      });
      wrap.querySelectorAll("[data-app-id]").forEach((tr) => {
        tr.addEventListener("click", () => openAppDetail(tr.getAttribute("data-app-id")));
      });
      if (state.selectedAppId) openAppDetail(state.selectedAppId);
    }

    async function openAppDetail(appId) {
      if (!features.browseDetail) return;
      state.selectedAppId = appId;
      root.querySelectorAll("tr.trk-row").forEach((tr) => {
        tr.classList.toggle("selected", tr.getAttribute("data-app-id") === appId);
      });
      const detail = $('[data-perf="detail"]');
      if (!detail) return;
      detail.innerHTML = `<div class="detail-drawer">Loading details…</div>`;
      let d;
      try {
        d = await api(endpoints.applicationDetail + encodeURIComponent(appId));
      } catch (e) {
        detail.innerHTML = `<div class="err">${BW.esc(e.message)}</div>`;
        return;
      }
      const a = d.application || {};
      const interviews = d.interviews || [];
      detail.innerHTML = `
        <div class="detail-drawer">
          <div style="display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; align-items:flex-start;">
            <div>
              <h2 style="margin:0;">${BW.esc(a.company_name)} — ${BW.esc(a.position_title)}</h2>
              <p class="hint" style="margin:4px 0 0;">${BW.esc(a.status || "")}${
                a.job_link
                  ? ` · <a href="${BW.esc(a.job_link)}" target="_blank" rel="noopener" style="color:var(--accent)">job link</a>`
                  : ""
              }</p>
            </div>
            <button class="link" data-perf="closeDetail">Close</button>
          </div>
          <div class="meta-grid">
            <div><div class="k">User</div><div class="v">${BW.esc(a.user_email || "-")}</div></div>
            <div><div class="k">Profile</div><div class="v">${BW.esc(a.profile_key || "-")}${
              a.profile_name ? " — " + BW.esc(a.profile_name) : ""
            }</div></div>
            <div><div class="k">Account label</div><div class="v">${BW.esc(a.account) || "-"}</div></div>
            <div><div class="k">Applied</div><div class="v">${a.applied_date ? BW.fmtAppliedDate(a.applied_date) : "-"}</div></div>
            <div><div class="k">Last modified</div><div class="v">${
              a.last_modified ? new Date(a.last_modified).toLocaleString() : "-"
            }</div></div>
            <div><div class="k">Resume</div><div class="v mono">${BW.esc(
              a.resume_file_path || a.resume_version || "-"
            )}</div></div>
          </div>
          <h2 style="margin:0 0 4px;">Notes</h2>
          <div class="notes">${a.notes ? BW.esc(a.notes) : "<span style='color:var(--muted)'>No notes</span>"}</div>
          <h2 style="margin:0 0 8px;">Interviews (${interviews.length})</h2>
          <table><thead><tr><th>Title</th><th>Start</th><th>Status</th><th>Type</th><th>Location</th></tr></thead>
          <tbody>${
            interviews
              .map(
                (iv) => `<tr>
              <td>${BW.esc(iv.title)}</td>
              <td>${iv.start_time_utc ? BW.fmtInterviewWallClock(iv.start_time_utc, iv.timezone) : "-"}</td>
              <td>${BW.esc(iv.status)}</td>
              <td>${BW.esc(iv.interview_type) || "-"}</td>
              <td>${BW.esc(iv.location) || "-"}</td>
            </tr>`
              )
              .join("") || `<tr><td colspan="5">No interviews linked to this application</td></tr>`
          }</tbody></table>
        </div>`;
      const closeBtn = detail.querySelector('[data-perf="closeDetail"]');
      if (closeBtn) {
        closeBtn.addEventListener("click", () => {
          state.selectedAppId = "";
          root.querySelectorAll("tr.trk-row.selected").forEach((tr) => tr.classList.remove("selected"));
          detail.innerHTML = "";
        });
      }
    }

    async function findDuplicates() {
      const out = $('[data-perf="dupResult"]');
      if (!out) return;
      out.innerHTML = "Scanning...";
      const params = new URLSearchParams();
      if (state.userId) params.set("user_id", state.userId);
      if (state.profileKey) params.set("account", state.profileKey);
      let d;
      try {
        d = await api(endpoints.duplicates + "?" + params.toString());
      } catch (e) {
        out.innerHTML = `<div class="err">${BW.esc(e.message)}</div>`;
        return;
      }
      const groups = d.groups || [];
      if (!groups.length) {
        out.innerHTML = `<p class="hint">No duplicates found for the current filter.</p>`;
        return;
      }
      const groupHtml = groups
        .map((g) => {
          const removeRows = g.remove
            .map(
              (r) => `<label style="display:flex; gap:8px; align-items:center; font-size:13px; padding:2px 0;">
            <input type="checkbox" class="dupChk" value="${BW.esc(r.id)}" checked />
            <span class="mono">${BW.esc(String(r.id).slice(0, 8))}</span>
            <span>${BW.esc(r.account) || "no account"}</span>
            <span style="color:var(--muted);">applied ${
              r.applied_date ? BW.fmtAppliedDate(r.applied_date) : "-"
            } · modified ${r.last_modified ? new Date(r.last_modified).toLocaleDateString() : "-"}</span>
          </label>`
            )
            .join("");
          return `<div style="border:1px solid var(--line); border-radius:10px; padding:12px; margin-bottom:10px; background:var(--panel);">
            <div style="font-weight:600;">${BW.esc(g.company_name)} — ${BW.esc(g.position_title)}</div>
            <div class="hint" style="margin:2px 0 8px;">${BW.esc(g.user_email || g.user_id)}${
              g.job_link
                ? ` · <a href="${BW.esc(g.job_link)}" target="_blank" rel="noopener" style="color:var(--accent)">link</a>`
                : ""
            }</div>
            <div style="color:var(--ok); font-size:14px; margin-bottom:6px;">Keep newest: <span class="mono">${BW.esc(
              String(g.keep.id).slice(0, 8)
            )}</span> (modified ${
              g.keep.last_modified ? new Date(g.keep.last_modified).toLocaleDateString() : "-"
            })</div>
            <div style="color:var(--bad); font-size:14px; margin-bottom:4px;">Remove:</div>
            ${removeRows}
          </div>`;
        })
        .join("");
      out.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; margin:6px 0 10px;">
          <span>${d.total_groups} duplicate group(s), ${d.total_removable} removable record(s)</span>
          <button class="primary" data-perf="delDup">Delete selected duplicates</button>
        </div>
        ${groupHtml}`;
      const delBtn = out.querySelector('[data-perf="delDup"]');
      if (delBtn) delBtn.addEventListener("click", deleteSelectedDuplicates);
    }

    async function deleteSelectedDuplicates() {
      const ids = Array.from(root.querySelectorAll(".dupChk:checked")).map((c) => c.value);
      if (!ids.length) {
        alert("No records selected.");
        return;
      }
      if (!confirm(`Soft-delete ${ids.length} duplicate record(s)? The deletion will sync to clients.`)) return;
      try {
        const res = await api(endpoints.resolveDuplicates, {
          method: "POST",
          body: JSON.stringify({ ids }),
        });
        await Promise.all([loadMetrics(), loadBrowse()]);
        await findDuplicates();
        alert(`Deleted ${res.deleted} duplicate record(s).`);
      } catch (e) {
        alert(e.message || String(e));
      }
    }

    async function refresh() {
      await loadMetrics();
      await loadBrowse();
    }

    // ---- bind controls ----
    if (filters.granularity) {
      fillPeriodSelect();
      $('[data-perf="gran"]').addEventListener("change", (e) => {
        state.granularity = e.target.value;
        state.periods = trkDefaultPeriods(state.granularity);
        fillPeriodSelect();
        loadMetrics();
      });
      $('[data-perf="periods"]').addEventListener("change", (e) => {
        state.periods = parseInt(e.target.value, 10) || trkDefaultPeriods(state.granularity);
        loadMetrics();
      });
    }
    const winEl = $('[data-perf="window"]');
    if (winEl) {
      winEl.querySelectorAll("button").forEach((b) => {
        b.addEventListener("click", () => {
          state.window = b.dataset.window;
          winEl.querySelectorAll("button").forEach((x) =>
            x.classList.toggle("active", x.dataset.window === state.window)
          );
          refresh();
        });
      });
    }
    const viewToggle = $('[data-perf="viewToggle"]');
    if (viewToggle) {
      viewToggle.querySelectorAll("button").forEach((b) => {
        b.addEventListener("click", () => {
          state.view = b.dataset.view === "chart" ? "chart" : "table";
          viewToggle.querySelectorAll("button").forEach((x) =>
            x.classList.toggle("active", x.dataset.view === state.view)
          );
          if (state.lastMetrics) renderUserMetricsTable(state.lastMetrics);
        });
      });
    }
    const userSel = $('[data-perf="user"]');
    if (userSel) {
      userSel.addEventListener("change", (e) => {
        state.userId = e.target.value;
        state.offset = 0;
        state.selectedAppId = "";
        refresh();
      });
    }
    const profileSel = $('[data-perf="profile"]');
    if (profileSel) {
      profileSel.addEventListener("change", (e) => {
        state.profileKey = e.target.value;
        state.offset = 0;
        state.selectedAppId = "";
        refresh();
      });
    }
    const browseScope = $('[data-perf="browseScope"]');
    if (browseScope) {
      browseScope.querySelectorAll("button").forEach((b) => {
        b.addEventListener("click", () => {
          state.scope = b.dataset.scope === "profile" ? "profile" : "user";
          state.offset = 0;
          state.selectedAppId = "";
          browseScope.querySelectorAll("button").forEach((x) =>
            x.classList.toggle("active", x.dataset.scope === state.scope)
          );
          loadBrowse();
        });
      });
    }
    const qInput = $('[data-perf="q"]');
    const searchBtn = $('[data-perf="search"]');
    function applySearch() {
      state.q = qInput ? qInput.value : "";
      state.offset = 0;
      state.selectedAppId = "";
      loadBrowse();
    }
    if (searchBtn) searchBtn.addEventListener("click", applySearch);
    if (qInput) {
      qInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") applySearch();
      });
    }
    const findDup = $('[data-perf="findDup"]');
    if (findDup) findDup.addEventListener("click", findDuplicates);

    const controller = {
      refresh,
      destroy() {
        root.innerHTML = "";
        root._wsPerformance = null;
      },
    };
    root._wsPerformance = controller;
    refresh();
    return controller;
  }

  BW.mountPerformance = mountPerformance;
})(window);
