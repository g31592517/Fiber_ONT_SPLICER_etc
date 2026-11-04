import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="fnms-test-")
# Set FNMS_TEST_DATABASE_URL to run the suite against PostgreSQL (the database is wiped).
os.environ["FNMS_DATABASE_URL"] = os.environ.get("FNMS_TEST_DATABASE_URL") or f"sqlite:///{_tmp}/test.db"
os.environ["FNMS_ENVIRONMENT"] = "test"
os.environ["FNMS_OPERATION_RATE_LIMIT_PER_MINUTE"] = "10000"
os.environ["FNMS_LOGIN_RATE_LIMIT_PER_MINUTE"] = "10000"
os.environ["FNMS_EMBEDDED_WORKER"] = "false"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import seed  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.jobs.queue import run_pending  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def seeded():
    if os.environ.get("FNMS_TEST_DATABASE_URL"):
        seed.reset(SessionLocal())
    else:
        seed.migrate()
    db = SessionLocal()
    seed.seed_reference(db)
    seed.seed_demo(db)
    db.close()
    yield


@pytest.fixture(scope="session")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def _login(client, username, password):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def admin(client):
    return _login(client, "admin", "ChangeMe!2026")


@pytest.fixture(scope="session")
def netadmin(client):
    return _login(client, "netadmin", "NetAdmin!2026")


@pytest.fixture(scope="session")
def noc(client):
    return _login(client, "noc", "Monitoring!2026")


@pytest.fixture(scope="session")
def technician(client):
    return _login(client, "technician", "Technician!2026")


@pytest.fixture(scope="session")
def auditor(client):
    return _login(client, "auditor", "Auditor!2026")


@pytest.fixture
def work():
    """Run queued background jobs synchronously (stands in for the worker process)."""
    return run_pending


def olt_by_name(client, h, name):
    return next(o for o in client.get("/api/v1/olts", headers=h).json() if o["name"] == name)


def sim_event(client, h, olt_id, event, params=None, poll=True):
    r = client.post(f"/api/v1/simulator/olts/{olt_id}/events", headers=h,
                    json={"event": event, "params": params or {}, "poll_after": poll})
    assert r.status_code == 200, r.text
    run_pending()
    return r.json()
