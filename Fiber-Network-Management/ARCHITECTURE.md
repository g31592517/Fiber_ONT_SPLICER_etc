# Architecture

## 1. Context and technology decisions

The EazPay source code was **not available** in the development environment: no repository
of that name was attached or visible to the session. Its stack could therefore not be
inspected. The choices below were made on their own merits, and the integration boundary is
an HTTP/JSON contract, so a future EazPay integration does not depend on sharing a language
or framework.

| Concern | Choice | Why |
|---|---|---|
| Backend | Python 3.13, FastAPI, Pydantic v2 | Strong network-automation ecosystem (SSH: paramiko; SNMP: pysnmp), typed request validation, automatic OpenAPI |
| Persistence | SQLAlchemy 2 + Alembic; PostgreSQL (production), SQLite (dev/test) | Portable schema; one migration chain verified on both engines |
| Background work | DB-backed job queue + worker process + scheduler | No extra infrastructure (Redis/Celery) needed to install the product; atomic claim is safe for several workers on PostgreSQL |
| Frontend | React 19 + TypeScript + Vite, TanStack Query, React Flow, Leaflet, Recharts | Interactive topology, optional GIS, charts; separate deployable |
| Shape | Modular monolith (API process + worker process) | Clear module boundaries without microservice overhead |

## 2. Component view

```
                 ┌───────────────────────────── frontend (React) ─────────────────────────────┐
                 │ dashboards · OLT/ONT/FAT pages · topology · map · provisioning · admin     │
                 └───────────────▲──────────────────────────────────────────────────────────────┘
                                 │ REST/JSON (/api/v1) — bearer token
┌────────────────────────────────┴──────────── backend (FastAPI) ─────────────────────────────────┐
│ api/          routers: thin, validate input, check permissions, enqueue jobs, audit           │
│ core/         Network Management Core + Physical Infrastructure Engine                          │
│               topology.py (link rules, tracing) · impact.py · alarms.py · optical.py · audit   │
│ engines/      monitoring.py · correlation.py · provisioning.py · operations.py                  │
│ adapters/     base.py (contract) · registry.py · simulator/ · huawei/ · snmp/                   │
│ jobs/         queue.py (DB queue) · worker.py (worker + scheduler)                              │
│ security/     auth (JWT + server-side sessions), RBAC permissions, crypto (Fernet, bcrypt)       │
│ models/       SQLAlchemy entities  ·  alembic/ migrations                                       │
└──────────────────────────────┬──────────────────────────────────────────────┬──────────────────┘
                               │ SQL                                          │ SSH / SNMP / API
                         PostgreSQL                                  OLTs / vendor platforms
```

### A. Network Management Core (`app/core`, `app/models`)
Sites, OLTs, boards, ports, ONTs, FATs, splitters, cables, links, device state, events and
relationships. **It contains no vendor CLI or MIB.** State uses normalized vocabularies
(`core/enums.py`), and the raw vendor value is always stored next to the normalized one
(`vendor_status`, `vendor_severity`, `vendor_alarm_id`, `vendor_raw`).

### B. Vendor Integration Layer (`app/adapters`)
`VendorAdapter` (`adapters/base.py`) defines the contract in five groups: connection,
monitoring, discovery, provisioning and administration. It also defines the vendor-neutral
DTOs (`OntStatusInfo`, `OpticalInfo`, `AlarmRecord`, `ServicePortSpec`, …).

* Every adapter declares `CAPABILITIES`: one entry per capability, holding `supported` and a
  **verification level** (`hardware_verified`, `simulated`, `fixture_tested`, `unverified`).
* Any method an adapter does not implement raises `UnsupportedOperation`, which the job layer
  turns into a structured `{"status": "unsupported", ...}` result. An unimplemented operation
  never reports success.
* `experimental = True` adapters are blocked from write operations unless
  `FNMS_ALLOW_UNVERIFIED_ADAPTER_WRITES=true` (`registry.write_allowed`).
* `planned = True` entries (ZTE, FiberHome, V-SOL, Nokia, Calix SMx, Adtran Mosaic) are
  architectural targets. They implement nothing and cannot be selected for an OLT.
* Adapters are instantiated only inside the worker, with credentials decrypted at that moment
  (`registry.build_adapter`).

### C. Monitoring Engine (`engines/monitoring.py`, `engines/correlation.py`)
The scheduler enqueues `olt.poll` jobs according to each OLT's interval (minimum 60 s). A poll
does the following, each step only if the adapter supports it:
1. health/reachability
2. hardware
3. ports
4. ONT status (with automatic synchronization of ONTs configured on the device)
5. optical readings plus threshold evaluation
6. device alarms
7. discovery list

It then runs outage correlation. See [MONITORING.md](MONITORING.md).

### D. Provisioning Engine (`engines/provisioning.py`)
Request → validation (database checks plus a device dry run) → plan → approval → execution
through the adapter → read-back verification → recorded result. Execution includes
compensation (rollback) and idempotent, safe retries. See [PROVISIONING.md](PROVISIONING.md).

### E. Physical Infrastructure Engine (`core/topology.py`, `core/impact.py`)
Passive elements (FAT, splitter, ODF/closure, cable, core) are **never polled**. They are
modelled as ports joined by directed links (upstream `a` → downstream `b`). The engine:
- enforces direction and occupancy rules and prevents loops and core reuse
- traces any endpoint up to its OLT PON port
- walks downstream to compute dependent ONTs and subscribers
- marks each link `verified`, `unverified` or `planned`

See [DATA_MODEL.md](DATA_MODEL.md).

## 3. Background processing

* `jobs` table: `queued → running → succeeded|failed`, with a dedupe key (for example
  `poll:<olt_id>`), attempts, `run_after` and a lock.
* Claiming is an atomic `UPDATE … WHERE status='queued'`.
* Connection errors may be retried with backoff, up to `max_attempts`. Polls are not retried;
  the next scheduled poll covers them.
* Jobs left `running` by a crashed worker are **failed, never re-run blindly**.
* No network operation runs inside an HTTP request. The API returns `202 {job_id}` and the UI
  polls `/api/v1/jobs/{id}`.

## 4. Security

| Control | Implementation |
|---|---|
| Authentication | Username/password (bcrypt); HS256 JWT bound to a server-side `user_sessions` row so logout, disabling a user and role changes revoke tokens |
| Brute force | Per-IP login rate limit; account lockout after `FNMS_MAX_FAILED_LOGINS` failures |
| Authorization | 30 fine-grained permissions (`security/permissions.py`); 5 built-in roles; custom roles; each route checks permissions server-side |
| Destructive operations | Typed confirmation (ONT serial / service ref) checked by the API; separate permissions (`ont.deregister`, `provisioning.disconnect`, `ont.reboot`, …) |
| Secrets at rest | Fernet (AES-128-CBC + HMAC) encryption of every device credential (`*_enc` columns) |
| Secret exposure | Serializer drops all `*_enc` columns (only `has_<x>` booleans are returned); audit details and job results pass through `redact()`; JSON logs pass through `scrub()` |
| SNMP | SNMPv3 (authPriv) supported and recommended; v2c is flagged as insecure in the API and UI |
| Input validation | Pydantic models with ranges, enums and patterns; CLI arguments built by escaping builders (`huawei/cli.py`) |
| Audit | `audit_logs` for every configuration change, credential change, device operation, provisioning action, role/user change and login |
| Rate limiting | Per-user limit on device operation endpoints |
| HTTP | Security headers (nosniff, frame deny, no-referrer, no-store); CORS allow-list |
| Production guard | The API refuses to start in `production` with default JWT/encryption secrets |

## 5. Multi-tenancy

Each deployment is single-tenant (one ISP per database). Multi-tenant row isolation was not
added because the host (EazPay) architecture could not be inspected. If needed, an `org_id`
column plus a request-scoped filter can be added to the root entities (sites, OLTs,
subscribers, infrastructure) without changing the adapter or engine contracts.

## 6. Integration boundary (future EazPay phase)

* Optional identifiers only, never foreign keys: `subscribers.external_customer_id`,
  `external_account_id`, `services.external_service_id`, `external_package_id`.
* `/api/v1/integration/v1/services/{external_service_id}`: status, activate, change-profile,
  suspend, restore, disconnect. These are idempotent through `external_request_id`, and
  auto-approval is controlled by the `integration.auto_approve` setting.
* Permission `integration.api` for a dedicated service account.
