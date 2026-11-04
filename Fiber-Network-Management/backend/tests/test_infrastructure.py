"""Acceptance criteria A — infrastructure management."""
from tests.conftest import olt_by_name

API = "/api/v1"


def test_create_site_and_olt_with_credentials(client, admin, work):
    site = client.post(f"{API}/sites", headers=admin, json={"name": "Test Site A", "code": "TSA", "latitude": -1.3, "longitude": 36.8})
    assert site.status_code == 201
    r = client.post(f"{API}/olts", headers=admin, json={
        "name": "Test Huawei OLT", "site_id": site.json()["id"], "adapter": "huawei_smartax_cli", "model": "MA5800-X7",
        "host": "192.0.2.10", "management_protocol": "ssh", "management_port": 22, "snmp_version": "v3",
        "snmp_v3_username": "nms", "snmp_v3_auth_protocol": "SHA256", "snmp_v3_priv_protocol": "AES",
        "poll_interval_seconds": 300, "monitoring_enabled": False})
    assert r.status_code == 201, r.text
    olt = r.json()
    assert olt["adapter_experimental"] is True
    assert olt["capabilities"]["ont_registration"]["verification"] == "unverified"
    # edit
    body = {k: olt[k] for k in ("name", "site_id", "adapter", "host", "management_protocol", "management_port", "snmp_version",
                                "snmp_v3_username", "snmp_v3_auth_protocol", "snmp_v3_priv_protocol", "poll_interval_seconds",
                                "monitoring_enabled", "model")}
    body["rack_location"] = "Rack 9"
    r = client.put(f"{API}/olts/{olt['id']}", headers=admin, json=body)
    assert r.status_code == 200 and r.json()["rack_location"] == "Rack 9"
    # credentials are write-only
    r = client.put(f"{API}/olts/{olt['id']}/credentials", headers=admin,
                   json={"username": "nmsuser", "password": "S3cret-SSH-pass", "snmp_v3_auth_key": "authkey-123456",
                         "snmp_v3_priv_key": "privkey-123456"})
    assert r.status_code == 200
    text = r.text
    assert "S3cret-SSH-pass" not in text and "authkey-123456" not in text and "privkey-123456" not in text
    assert r.json()["has_password"] is True and r.json()["has_snmp_v3_auth_key"] is True
    assert "password_enc" not in r.json()
    assert "S3cret" not in client.get(f"{API}/olts/{olt['id']}", headers=admin).text
    # audit entry exists but does not contain the secret
    logs = client.get(f"{API}/audit-logs?action=olt.credentials_change", headers=admin).json()
    assert logs["total"] >= 1 and "S3cret" not in str(logs)


def test_poll_interval_minimum_enforced(client, admin):
    r = client.post(f"{API}/olts", headers=admin, json={"name": "Too fast", "adapter": "simulator", "host": "x.simulated",
                                                       "poll_interval_seconds": 5})
    assert r.status_code == 422


def test_planned_adapter_cannot_be_selected(client, admin):
    r = client.post(f"{API}/olts", headers=admin, json={"name": "ZTE", "adapter": "zte_cli", "host": "192.0.2.20"})
    assert r.status_code == 422
    assert "planned" in r.json()["error"]["message"]


def test_connection_test_through_adapter(client, admin, work):
    olt = olt_by_name(client, admin, "SIM-OLT-CENTRAL")
    r = client.post(f"{API}/olts/{olt['id']}/actions/test-connection", headers=admin)
    assert r.status_code == 202
    work()
    job = client.get(r.json()["poll_url"].replace("/api/v1", API), headers=admin).json()
    assert job["status"] == "succeeded"
    assert job["result"]["ok"] is True and job["result"]["identity"]["vendor"] == "SIMULATED"


def test_hardware_hierarchy(client, admin):
    olt = olt_by_name(client, admin, "SIM-OLT-CENTRAL")
    hw = client.get(f"{API}/olts/{olt['id']}/hardware", headers=admin).json()
    boards = hw["frames"][0]["boards"]
    slots = {b["slot"]: b for b in boards}
    assert slots[1]["installed"] and len(slots[1]["ports"]) == 8
    assert slots[3]["installed"] is False and slots[3]["status"] == "empty"
    assert slots[9]["role"] == "uplink"
    assert all(b["source"] == "device" for b in boards)


def test_fat_ports_capacity_and_reservation(client, admin, technician):
    r = client.post(f"{API}/fats", headers=admin, json={"name": "TEST-FAT-1", "capacity": 6, "latitude": -1.28, "longitude": 36.82})
    assert r.status_code == 201
    fat = r.json()
    assert fat["capacity_summary"]["total"] == 6 and fat["capacity_summary"]["available"] == 6
    p3 = next(p for p in fat["ports"] if p["number"] == 3)
    r = client.patch(f"{API}/infra-ports/{p3['id']}", headers=technician, json={"status": "faulty"})
    assert r.status_code == 200
    p5 = next(p for p in fat["ports"] if p["number"] == 5)
    assert client.patch(f"{API}/infra-ports/{p5['id']}", headers=technician, json={"status": "reserved"}).status_code == 422
    assert client.patch(f"{API}/infra-ports/{p5['id']}", headers=technician,
                        json={"status": "reserved", "reserved_for": "Job 42"}).status_code == 200
    cap = client.get(f"{API}/fats/{fat['id']}", headers=admin).json()["capacity_summary"]
    assert cap["faulty"] == 1 and cap["reserved"] == 1 and cap["available"] == 4
    # capacity can grow; cannot shrink below a port in use is covered in link test
    body = {"name": "TEST-FAT-1", "capacity": 8}
    assert client.put(f"{API}/fats/{fat['id']}", headers=admin, json=body).json()["capacity_summary"]["total"] == 8


def test_splitter_cascade_links_trace_and_duplicate_prevention(client, admin):
    olt = olt_by_name(client, admin, "SIM-OLT-EAST")
    ports = client.get(f"{API}/pon-ports?olt_id={olt['id']}", headers=admin).json()
    free_pon = next(p for p in ports if p["fsp"] == "0/1/5")
    types = {t["name"]: t["id"] for t in client.get(f"{API}/splitter-types", headers=admin).json()}
    s1 = client.post(f"{API}/splitters", headers=admin, json={"name": "TEST-SPL-L1", "splitter_type_id": types["1:2"]}).json()
    s2 = client.post(f"{API}/splitters", headers=admin, json={"name": "TEST-SPL-L2", "splitter_type_id": types["1:8"]}).json()
    assert len([p for p in s2["ports"] if p["direction"] == "out"]) == 8
    fat = client.post(f"{API}/fats", headers=admin, json={"name": "TEST-FAT-2", "capacity": 8}).json()
    cable = client.post(f"{API}/cables", headers=admin, json={"name": "TEST-FDR", "cable_type": "feeder", "fiber_count": 12}).json()
    assert len(cable["cores"]) == 12
    core1 = cable["cores"][0]["id"]
    s1_in = next(p for p in s1["ports"] if p["direction"] == "in")["id"]
    s1_out1 = next(p for p in s1["ports"] if p["direction"] == "out" and p["number"] == 1)["id"]
    s2_in = next(p for p in s2["ports"] if p["direction"] == "in")["id"]
    s2_out1 = next(p for p in s2["ports"] if p["direction"] == "out" and p["number"] == 1)["id"]
    fat_p1 = fat["ports"][0]["id"]

    def link(a_type, a_id, b_type, b_id, **kw):
        return client.post(f"{API}/links", headers=admin, json={"a_type": a_type, "a_id": a_id, "b_type": b_type, "b_id": b_id, **kw})

    assert link("olt_port", free_pon["id"], "infra_port", s1_in, cable_id=cable["id"], core_id=core1, verification="verified").status_code == 201
    assert link("infra_port", s1_out1, "infra_port", s2_in, verification="verified").status_code == 201
    assert link("infra_port", s2_out1, "infra_port", fat_p1, verification="unverified").status_code == 201
    # duplicate occupancy: the same splitter output cannot feed a second connection
    dup = link("infra_port", s2_out1, "infra_port", fat["ports"][1]["id"])
    assert dup.status_code == 422 and "already feeds" in str(dup.json())
    # same core cannot be reused
    s1_out2 = next(p for p in s1["ports"] if p["direction"] == "out" and p["number"] == 2)["id"]
    s3 = client.post(f"{API}/splitters", headers=admin, json={"name": "TEST-SPL-L3", "splitter_type_id": types["1:4"]}).json()
    s3_in = next(p for p in s3["ports"] if p["direction"] == "in")["id"]
    reuse = link("infra_port", s1_out2, "infra_port", s3_in, cable_id=cable["id"], core_id=core1)
    assert reuse.status_code == 422 and "already used" in str(reuse.json())
    # impossible direction
    assert link("infra_port", s2_in, "infra_port", s3_in).status_code == 422
    # loop prevention
    s3_out = next(p for p in s3["ports"] if p["direction"] == "out")["id"]
    assert link("infra_port", s1_out2, "infra_port", s3_in).status_code == 201
    loop = client.post(f"{API}/links/validate", headers=admin, json={"a_type": "infra_port", "a_id": s3_out, "b_type": "infra_port", "b_id": s1_in})
    assert loop.json()["valid"] is False
    # attach an existing registered ONT? use a FAT port -> ONT via validate only (ONT already has upstream)
    trace = client.get(f"{API}/trace/endpoint?type=infra_port&id={fat_p1}", headers=admin).json()
    labels = [h["label"] for h in trace["hops"]]
    assert trace["complete"] is True
    assert any("TEST-SPL-L2" in l for l in labels) and any("TEST-SPL-L1" in l for l in labels) and "PON 0/1/5" in labels[-1]
    assert trace["all_verified"] is False  # one unverified segment
    # cannot shrink FAT below an occupied/connected port
    r = client.put(f"{API}/fats/{fat['id']}", headers=admin, json={"name": "TEST-FAT-2", "capacity": 0})
    assert r.status_code == 422


def test_trace_registered_ont_to_olt(client, admin):
    onts = client.get(f"{API}/onts?fat_id=" + str(next(f["id"] for f in client.get(f"{API}/fats", headers=admin).json()
                                                      if f["name"] == "SIM-FAT-001")), headers=admin).json()["items"]
    assert len(onts) == 8
    detail = client.get(f"{API}/onts/{onts[0]['id']}", headers=admin).json()
    hops = [h["label"] for h in detail["trace"]["physical"]["hops"]]
    assert detail["trace"]["physical"]["complete"]
    assert hops[0].startswith("ONT") and "SIM-FAT-001" in hops[1] and hops[-1].endswith("PON 0/1/0")
    assert any("SIM-SPL-L1-01" in h for h in hops) and any("SIM-ODF" in h for h in hops)
    assert detail["fat"]["fat_name"] == "SIM-FAT-001"
    assert detail["subscriber"] is not None


def test_planned_paths_are_not_confirmed(client, admin):
    east = olt_by_name(client, admin, "SIM-OLT-EAST")
    onts = client.get(f"{API}/onts?olt_id={east['id']}", headers=admin).json()["items"]
    with_fat = [o for o in onts if o["fat"]]
    d = client.get(f"{API}/onts/{with_fat[0]['id']}", headers=admin).json()
    assert d["trace"]["physical"]["all_verified"] is False
    assert any("unverified" in w or "planned" in w for w in d["trace"]["warnings"])


def test_topology_graph_and_map(client, admin):
    g = client.get(f"{API}/topology/graph", headers=admin).json()
    types = {n["type"] for n in g["nodes"]}
    assert {"olt", "pon_port", "splitter", "fat", "ont", "odf", "closure"} <= types
    assert any(e["kind"] == "logical" for e in g["edges"])
    fat1 = next(n for n in g["nodes"] if n["label"] == "SIM-FAT-001")
    sub = client.get(f"{API}/topology/graph?fat_id={fat1['ref_id']}", headers=admin).json()
    assert any(n["type"] == "olt" for n in sub["nodes"]) and sum(1 for n in sub["nodes"] if n["type"] == "ont") == 8
    m = client.get(f"{API}/topology/map", headers=admin).json()
    assert any(f["kind"] == "fat" for f in m["features"]) and any(l["kind"] == "cable" for l in m["lines"])


def test_find_subscriber_by_ont_fat_and_port(client, admin):
    ont = client.get(f"{API}/onts?limit=1", headers=admin).json()["items"][0]
    r = client.get(f"{API}/subscribers?ont_serial={ont['serial_number']}", headers=admin).json()
    assert r["total"] == 1
    fat = next(f for f in client.get(f"{API}/fats", headers=admin).json() if f["name"] == "SIM-FAT-001")
    assert client.get(f"{API}/subscribers?fat_id={fat['id']}", headers=admin).json()["total"] == 8
    assert client.get(f"{API}/subscribers?pon_port_id={ont['pon_port_id']}", headers=admin).json()["total"] >= 1
