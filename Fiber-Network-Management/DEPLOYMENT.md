# Deployment

## 1. Components

| Process | Command | Notes |
|---|---|---|
| API | `uvicorn app.main:app` | Stateless; can run several instances behind a reverse proxy |
| Worker | `python -m app.jobs.worker` | Runs the scheduler and the job queue. **At least one is required.** Several workers are safe on PostgreSQL |
| Web UI | static files from `frontend/dist` | Serve with any web server; `/api/` must be proxied to the API |
| Database | PostgreSQL 13+ (verified on 16) | SQLite only for development/demo |

Single-process demo mode: `FNMS_EMBEDDED_WORKER=true` starts the worker as a thread inside the API process.

## 2. Configuration (environment variables, prefix `FNMS_`)

| Variable | Default | Purpose |
|---|---|---|
| `FNMS_ENVIRONMENT` | `development` | `production` refuses to start with default secrets |
| `FNMS_DATABASE_URL` | `sqlite:///./fnms.db` | e.g. `postgresql+psycopg://fnms:pass@db:5432/fnms` |
| `FNMS_JWT_SECRET` | dev value | Token signing key: **set in production** |
| `FNMS_ENCRYPTION_KEY` | dev value | Fernet key used to encrypt device credentials: **set in production and back it up**. Losing it makes stored credentials unreadable |
| `FNMS_ACCESS_TOKEN_MINUTES` | 60 | Session lifetime |
| `FNMS_LOGIN_RATE_LIMIT_PER_MINUTE` | 10 | Per IP |
| `FNMS_MAX_FAILED_LOGINS` / `FNMS_LOCKOUT_MINUTES` | 5 / 15 | Account lockout |
| `FNMS_OPERATION_RATE_LIMIT_PER_MINUTE` | 60 | Per user, device operations |
| `FNMS_CORS_ORIGINS` | localhost:5173 | JSON list; not needed when the UI and API share an origin |
| `FNMS_EMBEDDED_WORKER` | false | See above |
| `FNMS_WORKER_POLL_SECONDS` / `FNMS_SCHEDULER_TICK_SECONDS` | 2 / 15 | Worker loop timings |
| `FNMS_MIN_POLL_INTERVAL_SECONDS` / `FNMS_DEFAULT_POLL_INTERVAL_SECONDS` | 60 / 300 | Polling limits |
| `FNMS_JOB_LOCK_TIMEOUT_SECONDS` | 600 | Stuck-job detection |
| `FNMS_ALLOW_UNVERIFIED_ADAPTER_WRITES` | false | Enables write operations on experimental adapters (Huawei). Only after validation on your equipment |
| `FNMS_SIMULATOR_ENABLED` | true | Disable on production networks |
| `FNMS_MAP_TILE_URL` / `FNMS_MAP_ATTRIBUTION` | OpenStreetMap | Empty URL = schematic plot only (no external map service). Respect your tile provider's usage policy |
| `FNMS_BOOTSTRAP_ADMIN_USERNAME` / `_PASSWORD` | admin / ChangeMe!2026 | Created by `python -m app.seed` if absent |

Generate secrets:
```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"                                  # JWT secret
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"     # encryption key
```

## 3. Docker Compose

```bash
cp .env.example .env        # then set POSTGRES_PASSWORD, FNMS_JWT_SECRET, FNMS_ENCRYPTION_KEY, admin password
docker compose up -d --build
docker compose exec api python -m app.seed            # reference data + bootstrap admin (add --demo for the simulated network)
# UI: http://<host>:8080   API docs: http://<host>:8080/api/docs
```

The `api` container runs `alembic upgrade head` before it starts. Put TLS in front of the
`web` service (reverse proxy or load balancer).

> **Verification status:** the Dockerfiles and `docker-compose.yml` were written but **not
> built**, because no Docker daemon was available in the development environment. Every
> command they run was verified natively: migrations, API, worker, the frontend build, and
> PostgreSQL 16.

## 4. Native install (systemd)

```bash
sudo useradd --system fnms && sudo mkdir -p /opt/fnms && sudo chown fnms /opt/fnms
# copy the project to /opt/fnms, then as fnms:
python3 -m venv /opt/fnms/.venv && /opt/fnms/.venv/bin/pip install -r /opt/fnms/backend/requirements.txt
cp .env.example /opt/fnms/backend/.env      # edit it
cd /opt/fnms/backend && ../.venv/bin/alembic upgrade head && ../.venv/bin/python -m app.seed
cd /opt/fnms/frontend && npm ci && npm run build     # serve dist/ with nginx (see frontend/nginx.conf; change proxy_pass to 127.0.0.1:8000)
sudo cp /opt/fnms/deploy/fnms-*.service /etc/systemd/system/ && sudo systemctl enable --now fnms-api fnms-worker
```

## 5. Database migrations

```bash
cd backend
alembic upgrade head          # apply
alembic check                 # verify models and migrations agree
alembic revision --autogenerate -m "describe change"   # after changing models
```

Verified: `0001_initial_schema` applies cleanly on SQLite and PostgreSQL 16, and `alembic check` reports no drift on either.

## 6. Network access requirements

The worker needs to reach the OLTs or vendor platforms: SSH (TCP 22 or as configured),
SNMP (UDP 161) and ICMP. The API does not talk to devices. Restrict outbound access from the
worker host to the management networks.

## 7. Operations checklist

- [ ] Real secrets set; `FNMS_ENVIRONMENT=production`
- [ ] Encryption key backed up separately from database backups
- [ ] Bootstrap admin password changed; demo users not created (`--demo` not used)
- [ ] `FNMS_SIMULATOR_ENABLED=false` unless you want demo OLTs
- [ ] Worker running and supervised; *Background jobs* page shows jobs completing
- [ ] SNMPv3 credentials preferred over v2c communities
- [ ] Optical thresholds reviewed against your optics' datasheets
- [ ] Database backups scheduled (all history lives in PostgreSQL)
