# Implementation status

Status as of 2026-11-04. Legend: ✅ complete · 🟡 partial · 🧪 simulated only · ⚠️ implemented but unverified · ❌ not implemented

## Headline facts

* **Nothing has been verified against physical equipment.** No OLT, credentials or lab
  environment were available. Every device-facing behaviour shown in the demo runs against
  the built-in **simulator**, through the same adapter contract, engines and API a real
  adapter uses.
* The **Huawei SmartAX adapter is experimental and unverified.** Its command syntax and
  parsers follow commonly published conventions. Official Huawei documentation could not be
  read from the environment. Write operations are blocked unless explicitly enabled.
* **Splynx/Sonar detailed documentation could not be read** (`wiki.splynx.com` and
  `docs.sonar.expert` were blocked). See [REFERENCE_RESEARCH.md](REFERENCE_RESEARCH.md).
* The **EazPay code base was not available** to inspect. Nothing in this project references it.

## Deliverables

| # | Deliverable | Status | Notes |
|---|---|---|---|
| 1 | Standalone frontend | ✅ | React/TS; 40 routes; consumes only the API |
| 2 | Standalone backend | ✅ | FastAPI modular monolith + worker |
| 3 | Schema & migrations | ✅ | 40 tables; Alembic; verified on SQLite and PostgreSQL 16 |
| 4 | Network management core | ✅ | |
| 5 | Vendor adapter architecture | ✅ | Capability declarations with verification levels; structured unsupported results |
| 6 | Working simulation adapter | ✅ 🧪 | Full contract; fault injection; persistent device state |
| 7 | Genuine vendor integration | ⚠️ | Huawei SmartAX CLI (experimental) and standard SNMP/ICMP monitoring are implemented, but neither has been tested against real devices. See *Why* below |
| 8 | OLT management | ✅ | |
| 9 | ONT/ONU management | ✅ | |
| 10 | FAT management | ✅ | |
| 11 | Splitter & fiber infrastructure | ✅ | |
| 12 | Topology visualization | ✅ | Schematic (React Flow) + GIS (Leaflet) with offline fallback |
| 13 | Monitoring engine | ✅ | |
| 14 | Alarm & incident management | ✅ | |
| 15 | Service profile management | ✅ | |
| 16 | Provisioning engine | ✅ | |
| 17 | Authentication & authorization | ✅ | |
| 18 | Audit logging | ✅ | |
| 19 | API documentation | ✅ | OpenAPI at `/api/docs`; [API_CONTRACT.md](API_CONTRACT.md) |
| 20 | Setup & deployment instructions | ✅ / 🟡 | Native setup verified; Docker files written but not built (no Docker daemon available) |
| 21 | Automated tests | ✅ | 52 backend tests (SQLite + PostgreSQL), frontend build, Playwright smoke + flow |
| 22 | Seed/demo data | ✅ 🧪 | Clearly labelled simulated network |

**Why no hardware-verified vendor integration:** the brief requires one "where the required
device model, official technical documentation and access to a test environment are
available". None were available. Rather than claim an integration that was never exercised,
the Huawei adapter is shipped behind an `experimental` flag, with its limitations documented.

## Feature detail

### Infrastructure
| Feature | Status |
|---|---|
| Sites CRUD | ✅ |
| OLT registration with all fields, SNMPv3/v2c, SSH, encrypted write-only credentials | ✅ |
| OLT overview: status, last communication, health, ONT counts, alarms | ✅ (values the adapter cannot supply are shown as unsupported) |
| Hardware hierarchy frame → slot → board → port; installed/empty/failed/disabled | ✅ (from device via adapter, or manual entry) |
| PON port list, utilization, per-port detail and history | ✅ |
| Splitter ratios configurable (1:2–1:64 seeded, custom e.g. 2:32), cascades, housing in FAT/closure | ✅ |
| FAT CRUD, configurable port count, port status/reservation/drop cable/notes, capacity and warnings, terminology setting, photo links | ✅ (photos are URLs; file upload/storage not implemented) |
| ODF/ODB/closure/splice point/termination points | ✅ |
| Cables (feeder/distribution/drop/backbone/patch), cores with colour/tube, segments (parent cable), GIS route | ✅ (route is stored as JSON; no drawing editor) |
| Links with occupancy, direction, loop, core and port-status validation; verified/unverified/planned | ✅ |
| Trace any endpoint to the OLT; downstream impact | ✅ |

### ONT
| Feature | Status |
|---|---|
| Inventory with all listed fields, search and filters | ✅ |
| Discovery (autofind) list, inspect, ignore | ✅ 🧪 / ⚠️ Huawei |
| Registration workflow with summary, validation, dry run, approval, read-back verification, FAT port assignment | ✅ 🧪 |
| Normalized + vendor state; unreachable ≠ offline | ✅ |
| Optical metrics, history, configurable thresholds, declining-signal view | ✅ 🧪 |
| Reboot / enable / disable (permission, typed confirmation, audit) | ✅ 🧪 |
| Deregister (through a provisioning job) | ✅ 🧪 |
| Automatic sync of ONTs configured on the OLT | ✅ 🧪 |

### Monitoring
| Feature | Status |
|---|---|
| Scheduled polling per OLT, configurable interval, minimum enforced | ✅ |
| Device metrics, port metrics, optical history | ✅ |
| Alarm ingestion with vendor ids/severity; NMS-derived alarms | ✅ 🧪 |
| Alarm lifecycle (new/ack/investigating/resolved/reopened), history, bulk acknowledge | ✅ |
| Outage correlation (OLT, port, board, LOS cluster → common upstream element), confirmed/inferred/unknown evidence | ✅ |
| Manual infrastructure faults → incidents | ✅ |
| SNMP traps (push) | ❌ (polling only) |
| Notifications (e-mail/SMS/webhook) | ❌ (shown in the UI only) |

### Provisioning
| Feature | Status |
|---|---|
| Bandwidth profiles, service templates, vendor mappings, VLAN register, OLT profile sync/manual entry | ✅ |
| Register / activate / modify / suspend / restore / disconnect / deregister | ✅ 🧪 (⚠️ Huawei: implemented, writes disabled by default) |
| All required states, job ids, history, plan review, approval, verification | ✅ |
| Rollback (compensation), partial success, unknown-outcome handling, safe retry | ✅ 🧪 |
| Integration contract for a future billing system (idempotent) | ✅ |
| Profile creation on the OLT, ONT-side WAN/PPPoE configuration, multi-service bundles, QinQ-specific Huawei syntax | ❌ |

### Security & administration
| Feature | Status |
|---|---|
| Login, server-side sessions, logout/revocation, lockout, rate limits | ✅ |
| 30 permissions, 5 built-in roles, custom roles | ✅ |
| Encrypted credentials, redaction in API/audit/jobs/logs | ✅ |
| Audit log with filters | ✅ |
| Application settings (FAT terminology, capacity warning, integration auto-approve) | ✅ |
| Multi-tenant isolation | ❌ (single tenant per deployment; see ARCHITECTURE §5) |
| SSO / MFA | ❌ |

## Unsupported vendor operations (current adapters)

* **Huawei SmartAX CLI (experimental):**
  - PON port monitoring, profile retrieval, VLAN creation, traffic-table creation and
    configuration backup are not implemented
  - CPU/memory/temperature are not retrieved
  - SSH host keys are auto-accepted (pin them for production; see code comment)
* **Standard SNMP:** everything except connection test, identity, reachability/uptime and interface state.
* **ZTE, FiberHome, V-SOL, Nokia, Calix SMx, Adtran Mosaic:** planned only. Nothing is implemented.

## Verified against physical equipment

None.

## Outstanding limitations / next steps

1. Validate the Huawei adapter on a lab OLT:
   - capture real outputs and replace the synthetic fixtures
   - correct the syntax per firmware
   - promote each capability's verification level
   - pin SSH host keys
2. Read the Splynx Huawei module docs and the Sonar knowledge base once those hosts are
   allowed, and reconcile [REFERENCE_RESEARCH.md](REFERENCE_RESEARCH.md).
3. Implement SNMP trap reception and outbound notifications.
4. Build and test the Docker images in an environment with Docker.
5. Add a GIS import adapter (e.g. VETRO/KML) and a map-based cable route editor.
6. Add multi-tenancy if the EazPay integration requires it.
7. Load-test topology and impact queries on large networks; add caching where needed.
