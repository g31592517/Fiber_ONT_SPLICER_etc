# Reference research: Splynx and Sonar

## 0. Source access — read this first

The research was done from inside a sandboxed development environment with an egress allow-list.

| Source | Status (2026-11-04) |
|---|---|
| `splynx.com` (product pages) | **Read** |
| `docs.splynx.com` | Returned HTTP 502 |
| `wiki.splynx.com` (the real Splynx documentation host, linked from splynx.com) | **Blocked** by the environment's network policy |
| `sonar.software` (product pages) | **Read** |
| `docs.sonar.express` (URL given in the brief) | Did not resolve / blocked. The knowledge-base link on sonar.software points to **`docs.sonar.expert`** |
| `docs.sonar.expert` | **Blocked** by the environment's network policy |
| `support.huawei.com`, `info.support.huawei.com` | Reachable, but they serve JavaScript-rendered pages with no readable command reference. No Huawei command syntax was verified |

**Consequence.** Section 1 records only what the official pages that could be read actually
say, with URLs. Section 2 contains the operational requirements used for the design. They come
from general GPON/OMCI domain knowledge and are marked **UNVERIFIED** against Splynx/Sonar
documentation. To complete the verification, allow `wiki.splynx.com` and `docs.sonar.expert`
and review the pages listed in section 4.

## 1. Verified findings (official pages read)

### Splynx

| Finding | Source |
|---|---|
| "Splynx is compatible with any hardware or software that is managed via RADIUS, SNMP, API, TR-069 or SSH" | https://splynx.com/network-management/ |
| Monitoring & backups: "SNMP, PING, SSH · Monitoring · Backup · Change management" | https://splynx.com/network-management/ |
| "SNMP monitoring forms a part of Splynx and allows notifying admins in case equipment goes offline. Network weathermaps show your network topology as well as live usage of links between sites." | https://splynx.com/network-management/ |
| "…our SSH backup tool allows to backup any device and also track the changes in configurations." | https://splynx.com/network-management/ |
| "The program already has modules for Huawei OLT management, Mikrotik queue bandwidth control, Preseem QoE, and Whalebone secure DNS platforms." | https://splynx.com/network-management/ |
| Integration catalogue entry: "Huawei — Enables you to manage a Huawei GPON from within Splynx." The entry links to `https://wiki.splynx.com/addons_modules/huawei-olt` (blocked) | https://splynx.com/integrations/ |
| Inventory: equipment tracked by barcode/serial, multi-location, "auto-updating equipment statuses", returns verified by barcode or serial number | https://splynx.com/isp-inventory-management/ |

### Sonar

| Finding | Source |
|---|---|
| "Sonar talks to Calix, Adtran, Cambium, and MikroTik out of the box. IPAM, monitoring, and provisioning live in one place so fiber subscribers activate without manual overrides." | https://sonar.software/industries/fiber |
| Capabilities listed: "Multi-vendor provisioning templates", "Outage detection across fiber, FWA, and edge devices", "IPAM with subnet pool management and collision detection" | https://sonar.software/industries/fiber |
| Activation flow: "IP allocated → Equipment registered (Serials matched to subscriber and warehouse pulled) → Service activated (Vendor-neutral provisioning template fires automatically) → Health monitored (Multi-vendor monitoring) → Alert escalated (Critical events open tickets routed to the right tech)" | https://sonar.software/industries/fiber |
| Calix: "Streamline fiber from provisioning to troubleshooting so the network runs at peak efficiency." | https://sonar.software/solutions/partner-integrations |
| Adtran Mosaic: "Automate service provisioning and link customer accounts directly to network data." | https://sonar.software/solutions/partner-integrations |
| VETRO: "Keep customer and network data in sync for serviceability and address validation, and visualize your fiber network inside Sonar." | https://sonar.software/solutions/partner-integrations |
| Positron: provisioning for brownfield MDUs "with Calix SMx" | https://sonar.software/solutions/partner-integrations |
| Knowledge base is linked as `https://docs.sonar.expert` | https://sonar.software/ (site navigation) |

### What these confirm for the design

1. **Both platforms are multi-vendor and adapter-based.** Splynx uses per-vendor modules
   (Huawei OLT); Sonar uses per-vendor or per-platform integrations (Calix, Adtran Mosaic).
   → Adapter layer with declared capabilities.
2. **Sonar integrates with vendor *management platforms*** (Calix SMx, Adtran Mosaic) rather
   than only individual OLTs. → The adapter contract must allow platform adapters, not just
   per-OLT CLI (`calix_smx` and `adtran_mosaic` are registered as planned platform adapters).
3. **Templates drive provisioning** ("vendor-neutral provisioning template"). → Vendor-neutral
   `service_templates` plus per-adapter `template_vendor_mappings`.
4. **Equipment is matched to subscribers by serial number.** → The ONT serial is the unique
   identity; services link a subscriber to an ONT.
5. **Monitoring is SNMP/ping/SSH-based, with offline notifications and outage detection.**
   → Monitoring engine, alarms and incident correlation.
6. **Fiber mapping/GIS comes from an external system (VETRO) synced into the platform.**
   → GIS is optional. Physical topology lives in our own model, and the map view works
   without any external provider.
7. **Configuration backup and change tracking over SSH.** → `configuration_backup` capability.

## 2. Derived operational requirements (UNVERIFIED against Splynx/Sonar docs)

These come from general GPON (ITU-T G.984/G.988 OMCI) and ISP operations practice. They are
how this product implements each capability. They are **not** claims about how Splynx or
Sonar implement them.

| Capability | Information stored | Supplied by | Retrieved how | Remote operations | Effect on subscriber | Errors / failure handling |
|---|---|---|---|---|---|---|
| OLT inventory & health | identity, firmware, boards, ports, CPU/mem/temp | OLT (or vendor platform) | SSH CLI, SNMP (vendor MIBs), vendor API | test connection, poll, backup | none | Unreachable → OLT alarm + incident; ONT states become *unknown*, not *offline* |
| ONT discovery | serial, F/S/P, vendor/equipment id, versions, LOID, Rx | OLT autofind table | CLI / SNMP / API | none (read only) | none | Device seen on a different port, or already registered → conflict shown |
| ONT registration | ONT ID, auth (SN/LOID/password), line & service profile | operator → OLT | CLI `ont add` (vendor-specific) / API | register, deregister | ONT becomes manageable; **no service yet** | Rejected profile or ID → validation/dry-run error; failure after accept → compensating deregister |
| Profiles (line/service/DBA/traffic table) | IDs and names on the OLT | OLT | CLI listing / API | (creation on OLT is out of scope) | determine T-CONT/GEM/bandwidth | Missing profile → validation error/warning |
| Service activation | VLAN(s), GEM, user VLAN, traffic tables, service-port index | template + mapping → OLT | CLI `service-port` / API | create/delete/enable/disable service-port | Internet service flows | Partial application → rollback or partially_succeeded; verify by read-back |
| Suspension / restoration | service-port admin state | billing event → NMS → OLT | as above | disable/enable service-port (or deactivate ONT) | Service stops/resumes; ONT stays registered | Idempotent; verify admin state |
| ONT status | normalized + raw state, last down cause | OLT | CLI / SNMP / API | refresh | — | LOS vs dying gasp vs power-off distinguished |
| Optical levels | ONT Rx/Tx, OLT Rx, temp, voltage, bias | ONT via OMCI, relayed by the OLT | CLI / SNMP / API | refresh | — | Absent values stored as NULL, never estimated |
| Alarms | vendor id, severity, time, source | OLT / platform | CLI / SNMP traps / API | acknowledge (in NMS) | — | Disappearing alarms are resolved, history kept |
| Outage detection | incident, affected elements and subscribers | NMS correlation | topology + statuses | — | — | Confirmed vs inferred vs unknown evidence |
| Physical topology / GIS | FAT, splitter, cable, core, links, coordinates | operator / GIS tool | manual entry / future import | — | — | Planned or unverified links flagged |

## 3. Translation into this product (no UI copying)

| Reference concept | Our implementation |
|---|---|
| Splynx Huawei OLT module | `adapters/huawei` (experimental, unverified) behind the generic `VendorAdapter` contract |
| Sonar Calix/Adtran integrations | Planned platform adapters (`calix_smx`, `adtran_mosaic`); same contract |
| Sonar provisioning templates | `service_templates` (vendor-neutral) + `template_vendor_mappings` (per adapter/model) |
| Unconfigured ONT list | `ont_discoveries`, separate from `onts` |
| Network sites | `sites` |
| SNMP/ping monitoring + notifications | `engines/monitoring.py`, `alarms`, dashboard counts |
| Outage detection | `engines/correlation.py`, `incidents` with impact evidence |
| VETRO FiberMap sync | Own physical model (`fats`, `splitters`, `cables`, `fiber_links`) + optional GIS map; an import adapter can be added later |
| Suspend on delinquency | `suspend_service` / `restore_service` operations and `/integration/v1/services/{id}/suspend` |

## 4. Pages to review when access is available

- `https://wiki.splynx.com/addons_modules/huawei-olt`: OLT connection fields, ONT
  autofind/registration flow, service-port templates, supported models and limitations.
- `https://docs.sonar.expert`: Calix SMx integration, Adtran Mosaic integration, network
  monitoring/alerting, network sites, inventory/ONT assignment, VETRO integration, account
  status → network actions (delinquency).
- Huawei MA5800 / MA5600T command reference for the target firmware: confirm or correct
  every builder and parser in `backend/app/adapters/huawei/cli.py`.
