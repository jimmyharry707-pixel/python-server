/**
 * Shared Work Plan UI for admin + user apps.
 * BaymaxWorkspace.mountWorkPlan(root, config)
 *
 * Board (first page): plan item cards only.
 * Item detail: edit fields + comments (plan owner and admin).
 */
(function (global) {
  "use strict";

  const BW = global.BaymaxWorkspace || (global.BaymaxWorkspace = {});

  const WEEK_CATEGORIES = [
    { key: "current_job", label: "Current job" },
    { key: "self_study", label: "Self-study" },
    { key: "work_plans", label: "Work plans" },
  ];

  const CAT_LABEL = Object.fromEntries(WEEK_CATEGORIES.map((c) => [c.key, c.label]));

  function isoDate(d) {
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }

  function quarterStart(d) {
    const m = Math.floor(d.getMonth() / 3) * 3;
    return new Date(d.getFullYear(), m, 1);
  }

  function monthStart(d) {
    return new Date(d.getFullYear(), d.getMonth(), 1);
  }

  function formatWeekLabel(weekStart) {
    const end = BW.addDays(weekStart, 6);
    const opts = { month: "short", day: "numeric" };
    const yOpts = { month: "short", day: "numeric", year: "numeric" };
    if (weekStart.getFullYear() !== end.getFullYear()) {
      return weekStart.toLocaleDateString(undefined, yOpts) + " – " + end.toLocaleDateString(undefined, yOpts);
    }
    return weekStart.toLocaleDateString(undefined, opts) + " – " + end.toLocaleDateString(undefined, yOpts);
  }

  function fmtWhen(iso) {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      });
    } catch (_) {
      return iso;
    }
  }

  function previewText(text, max) {
    const s = String(text || "").trim();
    if (!s) return "";
    if (s.length <= max) return s;
    return s.slice(0, max).trim() + "…";
  }

  function mountWorkPlan(root, config) {
    if (!root) throw new Error("mountWorkPlan: root required");
    const api = config.api;
    if (typeof api !== "function") throw new Error("mountWorkPlan: config.api required");

    const role = config.role || "member";
    const title = config.title || "Work plan";
    const subtitle =
      config.subtitle ||
      (role === "admin"
        ? "View weekly plans across the team, or open an item to comment."
        : "Browse your plan cards. Open one to edit or discuss.");

    const state = {
      mode: config.defaultMode || (role === "admin" ? "overview" : "detail"),
      view: "board", // board | item
      anchor: BW.startOfWeekSaturday(new Date()),
      userId: "",
      users: [],
      plan: null,
      overview: null,
      selectedId: null,
      selectedItem: null,
      comments: [],
      addingKey: null, // "week:current_job" | "month" | "quarter"
      busy: false,
    };

    function $(sel) {
      return root.querySelector(sel);
    }

    function weekStartIso() {
      return isoDate(state.anchor);
    }

    function derivedPeriods() {
      const ws = state.anchor;
      const ms = monthStart(ws);
      const qs = quarterStart(ws);
      const q = Math.floor(qs.getMonth() / 3) + 1;
      return {
        week_start: isoDate(ws),
        week_end: isoDate(BW.addDays(ws, 6)),
        month_start: isoDate(ms),
        quarter_start: isoDate(qs),
        month_label: ms.toLocaleDateString(undefined, { month: "long", year: "numeric" }),
        quarter_label: `Q${q} ${qs.getFullYear()}`,
      };
    }

    function canEdit() {
      if (role === "member" || role === "manager" || role === "user") return true;
      return state.mode === "detail" && !!state.userId;
    }

    function canComment() {
      return canEdit() || (role === "admin" && state.mode === "detail" && !!state.userId);
    }

    let toastTimer = null;
    function notify(message, kind) {
      const text = String(message || "").trim();
      if (!text) return;
      let el = document.getElementById("wp-toast");
      if (!el) {
        el = document.createElement("div");
        el.id = "wp-toast";
        el.setAttribute("role", "status");
        el.setAttribute("aria-live", "polite");
        document.body.appendChild(el);
      }
      el.className = "wp-toast " + (kind === "err" ? "err" : "ok");
      el.textContent = text;
      el.classList.add("show");
      if (toastTimer) clearTimeout(toastTimer);
      toastTimer = setTimeout(() => {
        el.classList.remove("show");
      }, 2800);
    }

    root.innerHTML = `
      <div class="page-head">
        <div>
          <h1 class="page-title">${BW.esc(title)}</h1>
          <p class="page-sub">${BW.esc(subtitle)}</p>
        </div>
        <span class="hint" data-wp="countHint"></span>
      </div>

      <div class="toolbar" data-wp="filters">
        ${role === "admin" ? `
        <div class="seg" data-wp="modeToggle">
          <button type="button" data-mode="overview" class="${state.mode === "overview" ? "active" : ""}">Overview</button>
          <button type="button" data-mode="detail" class="${state.mode === "detail" ? "active" : ""}">User detail</button>
        </div>
        <select data-wp="userFilter" class="${state.mode === "detail" ? "" : "hidden"}">
          <option value="">Select a user…</option>
        </select>` : ""}
      </div>

      <div class="toolbar tight" data-wp="calToolbar">
        <button class="btn-primary btn" type="button" data-wp="today">This week</button>
        <button type="button" class="btn btn-icon" data-wp="prev" title="Previous week" aria-label="Previous week">
          &#8592;
        </button>
        <button type="button" class="btn btn-icon" data-wp="next" title="Next week" aria-label="Next week">
          &#8594;
        </button>
        <span class="period-label" data-wp="periodLabel"></span>
      </div>
 

      <div data-wp="body"></div>
    `;

    function findItem(id) {
      const plan = state.plan;
      if (!plan || !id) return null;
      for (const list of [plan.week || [], plan.month || [], plan.quarter || []]) {
        const found = list.find((x) => x.id === id);
        if (found) return found;
      }
      return state.selectedItem && state.selectedItem.id === id ? state.selectedItem : null;
    }

    function renderCard(item) {
      const doneCls = item.done ? " done" : "";
      const cat = item.category ? CAT_LABEL[item.category] || item.category : "";
      const preview = previewText(item.contents, 120);
      const comments = item.comment_count || 0;
      return `
        <button type="button" class="wp-card${doneCls}" data-wp-open="${BW.esc(item.id)}">
          <div class="wp-card-top">
            <span class="wp-card-title">${BW.esc(item.title)}</span>
            ${item.done ? '<span class="wp-badge ok">Done</span>' : '<span class="wp-badge">Open</span>'}
          </div>
          ${cat ? `<div class="wp-card-meta">${BW.esc(cat)}</div>` : ""}
          ${preview ? `<div class="wp-card-preview">${BW.esc(preview)}</div>` : ""}
          <div class="wp-card-foot">
            <span>${comments} comment${comments === 1 ? "" : "s"}</span>
          </div>
        </button>`;
    }

    function renderAddForm(horizon, periodStart, category, addKey) {
      if (!canEdit()) return "";
      if (state.addingKey !== addKey) {
        return `<button type="button" class="btn wp-add-toggle" data-wp-add-toggle="${BW.esc(addKey)}">+ Add item</button>`;
      }
      const catAttr = category ? ` data-category="${BW.esc(category)}"` : "";
      return `
        <form class="wp-add" data-wp-add data-horizon="${BW.esc(horizon)}" data-period="${BW.esc(periodStart)}"${catAttr}>
          <input type="text" name="title" placeholder="Title" required maxlength="512" autocomplete="off" />
          <textarea name="contents" rows="3" maxlength="4000" placeholder="Notes (optional)"></textarea>
          <div class="wp-edit-actions">
            <button type="submit" class="btn btn-primary">Add</button>
            <button type="button" class="btn" data-wp-add-cancel>Cancel</button>
          </div>
        </form>`;
    }

    function renderCardSection(heading, items, horizon, periodStart, category, addKey) {
      const list = (items || []).filter((it) =>
        category ? it.category === category : true
      );
      const cards = list.length
        ? `<div class="wp-card-grid">${list.map(renderCard).join("")}</div>`
        : `<div class="wp-empty">No items yet</div>`;
      return `
        <section class="wp-cat">
          <div class="wp-cat-head">
            <h3 class="wp-cat-title">${BW.esc(heading)}</h3>
          </div>
          ${cards}
          ${renderAddForm(horizon, periodStart, category, addKey)}
        </section>`;
    }

    function renderBoard() {
      const periods = (state.plan && state.plan.periods) || derivedPeriods();
      const week = (state.plan && state.plan.week) || [];
      const month = (state.plan && state.plan.month) || [];
      const quarter = (state.plan && state.plan.quarter) || [];

      if (role === "admin" && !state.userId) {
        return `<div class="placeholder-tab"><h2>Select a user</h2><p>Pick someone from the dropdown to see their plan cards.</p></div>`;
      }

      const cats = WEEK_CATEGORIES.map((c) =>
        renderCardSection(
          c.label,
          week,
          "week",
          periods.week_start,
          c.key,
          "week:" + c.key
        )
      ).join("");

      return `
        <div class="ws-wp-layout">
          <div class="ws-wp-main panel">
            <div class="panel-head">
              <h2 class="panel-title">Weekly plan</h2>
              <span class="hint">${BW.esc(formatWeekLabel(state.anchor))}</span>
            </div>
            <div class="panel-body wp-week-body">${cats}</div>
          </div>
          <aside class="ws-wp-side">
            <div class="panel wp-side-panel">
              <div class="panel-head">
                <h2 class="panel-title">Monthly</h2>
                <span class="hint">${BW.esc(periods.month_label || "")}</span>
              </div>
              <div class="panel-body">
                ${renderCardSection("Items", month, "month", periods.month_start, null, "month")}
              </div>
            </div>
            <div class="panel wp-side-panel">
              <div class="panel-head">
                <h2 class="panel-title">3 months</h2>
                <span class="hint">${BW.esc(periods.quarter_label || "")}</span>
              </div>
              <div class="panel-body">
                ${renderCardSection("Items", quarter, "quarter", periods.quarter_start, null, "quarter")}
              </div>
            </div>
          </aside>
        </div>`;
    }

    function renderComments() {
      const list = state.comments || [];
      const thread = list.length
        ? list
            .map((c) => {
              const who = c.author_name || c.author_email || "User";
              const rolePill =
                c.author_role === "admin"
                  ? '<span class="wp-badge admin">Admin</span>'
                  : "";
              const canDel =
                canEdit() ||
                role === "admin" ||
                true; /* delete allowed for author via API; show for all, API enforces */
              return `
              <div class="wp-comment" data-wp-comment="${BW.esc(c.id)}">
                <div class="wp-comment-head">
                  <strong>${BW.esc(who)}</strong>
                  ${rolePill}
                  <span class="hint">${BW.esc(fmtWhen(c.created_at))}</span>
                  <button type="button" class="link danger wp-comment-del" data-wp-comment-del="${BW.esc(c.id)}">Delete</button>
                </div>
                <div class="wp-comment-body">${BW.esc(c.body)}</div>
              </div>`;
            })
            .join("")
        : `<div class="wp-empty">No comments yet</div>`;

      const form = canComment()
        ? `<form class="wp-comment-form" data-wp-comment-add>
            <textarea name="body" rows="3" maxlength="8000" placeholder="Add a comment…" required></textarea>
            <button type="submit" class="btn btn-primary">Post comment</button>
          </form>`
        : "";

      return `
        <section class="wp-comments panel">
          <div class="panel-head"><h2 class="panel-title">Comments</h2></div>
          <div class="panel-body">
            <div class="wp-comment-list">${thread}</div>
            ${form}
          </div>
        </section>`;
    }

    function renderItemDetail() {
      const item = state.selectedItem || findItem(state.selectedId);
      if (!item) {
        return `<div class="placeholder-tab"><h2>Item not found</h2><p><button type="button" class="btn" data-wp-back>Back to board</button></p></div>`;
      }
      const editable = canEdit();
      const cat = item.category ? CAT_LABEL[item.category] || item.category : "";
      const horizonLabel =
        item.horizon === "week"
          ? "Weekly"
          : item.horizon === "month"
            ? "Monthly"
            : "3 months";

      const editBlock = editable
        ? `<form class="wp-edit" data-wp-edit="${BW.esc(item.id)}">
            <label class="wp-field">
              <span class="k">Title</span>
              <input type="text" name="title" value="${BW.esc(item.title)}" required maxlength="512" />
            </label>
            <label class="wp-field">
              <span class="k">Notes</span>
              <textarea name="contents" rows="6" maxlength="4000" placeholder="Notes (optional)">${BW.esc(item.contents || "")}</textarea>
            </label>
            <label class="wp-check-inline">
              <input type="checkbox" name="done" ${item.done ? "checked" : ""} />
              <span>Mark as done</span>
            </label>
            <div class="wp-edit-actions">
              <button type="submit" class="btn btn-primary">Save</button>
              <button type="button" class="btn danger-outline" data-wp-del="${BW.esc(item.id)}">Delete</button>
            </div>
          </form>`
        : `<div class="wp-item-readonly">
            <h2 class="wp-item-title">${BW.esc(item.title)}</h2>
            ${item.contents ? `<div class="wp-item-contents">${BW.esc(item.contents)}</div>` : '<div class="hint">No notes</div>'}
            <div class="meta-grid" style="margin-top:12px">
              <div><div class="k">Status</div><div class="v">${item.done ? "Done" : "Open"}</div></div>
            </div>
          </div>`;

      return `
        <div class="wp-item-page">
          <div class="wp-item-toolbar">
            <button type="button" class="btn" data-wp-back>← Back to board</button>
            <span class="hint">${BW.esc(horizonLabel)}${cat ? " · " + BW.esc(cat) : ""}</span>
          </div>
          <div class="ws-wp-item-layout">
            <div class="panel">
              <div class="panel-head"><h2 class="panel-title">Plan item</h2></div>
              <div class="panel-body">${editBlock}</div>
            </div>
            ${renderComments()}
          </div>
        </div>`;
    }

    function categoryBreakdown(counts) {
      return WEEK_CATEGORIES.map((c) => {
        const n = (counts && counts[c.key]) || 0;
        return `<span class="wp-count-pill" title="${BW.esc(c.label)}">${BW.esc(c.label)}: ${n}</span>`;
      }).join(" ");
    }

    function renderOverview() {
      const rows = (state.overview && state.overview.users) || [];
      if (!rows.length) {
        return `<div class="placeholder-tab"><h2>No users</h2><p>Active users will appear here with their weekly plans.</p></div>`;
      }
      const body = rows
        .map((u) => {
          const c = u.counts || {};
          const preview = (u.items || [])
            .slice(0, 4)
            .map((it) => {
              const mark = it.done ? "✓ " : "";
              return `<li class="${it.done ? "done" : ""}">${mark}${BW.esc(it.title)}</li>`;
            })
            .join("");
          const more =
            (u.items || []).length > 4
              ? `<li class="hint">+${(u.items || []).length - 4} more</li>`
              : "";
          return `
          <tr class="wp-ov-row" data-wp-user="${BW.esc(u.user_id)}" tabindex="0" role="button">
            <td>
              <div class="wp-ov-user">${BW.esc(u.user_name || u.user_email)}</div>
              <div class="hint">${BW.esc(u.user_email)}</div>
            </td>
            <td>
              <div class="wp-ov-counts">${categoryBreakdown(c)}</div>
              <div class="hint">${c.done || 0}/${c.total || 0} done</div>
            </td>
            <td>
              <ul class="wp-ov-preview">${preview || '<li class="hint">No items this week</li>'}${more}</ul>
            </td>
          </tr>`;
        })
        .join("");

      return `
        <div class="panel">
          <div class="panel-head">
            <h2 class="panel-title">Weekly plans</h2>
            <span class="hint">${BW.esc(formatWeekLabel(state.anchor))}</span>
          </div>
          <div class="panel-body flush">
            <table class="wp-ov-table">
              <thead><tr><th>User</th><th>Categories</th><th>Items</th></tr></thead>
              <tbody>${body}</tbody>
            </table>
          </div>
        </div>`;
    }

    function paint() {
      $('[data-wp="periodLabel"]').textContent = formatWeekLabel(state.anchor);
      const body = $('[data-wp="body"]');
      const calToolbar = $('[data-wp="calToolbar"]');
      if (calToolbar) {
        calToolbar.classList.toggle("hidden", state.view === "item" && state.mode !== "overview");
      }

      if (state.mode === "overview") {
        body.innerHTML = renderOverview();
        const total = ((state.overview && state.overview.users) || []).reduce(
          (n, u) => n + ((u.counts && u.counts.total) || 0),
          0
        );
        $('[data-wp="countHint"]').textContent =
          total + " weekly item" + (total === 1 ? "" : "s");
      } else if (state.view === "item") {
        body.innerHTML = renderItemDetail();
        $('[data-wp="countHint"]').textContent = "";
      } else {
        body.innerHTML = renderBoard();
        const plan = state.plan;
        const n = plan
          ? (plan.week || []).length + (plan.month || []).length + (plan.quarter || []).length
          : 0;
        $('[data-wp="countHint"]').textContent = n + " item" + (n === 1 ? "" : "s");
      }
      bindBodyEvents();
    }

    async function openItem(id) {
      state.selectedId = id;
      state.view = "item";
      state.addingKey = null;
      try {
        const params = new URLSearchParams();
        // item endpoint is enough; also load comments
        state.selectedItem = await api("/work-plans/" + encodeURIComponent(id));
        state.comments = await api("/work-plans/" + encodeURIComponent(id) + "/comments");
      } catch (e) {
        state.selectedItem = findItem(id);
        state.comments = [];
        notify(e.message || "Failed to open item.", "err");
      }
      paint();
    }

    function backToBoard() {
      state.view = "board";
      state.selectedId = null;
      state.selectedItem = null;
      state.comments = [];
      refresh();
    }

    function bindBodyEvents() {
      root.querySelectorAll("[data-wp-open]").forEach((btn) => {
        btn.addEventListener("click", () => openItem(btn.getAttribute("data-wp-open")));
      });

      root.querySelectorAll("[data-wp-back]").forEach((btn) => {
        btn.addEventListener("click", () => backToBoard());
      });

      root.querySelectorAll("[data-wp-add-toggle]").forEach((btn) => {
        btn.addEventListener("click", () => {
          state.addingKey = btn.getAttribute("data-wp-add-toggle");
          paint();
          const form = root.querySelector("[data-wp-add]");
          const titleEl = form && form.querySelector('[name="title"]');
          if (titleEl) titleEl.focus();
        });
      });

      root.querySelectorAll("[data-wp-add-cancel]").forEach((btn) => {
        btn.addEventListener("click", () => {
          state.addingKey = null;
          paint();
        });
      });

      root.querySelectorAll("[data-wp-add]").forEach((form) => {
        form.addEventListener("submit", async (ev) => {
          ev.preventDefault();
          const titleEl = form.querySelector('[name="title"]');
          const contentsEl = form.querySelector('[name="contents"]');
          const title = (titleEl.value || "").trim();
          if (!title) return;
          const payload = {
            horizon: form.getAttribute("data-horizon"),
            period_start: form.getAttribute("data-period"),
            title,
            contents: (contentsEl.value || "").trim() || null,
          };
          const cat = form.getAttribute("data-category");
          if (cat) payload.category = cat;
          if (role === "admin" && state.userId) payload.user_id = state.userId;
          try {
            const created = await api("/work-plans/", {
              method: "POST",
              body: JSON.stringify(payload),
            });
            state.addingKey = null;
            await refresh();
            notify("Item added.", "ok");
            if (created && created.id) openItem(created.id);
          } catch (e) {
            notify(e.message || "Failed to add item.", "err");
          }
        });
      });

      root.querySelectorAll("[data-wp-edit]").forEach((form) => {
        form.addEventListener("submit", async (ev) => {
          ev.preventDefault();
          const id = form.getAttribute("data-wp-edit");
          const titleEl = form.querySelector('[name="title"]');
          const contentsEl = form.querySelector('[name="contents"]');
          const doneEl = form.querySelector('[name="done"]');
          const title = (titleEl.value || "").trim();
          if (!title) return;
          try {
            state.selectedItem = await api("/work-plans/" + encodeURIComponent(id), {
              method: "PATCH",
              body: JSON.stringify({
                title,
                contents: (contentsEl.value || "").trim() || null,
                done: !!(doneEl && doneEl.checked),
              }),
            });
            await refresh({ keepItem: true });
            paint();
            notify("Changes saved.", "ok");
          } catch (e) {
            notify(e.message || "Failed to save.", "err");
          }
        });
      });

      root.querySelectorAll("[data-wp-del]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const id = btn.getAttribute("data-wp-del");
          if (!confirm("Delete this item?")) return;
          try {
            await api("/work-plans/" + encodeURIComponent(id), { method: "DELETE" });
            notify("Item deleted.", "ok");
            backToBoard();
          } catch (e) {
            notify(e.message || "Failed to delete.", "err");
          }
        });
      });

      root.querySelectorAll("[data-wp-comment-add]").forEach((form) => {
        form.addEventListener("submit", async (ev) => {
          ev.preventDefault();
          const id = state.selectedId;
          const bodyEl = form.querySelector('[name="body"]');
          const body = (bodyEl.value || "").trim();
          if (!id || !body) return;
          try {
            await api("/work-plans/" + encodeURIComponent(id) + "/comments", {
              method: "POST",
              body: JSON.stringify({ body }),
            });
            state.comments = await api(
              "/work-plans/" + encodeURIComponent(id) + "/comments"
            );
            if (state.selectedItem) {
              state.selectedItem.comment_count = state.comments.length;
            }
            paint();
            notify("Comment posted.", "ok");
          } catch (e) {
            notify(e.message || "Failed to post comment.", "err");
          }
        });
      });

      root.querySelectorAll("[data-wp-comment-del]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const cid = btn.getAttribute("data-wp-comment-del");
          if (!confirm("Delete this comment?")) return;
          try {
            await api("/work-plans/comments/" + encodeURIComponent(cid), {
              method: "DELETE",
            });
            if (state.selectedId) {
              state.comments = await api(
                "/work-plans/" + encodeURIComponent(state.selectedId) + "/comments"
              );
              if (state.selectedItem) {
                state.selectedItem.comment_count = state.comments.length;
              }
            }
            paint();
            notify("Comment deleted.", "ok");
          } catch (e) {
            notify(e.message || "Failed to delete comment.", "err");
          }
        });
      });

      root.querySelectorAll("[data-wp-user]").forEach((row) => {
        const open = () => {
          state.userId = row.getAttribute("data-wp-user") || "";
          state.mode = "detail";
          state.view = "board";
          state.selectedId = null;
          state.selectedItem = null;
          state.comments = [];
          syncModeChrome();
          refresh();
        };
        row.addEventListener("click", open);
        row.addEventListener("keydown", (ev) => {
          if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            open();
          }
        });
      });
    }

    function syncModeChrome() {
      const modeToggle = $('[data-wp="modeToggle"]');
      if (modeToggle) {
        modeToggle.querySelectorAll("button").forEach((b) =>
          b.classList.toggle("active", b.dataset.mode === state.mode)
        );
      }
      const userSel = $('[data-wp="userFilter"]');
      if (userSel) {
        userSel.classList.toggle("hidden", state.mode !== "detail");
        userSel.value = state.userId || "";
      }
    }

    async function ensureUsers() {
      if (role !== "admin" || typeof config.loadUsers !== "function") return;
      if (state.users.length) return;
      try {
        state.users = ((await config.loadUsers()) || []).filter((u) => {
          const r = String(u.role || "").toLowerCase();
          return r === "member" || r === "user";
        });
      } catch (_) {
        state.users = [];
      }
      const userSel = $('[data-wp="userFilter"]');
      if (!userSel) return;
      const opts = ['<option value="">Select a user…</option>'].concat(
        state.users.map(
          (u) =>
            `<option value="${BW.esc(u.id)}">${BW.esc(u.name || u.email)} (${BW.esc(u.email)})</option>`
        )
      );
      userSel.innerHTML = opts.join("");
      userSel.value = state.userId || "";
    }

    async function refresh(opts) {
      if (state.busy) return;
      state.busy = true;
      const keepItem = opts && opts.keepItem;
      const body = $('[data-wp="body"]');
      try {
        await ensureUsers();
        syncModeChrome();
        if (state.mode === "overview") {
          state.overview = await api(
            "/work-plans/overview?week_start=" + encodeURIComponent(weekStartIso())
          );
          state.plan = null;
          state.view = "board";
        } else {
          const params = new URLSearchParams({ week_start: weekStartIso() });
          if (role === "admin" && state.userId) params.set("user_id", state.userId);
          if (role === "admin" && !state.userId) {
            state.plan = null;
          } else {
            state.plan = await api("/work-plans/?" + params.toString());
          }
          if (keepItem && state.selectedId) {
            const fresh = findItem(state.selectedId);
            if (fresh) state.selectedItem = Object.assign({}, state.selectedItem || {}, fresh);
          }
        }
        paint();
      } catch (e) {
        if (body) {
          body.innerHTML =
            '<div class="err" style="padding:16px;">' + BW.esc(e.message || String(e)) + "</div>";
        }
      } finally {
        state.busy = false;
      }
    }

    const modeToggle = $('[data-wp="modeToggle"]');
    if (modeToggle) {
      modeToggle.querySelectorAll("button").forEach((b) => {
        b.addEventListener("click", () => {
          state.mode = b.dataset.mode;
          state.view = "board";
          state.selectedId = null;
          state.selectedItem = null;
          state.comments = [];
          state.addingKey = null;
          syncModeChrome();
          refresh();
        });
      });
    }
    const userSel = $('[data-wp="userFilter"]');
    if (userSel) {
      userSel.addEventListener("change", () => {
        state.userId = userSel.value;
        state.mode = "detail";
        state.view = "board";
        state.selectedId = null;
        state.selectedItem = null;
        state.comments = [];
        syncModeChrome();
        refresh();
      });
    }
    $('[data-wp="today"]').addEventListener("click", () => {
      state.anchor = BW.startOfWeekSaturday(new Date());
      state.view = "board";
      state.selectedId = null;
      state.addingKey = null;
      refresh();
    });
    $('[data-wp="prev"]').addEventListener("click", () => {
      state.anchor = BW.addDays(state.anchor, -7);
      state.view = "board";
      state.selectedId = null;
      state.addingKey = null;
      refresh();
    });
    $('[data-wp="next"]').addEventListener("click", () => {
      state.anchor = BW.addDays(state.anchor, 7);
      state.view = "board";
      state.selectedId = null;
      state.addingKey = null;
      refresh();
    });

    refresh();

    return {
      refresh,
      destroy() {
        if (toastTimer) clearTimeout(toastTimer);
        const toast = document.getElementById("wp-toast");
        if (toast) toast.remove();
        root.innerHTML = "";
      },
    };
  }

  BW.mountWorkPlan = mountWorkPlan;
})(window);
