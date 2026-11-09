import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, type Row } from "../../api";
import { ErrorBox, Modal } from "../../ui";
import { UserIdentity, userPerson } from "../../components/Avatar";
import { formatInterviewRange } from "../../interviewTime";

/** How a pursuit is going. Reserved status colours - always paired with an
 *  icon and a word, never carried by colour alone. */
const STATE_META: Record<string, { label: string; tone: string; icon: string }> = {
  active: { label: "Active", tone: "azure", icon: "ti-player-play" },
  stalled: { label: "Stalled", tone: "warning", icon: "ti-hourglass-low" },
  offer: { label: "Offer", tone: "success", icon: "ti-confetti" },
  rejected: { label: "Closed", tone: "danger", icon: "ti-circle-x" },
  withdrawn: { label: "Withdrawn", tone: "secondary", icon: "ti-arrow-back-up" },
};

const OUTCOME_META: Record<string, { tone: string; icon: string }> = {
  passed: { tone: "success", icon: "ti-check" },
  failed: { tone: "danger", icon: "ti-x" },
  no_response: { tone: "secondary", icon: "ti-mail-off" },
  withdrew: { tone: "secondary", icon: "ti-arrow-back-up" },
  pending: { tone: "muted", icon: "ti-dots" },
};

/** One application per row, its actual rounds laid out along the row.
 *
 *  Deliberately not a column-per-stage board. Slotting each pursuit into a
 *  single fixed column answered "how many are at each stage" but hid the thing
 *  the page exists for - what this company's process actually was, and how far
 *  through it you are. It also forced every employer into the same seven rungs,
 *  when a real process might be two rounds or eight. A row grows to whatever
 *  length the pursuit needs. */
export function PipelineBoard({
  rows,
  onOpen,
  showOwner = false,
}: {
  rows: Row[];
  onOpen: (row: Row) => void;
  showOwner?: boolean;
}) {
  return (
    <div className="stage-pipe-tracks">
      {rows.map((row) => (
        <PipelineTrack
          key={text(row.application_id)}
          row={row}
          onOpen={onOpen}
          showOwner={showOwner}
        />
      ))}
    </div>
  );
}

function PipelineTrack({
  row,
  onOpen,
  showOwner,
}: {
  row: Row;
  onOpen: (row: Row) => void;
  showOwner?: boolean;
}) {
  const steps = arr(row.steps);
  return (
    <button
      type="button"
      className={`stage-pipe-track${showOwner ? " stage-pipe-track-owned" : ""}`}
      onClick={() => onOpen(row)}
    >
      {showOwner && (
        <span className="stage-pipe-track-owner">
          <UserIdentity person={userPerson(row)} size="sm" />
        </span>
      )}
      <div className="stage-pipe-track-id">
        <span className="stage-pipe-card-company">
          {text(row.company_name) || "Unknown company"}
        </span>
        <span className="stage-pipe-card-role" title={text(row.position_title)}>
          {text(row.position_title) || "—"}
        </span>
      </div>

      <div className="stage-pipe-track-rounds">
        {steps.map((step, index) => (
          <RoundNode key={text(step.id) || index} step={step} />
        ))}
      </div>

      <div className="stage-pipe-track-meta">
        <StateChip value={row.pipeline_state} />
        <span title="Rounds passed out of rounds held so far">
          <i className="ti ti-checks" /> {num(row.rounds_passed)}/{num(row.rounds_total)}
        </span>
        <Quiet row={row} />
      </div>
    </button>
  );
}

/** One round: its position, what it was, and whether it was passed.
 *
 *  The round number is shown rather than drawn as a connecting line. A line
 *  reads as a sequence only while every round fits on one line, and a six-round
 *  process does not - it would either clip mid-word or leave a connector
 *  dangling at the wrap. An explicit number survives any length. */
function RoundNode({ step }: { step: Row }) {
  const outcome = text(step.outcome) || "pending";
  const meta = OUTCOME_META[outcome] || OUTCOME_META.pending;
  const label = text(step.interview_type) || text(step.stage_label) || "Round";
  const scheduled = text(step.status) === "scheduled";
  return (
    <span
      className={`stage-pipe-node stage-pipe-node-${outcome}${scheduled ? " is-scheduled" : ""}`}
      title={`Round ${num(step.round_index)}: ${label} · ${text(step.status)} · ${
        text(step.outcome_label) || "Pending"
      }${text(step.start_time_utc) ? ` · ${shortDate(step.start_time_utc)}` : ""}`}
    >
      <span className="stage-pipe-node-dot">
        <i className={`ti ${meta.icon}`} />
      </span>
      <span className="stage-pipe-node-round">{num(step.round_index)}</span>
      <span className="stage-pipe-node-label">{label}</span>
    </span>
  );
}

function StateChip({ value }: { value: unknown }) {
  const meta = STATE_META[text(value)] || STATE_META.active;
  return (
    <span className={`badge bg-${meta.tone}-lt stage-pipe-state`}>
      <i className={`ti ${meta.icon}`} /> {meta.label}
    </span>
  );
}

function Quiet({ row }: { row: Row }) {
  const next = text(row.next_scheduled);
  if (next) {
    return (
      <span title="Next scheduled round">
        <i className="ti ti-calendar-event" /> {shortDate(next)}
      </span>
    );
  }
  const days = row.days_since_activity;
  if (days === null || days === undefined) return <span />;
  return (
    <span title="Days since the last round that actually happened">
      <i className="ti ti-clock-pause" /> quiet {num(days)}d
    </span>
  );
}

/** Application statuses, in the desktop client's canonical casing. */
const APPLICATION_STATUSES = [
  "Applied",
  "Phone Screen",
  "Interview",
  "Offer",
  "Rejected",
  "Withdrawn",
] as const;

/** The application behind the pipeline row: its details, and its status.
 *
 *  Status is editable here because it is what closes a pursuit - the pipeline
 *  state honours a terminal status (Offer / Rejected / Withdrawn) over anything
 *  inferred from the rounds, so this is the control that moves a row out of
 *  "active" without having to open the desktop app. */
function ApplicationPanel({ row }: { row: Row }) {
  const client = useQueryClient();
  const applicationId = text(row.application_id);
  const [status, setStatus] = useState(text(row.application_status) || "Applied");
  const [error, setError] = useState<unknown>(null);

  const save = useMutation({
    mutationFn: (next: string) => api.updateApplicationStatus(applicationId, next),
    onMutate: () => setError(null),
    onSuccess: (result) => {
      setStatus(text(result?.status) || status);
      // The pipeline state is derived from this status, so the board and the
      // performance metrics are both stale the moment it changes.
      client.invalidateQueries({ queryKey: ["interview-pipeline"] });
      client.invalidateQueries({ queryKey: ["interview-pipeline-stats"] });
    },
    onError: (err) => {
      // Put the control back to what the server still holds, so the UI never
      // shows a status that was not saved.
      setStatus(text(row.application_status) || "Applied");
      setError(err);
    },
  });

  return (
    <div className="stage-pipe-appinfo">
      <div className="stage-detail-section-label mb-2">Application</div>
      <div className="stage-pipe-appinfo-grid">
        <Field label="Company" value={row.company_name} />
        <Field label="Role" value={row.position_title} />
        <Field label="Applied" value={shortDate(row.application_applied_date)} />
        <Field label="Account" value={row.application_account} />
        <Field label="Profile" value={row.profile_name || row.profile_key} />
        <Field label="Owner" value={row.user_name || row.user_email} />
      </div>

      {text(row.application_job_link) && (
        <a
          className="stage-pipe-appinfo-link"
          href={text(row.application_job_link)}
          target="_blank"
          rel="noreferrer noopener"
        >
          <i className="ti ti-external-link" /> Open job posting
        </a>
      )}

      {text(row.application_notes) && (
        <div className="stage-pipe-detail-text">
          <div className="stage-detail-section-label">Application notes</div>
          <div className="text-secondary" style={{ whiteSpace: "pre-wrap" }}>
            {text(row.application_notes)}
          </div>
        </div>
      )}

      <label className="stage-pipe-appinfo-status">
        <span className="stage-detail-section-label">Status</span>
        <select
          className="form-select form-select-sm"
          value={status}
          disabled={!applicationId || save.isPending}
          onChange={(event) => {
            const next = event.target.value;
            setStatus(next);
            save.mutate(next);
          }}
        >
          {/* A value the desktop wrote that is not in this list would otherwise
              vanish from the control and be silently replaced on the next save. */}
          {!APPLICATION_STATUSES.includes(status as never) && <option>{status}</option>}
          {APPLICATION_STATUSES.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
        {save.isPending && <span className="spinner-border spinner-border-sm" />}
        {save.isSuccess && !save.isPending && (
          <span className="text-success small">
            <i className="ti ti-check" /> Saved
          </span>
        )}
      </label>
      <div className="text-secondary small">
        Closing an application here moves its pipeline row out of Active, and syncs to the desktop
        app.
      </div>
      {error ? <ErrorBox error={error} /> : null}
    </div>
  );
}

function Field({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <div className="stage-detail-section-label">{label}</div>
      <div className="stage-pipe-stat-value">{text(value) || "—"}</div>
    </div>
  );
}

/** The per-application journey: every round, in order, with its outcome. */
export function PipelineDetail({ row }: { row: Row }) {
  const steps = Array.isArray(row.steps) ? (row.steps as Row[]) : [];
  return (
    <div className="stage-pipe-detail">
      <div className="stage-pipe-detail-head">
        <div>
          <div className="stage-pipe-detail-company">
            {text(row.company_name) || "Unknown company"}
          </div>
          <div className="text-secondary">{text(row.position_title) || "—"}</div>
        </div>
        <StateChip value={row.pipeline_state} />
      </div>

      <div className="stage-pipe-detail-stats">
        <Stat label="Stage" value={text(row.current_stage_label) || "Other"} />
        <Stat label="Round" value={num(row.current_round) || "—"} />
        <Stat label="Passed" value={`${num(row.rounds_passed)} of ${num(row.rounds_total)}`} />
        <Stat
          label="Last activity"
          value={
            row.days_since_activity === null || row.days_since_activity === undefined
              ? "—"
              : `${num(row.days_since_activity)}d ago`
          }
        />
      </div>

      <ApplicationPanel row={row} />

      {text(row.next_step) && (
        <div className="stage-pipe-detail-next">
          <i className="ti ti-arrow-right" />
          <div>
            <div className="stage-detail-section-label">Next step</div>
            <div>{text(row.next_step)}</div>
            {text(row.next_step_due) && (
              <div className="text-secondary small">Due {shortDate(row.next_step_due)}</div>
            )}
          </div>
        </div>
      )}

      <div className="stage-detail-section-label mb-2">Rounds</div>
      <ol className="stage-pipe-timeline">
        {steps.map((step, index) => {
          const meta = OUTCOME_META[text(step.outcome)] || OUTCOME_META.pending;
          return (
            <li className="stage-pipe-step" key={text(step.id) || index}>
              <span className={`stage-pipe-step-dot stage-pipe-outcome-${text(step.outcome)}`}>
                <i className={`ti ${meta.icon}`} />
              </span>
              <div className="stage-pipe-step-body">
                <div className="stage-pipe-step-head">
                  <span className="stage-pipe-step-title">
                    <span className="stage-pipe-step-round">R{num(step.round_index)}</span>
                    {text(step.title) || text(step.interview_type) || "Interview"}
                  </span>
                  <span className={`badge bg-${meta.tone}-lt`}>
                    {text(step.outcome_label) || "Pending"}
                  </span>
                </div>
                <div className="stage-pipe-step-meta">
                  <span>{text(step.stage_label) || "Other"}</span>
                  {text(step.interview_type) && <span>{text(step.interview_type)}</span>}
                  <span>{text(step.status)}</span>
                </div>
                <div className="text-secondary small">
                  {formatInterviewRange(step.start_time_utc, undefined, step.timezone)}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
      {!steps.length && <div className="text-secondary">No rounds recorded.</div>}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="stage-pipe-stat">
      <div className="stage-detail-section-label">{label}</div>
      <div className="stage-pipe-stat-value">{text(value) || "—"}</div>
    </div>
  );
}

export function PipelineModal({ row, onClose }: { row: Row; onClose: () => void }) {
  return (
    <Modal
      size="lg"
      variant="center"
      title={`${text(row.company_name) || "Application"} — pipeline`}
      onClose={onClose}
    >
      <PipelineDetail row={row} />
    </Modal>
  );
}

function shortDate(value: unknown) {
  const date = new Date(text(value));
  return Number.isNaN(date.getTime())
    ? "—"
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}
function arr(value: unknown): Row[] {
  return Array.isArray(value) ? (value as Row[]) : [];
}
function num(value: unknown) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}
function text(value: unknown) {
  return value === null || value === undefined ? "" : String(value).trim();
}
