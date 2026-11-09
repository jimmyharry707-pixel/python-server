import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  uploadReviewScan,
  type ReviewScan,
  type ScanFileRef,
  type ScanFinding,
  type ScanMatch,
  type ScanJob,
  type ScanOptions,
  type ScanVerdict,
  type Row,
} from "../../api";
import { ErrorBox, Loading, Modal, NumberedPager, PageHeader, whenUtc } from "../../ui";
import { useToast } from "../../components/ToastProvider";
import { UserIdentity, userPerson } from "../../components/Avatar";
import { localDate, shiftDate } from "../../reportingPeriod";

const CURRENT_KEY = ["review-current"];
const STEPS = ["Upload", "Options", "Scan", "Review", "Finish"];
const LEVEL: Record<string, { label: string; tone: string }> = {
  fail: { label: "Fail", tone: "danger" },
  issue: { label: "Issue", tone: "warning" },
  check: { label: "To check", tone: "azure" },
  ok: { label: "OK", tone: "success" },
  info: { label: "Info", tone: "secondary" },
};

export function QualityPage() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("tab");
  const tab = raw === "history" || raw === "summary" ? raw : "scan";
  const setTab = (next: "scan" | "history" | "summary") =>
    setParams(next === "scan" ? {} : { tab: next }, { replace: true });
  return (
    <>
      <PageHeader text="Check one supporter's work for one day: upload their two ZIP files, let the scan match them to the tracker, then review what it found." />
      <div className="stage-users-toolbar mb-3">
        <div className="btn-group stage-users-tabs" role="tablist" aria-label="Quality review">
          {(
            [
              ["scan", "New scan", "ti-scan"],
              ["history", "History", "ti-history"],
              ["summary", "Summary", "ti-report-analytics"],
            ] as const
          ).map(([key, label, icon]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              className={`btn ${tab === key ? "btn-primary" : "btn-outline-secondary"}`}
              onClick={() => setTab(key)}
            >
              <i className={`ti ${icon} me-1`} aria-hidden="true" />
              {label}
            </button>
          ))}
        </div>
      </div>
      {tab === "scan" ? <NewScan /> : tab === "history" ? <History /> : <Summary />}
    </>
  );
}

// ----- the wizard -----

function NewScan() {
  const current = useQuery({
    queryKey: CURRENT_KEY,
    queryFn: api.reviewCurrent,
    refetchInterval: (query) => {
      const status = query.state.data?.scan?.status;
      return status === "queued" || status === "scanning" ? 1500 : false;
    },
  });
  if (current.isLoading) return <Loading />;
  if (current.error) return <ErrorBox error={current.error} />;
  const scan = current.data?.scan ?? null;
  const step = !scan
    ? 0
    : scan.status === "uploaded"
      ? 1
      : scan.status === "queued" || scan.status === "scanning" || scan.status === "failed"
        ? 2
        : 3;
  return (
    <>
      <Stepper active={step} />
      {!scan && <UploadStep />}
      {scan?.status === "uploaded" && <OptionsStep scan={scan} />}
      {(scan?.status === "queued" || scan?.status === "scanning") && <ProgressStep scan={scan} />}
      {scan?.status === "failed" && <FailedStep scan={scan} />}
      {scan?.status === "ready" && <ResultsStep scan={scan} />}
    </>
  );
}

function Stepper({ active }: { active: number }) {
  return (
    <ol className="stage-scan-steps mb-3" aria-label="Steps">
      {STEPS.map((label, i) => (
        <li
          key={label}
          className={i < active ? "is-done" : i === active ? "is-active" : ""}
          aria-current={i === active ? "step" : undefined}
        >
          <span className="stage-scan-step-dot">
            {i < active ? <i className="ti ti-check" aria-hidden="true" /> : i + 1}
          </span>
          <span className="stage-scan-step-label">{label}</span>
        </li>
      ))}
    </ol>
  );
}

function UploadStep() {
  const toast = useToast();
  const client = useQueryClient();
  const [supporter, setSupporter] = useState("");
  const [day, setDay] = useState(shiftDate(localDate(), -1));
  const [resumes, setResumes] = useState<File | null>(null);
  const [snapshots, setSnapshots] = useState<File | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState("");
  const supporters = useQuery({ queryKey: ["review-supporters"], queryFn: api.reviewSupporters });
  const preview = useQuery({
    queryKey: ["review-day", supporter, day],
    queryFn: () => api.reviewDay(supporter, day),
    enabled: Boolean(supporter && day),
  });
  useEffect(() => {
    const list = supporters.data || [];
    if (!supporter && list.length === 1) setSupporter(list[0].id);
  }, [supporters.data, supporter]);

  const ready = supporter && day && resumes && snapshots && progress === null;
  const submit = async () => {
    if (!ready) return;
    setError("");
    const form = new FormData();
    form.append("supporter_id", supporter);
    form.append("work_date", day);
    form.append("resumes", resumes);
    form.append("snapshots", snapshots);
    setProgress(0);
    try {
      await uploadReviewScan(form, setProgress);
      toast.success("Uploaded. Choose the options next.");
      client.invalidateQueries({ queryKey: CURRENT_KEY });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setProgress(null);
    }
  };
  const person = (supporters.data || []).find((s) => s.id === supporter);

  return (
    <div className="card">
      <div className="card-header">
        <h3 className="card-title mb-0">1. Choose the day and upload the files</h3>
      </div>
      <div className="card-body">
        <div className="row g-3">
          <div className="col-md-6">
            <label className="form-label" htmlFor="scan-supporter">
              Supporter
            </label>
            <select
              id="scan-supporter"
              className="form-select"
              value={supporter}
              onChange={(e) => setSupporter(e.target.value)}
            >
              <option value="">Choose a supporter</option>
              {(supporters.data || []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name || s.email}
                </option>
              ))}
            </select>
          </div>
          <div className="col-md-6">
            <label className="form-label" htmlFor="scan-day">
              Day of work
            </label>
            <input
              id="scan-day"
              type="date"
              className="form-control"
              value={day}
              max={localDate()}
              onChange={(e) => setDay(e.target.value)}
            />
          </div>
        </div>
        {supporters.data && !supporters.data.length && (
          <div className="alert alert-info mt-3 mb-0">No supporters to review yet.</div>
        )}
        {preview.data && person && (
          <div
            className={`alert ${preview.data.records ? "alert-secondary" : "alert-warning"} mt-3 mb-0`}
          >
            {preview.data.records
              ? `${person.name || person.email} has ${preview.data.records} tracker record${preview.data.records === 1 ? "" : "s"} on ${longDay(day)}.`
              : `${person.name || person.email} has no tracker records on ${longDay(day)}. The scan will list every file as untracked.`}
            {preview.data.finished_scans > 0 &&
              ` This day was already reviewed ${preview.data.finished_scans === 1 ? "once" : `${preview.data.finished_scans} times`}.`}
          </div>
        )}
        <div className="row g-3 mt-1">
          <FilePick
            id="scan-resumes"
            label="Resumes ZIP"
            hint="CareerCraft's Documents → Export ZIP… for that day"
            file={resumes}
            onChange={setResumes}
          />
          <FilePick
            id="scan-snapshots"
            label="Snapshots ZIP"
            hint="The day's SingleFile captures"
            file={snapshots}
            onChange={setSnapshots}
          />
        </div>
        {error && <div className="alert alert-danger mt-3 mb-0">{error}</div>}
        {progress !== null && (
          <div className="mt-3" role="status">
            <div className="d-flex justify-content-between small text-secondary mb-1">
              <span>{progress < 100 ? "Uploading…" : "Checking the ZIP files…"}</span>
              <span>{progress}%</span>
            </div>
            <div className="progress">
              <div className="progress-bar" style={{ width: `${progress}%` }} />
            </div>
          </div>
        )}
      </div>
      <div className="card-footer d-flex justify-content-between align-items-center gap-2 flex-wrap">
        <span className="text-secondary small">
          Files stay on the server only until the scan is finished or cancelled, and at most 48
          hours.
        </span>
        <button type="button" className="btn btn-primary" disabled={!ready} onClick={submit}>
          <i className="ti ti-upload me-1" aria-hidden="true" />
          Upload
        </button>
      </div>
    </div>
  );
}

function FilePick({
  id,
  label,
  hint,
  file,
  onChange,
}: {
  id: string;
  label: string;
  hint: string;
  file: File | null;
  onChange: (f: File | null) => void;
}) {
  return (
    <div className="col-md-6">
      <label className="form-label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        type="file"
        accept=".zip,application/zip"
        className="form-control"
        onChange={(e) => onChange(e.target.files?.[0] ?? null)}
      />
      <div className="form-hint">{file ? `${file.name} · ${fmtBytes(file.size)}` : hint}</div>
    </div>
  );
}

function OptionsStep({ scan }: { scan: ReviewScan }) {
  const toast = useToast();
  const client = useQueryClient();
  const [o, setO] = useState<ScanOptions>(scan.options);
  const [lists, setLists] = useState({
    excluded_sites: scan.options.excluded_sites.join("\n"),
    staffing_agencies: scan.options.staffing_agencies.join("\n"),
    leftover_markers: scan.options.leftover_markers.join("\n"),
  });
  const set = <K extends keyof ScanOptions>(key: K, value: ScanOptions[K]) =>
    setO((prev) => ({ ...prev, [key]: value }));
  const start = useMutation({
    mutationFn: () =>
      api.startReviewScan(scan.id, {
        ...o,
        excluded_sites: splitLines(lists.excluded_sites),
        staffing_agencies: splitLines(lists.staffing_agencies),
        leftover_markers: splitLines(lists.leftover_markers),
      }),
    onSuccess: (data) => client.setQueryData(CURRENT_KEY, { scan: data }),
    onError: (e: Error) => toast.error(e.message),
  });
  const uploads = scan.uploads || {};
  const warnings: string[] = uploads.warnings || [];
  const valid =
    o.duplicate_window_days >= 1 &&
    o.duplicate_window_days <= 365 &&
    o.pace_minutes >= 0 &&
    o.resume_max_pages >= 0 &&
    o.cover_letter_max_pages >= 0;

  return (
    <div className="card">
      <div className="card-header">
        <div>
          <h3 className="card-title mb-0">2. Choose what to check</h3>
          <div className="text-secondary small">
            {scan.supporter?.name} · {longDay(scan.work_date)} · {uploads.resumes?.files ?? 0}{" "}
            resume files · {uploads.snapshots?.files ?? 0} capture files. These options are
            remembered for the next scan.
          </div>
        </div>
      </div>
      <div className="card-body">
        {warnings.map((w) => (
          <div key={w} className="alert alert-warning py-2">
            <i className="ti ti-alert-triangle me-1" aria-hidden="true" />
            {w}
          </div>
        ))}
        <div className="row g-4">
          <OptionGroup title="Matching records">
            <div className="form-hint mt-0">
              Records are never judged duplicates automatically. Matches are shown side by side for
              you to decide.
            </div>
            <NumberField
              id="o-window"
              label="Same company window (days)"
              hint="Show the candidate's records at a company with a matching name within this many days. A matching job link is shown however long ago it was."
              value={o.duplicate_window_days}
              min={1}
              max={365}
              onChange={(v) => set("duplicate_window_days", v)}
            />
            <Switch
              id="o-lastday"
              label={`An application exactly ${o.duplicate_window_days} days ago still counts`}
              checked={o.window_includes_last_day}
              onChange={(v) => set("window_includes_last_day", v)}
            />
            <ListField
              id="o-agencies"
              label="Staffing agencies"
              hint="One per line. The same-company rule applies to end clients, so these names aren't matched by company. A matching job link is still shown."
              value={lists.staffing_agencies}
              onChange={(v) => setLists((p) => ({ ...p, staffing_agencies: v }))}
            />
          </OptionGroup>
          <OptionGroup title="Job links">
            <Switch
              id="o-jobright-apply"
              label="Applying through Jobright's Apply button is allowed"
              hint="When off, applications whose link carries Jobright's tag are flagged."
              checked={o.allow_jobright_apply}
              onChange={(v) => set("allow_jobright_apply", v)}
            />
            <Switch
              id="o-jobright-link"
              label="The tracker link must be the Jobright job link"
              checked={o.require_jobright_link}
              onChange={(v) => set("require_jobright_link", v)}
            />
            <ListField
              id="o-sites"
              label="Excluded job sites"
              hint="One per line. Candidates apply on these themselves, so supporters' applications there fail."
              value={lists.excluded_sites}
              onChange={(v) => setLists((p) => ({ ...p, excluded_sites: v }))}
            />
          </OptionGroup>
          <OptionGroup title="Resumes and cover letters">
            <ListField
              id="o-markers"
              label="Leftover text to look for"
              hint="One per line. [...] means any text in square brackets, like [need to verify]."
              value={lists.leftover_markers}
              onChange={(v) => setLists((p) => ({ ...p, leftover_markers: v }))}
            />
            <div className="row g-2">
              <div className="col-6">
                <NumberField
                  id="o-rpages"
                  label="Resume max pages"
                  hint="0 = don't check"
                  value={o.resume_max_pages}
                  min={0}
                  max={10}
                  onChange={(v) => set("resume_max_pages", v)}
                />
              </div>
              <div className="col-6">
                <NumberField
                  id="o-cpages"
                  label="Cover letter max pages"
                  hint="0 = don't check"
                  value={o.cover_letter_max_pages}
                  min={0}
                  max={10}
                  onChange={(v) => set("cover_letter_max_pages", v)}
                />
              </div>
            </div>
          </OptionGroup>
          <OptionGroup title="Form captures">
            <Switch
              id="o-consent"
              label="Point out consent, signature and background-check answers"
              checked={o.flag_consent_answers}
              onChange={(v) => set("flag_consent_answers", v)}
            />
            <Switch
              id="o-consistency"
              label="Point out different Yes/No answers on different jobs"
              hint="Work authorization, sponsorship, referral."
              checked={o.check_answer_consistency}
              onChange={(v) => set("check_answer_consistency", v)}
            />
            <Switch
              id="o-order"
              label="Point out forms saved before their resume was made"
              checked={o.check_capture_order}
              onChange={(v) => set("check_capture_order", v)}
            />
            <NumberField
              id="o-pace"
              label="Note applications made within (minutes) of the previous one"
              hint="0 = off. A note only, never a problem."
              value={o.pace_minutes}
              min={0}
              max={240}
              onChange={(v) => set("pace_minutes", v)}
            />
          </OptionGroup>
        </div>
      </div>
      <div className="card-footer d-flex justify-content-between gap-2 flex-wrap">
        <CancelButton scan={scan} />
        <button
          type="button"
          className="btn btn-primary"
          disabled={!valid || start.isPending}
          onClick={() => start.mutate()}
        >
          <i className="ti ti-player-play me-1" aria-hidden="true" />
          Start scan
        </button>
      </div>
    </div>
  );
}

function ProgressStep({ scan }: { scan: ReviewScan }) {
  const waiting = scan.status === "queued";
  return (
    <div className="card">
      <div className="card-header">
        <h3 className="card-title mb-0">3. Scanning</h3>
      </div>
      <div className="card-body" role="status">
        <div className="d-flex justify-content-between small text-secondary mb-1">
          <span>
            {waiting
              ? scan.queue_position
                ? `Waiting: ${scan.queue_position} scan${scan.queue_position === 1 ? "" : "s"} ahead of yours`
                : "Starting…"
              : scan.stage}
          </span>
          <span>{scan.progress}%</span>
        </div>
        <div className="progress">
          <div
            className={`progress-bar${waiting ? " progress-bar-indeterminate" : ""}`}
            style={waiting ? undefined : { width: `${scan.progress}%` }}
          />
        </div>
        <p className="text-secondary small mt-3 mb-0">
          The server runs one scan at a time. You can leave this page; the scan keeps going.
        </p>
      </div>
      <div className="card-footer">
        <CancelButton scan={scan} />
      </div>
    </div>
  );
}

function FailedStep({ scan }: { scan: ReviewScan }) {
  const toast = useToast();
  const client = useQueryClient();
  const retry = useMutation({
    mutationFn: () => api.startReviewScan(scan.id, scan.options),
    onSuccess: (data) => client.setQueryData(CURRENT_KEY, { scan: data }),
    onError: (e: Error) => toast.error(e.message),
  });
  return (
    <div className="card">
      <div className="card-header">
        <h3 className="card-title mb-0">The scan stopped</h3>
      </div>
      <div className="card-body">
        <div className="alert alert-danger mb-0">{scan.error || "Something went wrong."}</div>
      </div>
      <div className="card-footer d-flex justify-content-between gap-2">
        <CancelButton scan={scan} label="Cancel and upload again" />
        <button
          type="button"
          className="btn"
          disabled={retry.isPending}
          onClick={() => retry.mutate()}
        >
          Try again
        </button>
      </div>
    </div>
  );
}

function ResultsStep({ scan }: { scan: ReviewScan }) {
  const toast = useToast();
  const client = useQueryClient();
  const jobs = scan.jobs || [];
  const [filter, setFilter] = useState<string>("all");
  const [selected, setSelected] = useState<number | null>(
    () => (jobs.find((j) => !j.verdict) || jobs[0])?.id ?? null,
  );
  const [dayFile, setDayFile] = useState<ScanFileRef | null>(null);
  const [confirm, setConfirm] = useState(false);
  const s = scan.summary || {};
  const counts = s.status || {};
  const shown = filter === "all" ? jobs : jobs.filter((j) => j.status === filter);
  const reviewed = jobs.filter((j) => j.verdict).length;
  const current = jobs.find((j) => j.id === selected) || null;
  const index = current ? shown.findIndex((j) => j.id === current.id) : -1;
  const go = (step: number) => {
    const next = shown[index + step];
    if (next) setSelected(next.id);
  };
  const finish = useMutation({
    mutationFn: () => api.finishReviewScan(scan.id),
    onSuccess: () => {
      toast.success("Report saved. The uploaded files were deleted.");
      client.invalidateQueries({ queryKey: CURRENT_KEY });
      client.invalidateQueries({ queryKey: ["review-history"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <>
      <div className="card mb-3">
        <div className="card-body d-flex flex-wrap align-items-center gap-3">
          <div className="me-auto">
            <div className="fw-bold">
              {scan.supporter?.name} · {longDay(scan.work_date)}
            </div>
            <div className="text-secondary small">
              {s.records} tracker records
              {s.same_link ? ` · ${s.same_link} with a matching job link` : ""}
              {s.same_company ? ` · ${s.same_company} with a matching company` : ""} ·{" "}
              {s.resume_folders} resume folders · {s.captures} captures
              {s.clock ? ` · times on the supporter's clock (${s.clock})` : ""}
            </div>
          </div>
          <span className="text-secondary small">
            Reviewed {reviewed} of {jobs.length}
          </span>
          <div className="btn-group btn-group-sm flex-wrap" role="group" aria-label="Show jobs">
            {(["all", "fail", "issue", "check", "ok"] as const).map((key) => (
              <button
                key={key}
                type="button"
                aria-pressed={filter === key}
                className={`btn ${filter === key ? "btn-secondary" : "btn-outline-secondary"}`}
                onClick={() => setFilter(key)}
              >
                {key === "all" ? "All" : LEVEL[key].label}
                <span className="ms-1 opacity-75">
                  {key === "all" ? jobs.length : (counts[key] ?? 0)}
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>

      {scan.day_findings.length > 0 && (
        <div className="card mb-3">
          <div className="card-header">
            <h3 className="card-title mb-0">Whole day</h3>
          </div>
          <ul className="list-group list-group-flush">
            {scan.day_findings.map((f, i) => (
              <li className="list-group-item" key={i}>
                <Finding f={f} />
                {f.files && f.files.length > 0 && (
                  <div className="d-flex flex-wrap gap-1 mt-2 ms-5">
                    {f.files.slice(0, 12).map((file) => (
                      <button
                        key={file.rel}
                        type="button"
                        className="btn btn-sm btn-outline-secondary"
                        onClick={() => setDayFile(file)}
                      >
                        <i className="ti ti-eye me-1" aria-hidden="true" />
                        {file.label || file.rel}
                      </button>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="row g-3 mb-3">
        <div className="col-xl-4">
          <div className="card stage-scan-joblist">
            <div className="card-header">
              <h3 className="card-title mb-0">4. Jobs</h3>
            </div>
            <div className="list-group list-group-flush">
              {shown.map((job) => (
                <button
                  key={job.id}
                  type="button"
                  className={`list-group-item list-group-item-action stage-scan-joblist-item${job.id === selected ? " active" : ""}`}
                  aria-current={job.id === selected ? "true" : undefined}
                  onClick={() => setSelected(job.id)}
                >
                  <span className="text-secondary stage-scan-job-num">{job.position + 1}</span>
                  <StatusBadge status={job.status} />
                  <span className="stage-scan-job-text">
                    <span className="d-block text-truncate fw-medium">{job.company || "—"}</span>
                    <span className="d-block text-truncate small text-secondary">
                      {job.title || "—"}
                    </span>
                  </span>
                  <VerdictChip verdict={job.verdict} />
                </button>
              ))}
              {!shown.length && (
                <div className="list-group-item text-secondary text-center py-4">
                  {jobs.length ? "No jobs with this result." : "No tracker records for this day."}
                </div>
              )}
            </div>
          </div>
        </div>
        <div className="col-xl-8">
          {current ? (
            <JobPanel
              key={current.id}
              scan={scan}
              job={current}
              position={index}
              total={shown.length}
              onPrev={index > 0 ? () => go(-1) : undefined}
              onNext={index >= 0 && index < shown.length - 1 ? () => go(1) : undefined}
            />
          ) : (
            <div className="card">
              <div className="card-body text-secondary">Choose a job on the left.</div>
            </div>
          )}
        </div>
      </div>

      <DayNote scan={scan} />
      <ReportCard text={scan.report_text || ""} fileName={reportName(scan)} />

      <div className="card">
        <div className="card-body d-flex justify-content-between align-items-center gap-2 flex-wrap">
          <CancelButton scan={scan} />
          <div className="d-flex align-items-center gap-3 flex-wrap">
            <span className="text-secondary small">
              5. Finishing saves the report and deletes the uploaded files.
            </span>
            <button
              type="button"
              className="btn btn-primary"
              disabled={!scan.can_edit}
              onClick={() => setConfirm(true)}
            >
              <i className="ti ti-device-floppy me-1" aria-hidden="true" />
              Finish and save report
            </button>
          </div>
        </div>
      </div>
      {dayFile && (
        <Modal title={dayFile.label || dayFile.rel} size="xl" onClose={() => setDayFile(null)}>
          <FileViewer scanId={scan.id} file={dayFile} />
        </Modal>
      )}
      {confirm && (
        <Modal
          title="Finish this review?"
          size="sm"
          onClose={() => setConfirm(false)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setConfirm(false)}>
                Not yet
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={finish.isPending}
                onClick={() => finish.mutate()}
              >
                Finish
              </button>
            </>
          }
        >
          {reviewed < jobs.length && (
            <div className="alert alert-warning">
              {jobs.length - reviewed} of {jobs.length} jobs have no verdict yet. They'll show as
              "not reviewed" in the report.
            </div>
          )}
          <p className="mb-0">
            The report is saved to History and the uploaded ZIP files are deleted from the server.
            You can't open the files from this scan afterwards.
          </p>
        </Modal>
      )}
    </>
  );
}

type Tab = {
  key: string;
  label: string;
  file?: ScanFileRef;
  kind: "resume" | "cover" | "capture" | "tracker";
};

function JobPanel({
  scan,
  job,
  position,
  total,
  onPrev,
  onNext,
}: {
  scan: ReviewScan;
  job: ScanJob;
  position: number;
  total: number;
  onPrev?: () => void;
  onNext?: () => void;
}) {
  const d = job.details || {};
  const tabs = useMemo<Tab[]>(() => {
    const out: Tab[] = [];
    // Resume first, then the cover letter, then the captures.
    for (const kind of ["resume", "cover"] as const) {
      const f = ((d.files || []) as Row[]).find((x) => x.kind === kind);
      if (f)
        out.push({
          key: f.rel,
          label: kind === "resume" ? "Resume" : "Cover letter",
          kind,
          file: { area: "resumes", rel: f.rel },
        });
    }
    ((d.captures || []) as Row[]).forEach((c, i, all) =>
      out.push({
        key: c.rel,
        label: `${all.length > 1 ? `Capture ${i + 1}` : "Capture"}${c.saved ? ` · ${c.saved}` : ""}`,
        kind: "capture",
        file: { area: "snapshots", rel: c.rel },
      }),
    );
    out.push({ key: "tracker", label: "Tracker and answers", kind: "tracker" });
    return out;
  }, [d]);
  const [tab, setTab] = useState(tabs[0].key);
  const active = tabs.find((t) => t.key === tab) || tabs[0];
  const missing = [
    !(d.files || []).some((f: Row) => f.kind === "resume") && "resume",
    !(d.captures || []).length && "capture",
  ].filter(Boolean);

  return (
    <div className="card">
      <div className="card-header flex-wrap gap-2">
        <div className="me-auto" style={{ minWidth: 0 }}>
          <div className="d-flex align-items-center gap-2">
            <StatusBadge status={job.status} />
            <h3 className="card-title mb-0 text-truncate">
              {job.position + 1}. {job.company || "—"}
            </h3>
          </div>
          <div className="text-secondary small">
            {job.title || "—"}
            {d.time ? ` · ${d.time}` : ""}
            {d.profile ? ` · ${d.profile}` : ""}
          </div>
        </div>
        <div className="btn-group btn-group-sm">
          <button
            type="button"
            className="btn"
            disabled={!onPrev}
            onClick={onPrev}
            aria-label="Previous job"
          >
            <i className="ti ti-chevron-left" aria-hidden="true" />
          </button>
          <span className="btn disabled">
            {position + 1} / {total}
          </span>
          <button
            type="button"
            className="btn"
            disabled={!onNext}
            onClick={onNext}
            aria-label="Next job"
          >
            <i className="ti ti-chevron-right" aria-hidden="true" />
          </button>
        </div>
      </div>
      {job.findings.length > 0 && (
        <ul className="list-group list-group-flush">
          {job.findings.map((f, i) => (
            <li key={i} className="list-group-item py-2">
              <Finding f={f} self={d.records?.[0]} />
            </li>
          ))}
        </ul>
      )}
      <div className="card-body">
        <div className="stage-scan-tabs mb-2" role="tablist" aria-label="Files">
          {tabs.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={t.key === active.key}
              className={`btn btn-sm ${t.key === active.key ? "btn-primary" : "btn-outline-secondary"}`}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>
        {missing.length > 0 && (
          <div className="small text-secondary mb-2">
            No {missing.join(" or ")} was matched to this job.
          </div>
        )}
        {active.file ? (
          <FileViewer scanId={scan.id} file={active.file} />
        ) : (
          <TrackerTab details={d} />
        )}
        <div className="row g-3 mt-1">
          <div className="col-lg-6">
            <Checklist key={active.key} kind={active.kind} job={job} />
          </div>
          <div className="col-lg-6">
            <VerdictBox scan={scan} job={job} onNext={onNext} />
          </div>
        </div>
      </div>
    </div>
  );
}

function FileViewer({ scanId, file }: { scanId: string; file: ScanFileRef }) {
  const link = useQuery({
    queryKey: ["review-file", scanId, file.area, file.rel],
    queryFn: () => api.reviewFileLink(scanId, file.area, file.rel),
    staleTime: 20 * 60 * 1000,
    retry: false,
  });
  if (link.isLoading)
    return <div className="stage-scan-viewer stage-scan-viewer-empty">Loading…</div>;
  if (link.error) return <ErrorBox error={link.error} />;
  const data = link.data!;
  return (
    <div>
      {data.kind === "image" ? (
        <div className="stage-scan-viewer stage-scan-viewer-image">
          <img src={data.url} alt={data.name} />
        </div>
      ) : data.kind === "download" ? (
        <div className="stage-scan-viewer stage-scan-viewer-empty">
          Word files can't be shown in the browser.
          <a className="btn btn-sm ms-2" href={data.url}>
            Download {data.name}
          </a>
        </div>
      ) : (
        <iframe
          className="stage-scan-viewer"
          src={data.url}
          title={data.name}
          // Captures are other companies' web pages: no scripts, forms or
          // navigation. PDFs need Chrome's viewer, which a sandbox blocks;
          // the server sends them with their own safe headers.
          sandbox={data.kind === "html" || data.kind === "text" ? "" : undefined}
          referrerPolicy="no-referrer"
        />
      )}
      <div className="d-flex justify-content-between small text-secondary mt-1 gap-2">
        <span className="text-truncate">{data.name}</span>
        <a href={data.url} target="_blank" rel="noreferrer noopener" className="text-nowrap">
          Open in a new tab <i className="ti ti-external-link" aria-hidden="true" />
        </a>
      </div>
    </div>
  );
}

function TrackerTab({ details: d }: { details: Row }) {
  return (
    <div className="stage-scan-tracker">
      <Section title={`Tracker (${(d.records || []).length})`}>
        {(d.records || []).map((r: Row) => (
          <div key={r.id} className="small mb-2">
            <div>
              {r.time} · {r.company} · {r.title} · {r.status}
            </div>
            {r.link ? (
              <a
                href={safeUrl(r.link)}
                target="_blank"
                rel="noreferrer noopener"
                className="text-break"
              >
                {r.link}
              </a>
            ) : (
              <span className="text-secondary">No link</span>
            )}
            {r.notes && <div className="text-secondary">Notes: {r.notes}</div>}
          </div>
        ))}
      </Section>
      {d.folder && (
        <Section title="Resume folder">
          <div className="small text-break">{d.folder}</div>
          {(d.files || []).map((f: Row) => (
            <div key={f.rel} className="small text-secondary">
              {f.kind === "cover" ? "Cover letter" : f.kind === "resume" ? "Resume" : "Other"}
              {f.pages ? ` · ${f.pages} page${f.pages === 1 ? "" : "s"}` : ""}
              {f.page_size ? ` · ${f.page_size}` : ""}
              {f.made_at ? ` · made ${f.made_at}` : ""}
            </div>
          ))}
        </Section>
      )}
      {(d.captures || []).length > 0 && (
        <Section title="Captures">
          {(d.captures || []).map((c: Row) => (
            <div key={c.rel} className="small mb-1 text-break">
              {c.saved ? `${c.saved} · ` : ""}
              {c.title || c.rel}
              {c.confirmation && (
                <span className="badge bg-success-lt text-success ms-1">Confirmation</span>
              )}
              {c.attached?.length > 0 && (
                <span className="text-secondary"> · attached {c.attached.join(", ")}</span>
              )}
            </div>
          ))}
        </Section>
      )}
      <Answers captures={d.captures || []} open />
    </div>
  );
}

const CHECKS: Record<Tab["kind"], (job: ScanJob) => string[]> = {
  resume: (job) => [
    `Name, email, phone and LinkedIn are the candidate's${job.details?.profile ? ` (${job.details.profile})` : ""}`,
    "Skills, employers, dates and degrees are true. Nothing invented",
    `Tailored to ${job.company || "this job"}: the summary and skills fit the posting`,
    "Layout: no broken lines, empty sections or odd page breaks",
  ],
  cover: (job) => [
    `Names ${job.company || "the company"} and the ${job.title || "job"} role`,
    "Nothing left over from another job",
    "Plain and specific, not generic AI wording",
  ],
  capture: (job) => [
    `It's this job: ${job.company || "the company"}, ${job.title || "the role"}`,
    "Every field is filled, and each answer is in the right box",
    "Authorization, sponsorship, salary and start date match the candidate brief",
    "No question shows the job is onsite or hybrid",
    "Attention-check questions are answered correctly",
    "The attached resume is this job's tailored resume",
    'No assessment or e-signature done, and no "Referral" answer',
  ],
  tracker: () => [
    "Company and title match the posting",
    "The link opens this job (the Jobright link is preferred)",
    "Withdrawn only with a reason",
  ],
};

function Checklist({ kind, job }: { kind: Tab["kind"]; job: ScanJob }) {
  const [done, setDone] = useState<Set<number>>(new Set());
  const items = CHECKS[kind](job);
  return (
    <div>
      <div className="stage-detail-section-label mb-2">Things to check</div>
      {items.map((text, i) => (
        <label key={i} className="form-check small mb-1">
          <input
            type="checkbox"
            className="form-check-input"
            checked={done.has(i)}
            onChange={() =>
              setDone((prev) => {
                const next = new Set(prev);
                if (next.has(i)) next.delete(i);
                else next.add(i);
                return next;
              })
            }
          />
          <span className="form-check-label">{text}</span>
        </label>
      ))}
      <div className="form-hint">A guide only; ticks aren't saved.</div>
    </div>
  );
}

function VerdictBox({
  scan,
  job,
  onNext,
}: {
  scan: ReviewScan;
  job: ScanJob;
  onNext?: () => void;
}) {
  const toast = useToast();
  const client = useQueryClient();
  const [verdict, setVerdict] = useState<ScanVerdict | null>(job.verdict);
  const [note, setNote] = useState(job.note || "");
  const dirty = verdict !== job.verdict || note.trim() !== (job.note || "");
  const save = useMutation({
    mutationFn: (_advance: boolean) => api.saveJobVerdict(scan.id, job.id, { verdict, note }),
    onSuccess: (saved, advance) => {
      client.setQueryData<{ scan: ReviewScan | null }>(CURRENT_KEY, (old) =>
        old?.scan
          ? {
              scan: {
                ...old.scan,
                jobs: (old.scan.jobs || []).map((j) =>
                  j.id === saved.id
                    ? {
                        ...j,
                        verdict: saved.verdict,
                        note: saved.note,
                        reviewed_at: saved.reviewed_at,
                      }
                    : j,
                ),
              },
            }
          : old,
      );
      if (advance && onNext) onNext();
      else toast.success("Saved");
    },
    onError: (e: Error) => toast.error(e.message),
  });
  if (!scan.can_edit)
    return (
      <div>
        <div className="stage-detail-section-label mb-2">Verdict</div>
        <VerdictChip verdict={job.verdict} />
        {job.note && <p className="small mt-2 mb-0">{job.note}</p>}
      </div>
    );
  return (
    <div>
      <div className="stage-detail-section-label mb-2">Your verdict</div>
      <div className="btn-group w-100 mb-2" role="group" aria-label="Verdict">
        {(["pass", "issue", "fail"] as const).map((v) => (
          <button
            key={v}
            type="button"
            aria-pressed={verdict === v}
            className={`btn ${verdict === v ? `btn-${VERDICT[v].tone}` : `btn-outline-${VERDICT[v].tone}`}`}
            onClick={() => setVerdict(verdict === v ? null : v)}
          >
            <i className={`ti ${VERDICT[v].icon} me-1`} aria-hidden="true" />
            {VERDICT[v].label}
          </button>
        ))}
      </div>
      <label className="form-label small mb-1" htmlFor={`note-${job.id}`}>
        Note for the report (optional)
      </label>
      <textarea
        id={`note-${job.id}`}
        className="form-control mb-2"
        rows={3}
        maxLength={2000}
        placeholder="What should the supporter fix?"
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      <div className="d-flex gap-2 justify-content-end">
        <button
          type="button"
          className="btn"
          disabled={!dirty || save.isPending}
          onClick={() => save.mutate(false)}
        >
          Save
        </button>
        <button
          type="button"
          className="btn btn-primary"
          disabled={save.isPending || (!dirty && !onNext)}
          onClick={() => (dirty ? save.mutate(true) : onNext?.())}
        >
          {onNext ? (dirty ? "Save and next" : "Next job") : "Save"}
        </button>
      </div>
    </div>
  );
}

const VERDICT: Record<ScanVerdict, { label: string; tone: string; icon: string }> = {
  pass: { label: "Pass", tone: "success", icon: "ti-check" },
  issue: { label: "Issue", tone: "warning", icon: "ti-alert-triangle" },
  fail: { label: "Fail", tone: "danger", icon: "ti-x" },
};

function VerdictChip({ verdict }: { verdict: ScanVerdict | null }) {
  if (!verdict)
    return <span className="badge bg-secondary-lt text-secondary flex-shrink-0">Not reviewed</span>;
  const v = VERDICT[verdict];
  return (
    <span className={`badge bg-${v.tone} text-${v.tone}-fg flex-shrink-0`}>
      <i className={`ti ${v.icon} me-1`} aria-hidden="true" />
      {v.label}
    </span>
  );
}

function DayNote({ scan }: { scan: ReviewScan }) {
  const toast = useToast();
  const client = useQueryClient();
  const [note, setNote] = useState(scan.reviewer_note || "");
  const save = useMutation({
    mutationFn: () => api.saveScanNote(scan.id, note),
    onSuccess: (data) => {
      client.setQueryData<{ scan: ReviewScan | null }>(CURRENT_KEY, (old) =>
        old?.scan ? { scan: { ...old.scan, reviewer_note: data.reviewer_note } } : old,
      );
      toast.success("Note saved");
    },
    onError: (e: Error) => toast.error(e.message),
  });
  if (!scan.can_edit && !scan.reviewer_note) return null;
  return (
    <div className="card mb-3">
      <div className="card-header">
        <h3 className="card-title mb-0">Note for the whole day</h3>
      </div>
      <div className="card-body">
        <textarea
          className="form-control"
          rows={3}
          maxLength={5000}
          aria-label="Note for the whole day"
          placeholder="Feedback for the supporter, printed at the top of the report"
          value={note}
          readOnly={!scan.can_edit}
          onChange={(e) => setNote(e.target.value)}
        />
      </div>
      {scan.can_edit && (
        <div className="card-footer text-end">
          <button
            type="button"
            className="btn"
            disabled={save.isPending || note.trim() === (scan.reviewer_note || "")}
            onClick={() => save.mutate()}
          >
            Save note
          </button>
        </div>
      )}
    </div>
  );
}

function Answers({ captures, open = false }: { captures: Row[]; open?: boolean }) {
  const rows = useMemo(() => {
    const seen = new Set<string>();
    const out: [string, string][] = [];
    for (const c of captures)
      for (const [q, a] of c.answers || [])
        if (!seen.has(q)) {
          seen.add(q);
          out.push([q, a]);
        }
    return out;
  }, [captures]);
  if (!rows.length) return null;
  return (
    <details className="mt-3" open={open}>
      <summary className="small fw-medium">
        Form answers read from the captures ({rows.length})
      </summary>
      <div className="table-responsive mt-2">
        <table className="table table-sm small mb-0">
          <tbody>
            {rows.map(([q, a]) => (
              <tr key={q}>
                <td className="text-secondary" style={{ width: "60%" }}>
                  {q}
                </td>
                <td>{a || <span className="text-secondary">not readable</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div className="stage-detail-section-label mb-1">{title}</div>
      {children}
    </div>
  );
}

function Finding({ f, self }: { f: ScanFinding; self?: Row }) {
  const lvl = LEVEL[f.level] || LEVEL.info;
  return (
    <>
      <span className="d-flex gap-2 align-items-start">
        <span className={`badge bg-${lvl.tone}-lt text-${lvl.tone} flex-shrink-0`}>
          {lvl.label}
        </span>
        <span>{f.text}</span>
      </span>
      {f.matches && f.matches.length > 0 && (
        <MatchTable
          matches={f.matches}
          self={self}
          by={f.code === "same_link" ? "link" : "company"}
        />
      )}
    </>
  );
}

// This job's values above each matching record's, so the reviewer can
// decide whether it is the same job or end client.
function MatchTable({
  matches,
  self,
  by,
}: {
  matches: ScanMatch[];
  self?: Row;
  by: "link" | "company";
}) {
  return (
    <div className="stage-scan-matches mt-2 small">
      {self && (
        <MatchRow
          self
          by={by}
          when={`This job${self.time ? ` · ${self.time}` : ""}`}
          company={self.company}
          title={self.title}
          status={self.status}
          link={self.link}
        />
      )}
      {matches.map((m, i) => (
        <MatchRow
          key={i}
          by={by}
          when={m.time ? `Today ${m.time}` : m.date}
          ago={m.days_ago ? `${m.days_ago} days earlier` : ""}
          who={m.who}
          company={m.company}
          title={m.title}
          status={m.status}
          link={m.link}
          how={m.how}
        />
      ))}
    </div>
  );
}

function MatchRow({
  self,
  by,
  when,
  ago,
  who,
  company,
  title,
  status,
  link,
  how,
}: {
  self?: boolean;
  by: "link" | "company";
  when: string;
  ago?: string;
  who?: string;
  company: string;
  title: string;
  status: string;
  link: string;
  how?: string;
}) {
  return (
    <div className={`stage-scan-match${self ? " is-self" : ""}`}>
      <div>
        <div className="fw-medium">{when}</div>
        {ago && <div className="text-secondary">{ago}</div>}
        {who && <div className="text-secondary">by {who}</div>}
      </div>
      <div>
        <div>
          <span className={by === "company" ? "fw-bold" : undefined}>
            {company || "No company"}
          </span>
          {title ? ` · ${title}` : ""}
          {status && <span className="text-secondary"> · {status}</span>}
          {how && <span className="badge bg-azure-lt text-azure ms-2">{how}</span>}
        </div>
        <div className={`stage-scan-match-link${by === "link" ? " fw-medium" : ""}`}>
          {link ? (
            <a href={safeUrl(link)} target="_blank" rel="noreferrer noopener">
              {link.replace(/^https?:\/\/(www\.)?/i, "")}
            </a>
          ) : (
            <span className="text-secondary">No link</span>
          )}
        </div>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const lvl = LEVEL[status] || LEVEL.info;
  return (
    <span className={`badge bg-${lvl.tone}-lt text-${lvl.tone} stage-scan-status`}>
      {lvl.label}
    </span>
  );
}

function CancelButton({ scan, label = "Cancel scan" }: { scan: ReviewScan; label?: string }) {
  const toast = useToast();
  const client = useQueryClient();
  const [asking, setAsking] = useState(false);
  const cancel = useMutation({
    mutationFn: () => api.cancelReviewScan(scan.id),
    onSuccess: () => {
      toast.success("Scan cancelled. The uploaded files were deleted.");
      client.invalidateQueries({ queryKey: CURRENT_KEY });
    },
    onError: (e: Error) => toast.error(e.message),
  });
  return (
    <>
      <button type="button" className="btn btn-ghost-danger" onClick={() => setAsking(true)}>
        {label}
      </button>
      {asking && (
        <Modal
          title="Cancel this scan?"
          size="sm"
          onClose={() => setAsking(false)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setAsking(false)}>
                Keep it
              </button>
              <button
                type="button"
                className="btn btn-danger"
                disabled={cancel.isPending}
                onClick={() => cancel.mutate()}
              >
                Cancel scan
              </button>
            </>
          }
        >
          <p className="mb-0">Nothing is saved and the uploaded files are deleted.</p>
        </Modal>
      )}
    </>
  );
}

function ReportCard({ text, fileName }: { text: string; fileName: string }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  if (!text) return null;
  return (
    <div className="card mb-3">
      <div className="card-header">
        <h3 className="card-title mb-0">Report</h3>
        <div className="ms-auto d-flex gap-2">
          <button type="button" className="btn btn-sm" onClick={() => setOpen(!open)}>
            {open ? "Hide" : "Show"}
          </button>
          <ReportButtons
            text={text}
            fileName={fileName}
            onCopied={() => toast.success("Report copied")}
          />
        </div>
      </div>
      {open && <pre className="stage-scan-report">{text}</pre>}
    </div>
  );
}

function ReportButtons({
  text,
  fileName,
  onCopied,
}: {
  text: string;
  fileName: string;
  onCopied: () => void;
}) {
  return (
    <>
      <button
        type="button"
        className="btn btn-sm"
        onClick={() => navigator.clipboard?.writeText(text).then(onCopied)}
      >
        <i className="ti ti-copy me-1" aria-hidden="true" />
        Copy
      </button>
      <button type="button" className="btn btn-sm" onClick={() => download(text, fileName)}>
        <i className="ti ti-download me-1" aria-hidden="true" />
        Download
      </button>
    </>
  );
}

// ----- options form pieces -----

function OptionGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="col-lg-6">
      <fieldset>
        <legend className="stage-detail-section-label mb-2">{title}</legend>
        <div className="d-grid gap-3">{children}</div>
      </fieldset>
    </div>
  );
}

function Switch({
  id,
  label,
  hint,
  checked,
  onChange,
}: {
  id: string;
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div>
      <label className="form-check form-switch mb-0" htmlFor={id}>
        <input
          id={id}
          className="form-check-input"
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span className="form-check-label">{label}</span>
      </label>
      {hint && <div className="form-hint ms-5">{hint}</div>}
    </div>
  );
}

function NumberField({
  id,
  label,
  hint,
  value,
  min,
  max,
  onChange,
}: {
  id: string;
  label: string;
  hint?: string;
  value: number;
  min: number;
  max: number;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <label className="form-label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        type="number"
        className="form-control"
        min={min}
        max={max}
        value={Number.isFinite(value) ? value : ""}
        onChange={(e) => onChange(e.target.value === "" ? NaN : Number(e.target.value))}
      />
      {hint && <div className="form-hint">{hint}</div>}
    </div>
  );
}

function ListField({
  id,
  label,
  hint,
  value,
  onChange,
}: {
  id: string;
  label: string;
  hint: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div>
      <label className="form-label" htmlFor={id}>
        {label}
      </label>
      <textarea
        id={id}
        className="form-control"
        rows={4}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <div className="form-hint">{hint}</div>
    </div>
  );
}

// ----- history -----

function History() {
  const [supporter, setSupporter] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [openId, setOpenId] = useState<string | null>(null);
  const supporters = useQuery({ queryKey: ["review-supporters"], queryFn: api.reviewSupporters });
  const params = { supporter_id: supporter || undefined, limit: size, offset: (page - 1) * size };
  const list = useQuery({
    queryKey: ["review-history", params],
    queryFn: () => api.reviewScans(params),
    placeholderData: keepPreviousData,
  });
  const rows = list.data?.items || [];
  return (
    <div className="card">
      <div className="card-header flex-wrap gap-2">
        <h3 className="card-title mb-0">Saved reports</h3>
        <select
          className="form-select form-select-sm w-auto ms-auto"
          aria-label="Supporter"
          value={supporter}
          onChange={(e) => {
            setSupporter(e.target.value);
            setPage(1);
          }}
        >
          <option value="">All supporters</option>
          {(supporters.data || []).map((s) => (
            <option key={s.id} value={s.id}>
              {s.name || s.email}
            </option>
          ))}
        </select>
      </div>
      {list.error ? (
        <div className="card-body">
          <ErrorBox error={list.error} />
        </div>
      ) : list.isLoading ? (
        <div className="card-body">
          <Loading />
        </div>
      ) : (
        <div className="table-responsive">
          <table className="table table-vcenter card-table">
            <thead>
              <tr>
                <th>Day</th>
                <th>Supporter</th>
                <th>Reviewer</th>
                <th className="text-end">Jobs</th>
                <th>Result</th>
                <th>Finished</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const st = r.summary?.status || {};
                return (
                  <tr
                    key={r.id}
                    className="stage-interactive-row"
                    tabIndex={0}
                    style={{ cursor: "pointer" }}
                    onClick={() => setOpenId(r.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        setOpenId(r.id);
                      }
                    }}
                  >
                    <td className="text-nowrap">{longDay(r.work_date)}</td>
                    <td>{r.supporter && <UserIdentity person={userPerson(r.supporter)} />}</td>
                    <td>{r.reviewer?.name || r.reviewer?.email}</td>
                    <td className="text-end">{r.summary?.jobs ?? "—"}</td>
                    <td>
                      <div className="d-flex flex-wrap gap-1">
                        {(["fail", "issue", "check", "ok"] as const).map((k) =>
                          st[k] ? (
                            <span
                              key={k}
                              className={`badge bg-${LEVEL[k].tone}-lt text-${LEVEL[k].tone}`}
                            >
                              {st[k]} {LEVEL[k].label.toLowerCase()}
                            </span>
                          ) : null,
                        )}
                        {r.status === "expired" && (
                          <span className="badge bg-secondary-lt">Expired before finishing</span>
                        )}
                      </div>
                    </td>
                    <td className="text-nowrap">{whenUtc(r.finished_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!rows.length && (
            <div className="text-secondary text-center py-4">No saved reports yet.</div>
          )}
        </div>
      )}
      <NumberedPager
        page={page}
        total={list.data?.total ?? 0}
        size={size}
        onChange={setPage}
        onSizeChange={(n) => {
          setSize(n);
          setPage(1);
        }}
      />
      {openId && <ReportModal id={openId} onClose={() => setOpenId(null)} />}
    </div>
  );
}

function ReportModal({ id, onClose }: { id: string; onClose: () => void }) {
  const toast = useToast();
  const scan = useQuery({ queryKey: ["review-scan", id], queryFn: () => api.reviewScan(id) });
  const data = scan.data;
  return (
    <Modal
      title={
        data ? `${data.supporter?.name || "Supporter"} · ${longDay(data.work_date)}` : "Report"
      }
      size="xl"
      onClose={onClose}
      footer={
        data?.report_text ? (
          <ReportButtons
            text={data.report_text}
            fileName={reportName(data)}
            onCopied={() => toast.success("Report copied")}
          />
        ) : undefined
      }
    >
      {scan.isLoading && <Loading />}
      {scan.error && <ErrorBox error={scan.error} />}
      {data &&
        (data.report_text ? (
          <pre className="stage-scan-report mb-0">{data.report_text}</pre>
        ) : (
          <p className="text-secondary mb-0">This scan has no report.</p>
        ))}
    </Modal>
  );
}

// ----- summary report -----

const SUMMARY_RANGES = [
  ["7", "Last 7 days", 7],
  ["30", "Last 30 days", 30],
  ["90", "Last 90 days", 90],
] as const;

function Summary() {
  const toast = useToast();
  const today = localDate();
  const [supporter, setSupporter] = useState("");
  const [from, setFrom] = useState(shiftDate(today, -29));
  const [to, setTo] = useState(today);
  const supporters = useQuery({ queryKey: ["review-supporters"], queryFn: api.reviewSupporters });
  const valid = Boolean(from && to && from <= to);
  const params = { supporter_id: supporter || undefined, date_from: from, date_to: to };
  const report = useQuery({
    queryKey: ["review-summary", params],
    queryFn: () => api.reviewSummary(params),
    enabled: valid,
    placeholderData: keepPreviousData,
  });
  const data = report.data;
  const team = data?.team;
  const rows: Row[] = data?.supporters || [];
  return (
    <>
      <div className="card stage-perf-filter-card mb-3">
        <div className="card-body">
          <div className="stage-perf-filters">
            <div className="stage-perf-window stage-dash-period" aria-label="Period">
              {SUMMARY_RANGES.map(([key, label, days]) => {
                const start = shiftDate(today, -(days - 1));
                const active = from === start && to === today;
                return (
                  <button
                    key={key}
                    type="button"
                    className={`btn btn-sm ${active ? "active" : "btn-ghost-secondary"}`}
                    onClick={() => {
                      setFrom(start);
                      setTo(today);
                    }}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
            <div className="stage-perf-scope">
              <label>
                <span>Supporter</span>
                <select
                  className="form-select form-select-sm"
                  value={supporter}
                  onChange={(e) => setSupporter(e.target.value)}
                >
                  <option value="">All supporters</option>
                  {(supporters.data || []).map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name || s.email}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>From</span>
                <input
                  type="date"
                  className="form-control form-control-sm"
                  value={from}
                  max={to || today}
                  onChange={(e) => setFrom(e.target.value)}
                />
              </label>
              <label>
                <span>To</span>
                <input
                  type="date"
                  className="form-control form-control-sm"
                  value={to}
                  min={from}
                  max={today}
                  onChange={(e) => setTo(e.target.value)}
                />
              </label>
            </div>
          </div>
        </div>
      </div>
      {!valid && <div className="alert alert-warning">Pick a start date before the end date.</div>}
      {report.error && <ErrorBox error={report.error} />}
      {report.isLoading && <Loading />}
      {team && !team.days && (
        <div className="card">
          <div className="card-body text-secondary text-center py-5">
            No finished reviews in this period. Reviews count once they're finished.
          </div>
        </div>
      )}
      {team && team.days > 0 && (
        <>
          <div className="row row-cards mb-3">
            <SummaryStat
              label="Reviewed days"
              icon="ti-calendar-check"
              value={team.days}
              note={`${team.supporters} supporter${team.supporters === 1 ? "" : "s"}`}
            />
            <SummaryStat
              label="Jobs checked"
              icon="ti-briefcase"
              value={team.jobs}
              note={`from ${team.records} tracker records`}
            />
            <SummaryStat
              label="Scan fails"
              icon="ti-alert-octagon"
              tone="danger"
              value={`${team.fail_rate}%`}
              note={`${team.status.fail} of ${team.jobs} jobs`}
            />
            <SummaryStat
              label="Your verdicts"
              icon="ti-gavel"
              tone="success"
              value={`${team.verdicts.pass} pass`}
              note={`${team.verdicts.issue} issue · ${team.verdicts.fail} fail · ${team.verdicts.none} not reviewed`}
            />
          </div>
          <div className="card mb-3">
            <div className="card-header flex-wrap gap-1">
              <h3 className="card-title mb-0">By supporter</h3>
              {team.top_problems.length > 0 && (
                <span className="ms-md-auto text-secondary small">
                  Most common for everyone:{" "}
                  {team.top_problems
                    .slice(0, 3)
                    .map((p: Row) => `${p.label} (${p.count})`)
                    .join(", ")}
                </span>
              )}
            </div>
            <div className="table-responsive">
              <table className="table table-vcenter card-table">
                <thead>
                  <tr>
                    <th>Supporter</th>
                    <th className="text-end">Days</th>
                    <th className="text-end">Jobs</th>
                    <th>Scan results</th>
                    <th>Your verdicts</th>
                    <th>Most common problems</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id}>
                      <td className="fw-medium">{r.name}</td>
                      <td className="text-end">{r.days}</td>
                      <td className="text-end">{r.jobs}</td>
                      <td>
                        <div className="d-flex flex-wrap gap-1">
                          {(["fail", "issue", "check", "ok"] as const).map((k) =>
                            r.status[k] ? (
                              <span
                                key={k}
                                className={`badge bg-${LEVEL[k].tone}-lt text-${LEVEL[k].tone}`}
                              >
                                {r.status[k]} {LEVEL[k].label.toLowerCase()}
                              </span>
                            ) : null,
                          )}
                        </div>
                      </td>
                      <td className="small text-nowrap">
                        {r.verdicts.pass} pass · {r.verdicts.issue} issue · {r.verdicts.fail} fail
                        {r.verdicts.none ? (
                          <span className="text-secondary"> · {r.verdicts.none} not reviewed</span>
                        ) : null}
                      </td>
                      <td className="small">
                        {r.top_problems.length
                          ? r.top_problems.map((p: Row) => `${p.label} (${p.count})`).join(", ")
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div className="card">
            <div className="card-header">
              <h3 className="card-title mb-0">Summary report</h3>
              <div className="ms-auto d-flex gap-2">
                <ReportButtons
                  text={data.text}
                  fileName={`${data.date_from}_to_${data.date_to}_review_summary.txt`}
                  onCopied={() => toast.success("Summary copied")}
                />
              </div>
            </div>
            <pre className="stage-scan-report rounded-0">{data.text}</pre>
          </div>
        </>
      )}
    </>
  );
}

function SummaryStat({
  label,
  icon,
  value,
  note,
  tone = "primary",
}: {
  label: string;
  icon: string;
  value: ReactNode;
  note: string;
  tone?: string;
}) {
  return (
    <div className="col-sm-6 col-lg-3">
      <div className="card">
        <div className="card-body d-flex justify-content-between align-items-start">
          <div>
            <div className="text-secondary">{label}</div>
            <div className="stage-metric-value">{value}</div>
            <div className="text-secondary small mt-1">{note}</div>
          </div>
          <span className={`avatar bg-${tone}-lt text-${tone}`}>
            <i className={`ti ${icon}`} aria-hidden="true" />
          </span>
        </div>
      </div>
    </div>
  );
}

// ----- helpers -----

function splitLines(value: string) {
  return value
    .split(/\n/)
    .map((v) => v.trim())
    .filter(Boolean);
}

function longDay(value: string) {
  const d = new Date(`${value}T12:00:00`);
  return Number.isNaN(d.getTime())
    ? value
    : d.toLocaleDateString(undefined, {
        weekday: "short",
        month: "short",
        day: "numeric",
        year: "numeric",
      });
}

function fmtBytes(n: number) {
  return n >= 1048576
    ? `${(n / 1048576).toFixed(1)} MB`
    : `${Math.max(1, Math.round(n / 1024))} KB`;
}

function safeUrl(href: string) {
  return /^https?:\/\//i.test(href) ? href : `https://${href}`;
}

function reportName(scan: ReviewScan) {
  const who = (scan.supporter?.name || "supporter").replace(/[^A-Za-z0-9]+/g, "_");
  return `${scan.work_date}_review_${who}.txt`;
}

function download(text: string, fileName: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
