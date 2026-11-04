# Vendor capability matrix

This matrix is generated from the adapters' own declarations (`GET /api/v1/adapters`; UI:
*Integrations → Vendor integrations*). The code is the source of truth. If an adapter does
not declare a capability, calling it returns a structured `unsupported` result.

## Verification levels

| Level | Meaning |
|---|---|
| **HW** `hardware_verified` | Exercised against physical equipment. **No capability in this build has this level.** |
| **SIM** `simulated` | Exercised end-to-end against the built-in simulator only |
| **UNV** `unverified` | Implemented in code, but not verified against official documentation or hardware |
| — | Not implemented. Calls return `unsupported` |

## Matrix

| Capability | `simulator` | `huawei_smartax_cli` (experimental) | `snmp_standard` | ZTE · FiberHome · V-SOL · Nokia · Calix SMx · Adtran Mosaic (planned) |
|---|---|---|---|---|
| Connection test | SIM | UNV (SSH login + prompt) | UNV (ICMP + SNMP sysDescr) | — |
| Device discovery (identity, boards) | SIM | UNV (`display version`, `display board`) | UNV (sysDescr/sysName/uptime only) | — |
| OLT health monitoring | SIM (CPU, memory, temperature, uptime, latency) | UNV (reachability + latency only) | UNV (reachability, latency, uptime) | — |
| PON port monitoring | SIM | — | UNV (IF-MIB admin/oper; port type not distinguished) | — |
| ONT discovery (autofind) | SIM | UNV (`display ont autofind all`) | — | — |
| ONT registration | SIM | UNV (`ont add … sn-auth/loid-auth … omci`) | — | — |
| ONT deregistration | SIM | UNV (`ont delete`) | — | — |
| ONT status | SIM | UNV (`display ont info`) | — | — |
| Optical power | SIM | UNV (`display ont optical-info`) | — | — |
| Profile retrieval | SIM | — | — | — |
| VLAN configuration | SIM (part of service-port) | — (VLANs must pre-exist on the OLT) | — | — |
| Bandwidth profile configuration | SIM (traffic-table reference) | — (traffic tables must pre-exist) | — | — |
| Service-port configuration | SIM | UNV (`service-port` / `undo service-port`) | — | — |
| Service activation | SIM | UNV | — | — |
| Service suspension | SIM | UNV (`service-port … adminstatus disable`) | — | — |
| Service restoration | SIM | UNV (`… adminstatus enable`) | — | — |
| Alarm retrieval | SIM | UNV (best-effort parser) | — | — |
| Configuration backup | SIM | — | — | — |
| ONT reboot | SIM | UNV (`ont reset`) | — | — |
| ONT enable/disable | SIM | UNV (`ont activate`/`deactivate`) | — | — |

## Safeguards for unverified adapters

* `huawei_smartax_cli` is `experimental=True`. Every write capability is **blocked** unless the
  operator sets `FNMS_ALLOW_UNVERIFIED_ADAPTER_WRITES=true` after validating the commands
  against their firmware. Validation fails with an explicit message if it is not set.
* The Huawei parsers are tested only against **synthetic** fixtures (`tests/test_adapters.py`),
  written to the commonly published output layout. Passing those tests does not show that the
  parsers match real device output.
* Huawei CPU/memory/temperature are reported as `unsupported_metrics`, not invented.

## Why these vendors, in this order

No hardware inventory, device models or credentials were available in the project context,
and the EazPay code base could not be inspected. The prioritisation is therefore based on the
reference platforms:
- Splynx publishes a Huawei OLT add-on (listed on splynx.com/integrations; the documentation
  page is at wiki.splynx.com/addons_modules/huawei-olt, which was blocked here).
- Sonar's integrations page lists Calix and Adtran Mosaic.

| Priority | Target | Path to implementation |
|---|---|---|
| 1 | Huawei MA5600T/MA5800 (CLI) | Code exists (experimental). Needs: official command reference for the target firmware, a lab OLT, captured real outputs to replace the synthetic fixtures, then promotion to `hardware_verified` per capability |
| 2 | Generic SNMP | Code exists. Needs a test against a real agent; vendor-private MIBs (e.g. Huawei GPON ONT optical OIDs) need vendor documentation |
| 3 | ZTE C300/C600, V-SOL | Implement `VendorAdapter` subclasses over SSH CLI, following the Huawei structure (pure `cli.py` builders/parsers + transport) |
| 4 | Calix SMx, Adtran Mosaic CP | Management-platform adapters (REST/northbound API) rather than per-OLT CLI. Require vendor API access/credentials |
| 5 | FiberHome (UNM2000 TL1), Nokia (NETCONF/TL1) | Protocol-specific transports |

### Adding a vendor
1. Create `app/adapters/<vendor>/adapter.py` subclassing `VendorAdapter`.
2. Declare `CAPABILITIES` honestly. Implement only those methods.
3. Keep command builders and parsers pure and unit-tested with **real captured** output.
4. Register it in `adapters/registry.py`. Set `experimental=True` until it has been validated on hardware.
5. Add a `TemplateVendorMapping` for each service template it should provision.
