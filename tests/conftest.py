import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="aegis_test_")
os.environ.update({"DATABASE_URL": f"sqlite:///{_tmp}/test.db", "UPLOAD_DIR": f"{_tmp}/uploads", "RATE_LIMIT_PER_MINUTE": "100000",
                   "AUTH_RATE_LIMIT_PER_MINUTE": "100000", "AI_PROVIDER": "none", "JWT_SECRET": "test-secret-" + "x" * 40, "APP_ENV": "test"})
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

PW = "Str0ng-Password-1"


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


_counter = {"n": 0}


def make_user(client, org="Org", email=None):
    _counter["n"] += 1
    email = email or f"user{_counter['n']}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "full_name": "Test User", "password": PW, "organization_name": org})
    assert r.status_code == 201, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}, r.json()["user"], email


@pytest.fixture()
def tenant(client):
    h, user, email = make_user(client)
    return {"h": h, "user": user, "email": email, "org": user["memberships"][0]["org_id"]}


@pytest.fixture()
def other(client):
    h, user, email = make_user(client, "Other Org")
    return {"h": h, "user": user, "email": email, "org": user["memberships"][0]["org_id"]}


def add_member(client, owner_h, role, org_id=None):
    _counter["n"] += 1
    email = f"member{_counter['n']}@example.com"
    r = client.post("/api/members", headers=owner_h, json={"email": email, "role": role, "full_name": role, "temporary_password": PW})
    assert r.status_code == 201, r.text
    lg = client.post("/api/auth/login", json={"email": email, "password": PW})
    assert lg.status_code == 200
    return {"Authorization": "Bearer " + lg.json()["access_token"]}


def framework_id(client, h, key="iso27001"):
    return next(f["id"] for f in client.get("/api/frameworks", headers=h).json()["items"] if f["key"] == key)
