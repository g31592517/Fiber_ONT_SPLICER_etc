# Fiber Network Management (FNMS)

A standalone FTTH network operations platform: OLTs, ONTs/ONUs, FATs (FDB/FDT), splitters,
fiber cables and cores, physical topology, monitoring, alarms, outage correlation and
service provisioning — built with a vendor-neutral adapter architecture.

It is an **independent product**. It has its own code, database, migrations, authentication
and deployment, and has no runtime dependency on EazPay. Integration with a billing system
is prepared through optional external identifiers and the `/api/v1/integration/v1` contract
(see [API_CONTRACT.md](API_CONTRACT.md)).

> **Hardware status:** no adapter in this build has been verified against physical OLTs.
> Everything demonstrable runs against the built-in **simulator**, whose data is labelled
> `SIMULATED` throughout the UI and API. See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

## Contents

| Path | What |
|---|---|
| `backend/` | FastAPI application, SQLAlchemy models, Alembic migrations, adapters, engines, worker, tests |
| `frontend/` | React + TypeScript (Vite) operations UI; consumes only the backend API |
| `docker-compose.yml`, `deploy/` | Container and systemd deployment |
| `dev.sh` | One-command local demo |
| `*.md` | Architecture, data model, research, capability matrix, API, monitoring, provisioning, deployment, test plan, status |

## Quick start (local demo, ~2 minutes)

Requirements: Python 3.11+ (tested on 3.13), Node.js 20+ (tested on 22).

```bash
./dev.sh
```

This creates a virtualenv, installs dependencies, migrates a SQLite database, seeds the
reference data plus a **simulated** demo network, then starts the API (:8000), the background
worker and the UI dev server (:5173).

Open http://localhost:5173 and sign in.

### Seeded development accounts

| Username | Password | Role |
|---|---|---|
| `admin` | `ChangeMe!2026` | Super Administrator |
| `netadmin` | `NetAdmin!2026` | Network Administrator |
| `technician` | `Technician!2026` | Network Technician |
| `noc` | `Monitoring!2026` | Monitoring Operator |
| `auditor` | `Auditor!2026` | Read-only Auditor |

The demo accounts are created only by `python -m app.seed --demo`. Change or remove them
before any real use. The bootstrap admin password comes from `FNMS_BOOTSTRAP_ADMIN_PASSWORD`.

### Manual start

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements-dev.txt
cd backend
export FNMS_DATABASE_URL=sqlite:///./fnms.db          # or postgresql+psycopg://user:pass@host/db
../.venv/bin/python -m app.seed --demo                # migrations + reference data + simulated network
../.venv/bin/uvicorn app.main:app --port 8000         # API (OpenAPI UI at /api/docs)
../.venv/bin/python -m app.jobs.worker                # in a second terminal: monitoring + provisioning worker
cd ../frontend && npm install && npm run dev          # UI at http://localhost:5173 (proxies /api to :8000)
```

Single-process mode for demos: `FNMS_EMBEDDED_WORKER=true` runs the worker inside the API process.

## What you can do with the demo

1. **Dashboard** — OLT/ONT/FAT/alarm/incident counts, filterable by site and OLT.
2. **OLTs → SIM-OLT-CENTRAL → Hardware** — chassis → slot → board → PON port, as reported by the device.
3. **Discovered ONTs** — inspect an unregistered ONT and register it, either with line/service
   profiles only, or registered and activated for a pending subscriber service. The plan is
   validated (including a device dry run), reviewed, approved, executed and read back from the OLT.
4. **ONT detail** — where the ONT is connected (OLT/PON/ONT ID/FAT port), the subscriber, the
   latest optical reading, the full fiber path to the OLT, plus optical and status history.
5. **FATs** — port grid (occupied/available/reserved/faulty), capacity warnings, the upstream
   path to the OLT, and downstream dependency.
6. **Fiber topology** — interactive schematic. Click any element to see the subscribers that depend on it.
7. **Incidents** — the seeded LOS cluster behind SIM-FAT-002 is correlated to its common
   upstream element. Impacts are split into confirmed, inferred and unknown.
8. **Simulator control** — inject LOS, dying gasp, PON port faults, board failures, an
   unreachable OLT, signal degradation or new ONTs. Then watch alarms, incidents and the dashboard react.
9. **Provisioning jobs** — suspend, restore, change profile and disconnect services. Every job
   has a plan, an approval step, an execution log, verification, rollback and safe retry.

## Tests

```bash
cd backend && ../.venv/bin/python -m pytest          # 52 tests (SQLite)
FNMS_TEST_DATABASE_URL=postgresql+psycopg://fnms:pass@localhost/fnms_test ../.venv/bin/python -m pytest   # same suite on PostgreSQL (wipes that DB)
cd ../frontend && npm run build                       # type-check + production build
node e2e/smoke.cjs && node e2e/flow.cjs               # browser tests against a running stack (Playwright)
```

See [TEST_PLAN.md](TEST_PLAN.md).

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — components, adapter layer, jobs, security
- [DATA_MODEL.md](DATA_MODEL.md) — entities and relationships
- [REFERENCE_RESEARCH.md](REFERENCE_RESEARCH.md) — Splynx / Sonar findings and source status
- [VENDOR_CAPABILITY_MATRIX.md](VENDOR_CAPABILITY_MATRIX.md) — vendors × operations, verification level
- [API_CONTRACT.md](API_CONTRACT.md) — REST API and future billing-integration contract
- [MONITORING.md](MONITORING.md) — polling, state normalization, alarms, correlation
- [PROVISIONING.md](PROVISIONING.md) — templates, plans, states, rollback, retries
- [DEPLOYMENT.md](DEPLOYMENT.md) — configuration and deployment
- [TEST_PLAN.md](TEST_PLAN.md) — coverage and how to run
- [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) — completed / partial / unsupported / unverified
