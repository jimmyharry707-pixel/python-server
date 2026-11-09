# Baymax Central Server

FastAPI + SQLAlchemy backend for the CareerCraft/Baymax desktop client. It
implements the sync contract the client already speaks and adds the
productization features: licensing, admin-provisioned profiles, fleet
monitoring, telemetry, error reporting, release manifests and an admin
dashboard.

## Quick start (local dev, SQLite)

**Important:** the package is imported as `server.main`, so uvicorn must be
started with the **repo root** as the working directory (not `server/`).
Running from inside `server/` causes `ModuleNotFoundError: No module named 'server'`.

### One-time setup

```powershell
cd server
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

### Start the server (pick one)

From `server/` (easiest on Windows):

```powershell
.\run_dev.ps1
```

Or manually from the **repo root** (`baymax-synergy/`):

```powershell
cd ..   # if you are currently in server/
.\server\.venv\Scripts\uvicorn.exe server.main:app --reload --port 8000 --reload-dir server
```

- API docs: http://localhost:8000/docs
- Web app (admins + members): http://localhost:8000/login
- Health: http://localhost:8000/health

Legacy pre-Tabler SPAs live under `server/deprecated/` (not served).

A bootstrap admin is created on first launch from `BAYMAX_BOOTSTRAP_ADMIN_*`.
Change the password immediately.

## Run the smoke test

```bash
# from repo root, with the server venv
server/.venv/Scripts/python.exe -m server.smoke_test
```

## Production (Docker + Postgres + nginx TLS)

1. Point DNS for `app.baymax.example.com` at the host and edit
   `nginx/baymax.conf` `server_name`.
2. Issue TLS certs with certbot's webroot mode (nginx serves the challenge
   from `/var/www/certbot`) and reload nginx on renewal:
   `certbot certonly --webroot -w /var/www/certbot -d app.example.com
   --deploy-hook "docker exec server-nginx-1 nginx -s reload"`.
3. Set secrets and start:

```bash
export BAYMAX_JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
export BAYMAX_BOOTSTRAP_ADMIN_PASSWORD='a-strong-password'
export POSTGRES_PASSWORD='a-strong-db-password'
# If the DB password has URL-special chars (@ : / # ? etc.), percent-encode it:
export POSTGRES_PASSWORD_URLENC=$(python3 -c "import urllib.parse,os; print(urllib.parse.quote(os.environ['POSTGRES_PASSWORD'], safe=''))")
docker compose -f server/docker-compose.yml up -d --build
```

For repeatable VPS deployments, create `server/.env` once and use the deployment
script from the repository root:

```bash
chmod +x ops/deploy_server.sh
./ops/deploy_server.sh --pull
```

The script validates Compose configuration, creates a pre-deployment database
dump under `backups/`, builds the multi-stage API image (including the React/Vite
frontend), recreates the services, checks both internal and public health, and
restores the previous API image if startup fails. Run
`./ops/deploy_server.sh --help` for build-only and no-cache options.

Uploaded profile photos are stored in `server/data/avatars`, which Compose bind-mounts
into the API container. This also makes existing files in that directory available
to the running application.
The volume is reused when the API image is rebuilt or its container is recreated;
do not pass `--volumes` to `docker compose down` unless the photos are intentionally
being removed. On the first volume-backed deployment, the deployment script copies
photos from the existing API container into the new volume. It cannot recover files
lost during an earlier container replacement, so affected users must upload those
photos again.

The client selects this server via `app_config.ENVIRONMENTS['production']`;
update that URL (and the staging one) to your real domains before building
releases.

### User portal (`/userportal`)

A public static download page is served by nginx at
`https://app.example.com/userportal`. It links to:

- `/userportal/downloads/CareerCraft.exe` — app release (not host-mounted by
  the current Compose configuration)
- `/userportal/downloads/TekAbsurd_cert.cer` — signing cert (mounted from
  `/root/TekAbsurd_cert.cer`)

The page itself lives in `server/nginx/userportal/index.html`. After changing
the nginx config or mounts, recreate the nginx container:

```bash
docker compose -f server/docker-compose.yml up -d nginx
```

Admins can replace those files from the dashboard **CC Releases** tab
(`https://app.example.com/login`). Uploads write to the same host paths
mounted into both the `api` and `nginx` containers (`BAYMAX_RELEASE_FILES_DIR`,
defaulting to `/data/userportal-downloads` in compose). Optionally include a
version to publish a release manifest for client auto-update.

## API surface (prefix `/api/v1`)

- Auth: `POST /auth/login`, `POST /auth/refresh`, `POST /auth/logout`
  (sessions: see `auth_sessions.py`; the website keeps its refresh token in
  an HttpOnly cookie and sends `X-Requested-With` with these calls)
- Users: `GET /users/me`, `GET /users/me/profiles`
- Applications: `POST/GET /applications/`, `GET /applications/stats`
- Interviews: `GET /interviews/`, `GET /interviews/all`
- Sync: `POST /sync/push`, `GET /sync/pull`
- Licensing for every app (CareerCraft, Sidekick, SnowLink):
  `POST /licensing/{product}/activate`, `/refresh`, `/release`,
  `GET /licensing/{product}/public-keys`. CareerCraft needs a signed-in user;
  the other apps use the key alone. Contract:
  [docs/licensing/CLIENT_PROTOCOL.md](../docs/licensing/CLIENT_PROTOCOL.md).
- CareerCraft's older paths stay for installed copies: `POST /license/activate`,
  `POST /license/refresh` (auth required), `GET /license/public-keys`.
  Sync and `/users/me/profiles` require `X-License-Token` (a CareerCraft lease).
- License admin (admins and managers): `GET /license/products`,
  `POST/GET /license`, `PATCH/DELETE /license/{id}`, `POST /license/{id}/extend`,
  `/suspend`, `/resume`, `GET /license/machines`,
  `POST /license/seats/{seat_id}/release`, `/block`, `GET /license/events`.
  What each one means: [docs/licensing/ADMIN_LICENSES.md](../docs/licensing/ADMIN_LICENSES.md).
  Sidekick and SnowLink need their own signing keys in production
  (`BAYMAX_LICENSE_SIGNING_KEY_SIDEKICK`, `BAYMAX_LICENSE_SIGNING_KEY_SNOWLINK`).
- Fleet: `POST /fleet/telemetry`, `/fleet/errors`, `/fleet/consent`
  (online presence comes from sync push/pull updating `Client.last_seen`)
- Releases: `GET /releases/latest`, admin `POST /releases`
- Admin: `/admin/api/*` (overview, fleet, users, per-user data, errors, telemetry),
  `/admin/profiles/*` (CRUD + assign/unassign)

## Database migrations

Schema is created automatically on startup. Additive changes are registered in
`server/migrations.py::MIGRATIONS` and applied exactly once (tracked in
`schema_migrations`). This replaces the client's ad-hoc `migrate_add_*.py`
scripts.

## Backups & operations

See `ops/` in the repo root for backup and uptime-check helpers.
