"""Seed reference data and (optionally) a clearly labelled simulated demo network.

    python -m app.seed                 # reference data + bootstrap admin only
    python -m app.seed --demo          # + simulated network (sites, OLTs, FATs, ONTs, subscribers, jobs, alarms)
    python -m app.seed --demo --reset  # wipe all data first (development only)

Every simulated record has ``is_simulated = true`` and simulated names start with "SIM".
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import timedelta

from sqlalchemy import select

from app.config import get_settings
from app.core.audit import Actor
from app.core.enums import EndpointType
from app.db import Base, SessionLocal, get_engine, utcnow
from app.models import (
    BandwidthProfile,
    Cable,
    DistributionPoint,
    Fat,
    FiberCore,
    FiberLink,
    InfraPort,
    Olt,
    OltPort,
    Ont,
    OntDiscovery,
    OpticalReading,
    OpticalThreshold,
    Role,
    RolePermission,
    Service,
    ServiceTemplate,
    Site,
    Splitter,
    SplitterType,
    Subscriber,
    TemplateVendorMapping,
    User,
    Vlan,
)
from app.security.crypto import hash_password
from app.security.permissions import BUILTIN_ROLES

DEMO_USERS = [
    ("netadmin", "Network Administrator", "NetAdmin!2026"),
    ("technician", "Network Technician", "Technician!2026"),
    ("noc", "Monitoring Operator", "Monitoring!2026"),
    ("auditor", "Read-only Auditor", "Auditor!2026"),
]

SPLITTER_TYPES = [("1:2", 2, 3.7), ("1:4", 4, 7.3), ("1:8", 8, 10.5), ("1:16", 16, 13.7), ("1:32", 32, 17.1), ("1:64", 64, 20.5)]
SPEEDS = [10, 20, 30, 50, 100, 200]


# ------------------------------------------------------------------------------------------- reference
def seed_reference(db) -> None:
    for name, (desc, perms) in BUILTIN_ROLES.items():
        role = db.scalar(select(Role).where(Role.name == name))
        if role is None:
            role = Role(name=name, description=desc, builtin=True)
            db.add(role)
        role.permissions = [RolePermission(permission=p) for p in sorted(perms)]
    db.flush()
    s = get_settings()
    if not db.scalar(select(User).where(User.username == s.bootstrap_admin_username)):
        sa = db.scalar(select(Role).where(Role.name == "Super Administrator"))
        db.add(User(username=s.bootstrap_admin_username, full_name="Bootstrap Administrator",
                    password_hash=hash_password(s.bootstrap_admin_password), role_id=sa.id, password_changed_at=utcnow()))
    for name, outs, loss in SPLITTER_TYPES:
        if not db.scalar(select(SplitterType).where(SplitterType.name == name)):
            db.add(SplitterType(name=name, input_ports=1, output_ports=outs, nominal_loss_db=loss))
    if not db.scalar(select(OpticalThreshold)):
        db.add(OpticalThreshold(
            name="Example default (GPON Class B+ style) — review against your optics", rx_warning_low=-25.0,
            rx_critical_low=-27.0, rx_warning_high=-10.0, rx_critical_high=-8.0, olt_rx_warning_low=-28.0,
            olt_rx_critical_low=-30.0,
            notes="Starting values only. Replace with the receiver sensitivity/overload figures from the datasheets of "
                  "your ONT and OLT optics; add vendor/model-specific thresholds where they differ."))
    for mbps in SPEEDS:
        if not db.scalar(select(BandwidthProfile).where(BandwidthProfile.name == f"{mbps} Mbps")):
            db.add(BandwidthProfile(name=f"{mbps} Mbps", download_kbps=mbps * 1024, upload_kbps=max(5, mbps // 2) * 1024,
                                    description="Sample profile"))
    db.commit()


def seed_demo_users(db) -> None:
    for username, role_name, pw in DEMO_USERS:
        if not db.scalar(select(User).where(User.username == username)):
            role = db.scalar(select(Role).where(Role.name == role_name))
            db.add(User(username=username, full_name=f"Demo {role_name}", password_hash=hash_password(pw), role_id=role.id))
    db.commit()


# ------------------------------------------------------------------------------------------- demo network
def _hex_serial(rng: random.Random) -> str:
    return "SIMU" + "".join(rng.choice("0123456789ABCDEF") for _ in range(8))


def _port(db, owner, oid, direction, number) -> InfraPort:
    return db.scalar(select(InfraPort).where(InfraPort.owner_type == owner, InfraPort.owner_id == oid,
                                             InfraPort.direction == direction, InfraPort.number == number))


def _link(db, a_type, a_id, b_type, b_id, link_type, cable=None, core_no=None, verification="verified", length=None):
    core = None
    if cable is not None and core_no is not None:
        core = db.scalar(select(FiberCore).where(FiberCore.cable_id == cable.id, FiberCore.number == core_no))
        core.status = "used"
    db.add(FiberLink(a_type=a_type, a_id=a_id, b_type=b_type, b_id=b_id, link_type=link_type,
                     cable_id=cable.id if cable else None, core_id=core.id if core else None, verification=verification,
                     status="planned" if verification == "planned" else "active", length_m=length,
                     verified_at=utcnow() if verification == "verified" else None,
                     verified_by="seed" if verification == "verified" else None, is_simulated=True))


def _cable(db, name, ctype, fibers, site, frm, to, route=None, length=None) -> Cable:
    from app.api.infrastructure import FIBER_COLORS

    c = Cable(name=name, cable_type=ctype, fiber_count=fibers, site_id=site.id, from_description=frm, to_description=to,
              route=route, length_m=length, is_simulated=True)
    db.add(c)
    db.flush()
    for n in range(1, fibers + 1):
        db.add(FiberCore(cable_id=c.id, number=n, color=FIBER_COLORS[(n - 1) % 12], tube=(n - 1) // 12 + 1))
    db.flush()
    return c


def _splitter(db, name, ratio, site, lat, lng, fat=None, dpoint=None) -> Splitter:
    from app.core import topology

    t = db.scalar(select(SplitterType).where(SplitterType.name == ratio))
    s = Splitter(name=name, splitter_type_id=t.id, site_id=site.id, fat_id=fat.id if fat else None,
                 dpoint_id=dpoint.id if dpoint else None, latitude=lat, longitude=lng, estimated_loss_db=t.nominal_loss_db,
                 location=f"Housed in {fat.name if fat else dpoint.name if dpoint else 'cabinet'}", is_simulated=True)
    db.add(s)
    db.flush()
    topology.ensure_ports(db, "splitter", s.id, "in", 1)
    topology.ensure_ports(db, "splitter", s.id, "out", t.output_ports)
    db.flush()
    return s


def _fat(db, name, code, site, cap, lat, lng, address) -> Fat:
    from app.core import topology

    f = Fat(name=name, code=code, site_id=site.id, capacity=cap, latitude=lat, longitude=lng, address=address,
            fat_type="pole", manufacturer="Generic", installation_date=(utcnow() - timedelta(days=400)).date(),
            description="Simulated FAT", is_simulated=True)
    db.add(f)
    db.flush()
    topology.ensure_ports(db, "fat", f.id, "through", cap)
    db.flush()
    return f


def seed_demo(db) -> None:
    from app.adapters.simulator.device import create_device, make_ont, new_device_state
    from app.core import topology
    from app.jobs.queue import enqueue, run_pending
    from app.jobs.worker import load_handlers

    load_handlers()
    if db.scalar(select(Olt).where(Olt.is_simulated.is_(True))):
        print("Demo network already present; skipping (use --reset to rebuild).")
        return
    rng = random.Random(2026)
    seed_demo_users(db)

    # ---- sites (Nairobi-area coordinates, illustrative only)
    central = Site(name="SIM Central POP", code="SIM-CPOP", site_type="pop", address="Simulated POP, Westlands",
                   latitude=-1.2676, longitude=36.8108, description="[SIMULATED] Demo point of presence", is_simulated=True)
    east = Site(name="SIM East Cabinet", code="SIM-ECAB", site_type="cabinet", address="Simulated cabinet, Eastleigh",
                latitude=-1.2741, longitude=36.8516, description="[SIMULATED] Demo street cabinet", is_simulated=True)
    db.add_all([central, east])
    db.flush()

    # ---- VLANs
    db.add_all([Vlan(vlan_id=100, name="Internet", purpose="internet"), Vlan(vlan_id=200, name="VoIP", purpose="voip"),
                Vlan(vlan_id=999, name="ONT management", purpose="management")])

    # ---- simulated equipment
    olt_specs = [
        ("SIM-OLT-CENTRAL", "sim-olt-central.simulated", central, (1, 2), -1.2676, 36.8108),
        ("SIM-OLT-EAST", "sim-olt-east.simulated", east, (1,), -1.2741, 36.8516),
    ]
    devices = {}
    olts = {}
    for name, host, site, slots, lat, lng in olt_specs:
        st = new_device_state(serial=f"SIMOLT{rng.randint(100000, 999999)}", pon_slots=slots)
        devices[name] = st
        olts[name] = Olt(name=name, site_id=site.id, adapter="simulator", manufacturer="SIMULATED", model="SIM-GPON-OLT",
                         host=host, management_protocol="simulator", rack_location="Rack A / U12" if site is central else "Cabinet shelf 1",
                         latitude=lat, longitude=lng, poll_interval_seconds=300, is_simulated=True,
                         notes="[SIMULATED] Demo OLT backed by the built-in simulator")

    # ONTs on devices: (olt, fsp, count, states/rx overrides)
    plan = {
        "SIM-OLT-CENTRAL": {"0/1/0": 22, "0/1/1": 6, "0/2/0": 4},
        "SIM-OLT-EAST": {"0/1/0": 10},
    }
    serial_map: dict[str, list[str]] = {}
    for oname, ports in plan.items():
        st = devices[oname]
        for fsp, n in ports.items():
            st["onts"].setdefault(fsp, {})
            for i in range(n):
                serial = _hex_serial(rng)
                rx = round(rng.uniform(-23.5, -17.0), 2)
                st["onts"][fsp][str(i)] = make_ont(serial, rx=rx, desc=f"SIM subscriber {oname[-4:]}-{fsp}-{i}")
                serial_map.setdefault(f"{oname}|{fsp}", []).append(serial)
    # special conditions
    c00 = serial_map["SIM-OLT-CENTRAL|0/1/0"]
    devices["SIM-OLT-CENTRAL"]["onts"]["0/1/0"]["3"]["rx_base"] = -26.2     # warning
    devices["SIM-OLT-CENTRAL"]["onts"]["0/1/0"]["4"]["rx_base"] = -27.6     # critical
    devices["SIM-OLT-CENTRAL"]["onts"]["0/1/0"]["5"].update(make_ont(c00[5], state="dying_gasp"))
    devices["SIM-OLT-CENTRAL"]["onts"]["0/1/0"]["6"].update(make_ont(c00[6], state="offline"))
    # FAT-002 (ONTs 8..13) suffers a distribution fibre cut -> LOS cluster
    for i in range(8, 14):
        o = devices["SIM-OLT-CENTRAL"]["onts"]["0/1/0"][str(i)]
        o.update(make_ont(o["serial"], state="los", rx=o["rx_base"], desc=o["desc"]))
    # unconfigured ONTs waiting for registration
    for fsp, rx in (("0/1/0", -19.8), ("0/1/1", -21.4), ("0/2/0", -24.9)):
        devices["SIM-OLT-CENTRAL"]["autofind"].append({"fsp": fsp, "serial": _hex_serial(rng), "vendor_id": "SIMU",
                                                       "equipment_id": "SIM-ONT-HG8", "hw": "V1", "sw": "V1R002C00",
                                                       "loid": None, "rx": rx, "ts": utcnow().isoformat()})
    devices["SIM-OLT-EAST"]["autofind"].append({"fsp": "0/1/1", "serial": _hex_serial(rng), "vendor_id": "SIMU",
                                                "equipment_id": "SIM-ONT-HG4", "hw": "V2", "sw": "V2R001", "loid": "LOID-7781",
                                                "rx": -20.2, "ts": utcnow().isoformat()})
    # existing service-ports on equipment for active services (all ONTs except a few)
    for oname, st in devices.items():
        idx = 1
        for fsp, onts in st["onts"].items():
            for oi, o in onts.items():
                plan_speed = SPEEDS[(idx - 1) % len(SPEEDS)]
                st["service_ports"][str(idx)] = {"fsp": fsp, "ont": int(oi), "vlan": 100, "gem": 1, "user_vlan": 100,
                                                 "inner_vlan": None, "admin": "enabled",
                                                 "tt_up": str(10 + SPEEDS.index(plan_speed)), "tt_down": str(10 + SPEEDS.index(plan_speed))}
                o["_sp"] = idx
                o["_speed"] = plan_speed
                idx += 1
        # keep the private helper keys out of the device state copy used by the adapter
    for oname, olt in olts.items():
        db.add(olt)
        db.flush()
        state = devices[oname]
        clean = {**state, "onts": {f: {i: {k: v for k, v in o.items() if not k.startswith("_")} for i, o in onts.items()}
                                   for f, onts in state["onts"].items()}}
        create_device(db, olt.host, clean)
    db.commit()

    # ---- let the NMS learn the equipment through the adapter (sync profiles + full poll)
    for olt in olts.values():
        enqueue(db, "olt.sync_profiles", {"olt_id": olt.id}, created_by="seed")
        enqueue(db, "olt.poll", {"olt_id": olt.id}, created_by="seed")
    db.commit()
    run_pending()
    db.expire_all()

    # ---- service templates (one per speed), mapped to the simulator and (example) Huawei
    templates = {}
    for i, mbps in enumerate(SPEEDS):
        bw = db.scalar(select(BandwidthProfile).where(BandwidthProfile.name == f"{mbps} Mbps"))
        t = ServiceTemplate(name=f"FTTH Internet {mbps} Mbps", bandwidth_profile_id=bw.id, vlan_mode="single", svlan=100,
                            user_vlan=100, gem_port=1, tcont=1, wan_mode="pppoe",
                            description=f"Residential internet {mbps} Mbps on VLAN 100")
        t.mappings = [
            TemplateVendorMapping(adapter="simulator", line_profile_ref="10", service_profile_ref="10", dba_profile_ref="20",
                                  traffic_table_up_ref=str(10 + i), traffic_table_down_ref=str(10 + i)),
            TemplateVendorMapping(adapter="huawei_smartax_cli", line_profile_ref="10", service_profile_ref="10",
                                  dba_profile_ref="10", traffic_table_up_ref=str(10 + i), traffic_table_down_ref=str(10 + i),
                                  parameters={"note": "EXAMPLE values — must match profiles that exist on your OLT"}),
        ]
        db.add(t)
        templates[mbps] = t
    db.flush()

    # ---- passive infrastructure for SIM-OLT-CENTRAL PON 0/1/0 (fully documented) and 0/1/1 (partially)
    olt_c, olt_e = olts["SIM-OLT-CENTRAL"], olts["SIM-OLT-EAST"]
    pon = {(p.olt_id, p.fsp): p for p in db.scalars(select(OltPort).where(OltPort.port_type == "pon"))}
    odf = DistributionPoint(name="SIM-ODF-CPOP-01", point_type="odf", site_id=central.id, olt_id=olt_c.id, port_count=24,
                            latitude=-1.26762, longitude=36.81085, address="POP rack A", is_simulated=True)
    closure = DistributionPoint(name="SIM-CLOSURE-WL-01", point_type="closure", site_id=central.id, port_count=12,
                                latitude=-1.2655, longitude=36.8075, address="Manhole, Waiyaki Way", is_simulated=True)
    db.add_all([odf, closure])
    db.flush()
    topology.ensure_ports(db, "dpoint", odf.id, "through", 24)
    topology.ensure_ports(db, "dpoint", closure.id, "through", 12)
    db.flush()
    feeder = _cable(db, "SIM-FDR-CPOP-01", "feeder", 24, central, "SIM-ODF-CPOP-01", "SIM-CLOSURE-WL-01",
                    route=[[-1.26762, 36.81085], [-1.2668, 36.8095], [-1.2655, 36.8075]], length=620)
    dist = _cable(db, "SIM-DST-WL-01", "distribution", 12, central, "SIM-CLOSURE-WL-01", "FAT-001..004",
                  route=[[-1.2655, 36.8075], [-1.2642, 36.8061], [-1.2630, 36.8049]], length=900)
    # patch OLT PON 0/1/0 -> ODF 1, ODF 1 -> feeder core 1 -> closure 1
    p010 = pon[(olt_c.id, "0/1/0")]
    _link(db, EndpointType.OLT_PORT, p010.id, EndpointType.INFRA_PORT, _port(db, "dpoint", odf.id, "through", 1).id, "patch", length=3)
    _link(db, EndpointType.INFRA_PORT, _port(db, "dpoint", odf.id, "through", 1).id, EndpointType.INFRA_PORT,
          _port(db, "dpoint", closure.id, "through", 1).id, "feeder", feeder, 1, length=620)
    l1 = _splitter(db, "SIM-SPL-L1-01", "1:4", central, -1.2655, 36.8075, dpoint=closure)
    _link(db, EndpointType.INFRA_PORT, _port(db, "dpoint", closure.id, "through", 1).id, EndpointType.INFRA_PORT,
          _port(db, "splitter", l1.id, "in", 1).id, "splice")
    fat_specs = [("SIM-FAT-001", "F001", 8, "1:8", -1.2648, 36.8069, range(0, 8)),
                 ("SIM-FAT-002", "F002", 8, "1:8", -1.2642, 36.8061, range(8, 14)),
                 ("SIM-FAT-003", "F003", 16, "1:16", -1.2636, 36.8055, range(14, 19)),
                 ("SIM-FAT-004", "F004", 4, "1:4", -1.2630, 36.8049, range(19, 22))]
    fats = {}
    for n, (fname, code, cap, ratio, lat, lng, ont_range) in enumerate(fat_specs, start=1):
        f = _fat(db, fname, code, central, cap, lat, lng, f"Pole {code}, simulated street")
        fats[fname] = f
        l2 = _splitter(db, f"SIM-SPL-{code}", ratio, central, lat, lng, fat=f)
        _link(db, EndpointType.INFRA_PORT, _port(db, "splitter", l1.id, "out", n).id, EndpointType.INFRA_PORT,
              _port(db, "splitter", l2.id, "in", 1).id, "distribution", dist, n, length=150 * n)
        for k in range(1, min(cap, l2.splitter_type.output_ports) + 1):
            _link(db, EndpointType.INFRA_PORT, _port(db, "splitter", l2.id, "out", k).id, EndpointType.INFRA_PORT,
                  _port(db, "fat", f.id, "through", k).id, "patch")
        onts = list(db.scalars(select(Ont).where(Ont.pon_port_id == p010.id).order_by(Ont.ont_index)))
        for k, oi in enumerate(ont_range, start=1):
            ont = next((o for o in onts if o.ont_index == oi), None)
            if ont:
                _link(db, EndpointType.INFRA_PORT, _port(db, "fat", f.id, "through", k).id, EndpointType.ONT, ont.id, "drop",
                      verification="verified" if k % 3 else "unverified", length=rng.randint(30, 180))
    # reserved & faulty ports for capacity demo
    fp = _port(db, "fat", fats["SIM-FAT-003"].id, "through", 9)
    fp.status, fp.reserved_for = "reserved", "Pending installation SIM-CUST-PLANNED"
    _port(db, "fat", fats["SIM-FAT-003"].id, "through", 12).status = "faulty"
    _port(db, "fat", fats["SIM-FAT-001"].id, "through", 8).notes = "Port 8 in use"
    # PON 0/1/1 partial documentation: splitter recorded as *planned* path
    p011 = pon[(olt_c.id, "0/1/1")]
    fat5 = _fat(db, "SIM-FAT-005", "F005", central, 8, -1.2690, 36.8130, "Simulated estate gate")
    s5 = _splitter(db, "SIM-SPL-F005", "1:8", central, -1.2690, 36.8130, fat=fat5)
    _link(db, EndpointType.OLT_PORT, p011.id, EndpointType.INFRA_PORT, _port(db, "splitter", s5.id, "in", 1).id, "feeder",
          verification="unverified", length=1400)
    for k in range(1, 9):
        _link(db, EndpointType.INFRA_PORT, _port(db, "splitter", s5.id, "out", k).id, EndpointType.INFRA_PORT,
              _port(db, "fat", fat5.id, "through", k).id, "patch", verification="unverified")
    for k, ont in enumerate(db.scalars(select(Ont).where(Ont.pon_port_id == p011.id).order_by(Ont.ont_index).limit(3)), start=1):
        _link(db, EndpointType.INFRA_PORT, _port(db, "fat", fat5.id, "through", k).id, EndpointType.ONT, ont.id, "drop",
              verification="unverified")
    # East: a FAT whose upstream is only *planned*
    pe = pon[(olt_e.id, "0/1/0")]
    fat101 = _fat(db, "SIM-FAT-101", "F101", east, 16, -1.2750, 36.8530, "Simulated market street")
    s101 = _splitter(db, "SIM-SPL-F101", "1:16", east, -1.2750, 36.8530, fat=fat101)
    _link(db, EndpointType.OLT_PORT, pe.id, EndpointType.INFRA_PORT, _port(db, "splitter", s101.id, "in", 1).id, "feeder",
          verification="planned", length=800)
    for k in range(1, 17):
        _link(db, EndpointType.INFRA_PORT, _port(db, "splitter", s101.id, "out", k).id, EndpointType.INFRA_PORT,
              _port(db, "fat", fat101.id, "through", k).id, "patch", verification="verified")
    for k, ont in enumerate(db.scalars(select(Ont).where(Ont.pon_port_id == pe.id).order_by(Ont.ont_index).limit(6)), start=1):
        _link(db, EndpointType.INFRA_PORT, _port(db, "fat", fat101.id, "through", k).id, EndpointType.ONT, ont.id, "drop")
    db.flush()

    # ---- subscribers & active services matching what is configured on the simulated equipment
    first = ["Amina", "Brian", "Cynthia", "David", "Esther", "Felix", "Grace", "Hassan", "Irene", "James", "Kamau", "Lilian",
             "Moses", "Njeri", "Otieno", "Purity", "Quentin", "Rose", "Samuel", "Teresa", "Umar", "Violet", "Wanjiru", "Yusuf"]
    n = 0
    for oname, st in devices.items():
        olt = olts[oname]
        for fsp, onts in st["onts"].items():
            for oi, o in onts.items():
                ont = db.scalar(select(Ont).where(Ont.serial_number == o["serial"]))
                if ont is None:
                    continue
                n += 1
                fat = topology.fat_for_ont(db, ont.id)
                fat_obj = db.get(Fat, fat["fat_id"]) if fat else None
                lat = (fat_obj.latitude + rng.uniform(-0.0006, 0.0006)) if fat_obj else None
                lng = (fat_obj.longitude + rng.uniform(-0.0006, 0.0006)) if fat_obj else None
                sub = Subscriber(customer_ref=f"SIM-CUST-{n:04d}", display_name=f"{first[n % len(first)]} (simulated)",
                                 account_ref=f"SIM-ACC-{n:04d}", external_customer_id=f"EXT-C-{n:04d}",
                                 external_account_id=f"EXT-A-{n:04d}", installation_address=f"Simulated address {n}",
                                 latitude=lat, longitude=lng, is_simulated=True)
                db.add(sub)
                db.flush()
                speed = o["_speed"]
                svc = Service(subscriber_id=sub.id, service_ref=f"SIM-SVC-{n:04d}", external_service_id=f"EXT-S-{n:04d}",
                              external_package_id=f"PKG-{speed}M", ont_id=ont.id, template_id=templates[speed].id, status="active",
                              activation_date=(utcnow() - timedelta(days=rng.randint(30, 700))).date(), is_simulated=True,
                              network_config={"service_ports": [{"index": o["_sp"], "vlan": 100, "gem_port": 1, "user_vlan": 100,
                                                                 "inner_vlan": None, "traffic_up_ref": str(10 + SPEEDS.index(speed)),
                                                                 "traffic_down_ref": str(10 + SPEEDS.index(speed))}],
                                              "olt_id": olt.id, "applied_by_job": "seed"})
                db.add(svc)
    # a pending service waiting for the first discovered ONT
    sub = Subscriber(customer_ref="SIM-CUST-NEW-01", display_name="New customer (simulated)", account_ref="SIM-ACC-NEW-01",
                     external_customer_id="EXT-C-NEW-01", installation_address="Simulated new-build", is_simulated=True,
                     latitude=-1.2649, longitude=36.8071)
    db.add(sub)
    db.flush()
    db.add(Service(subscriber_id=sub.id, service_ref="SIM-SVC-NEW-01", external_service_id="EXT-S-NEW-01",
                   external_package_id="PKG-50M", template_id=templates[50].id, status="pending", is_simulated=True))
    db.commit()

    # ---- simulated optical history (backfilled, labelled source=simulator-backfill)
    now = utcnow()
    declining = c00[2]
    for ont in db.scalars(select(Ont).where(Ont.is_simulated.is_(True), Ont.rx_power_dbm.is_not(None))):
        for h in range(7 * 24, 0, -6):
            base = ont.rx_power_dbm
            if ont.serial_number == declining:
                base = ont.rx_power_dbm + 3.0 * h / (7 * 24)  # was ~3 dB stronger a week ago
            db.add(OpticalReading(ont_id=ont.id, ts=now - timedelta(hours=h), rx_power_dbm=round(base + rng.uniform(-0.25, 0.25), 2),
                                  tx_power_dbm=round(2.3 + rng.uniform(-0.1, 0.1), 2),
                                  olt_rx_power_dbm=round(base - 1.8 + rng.uniform(-0.25, 0.25), 2),
                                  temperature_c=round(46 + rng.uniform(-3, 3), 1), voltage_v=round(3.3 + rng.uniform(-0.03, 0.03), 3),
                                  bias_current_ma=round(11.5 + rng.uniform(-1, 1), 2), source="simulator-backfill"))
    db.commit()

    # ---- sample provisioning: register + activate the new customer on a discovered ONT (real engine path)
    from app.engines import provisioning as prov

    admin = db.scalar(select(User).where(User.username == get_settings().bootstrap_admin_username))
    actor = Actor(admin.id, admin.username, None, set(admin.role.permission_names))
    disc = db.scalar(select(OntDiscovery).where(OntDiscovery.olt_id == olt_c.id, OntDiscovery.state == "unregistered")
                     .order_by(OntDiscovery.id))
    new_svc = db.scalar(select(Service).where(Service.service_ref == "SIM-SVC-NEW-01"))
    fat1_port = topology.Endpoint  # noqa: F841 - readability
    free_port = next(p for p in db.scalars(select(InfraPort).where(InfraPort.owner_type == "fat", InfraPort.owner_id == fats["SIM-FAT-003"].id)
                                         .order_by(InfraPort.number)) if topology.port_effective_status(db, p) == "available")
    job = prov.create_request(db, actor, {"operation": "activate_service", "discovery_id": disc.id, "service_id": new_svc.id,
                                          "template_id": templates[50].id, "fat_port_id": free_port.id,
                                          "description": "SIM new customer"})
    db.commit()
    run_pending()
    db.refresh(job)
    if job.state == "awaiting_approval":
        prov.approve(db, job, actor)
        db.commit()
        run_pending()
    # a failed + rolled-back job (fault injection) for the history view
    from app.adapters.simulator.device import apply_event
    from app.models import SimulatedDevice

    dev = db.scalar(select(SimulatedDevice).where(SimulatedDevice.host == olt_c.host))
    import copy

    st = copy.deepcopy(dev.state)
    apply_event(st, "fail_next", {"operation": "create_service_port", "count": 1})
    dev.state = st
    db.commit()
    disc2 = db.scalar(select(OntDiscovery).where(OntDiscovery.olt_id == olt_c.id, OntDiscovery.state == "unregistered")
                      .order_by(OntDiscovery.id))
    if disc2:
        sub2 = Subscriber(customer_ref="SIM-CUST-NEW-02", display_name="Second new customer (simulated)", is_simulated=True)
        db.add(sub2)
        db.flush()
        svc2 = Service(subscriber_id=sub2.id, service_ref="SIM-SVC-NEW-02", external_service_id="EXT-S-NEW-02",
                       template_id=templates[20].id, status="pending", is_simulated=True)
        db.add(svc2)
        db.flush()
        job2 = prov.create_request(db, actor, {"operation": "activate_service", "discovery_id": disc2.id, "service_id": svc2.id,
                                               "template_id": templates[20].id, "auto_approve": True})
        db.commit()
        run_pending()
    # final poll so dashboards reflect everything
    for olt in olts.values():
        enqueue(db, "olt.poll", {"olt_id": olt.id}, created_by="seed")
    db.commit()
    run_pending()
    print(f"Demo network seeded: {db.query(Ont).count()} ONTs, {db.query(Fat).count()} FATs, "
          f"{db.query(Subscriber).count()} subscribers (all SIMULATED).")


def reset(db) -> None:
    if get_settings().environment == "production":
        raise SystemExit("Refusing to reset a production database")
    db.close()
    eng = get_engine()
    Base.metadata.drop_all(eng)
    with eng.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    migrate()


def migrate() -> None:
    """Apply Alembic migrations (same as `alembic upgrade head`)."""
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    command.upgrade(cfg, "head")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", action="store_true", help="also create the simulated demo network")
    ap.add_argument("--reset", action="store_true", help="drop and recreate all tables first (development only)")
    args = ap.parse_args(argv)
    from app.logging_setup import setup_logging

    setup_logging("WARNING")
    if not args.reset:
        migrate()
    db = SessionLocal()
    if args.reset:
        reset(db)
        db = SessionLocal()
    seed_reference(db)
    s = get_settings()
    print(f"Reference data ready. Bootstrap admin: {s.bootstrap_admin_username} / {s.bootstrap_admin_password} "
          "(change it after first login).")
    if args.demo:
        if not s.simulator_enabled:
            sys.exit("Simulator disabled (FNMS_SIMULATOR_ENABLED=false); demo network not created")
        seed_demo(db)
        print("Demo users: " + ", ".join(f"{u}/{p} ({r})" for u, r, p in DEMO_USERS))
    db.close()


if __name__ == "__main__":
    main()
