"""Acceptance criteria B (ONT management) and C (monitoring)."""
from tests.conftest import olt_by_name, sim_event

API = "/api/v1"


def test_discovery_list_from_simulator(client, admin, work):
    olt = olt_by_name(client, admin, "SIM-OLT-EAST")
    sim_event(client, admin, olt["id"], "new_ont", {"fsp": "0/1/2", "serial": "SIMUD15C0001", "rx_dbm": -21.0})
    r = client.post(f"{API}/olts/{olt['id']}/actions/discover", headers=admin)
    assert r.status_code == 202
    work()
    disc = client.get(f"{API}/discovered-onts?olt_id={olt['id']}", headers=admin).json()
    d = next(x for x in disc if x["serial_number"] == "SIMUD15C0001")
    assert d["pon_port"] == "0/1/2" and d["rx_power_dbm"] == -21.0 and d["state"] == "unregistered"
    assert d["inventory_match"] is None
    detail = client.get(f"{API}/discovered-onts/{d['id']}", headers=admin).json()
    assert detail["registration_supported"] is True and len(detail["profiles"]) > 0
    # discovery is not registration: no ONT inventory record exists yet
    assert client.get(f"{API}/onts?q=SIMUD15C0001&registration=all", headers=admin).json()["total"] == 0


def test_register_discovered_ont_with_profiles_and_fat_port(client, admin, work):
    olt = olt_by_name(client, admin, "SIM-OLT-EAST")
    d = next(x for x in client.get(f"{API}/discovered-onts?olt_id={olt['id']}", headers=admin).json()
             if x["serial_number"] == "SIMUD15C0001")
    fat = next(f for f in client.get(f"{API}/fats", headers=admin).json() if f["name"] == "SIM-FAT-101")
    free = next(p for p in client.get(f"{API}/fats/{fat['id']}", headers=admin).json()["ports"] if p["effective_status"] == "available")
    r = client.post(f"{API}/provisioning/jobs", headers=admin, json={
        "operation": "register_ont", "discovery_id": d["id"], "line_profile_ref": "10", "service_profile_ref": "20",
        "fat_port_id": free["id"], "description": "test registration"})
    assert r.status_code == 201, r.text
    job = r.json()
    assert job["state"] == "validation_pending"
    work()
    job = client.get(f"{API}/provisioning/jobs/{job['id']}", headers=admin).json()
    assert job["state"] == "awaiting_approval", job["validation"]
    assert [s["action"] for s in job["steps"]] == ["register_ont", "verify_ont"]
    assert job["validation"]["dry_run"][0]["status"] == "ok"
    r = client.post(f"{API}/provisioning/jobs/{job['id']}/approve", headers=admin, json={})
    assert r.status_code == 200 and r.json()["state"] == "queued"
    work()
    job = client.get(f"{API}/provisioning/jobs/{job['id']}", headers=admin).json()
    assert job["state"] == "succeeded", job
    ont = client.get(f"{API}/onts/{job['ont_id']}", headers=admin).json()
    assert ont["registration_status"] == "registered" and ont["pon_port"] == "0/1/2" and ont["ont_index"] is not None
    assert ont["line_profile"] and ont["service_profile"]
    assert ont["fat"]["fat_name"] == "SIM-FAT-101"
    assert ont["status"] == "online"
    # registration is not service activation
    assert ont["subscriber"] is None
    # refresh status + optical
    r = client.post(f"{API}/onts/{ont['id']}/refresh", headers=admin)
    work()
    res = client.get(f"{API}/jobs/{r.json()['job_id']}", headers=admin).json()["result"]
    assert res["status"] == "online" and res["optical"]["rx_power_dbm"] is not None
    hist = client.get(f"{API}/onts/{ont['id']}/history", headers=admin).json()
    assert len(hist["optical"]) >= 1 and hist["optical"][-1]["source"] == "simulator"
    assert any(e["to_status"] == "online" for e in hist["status_events"])


def test_status_change_detected_and_alarm_lifecycle(client, admin, noc, work):
    olt = olt_by_name(client, admin, "SIM-OLT-CENTRAL")
    ont = client.get(f"{API}/onts?olt_id={olt['id']}&status=online&limit=50", headers=admin).json()["items"][-1]
    sim_event(client, admin, olt["id"], "ont_state", {"serial": ont["serial_number"], "state": "los"})
    o = client.get(f"{API}/onts/{ont['id']}", headers=admin).json()
    assert o["status"] == "los" and "LOS" in o["vendor_status"]
    alarms = client.get(f"{API}/alarms?source_type=ont&source_id={ont['id']}", headers=noc).json()["items"]
    los = next(a for a in alarms if a["category"] == "ont_los")
    assert los["origin"] == "device" and los["vendor_alarm_id"].startswith("SIM-") and los["state"] == "new"
    # monitoring operator may acknowledge
    r = client.post(f"{API}/alarms/{los['id']}/actions", headers=noc, json={"action": "acknowledge", "note": "looking"})
    assert r.status_code == 200 and r.json()["state"] == "acknowledged" and r.json()["acknowledged_by"] == "noc"
    r = client.post(f"{API}/alarms/{los['id']}/actions", headers=noc, json={"action": "investigate"})
    assert r.json()["state"] == "investigating"
    # condition clears on the device -> resolved by system, history preserved
    sim_event(client, admin, olt["id"], "ont_state", {"serial": ont["serial_number"], "state": "online"})
    a = client.get(f"{API}/alarms/{los['id']}", headers=noc).json()
    assert a["state"] == "resolved" and a["resolved_by"] == "system" and a["cleared_on_device_at"]
    assert [e["action"] for e in a["events"]][:4] == ["raised", "acknowledged", "investigating", "cleared"]
    # re-occurrence within the window reopens the same alarm
    sim_event(client, admin, olt["id"], "ont_state", {"serial": ont["serial_number"], "state": "los"})
    a = client.get(f"{API}/alarms/{los['id']}", headers=noc).json()
    assert a["state"] == "reopened" and a["occurrence_count"] >= 2
    sim_event(client, admin, olt["id"], "ont_state", {"serial": ont["serial_number"], "state": "online"})
    events = client.get(f"{API}/onts/{ont['id']}/history", headers=noc).json()["status_events"]
    assert {e["to_status"] for e in events} >= {"los", "online"}


def test_manual_alarm_resolution(client, admin, noc):
    alarms = client.get(f"{API}/alarms?category=low_optical_signal", headers=noc).json()["items"]
    a = alarms[0]
    r = client.post(f"{API}/alarms/{a['id']}/actions", headers=noc, json={"action": "resolve", "note": "Connector cleaned"})
    assert r.json()["state"] == "resolved" and r.json()["resolved_by"] == "noc"
    assert client.post(f"{API}/alarms/{a['id']}/actions", headers=noc, json={"action": "acknowledge"}).status_code == 422


def test_olt_communication_failure_is_not_ont_failure(client, admin, work):
    olt = olt_by_name(client, admin, "SIM-OLT-EAST")
    before = client.get(f"{API}/onts?olt_id={olt['id']}&status=online", headers=admin).json()["total"]
    sim_event(client, admin, olt["id"], "olt_unreachable", {"unreachable": True})
    o = client.get(f"{API}/olts/{olt['id']}", headers=admin).json()
    assert o["connection_status"] == "unreachable" and o["last_poll_error"]
    onts = client.get(f"{API}/onts?olt_id={olt['id']}", headers=admin).json()["items"]
    assert all(x["status"] == "unreachable" for x in onts)
    assert not any(x["status"] in ("offline", "los") for x in onts)
    inc = next(i for i in client.get(f"{API}/incidents", headers=admin).json()["items"] if i["source_type"] == "olt"
               and i["source_id"] == olt["id"])
    detail = client.get(f"{API}/incidents/{inc['id']}", headers=admin).json()
    assert detail["impact"]["summary"]["unknown"] >= before and detail["impact"]["summary"]["confirmed_down"] == 0
    dash = client.get(f"{API}/dashboard", headers=admin).json()
    assert dash["olts"]["unreachable"] >= 1 and dash["onts"]["unreachable"] >= before
    sim_event(client, admin, olt["id"], "olt_unreachable", {"unreachable": False})
    assert client.get(f"{API}/onts?olt_id={olt['id']}&status=online", headers=admin).json()["total"] >= before
    assert client.get(f"{API}/incidents/{inc['id']}", headers=admin).json()["status"] == "resolved"


def test_pon_port_failure_outage_correlation(client, admin, work):
    olt = olt_by_name(client, admin, "SIM-OLT-CENTRAL")
    sim_event(client, admin, olt["id"], "pon_port_fault", {"fsp": "0/1/1", "fault": True})
    incs = client.get(f"{API}/incidents", headers=admin).json()["items"]
    inc = next(i for i in incs if i["source_type"] == "olt_port" and "0/1/1" in i["title"])
    d = client.get(f"{API}/incidents/{inc['id']}", headers=admin).json()
    assert d["impact"]["summary"]["onts"] == 6 and d["impact"]["summary"]["confirmed_down"] == 6
    assert d["affected_subscribers"] == 6
    assert any(x["type"] == "fat" for x in d["impact"]["infrastructure"])
    assert any(a["category"] == "pon_port_failure" for a in d["alarms"])
    sim_event(client, admin, olt["id"], "pon_port_fault", {"fsp": "0/1/1", "fault": False})
    assert client.get(f"{API}/incidents/{inc['id']}", headers=admin).json()["status"] == "resolved"


def test_los_cluster_points_at_common_upstream_element(client, admin):
    incs = client.get(f"{API}/incidents", headers=admin).json()["items"]
    inc = next(i for i in incs if "SIM-FAT-002" in i["title"])
    assert inc["source_type"] == "fat" and "inferred from topology" in inc["suspected_cause"]
    assert inc["confirmed_down_onts"] == 6


def test_manual_infrastructure_fault_shows_inferred_impact(client, admin, technician):
    spl = next(s for s in client.get(f"{API}/splitters", headers=admin).json() if s["name"] == "SIM-SPL-L1-01")
    r = client.post(f"{API}/infrastructure/fault", headers=technician, json={"element_type": "splitter", "element_id": spl["id"],
                                                                            "note": "Closure flooded"})
    assert r.status_code == 200
    imp = r.json()["impact"]["summary"]
    assert imp["onts"] >= 20 and imp["inferred"] > 0 and imp["confirmed_down"] >= 6
    # inferred ONTs are not marked down
    ont = next(o for o in r.json()["impact"]["onts"] if o["evidence"] == "inferred")
    assert client.get(f"{API}/onts/{ont['id']}", headers=admin).json()["status"] == "online"
    client.post(f"{API}/infrastructure/fault", headers=technician, json={"element_type": "splitter", "element_id": spl["id"],
                                                                         "faulty": False, "create_incident": False})


def test_optical_thresholds_and_overview(client, admin, work):
    olt = olt_by_name(client, admin, "SIM-OLT-CENTRAL")
    ont = client.get(f"{API}/onts?olt_id={olt['id']}&status=online&limit=3", headers=admin).json()["items"][0]
    sim_event(client, admin, olt["id"], "set_rx_power", {"serial": ont["serial_number"], "rx_dbm": -28.5})
    o = client.get(f"{API}/onts/{ont['id']}", headers=admin).json()
    assert o["optical_status"] == "critical" and o["optical_violations"]
    ov = client.get(f"{API}/optical/overview", headers=admin).json()
    assert ov["weakest"][0]["rx_power_dbm"] <= -27
    assert any(d["delta_db"] <= -1 for d in ov["declining"])
    # model-specific threshold overrides the default
    r = client.post(f"{API}/optical-thresholds", headers=admin, json={"name": "SIM ONT lenient", "model": "SIM-ONT-HG8",
                                                                     "rx_warning_low": -29, "rx_critical_low": -31})
    assert r.status_code == 201
    client.post(f"{API}/onts/{ont['id']}/refresh", headers=admin)
    work()
    assert client.get(f"{API}/onts/{ont['id']}", headers=admin).json()["optical_status"] == "ok"
    client.delete(f"{API}/optical-thresholds/{r.json()['id']}", headers=admin)
    sim_event(client, admin, olt["id"], "set_rx_power", {"serial": ont["serial_number"], "rx_dbm": -20.0})
    assert client.post(f"{API}/optical-thresholds", headers=admin, json={"name": "bad", "rx_warning_low": -30,
                                                                        "rx_critical_low": -25}).status_code == 422


def test_device_metrics_history_and_unsupported_values(client, admin):
    olt = olt_by_name(client, admin, "SIM-OLT-CENTRAL")
    m = client.get(f"{API}/olts/{olt['id']}/metrics", headers=admin).json()
    assert len(m) >= 2 and m[-1]["cpu_percent"] is not None and m[-1]["source"] == "simulator"
    o = client.get(f"{API}/olts/{olt['id']}", headers=admin).json()
    assert o["temperature_c"] is not None and o["onts_registered"] > 0


def test_ont_operations_require_confirmation_and_permission(client, admin, noc, work):
    ont = client.get(f"{API}/onts?status=online&limit=1", headers=admin).json()["items"][0]
    assert client.post(f"{API}/onts/{ont['id']}/operations", headers=noc, json={"operation": "reboot",
                                                                               "confirm": ont["serial_number"]}).status_code == 403
    r = client.post(f"{API}/onts/{ont['id']}/operations", headers=admin, json={"operation": "reboot", "confirm": "nope"})
    assert r.status_code == 422 and r.json()["error"]["details"]["confirmation_required"] == ont["serial_number"]
    r = client.post(f"{API}/onts/{ont['id']}/operations", headers=admin, json={"operation": "disable", "confirm": ont["serial_number"]})
    assert r.status_code == 202
    work()
    assert client.get(f"{API}/jobs/{r.json()['job_id']}", headers=admin).json()["status"] == "succeeded"
    assert client.get(f"{API}/onts/{ont['id']}", headers=admin).json()["admin_status"] == "disabled"
    r = client.post(f"{API}/onts/{ont['id']}/operations", headers=admin, json={"operation": "enable", "confirm": ont["serial_number"]})
    work()
    logs = client.get(f"{API}/audit-logs?entity_type=ont&entity_id={ont['id']}", headers=admin).json()["items"]
    assert {"ont.disable", "ont.enable"} <= {l["action"] for l in logs}
