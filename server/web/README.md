# Absurd Technologies React Web UI

React + TypeScript + Vite single-page application for Absurd Technologies admins,
managers, members, and supporters. It uses the FastAPI JWT API under
`/api/v1` and retains the existing Tabler visual system.

## Local development

Start FastAPI from the repository root, then Vite:

```bash
uvicorn server.main:app --reload --port 8000
cd server/web
npm ci
npm run dev
```

Open `http://127.0.0.1:5173/login`. Vite proxies `/api` to port 8000.

To run the local React UI against the live production backend instead:

```bash
npm run dev:production-backend
```

Open `http://127.0.0.1:5173/login`. Requests to `/api`, `/meta`, and
`/uploads` are proxied to `https://app.example.com`. This uses live
production accounts and data; create, update, and delete actions are real.

## Production

```bash
npm run build
```

The single `dist/index.html` entry and hashed assets are served by FastAPI.
Direct navigation to every application route returns the SPA entry. Docker
builds perform `npm ci` and `npm run build` automatically.

## Source layout

```text
src/react/
  App.tsx             routing and access guards
  AppShell.tsx        navigation, account settings, and theme
  api.ts              API transport and endpoint functions
  ui.tsx              shared tables, dialogs, paging, and states
  pages/
    dashboard/DashboardPage.tsx
    login/LoginPage.tsx
    users/UsersPage.tsx
    profiles/ProfilesPage.tsx
    licenses/LicensesPage.tsx
    interviews/InterviewsPage.tsx
    work-plan/WorkPlanPage.tsx
    performance/PerformancePage.tsx
    colleagues/ColleaguesPage.tsx
    sync/SyncPage.tsx
    errors/ErrorsPage.tsx
    telemetry/TelemetryPage.tsx
```
