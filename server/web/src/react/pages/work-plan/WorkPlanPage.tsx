import { useEffect, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Row } from "../../api";
import { useMe } from "../../auth";
import {
  arr,
  Confirm,
  ErrorBox,
  Loading,
  Modal,
  PageHeader,
  staff,
  useNotice,
  when,
} from "../../ui";
import { UserIdentity, userPerson } from "../../components/Avatar";

const categories = [
  { key: "current_job", label: "Current job" },
  { key: "self_study", label: "Self-study" },
  { key: "work_plans", label: "Work plans" },
];
type NoticeTone = "success" | "danger" | "secondary" | "error" | "warning" | "info";

export function WorkPlanPage() {
  const me = useMe();
  const isStaff = staff(me.role);
  // Work plan reviewers (members an admin picked) see the team and comment,
  // but only staff can change someone else's plan.
  const canSeeTeam = isStaff || Boolean(me.can_review_work_plans);
  const queryClient = useQueryClient();
  const notice = useNotice();
  const [week, setWeek] = useState(saturday());
  const [personId, setPersonId] = useState("");
  const [adding, setAdding] = useState<{ horizon: string; category?: string } | null>(null);
  const [selected, setSelected] = useState<Row | null>(null);
  const [deleting, setDeleting] = useState<Row | null>(null);
  const [openingId, setOpeningId] = useState("");
  const [view, setView] = useState<"cards" | "document">(() =>
    localStorage.getItem("wp-view") === "cards" ? "cards" : "document",
  );
  useEffect(() => {
    localStorage.setItem("wp-view", view);
  }, [view]);

  const users = useQuery({ queryKey: ["users"], queryFn: api.users, enabled: isStaff });
  const overview = useQuery({
    queryKey: ["workplan-overview", week],
    queryFn: () => api.workOverview(week),
    // Reviewers can't read the admin users list, so for them the overview
    // also feeds the member picker and stays loaded while a plan is open.
    enabled: canSeeTeam && (!personId || !isStaff),
  });
  const plan = useQuery({
    queryKey: ["workplan", week, personId || me.id],
    queryFn: () => api.workPlan({ week_start: week, user_id: personId || undefined }),
    enabled: !canSeeTeam || Boolean(personId),
  });
  const members = isStaff
    ? (users.data || []).filter((user) =>
        ["member", "user"].includes(String(user.role).toLowerCase()),
      )
    : arr(overview.data?.users).map((row) => ({
        id: row.user_id,
        name: row.user_name,
        email: row.user_email,
        avatar_url: row.avatar_url,
        role: "member",
      }));
  const readOnly = !isStaff && Boolean(personId) && personId !== me.id;
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["workplan"] });
    queryClient.invalidateQueries({ queryKey: ["workplan-overview"] });
  };
  const shift = (days: number) => {
    const date = new Date(`${week}T12:00:00`);
    date.setDate(date.getDate() + days);
    setWeek(saturday(date));
  };
  const loading = canSeeTeam && !personId ? overview.isLoading : plan.isLoading;
  const error = canSeeTeam && !personId ? overview.error : plan.error;

  return (
    <>
      <PageHeader text="Set weekly, monthly, and quarterly goals and collaborate through comments." />
      <div className="card mb-3">
        <div className="card-body stage-wp-filters d-flex flex-wrap align-items-center justify-content-between gap-3">
          <div className="d-flex align-items-center gap-2">
            {canSeeTeam && (
              <select
                className="form-select form-select-sm"
                value={personId}
                onChange={(event) => {
                  setPersonId(event.target.value);
                  setSelected(null);
                }}
              >
                <option value="">All members</option>
                {!isStaff && <option value={me.id}>My plan</option>}
                {members
                  .filter((user) => isStaff || user.id !== me.id)
                  .map((user) => (
                    <option key={user.id} value={user.id}>
                      {user.name || user.email}
                    </option>
                  ))}
              </select>
            )}
          </div>
          {(!canSeeTeam || Boolean(personId)) && (
            <div className="btn-group" role="group" aria-label="Plan view style">
              <button
                type="button"
                className={`btn btn-sm ${view === "cards" ? "btn-primary" : "btn-outline-secondary"}`}
                aria-pressed={view === "cards"}
                onClick={() => setView("cards")}
              >
                <i className="ti ti-layout-cards me-1" aria-hidden="true" />
                Cards
              </button>
              <button
                type="button"
                className={`btn btn-sm ${view === "document" ? "btn-primary" : "btn-outline-secondary"}`}
                aria-pressed={view === "document"}
                onClick={() => setView("document")}
              >
                <i className="ti ti-file-text me-1" aria-hidden="true" />
                Document
              </button>
            </div>
          )}
          <div className="stage-wp-week-nav d-flex flex-wrap align-items-center gap-2">
            <button
              className={`btn btn-sm ${week === saturday() ? "btn-primary" : "btn-outline-secondary"}`}
              onClick={() => setWeek(saturday())}
              aria-current={week === saturday() ? "date" : undefined}
            >
              This week
            </button>
            <div className="btn-group">
              <button
                className="btn btn-outline-secondary btn-sm"
                onClick={() => shift(-7)}
                aria-label="Previous week"
              >
                <i className="ti ti-chevron-left" />
                <span className="d-none d-md-inline ms-1">Previous</span>
              </button>
              <button
                className="btn btn-outline-secondary btn-sm"
                onClick={() => shift(7)}
                aria-label="Next week"
              >
                <span className="d-none d-md-inline me-1">Next</span>
                <i className="ti ti-chevron-right" />
              </button>
            </div>
            <span className="stage-wp-period-label text-secondary">{weekLabel(week)}</span>
          </div>
        </div>
      </div>
      {loading ? (
        <Loading />
      ) : error ? (
        <ErrorBox error={error} />
      ) : canSeeTeam && !personId ? (
        <TeamOverview data={overview.data} onSelect={setPersonId} />
      ) : view === "document" ? (
        <PaperPlanView
          data={plan.data || {}}
          week={week}
          owner={members.find((user) => user.id === personId)}
          me={me}
          readOnly={readOnly}
          onBack={canSeeTeam ? () => setPersonId("") : undefined}
          onOpen={async (item) => {
            setOpeningId(item.id);
            try {
              setSelected(await api.workItem(item.id));
            } catch (openError: any) {
              notice.show(openError?.message || "Could not open goal", "danger");
            } finally {
              setOpeningId("");
            }
          }}
          onChange={refresh}
          onNotice={(message, tone = "success") => notice.show(message, tone)}
        />
      ) : (
        <PlanBoard
          data={plan.data || {}}
          week={week}
          owner={members.find((user) => user.id === personId)}
          readOnly={readOnly}
          onBack={canSeeTeam ? () => setPersonId("") : undefined}
          onAdd={(horizon, category) => setAdding({ horizon, category })}
          openingId={openingId}
          onOpen={async (item) => {
            setOpeningId(item.id);
            try {
              setSelected(await api.workItem(item.id));
            } catch (openError: any) {
              notice.show(openError?.message || "Could not open goal", "danger");
            } finally {
              setOpeningId("");
            }
          }}
          onChange={refresh}
          onNotice={(message, tone = "success") => notice.show(message, tone)}
        />
      )}
      {adding && (
        <AddItem
          target={adding}
          periods={plan.data?.periods || {}}
          week={week}
          userId={personId}
          onClose={() => setAdding(null)}
          onSave={() => {
            setAdding(null);
            refresh();
            notice.show("Item created");
          }}
        />
      )}
      {selected && (
        <ItemDetail
          item={selected}
          meId={me.id}
          readOnly={!isStaff && selected.user_id !== me.id}
          onClose={() => setSelected(null)}
          onChange={(updated) => {
            if (updated) setSelected(updated);
            refresh();
          }}
          onDelete={() => setDeleting(selected)}
          onNotice={(message, tone = "success") => notice.show(message, tone)}
        />
      )}
      {deleting && (
        <Confirm
          text={`Delete “${deleting.title}”?`}
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            await api.deleteWorkItem(deleting.id);
            setDeleting(null);
            setSelected(null);
            refresh();
            notice.show("Item deleted");
          }}
        />
      )}
      {notice.node}
    </>
  );
}

function TeamOverview({
  data,
  onSelect,
}: {
  data: Row | undefined;
  onSelect: (id: string) => void;
}) {
  const rows = arr(data?.users).sort((a, b) => num(b.counts?.total) - num(a.counts?.total));
  if (!rows.length)
    return (
      <div className="card">
        <div className="card-body text-secondary">
          No active members yet. They will appear here with their weekly plans.
        </div>
      </div>
    );
  const withPlan = rows.filter((row) => num(row.counts?.total) > 0).length;
  return (
    <div className="card">
      <div className="card-header stage-wp-summary-head">
        <div>
          <h3 className="card-title mb-0">Team week</h3>
          <div className="text-secondary small mt-1">
            {withPlan}/{rows.length} with a plan
          </div>
        </div>
      </div>
      <div className="table-responsive">
        <table className="table table-vcenter card-table table-hover mb-0 stage-wp-summary-table">
          <thead>
            <tr>
              <th>Member</th>
              <th className="stage-wp-col-progress">Progress</th>
              <th className="stage-wp-col-items">This week</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                className={`stage-wp-summary-row${num(row.counts?.total) ? "" : " stage-wp-row-empty"}`}
                key={row.user_id}
                tabIndex={0}
                role="button"
                onClick={() => onSelect(row.user_id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelect(row.user_id);
                  }
                }}
              >
                <td>
                  <UserIdentity person={userPerson(row)} />
                </td>
                <td>
                  <Progress counts={row.counts} />
                  <CategoryCounts counts={row.counts} />
                </td>
                <td>
                  <ItemPreview items={arr(row.items)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Progress({ counts }: { counts: Row }) {
  const total = num(counts?.total),
    done = num(counts?.done),
    percent = total ? Math.round((done / total) * 100) : 0;
  return total ? (
    <div className="stage-wp-progress">
      <div
        className="stage-wp-progress-track"
        role="progressbar"
        aria-label="Completed weekly goals"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
        aria-valuetext={`${done} of ${total} goals completed`}
      >
        <div className="stage-wp-progress-fill" style={{ width: `${percent}%` }} />
      </div>
      <span className="stage-wp-progress-label">
        {done}/{total}
      </span>
    </div>
  ) : (
    <span className="text-secondary small">—</span>
  );
}
function CategoryCounts({ counts }: { counts: Row }) {
  return (
    <div className="stage-wp-cat-counts">
      {categories.map((category, index) => (
        <span key={category.key}>
          <span className={`stage-wp-cat-count${num(counts?.[category.key]) ? "" : " is-zero"}`}>
            {category.label.split(/[- ]/)[0]} <strong>{num(counts?.[category.key])}</strong>
          </span>
          {index < categories.length - 1 && <span className="stage-wp-cat-sep"> · </span>}
        </span>
      ))}
    </div>
  );
}
function ItemPreview({ items }: { items: Row[] }) {
  if (!items.length) return <span className="stage-wp-empty-hint">No plan this week</span>;
  return (
    <div className="stage-wp-preview">
      {items.slice(0, 3).map((item) => (
        <span className={`stage-wp-preview-item${item.done ? " stage-wp-done" : ""}`} key={item.id}>
          <span className="stage-wp-preview-cat">
            {categories.find((category) => category.key === item.category)?.label || "Plan"}
          </span>
          <span className="stage-wp-preview-title">{item.title}</span>
        </span>
      ))}
      {items.length > 3 && <span className="stage-wp-preview-more">+{items.length - 3} more</span>}
    </div>
  );
}

function PlanBoard({
  data,
  week,
  owner,
  readOnly,
  onBack,
  onAdd,
  onOpen,
  onChange,
  onNotice,
  openingId,
}: {
  data: Row;
  week: string;
  owner?: Row;
  readOnly: boolean;
  onBack?: () => void;
  onAdd: (horizon: string, category?: string) => void;
  onOpen: (item: Row) => void;
  onChange: () => void;
  onNotice: (message: string, tone?: NoticeTone) => void;
  openingId: string;
}) {
  const periods = data.periods || {};
  return (
    <>
      {onBack && (
        <div className="stage-wp-board-nav mb-3">
          <button className="btn btn-ghost-secondary" onClick={onBack}>
            <i className="ti ti-arrow-left me-1" />
            Team week
          </button>
          <span className="stage-wp-board-nav-sep text-secondary">/</span>
          {owner ? (
            <UserIdentity person={userPerson(owner)} />
          ) : (
            <span className="stage-wp-board-nav-member fw-medium">Member</span>
          )}
        </div>
      )}
      <div className="stage-wp-board">
        <div className="card stage-wp-week-card">
          <div className="card-header d-flex justify-content-between">
            <h3 className="card-title mb-0">Weekly goals</h3>
            <span className="text-secondary small">{weekLabel(week)}</span>
          </div>
          <div className="card-body">
            <div className="stage-wp-week-cols">
              {categories.map((category) => (
                <PlanSection
                  key={category.key}
                  title={category.label}
                  items={arr(data.week).filter((item) => item.category === category.key)}
                  readOnly={readOnly}
                  onAdd={() => onAdd("week", category.key)}
                  onOpen={onOpen}
                  onChange={onChange}
                  onNotice={onNotice}
                  openingId={openingId}
                />
              ))}
            </div>
          </div>
        </div>
        <div className="stage-wp-horizons">
          <div className="card">
            <div className="card-header">
              <div>
                <h3 className="card-title mb-0">Monthly goals</h3>
                <span className="text-secondary small">{periods.month_label}</span>
              </div>
            </div>
            <div className="card-body">
              <PlanSection
                title="Goals"
                items={arr(data.month)}
                readOnly={readOnly}
                onAdd={() => onAdd("month")}
                onOpen={onOpen}
                onChange={onChange}
                onNotice={onNotice}
                openingId={openingId}
              />
            </div>
          </div>
          <div className="card">
            <div className="card-header">
              <div>
                <h3 className="card-title mb-0">Quarterly goals</h3>
                <span className="text-secondary small">{periods.quarter_label}</span>
              </div>
            </div>
            <div className="card-body">
              <PlanSection
                title="Goals"
                items={arr(data.quarter)}
                readOnly={readOnly}
                onAdd={() => onAdd("quarter")}
                onOpen={onOpen}
                onChange={onChange}
                onNotice={onNotice}
                openingId={openingId}
              />
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
function PlanSection({
  title,
  items,
  readOnly,
  onAdd,
  onOpen,
  onChange,
  onNotice,
  openingId,
}: {
  title: string;
  items: Row[];
  readOnly: boolean;
  onAdd: () => void;
  onOpen: (item: Row) => void;
  onChange: () => void;
  onNotice: (message: string, tone?: NoticeTone) => void;
  openingId: string;
}) {
  return (
    <div className="stage-wp-section">
      <div className="stage-wp-section-head">
        <h4 className="stage-wp-section-title">{title}</h4>
        <span className="badge bg-secondary-lt">{items.length}</span>
      </div>
      {items.length ? (
        <div className="stage-wp-item-list">
          {items.map((item) => (
            <PlanItem
              item={item}
              key={item.id}
              opening={openingId === item.id}
              readOnly={readOnly}
              onOpen={() => onOpen(item)}
              onChange={onChange}
              onNotice={onNotice}
            />
          ))}
        </div>
      ) : readOnly ? (
        <div className="text-secondary small">No goals.</div>
      ) : (
        <button className="stage-wp-empty-add" onClick={onAdd}>
          <i className="ti ti-plus" />
          Add your first goal
        </button>
      )}
      {items.length > 0 && !readOnly && (
        <button className="btn btn-ghost-secondary btn-sm w-100 mt-2" onClick={onAdd}>
          <i className="ti ti-plus me-1" />
          Add goal
        </button>
      )}
    </div>
  );
}

function PlanItem({
  item,
  opening,
  readOnly,
  onOpen,
  onChange,
  onNotice,
}: {
  item: Row;
  opening: boolean;
  readOnly: boolean;
  onOpen: () => void;
  onChange: () => void;
  onNotice: (message: string, tone?: NoticeTone) => void;
}) {
  const [completing, setCompleting] = useState(false);
  const [movingWeek, setMovingWeek] = useState(false);
  const comments = arr(item.comments);
  const visibleComments = comments.slice(-3);

  const toggleDone = async () => {
    if (completing) return;
    setCompleting(true);
    try {
      await api.updateWorkItem(item.id, { done: !item.done });
      onChange();
      onNotice(item.done ? "Goal reopened" : "Goal completed");
    } catch (error: any) {
      onNotice(error?.message || "Could not update goal", "danger");
    } finally {
      setCompleting(false);
    }
  };
  const sendToNextWeek = async () => {
    if (movingWeek) return;
    setMovingWeek(true);
    try {
      await api.moveWorkItemNextWeek(item.id);
      onChange();
      onNotice("Sent to next week");
    } catch (error: any) {
      onNotice(error?.message || "Could not send to next week", "danger");
    } finally {
      setMovingWeek(false);
    }
  };

  return (
    <article className={`stage-wp-item${item.done ? " stage-wp-item-done" : ""}`}>
      <div className="stage-wp-item-top">
        <label
          className="stage-wp-item-check"
          title={readOnly ? undefined : item.done ? "Reopen goal" : "Complete goal"}
        >
          <input
            className="form-check-input"
            type="checkbox"
            checked={Boolean(item.done)}
            disabled={completing || readOnly}
            onChange={toggleDone}
            aria-label={`${item.done ? "Reopen" : "Complete"} ${item.title}`}
          />
        </label>
        <button
          type="button"
          className="stage-wp-item-open"
          onClick={onOpen}
          disabled={opening}
          aria-label={`Open details for ${item.title}`}
        >
          <span className="stage-wp-item-title">{item.title}</span>
          {item.contents && <span className="stage-wp-item-preview">{preview(item.contents)}</span>}
          <span className="stage-wp-item-foot">
            {opening ? (
              <>
                <span className="spinner-border spinner-border-sm me-1" aria-hidden="true" />
                Opening…
              </>
            ) : (
              <>
                <i className="ti ti-message-circle me-1" aria-hidden="true" />
                {num(item.comment_count)} comments
              </>
            )}
          </span>
        </button>
        {item.horizon === "week" && !item.done && !readOnly && (
          <button
            type="button"
            className="stage-wp-item-next"
            onClick={sendToNextWeek}
            disabled={movingWeek}
            title="Send to next week"
            aria-label={`Send ${item.title} to next week`}
          >
            {movingWeek ? (
              <span className="spinner-border spinner-border-sm" aria-hidden="true" />
            ) : (
              <i className="ti ti-arrow-right" aria-hidden="true" />
            )}
          </button>
        )}
      </div>
      {visibleComments.length > 0 && (
        <div className="stage-wp-card-comments" aria-label={`Comments on ${item.title}`}>
          {comments.length > visibleComments.length && (
            <button type="button" className="stage-wp-card-comments-more" onClick={onOpen}>
              {comments.length - visibleComments.length} earlier comment
              {comments.length - visibleComments.length === 1 ? "" : "s"}
            </button>
          )}
          {visibleComments.map((comment) => (
            <div className="stage-wp-card-comment" key={comment.id}>
              <div className="stage-wp-card-comment-meta">
                <strong>{comment.author_name || comment.author_email || "Unknown user"}</strong>
                <span>{when(comment.created_at)}</span>
              </div>
              <div className="stage-wp-comment-body">{comment.body}</div>
            </div>
          ))}
        </div>
      )}
    </article>
  );
}

function PaperPlanView({
  data,
  week,
  owner,
  me,
  readOnly,
  onBack,
  onOpen,
  onChange,
  onNotice,
}: {
  data: Row;
  week: string;
  owner?: Row;
  me: Row;
  readOnly: boolean;
  onBack?: () => void;
  onOpen: (item: Row) => void;
  onChange: () => void;
  onNotice: (message: string, tone?: NoticeTone) => void;
}) {
  const periods = data.periods || {};
  const ownerName = (owner ? owner.name || owner.email : me.name || me.email) || "Unknown";
  return (
    <>
      {onBack && (
        <div className="stage-wp-board-nav mb-3">
          <button className="btn btn-ghost-secondary" onClick={onBack}>
            <i className="ti ti-arrow-left me-1" />
            Team week
          </button>
          <span className="stage-wp-board-nav-sep text-secondary">/</span>
          {owner ? (
            <UserIdentity person={userPerson(owner)} />
          ) : (
            <span className="stage-wp-board-nav-member fw-medium">Member</span>
          )}
        </div>
      )}
      <div className="stage-wp-paper">
        <div className="stage-wp-paper-head">
          <h2 className="stage-wp-paper-heading">{ownerName} — Work Plan</h2>
          <div className="stage-wp-paper-range">
            Week {weekIndexOf(week)} &middot; {weekLabel(week)}
          </div>
        </div>

        <section className="stage-wp-paper-section">
          <h3 className="stage-wp-paper-title">Weekly goals</h3>
          {categories.map((category) => (
            <div key={category.key} className="stage-wp-paper-group">
              <h4 className="stage-wp-paper-subtitle">{category.label}</h4>
              <PaperList
                items={arr(data.week).filter((item) => item.category === category.key)}
                readOnly={readOnly}
                onOpen={onOpen}
                onChange={onChange}
                onNotice={onNotice}
              />
            </div>
          ))}
        </section>

        <section className="stage-wp-paper-section">
          <h3 className="stage-wp-paper-title">
            Monthly goals <span className="stage-wp-paper-range">{periods.month_label}</span>
          </h3>
          <PaperList
            items={arr(data.month)}
            readOnly={readOnly}
            onOpen={onOpen}
            onChange={onChange}
            onNotice={onNotice}
          />
        </section>

        <section className="stage-wp-paper-section">
          <h3 className="stage-wp-paper-title">
            Quarterly goals <span className="stage-wp-paper-range">{periods.quarter_label}</span>
          </h3>
          <PaperList
            items={arr(data.quarter)}
            readOnly={readOnly}
            onOpen={onOpen}
            onChange={onChange}
            onNotice={onNotice}
          />
        </section>
      </div>
    </>
  );
}
function PaperList({
  items,
  readOnly,
  onOpen,
  onChange,
  onNotice,
}: {
  items: Row[];
  readOnly: boolean;
  onOpen: (item: Row) => void;
  onChange: () => void;
  onNotice: (message: string, tone?: NoticeTone) => void;
}) {
  if (!items.length) return <p className="stage-wp-paper-empty">No goals.</p>;
  return (
    <ul className="stage-wp-paper-list">
      {items.map((item) => (
        <PaperRow
          key={item.id}
          item={item}
          readOnly={readOnly}
          onOpen={onOpen}
          onChange={onChange}
          onNotice={onNotice}
        />
      ))}
    </ul>
  );
}
function PaperRow({
  item,
  readOnly,
  onOpen,
  onChange,
  onNotice,
}: {
  item: Row;
  readOnly: boolean;
  onOpen: (item: Row) => void;
  onChange: () => void;
  onNotice: (message: string, tone?: NoticeTone) => void;
}) {
  const [busy, setBusy] = useState(false);
  const comments = arr(item.comments);
  const toggleDone = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await api.updateWorkItem(item.id, { done: !item.done });
      onChange();
    } catch (error: any) {
      onNotice(error?.message || "Could not update goal", "danger");
    } finally {
      setBusy(false);
    }
  };
  const sendToNextWeek = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await api.moveWorkItemNextWeek(item.id);
      onChange();
      onNotice("Sent to next week");
    } catch (error: any) {
      onNotice(error?.message || "Could not send to next week", "danger");
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className={`stage-wp-paper-row${item.done ? " stage-wp-paper-row-done" : ""}`}>
      <div className="stage-wp-paper-row-head">
        <label
          className="stage-wp-paper-check"
          title={readOnly ? undefined : item.done ? "Reopen goal" : "Complete goal"}
        >
          <input
            type="checkbox"
            checked={Boolean(item.done)}
            disabled={busy || readOnly}
            onChange={toggleDone}
            aria-label={`${item.done ? "Reopen" : "Complete"} ${item.title}`}
          />
        </label>
        <button type="button" className="stage-wp-paper-text" onClick={() => onOpen(item)}>
          <span className="stage-wp-paper-item-title">{item.title}</span>
        </button>
        {item.horizon === "week" && !item.done && !readOnly && (
          <button
            type="button"
            className="stage-wp-paper-next"
            onClick={sendToNextWeek}
            disabled={busy}
            title="Send to next week"
            aria-label={`Send ${item.title} to next week`}
          >
            <i className="ti ti-arrow-right" aria-hidden="true" />
          </button>
        )}
      </div>
      {item.contents && (
        <p className="stage-wp-paper-item-contents stage-wp-prewrap">{item.contents}</p>
      )}
      {comments.length > 0 && (
        <div className="stage-wp-paper-comments-block">
          <div className="stage-wp-paper-comments-label">
            Comments <span className="text-secondary">({comments.length})</span>
          </div>
          {comments.map((comment) => (
            <div className="stage-wp-paper-comment" key={comment.id}>
              <div className="stage-wp-paper-comment-meta">
                <strong>{comment.author_name || comment.author_email || "Unknown user"}</strong>
                <span>{when(comment.created_at)}</span>
              </div>
              <div className="stage-wp-paper-comment-body stage-wp-prewrap">{comment.body}</div>
            </div>
          ))}
        </div>
      )}
    </li>
  );
}

function AddItem({
  target,
  periods,
  week,
  userId,
  onClose,
  onSave,
}: {
  target: { horizon: string; category?: string };
  periods: Row;
  week: string;
  userId: string;
  onClose: () => void;
  onSave: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      await api.createWorkItem({
        title: form.get("title"),
        contents: form.get("contents"),
        horizon: target.horizon,
        category: target.category || null,
        period_start:
          target.horizon === "month"
            ? periods.month_start
            : target.horizon === "quarter"
              ? periods.quarter_start
              : periods.week_start || week,
        user_id: userId || undefined,
      });
      onSave();
    } catch (submitError: any) {
      setError(submitError?.message || "Could not add goal");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={`Add ${horizonLabel(target.horizon)} goal`} onClose={onClose}>
      <form onSubmit={submit}>
        {error && (
          <div className="alert alert-danger py-2" role="alert">
            {error}
          </div>
        )}
        <label className="form-label">Title</label>
        <input className="form-control mb-3" name="title" required maxLength={512} autoFocus />
        <label className="form-label">Details</label>
        <textarea className="form-control mb-3" name="contents" rows={4} maxLength={4000} />
        <div className="d-flex justify-content-end gap-2">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={busy}>
            {busy ? "Adding…" : "Add goal"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
function ItemDetail({
  item,
  meId,
  readOnly,
  onClose,
  onChange,
  onDelete,
  onNotice,
}: {
  item: Row;
  meId: string;
  /** Reviewer on someone else's goal: may comment, can't change the goal. */
  readOnly: boolean;
  onClose: () => void;
  onChange: (updated?: Row) => void;
  onDelete: () => void;
  onNotice: (message: string, tone?: NoticeTone) => void;
}) {
  const [comments, setComments] = useState<Row[]>([]);
  const [commentsLoading, setCommentsLoading] = useState(true);
  const [commentsError, setCommentsError] = useState("");
  const [commentBusy, setCommentBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(String(item.title || ""));
  const [contents, setContents] = useState(String(item.contents || ""));
  const [saving, setSaving] = useState(false);
  const [completing, setCompleting] = useState(false);
  const [movingWeek, setMovingWeek] = useState(false);
  const dirty = title !== String(item.title || "") || contents !== String(item.contents || "");

  const close = () => {
    if (dirty && !window.confirm("Discard your unsaved changes?")) return;
    onClose();
  };
  const loadComments = async () => {
    setCommentsLoading(true);
    setCommentsError("");
    try {
      setComments(await api.comments(item.id));
    } catch (loadError: any) {
      setCommentsError(loadError?.message || "Could not load comments");
    } finally {
      setCommentsLoading(false);
    }
  };
  useEffect(() => {
    loadComments();
  }, [item.id]);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!title.trim() || saving) return;
    setSaving(true);
    try {
      const updated = await api.updateWorkItem(item.id, {
        title: title.trim(),
        contents: contents.trim() || null,
      });
      setTitle(updated.title);
      setContents(updated.contents || "");
      setEditing(false);
      onChange(updated);
      onNotice("Goal saved");
    } catch (saveError: any) {
      onNotice(saveError?.message || "Could not save goal", "danger");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={editing ? "Edit goal" : item.title} onClose={close}>
      {editing ? (
        <form className="stage-wp-edit-form" onSubmit={save}>
          <label className="form-label" htmlFor={`goal-title-${item.id}`}>
            Title
          </label>
          <input
            id={`goal-title-${item.id}`}
            className="form-control mb-3"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            maxLength={512}
            required
            autoFocus
          />
          <label className="form-label" htmlFor={`goal-details-${item.id}`}>
            Details
          </label>
          <textarea
            id={`goal-details-${item.id}`}
            className="form-control mb-3"
            value={contents}
            onChange={(event) => setContents(event.target.value)}
            rows={5}
            maxLength={4000}
          />
          <div className="d-flex justify-content-end gap-2 mb-4">
            <button
              type="button"
              className="btn"
              disabled={saving}
              onClick={() => {
                setTitle(String(item.title || ""));
                setContents(String(item.contents || ""));
                setEditing(false);
              }}
            >
              Cancel
            </button>
            <button className="btn btn-primary" disabled={saving || !title.trim() || !dirty}>
              {saving ? "Saving…" : "Save changes"}
            </button>
          </div>
        </form>
      ) : (
        <div className="stage-wp-detail-copy">
          <p className="text-secondary stage-wp-prewrap">{item.contents || "No details added."}</p>
          {!readOnly && (
            <button className="btn btn-outline-primary btn-sm" onClick={() => setEditing(true)}>
              <i className="ti ti-edit me-1" aria-hidden="true" />
              Edit goal
            </button>
          )}
        </div>
      )}
      <div className="d-flex align-items-center justify-content-between mb-4">
        <label className="form-check form-switch">
          <input
            className="form-check-input"
            type="checkbox"
            checked={Boolean(item.done)}
            disabled={completing || readOnly}
            onChange={async (event) => {
              const done = event.target.checked;
              setCompleting(true);
              try {
                const updated = await api.updateWorkItem(item.id, { done });
                onChange(updated);
                onNotice(done ? "Goal completed" : "Goal reopened");
              } catch (updateError: any) {
                onNotice(updateError?.message || "Could not update goal", "danger");
              } finally {
                setCompleting(false);
              }
            }}
          />
          <span className="form-check-label">Completed</span>
        </label>
        {item.horizon === "week" && !item.done && !readOnly && (
          <button
            type="button"
            className="btn btn-outline-secondary btn-sm"
            disabled={movingWeek}
            onClick={async () => {
              if (movingWeek) return;
              setMovingWeek(true);
              try {
                await api.moveWorkItemNextWeek(item.id);
                onNotice("Sent to next week");
                onChange();
                onClose();
              } catch (moveError: any) {
                onNotice(moveError?.message || "Could not send to next week", "danger");
              } finally {
                setMovingWeek(false);
              }
            }}
          >
            <i className="ti ti-arrow-right me-1" aria-hidden="true" />
            {movingWeek ? "Sending…" : "Send to next week"}
          </button>
        )}
      </div>
      <div className="stage-detail-section-label">Comments</div>
      <div className="stage-wp-comment-list" aria-live="polite">
        {commentsLoading ? (
          <div className="text-secondary small">
            <span className="spinner-border spinner-border-sm me-2" />
            Loading comments…
          </div>
        ) : commentsError ? (
          <div className="alert alert-danger py-2 mb-0">
            {commentsError}
            <button className="btn btn-sm btn-link" onClick={loadComments}>
              Try again
            </button>
          </div>
        ) : comments.length ? (
          comments.map((comment) => (
            <div className="stage-wp-comment" key={comment.id}>
              <div className="d-flex justify-content-between">
                <UserIdentity
                  person={{
                    id: comment.author_id,
                    name: comment.author_name,
                    email: comment.author_email,
                    role: comment.author_role,
                    avatar_url: comment.author_avatar_url,
                  }}
                  secondary={when(comment.created_at)}
                />
                {(!readOnly || comment.author_id === meId) && (
                  <button
                    className="btn btn-ghost-danger btn-sm"
                    title="Delete comment"
                    aria-label={`Delete comment by ${comment.author_name || comment.author_email || "this author"}`}
                    onClick={async () => {
                      if (!window.confirm("Delete this comment?")) return;
                      try {
                        await api.deleteComment(comment.id);
                        await loadComments();
                        onChange();
                        onNotice("Comment deleted");
                      } catch (deleteError: any) {
                        onNotice(deleteError?.message || "Could not delete comment", "danger");
                      }
                    }}
                  >
                    <i className="ti ti-trash" aria-hidden="true" />
                  </button>
                )}
              </div>
              <div className="mt-1 stage-wp-comment-body">{comment.body}</div>
            </div>
          ))
        ) : (
          <p className="text-secondary small">No comments yet</p>
        )}
      </div>
      <form
        className="mt-3"
        onSubmit={async (event) => {
          event.preventDefault();
          const input = event.currentTarget.elements.namedItem("body") as HTMLTextAreaElement;
          if (!input.value.trim() || commentBusy) return;
          setCommentBusy(true);
          try {
            await api.addComment(item.id, input.value.trim());
            input.value = "";
            await loadComments();
            onChange();
            onNotice("Comment posted");
          } catch (postError: any) {
            onNotice(postError?.message || "Could not post comment", "danger");
          } finally {
            setCommentBusy(false);
          }
        }}
      >
        <textarea
          className="form-control mb-2"
          name="body"
          rows={3}
          placeholder="Add a comment…"
          maxLength={8000}
          disabled={commentBusy}
          required
        />
        <button className="btn btn-primary btn-sm" disabled={commentBusy}>
          {commentBusy ? "Posting…" : "Post comment"}
        </button>
      </form>
      {!readOnly && (
        <div className="stage-wp-danger-zone">
          <button className="btn btn-sm btn-ghost-danger" onClick={onDelete}>
            <i className="ti ti-trash me-1" aria-hidden="true" />
            Delete goal
          </button>
        </div>
      )}
    </Modal>
  );
}

function saturday(date = new Date()) {
  const value = new Date(date);
  value.setHours(12, 0, 0, 0);
  value.setDate(value.getDate() - ((value.getDay() + 1) % 7));
  return localIso(value);
}
function localIso(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}
function weekLabel(value: string) {
  const start = new Date(`${value}T12:00:00`),
    end = new Date(start);
  end.setDate(end.getDate() + 6);
  return `${start.toLocaleDateString(undefined, { month: "short", day: "numeric" })} – ${end.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}`;
}
function weekIndexOf(value: string) {
  const start = new Date(`${value}T12:00:00`);
  const yearStart = new Date(start.getFullYear(), 0, 1, 12);
  yearStart.setDate(yearStart.getDate() - ((yearStart.getDay() + 1) % 7));
  const diffDays = Math.round((start.getTime() - yearStart.getTime()) / 86400000);
  return Math.floor(diffDays / 7) + 1;
}
function preview(value: unknown) {
  const text = String(value || "").trim();
  return text.length > 100 ? `${text.slice(0, 100).trim()}…` : text;
}
function horizonLabel(value: string) {
  return value === "quarter" ? "quarterly" : value === "month" ? "monthly" : "weekly";
}
function num(value: unknown) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}
