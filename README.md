# Baymax Central Server

FastAPI and SQLAlchemy backend for the CareerCraft/Baymax desktop client, with
a React/Vite web interface for administrators and members. The service provides
authentication, client sync, profiles, licensing, fleet telemetry, and release
management.

## Local Development

Requirements: Python and PowerShell on Windows. The local development database
uses SQLite.

From the repository root, create the environment and install the server
dependencies:

```powershell
cd server
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Start the development server:

```powershell
.\run_dev.ps1
```

The script copies `.env.example` to `.env` if needed and starts the server from
the repository root. You can also start it manually from the repository root
after installing the requirements:

```powershell
.\server\.venv\Scripts\uvicorn.exe server.main:app --reload --port 8000 --reload-dir server
```

The application is available at:

- Web interface: <http://localhost:8000/login>
- API documentation: <http://localhost:8000/docs>
- Health check: <http://localhost:8000/health>

The web frontend source is in `server/web`. Install its dependencies with
`npm ci` from that directory and run `npm run dev` for frontend development.

## Configuration

Local settings are read from `server/.env`. The checked-in
[`server/.env.example`](server/.env.example) contains development defaults;
replace them with strong, unique secrets and explicit CORS origins before any
production deployment. Do not commit real credentials or private keys.

The Docker Compose stack in `server/docker-compose.yml` runs the API with
PostgreSQL and nginx and requires production secrets and TLS files to be
configured on the host.

See [`server/README.md`](server/README.md) for additional backend and API
details.

## License

This project is licensed under the MIT License. See [`LICENSE`](LICENSE).