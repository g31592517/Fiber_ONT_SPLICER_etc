"""Acceptance criteria D — provisioning."""
from tests.conftest import olt_by_name, sim_event

API = "/api/v1"


def _template(client, h, mbps):
    return next(t for t in client.get(f"{API}/service-templates", headers=h).json() if t["name"] == f"FTTH Internet {mbps} Mbps")


def _job(client, h, jid):
    return client.get(f"{API}/provisioning/jobs/{jid}", headers=h).json()


def _new_service_on_discovered(client, h, work, serial, fsp="0/2/3"):
    olt = olt_by_name(client, h, "SIM-OLT-CENTRAL")
    sim_event(client, h, olt["id"], "new_ont", {"fsp": fsp, "serial": serial, "rx_dbm": -20.0})
    disc = next(d for d in client.get(f"{API}/discovered-onts?olt_id={olt['id']}", headers=h).json() if d["serial_number"] == serial)
    sub = client.post(f"{API}/subscribers", headers=h, json={"customer_ref": f"T-{serial}", "display_name": f"Test {serial}"}).json()
    svc = client.post(f"{API}/services", headers=h, json={"subscriber_id": sub["id"], "service_ref": f"TS-{serial}",
                                                           "external_service_id": f"X-{serial}"}).json()
    return olt, disc, svc


def test_service_template_crud_and_validation(client, admin):
    bw = client.get(f"{API}/bandwidth-profiles", headers=admin).json()[0]
    r = client.post(f"{API}/service-templates", headers=admin, json={
        "name": "Test QinQ", "bandwidth_profile_id": bw["id"], "vlan_mode": "qinq", "svlan": 300, "gem_port": 1,
        "mappings": [{"adapter": "simulator", "line_profile_ref": "10", "service_profile_ref": "10",
                      "traffic_table_up_ref": "10", "traffic_table_down_ref": "10"}]})
    assert r.status_code == 422  # QinQ requires a C-VLAN
    r = client.post(f"{API}/service-templates", headers=admin, json={
        "name": "Test QinQ", "bandwidth_profile_id": bw["id"], "vlan_mode": "qinq", "svlan": 300, "cvlan": 10, "gem_port": 1,
        "mappings": [{"adapter": "simulator", "line_profile_ref": "10", "service_profile_ref": "10",
                      "traffic_table_up_ref": "10", "traffic_table_down_ref": "10"}]})
    assert r.status_code == 201 and r.json()["mappings"][0]["adapter"] == "simulator"


def test_activate_suspend_restore_modify_disconnect(client, admin, work):
    olt, disc, svc = _new_service_on_discovered(client, admin, work, "SIMUE2E00001")
    t50, t100 = _template(client, admin, 50), _template(client, admin, 100)
    # activate (register + service-port + verify)
    job = client.post(f"{API}/provisioning/jobs", headers=admin, json={
        "operation": "activate_service", "discovery_id": disc["id"], "service_id": svc["id"], "template_id": t50["id"]}).json()
    work()
    job = _job(client, admin, job["id"])
    assert job["state"] == "awaiting_approval", job["validation"]
    assert [s["action"] for s in job["steps"]] == ["register_ont", "verify_ont", "create_service_port", "verify_service"]
    assert any("VLAN 100" in s["description"] for s in job["steps"])
    client.post(f"{API}/provisioning/jobs/{job['id']}/approve", headers=admin, json={})
    work()
    job = _job(client, admin, job["id"])
    assert job["state"] == "succeeded", job
    assert job["verification"]["ok"] is True
    s = client.get(f"{API}/services/{svc['id']}", headers=admin).json()
    assert s["status"] == "active" and s["ont"]["serial_number"] == "SIMUE2E00001"
    sp_index = s["network_config"]["service_ports"][0]["index"]
    state = client.get(f"{API}/simulator/olts/{olt['id']}/state", headers=admin).json()
    assert state["service_ports"][str(sp_index)]["vlan"] == 100

    def run(op, **extra):
        j = client.post(f"{API}/provisioning/jobs", headers=admin, json={"operation": op, "service_id": svc["id"], **extra}).json()
        work()
        j = _job(client, admin, j["id"])
        assert j["state"] == "awaiting_approval", (op, j["validation"])
        r = client.post(f"{API}/provisioning/jobs/{j['id']}/approve", headers=admin, json={"confirm": j.get("confirmation_token")})
        assert r.status_code == 200, r.text
        work()
        return _job(client, admin, j["id"])

    j = run("suspend_service")
    assert j["state"] == "succeeded"
    assert client.get(f"{API}/services/{svc['id']}", headers=admin).json()["status"] == "suspended"
    state = client.get(f"{API}/simulator/olts/{olt['id']}/state", headers=admin).json()
    assert state["service_ports"][str(sp_index)]["admin"] == "disabled"
    # suspending twice is rejected at validation, not executed blindly
    j2 = client.post(f"{API}/provisioning/jobs", headers=admin, json={"operation": "suspend_service", "service_id": svc["id"]}).json()
    work()
    assert _job(client, admin, j2["id"])["state"] == "validation_failed"
    assert run("restore_service")["state"] == "succeeded"
    assert client.get(f"{API}/services/{svc['id']}", headers=admin).json()["status"] == "active"
    j = run("modify_service", template_id=t100["id"])
    assert j["state"] == "succeeded"
    s = client.get(f"{API}/services/{svc['id']}", headers=admin).json()
    assert s["template"] == "FTTH Internet 100 Mbps"
    assert s["network_config"]["service_ports"][0]["traffic_down_ref"] == t100["mappings"][0]["traffic_table_down_ref"]
    j = run("disconnect_service", deregister_ont=True)
    assert j["state"] == "succeeded", j
    s = client.get(f"{API}/services/{svc['id']}", headers=admin).json()
    assert s["status"] == "disconnected"
    assert client.get(f"{API}/onts/{s['ont']['id']}", headers=admin).json()["registration_status"] == "deregistered"


def test_destructive_operation_needs_confirmation(client, admin, work):
    svc = client.get(f"{API}/services?status=active&limit=1", headers=admin).json()["items"][0]
    j = client.post(f"{API}/provisioning/jobs", headers=admin, json={"operation": "disconnect_service", "service_id": svc["id"]}).json()
    work()
    j = _job(client, admin, j["id"])
    assert j["confirmation_token"]
    r = client.post(f"{API}/provisioning/jobs/{j['id']}/approve", headers=admin, json={"confirm": "wrong"})
    assert r.status_code == 422
    assert client.post(f"{API}/provisioning/jobs/{j['id']}/cancel", headers=admin).json()["state"] == "cancelled"


def test_failure_rollback_and_safe_retry(client, admin, work):
    olt, disc, svc = _new_service_on_discovered(client, admin, work, "SIMUFA110001", fsp="0/2/4")
    t20 = _template(client, admin, 20)
    sim_event(client, admin, olt["id"], "fail_next", {"operation": "create_service_port", "count": 1}, poll=False)
    j = client.post(f"{API}/provisioning/jobs", headers=admin, json={
        "operation": "activate_service", "discovery_id": disc["id"], "service_id": svc["id"], "template_id": t20["id"],
        "auto_approve": True}).json()
    work()
    j = _job(client, admin, j["id"])
    assert j["state"] == "rolled_back", j
    steps = {s["action"]: s for s in j["steps"]}
    assert steps["register_ont"]["state"] == "compensated" and steps["create_service_port"]["state"] == "failed"
    assert "injected failure" in j["error"]
    # the ONT is back in discovery (compensation deregistered it) and the service is marked failed
    assert client.get(f"{API}/services/{svc['id']}", headers=admin).json()["status"] == "failed"
    client.post(f"{API}/olts/{olt['id']}/actions/discover", headers=admin)
    work()
    assert j["retryable"] is True
    r = client.post(f"{API}/provisioning/jobs/{j['id']}/retry", headers=admin)
    assert r.status_code == 201
    new = r.json()
    assert new["retry_of"] == j["id"]
    # a job cannot be retried twice
    assert client.post(f"{API}/provisioning/jobs/{j['id']}/retry", headers=admin).status_code == 422
    work()
    new = _job(client, admin, new["id"])
    assert new["state"] == "awaiting_approval", new["validation"]
    client.post(f"{API}/provisioning/jobs/{new['id']}/approve", headers=admin, json={})
    work()
    assert _job(client, admin, new["id"])["state"] == "succeeded"


def test_partial_success_without_rollback_and_idempotent_retry(client, admin, work):
    olt, disc, svc = _new_service_on_discovered(client, admin, work, "SIMUPA770001", fsp="0/2/5")
    t30 = _template(client, admin, 30)
    sim_event(client, admin, olt["id"], "fail_next", {"operation": "create_service_port", "count": 1}, poll=False)
    j = client.post(f"{API}/provisioning/jobs", headers=admin, json={
        "operation": "activate_service", "discovery_id": disc["id"], "service_id": svc["id"], "template_id": t30["id"],
        "rollback_on_failure": False, "auto_approve": True}).json()
    work()
    j = _job(client, admin, j["id"])
    assert j["state"] == "partially_succeeded"
    ont = client.get(f"{API}/onts?q=SIMUPA770001", headers=admin).json()["items"][0]
    assert ont["registration_status"] == "registered"
    # Retry: the ONT is now registered, so the new plan does not repeat registration.
    new = client.post(f"{API}/provisioning/jobs/{j['id']}/retry", headers=admin).json()
    work()
    new = _job(client, admin, new["id"])
    assert new["state"] == "awaiting_approval", new["validation"]
    assert "register_ont" not in [s["action"] for s in new["steps"]]
    client.post(f"{API}/provisioning/jobs/{new['id']}/approve", headers=admin, json={})
    work()
    assert _job(client, admin, new["id"])["state"] == "succeeded"


def test_validation_failures_are_recorded(client, admin, work):
    olt = olt_by_name(client, admin, "SIM-OLT-CENTRAL")
    sim_event(client, admin, olt["id"], "new_ont", {"fsp": "0/2/6", "serial": "SIMUBAD00001"})
    disc = next(d for d in client.get(f"{API}/discovered-onts?olt_id={olt['id']}", headers=admin).json() if d["serial_number"] == "SIMUBAD00001")
    j = client.post(f"{API}/provisioning/jobs", headers=admin, json={
        "operation": "register_ont", "discovery_id": disc["id"], "line_profile_ref": "999", "service_profile_ref": "10"}).json()
    work()
    j = _job(client, admin, j["id"])
    assert j["state"] == "validation_failed"
    assert any("does not exist" in e for e in j["validation"]["errors"])
    assert client.post(f"{API}/provisioning/jobs/{j['id']}/approve", headers=admin, json={}).status_code == 422
    hist = client.get(f"{API}/provisioning/jobs?state=validation_failed", headers=admin).json()
    assert hist["total"] >= 1


def test_unverified_adapter_writes_blocked(client, admin, work):
    from app.adapters.base import Capability
    from app.adapters.huawei.adapter import HuaweiGponAdapter
    from app.adapters.registry import write_allowed

    ok, why = write_allowed(HuaweiGponAdapter, Capability.ONT_REGISTRATION)
    assert ok is False and "unverified" in why


def test_integration_contract_idempotent(client, admin, work):
    svc = client.get(f"{API}/services?status=active&limit=200", headers=admin).json()["items"]
    target = next(s for s in svc if s["external_service_id"] and s["ont"] and s["ont"]["status"] == "online")
    ext = target["external_service_id"]
    st = client.get(f"{API}/integration/v1/services/{ext}", headers=admin).json()
    assert st["status"] == "active"
    r1 = client.post(f"{API}/integration/v1/services/{ext}/suspend", headers=admin, json={"external_request_id": "billing-req-1"})
    r2 = client.post(f"{API}/integration/v1/services/{ext}/suspend", headers=admin, json={"external_request_id": "billing-req-1"})
    assert r1.status_code == 202 and r1.json()["job_id"] == r2.json()["job_id"]
    work()
    job = client.get(f"{API}/integration/v1/jobs/{r1.json()['job_id']}", headers=admin).json()
    assert job["state"] == "awaiting_approval"  # integration.auto_approve defaults to false
