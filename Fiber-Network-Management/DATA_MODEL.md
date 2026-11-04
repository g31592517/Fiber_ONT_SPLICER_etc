# Data model

40 tables, defined in `backend/app/models/` and created by `backend/alembic/versions/0001_initial_schema.py`.
All timestamps are stored as timezone-aware UTC. Enumerated values are stored as strings
(vocabularies in `app/core/enums.py`), so new vendor states never require a migration.
Every record produced from simulated equipment has `is_simulated = true`.

## 1. Active equipment

```
sites 1─* olts 1─* olt_boards (frame, slot)     ← chassis/shelf → slot → board
              │        1─* olt_ports (frame, slot, port, port_type=pon|uplink)
              ├─* olt_profiles (kind=line|service|dba|traffic_table, vendor_ref)
              ├─* vlans
              ├─* device_metrics (time series)        olt_ports 1─* port_metrics (time series)
              └─* ont_discoveries (autofind; NOT registration)
olt_ports 1─* onts ─* ont_status_events, optical_readings (time series)
```

| Table | Key fields | Notes |
|---|---|---|
| `sites` | name, code, site_type, address, lat/lng | POP / central office / cabinet |
| `olts` | adapter, manufacturer, model, serial, firmware, host, management_protocol/port, rack_location, lat/lng, slot_count, pon_port_count, admin_status, connection_status, last_contact_at, poll_interval_seconds, `*_enc` credentials, SNMP v2c/v3 settings, cpu/memory/temperature/uptime | Credentials encrypted (Fernet). Health fields stay NULL when the adapter does not support them ("unsupported", never estimated) |
| `olt_boards` | frame, slot, installed, board_type, role, status (normal/failed/disabled/empty/unknown), vendor_status, serial, versions, source (device/manual) | Empty slots are rows with `installed=false` |
| `olt_ports` | frame/slot/port, port_type, technology, admin/oper status, vendor_status, max_onts, tx_power, online_onts | Flexible for any F/S/P architecture |
| `olt_profiles` | kind, vendor_ref, name, parameters, source, last_synced_at | Objects that exist **on the OLT** |
| `vlans` | vlan_id, purpose, olt_id (NULL = global) | VLAN register used during validation |
| `onts` | serial (unique), olt, pon_port, ont_index, vendor/model/equipment_id/hw/fw/mac, auth_method, loid, auth_password_enc, line/service profile, registration_status, admin_status, **status** (normalized) + **vendor_status** (raw), last_down_cause, last online/offline, latest optical snapshot, optical_status, distance, installation_date, vendor_config (GEM/T-CONT JSON) | Optional vendor fields are nullable |
| `ont_discoveries` | olt, pon_port, serial, vendor_id, model, versions, loid, rx, vendor_reported (raw, secrets removed), first/last seen, state (unregistered/registered/ignored/gone) | Discovery ≠ registration |
| `ont_status_events` | from/to status, vendor_status, reason | Every normalized state change |
| `optical_readings` | rx, tx, olt_rx, temperature, voltage, bias, **source** (adapter key) | History; `source` marks simulated data |
| `device_metrics`, `port_metrics` | reachability, latency, cpu, mem, temp, ONT counts / port status | History |
| `optical_thresholds` | vendor, model, rx warn/crit low/high, OLT-rx warn/crit | Most specific match wins |

## 2. Passive infrastructure and connectivity

```
splitter_types 1─* splitters ──(housed in)── fats
                     │                         │
              infra_ports(owner=splitter,     infra_ports(owner=fat, direction=through)
                direction=in|out)
distribution_points (odf/odb/closure/splice_point/termination) ─ infra_ports(owner=dpoint, through)
cables 1─* fiber_cores          cables ─(parent_cable_id)─ cables   (segments)

fiber_links:  a_type/a_id  ──►  b_type/b_id     (+ cable_id, core_id, link_type, verification, status, length, loss)
   endpoint types:  olt_port | infra_port | ont
```

### Port and link rules (enforced by `core/topology.validate_link`)

| Endpoint | Can be `a` (upstream end) | Can be `b` (downstream end) | Internal traversal |
|---|---|---|---|
| OLT PON port | once | never | — |
| Splitter `in` port | never | once | fans out to **all** outputs of the same splitter |
| Splitter `out` port | once | never | — |
| FAT / ODF / closure `through` port | once | once | passes straight through |
| ONT | never | once | terminal |

Additional checks:
- no loops (b's element may not be upstream of a)
- a fiber core cannot carry two live links, and must belong to the selected cable
- damaged cores, faulty ports and reserved ports are refused unless explicitly allowed
- an ONT cannot connect directly to an OLT port

### Verification
Each link is `verified`, `unverified` or `planned`. A trace reports `complete` (reaches an OLT
port) and `all_verified`, so estimated or planned infrastructure is never presented as confirmed.

### FAT ports
Each port has an administrative status (`available`, `reserved` with `reserved_for`/`until`,
or `faulty`), plus drop cable, `connected_since` and notes. **`occupied` is derived** from
live links, so the same physical port cannot be counted as assigned twice. FAT capacity and
utilization are computed from the ports (`capacity_summary`). Terminology (FAT/FDB/FDT/…) is
the `terminology.fat` setting and does not affect the model.

### Example: full path for one subscriber (seeded demo)

```
SIM-OLT-CENTRAL PON 0/1/0 ─patch─► ODF P1 ─feeder (SIM-FDR core 1)─► CLOSURE P1 ─splice─►
SPL-L1-01 (1:4) IN → OUT2 ─distribution (SIM-DST core 2)─► SPL-F002 (1:8) IN → OUT1 ─patch─►
SIM-FAT-002 P1 ─drop─► ONT SIMU… ─(service)─► subscriber SIM-CUST-…
```

## 3. Services and provisioning

| Table | Notes |
|---|---|
| `subscribers` | customer_ref, display_name, account_ref, **external_customer_id / external_account_id** (optional, not foreign keys), address, coordinates |
| `services` | subscriber, service_ref, **external_service_id / external_package_id**, ont, template, status (pending/active/suspended/disconnected/failed), activation_date, `network_config` (service-port indexes actually created on the device) |
| `bandwidth_profiles` | vendor-neutral speeds |
| `service_templates` | service type, bandwidth profile, VLAN mode, S/C/user VLAN, GEM, T-CONT, WAN mode |
| `template_vendor_mappings` | adapter (+ optional OLT model) → line/service/DBA profile refs, traffic tables, parameters. **Vendor-specific values live only here** |
| `provisioning_jobs` | UUID, operation, state, targets, request (no secrets; auth password encrypted), validation, plan, verification, result, attempt, retry_of, approvals, external_request_id |
| `provisioning_steps` | ordered steps with params, destructive flag, state, output (commands sent), error, compensation |
| `provisioning_events` | state-transition history |

## 4. Monitoring and operations

| Table | Notes |
|---|---|
| `alarms` | alarm_key (dedupe), origin (device/nms/manual), source, OLT/site, normalized category + severity, vendor id/severity/raw, first/last occurrence, count, state (new/acknowledged/investigating/resolved/reopened), acknowledgement, cleared_on_device_at, resolution, incident |
| `alarm_events` | every lifecycle action (never deleted) |
| `incidents` | source element, suspected cause, detection (automatic/manual), impact snapshot (infrastructure, ONTs with evidence, subscribers), counts, start/resolution |
| `jobs` | generic background job queue |
| `simulated_devices` | JSON state of each simulated OLT (plays the role of the equipment) |

## 5. Identity

`users`, `roles`, `role_permissions`, `user_sessions` (token revocation), `audit_logs`, `app_settings`.
