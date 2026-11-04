# Provisioning

## 1. Model

| Layer | Entity | Vendor-specific? |
|---|---|---|
| Speed | `bandwidth_profiles` (download/upload kbps) | no |
| Service definition | `service_templates`: service type, bandwidth profile, VLAN mode (single / QinQ / transparent), S-VLAN, C-VLAN, user VLAN, GEM port, T-CONT, WAN mode | no |
| Translation | `template_vendor_mappings`: adapter (+ optional OLT model) → line profile, service profile, DBA profile, traffic table up/down, extra parameters | **yes, and only here** |
| Device objects | `olt_profiles`: line/service/DBA profiles and traffic tables that exist on each OLT (synced from the device or recorded manually) | yes |
| Network-side state | `services.network_config`: service-port indexes actually created, with VLAN/GEM/traffic refs | as reported by the adapter |

The provisioning engine never assumes that one manufacturer's configuration works on another.
A template without a mapping for the OLT's adapter (and model) fails validation.

## 2. Operations

| Operation | Plan steps (abstract) | Destructive | Approval permission |
|---|---|---|---|
| `register_ont` | `register_ont` → `verify_ont` | no | `ont.register` |
| `activate_service` | [`register_ont` → `verify_ont` if the ONT is new] → `create_service_port` → `verify_service` | no | `provisioning.approve` |
| `modify_service` | `delete_service_port` (old) → `create_service_port` (new) → `verify_service` | yes (brief interruption; warning shown) | `provisioning.approve` |
| `suspend_service` | `set_service_port_admin(disabled)` per port → `verify_service` | no | `provisioning.suspend` |
| `restore_service` | `set_service_port_admin(enabled)` per port → `verify_service` | no | `provisioning.suspend` |
| `disconnect_service` | `delete_service_port` per port → [`deregister_ont`] → `verify_service` | yes | `provisioning.disconnect` |
| `deregister_ont` | `deregister_ont` → `verify_absent` | yes | `ont.deregister` |

Creating a request needs `provisioning.request` (or `ont.register` for registrations).
Destructive plans also need a **typed confirmation** (the ONT serial or the service reference),
checked by the API.

## 3. Workflow

1. The operator selects an ONT, either a discovered one or a registered one.
2. They associate it with a subscriber/service reference.
3. They choose a service template, or line/service profiles for registration only.
4. **Validation (worker)**, local checks:
   - OLT enabled and reachable; adapter capability present; write permitted (experimental adapters blocked)
   - discovery still `unregistered`; serial not already registered
   - PON port up and below `max_onts`; ONT ID free
   - profiles and traffic tables known on the OLT (warning if not synced)
   - template active, mapping present, valid VLAN (warning if not in the VLAN register)
   - FAT port available (reserved ports need explicit confirmation; faulty or occupied ports are refused)
   - service in the right state (e.g. cannot suspend a service that is not active)
5. **Device dry run:** the first write step is sent to the adapter with `dry_run=True`. The
   simulator validates against device state; the Huawei adapter only renders the command and
   says so. Later steps depend on earlier results and are marked *deferred*, to be validated
   by the device at execution.
6. **Plan:** ordered steps with parameters and compensations, stored on the job and shown
   for review.
7. **Approval** → `queued`.
8. **Execution** by the worker, through the adapter, step by step. Each step records the
   commands sent (secrets masked), output and timing.
9. **Verification:** read-back from the device (`verify_ont`, `verify_service`,
   `verify_absent`). A command being accepted is never accepted as proof.
10. **Result:**
    - ONT inventory, discovery state and FAT drop link are updated
    - service status, `network_config` and `activation_date` are updated
    - an audit record is written

## 4. States

```
draft → validation_pending → validation_failed
                           → awaiting_approval → queued → running → succeeded
                                                                  → failed
                                                                  → partially_succeeded
                                                                  → rolled_back
draft / validation_pending / validation_failed / awaiting_approval / queued → cancelled
```

Every transition is a `provisioning_events` row (time, from, to, actor, message).
Every job has a UUID.

## 5. Failure handling, rollback and retries

* **Idempotent steps:** adapters check the current device state and return `changed=False`
  when it already matches. Examples: the ONT is already registered with the same serial at the
  same port; an equivalent service-port exists; a port is already disabled. Such steps are
  recorded as `skipped`.
* **Rollback (compensation):** with `rollback_on_failure` (the default), completed steps that
  changed the device are undone in reverse order:
  - `register_ont` ↔ `deregister_ont`
  - `create_service_port` ↔ `delete_service_port`
  - `delete_service_port` ↔ re-create with the old parameters
  - `set_service_port_admin` ↔ the opposite state

  If every compensation succeeds the job is `rolled_back`. Otherwise it is `failed`, the step
  is marked `compensation_failed`, and an event asks for manual action.
* **Without rollback:** if some steps were applied the job is `partially_succeeded`, and the
  inventory reflects what was actually applied (e.g. the ONT is recorded as registered).
* **Communication lost mid-step:** the outcome is *unknown*. The step is marked
  "outcome UNKNOWN", the job fails, and **no automatic compensation** is attempted, because
  compensating a step that never ran could do harm. Manual verification is requested.
* **Retry:** `POST /provisioning/jobs/{id}/retry` is allowed for `failed`,
  `partially_succeeded`, `rolled_back` and `validation_failed` jobs, once per job. It creates a
  **new job** (`retry_of` = original) that is re-validated and re-planned from the *current*
  state. For example, an ONT registered by the failed attempt is not registered again. It then
  needs approval again. Configuration commands are never replayed blindly.
* **Worker crash:** jobs stuck in `running` are failed after the lock timeout, never re-run.
* **Vendors without full rollback:** an adapter step with no inverse operation has no
  compensation; the job then reports "manual action may be required" for that step.

## 6. Limitations (current build)

* Service templates provision one service-port per template. Multi-service (internet + VoIP +
  IPTV) needs one template per service; a template bundle is not implemented.
* Profile **creation** on OLTs (line/service/DBA/traffic tables) is not automated. Profiles
  must exist on the device and are referenced by ID.
* QinQ is modelled (S-VLAN + C-VLAN), passed to the adapter and enforced by validation. The
  simulator stores it; the Huawei command builder does not yet emit QinQ-specific syntax.
* ONT-side configuration (WAN/PPPoE through OMCI or TR-069) is stored in the template but not
  pushed by any adapter.
