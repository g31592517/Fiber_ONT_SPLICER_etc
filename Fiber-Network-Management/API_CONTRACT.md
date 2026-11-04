# API contract

* Base path: `/api/v1`. The OpenAPI specification is at `/api/openapi.json`, with interactive docs at `/api/docs` (Swagger) and `/api/redoc`.
* Authentication: `POST /api/v1/auth/login` → `{access_token, expires_in, user}`. Send `Authorization: Bearer <token>`. Tokens are bound to server-side sessions, so logout, disabling a user and role changes revoke them.
* Errors: `{"error": {"code", "message", "details"}}`. The codes are `unauthorized` (401), `permission_denied` (403), `not_found` (404), `conflict` (409), `unsupported_operation` (409), `validation_failed` / `invalid_request` (422), `rate_limited` (429) and `internal_error` (500).
* **Long-running operations never block a request.** They return `202 {"job_id", "job_type", "status", "poll_url"}`. Poll `GET /api/v1/jobs/{job_id}` until `status` is `succeeded`, `failed` or `cancelled`. Unsupported adapter operations finish as `failed` with `result = {"status": "unsupported", "capability", "reason"}`.
* Credentials are write-only (`PUT /olts/{id}/credentials`). Responses only contain `has_password`, `has_snmp_v3_auth_key`, etc.
* Lists that can grow large are paginated: `?limit=&offset=` → `{total, limit, offset, items}`.
* Every response that contains equipment data carries `is_simulated` / `source` so simulated data can be told apart.

## Key workflows

### Register a discovered ONT
```
GET  /discovered-onts?olt_id=…                        → list (state=unregistered)
GET  /discovered-onts/{id}                             → details + OLT profiles
POST /provisioning/jobs {operation:"register_ont", discovery_id, line_profile_ref, service_profile_ref,
                         ont_index?, auth_method, loid?, auth_password?, fat_port_id?, description?}
     → 201 job (state validation_pending)  … worker validates + device dry run …
GET  /provisioning/jobs/{id}                           → state awaiting_approval, plan, validation, dry_run
POST /provisioning/jobs/{id}/approve {confirm?}        → queued → running → succeeded (verified) | failed | rolled_back
```

### Activate / change / suspend / restore / disconnect a service
```
POST /provisioning/jobs {operation:"activate_service", service_id, template_id, discovery_id?}
POST /provisioning/jobs {operation:"modify_service",   service_id, template_id}
POST /provisioning/jobs {operation:"suspend_service" | "restore_service", service_id}
POST /provisioning/jobs {operation:"disconnect_service", service_id, deregister_ont?}
POST /provisioning/jobs/{id}/approve {confirm: "<service_ref or ONT serial>"}   ← required for destructive plans
POST /provisioning/jobs/{id}/retry | /cancel
```

## Future billing-system integration contract (`/api/v1/integration/v1`)

Designed for EazPay (or any billing system) to drive network actions with its own identifiers.
It works today against the standalone service records. Use a dedicated user whose role holds
`integration.api`, plus the permissions for the requested operation
(`provisioning.request`, `provisioning.suspend`, …).

| Method | Path | Body | Result |
|---|---|---|---|
| GET | `/integration/v1/services/{external_service_id}` | — | `{external_service_id, service_ref, status, external_customer_id, external_package_id, template, ont{serial,status,rx_power_dbm}, last_job}` |
| POST | `/integration/v1/services/{external_service_id}/activate` | `{external_request_id, template_id?}` | `202 {job_id, state, auto_approved, status_url}` |
| POST | `/integration/v1/services/{external_service_id}/change-profile` | `{external_request_id, template_id}` | 202 as above |
| POST | `/integration/v1/services/{external_service_id}/suspend` | `{external_request_id}` | 202 |
| POST | `/integration/v1/services/{external_service_id}/restore` | `{external_request_id}` | 202 |
| POST | `/integration/v1/services/{external_service_id}/disconnect` | `{external_request_id, deregister_ont?}` | 202 |
| GET | `/integration/v1/jobs/{job_id}` | — | `{job_id, operation, state, error, validation_errors, finished_at}` |

* **Idempotency:** repeating a request with the same `external_request_id` returns the original job and never creates a second one.
* **Approval:** by default integration jobs stop at `awaiting_approval`. Set `integration.auto_approve = true` (Administration → Settings) to execute them automatically; this also requires the approval permission for that operation.
* **Identifiers:** `external_customer_id`, `external_account_id`, `external_service_id` and `external_package_id` are optional strings, not foreign keys. The billing system stays authoritative for commercial data.
* **Mapping packages to templates:** the caller passes `template_id`. A package → template mapping table can be added when the integration phase starts.

## Endpoint index (generated from the OpenAPI specification)

### administration

| Method | Path |
|---|---|
| DELETE | `/api/v1/roles/{role_id}` |
| GET | `/api/v1/audit-logs` |
| GET | `/api/v1/permissions` |
| GET | `/api/v1/roles` |
| GET | `/api/v1/settings` |
| GET | `/api/v1/users` |
| PATCH | `/api/v1/users/{user_id}` |
| POST | `/api/v1/roles` |
| POST | `/api/v1/users` |
| PUT | `/api/v1/roles/{role_id}` |
| PUT | `/api/v1/settings/{key}` |

### auth

| Method | Path |
|---|---|
| GET | `/api/v1/auth/me` |
| POST | `/api/v1/auth/change-password` |
| POST | `/api/v1/auth/login` |
| POST | `/api/v1/auth/logout` |

### health

| Method | Path |
|---|---|
| GET | `/api/health` |

### infrastructure

| Method | Path |
|---|---|
| DELETE | `/api/v1/distribution-points/{did}` |
| DELETE | `/api/v1/fats/{fid}` |
| DELETE | `/api/v1/links/{link_id}` |
| DELETE | `/api/v1/splitters/{sid}` |
| GET | `/api/v1/cables` |
| GET | `/api/v1/cables/{cid}` |
| GET | `/api/v1/distribution-points` |
| GET | `/api/v1/distribution-points/{did}` |
| GET | `/api/v1/fats` |
| GET | `/api/v1/fats/{fid}` |
| GET | `/api/v1/impact/{element_type}/{element_id}` |
| GET | `/api/v1/links` |
| GET | `/api/v1/splitter-types` |
| GET | `/api/v1/splitters` |
| GET | `/api/v1/splitters/{sid}` |
| GET | `/api/v1/trace/endpoint` |
| GET | `/api/v1/trace/ont/{ont_id}` |
| PATCH | `/api/v1/fiber-cores/{core_id}` |
| PATCH | `/api/v1/infra-ports/{port_id}` |
| PATCH | `/api/v1/links/{link_id}` |
| POST | `/api/v1/cables` |
| POST | `/api/v1/distribution-points` |
| POST | `/api/v1/fats` |
| POST | `/api/v1/infrastructure/fault` |
| POST | `/api/v1/links` |
| POST | `/api/v1/links/validate` |
| POST | `/api/v1/splitter-types` |
| POST | `/api/v1/splitters` |
| PUT | `/api/v1/cables/{cid}` |
| PUT | `/api/v1/distribution-points/{did}` |
| PUT | `/api/v1/fats/{fid}` |
| PUT | `/api/v1/splitters/{sid}` |

### integration (future billing system)

| Method | Path |
|---|---|
| GET | `/api/v1/integration/v1/jobs/{job_id}` |
| GET | `/api/v1/integration/v1/services/{external_service_id}` |
| POST | `/api/v1/integration/v1/services/{external_service_id}/activate` |
| POST | `/api/v1/integration/v1/services/{external_service_id}/change-profile` |
| POST | `/api/v1/integration/v1/services/{external_service_id}/{action}` |

### integrations

| Method | Path |
|---|---|
| GET | `/api/v1/adapters` |
| GET | `/api/v1/integrations/health` |
| GET | `/api/v1/simulator/events` |
| GET | `/api/v1/simulator/olts/{olt_id}/state` |
| POST | `/api/v1/simulator/olts/{olt_id}/events` |

### monitoring

| Method | Path |
|---|---|
| DELETE | `/api/v1/optical-thresholds/{tid}` |
| GET | `/api/v1/alarms` |
| GET | `/api/v1/alarms/{alarm_id}` |
| GET | `/api/v1/dashboard` |
| GET | `/api/v1/incidents` |
| GET | `/api/v1/incidents/{iid}` |
| GET | `/api/v1/optical-thresholds` |
| GET | `/api/v1/optical/overview` |
| PATCH | `/api/v1/incidents/{iid}` |
| POST | `/api/v1/alarms/acknowledge` |
| POST | `/api/v1/alarms/{alarm_id}/actions` |
| POST | `/api/v1/incidents` |
| POST | `/api/v1/optical-thresholds` |
| PUT | `/api/v1/optical-thresholds/{tid}` |

### network

| Method | Path |
|---|---|
| DELETE | `/api/v1/olt-profiles/{profile_id}` |
| DELETE | `/api/v1/olts/{olt_id}` |
| DELETE | `/api/v1/sites/{site_id}` |
| DELETE | `/api/v1/vlans/{vlan_pk}` |
| GET | `/api/v1/jobs` |
| GET | `/api/v1/jobs/{job_id}` |
| GET | `/api/v1/olts` |
| GET | `/api/v1/olts/{olt_id}` |
| GET | `/api/v1/olts/{olt_id}/hardware` |
| GET | `/api/v1/olts/{olt_id}/jobs` |
| GET | `/api/v1/olts/{olt_id}/metrics` |
| GET | `/api/v1/olts/{olt_id}/profiles` |
| GET | `/api/v1/pon-ports` |
| GET | `/api/v1/pon-ports/{port_id}` |
| GET | `/api/v1/pon-ports/{port_id}/metrics` |
| GET | `/api/v1/sites` |
| GET | `/api/v1/sites/{site_id}` |
| GET | `/api/v1/vlans` |
| PATCH | `/api/v1/pon-ports/{port_id}` |
| POST | `/api/v1/olts` |
| POST | `/api/v1/olts/{olt_id}/actions/{action}` |
| POST | `/api/v1/olts/{olt_id}/boards` |
| POST | `/api/v1/olts/{olt_id}/profiles` |
| POST | `/api/v1/sites` |
| POST | `/api/v1/vlans` |
| PUT | `/api/v1/olts/{olt_id}` |
| PUT | `/api/v1/olts/{olt_id}/credentials` |
| PUT | `/api/v1/sites/{site_id}` |

### onts

| Method | Path |
|---|---|
| GET | `/api/v1/discovered-onts` |
| GET | `/api/v1/discovered-onts/{discovery_id}` |
| GET | `/api/v1/onts` |
| GET | `/api/v1/onts/{ont_id}` |
| GET | `/api/v1/onts/{ont_id}/history` |
| PATCH | `/api/v1/onts/{ont_id}` |
| POST | `/api/v1/discovered-onts/{discovery_id}/ignore` |
| POST | `/api/v1/onts/{ont_id}/operations` |
| POST | `/api/v1/onts/{ont_id}/refresh` |

### services & provisioning

| Method | Path |
|---|---|
| GET | `/api/v1/bandwidth-profiles` |
| GET | `/api/v1/provisioning/jobs` |
| GET | `/api/v1/provisioning/jobs/{job_id}` |
| GET | `/api/v1/service-templates` |
| GET | `/api/v1/service-templates/{tid}` |
| GET | `/api/v1/services` |
| GET | `/api/v1/services/{sid}` |
| GET | `/api/v1/subscribers` |
| GET | `/api/v1/subscribers/{sid}` |
| PATCH | `/api/v1/services/{sid}` |
| POST | `/api/v1/bandwidth-profiles` |
| POST | `/api/v1/provisioning/jobs` |
| POST | `/api/v1/provisioning/jobs/{job_id}/approve` |
| POST | `/api/v1/provisioning/jobs/{job_id}/cancel` |
| POST | `/api/v1/provisioning/jobs/{job_id}/retry` |
| POST | `/api/v1/service-templates` |
| POST | `/api/v1/services` |
| POST | `/api/v1/subscribers` |
| PUT | `/api/v1/bandwidth-profiles/{bid}` |
| PUT | `/api/v1/service-templates/{tid}` |
| PUT | `/api/v1/subscribers/{sid}` |

### topology

| Method | Path |
|---|---|
| GET | `/api/v1/topology/graph` |
| GET | `/api/v1/topology/map` |
| GET | `/api/v1/topology/search` |
