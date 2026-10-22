"""Authentication, RBAC and tenant isolation."""
from conftest import PW, add_member, make_user


def test_register_login_logout(client):
    h, user, email = make_user(client)
    assert client.get("/api/auth/me", headers=h).json()["role"] == "owner"
    assert client.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    assert client.post("/api/auth/logout", headers=h).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401  # token revoked


def test_weak_password_and_duplicate(client):
    body = {"email": "weak@example.com", "full_name": "Weak", "password": "alllowercase1", "organization_name": "Org"}
    assert client.post("/api/auth/register", json=body).status_code == 422
    _, _, email = make_user(client)
    dup = {"email": email, "full_name": "Xavier", "password": PW, "organization_name": "Second Org"}
    assert client.post("/api/auth/register", json=dup).status_code == 409


def test_lockout_after_failed_logins(client):
    _, _, email = make_user(client)
    for _ in range(5):
        assert client.post("/api/auth/login", json={"email": email, "password": "wrong-Password-9"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 423


def test_unauthenticated_requests_rejected(client):
    for path in ("/api/dashboard", "/api/controls", "/api/evidence", "/api/frameworks", "/api/audit-trail"):
        assert client.get(path).status_code == 401
    assert client.get("/api/dashboard", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_cross_tenant_isolation(client, tenant, other):
    c = client.post("/api/controls", headers=tenant["h"], json={"control_key": "AC-1", "title": "Access control"}).json()
    # other tenant cannot read, modify, or delete -> 404 (not 403, so existence is not revealed)
    assert client.get(f"/api/controls/{c['id']}", headers=other["h"]).status_code == 404
    assert client.patch(f"/api/controls/{c['id']}", headers=other["h"], json={"title": "pwned"}).status_code == 404
    assert client.delete(f"/api/controls/{c['id']}", headers=other["h"]).status_code == 404
    assert client.get(f"/api/controls/{c['id']}/detail", headers=other["h"]).status_code == 404
    assert client.get("/api/controls", headers=other["h"]).json()["total"] == 0
    # cannot act in another org by header
    assert client.get("/api/controls", headers={**other["h"], "X-Org-Id": str(tenant["org"])}).status_code == 403


def test_cross_tenant_reference_rejected(client, tenant, other):
    c = client.post("/api/controls", headers=tenant["h"], json={"control_key": "AC-9", "title": "Mine"}).json()
    r = client.post("/api/findings", headers=other["h"], json={"title": "Bad link", "control_id": c["id"]})
    assert r.status_code == 422 and "control_id" in r.json()["detail"]["errors"]
    r = client.post("/api/tasks", headers=other["h"], json={"title": "t", "assignee_id": tenant["user"]["id"]})
    assert r.status_code == 422  # user from a different org


def test_role_permissions(client, tenant):
    viewer = add_member(client, tenant["h"], "viewer")
    owner_role = add_member(client, tenant["h"], "control_owner")
    assert client.post("/api/controls", headers=viewer, json={"control_key": "V-1", "title": "no"}).status_code == 403
    assert client.get("/api/controls", headers=viewer).status_code == 200
    assert client.post("/api/controls", headers=owner_role, json={"control_key": "V-2", "title": "no"}).status_code == 403
    assert client.post("/api/tasks", headers=owner_role, json={"title": "ok"}).status_code == 201
    assert client.post("/api/members", headers=owner_role, json={"email": "x@example.com", "role": "viewer", "temporary_password": PW}).status_code == 403
    assert client.get("/api/audit-trail", headers=viewer).status_code == 403
    assert client.post("/api/ai/ask", headers=viewer, json={"question": "anything here"}).status_code == 403


def test_admin_cannot_remove_last_owner(client, tenant):
    members = client.get("/api/members", headers=tenant["h"]).json()
    assert client.delete(f"/api/members/{members[0]['id']}", headers=tenant["h"]).status_code == 409


def test_security_headers_present(client):
    r = client.get("/")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_sql_injection_in_search_is_inert(client, tenant):
    client.post("/api/controls", headers=tenant["h"], json={"control_key": "S-1", "title": "Safe"})
    r = client.get("/api/controls", headers=tenant["h"], params={"q": "' OR 1=1 --"})
    assert r.status_code == 200 and r.json()["total"] == 0
    assert client.get("/api/search", headers=tenant["h"], params={"q": "'; DROP TABLE controls; --"}).status_code == 200
    assert client.get("/api/controls", headers=tenant["h"]).json()["total"] == 1
