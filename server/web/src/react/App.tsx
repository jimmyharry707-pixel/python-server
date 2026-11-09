import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { SignedOutError, signOut } from "./session";
import { AppShell } from "./AppShell";
import { LoginPage } from "./pages/login/LoginPage";
import { UsersPage } from "./pages/users/UsersPage";
import { ProfilesPage } from "./pages/profiles/ProfilesPage";
import { MemberProfilesPage, MemberUsersPage } from "./pages/member-manage/MemberManagePages";
import { LicensesPage } from "./pages/licenses/LicensesPage";
import { ErrorsPage } from "./pages/errors/ErrorsPage";
import { TelemetryPage } from "./pages/telemetry/TelemetryPage";
import { ColleaguesPage } from "./pages/colleagues/ColleaguesPage";
import { PerformancePage } from "./pages/performance/PerformancePage";
import { WorkPlanPage } from "./pages/work-plan/WorkPlanPage";
import { ErrorBox, Loading, staff } from "./ui";
const InterviewsPage = lazy(() =>
  import("./pages/interviews/InterviewsPage").then((module) => ({
    default: module.InterviewsPage,
  })),
);
const DashboardPage = lazy(() =>
  import("./pages/dashboard/DashboardPage").then((module) => ({
    default: module.DashboardPage,
  })),
);
// Lazy: pulls in recharts (for the sync trend chart), same reasoning as
// DashboardPage above - keep that out of the eagerly-loaded main bundle.
const SyncPage = lazy(() =>
  import("./pages/sync/SyncPage").then((module) => ({ default: module.SyncPage })),
);
// Lazy: the Markdown renderer only matters on the training pages.
const TrainingCoursePage = lazy(() =>
  import("./pages/training/TrainingCoursePage").then((module) => ({
    default: module.TrainingCoursePage,
  })),
);
const TrainingEditorPage = lazy(() =>
  import("./pages/training/TrainingEditorPage").then((module) => ({
    default: module.TrainingEditorPage,
  })),
);
const AnnouncementsEditorPage = lazy(() =>
  import("./pages/announcements/AnnouncementsEditorPage").then((module) => ({
    default: module.AnnouncementsEditorPage,
  })),
);
const NotificationsPage = lazy(() =>
  import("./pages/notifications/NotificationsPage").then((module) => ({
    default: module.NotificationsPage,
  })),
);
const QualityPage = lazy(() =>
  import("./pages/quality/QualityPage").then((module) => ({ default: module.QualityPage })),
);
function Protected() {
  const loc = useLocation();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false });
  if (me.error instanceof SignedOutError)
    return <Navigate to="/login" state={{ from: loc }} replace />;
  if (me.isLoading)
    return (
      <div className="page page-center">
        <Loading />
      </div>
    );
  if (me.error || !me.data) return <AuthFailure error={me.error} />;
  // Supporters only get the training course on the website.
  if (me.data.role?.toLowerCase() === "supporter" && !isTrainingPath(loc.pathname))
    return <Navigate to="/training" replace />;
  return <AppShell user={me.data} />;
}
const isTrainingPath = (path: string) => path === "/training" || path.startsWith("/training/");
function AuthFailure({ error }: { error: unknown }) {
  return (
    <main className="page page-center">
      <div className="container-tight py-4">
        <ErrorBox error={error} />
        <button type="button" className="btn btn-primary w-100" onClick={() => void signOut()}>
          Return to sign in
        </button>
      </div>
    </main>
  );
}
function Staff({ children }: { children: React.ReactNode }) {
  const me = useQuery({ queryKey: ["me"], queryFn: api.me }).data;
  return me && staff(me.role) ? children : <Navigate to="/dashboard" replace />;
}
function WorkAccess({ children }: { children: React.ReactNode }) {
  const me = useQuery({ queryKey: ["me"], queryFn: api.me }).data;
  return me?.role !== "supporter" ? children : <Navigate to="/dashboard" replace />;
}
function MemberOnly({ children }: { children: React.ReactNode }) {
  const me = useQuery({ queryKey: ["me"], queryFn: api.me }).data;
  return me?.role?.toLowerCase() === "member" ? children : <Navigate to="/dashboard" replace />;
}
// Admins, managers and members review supporters' work; supporters never see it.
function Reviewer({ children }: { children: React.ReactNode }) {
  const me = useQuery({ queryKey: ["me"], queryFn: api.me }).data;
  return me && (staff(me.role) || me.role?.toLowerCase() === "member") ? (
    children
  ) : (
    <Navigate to="/dashboard" replace />
  );
}
function ManageUsers() {
  const me = useQuery({ queryKey: ["me"], queryFn: api.me }).data;
  return me?.role?.toLowerCase() === "member" ? (
    <MemberUsersPage />
  ) : me && staff(me.role) ? (
    <UsersPage />
  ) : (
    <Navigate to="/dashboard" replace />
  );
}
function ManageProfiles() {
  const me = useQuery({ queryKey: ["me"], queryFn: api.me }).data;
  return me?.role?.toLowerCase() === "member" ? (
    <MemberProfilesPage />
  ) : me && staff(me.role) ? (
    <ProfilesPage />
  ) : (
    <Navigate to="/dashboard" replace />
  );
}
export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<Protected />}>
        <Route
          path="/dashboard"
          element={
            <Suspense fallback={<Loading />}>
              <DashboardPage />
            </Suspense>
          }
        />
        <Route
          path="/interviews"
          element={
            <Suspense fallback={<Loading />}>
              <InterviewsPage />
            </Suspense>
          }
        />
        <Route
          path="/workplan"
          element={
            <WorkAccess>
              <WorkPlanPage />
            </WorkAccess>
          }
        />
        <Route path="/performance" element={<PerformancePage />} />
        <Route
          path="/quality"
          element={
            <Reviewer>
              <Suspense fallback={<Loading />}>
                <QualityPage />
              </Suspense>
            </Reviewer>
          }
        />
        <Route
          path="/colleagues"
          element={
            <MemberOnly>
              <ColleaguesPage />
            </MemberOnly>
          }
        />
        <Route
          path="/training/:slug?"
          element={
            <Suspense fallback={<Loading />}>
              <TrainingCoursePage />
            </Suspense>
          }
        />
        <Route
          path="/training-editor"
          element={
            <Staff>
              <Suspense fallback={<Loading />}>
                <TrainingEditorPage />
              </Suspense>
            </Staff>
          }
        />
        <Route
          path="/announcements"
          element={
            <Staff>
              <Suspense fallback={<Loading />}>
                <AnnouncementsEditorPage />
              </Suspense>
            </Staff>
          }
        />
        <Route
          path="/notifications"
          element={
            <MemberOnly>
              <Suspense fallback={<Loading />}>
                <NotificationsPage />
              </Suspense>
            </MemberOnly>
          }
        />
        <Route path="/users" element={<ManageUsers />} />
        <Route path="/profiles" element={<ManageProfiles />} />
        <Route
          path="/licenses"
          element={
            <Staff>
              <LicensesPage />
            </Staff>
          }
        />
        <Route
          path="/sync"
          element={
            <Staff>
              <Suspense fallback={<Loading />}>
                <SyncPage />
              </Suspense>
            </Staff>
          }
        />
        <Route
          path="/errors"
          element={
            <Staff>
              <ErrorsPage />
            </Staff>
          }
        />
        <Route
          path="/telemetry"
          element={
            <Staff>
              <TelemetryPage />
            </Staff>
          }
        />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
