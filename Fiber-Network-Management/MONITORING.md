# Monitoring, statuses and alarms

## 1. Polling

* The **scheduler** (inside the worker, checked every `FNMS_SCHEDULER_TICK_SECONDS`, default 15 s)
  enqueues an `olt.poll` job for every OLT that is `admin_status=enabled`, has
  `monitoring_enabled`, and whose `next_poll_at` has passed.
* Each OLT has its own interval, `poll_interval_seconds` (default 300 s). The minimum is
  `FNMS_MIN_POLL_INTERVAL_SECONDS` (default 60 s), enforced by the API to prevent aggressive polling.
* Dedupe key `poll:<olt_id>`: at most one poll per OLT is queued or running at a time.
* "Poll now", "Test connection", "Discover ONTs", "Sync profiles" and "Backup" are on-demand
  jobs (OLT page, `POST /olts/{id}/actions/{action}`).
* Polls never run inside an API request.

### What a poll does (`engines/monitoring.poll_olt`)

Each step runs only if the adapter declares the capability. Otherwise it is recorded as
`unsupported` in the job result, and the related fields stay NULL.

| # | Step | Capability | Stored in |
|---|---|---|---|
| 1 | Health/reachability (latency, CPU, memory, temperature, uptime) | `olt_health_monitoring` (falls back to `connection_test`) | `olts.*`, `device_metrics` |
| 2 | Hardware (boards per slot, empty slots, failed boards) | `device_discovery` | `olt_boards` (source=device) |
| 3 | PON and uplink ports | `pon_port_monitoring` | `olt_ports`, `port_metrics` |
| 4 | ONT status of all ONTs configured on the OLT | `ont_status` | `onts`, `ont_status_events` |
| 5 | Optical readings of online ONTs + threshold evaluation | `optical_power` | `optical_readings`, `onts.optical_status`, NMS alarms |
| 6 | Active alarms | `alarm_retrieval` | `alarms` (origin=device) |
| 7 | Autofind / discovery list | `ont_discovery` | `ont_discoveries` |
| 8 | Outage correlation | — | `incidents` |

A failure in one step does not abort the others: the OLT becomes `degraded` and
`last_poll_error` lists the failed steps.

### Automatic ONT synchronization
An ONT that is configured on the OLT but unknown to the NMS is imported as `registered` with
the note "Imported automatically from OLT configuration during sync". An ONT the NMS believes
is registered but which the OLT no longer reports becomes status `unknown`, and a *warning*
alarm "registered in the NMS but not configured on <OLT>" is raised. The NMS does not
silently delete it.

## 2. Status normalization

| Normalized `onts.status` | Meaning | Examples of vendor states (stored raw in `vendor_status`) |
|---|---|---|
| `online` | Operating | `online`, run=online |
| `offline` | Down, cause not LOS / dying gasp | `offline (power-off)` |
| `los` | Loss of signal (fiber or optical problem) | `offline (LOS)`, last down cause LOS/LOFi |
| `dying_gasp` | Lost power at the premises | `offline (dying-gasp)` |
| `registered_inactive` | Configured, never came online | `offline (never online)`, run=offline with no cause |
| `disabled` | Administratively deactivated | `deactivated` |
| `faulty` | Online but the configuration failed | config state failed |
| `discovered` | Seen in autofind (kept in `ont_discoveries`, not `onts`) | — |
| `unknown` | Not reported / missing from the OLT | `not present in OLT configuration` |
| `unreachable` | **The OLT could not be contacted, so the ONT state is unknown** | — |

### OLT communication failure ≠ ONT failure
When a poll cannot reach the OLT:
- the OLT becomes `unreachable`
- an `olt_unreachable` critical alarm is raised (origin `nms`)
- every ONT on it is set to `unreachable` (with reason "OLT communication failure — ONT state unknown")

The ONTs are **not** marked offline: their last vendor-reported state is kept and
`last_offline_at` is not touched. The incident created for the OLT reports ONT impact with
evidence **unknown**. When communication returns, the next poll restores the real states and
the incident resolves automatically.

## 3. Optical thresholds

* Configured under *Monitoring → Optical thresholds* (`/optical-thresholds`). Each threshold
  sets ONT Rx warning/critical low, optional overload (high) limits, and OLT-Rx-from-ONT limits.
* Resolution: the **most specific match wins**: vendor + model > model > vendor > default.
* The seeded default ("Example default (GPON Class B+ style) — review against your optics":
  warn ≤ −25, critical ≤ −27, overload warn ≥ −10 / critical ≥ −8, OLT-Rx warn ≤ −28 /
  critical ≤ −30 dBm) is a starting point only. Use the datasheet values of the deployed optics.
* Violations set `onts.optical_status` (`ok`/`warning`/`critical`) and raise NMS alarms
  `low_optical_signal` / `high_optical_signal`. These clear automatically when the level recovers.
* **Declining signal:** *Optical monitoring* lists ONTs whose Rx dropped by ≥ 1 dB over the last
  7 days (first reading in the window vs now), as well as the weakest current readings.
* When an ONT is not online, no optical values are stored. The UI shows "no reading", never an estimate.

## 4. Alarms

### Origin and normalization
* `origin=device`: retrieved from the equipment. The vendor alarm id, vendor severity and the
  raw record are preserved. The adapter maps each one to a normalized category and severity.
* `origin=nms`: derived by this system (OLT unreachable, threshold violations, ONT missing
  from the OLT, and LOS / dying-gasp alarms derived from status when the adapter cannot
  retrieve alarms).
* `origin=manual`: incidents opened by operators. Infrastructure faults are recorded as incidents.

Categories: `olt_unreachable`, `board_failure`, `pon_port_failure`, `ont_los`,
`ont_dying_gasp`, `low_optical_signal`, `high_optical_signal`, `power_failure`, `temperature`,
`uplink_failure`, `ont_registration_failure`, `infrastructure_fault`, `other`.
Severities: `critical`, `major`, `minor`, `warning`, `info`.

### Lifecycle
```
            raised                operator           operator
  (none) ─────────► new ───────► acknowledged ─────► investigating
                     │                 │                   │
                     └─────────────────┴───────┬───────────┘
          condition cleared on device (system) │ or operator "resolve"
                                               ▼
                                           resolved ──► reopened (condition re-occurs within 1 h, or operator)
```
* Deduplication by `alarm_key`. A repeat updates `last_occurred_at` and `occurrence_count`.
* When an alarm disappears from the device it is **resolved by `system`**, with
  `cleared_on_device_at` set. The record and its `alarm_events` history remain.
* Every action (raised, acknowledged, investigating, cleared, resolved, reopened, note,
  severity_changed) is an `alarm_events` row with actor and note.
* Bulk acknowledgement: `POST /alarms/acknowledge`.

## 5. Outage correlation (`engines/correlation.py`)

Evaluated after every poll, per OLT:

| Rule | Incident source | Severity |
|---|---|---|
| OLT unreachable | OLT | critical |
| PON port `fault`/`down` (admin enabled) with registered ONTs | PON port | critical |
| Board `failed` | board | critical |
| ≥ 2 ONTs in LOS on a port that is up | the **nearest passive element common** to all their upstream paths (FAT, splitter, closure) if ≥ 50 % of that element's downstream ONTs are in LOS; otherwise the port | major |

* Each incident stores an impact snapshot: downstream infrastructure, ONTs (physical
  dependency through recorded links, and logical dependency through registration on the
  port/board/OLT), and subscribers.
* Each ONT is tagged with **evidence**:
  - `confirmed`: its own status is down
  - `inferred`: it depends on the element, but its status does not show it down
  - `unknown`: its status cannot be determined
* Subscribers are **never** marked disconnected on inference alone.
* Correlated alarms are linked to the incident (`alarms.incident_id`).
* Automatic incidents resolve themselves when the condition clears (a note is appended).
  Operators can also set an incident to investigating or resolved, or reopen it.
* *Mark faulty* on a FAT, splitter, closure/ODF, cable or link opens a **manual** incident with
  the same impact analysis. *Mark repaired* returns the element to active.

## 6. Background job reliability
* Jobs left `running` by a stopped worker are failed after `FNMS_JOB_LOCK_TIMEOUT_SECONDS` and never re-run automatically.
* *Monitoring → Background jobs* shows the queue and warns when jobs sit queued for more than a minute (worker not running).
