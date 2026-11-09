import { accessTokenNow, authFetch, recover, renew } from "./session";
const BASE = "/api/v1";
export type Row = Record<string, any>;
export type CurrentUser = {
  id: string;
  email: string;
  name: string;
  role: string;
  avatar_url?: string | null;
  can_review_work_plans?: boolean;
};
export type TrainingLesson = {
  id: string;
  slug: string;
  title: string;
  body_md: string;
  position: number;
  updated_at: string | null;
};
export type TrainingCourse = { lessons: TrainingLesson[]; completed: string[]; can_edit: boolean };
export type TrainingAsset = {
  name: string;
  content_type: string;
  size: number;
  url: string;
  updated_at: string | null;
};
export type LessonInput = { title: string; slug?: string; body_md: string };
export type TrainingDocsUpdate = {
  lessons: {
    slug: string;
    title: string;
    status: "new" | "changed" | "moved" | "same";
    edited_on_site: boolean;
  }[];
  kept: { slug: string; title: string }[];
  images: { new: string[]; changed: string[]; same: number };
  up_to_date: boolean;
};
export type Announcement = {
  id: string;
  title: string;
  body_md: string;
  created_at: string | null;
  updated_at: string | null;
};
export type MemberAnnouncement = Announcement & { read: boolean };
export type MemberAnnouncements = { items: MemberAnnouncement[]; unread_count: number };
export type ManagedAnnouncement = Announcement & { read_count: number };
export type ManagedAnnouncements = { items: ManagedAnnouncement[]; member_count: number };
export type AnnouncementInput = { title: string; body_md: string };
export type ScanLevel = "fail" | "issue" | "check" | "info";
export type ScanFileRef = { area: "resumes" | "snapshots"; rel: string; label?: string };
export type ScanMatch = {
  date: string;
  time?: string;
  days_ago?: number | null;
  who: string;
  company: string;
  title: string;
  link: string;
  status: string;
  how: string;
};
export type ScanFinding = {
  code?: string;
  level: ScanLevel;
  area?: string;
  text: string;
  files?: ScanFileRef[];
  matches?: ScanMatch[];
};
export type ScanVerdict = "pass" | "issue" | "fail";
export type ScanFileLink = {
  url: string;
  kind: "pdf" | "html" | "image" | "text" | "download";
  name: string;
};
export type ScanOptions = {
  duplicate_window_days: number;
  window_includes_last_day: boolean;
  require_jobright_link: boolean;
  allow_jobright_apply: boolean;
  excluded_sites: string[];
  staffing_agencies: string[];
  leftover_markers: string[];
  resume_max_pages: number;
  cover_letter_max_pages: number;
  flag_consent_answers: boolean;
  check_answer_consistency: boolean;
  check_capture_order: boolean;
  pace_minutes: number;
};
export type ScanPerson = { id: string; name: string; email: string; avatar_url?: string | null };
export type ScanJob = {
  id: number;
  position: number;
  company: string;
  title: string;
  status: "fail" | "issue" | "check" | "ok";
  verdict: ScanVerdict | null;
  note: string;
  reviewed_at?: string | null;
  details: Row;
  findings: ScanFinding[];
};
export type ReviewScan = {
  id: string;
  status:
    "uploaded" | "queued" | "scanning" | "ready" | "finished" | "cancelled" | "failed" | "expired";
  progress: number;
  stage: string;
  error: string;
  work_date: string;
  created_at: string | null;
  scanned_at: string | null;
  finished_at: string | null;
  files_deleted: boolean;
  supporter: ScanPerson | null;
  reviewer: ScanPerson | null;
  options: ScanOptions;
  uploads: Row;
  summary: Row;
  day_findings: ScanFinding[];
  queue_position: number;
  can_edit: boolean;
  reviewer_note: string;
  jobs?: ScanJob[];
  report_text?: string;
};
type Opt = RequestInit & { json?: unknown; auth?: boolean };
export async function request<T = any>(path: string, options: Opt = {}): Promise<T> {
  const { json, auth = true, headers: extra, ...init } = options;
  const headers = new Headers(extra);
  let body = init.body;
  if (json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(json);
  }
  const res = await (auth ? authFetch : fetch)(BASE + path, { ...init, headers, body });
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try {
      const d = await res.json();
      if (typeof d.detail === "string") msg = d.detail;
    } catch {}
    throw new Error(msg);
  }
  if (res.status === 204) return null as T;
  return (res.headers.get("content-type") || "").includes("json")
    ? res.json()
    : (res.text() as Promise<T>);
}
const q = (v: Row = {}) => {
  const s = new URLSearchParams();
  Object.entries(v).forEach(([k, x]) => {
    if (x !== undefined && x !== null && x !== "") s.set(k, String(x));
  });
  return s.size ? `?${s}` : "";
};
const enc = encodeURIComponent;
// Real IANA zone (DST-aware), sent so a member's own account can pick up her
// timezone automatically the first time she opens her Dashboard/Performance —
// see _learn_member_timezone in server/routers/team.py.
const browserTimezone = () => {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
};
export const api = {
  me: () => request<CurrentUser>("/users/me"),
  updateMe: (d: Row) => request<CurrentUser>("/users/me", { method: "PATCH", json: d }),
  password: (d: Row) => request("/users/me/password", { method: "POST", json: d }),
  avatar: (f: File) => {
    const b = new FormData();
    b.append("file", f);
    return request<CurrentUser>("/users/me/avatar", { method: "POST", body: b });
  },
  removeAvatar: () => request<CurrentUser>("/users/me/avatar", { method: "DELETE" }),
  overview: () => request<Row>("/admin/api/overview"),
  profilesGeo: () => request<Row>("/admin/api/profiles/geo"),
  users: () => request<Row[]>("/admin/api/users"),
  // Users page only: includes archived accounts.
  allUsers: () => request<Row[]>("/admin/api/users?include_archived=true"),
  archiveUser: (id: string, d: { supporters?: string; move_to?: string | null } = {}) =>
    request<Row>(`/admin/api/users/${enc(id)}/archive`, { method: "POST", json: d }),
  restoreUser: (id: string) =>
    request<Row>(`/admin/api/users/${enc(id)}/restore`, { method: "POST" }),
  fleet: () => request<Row[]>("/admin/api/fleet"),
  signups: () => request<Row[]>("/admin/api/signups"),
  issueOtp: (id: string) =>
    request<Row>(`/admin/api/signups/${enc(id)}/issue-otp`, { method: "POST" }),
  rejectSignup: (id: string) => request(`/admin/api/signups/${enc(id)}/reject`, { method: "POST" }),
  userRole: (id: string, role: string) =>
    request(`/admin/api/users/${enc(id)}/set-role`, { method: "POST", json: { role } }),
  userTimezone: (id: string, timezone: string) =>
    request(`/admin/api/users/${enc(id)}/set-timezone`, { method: "POST", json: { timezone } }),
  userPassword: (id: string, password: string) =>
    request(`/admin/api/users/${enc(id)}/set-password`, { method: "POST", json: { password } }),
  deleteUser: (id: string) => request(`/admin/api/users/${enc(id)}`, { method: "DELETE" }),
  userData: (id: string) =>
    request<Row>(
      `/admin/api/users/${enc(id)}/data${q({ tz_offset: -new Date().getTimezoneOffset() })}`,
    ),
  deleteUserData: (id: string) => request(`/admin/api/users/${enc(id)}/data`, { method: "DELETE" }),
  profiles: () => request<Row[]>("/admin/profiles"),
  saveProfile: (k: string, d: Row) =>
    request(`/admin/profiles/${enc(k)}`, { method: "PUT", json: d }),
  deleteProfile: (k: string) => request(`/admin/profiles/${enc(k)}`, { method: "DELETE" }),
  createProfile: (d: Row) => request<Row>("/admin/profiles", { method: "POST", json: d }),
  importProfiles: (data: unknown) =>
    request<Row[]>("/admin/profiles/import", { method: "POST", json: data }),
  profileUsage: (k: string) => request<Row>(`/admin/profiles/${enc(k)}/usage`),
  setProfileAssignees: (k: string, userIds: string[]) =>
    request<Row>(`/admin/profiles/${enc(k)}/assignees`, {
      method: "PUT",
      json: { user_ids: userIds },
    }),
  archiveProfile: (k: string) =>
    request<Row>(`/admin/profiles/${enc(k)}/archive`, { method: "POST" }),
  restoreProfile: (k: string) =>
    request<Row>(`/admin/profiles/${enc(k)}/restore`, { method: "POST" }),
  userMember: (id: string, memberId: string | null) =>
    request(`/admin/api/users/${enc(id)}/set-member`, {
      method: "POST",
      json: { member_id: memberId },
    }),
  userWorkPlanReviewer: (id: string, enabled: boolean) =>
    request(`/admin/api/users/${enc(id)}/set-work-plan-reviewer`, {
      method: "POST",
      json: { enabled },
    }),
  memberSupporters: () => request<Row[]>("/team/member/supporters"),
  memberProfiles: () => request<Row[]>("/team/member/profiles"),
  createMemberProfile: (d: Row) =>
    request<Row>("/team/member/profiles", { method: "POST", json: d }),
  importMemberProfiles: (data: unknown) =>
    request<Row[]>("/team/member/profiles/import", { method: "POST", json: data }),
  archiveMemberProfile: (k: string) =>
    request(`/team/member/profiles/${enc(k)}/archive`, { method: "POST" }),
  setMemberProfileSupporters: (k: string, supporterIds: string[]) =>
    request<Row>(`/team/member/profiles/${enc(k)}/supporters`, {
      method: "PUT",
      json: { supporter_ids: supporterIds },
    }),
  setMemberAssignments: (assignments: Record<string, string[]>) =>
    request<Row[]>("/team/member/assignments", { method: "PUT", json: { assignments } }),
  supporterAvatar: (id: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<Row>(`/team/member/supporters/${enc(id)}/avatar`, { method: "POST", body });
  },
  removeSupporterAvatar: (id: string) =>
    request(`/team/member/supporters/${enc(id)}/avatar`, { method: "DELETE" }),
  supporterPassword: (id: string, password: string) =>
    request(`/team/member/supporters/${enc(id)}/set-password`, {
      method: "POST",
      json: { password },
    }),
  updateMemberProfile: (key: string, data: Row) =>
    request<Row>(`/team/member/profiles/${enc(key)}`, { method: "PUT", json: data }),
  licenseProducts: () => request<Row[]>("/license/products"),
  licenses: (product?: string) => request<Row[]>(`/license${q({ product })}`),
  createLicense: (d: Row) => request<Row>("/license", { method: "POST", json: d }),
  updateLicense: (id: string, d: Row) =>
    request<Row>(`/license/${enc(id)}`, { method: "PATCH", json: d }),
  extendLicense: (id: string, days: number) =>
    request<Row>(`/license/${enc(id)}/extend`, { method: "POST", json: { days } }),
  licenseAction: (id: string, a: "suspend" | "resume") =>
    request<Row>(`/license/${enc(id)}/${a}`, { method: "POST" }),
  deleteLicense: (id: string) => request(`/license/${enc(id)}`, { method: "DELETE" }),
  licenseMachines: (p: Row = {}) => request<Row[]>(`/license/machines${q(p)}`),
  releaseSeat: (seatId: number) =>
    request<Row>(`/license/seats/${enc(String(seatId))}/release`, { method: "POST" }),
  blockSeat: (seatId: number) =>
    request<Row>(`/license/seats/${enc(String(seatId))}/block`, { method: "POST" }),
  licenseEvents: (p: Row = {}) =>
    request<{ items: Row[]; total: number }>(`/license/events${q(p)}`),
  telemetrySummary: () => request<Row[]>("/admin/api/telemetry/summary"),
  telemetryRecent: () => request<Row[]>("/admin/api/telemetry/recent?limit=100"),
  errors: () => request<Row[]>("/admin/api/errors?limit=100"),
  syncActivity: (p: Row) => request<Row>(`/admin/api/sync/activity${q(p)}`),
  syncTrend: (p: Row) => request<Row>(`/admin/api/sync/trend${q(p)}`),
  profileData: (k: string) => request<Row>(`/admin/api/profiles/${enc(k)}/data`),
  reconcile: (k?: string) => request<Row>(`/admin/api/sync/reconcile${q({ profile_key: k })}`),
  repairScope: () => request<Row>("/admin/api/sync/repair-account-scope", { method: "POST" }),
  duplicates: (p: Row = {}) => request<Row>(`/admin/api/tracking/duplicates${q(p)}`),
  resolveDuplicates: (ids: string[]) =>
    request<Row>("/admin/api/tracking/duplicates/resolve", { method: "POST", json: { ids } }),
  metrics: (p: Row) =>
    request<Row>(`/admin/api/tracking/metrics${q({ ...p, timezone: browserTimezone() })}`),
  observability: (p: Row) =>
    request<Row>(`/team/observability${q({ ...p, timezone: browserTimezone() })}`),
  adminApplications: (p: Row) => request<Row>(`/admin/api/applications${q(p)}`),
  teamApplications: (p: Row) => request<Row>(`/team/applications${q(p)}`),
  application: (id: string) => request<Row>(`/admin/api/applications/${enc(id)}`),
  restoreApplications: (ids: string[]) =>
    request<Row>("/admin/api/applications/restore", { method: "POST", json: { ids } }),
  adminInterviews: (p: Row) => request<Row>(`/admin/api/interviews${q(p)}`),
  restoreInterviews: (ids: string[]) =>
    request<Row>("/admin/api/interviews/restore", { method: "POST", json: { ids } }),
  colleagues: () =>
    request<Row>(
      `/team/colleagues${q({
        tz_offset: -new Date().getTimezoneOffset(),
        timezone: browserTimezone(),
      })}`,
    ),
  interviews: (p: Row) => request<Row[]>(`/interviews/${q(p)}`),
  interviewPipeline: (p: Row) => request<Row>(`/interviews/pipeline${q(p)}`),
  interviewInsights: (p: Row) => request<Row>(`/interviews/insights${q(p)}`),
  updateApplicationStatus: (id: string, status: string) =>
    request<Row>(`/applications/${enc(id)}/status`, { method: "PATCH", json: { status } }),
  workOverview: (w: string) => request<Row>(`/work-plans/overview${q({ week_start: w })}`),
  workPlan: (p: Row) => request<Row>(`/work-plans/${q(p)}`),
  workItem: (id: string) => request<Row>(`/work-plans/${enc(id)}`),
  createWorkItem: (d: Row) => request<Row>("/work-plans/", { method: "POST", json: d }),
  updateWorkItem: (id: string, d: Row) =>
    request<Row>(`/work-plans/${enc(id)}`, { method: "PATCH", json: d }),
  deleteWorkItem: (id: string) => request(`/work-plans/${enc(id)}`, { method: "DELETE" }),
  moveWorkItemNextWeek: (id: string) =>
    request<Row>(`/work-plans/${enc(id)}/next-week`, { method: "POST" }),
  comments: (id: string) => request<Row[]>(`/work-plans/${enc(id)}/comments`),
  addComment: (id: string, body: string) =>
    request<Row>(`/work-plans/${enc(id)}/comments`, { method: "POST", json: { body } }),
  deleteComment: (id: string) => request(`/work-plans/comments/${enc(id)}`, { method: "DELETE" }),
  trainingCourse: () => request<TrainingCourse>("/training/course"),
  completeLesson: (id: string) =>
    request(`/training/lessons/${enc(id)}/complete`, { method: "POST" }),
  uncompleteLesson: (id: string) =>
    request(`/training/lessons/${enc(id)}/complete`, { method: "DELETE" }),
  createLesson: (d: LessonInput) =>
    request<TrainingLesson>("/training/lessons", { method: "POST", json: d }),
  updateLesson: (id: string, d: LessonInput) =>
    request<TrainingLesson>(`/training/lessons/${enc(id)}`, { method: "PUT", json: d }),
  deleteLesson: (id: string) => request(`/training/lessons/${enc(id)}`, { method: "DELETE" }),
  reorderLessons: (ids: string[]) =>
    request("/training/lessons/reorder", { method: "POST", json: { ids } }),
  trainingAssets: () => request<TrainingAsset[]>("/training/assets"),
  uploadTrainingAsset: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<TrainingAsset>("/training/assets", { method: "POST", body });
  },
  deleteTrainingAsset: (name: string) =>
    request(`/training/assets/${enc(name)}`, { method: "DELETE" }),
  trainingDocsUpdate: () => request<TrainingDocsUpdate>("/training/docs-update"),
  applyTrainingDocsUpdate: () =>
    request<TrainingDocsUpdate>("/training/docs-update", { method: "POST" }),
  announcements: () => request<MemberAnnouncements>("/announcements"),
  announcementsUnread: () => request<{ count: number }>("/announcements/unread-count"),
  markAnnouncementRead: (id: string) =>
    request(`/announcements/${enc(id)}/read`, { method: "POST" }),
  markAllAnnouncementsRead: () => request("/announcements/read-all", { method: "POST" }),
  manageAnnouncements: () => request<ManagedAnnouncements>("/announcements/manage"),
  createAnnouncement: (d: AnnouncementInput) =>
    request<ManagedAnnouncement>("/announcements", { method: "POST", json: d }),
  updateAnnouncement: (id: string, d: AnnouncementInput) =>
    request<ManagedAnnouncement>(`/announcements/${enc(id)}`, { method: "PUT", json: d }),
  deleteAnnouncement: (id: string) => request(`/announcements/${enc(id)}`, { method: "DELETE" }),
  reviewSupporters: () => request<ScanPerson[]>("/review/supporters"),
  reviewDay: (supporterId: string, workDate: string) =>
    request<{ records: number; jobs: number; finished_scans: number }>(
      `/review/day${q({ supporter_id: supporterId, work_date: workDate })}`,
    ),
  reviewDefaults: () => request<ScanOptions>("/review/defaults"),
  reviewCurrent: () => request<{ scan: ReviewScan | null }>("/review/current"),
  reviewScan: (id: string) => request<ReviewScan>(`/review/scans/${enc(id)}`),
  startReviewScan: (id: string, options: ScanOptions) =>
    request<ReviewScan>(`/review/scans/${enc(id)}/start`, { method: "POST", json: options }),
  finishReviewScan: (id: string) =>
    request<ReviewScan>(`/review/scans/${enc(id)}/finish`, { method: "POST" }),
  cancelReviewScan: (id: string) =>
    request<ReviewScan>(`/review/scans/${enc(id)}/cancel`, { method: "POST" }),
  reviewScans: (p: Row) => request<{ total: number; items: Row[] }>(`/review/scans${q(p)}`),
  reviewReport: (id: string) => request<string>(`/review/scans/${enc(id)}/report.txt`),
  reviewSummary: (p: Row) => request<Row>(`/review/summary${q(p)}`),
  reviewFileLink: (id: string, area: string, path: string) =>
    request<ScanFileLink>(`/review/scans/${enc(id)}/file-link${q({ area, path })}`),
  saveJobVerdict: (id: string, jobId: number, d: { verdict: ScanVerdict | null; note: string }) =>
    request<{ id: number; verdict: ScanVerdict | null; note: string; reviewed_at: string | null }>(
      `/review/scans/${enc(id)}/jobs/${jobId}`,
      { method: "PUT", json: d },
    ),
  saveScanNote: (id: string, note: string) =>
    request<{ reviewer_note: string }>(`/review/scans/${enc(id)}/note`, {
      method: "PUT",
      json: { note },
    }),
};

/** Upload a day's two ZIPs with progress (fetch can't report upload progress). */
export async function uploadReviewScan(form: FormData, onProgress: (percent: number) => void) {
  const send = (token: string) =>
    new Promise<XMLHttpRequest>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${BASE}/review/scans`);
      xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
      };
      xhr.onload = () => resolve(xhr);
      xhr.onerror = () =>
        reject(new Error("The upload failed. Check your connection and try again."));
      xhr.send(form);
    });
  // The server checks the token once the upload has arrived, so start with a
  // fresh one; if it ran out anyway, renew and send again.
  await renew();
  let xhr = await send(await accessTokenNow());
  if (xhr.status === 401) {
    await recover();
    onProgress(0);
    xhr = await send(await accessTokenNow());
  }
  let data: any = null;
  try {
    data = JSON.parse(xhr.responseText);
  } catch {}
  if (xhr.status >= 200 && xhr.status < 300) return data as ReviewScan;
  if (xhr.status === 413 && !data)
    throw new Error("The files are too large for the server to accept.");
  throw new Error(typeof data?.detail === "string" ? data.detail : `Upload failed (${xhr.status})`);
}
