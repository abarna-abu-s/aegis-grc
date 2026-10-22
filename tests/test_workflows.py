"""Critical user journeys: framework -> assessment -> evidence -> compliance -> risk -> remediation -> approval -> report."""
import io
from datetime import date, timedelta

from conftest import add_member, framework_id


def upload(client, h, title="Access review", content=b"Quarterly access review completed by the system owner.", name="review.txt",
           control_ids="", valid_days=90, **extra):
    data = {"title": title, "control_ids": control_ids, "valid_until": (date.today() + timedelta(days=valid_days)).isoformat(), **extra}
    return client.post("/api/evidence", headers=h, data=data, files={"file": (name, io.BytesIO(content), "text/plain")})


def start_assessment(client, h, key="iso27001"):
    a = client.post("/api/assessments", headers=h, json={"name": "ISO readiness", "framework_id": framework_id(client, h, key)})
    assert a.status_code == 201, a.text
    return a.json()["id"]


def test_framework_library_labels_templates_as_unverified(client, tenant):
    items = client.get("/api/frameworks", headers=tenant["h"]).json()["items"]
    keys = {f["key"] for f in items}
    assert {"iso27001", "iso27701", "iso42001", "soc2", "gdpr", "dpdp2023", "nistcsf2", "nist80053", "cis8", "pcidss4", "hipaa", "dora"} <= keys
    assert all(f["source_status"] == "template_unverified" for f in items)
    iso = next(f for f in items if f["key"] == "iso27001")
    detail = client.get(f"/api/frameworks/{iso['id']}", headers=tenant["h"]).json()
    assert all(not c["has_requirement_text"] for c in detail["controls"])  # no invented requirement text
    assert all(c["evidence_requirements_are_suggestions"] for c in detail["controls"])


def test_assessment_without_controls_loaded_is_refused(client, tenant):
    r = client.post("/api/assessments", headers=tenant["h"], json={"name": "x", "framework_id": framework_id(client, tenant["h"], "iso27701")})
    assert r.status_code == 422


def test_full_compliance_journey(client, tenant):
    h = tenant["h"]
    aid = start_assessment(client, h)
    link = client.post(f"/api/assessments/{aid}/auto-link", headers=h).json()
    assert link["created_controls"] > 0
    items = client.get(f"/api/assessments/{aid}/items", headers=h, params={"q": "Access control", "page_size": 5}).json()["items"]
    item = next(i for i in items if i["ref_id"] == "5.15")
    cid = item["control_id"]
    assert cid

    # cannot be compliant without evidence
    r = client.patch(f"/api/assessment-items/{item['id']}", headers=h, json={"result": "compliant"})
    assert r.status_code == 422 and "evidence" in r.json()["detail"]["message"].lower()

    # evidence uploaded -> still pending -> still cannot be compliant
    ev = upload(client, h, control_ids=str(cid))
    assert ev.status_code == 201, ev.text
    eid = ev.json()["id"]
    assert ev.json()["status"] == "pending" and ev.json()["sha256"]
    assert client.patch(f"/api/assessment-items/{item['id']}", headers=h, json={"result": "compliant"}).status_code == 422

    # approve evidence -> compliant allowed
    assert client.post(f"/api/evidence/{eid}/review", headers=h, json={"decision": "approved"}).status_code == 200
    assert client.patch(f"/api/assessment-items/{item['id']}", headers=h, json={"result": "compliant"}).status_code == 200

    score = client.get(f"/api/assessments/{aid}", headers=h).json()["score"]
    assert score["counts"]["compliant"] == 1 and score["assessment_score"] > 0
    dash = client.get("/api/dashboard", headers=h).json()
    assert dash["readiness"]["score"] > 0 and dash["evidence_status"]["current"] == 1

    # not-applicable requires justification
    other = next(i for i in client.get(f"/api/assessments/{aid}/items", headers=h, params={"page_size": 10}).json()["items"] if i["id"] != item["id"])
    assert client.patch(f"/api/assessment-items/{other['id']}", headers=h, json={"applicability": "not_applicable"}).status_code == 422
    assert client.patch(f"/api/assessment-items/{other['id']}", headers=h, json={"applicability": "not_applicable", "justification": "Out of scope"}).status_code == 200


def test_expired_evidence_breaks_compliance_coverage(client, tenant):
    h = tenant["h"]
    aid = start_assessment(client, h)
    client.post(f"/api/assessments/{aid}/auto-link", headers=h)
    cid = client.get(f"/api/assessments/{aid}/items", headers=h, params={"q": "Logging"}).json()["items"][0]["control_id"]
    eid = upload(client, h, control_ids=str(cid), valid_days=-1).json()["id"]
    client.post(f"/api/evidence/{eid}/review", headers=h, json={"decision": "approved"})
    detail = client.get(f"/api/controls/{cid}/detail", headers=h).json()
    assert detail["health"] == "at_risk" and detail["evidence"][0]["status"] == "expired"
    assert any(m["control_id"] == cid for m in client.get("/api/evidence-intel/missing", headers=h).json()["items"])


def test_cross_framework_evidence_reuse(client, tenant):
    h = tenant["h"]
    a1 = start_assessment(client, h, "iso27001")
    client.post(f"/api/assessments/{a1}/auto-link", headers=h)
    a2 = client.post("/api/assessments", headers=h, json={"name": "NIST CSF", "framework_id": framework_id(client, h, "nistcsf2")}).json()["id"]
    res = client.post(f"/api/assessments/{a2}/auto-link", headers=h).json()
    assert res["reused_controls"] > 0  # PR.AA reuses the control created for ISO 5.15 through the template mapping
    ctrl = client.get(f"/api/assessments/{a2}/items", headers=h, params={"q": "PR.AA"}).json()["items"][0]["control_id"]
    iso_ctrl = client.get(f"/api/assessments/{a1}/items", headers=h, params={"q": "Access control"}).json()["items"][0]["control_id"]
    assert ctrl == iso_ctrl


def test_risk_scoring_treatment_and_matrix(client, tenant):
    h = tenant["h"]
    r = client.post("/api/risks", headers=h, json={"title": "Data loss", "likelihood": 4, "impact": 5}).json()
    assert r["inherent_score"] == 20 and r["inherent_level"] == "critical"
    assert client.post("/api/risks", headers=h, json={"title": "bad", "likelihood": 9, "impact": 1}).status_code == 422
    assert client.post("/api/treatment-plans", headers=h, json={"risk_id": r["id"], "action": "Backups"}).status_code == 201
    m = client.get("/api/risk-matrix", headers=h).json()
    assert "4,5" in m["cells"]
    assert any(x["kind"] == "risk_no_plan" for x in client.get("/api/insights/recommendations", headers=h).json()["items"]) is False


def test_finding_remediation_flow(client, tenant):
    h = tenant["h"]
    f = client.post("/api/findings", headers=h, json={"title": "Weak password policy", "severity": "high"}).json()
    cap = client.post("/api/corrective-actions", headers=h, json={"finding_id": f["id"], "title": "Enforce policy"}).json()
    assert client.patch(f"/api/findings/{f['id']}", headers=h, json={"status": "resolved"}).status_code == 409
    assert client.patch(f"/api/corrective-actions/{cap['id']}", headers=h, json={"status": "verified"}).status_code == 422  # needs notes
    assert client.patch(f"/api/corrective-actions/{cap['id']}", headers=h, json={"status": "verified", "verification_notes": "Re-tested OK"}).status_code == 200
    assert client.patch(f"/api/findings/{f['id']}", headers=h, json={"status": "resolved"}).status_code == 200


def test_policy_approval_workflow(client, tenant):
    h = tenant["h"]
    p = client.post("/api/policies", headers=h, json={"title": "Acceptable Use", "body": "Staff must lock their screens when away."}).json()
    assert client.patch(f"/api/policies/{p['id']}", headers=h, json={"status": "approved"}).status_code == 409  # cannot bypass workflow
    req = client.post("/api/approvals/request", headers=h, json={"entity_type": "policy", "entity_id": p["id"]})
    assert req.status_code == 201
    mgr = add_member(client, h, "compliance_manager")
    d = client.post(f"/api/approvals/{req.json()['id']}/decision", headers=mgr, json={"decision": "approved"})
    assert d.status_code == 200
    assert client.get(f"/api/policies/{p['id']}", headers=h).json()["status"] == "approved"
    # editing an approved policy drops it back to draft and bumps the version
    upd = client.patch(f"/api/policies/{p['id']}", headers=h, json={"body": "Staff must lock screens and use a privacy filter."}).json()
    assert upd["status"] == "draft" and upd["version"] == 2


def test_self_approval_blocked_when_other_approver_exists(client, tenant):
    h = tenant["h"]
    add_member(client, h, "compliance_manager")
    p = client.post("/api/policies", headers=h, json={"title": "P", "body": "Body text long enough."}).json()
    req = client.post("/api/approvals/request", headers=h, json={"entity_type": "policy", "entity_id": p["id"]}).json()
    assert client.post(f"/api/approvals/{req['id']}/decision", headers=h, json={"decision": "approved"}).status_code == 403


def test_report_generation_uses_persisted_data(client, tenant):
    h = tenant["h"]
    client.post("/api/risks", headers=h, json={"title": "Phishing campaign", "likelihood": 3, "impact": 4})
    r = client.post("/api/reports/generate", headers=h, json={"prompt": "Give me a risk summary"}).json()
    assert r["kind"] == "risk_summary" and "Phishing campaign" in r["content"]
    assert client.get(f"/api/reports/{r['id']}/download", headers=h).status_code == 200


def test_audit_trail_records_and_detects_tampering(client, tenant):
    from app.db import SessionLocal
    from app.models import AuditEvent
    h = tenant["h"]
    client.post("/api/controls", headers=h, json={"control_key": "T-1", "title": "Trail"})
    events = client.get("/api/audit-trail", headers=h).json()["items"]
    assert any(e["action"] == "control.created" for e in events)
    assert client.get("/api/audit-trail/verify", headers=h).json()["valid"] is True
    with SessionLocal() as db:
        ev = db.query(AuditEvent).filter(AuditEvent.org_id == tenant["org"], AuditEvent.action == "control.created").first()
        ev.detail = "rewritten"
        db.commit()
    assert client.get("/api/audit-trail/verify", headers=h).json()["valid"] is False


def test_alerts_and_automation_are_idempotent(client, tenant):
    h = tenant["h"]
    cid = client.post("/api/controls", headers=h, json={"control_key": "E-1", "title": "Expiring"}).json()["id"]
    eid = upload(client, h, control_ids=str(cid), valid_days=-3).json()["id"]
    client.post(f"/api/evidence/{eid}/review", headers=h, json={"decision": "approved"})
    client.post("/api/automation-rules", headers=h, json={"name": "expired", "trigger": "evidence_expired", "action": "create_task"})
    client.post("/api/alert-rules", headers=h, json={"name": "controls w/o evidence", "metric": "readiness_below", "threshold": 100})
    first = client.post("/api/automation/run", headers=h).json()["actions"]
    second = client.post("/api/automation/run", headers=h).json()["actions"]
    assert len(first) == 1 and second == []
    fired = client.post("/api/alerts/run", headers=h).json()["fired"]
    assert fired and client.post("/api/alerts/run", headers=h).json()["fired"] == []
    assert client.get("/api/notifications", headers=h).json()["unread"] >= 1


def test_simulator_does_not_persist_and_projects_gain(client, tenant):
    h = tenant["h"]
    aid = start_assessment(client, h)
    client.post(f"/api/assessments/{aid}/auto-link", headers=h)
    cids = [i["control_id"] for i in client.get(f"/api/assessments/{aid}/items", headers=h, params={"page_size": 200}).json()["items"] if i["control_id"]]
    before = client.get("/api/dashboard", headers=h).json()["readiness"]["score"]
    sim = client.post("/api/insights/simulate", headers=h, json={"assume_compliant_controls": cids[:5]}).json()
    assert sim["after"]["score"] > sim["before"]["score"]
    assert client.get("/api/dashboard", headers=h).json()["readiness"]["score"] == before


def test_calendar_and_graph_use_real_data(client, tenant):
    h = tenant["h"]
    aid = start_assessment(client, h)
    client.post(f"/api/assessments/{aid}/auto-link", headers=h)
    g = client.get("/api/insights/graph", headers=h).json()
    assert g["nodes"] and any(e["kind"] == "implements" for e in g["edges"])
    client.post("/api/vendors", headers=h, json={"name": "Vend", "next_assessment": (date.today() + timedelta(days=5)).isoformat()})
    assert any(e["type"] == "vendor_assessment" for e in client.get("/api/insights/calendar", headers=h).json()["items"])


def test_custom_framework_import_and_versioning(client, tenant):
    h = tenant["h"]
    iso = framework_id(client, h)
    assert client.post(f"/api/frameworks/{iso}/import", headers=h, json={"csv": "ref_id,title\nX,Y"}).status_code == 409  # templates are read-only
    clone = client.post(f"/api/frameworks/{iso}/clone", headers=h, json={"version": "2022-org"}).json()
    assert client.post(f"/api/frameworks/{iso}/clone", headers=h, json={"version": "2022-org"}).status_code == 409
    csv = "ref_id,title,domain,requirement_text\n5.15,Access control,Annex A,Org-supplied licensed text about access control.\n"
    assert client.post(f"/api/frameworks/{clone['id']}/import", headers=h, json={"csv": csv, "mark_as_licensed_text": True}).json()["imported"] == 1
    d = client.get(f"/api/frameworks/{clone['id']}", headers=h).json()
    assert d["source_status"] == "imported"
    custom = client.post("/api/frameworks", headers=h, json={"key": "internal", "name": "Internal baseline", "controls": [{"ref_id": "I-1", "title": "Be careful"}]})
    assert custom.status_code == 201
    assert client.post("/api/mappings", headers=h, json={"from_id": d["controls"][0]["id"], "to_id": client.get(f"/api/frameworks/{custom.json()['id']}", headers=h).json()["controls"][0]["id"]}).status_code == 201


def test_dashboard_for_new_org_is_empty_not_fake(client, tenant):
    dash = client.get("/api/dashboard", headers=tenant["h"]).json()
    assert dash["counts"]["controls"] == 0 and dash["readiness"]["score"] == 0
    assert dash["readiness"]["insufficient_data"]
