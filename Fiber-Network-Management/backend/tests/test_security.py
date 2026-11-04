"""Acceptance criteria E — security."""
import io
import logging

from app.logging_setup import JsonFormatter, scrub
from app.security.crypto import decrypt_secret, encrypt_secret, redact

API = "/api/v1"


def test_requires_authentication(client):
    assert client.get(f"{API}/olts").status_code == 401
    assert client.get(f"{API}/olts", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_monitoring_operator_cannot_provision_or_change_devices(client, noc, admin):
    olt = client.get(f"{API}/olts", headers=noc).json()[0]
    assert client.post(f"{API}/olts/{olt['id']}/actions/poll", headers=noc).status_code == 403
    assert client.put(f"{API}/olts/{olt['id']}/credentials", headers=noc, json={"password": "x"}).status_code == 403
    assert client.post(f"{API}/provisioning/jobs", headers=noc, json={"operation": "suspend_service", "service_id": 1}).status_code == 403
    assert client.post(f"{API}/fats", headers=noc, json={"name": "nope", "capacity": 4}).status_code == 403
    assert client.get(f"{API}/audit-logs", headers=noc).status_code == 403
    assert client.get(f"{API}/alarms", headers=noc).status_code == 200


def test_technician_limited_operations(client, technician, admin):
    svc = client.get(f"{API}/services?status=active&limit=1", headers=admin).json()["items"][0]
    assert client.post(f"{API}/provisioning/jobs", headers=technician,
                       json={"operation": "suspend_service", "service_id": svc["id"]}).status_code == 403
    olt = client.get(f"{API}/olts", headers=technician).json()[0]
    assert client.post(f"{API}/olts/{olt['id']}/actions/discover", headers=technician).status_code == 202


def test_netadmin_cannot_approve_without_permission_scope(client, auditor):
    assert client.get(f"{API}/audit-logs", headers=auditor).status_code == 200
    assert client.post(f"{API}/alarms/1/actions", headers=auditor, json={"action": "acknowledge"}).status_code == 403


def test_user_and_role_management_audited(client, admin):
    roles = client.get(f"{API}/roles", headers=admin).json()
    noc_role = next(r for r in roles if r["name"] == "Monitoring Operator")
    r = client.post(f"{API}/users", headers=admin, json={"username": "weak", "password": "short", "role_id": noc_role["id"]})
    assert r.status_code == 422
    r = client.post(f"{API}/users", headers=admin, json={"username": "newnoc", "password": "GoodPass123", "role_id": noc_role["id"]})
    assert r.status_code == 201 and "password_hash" not in r.text
    r = client.post(f"{API}/roles", headers=admin, json={"name": "Viewer", "permissions": ["network.read", "bogus.perm"]})
    assert r.status_code == 422
    r = client.post(f"{API}/roles", headers=admin, json={"name": "Viewer", "permissions": ["network.read"]})
    assert r.status_code == 201
    logs = client.get(f"{API}/audit-logs?action=role.", headers=admin).json()["items"]
    assert any(l["action"] == "role.create" for l in logs)
    # disabling a user revokes their sessions
    tok = client.post(f"{API}/auth/login", json={"username": "newnoc", "password": "GoodPass123"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get(f"{API}/auth/me", headers=h).status_code == 200
    uid = next(u["id"] for u in client.get(f"{API}/users", headers=admin).json() if u["username"] == "newnoc")
    client.patch(f"{API}/users/{uid}", headers=admin, json={"is_active": False})
    assert client.get(f"{API}/auth/me", headers=h).status_code == 401


def test_logout_revokes_token(client):
    tok = client.post(f"{API}/auth/login", json={"username": "auditor", "password": "Auditor!2026"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.post(f"{API}/auth/logout", headers=h).status_code == 200
    assert client.get(f"{API}/auth/me", headers=h).status_code == 401


def test_account_lockout(client, admin):
    roles = client.get(f"{API}/roles", headers=admin).json()
    rid = next(r["id"] for r in roles if r["name"] == "Read-only Auditor")
    client.post(f"{API}/users", headers=admin, json={"username": "locktest", "password": "LockTest1234", "role_id": rid})
    for _ in range(5):
        assert client.post(f"{API}/auth/login", json={"username": "locktest", "password": "wrong-password"}).status_code == 401
    r = client.post(f"{API}/auth/login", json={"username": "locktest", "password": "LockTest1234"})
    assert r.status_code == 401 and "locked" in r.json()["error"]["message"]


def test_rate_limiter():
    import pytest

    from app.core.errors import RateLimited
    from app.security.auth import RateLimiter

    rl = RateLimiter()
    for _ in range(3):
        rl.check("k", 3)
    with pytest.raises(RateLimited):
        rl.check("k", 3)


def test_secrets_encrypted_and_redacted():
    token = encrypt_secret("community-public")
    assert token != "community-public" and decrypt_secret(token) == "community-public"
    red = redact({"password": "p", "snmp_community": "c", "nested": {"api_token": "t", "name": "ok"}, "list": [{"auth_key": "k"}]})
    assert red["password"] != "p" and red["snmp_community"] != "c" and red["nested"]["api_token"] != "t"
    assert red["nested"]["name"] == "ok" and red["list"][0]["auth_key"] != "k"


def test_logs_scrub_secrets():
    assert "hunter2" not in scrub("login password=hunter2 ok")
    assert "abc.def" not in scrub("Authorization: Bearer abc.def")
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(JsonFormatter())
    lg = logging.getLogger("fnms.test")
    lg.addHandler(h)
    lg.warning('device said "community": "s3cr3t"')
    assert "s3cr3t" not in buf.getvalue()


def test_no_credentials_in_any_olt_response(client, admin):
    for o in client.get(f"{API}/olts", headers=admin).json():
        assert not any(k.endswith("_enc") for k in o)
    jobs = client.get(f"{API}/jobs?limit=200", headers=admin).text
    assert "S3cret-SSH-pass" not in jobs


def test_openapi_available(client):
    spec = client.get("/api/openapi.json").json()
    assert "/api/v1/olts" in spec["paths"] and "/api/v1/integration/v1/services/{external_service_id}" in spec["paths"]
    assert client.get("/api/health").json()["status"] == "ok"
