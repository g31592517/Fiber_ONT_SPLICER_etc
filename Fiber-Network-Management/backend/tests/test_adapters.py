"""Adapter contract tests and Huawei CLI parser tests.

The Huawei fixtures are SYNTHETIC: written to match commonly published output layouts, not
captured from hardware. Passing these tests proves the parsers handle that layout, nothing more.
"""
import pytest

from app.adapters.base import Capability, ConnectionConfig, Fsp, UnsupportedOperation, VendorAdapter
from app.adapters.huawei import cli
from app.adapters.registry import PLANNED, all_adapters
from app.adapters.snmp.adapter import SnmpStandardAdapter

AUTOFIND = """
   ----------------------------------------------------------------------------
   Number              : 1
   F/S/P               : 0/1/0
   Ont SN              : 485754430A1B2C3D (HWTC-0A1B2C3D)
   Password            : 0x00000000000000000000
   Loid                :
   Checkcode           :
   VendorID            : HWTC
   Ont Version         : 10C7.A
   Ont SoftwareVersion : V5R019C00S100
   Ont EquipmentID     : EG8145V5
   Ont autofind time   : 2024-01-15 10:00:00+03:00
   ----------------------------------------------------------------------------
   Number              : 2
   F/S/P               : 0/1/3
   Ont SN              : 48575443DEADBEEF (HWTC-DEADBEEF)
   Loid                : LOID123
   VendorID            : HWTC
   Ont EquipmentID     : HG8310M
   ----------------------------------------------------------------------------
"""

ONT_INFO_ALL = """
  -----------------------------------------------------------------------------
  F/S/P   ONT         SN         Control     Run      Config   Match    Protect
          ID                     flag        state    state    state    side
  -----------------------------------------------------------------------------
  0/ 1/0    0  485754430A1B2C3D  active      online   normal   match    no
  0/ 1/0    1  48575443DEADBEEF  active      offline  initial  initial  no
  0/ 1/0    2  4857544311112222  deactivated offline  normal   match    no
  -----------------------------------------------------------------------------
"""

ONT_DETAIL = """
  -----------------------------------------------------------------------------
  F/S/P                   : 0/1/0
  ONT-ID                  : 1
  Control flag            : active
  Run state               : offline
  Config state            : initial
  SN                      : 48575443DEADBEEF (HWTC-DEADBEEF)
  Description             : cust-42
  Last down cause         : LOS
  Last up time            : 2024-01-15 10:00:00+03:00
  Last down time          : 2024-01-16 11:00:00+03:00
  ONT distance(m)         : -
  Line profile ID         : 10
  Service profile ID      : 10
  -----------------------------------------------------------------------------
"""

OPTICAL = """
  Rx optical power(dBm)                  : -19.52
  Tx optical power(dBm)                  : 2.11
  Laser bias current(mA)                 : 12
  Temperature(C)                         : 45
  Voltage(V)                             : 3.280
  OLT Rx ONT optical power(dBm)          : -21.30
"""

SERVICE_PORTS = """
  -----------------------------------------------------------------------------
   INDEX VLAN VLAN     PORT F/ S/ P VPI  VCI   FLOW  FLOW       RX   TX   STATE
         ID   ATTR     TYPE                    TYPE  PARA
  -----------------------------------------------------------------------------
     101  100 common   gpon 0/1 /0  1    1     vlan  100        11   11   up
     102  200 common   gpon 0/1 /0  1    2     vlan  200        -    -    down
  -----------------------------------------------------------------------------
"""


def test_every_adapter_declares_every_capability():
    for a in all_adapters():
        caps = a.capabilities()
        assert set(caps) == {c.value for c in Capability}
        for k, v in caps.items():
            if v.supported:
                assert v.verification is not None, f"{a.key}.{k} supported without a verification level"


def test_planned_adapters_support_nothing_and_raise_structured_unsupported():
    for p in PLANNED:
        assert p.planned and not any(c.supported for c in p.capabilities().values())
        inst = p(ConnectionConfig(host="x"))
        with pytest.raises(UnsupportedOperation) as e:
            inst.discover_onts()
        assert e.value.as_result()["status"] == "unsupported"


def test_snmp_adapter_does_not_claim_gpon_capabilities():
    assert not SnmpStandardAdapter.supports(Capability.ONT_DISCOVERY)
    assert not SnmpStandardAdapter.supports(Capability.ONT_REGISTRATION)
    with pytest.raises(UnsupportedOperation):
        SnmpStandardAdapter(ConnectionConfig(host="x")).register_ont(None)


def test_snmp_adapter_requires_configuration():
    from app.adapters.base import AdapterConnectionError

    with pytest.raises(AdapterConnectionError):
        SnmpStandardAdapter(ConnectionConfig(host="192.0.2.1"))._auth()


def test_base_adapter_defaults_unsupported():
    with pytest.raises(UnsupportedOperation):
        VendorAdapter(ConnectionConfig(host="x")).get_health()


def test_huawei_parse_autofind():
    found = cli.parse_autofind(AUTOFIND)
    assert len(found) == 2
    assert found[0].serial_number == "485754430A1B2C3D" and str(found[0].fsp) == "0/1/0"
    assert found[0].equipment_id == "EG8145V5" and found[0].software_version == "V5R019C00S100"
    assert "Password" not in found[0].raw
    assert found[1].loid == "LOID123" and str(found[1].fsp) == "0/1/3"


def test_huawei_parse_ont_info_and_state_normalization():
    rows = cli.parse_ont_info_all(ONT_INFO_ALL)
    assert [(r.ont_index, r.state) for r in rows] == [(0, "online"), (1, "registered_inactive"), (2, "disabled")]
    assert "run=online" in rows[0].vendor_state
    d = cli.parse_ont_detail(ONT_DETAIL, Fsp(0, 1, 0), 1)
    assert d.state == "los" and d.last_down_cause == "LOS" and d.line_profile_ref == "10" and d.distance_m is None
    assert cli.normalize_ont_state("offline", "active", "dying-gasp", "normal") == "dying_gasp"
    assert cli.normalize_ont_state("online", "active", None, "failed") == "faulty"


def test_huawei_parse_optical_and_service_ports():
    o = cli.parse_optical(OPTICAL)
    assert o.rx_power_dbm == -19.52 and o.olt_rx_power_dbm == -21.30 and o.voltage_v == 3.28
    sps = cli.parse_service_ports(SERVICE_PORTS)
    assert [(s.index, s.vlan, s.gem_port, s.admin_status) for s in sps] == [(101, 100, 1, "enabled"), (102, 200, 2, "disabled")]


def test_huawei_command_builders_and_serial_normalisation():
    assert cli.normalize_serial("HWTC0A1B2C3D") == "485754430A1B2C3D"
    with pytest.raises(ValueError):
        cli.normalize_serial("bad;serial")
    cmd = cli.cmd_ont_add(Fsp(0, 1, 0), None, "485754430A1B2C3D", "10", "20", 'evil" ; reboot')
    assert cmd.startswith("ont add 0 sn-auth 485754430A1B2C3D omci ont-lineprofile-id 10 ont-srvprofile-id 20")
    assert cmd.count('"') == 2  # description quotes cannot be broken out of
    sp = cli.cmd_service_port(None, 100, Fsp(0, 1, 0), 3, 1, 100, "11", "11")
    assert sp == ("service-port vlan 100 gpon 0/1/0 ont 3 gemport 1 multi-service user-vlan 100 tag-transform translate "
                  "inbound traffic-table index 11 outbound traffic-table index 11")


def test_simulator_adapter_full_contract(client, admin):
    from app.adapters.simulator.adapter import SimulatorAdapter

    assert all(c.supported and c.verification == "simulated" for c in SimulatorAdapter.capabilities().values())
