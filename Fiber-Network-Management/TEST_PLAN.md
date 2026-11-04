# Test plan

## 1. How to run

```bash
cd backend
../.venv/bin/python -m pytest                                    # full suite on SQLite (temporary DB)
FNMS_TEST_DATABASE_URL=postgresql+psycopg://user:pass@host/fnms_test ../.venv/bin/python -m pytest   # same suite on PostgreSQL (WIPES that database)
../.venv/bin/alembic check                                       # models vs migrations

cd ../frontend
npm run build                                                    # TypeScript type-check + production build

# Browser tests (Playwright) against a running stack (API + worker + UI on :4173, demo data):
npm run build && npx vite preview --port 4173 &
node e2e/smoke.cjs            # visits every route and tab, fails on JS errors or error boxes
node e2e/flow.cjs             # end-to-end workflow, writes screenshots to e2e-output/shots
```

The backend suite seeds the reference data and the full simulated demo network once per
session. Background jobs are executed synchronously through `run_pending()`, the same code
path the worker uses.

## 2. Coverage by acceptance criterion

### A. Infrastructure (`tests/test_infrastructure.py`)
| Criterion | Test |
|---|---|
| Create and edit an OLT, connection info | `test_create_site_and_olt_with_credentials` |
| Test an OLT connection through the adapter | `test_connection_test_through_adapter` |
| Slots, boards and PON ports | `test_hardware_hierarchy` |
| Create sites, FATs, configure FAT ports | `test_create_site_and_olt_with_credentials`, `test_fat_ports_capacity_and_reservation` |
| Create splitters (cascaded), cables, connections | `test_splitter_cascade_links_trace_and_duplicate_prevention` |
| Trace the path from OLT to subscriber ONT | `test_trace_registered_ont_to_olt`, `test_splitter_cascade_…` |
| Prevent duplicate physical port assignments; core reuse; loops; invalid directions | `test_splitter_cascade_…` |
| Planned/unverified paths not shown as confirmed | `test_planned_paths_are_not_confirmed` |
| Topology graph and map data | `test_topology_graph_and_map` |
| Find subscribers by ONT serial, FAT, PON port | `test_find_subscriber_by_ont_fat_and_port` |
| Planned adapters cannot be selected; polling interval minimum | `test_planned_adapter_cannot_be_selected`, `test_poll_interval_minimum_enforced` |

### B. ONT management (`tests/test_onts_monitoring.py`)
| Criterion | Test |
|---|---|
| Populate the discovered list from the simulator; inspect; discovery ≠ registration | `test_discovery_list_from_simulator` |
| Register; associate with PON port; assign profiles; FAT port; status; optical; history; not associated with a subscriber | `test_register_discovered_ont_with_profiles_and_fat_port` |
| Reboot/disable/enable with confirmation, permission and audit | `test_ont_operations_require_confirmation_and_permission` |

### C. Monitoring (`tests/test_onts_monitoring.py`)
| Criterion | Test |
|---|---|
| Detect ONT status changes; create, acknowledge, investigate, clear and reopen alarms; history kept | `test_status_change_detected_and_alarm_lifecycle` |
| Resolve alarms manually | `test_manual_alarm_resolution` |
| OLT communication failure is not ONT failure | `test_olt_communication_failure_is_not_ont_failure` |
| PON port failure → incident with confirmed impact | `test_pon_port_failure_outage_correlation` |
| LOS cluster → common upstream element | `test_los_cluster_points_at_common_upstream_element` |
| Manual infrastructure fault → inferred impact, ONTs not marked down | `test_manual_infrastructure_fault_shows_inferred_impact` |
| Thresholds (default + model-specific), declining signal | `test_optical_thresholds_and_overview` |
| Historical device metrics; supported metrics retrieved | `test_device_metrics_history_and_unsupported_values` |

### D. Provisioning (`tests/test_provisioning.py`)
| Criterion | Test |
|---|---|
| Create service templates (with validation) | `test_service_template_crud_and_validation` |
| Validate, plan, execute, verify; activate → suspend → restore → modify → disconnect + deregister; device state checked | `test_activate_suspend_restore_modify_disconnect` |
| Destructive approval requires typed confirmation; cancel | `test_destructive_operation_needs_confirmation` |
| Record failed jobs; rollback; safe retry; single retry | `test_failure_rollback_and_safe_retry` |
| Partial success without rollback; retry does not repeat applied steps | `test_partial_success_without_rollback_and_idempotent_retry` |
| Validation failure recorded and blocks approval | `test_validation_failures_are_recorded` |
| Unverified adapters blocked from writes | `test_unverified_adapter_writes_blocked` |
| Integration contract idempotency | `test_integration_contract_idempotent` |

### E. Security (`tests/test_security.py`)
| Criterion | Test |
|---|---|
| Credentials never returned (API, audit, jobs) | `test_create_site_and_olt_with_credentials`, `test_no_credentials_in_any_olt_response` |
| Unauthorized users cannot execute privileged actions | `test_requires_authentication`, `test_monitoring_operator_cannot_provision_or_change_devices`, `test_technician_limited_operations`, `test_netadmin_cannot_approve_without_permission_scope` |
| User/role management audited; disabling a user revokes sessions | `test_user_and_role_management_audited` |
| Logout revokes tokens; lockout; rate limiting | `test_logout_revokes_token`, `test_account_lockout`, `test_rate_limiter` |
| Secrets encrypted at rest and redacted; logs scrubbed | `test_secrets_encrypted_and_redacted`, `test_logs_scrub_secrets` |

### F. Application quality
| Criterion | Evidence |
|---|---|
| Independent installation | `dev.sh`; README; no EazPay dependency anywhere in the code |
| Migrations run | `alembic upgrade head` + `alembic check` on SQLite and PostgreSQL 16 |
| Backend builds; frontend builds | pytest import of the full app; `npm run build` |
| API documentation | `test_openapi_available`; `/api/docs` |
| Adapter contract | `tests/test_adapters.py`: every adapter declares every capability with a verification level; planned and SNMP adapters return structured `unsupported`; Huawei parsers/builders (synthetic fixtures) |

## 3. Results (last run, 2026-11-04)

| Check | Result |
|---|---|
| `pytest` on SQLite | **52 passed** |
| `pytest` on PostgreSQL 16 | **52 passed** |
| `alembic upgrade head` + `alembic check` (SQLite, PostgreSQL) | OK, no drift |
| `npm run build` (tsc + vite) | OK |
| `e2e/smoke.cjs`: 40 routes + OLT/ONT tabs | No JavaScript errors. The only console messages are OpenStreetMap tile requests blocked by the sandbox's network policy, which is the offline case the map's schematic fallback handles |
| `e2e/flow.cjs`: login → register a discovered ONT via the wizard → plan review → approve with confirmation → succeeded → ONT/OLT/FAT/topology/incident/map/integrations → simulator LOS → alarm list → tablet layout | Passed |

## 4. Not covered / not possible here
* No tests against physical OLTs: no hardware or credentials were available.
* The Huawei adapter is tested only with synthetic fixtures. The SSH transport (paramiko) and the SNMP transport (pysnmp) are not exercised against live endpoints.
* The Docker images were not built (no Docker daemon in the environment).
* No load or performance tests. The topology graph is computed per request and is intended for networks of up to a few thousand elements per view; larger networks should use the site/OLT filters.
